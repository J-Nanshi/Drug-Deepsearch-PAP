"""LangGraph-based Step 3 pathway mapping pipeline.

This module preserves the Step 3 pathway-mapping contract while replacing the
direct NL2SQL call path with a custom LangGraph SQL agent inspired by:
https://docs.langchain.com/oss/python/langgraph/sql-agent
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple
from urllib.parse import urlparse

from langchain.chat_models import init_chat_model
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


LOGGER = logging.getLogger(__name__)

DEFAULT_NL2SQL_MODEL = "gpt-4o"
TOP_CANDIDATES_FOR_MAPPING = 20

EXCLUDED_COLLECTIONS = {
    "C1",
    "C2:CGP",
    "C3",
    "C4",
    "C7",
    "C8",
}

PATHWAY_PRIORITY = [
    "HALLMARK",
    "REACTOME",
    "KEGG_MEDICUS",
    "KEGG",
    "GO",
    "BIOCARTA",
]

REL_CLASS_FILTER_VALUES = {
    "mechanistically accurate",
    "clinically validated",
    "experimental (clinical trials)",
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "via",
    "with",
    "without",
}

SQL_BLOCKLIST_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|"
    r"vacuum|reindex|analyze|grant|revoke|commit|rollback)\b",
    re.IGNORECASE,
)

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True)
class MSigDBRow:
    """Canonical in-memory representation of one MSigDB pathway record."""

    msigdb_name: str
    collection: Optional[str]
    description: str
    source: Optional[str]


@dataclass(frozen=True)
class FilteredRows:
    """Filtered Step 2 rows plus counters for trace output."""

    rows: Dict[str, Dict[str, Any]]
    rows_before_filter: int
    rows_after_include_filter: int
    rows_after_filter: int
    rows_dropped_by_include_filter: int
    rows_dropped_by_relationship_class_filter: int


@dataclass(frozen=True)
class AgentRunTrace:
    """Structured trace extracted from a LangGraph SQL-agent execution."""

    generated_sql: Optional[str]
    checked_sql: Optional[str]
    final_response: str
    query_result_raw: str
    query_result_rows: List[Any]


def load_json(path: str | Path) -> Dict[str, Any]:
    """Load a UTF-8 JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(obj: Any, path: str | Path) -> None:
    """Write a UTF-8 JSON file with indentation."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)


def save_pathways_txt(pathways: Iterable[str], path: str | Path) -> None:
    """Write unique pathway names in first-seen order."""
    unique_pathways = list(dict.fromkeys(pathways))
    with open(path, "w", encoding="utf-8") as handle:
        for pathway in unique_pathways:
            handle.write(f"{pathway}\n")


def _is_blackhole_proxy_url(value: Optional[str]) -> bool:
    """Return True for loopback port-9 proxies that break outbound API calls."""
    if not value:
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"} and parsed.port == 9


def clear_blackhole_proxy_env() -> List[str]:
    """Remove proxy env vars that point to a known local blackhole."""
    cleared: List[str] = []
    for key in PROXY_ENV_KEYS:
        if _is_blackhole_proxy_url(os.getenv(key)):
            os.environ.pop(key, None)
            cleared.append(key)
    if cleared:
        LOGGER.warning(
            "Cleared local port-9 proxy environment variables before OpenAI calls: %s",
            ", ".join(cleared),
        )
    return cleared


def norm_text(value: Any) -> str:
    """Trim text and collapse repeated whitespace."""
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def lower(value: Any) -> str:
    """Return normalized lowercase text."""
    return norm_text(value).lower()


def short(value: Any, max_len: int = 120) -> str:
    """Return a shortened preview string."""
    text = norm_text(value)
    return text if len(text) <= max_len else text[: max_len - 1] + "..."


def listify_refs(value: Any) -> List[str]:
    """Normalize a references field into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if norm_text(item)]
    if isinstance(value, str):
        parts = re.split(r"[;\n,]\s*", value.strip())
        return [part for part in parts if part]
    return [str(value)]


def normalize_key_name(key: str) -> str:
    """Normalize a dictionary key for fuzzy lookup."""
    return re.sub(r"\s+", " ", str(key).strip()).lower()


def find_entry_value(
    entry: Dict[str, Any],
    exact_keys: Sequence[str],
    contains_all: Optional[Sequence[str]] = None,
    default: Any = "",
) -> Any:
    """Look up a field using exact keys first, then fuzzy key matching."""
    for key in exact_keys:
        if key in entry:
            return entry[key]
    if contains_all:
        for key, value in entry.items():
            normalized = normalize_key_name(key)
            if all(token in normalized for token in contains_all):
                return value
    return default


def is_row_included(entry: Dict[str, Any]) -> bool:
    """Return True when a row should be kept by include decision."""
    decision = lower(find_entry_value(entry, ["Include decision"], ["include", "decision"], ""))
    return "include" in decision and "exclude" not in decision


def get_relationship_classification(entry: Dict[str, Any]) -> str:
    """Extract the pathway-drug relationship classification."""
    value = find_entry_value(
        entry,
        ["Pathway-drug relationship classification"],
        ["pathway", "drug", "relationship classification"],
        "",
    )
    return str(value or "")


def is_row_relationship_class_in_scope(entry: Dict[str, Any]) -> bool:
    """Return True when the relationship class is allowed for mapping."""
    return lower(get_relationship_classification(entry)) in REL_CLASS_FILTER_VALUES


def row_order(key: str) -> Tuple[int, str]:
    """Sort row-like keys numerically when possible."""
    match = re.search(r"(\d+)", key)
    return (int(match.group(1)) if match else 10**9, key)


def get_pathway_name(entry: Dict[str, Any]) -> str:
    """Extract the pathway name using supported fallback keys."""
    value = find_entry_value(
        entry,
        ["Original Pathway Name", "Pathway", "Pathway Name", "Pathway ID/Name"],
        ["pathway"],
        "",
    )
    return str(value or "")


def filter_rows_for_mapping(input_data: Dict[str, Any]) -> FilteredRows:
    """Apply include-decision and relationship-class filtering."""
    included_data = {
        row_key: entry
        for row_key, entry in input_data.items()
        if isinstance(entry, dict) and is_row_included(entry)
    }
    filtered_data = {
        row_key: entry
        for row_key, entry in included_data.items()
        if is_row_relationship_class_in_scope(entry)
    }
    return FilteredRows(
        rows=filtered_data,
        rows_before_filter=len(input_data),
        rows_after_include_filter=len(included_data),
        rows_after_filter=len(filtered_data),
        rows_dropped_by_include_filter=len(input_data) - len(included_data),
        rows_dropped_by_relationship_class_filter=len(included_data) - len(filtered_data),
    )


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    """Return all SQLite table names."""
    return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def _has_tables(conn: sqlite3.Connection, names: Sequence[str]) -> bool:
    """Return True when all requested tables exist."""
    table_names = set(_list_tables(conn))
    return all(name in table_names for name in names)


def load_msigdb_metadata(db_path: str | Path) -> List[MSigDBRow]:
    """Load MSigDB pathway metadata from SQLite."""
    conn = sqlite3.connect(db_path)
    try:
        if not _has_tables(conn, ["gene_set", "gene_set_details"]):
            raise RuntimeError("Unsupported MSigDB SQLite schema: expected gene_set + gene_set_details.")

        if "namespace" in set(_list_tables(conn)):
            query = """
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                COALESCE(NULLIF(gsd.description_full, ''), NULLIF(gsd.description_brief, ''), '') AS description,
                ns.label AS source
            FROM gene_set gs
            LEFT JOIN gene_set_details gsd
                ON gsd.gene_set_id = gs.id
            LEFT JOIN namespace ns
                ON ns.id = gsd.primary_namespace_id
            """
        else:
            query = """
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                COALESCE(NULLIF(gsd.description_full, ''), NULLIF(gsd.description_brief, ''), '') AS description,
                NULL AS source
            FROM gene_set gs
            LEFT JOIN gene_set_details gsd
                ON gsd.gene_set_id = gs.id
            """
        output: List[MSigDBRow] = []
        for name, collection, description, source in conn.execute(query).fetchall():
            if not name:
                continue
            output.append(
                MSigDBRow(
                    msigdb_name=str(name),
                    collection=str(collection) if collection is not None else None,
                    description=str(description or ""),
                    source=str(source) if source is not None else None,
                )
            )
        return output
    finally:
        conn.close()


def build_msigdb_lookup(msig_rows: Sequence[MSigDBRow]) -> Dict[str, MSigDBRow]:
    """Build a name-to-row lookup for MSigDB validation."""
    return {row.msigdb_name: row for row in msig_rows}


def validate_msigdb_name(name: str, msigdb_lookup: Dict[str, MSigDBRow]) -> bool:
    """Return True when a pathway name exists in the loaded lookup."""
    return name in msigdb_lookup


def is_collection_excluded(collection: str, excluded_collections: set[str]) -> bool:
    """Return True when a collection matches an exclusion rule."""
    normalized = collection or ""
    for excluded in excluded_collections:
        if normalized == excluded:
            return True
        if normalized.startswith(excluded + ":"):
            return True
        if ":" in excluded and excluded in normalized:
            return True
    return False


def tokenize_for_overlap(text: str) -> List[str]:
    """Tokenize text for overlap scoring using lowercase alphanumeric terms."""
    tokens = re.findall(r"[a-z0-9]+", lower(text))
    return [token for token in tokens if len(token) >= 3 and token not in STOPWORDS]


def jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    """Compute Jaccard similarity between token lists."""
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def get_pathway_priority(msigdb_name: str) -> int:
    """Return priority rank based on configured pathway prefixes."""
    upper_name = (msigdb_name or "").upper()
    for index, prefix in enumerate(PATHWAY_PRIORITY):
        if upper_name.startswith(prefix):
            return index
    return len(PATHWAY_PRIORITY)


def compute_candidate_scores(pathway_name: str, rationale: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Score one candidate using overlap plus priority bonus."""
    query_name_tokens = tokenize_for_overlap(pathway_name)
    query_rationale_tokens = tokenize_for_overlap(rationale)
    candidate_name_tokens = tokenize_for_overlap(str(candidate.get("msigdb_name", "")))
    candidate_desc_tokens = tokenize_for_overlap(str(candidate.get("description", "")))

    overlap_score = (
        0.40 * jaccard(query_name_tokens, candidate_name_tokens)
        + 0.20 * jaccard(query_name_tokens, candidate_desc_tokens)
        + 0.15 * jaccard(query_rationale_tokens, candidate_name_tokens)
        + 0.25 * jaccard(query_rationale_tokens, candidate_desc_tokens)
    )

    priority_rank = get_pathway_priority(str(candidate.get("msigdb_name", "")))
    priority_bonus = 0.0
    if priority_rank < len(PATHWAY_PRIORITY):
        priority_bonus = 0.05 * (len(PATHWAY_PRIORITY) - priority_rank) / len(PATHWAY_PRIORITY)

    enriched = dict(candidate)
    enriched["overlap_score"] = round(overlap_score, 6)
    enriched["priority_rank"] = priority_rank
    enriched["priority_bonus"] = round(priority_bonus, 6)
    enriched["ranking_score"] = round(overlap_score + priority_bonus, 6)
    return enriched


def rank_msigdb_candidates(pathway_name: str, rationale: str, candidates: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank candidates deterministically by score, priority, SQL score, and name."""
    scored = [compute_candidate_scores(pathway_name, rationale, candidate) for candidate in candidates]
    scored.sort(
        key=lambda candidate: (
            -float(candidate.get("ranking_score", 0.0)),
            int(candidate.get("priority_rank", len(PATHWAY_PRIORITY))),
            -float(candidate.get("sql_score", 0.0)),
            str(candidate.get("msigdb_name", "")).upper(),
        )
    )
    return scored


def parse_sql_agent_query_result(raw_content: Any) -> List[Any]:
    """Parse the SQL tool result content into a Python list when possible."""
    if isinstance(raw_content, list):
        return raw_content
    text = norm_text(raw_content)
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def validate_nl2sql_sql(sql: str) -> Tuple[bool, str, Optional[str]]:
    """Validate generated SQL and return a safe single SELECT statement."""
    raw = (sql or "").strip()
    if not raw:
        return False, "", "Generated SQL is empty."
    if "--" in raw or "/*" in raw or "*/" in raw:
        return False, "", "SQL comments are not allowed."

    statements = [part.strip() for part in raw.split(";") if part.strip()]
    if len(statements) != 1:
        return False, "", "SQL must contain exactly one statement."

    statement = statements[0]
    if not re.match(r"(?is)^\s*select\b", statement):
        return False, "", "Only SELECT statements are allowed."
    if SQL_BLOCKLIST_PATTERN.search(statement):
        return False, "", "SQL contains blocked keywords."
    return True, statement, None


def execute_candidate_sql(
    conn: sqlite3.Connection,
    sql: str,
    max_rows: int = 100,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Execute candidate SQL and normalize result rows."""
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchmany(max_rows)
    except Exception as exc:
        return [], f"SQL execution failed: {exc}"

    if not cursor.description:
        return [], "SQL did not return result columns."

    column_positions = {str(desc[0]).lower(): index for index, desc in enumerate(cursor.description) if desc and desc[0]}
    required_columns = ["msigdb_name", "collection", "description"]
    missing = [name for name in required_columns if name not in column_positions]
    if missing:
        return [], f"SQL result missing required aliases: {', '.join(missing)}"

    output: List[Dict[str, Any]] = []
    score_index = column_positions.get("sql_score")
    for row in rows:
        try:
            name = str(row[column_positions["msigdb_name"]] or "").strip()
            if not name:
                continue
            collection = str(row[column_positions["collection"]] or "").strip()
            description = str(row[column_positions["description"]] or "").strip()
            sql_score = 0.0
            if score_index is not None and row[score_index] is not None:
                try:
                    sql_score = float(row[score_index])
                except (TypeError, ValueError):
                    sql_score = 0.0
            output.append(
                {
                    "msigdb_name": name,
                    "collection": collection,
                    "description": description,
                    "sql_score": sql_score,
                }
            )
        except Exception:
            continue
    return output, None


def filter_sql_candidates_by_collection(
    candidates: Sequence[Dict[str, Any]],
    excluded_collections: set[str],
) -> Tuple[List[Dict[str, Any]], int]:
    """Drop candidates from excluded collections."""
    filtered: List[Dict[str, Any]] = []
    excluded_count = 0
    for candidate in candidates:
        collection = str(candidate.get("collection", ""))
        if is_collection_excluded(collection, excluded_collections):
            excluded_count += 1
            continue
        filtered.append(dict(candidate))
    return filtered, excluded_count


def filter_sql_candidates_to_known_msigdb(
    candidates: Sequence[Dict[str, Any]],
    msigdb_lookup: Dict[str, MSigDBRow],
) -> Tuple[List[Dict[str, Any]], int]:
    """Keep only SQL candidates present in the MSigDB lookup."""
    filtered: List[Dict[str, Any]] = []
    dropped_unknown = 0
    for candidate in candidates:
        name = str(candidate.get("msigdb_name", "")).strip()
        if not validate_msigdb_name(name, msigdb_lookup):
            dropped_unknown += 1
            continue
        filtered.append(dict(candidate))
    return filtered, dropped_unknown


def reduce_candidate_for_trace(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Shrink a scored candidate to the trace fields we care about."""
    return {
        "msigdb_name": candidate.get("msigdb_name"),
        "collection": candidate.get("collection"),
        "sql_score": candidate.get("sql_score", 0.0),
        "overlap_score": candidate.get("overlap_score", 0.0),
        "priority_rank": candidate.get("priority_rank", len(PATHWAY_PRIORITY)),
        "priority_bonus": candidate.get("priority_bonus", 0.0),
        "ranking_score": candidate.get("ranking_score", 0.0),
        "description": short(candidate.get("description", ""), 200),
    }


def sqlite_uri_for_path(db_path: str | Path) -> str:
    """Convert a local SQLite path to a LangChain SQLDatabase URI."""
    resolved = Path(db_path).resolve()
    return f"sqlite:///{resolved.as_posix()}"


class LangGraphPathwaySQLAgent:
    """Custom LangGraph SQL agent for MSigDB candidate retrieval."""

    def __init__(self, db_path: str | Path, model_name: str, top_k: int = TOP_CANDIDATES_FOR_MAPPING) -> None:
        self.db_path = str(db_path)
        self.model_name = model_name
        self.top_k = top_k
        self.db = SQLDatabase.from_uri(sqlite_uri_for_path(self.db_path))
        self.model = init_chat_model(model_name, temperature=0)
        self.toolkit = SQLDatabaseToolkit(db=self.db, llm=self.model)
        self.tools = {tool.name: tool for tool in self.toolkit.get_tools()}
        self.agent = self._build_agent()

    def _build_agent(self):
        get_schema_tool = self.tools["sql_db_schema"]
        get_schema_node = ToolNode([get_schema_tool], name="get_schema")

        run_query_tool = self.tools["sql_db_query"]
        run_query_node = ToolNode([run_query_tool], name="run_query")

        def list_tables(_: MessagesState) -> Dict[str, List[BaseMessage]]:
            tool_call = {
                "name": "sql_db_list_tables",
                "args": {},
                "id": "list_tables_call",
                "type": "tool_call",
            }
            tool_call_message = AIMessage(content="", tool_calls=[tool_call])
            tool_message = self.tools["sql_db_list_tables"].invoke(tool_call)
            response = AIMessage(content=f"Available tables: {tool_message.content}")
            return {"messages": [tool_call_message, tool_message, response]}

        def call_get_schema(state: MessagesState) -> Dict[str, List[BaseMessage]]:
            system_message = {
                "role": "system",
                "content": (
                    "You are selecting schema tables for pathway mapping in an MSigDB SQLite database. "
                    "Use the schema tool and inspect the tables required to query pathway names, collections, "
                    "descriptions, and namespace metadata."
                ),
            }
            llm_with_tools = self.model.bind_tools([get_schema_tool], tool_choice="any")
            response = llm_with_tools.invoke([system_message] + state["messages"])
            return {"messages": [response]}

        generate_query_system_prompt = (
            "You are a SQL agent for pathway mapping against an SQLite MSigDB database. "
            "Create one syntactically correct read-only SELECT query using the discovered schema. "
            f"Always limit results to at most {self.top_k}. "
            "The query must search both the original pathway name and the rationale semantics across pathway "
            "names and descriptions. "
            "The query must return these exact aliases: msigdb_name, collection, description. "
            "It may also return a numeric sql_score alias. "
            "Do not use DML or DDL. "
            "After query results are available, do not call more tools; instead provide a brief summary."
        )

        def generate_query(state: MessagesState) -> Dict[str, List[BaseMessage]]:
            system_message = {"role": "system", "content": generate_query_system_prompt}
            llm_with_tools = self.model.bind_tools([run_query_tool])
            response = llm_with_tools.invoke([system_message] + state["messages"])
            return {"messages": [response]}

        check_query_system_prompt = (
            "You are a SQLite SQL expert checking a generated query before execution. "
            "Double check the query for common mistakes, including incorrect joins, missing aliases, "
            "data-type issues, invalid columns, and failure to return the required aliases "
            "msigdb_name, collection, description. "
            "If you find issues, rewrite the query. If not, reproduce it exactly. "
            "Then call the SQL execution tool."
        )

        def check_query(state: MessagesState) -> Dict[str, List[BaseMessage]]:
            tool_call = state["messages"][-1].tool_calls[0]
            system_message = {"role": "system", "content": check_query_system_prompt}
            user_message = {"role": "user", "content": tool_call["args"]["query"]}
            llm_with_tools = self.model.bind_tools([run_query_tool], tool_choice="any")
            response = llm_with_tools.invoke([system_message, user_message])
            response.id = state["messages"][-1].id
            return {"messages": [response]}

        def should_continue(state: MessagesState) -> Literal[END, "check_query"]:
            last_message = state["messages"][-1]
            return "check_query" if getattr(last_message, "tool_calls", None) else END

        builder = StateGraph(MessagesState)
        builder.add_node("list_tables", list_tables)
        builder.add_node("call_get_schema", call_get_schema)
        builder.add_node("get_schema", get_schema_node)
        builder.add_node("generate_query", generate_query)
        builder.add_node("check_query", check_query)
        builder.add_node("run_query", run_query_node)
        builder.add_edge(START, "list_tables")
        builder.add_edge("list_tables", "call_get_schema")
        builder.add_edge("call_get_schema", "get_schema")
        builder.add_edge("get_schema", "generate_query")
        builder.add_conditional_edges("generate_query", should_continue)
        builder.add_edge("check_query", "run_query")
        builder.add_edge("run_query", "generate_query")
        return builder.compile()

    def build_mapping_request(self, pathway_name: str, rationale: str, top_k: int) -> str:
        """Create the user question passed into the graph."""
        return (
            "Map a Step 2 pathway mention to canonical MSigDB candidates.\n"
            f"Original pathway name: {pathway_name}\n"
            f"Rationale: {rationale or 'N/A'}\n"
            "Requirements:\n"
            "- Query SQLite tables only.\n"
            "- Use both pathway name and rationale semantics.\n"
            "- Return exact aliases msigdb_name, collection, description.\n"
            "- sql_score is optional but preferred.\n"
            f"- LIMIT {top_k} or fewer rows.\n"
            "- Keep the query read-only and use the strongest candidate ordering first."
        )

    @staticmethod
    def _extract_last_tool_message(messages: Sequence[BaseMessage], tool_name: str) -> Optional[ToolMessage]:
        for message in reversed(messages):
            if isinstance(message, ToolMessage) and getattr(message, "name", "") == tool_name:
                return message
        return None

    @staticmethod
    def _extract_sql_queries(messages: Sequence[BaseMessage]) -> List[str]:
        queries: List[str] = []
        for message in messages:
            tool_calls = getattr(message, "tool_calls", None) or []
            for tool_call in tool_calls:
                if tool_call.get("name") != "sql_db_query":
                    continue
                query = tool_call.get("args", {}).get("query")
                if norm_text(query):
                    queries.append(str(query))
        return queries

    def run_candidate_query(self, pathway_name: str, rationale: str, top_k: int) -> AgentRunTrace:
        """Run the graph and return structured SQL-agent trace details."""
        request = self.build_mapping_request(pathway_name, rationale, top_k)
        state = self.agent.invoke({"messages": [{"role": "user", "content": request}]})
        messages = state["messages"]

        queries = self._extract_sql_queries(messages)
        tool_message = self._extract_last_tool_message(messages, "sql_db_query")
        final_response = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage) and norm_text(message.content):
                final_response = norm_text(message.content)
                break

        raw_result = ""
        if tool_message is not None:
            raw_result = norm_text(tool_message.content)

        return AgentRunTrace(
            generated_sql=queries[0] if queries else None,
            checked_sql=queries[-1] if queries else None,
            final_response=final_response,
            query_result_raw=raw_result,
            query_result_rows=parse_sql_agent_query_result(raw_result),
        )


def build_sql_agent(db_path: str | Path, model_name: str, top_k: int = TOP_CANDIDATES_FOR_MAPPING) -> LangGraphPathwaySQLAgent:
    """Factory wrapper to make SQL-agent injection easy in tests."""
    return LangGraphPathwaySQLAgent(db_path=db_path, model_name=model_name, top_k=top_k)


def map_single_row_with_agent(
    row_key: str,
    entry: Dict[str, Any],
    sql_agent: Any,
    conn: sqlite3.Connection,
    msigdb_lookup: Dict[str, MSigDBRow],
    top_k: int = TOP_CANDIDATES_FOR_MAPPING,
) -> Dict[str, Any]:
    """Map one input row to a canonical MSigDB pathway using LangGraph SQL generation."""
    original_pathway = get_pathway_name(entry)
    rationale = str(entry.get("Rationale", ""))

    result: Dict[str, Any] = {
        "row_key": row_key,
        "verdict": "unmapped",
        "mapped_msigdb_name": "UNMAPPED",
        "generated_sql": None,
        "checked_sql": None,
        "agent_final_response": None,
        "agent_query_result_raw": None,
        "agent_query_result_rows": [],
        "candidate_count_raw": 0,
        "candidate_count_after_collection_filter": 0,
        "candidate_count_after_validation": 0,
        "excluded_by_collection": 0,
        "excluded_unknown_names": 0,
        "selected_candidate": None,
        "top_candidates": [],
        "decision_reason": None,
        "failure_reason": None,
        "failure_type": None,
    }

    if not norm_text(original_pathway):
        result["failure_reason"] = "Original pathway name is missing."
        result["failure_type"] = "missing_pathway_name"
        return result

    try:
        agent_trace = sql_agent.run_candidate_query(
            pathway_name=original_pathway,
            rationale=rationale,
            top_k=top_k,
        )
    except Exception as exc:
        result["failure_reason"] = f"SQL generation failed: {exc}"
        result["failure_type"] = "sql_generation_error"
        return result

    result["generated_sql"] = agent_trace.generated_sql
    result["checked_sql"] = agent_trace.checked_sql
    result["agent_final_response"] = agent_trace.final_response
    result["agent_query_result_raw"] = agent_trace.query_result_raw
    result["agent_query_result_rows"] = agent_trace.query_result_rows

    ok, safe_sql, validation_error = validate_nl2sql_sql(agent_trace.checked_sql or agent_trace.generated_sql or "")
    if not ok:
        result["failure_reason"] = validation_error
        result["failure_type"] = "sql_validation_error"
        return result

    candidates_raw, execution_error = execute_candidate_sql(conn, safe_sql, max_rows=max(top_k * 5, 50))
    if execution_error:
        result["failure_reason"] = execution_error
        result["failure_type"] = "sql_execution_error"
        return result

    result["candidate_count_raw"] = len(candidates_raw)

    candidates_filtered, excluded_count = filter_sql_candidates_by_collection(candidates_raw, EXCLUDED_COLLECTIONS)
    result["candidate_count_after_collection_filter"] = len(candidates_filtered)
    result["excluded_by_collection"] = excluded_count

    candidates_valid, unknown_count = filter_sql_candidates_to_known_msigdb(candidates_filtered, msigdb_lookup)
    result["candidate_count_after_validation"] = len(candidates_valid)
    result["excluded_unknown_names"] = unknown_count

    if not candidates_valid:
        result["failure_reason"] = "No valid candidates returned after filtering/validation."
        result["failure_type"] = "no_valid_candidates"
        return result

    ranked = rank_msigdb_candidates(original_pathway, rationale, candidates_valid)
    top_candidates = ranked[: top_k if top_k > 0 else 1]
    result["top_candidates"] = [reduce_candidate_for_trace(candidate) for candidate in top_candidates[:10]]

    selected = top_candidates[0]
    result["verdict"] = "mapped"
    result["mapped_msigdb_name"] = str(selected.get("msigdb_name", "UNMAPPED"))
    result["selected_candidate"] = reduce_candidate_for_trace(selected)
    result["decision_reason"] = (
        "Selected highest deterministic ranking score using overlap + priority bonus, "
        "then tie-break by priority, sql_score, and pathway name."
    )
    return result


def run_pathway_mapping_pipeline(
    input_file: Path,
    sql_agent: Any,
    msigdb_sqlite_path: str | Path,
    msigdb_lookup: Dict[str, MSigDBRow],
    nl2sql_model: str,
    out_final_dir: Path,
    out_trace_dir: Path,
    top_k: int = TOP_CANDIDATES_FOR_MAPPING,
) -> Tuple[str, str, str]:
    """Process one input JSON file and write final, trace, and pathways outputs."""
    drug_name = input_file.stem
    LOGGER.info("")
    LOGGER.info("=" * 70)
    LOGGER.info("Mapping pathways for %s", drug_name)
    LOGGER.info("Input: %s", input_file)
    LOGGER.info("=" * 70)

    input_data = load_json(input_file)
    filtered = filter_rows_for_mapping(input_data)
    LOGGER.info("Rows retained for mapping: %s", filtered.rows_after_filter)
    LOGGER.info("Rows dropped by include filter: %s", filtered.rows_dropped_by_include_filter)
    LOGGER.info("Rows dropped by relationship class filter: %s", filtered.rows_dropped_by_relationship_class_filter)

    final_data: Dict[str, Dict[str, Any]] = {}
    trace_data: List[Dict[str, Any]] = []
    mapped_count = 0
    unmapped_count = 0
    sql_generation_errors = 0
    sql_validation_errors = 0
    sql_execution_errors = 0

    sorted_rows = sorted(filtered.rows.items(), key=lambda item: row_order(item[0]))
    conn = sqlite3.connect(msigdb_sqlite_path)
    try:
        for index, (row_key, entry) in enumerate(sorted_rows, start=1):
            LOGGER.info("  [%s/%s] Mapping %s: %s", index, len(sorted_rows), row_key, short(get_pathway_name(entry), 55))
            mapping = map_single_row_with_agent(
                row_key=row_key,
                entry=entry,
                sql_agent=sql_agent,
                conn=conn,
                msigdb_lookup=msigdb_lookup,
                top_k=top_k,
            )

            if mapping["verdict"] == "mapped":
                mapped_count += 1
                LOGGER.info("    -> MAPPED: %s", mapping["mapped_msigdb_name"])
            else:
                unmapped_count += 1
                LOGGER.info("    -> UNMAPPED (%s)", mapping.get("failure_type"))
                if mapping.get("failure_type") == "sql_generation_error":
                    sql_generation_errors += 1
                elif mapping.get("failure_type") == "sql_validation_error":
                    sql_validation_errors += 1
                elif mapping.get("failure_type") == "sql_execution_error":
                    sql_execution_errors += 1

            enriched_row = {
                "Mapped MSigDB Pathway Name": mapping["mapped_msigdb_name"],
                "Original Pathway Name": get_pathway_name(entry),
                "Regulation": entry.get("Regulation", ""),
                "Baseline effect": entry.get("Baseline effect", ""),
                "Rationale": entry.get("Rationale", ""),
                "Pathway-drug relationship classification": get_relationship_classification(entry),
                "References": listify_refs(entry.get("References")),
                "verdict": mapping["verdict"],
                "mapping_method": "langgraph_sql_agent_deterministic_rank",
                "nl2sql_generated_sql": mapping.get("generated_sql"),
                "nl2sql_checked_sql": mapping.get("checked_sql"),
                "nl2sql_decision_reason": mapping.get("decision_reason"),
                "nl2sql_failure_reason": mapping.get("failure_reason"),
            }
            final_data[row_key] = enriched_row

            trace_data.append(
                {
                    "Row": row_key,
                    "Original Pathway Name": get_pathway_name(entry),
                    "Rationale": entry.get("Rationale", ""),
                    "Final Mapped MSigDB": mapping["mapped_msigdb_name"],
                    "verdict": mapping["verdict"],
                    "generated_sql": mapping.get("generated_sql"),
                    "checked_sql": mapping.get("checked_sql"),
                    "agent_final_response": mapping.get("agent_final_response"),
                    "agent_query_result_raw": mapping.get("agent_query_result_raw"),
                    "agent_query_result_rows": mapping.get("agent_query_result_rows"),
                    "candidate_count_raw": mapping.get("candidate_count_raw"),
                    "candidate_count_after_collection_filter": mapping.get("candidate_count_after_collection_filter"),
                    "candidate_count_after_validation": mapping.get("candidate_count_after_validation"),
                    "excluded_by_collection": mapping.get("excluded_by_collection"),
                    "excluded_unknown_names": mapping.get("excluded_unknown_names"),
                    "selected_candidate": mapping.get("selected_candidate"),
                    "top_candidates": mapping.get("top_candidates"),
                    "decision_reason": mapping.get("decision_reason"),
                    "failure_type": mapping.get("failure_type"),
                    "failure_reason": mapping.get("failure_reason"),
                }
            )
    finally:
        conn.close()

    final_path = out_final_dir / f"{drug_name}.json"
    trace_path = out_trace_dir / f"{drug_name}_trace_pathway_mapping.json"
    pathways_path = out_final_dir / f"{drug_name}_pathways.txt"

    save_json(final_data, final_path)
    save_pathways_txt(
        (
            row["Mapped MSigDB Pathway Name"]
            for _, row in sorted(final_data.items(), key=lambda item: row_order(item[0]))
        ),
        pathways_path,
    )
    save_json(
        {
            "summary": {
                "drug_name": drug_name,
                "input_file": str(input_file),
                "rows_before_filter": filtered.rows_before_filter,
                "rows_after_include_filter": filtered.rows_after_include_filter,
                "rows_after_filter": filtered.rows_after_filter,
                "rows_dropped_by_include_filter": filtered.rows_dropped_by_include_filter,
                "rows_dropped_by_relationship_class_filter": filtered.rows_dropped_by_relationship_class_filter,
                "mapped_count": mapped_count,
                "unmapped_count": unmapped_count,
                "sql_generation_errors": sql_generation_errors,
                "sql_validation_errors": sql_validation_errors,
                "sql_execution_errors": sql_execution_errors,
                "nl2sql_model": nl2sql_model,
            },
            "verifications": trace_data,
        },
        trace_path,
    )

    LOGGER.info("")
    LOGGER.info("--- MAPPING SUMMARY: %s ---", drug_name)
    LOGGER.info("Total rows mapped: %s", filtered.rows_after_filter)
    LOGGER.info("Mapped: %s", mapped_count)
    LOGGER.info("Unmapped: %s", unmapped_count)
    LOGGER.info("SQL generation errors: %s", sql_generation_errors)
    LOGGER.info("SQL validation errors: %s", sql_validation_errors)
    LOGGER.info("SQL execution errors: %s", sql_execution_errors)
    LOGGER.info("Outputs:")
    LOGGER.info("  Final: %s", final_path)
    LOGGER.info("  Trace: %s", trace_path)
    LOGGER.info("  Pathways TXT: %s", pathways_path)

    return str(final_path), str(trace_path), str(pathways_path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="MSigDB pathway mapper using a custom LangGraph SQL agent plus deterministic ranking."
    )
    parser.add_argument("-i", "--input-dir", required=True, help="Input directory containing JSON files to process.")
    parser.add_argument("--out-final-dir", required=True, help="Output directory for <drug>.json files.")
    parser.add_argument(
        "--out-trace-dir",
        required=True,
        help="Output directory for <drug>_trace_pathway_mapping.json files.",
    )
    parser.add_argument("--msigdb-sqlite-path", required=True, help="Path to the MSigDB SQLite database file.")
    parser.add_argument(
        "--nl2sql-model",
        default=DEFAULT_NL2SQL_MODEL,
        help=f"Model name for LangGraph SQL generation (default: {DEFAULT_NL2SQL_MODEL}).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)

    input_dir = Path(args.input_dir)
    out_final_dir = Path(args.out_final_dir)
    out_trace_dir = Path(args.out_trace_dir)
    msigdb_sqlite_path = Path(args.msigdb_sqlite_path)

    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")
    if not msigdb_sqlite_path.exists():
        raise RuntimeError(f"MSigDB SQLite database does not exist: {msigdb_sqlite_path}")

    out_final_dir.mkdir(parents=True, exist_ok=True)
    out_trace_dir.mkdir(parents=True, exist_ok=True)

    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise RuntimeError(f"No .json files found in input directory: {input_dir}")

    LOGGER.info("Loading MSigDB database...")
    msig_rows = load_msigdb_metadata(msigdb_sqlite_path)
    LOGGER.info("Loaded %s MSigDB pathways total", len(msig_rows))
    msigdb_lookup = build_msigdb_lookup(msig_rows)

    clear_blackhole_proxy_env()

    LOGGER.info("")
    LOGGER.info("Using LangGraph SQL model: %s", args.nl2sql_model)
    LOGGER.info("Found %s input file(s) to process:", len(input_files))
    for input_file in input_files:
        LOGGER.info("  - %s", input_file.name)

    sql_agent = build_sql_agent(
        db_path=msigdb_sqlite_path,
        model_name=args.nl2sql_model,
        top_k=TOP_CANDIDATES_FOR_MAPPING,
    )

    outputs = []
    for input_file in input_files:
        try:
            outputs.append(
                run_pathway_mapping_pipeline(
                    input_file=input_file,
                    sql_agent=sql_agent,
                    msigdb_sqlite_path=msigdb_sqlite_path,
                    msigdb_lookup=msigdb_lookup,
                    nl2sql_model=args.nl2sql_model,
                    out_final_dir=out_final_dir,
                    out_trace_dir=out_trace_dir,
                )
            )
        except Exception as exc:
            LOGGER.exception("ERROR processing %s: %s", input_file, exc)

    LOGGER.info("")
    LOGGER.info("%s", "=" * 70)
    LOGGER.info("VERIFICATION PIPELINE COMPLETE")
    LOGGER.info("%s", "=" * 70)
    LOGGER.info("Total files processed: %s", len(outputs))
    LOGGER.info("Final JSON outputs saved to: %s", out_final_dir)
    LOGGER.info("Trace JSON outputs saved to: %s", out_trace_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
