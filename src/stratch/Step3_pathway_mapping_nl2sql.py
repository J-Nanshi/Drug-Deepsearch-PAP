"""Step 3 pathway mapping pipeline using NL2SQL + deterministic ranking.

Overview:
This script processes Step-2 drug JSON files, filters rows by inclusion and
relationship-class rules, maps retained pathways to canonical MSigDB pathway
names by generating read-only SQL (NL2SQL), then deterministically ranking SQL
candidates using pathway name + rationale overlap.

Inputs:
- CLI:
  - `-i/--input-dir`
  - `--out-final-dir`
  - `--out-trace-dir`
  - `--out-pathways-dir`
  - `--msigdb-sqlite-path`
  - `--nl2sql-model`
- Environment:
  - `OPENAI_API_KEY`
- Per-row fields:
  - pathway name (via key fallbacks), rationale, references, relationship class,
    include decision, optional mapped MSigDB name.

Logic:
1. Filter rows by include decision and allowed relationship classes.
2. Load/normalize MSigDB metadata and build lookup for validation.
3. For each row:
   - generate SQL with NL2SQL model,
   - validate SQL safety (single read-only SELECT only),
   - execute SQL to fetch candidates,
   - remove excluded collections + invalid names,
   - rank candidates deterministically,
   - select best canonical pathway or mark UNMAPPED.
4. Persist outputs and NL2SQL trace.

Outputs:
- `<drug>.json`
- `<drug>_trace_pathway_mapping.json`
- `<drug>_pathways.txt` (unique pathways, first-seen order)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

client: Optional[OpenAI] = None

DEFAULT_NL2SQL_MODEL = "gpt-4o"
NL2SQL_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 2000
TOP_CANDIDATES_FOR_MAPPING = 20
ROW_PROCESS_DELAY_SECONDS = 0.25

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

PRINT_SUMMARY = True
PRINT_VERIFICATION_PROGRESS = True

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


# General helpers
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_pathways_txt(pathways: List[str], path: str) -> None:
    unique_pathways = list(dict.fromkeys(pathways))
    with open(path, "w", encoding="utf-8") as f:
        for pathway in unique_pathways:
            f.write(f"{pathway}\n")


def norm_text(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def lower(s: str) -> str:
    return norm_text(s).lower()


def short(s: str, n: int = 120) -> str:
    s = norm_text(s)
    return s if len(s) <= n else s[: n - 1] + "..."


def listify_refs(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i).strip()]
    if isinstance(x, str):
        parts = re.split(r"[;\n,]\s*", x.strip())
        return [p for p in parts if p]
    return [str(x)]


def strip_markdown_fence(s: str) -> str:
    s = (s or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def is_row_included(entry: Dict[str, Any]) -> bool:
    decision = lower(str(entry.get("Include decision", entry.get(" Include decision", ""))))
    return "include" in decision and "exclude" not in decision


def get_relationship_classification(entry: Dict[str, Any]) -> str:
    return str(
        entry.get(
            "Pathwayâ€“drug relationship classification",
            entry.get(
                "Pathway-drug relationship classification",
                entry.get("PathwayÃ¢â‚¬â€œdrug relationship classification", ""),
            ),
        )
    )


def is_row_relationship_class_in_scope(entry: Dict[str, Any]) -> bool:
    return lower(get_relationship_classification(entry)) in REL_CLASS_FILTER_VALUES


def row_order(key: str) -> Tuple[int, str]:
    m = re.search(r"(\d+)", key)
    return (int(m.group(1)) if m else 10**9, key)


def get_pathway_name(entry: Dict[str, Any]) -> str:
    return (
        entry.get("Original Pathway Name")
        or entry.get("Pathway")
        or entry.get("Pathway Name")
        or entry.get("Pathway ID/Name")
        or ""
    )

# MSigDB structures and loading
@dataclass
class MSigDBRow:
    msigdb_name: str
    collection: Optional[str]
    description: str
    source: Optional[str]


def _list_tables(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    return [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]


def _has_tables(conn: sqlite3.Connection, names: List[str]) -> bool:
    tset = set(_list_tables(conn))
    return all(n in tset for n in names)


def load_msigdb_metadata(db_path: str) -> List[MSigDBRow]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        if not _has_tables(conn, ["gene_set", "gene_set_details"]):
            raise RuntimeError("Unsupported MSigDB SQLite schema: expected gene_set + gene_set_details.")
        has_namespace = "namespace" in set(_list_tables(conn))
        if has_namespace:
            sql = """
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
            sql = """
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                COALESCE(NULLIF(gsd.description_full, ''), NULLIF(gsd.description_brief, ''), '') AS description,
                NULL AS source
            FROM gene_set gs
            LEFT JOIN gene_set_details gsd
                ON gsd.gene_set_id = gs.id
            """

        rows = cur.execute(sql).fetchall()
        out: List[MSigDBRow] = []
        for name, coll, desc, src in rows:
            if not name:
                continue
            out.append(
                MSigDBRow(
                    msigdb_name=str(name),
                    collection=str(coll) if coll is not None else None,
                    description=str(desc or ""),
                    source=str(src) if src is not None else None,
                )
            )
        return out
    finally:
        conn.close()


def build_msigdb_lookup(msig_rows: List[MSigDBRow]) -> Dict[str, MSigDBRow]:
    return {row.msigdb_name: row for row in msig_rows}


def validate_msigdb_name(name: str, msigdb_lookup: Dict[str, MSigDBRow]) -> bool:
    return name in msigdb_lookup


def is_collection_excluded(collection: str, excluded_collections: set) -> bool:
    collection = collection or ""
    for excl in excluded_collections:
        if collection == excl:
            return True
        if collection.startswith(excl + ":"):
            return True
        if ":" in excl and excl in collection:
            return True
    return False


def filter_msigdb_by_collection(msig_rows: List[MSigDBRow], excluded_collections: set) -> List[MSigDBRow]:
    filtered = []
    excluded_count = 0
    for row in msig_rows:
        if is_collection_excluded(row.collection or "", excluded_collections):
            excluded_count += 1
        else:
            filtered.append(row)

    print(f"  Filtered {excluded_count} pathways from excluded collections")
    print(f"  Remaining {len(filtered)} pathways for candidate search")
    return filtered


# Token overlap + deterministic ranking
def tokenize_for_overlap(text: str) -> List[str]:
    toks = re.findall(r"[a-z0-9]+", lower(text))
    return [t for t in toks if len(t) >= 3 and t not in STOPWORDS]


def jaccard(a: List[str], b: List[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return float(len(inter)) / float(len(union))


def get_pathway_priority(msigdb_name: str) -> int:
    name_upper = (msigdb_name or "").upper()
    for i, prefix in enumerate(PATHWAY_PRIORITY):
        if name_upper.startswith(prefix):
            return i
    return len(PATHWAY_PRIORITY)


def compute_candidate_scores(pathway_name: str, rationale: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    q_name = tokenize_for_overlap(pathway_name)
    q_rat = tokenize_for_overlap(rationale)
    c_name = tokenize_for_overlap(str(candidate.get("msigdb_name", "")))
    c_desc = tokenize_for_overlap(str(candidate.get("description", "")))

    score_name_name = jaccard(q_name, c_name)
    score_name_desc = jaccard(q_name, c_desc)
    score_rat_name = jaccard(q_rat, c_name)
    score_rat_desc = jaccard(q_rat, c_desc)

    overlap_score = (
        0.40 * score_name_name
        + 0.20 * score_name_desc
        + 0.15 * score_rat_name
        + 0.25 * score_rat_desc
    )

    priority_rank = get_pathway_priority(str(candidate.get("msigdb_name", "")))
    if priority_rank < len(PATHWAY_PRIORITY):
        priority_bonus = 0.05 * (len(PATHWAY_PRIORITY) - priority_rank) / len(PATHWAY_PRIORITY)
    else:
        priority_bonus = 0.0

    ranking_score = overlap_score + priority_bonus

    c = dict(candidate)
    c["overlap_score"] = round(overlap_score, 6)
    c["priority_rank"] = priority_rank
    c["priority_bonus"] = round(priority_bonus, 6)
    c["ranking_score"] = round(ranking_score, 6)
    return c


def rank_msigdb_candidates(pathway_name: str, rationale: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored = [compute_candidate_scores(pathway_name, rationale, c) for c in candidates]
    scored.sort(
        key=lambda c: (
            -float(c.get("ranking_score", 0.0)),
            int(c.get("priority_rank", len(PATHWAY_PRIORITY))),
            -float(c.get("sql_score", 0.0)),
            str(c.get("msigdb_name", "")).upper(),
        )
    )
    return scored

# OpenAI + NL2SQL
def init_openai_client() -> None:
    global client
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not found. Please set it using one of these methods:\n"
            "1. Environment variable: $env:OPENAI_API_KEY = 'your-key'\n"
            "2. Create a .env file with: OPENAI_API_KEY=your-key\n"
            "3. Set directly in code (not recommended)"
        )
    client = OpenAI(api_key=openai_api_key)


def call_openai_with_retry(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = NL2SQL_TEMPERATURE,
    max_retries: int = 3,
) -> str:
    if client is None:
        raise RuntimeError("OpenAI client is not initialized. Call init_openai_client() first.")

    for attempt in range(max_retries):
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": LLM_MAX_TOKENS,
            }
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            print(f"OpenAI API error (attempt {attempt+1}/{max_retries}): {e}")

            if "max_tokens" in err and "not supported" in err and attempt < max_retries - 1:
                try:
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = LLM_MAX_TOKENS
                    response = client.chat.completions.create(**kwargs)
                    return response.choices[0].message.content.strip()
                except Exception as e2:
                    print(f"Retry with max_completion_tokens also failed: {e2}")

            if attempt < max_retries - 1:
                sleep(2**attempt)
            else:
                raise

    return ""


def build_schema_context(conn: sqlite3.Connection) -> str:
    target_tables = ["gene_set", "gene_set_details", "namespace"]
    lines: List[str] = []

    for table in target_tables:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not row:
            continue

        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_desc = ", ".join([f"{c[1]} ({c[2] or 'TEXT'})" for c in cols])
        lines.append(f"- {table}: {col_desc}")

    return "\n".join(lines)


def generate_nl2sql_query(
    pathway_name: str,
    rationale: str,
    schema_context: str,
    model: str,
    top_k: int,
) -> Dict[str, Any]:
    prompt = f"""You are an expert SQL generator for SQLite MSigDB mapping.

Task:
Generate ONE read-only SQL SELECT query to retrieve the best candidate MSigDB pathways for the input pathway.

Input pathway name:
{pathway_name}

Input rationale:
{rationale}

SQLite schema (only these tables/columns are available):
{schema_context}

Required SQL output rules:
1. Output one SQL SELECT only (no INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH).
2. Return columns with these exact aliases:
   - msigdb_name
   - collection
   - description
   Optional numeric alias:
   - sql_score
3. Query must search using BOTH pathway name and rationale semantics over pathway names and descriptions.
4. Use JOINs as needed between gene_set and gene_set_details (namespace optional).
5. Sort strongest matches first and include LIMIT {top_k}.
6. No markdown. No explanation outside JSON.

Return ONLY valid JSON:
{{
  "sql": "SELECT ...",
  "reasoning": "brief rationale"
}}
"""

    messages = [
        {
            "role": "system",
            "content": "You produce safe, read-only SQLite SELECT queries. Return only valid JSON.",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        response = call_openai_with_retry(messages=messages, model=model, temperature=NL2SQL_TEMPERATURE)
        response = strip_markdown_fence(response)
        parsed = json.loads(response)
        return {
            "sql": str(parsed.get("sql", "")).strip(),
            "reasoning": str(parsed.get("reasoning", "")).strip(),
            "error": None,
            "raw_response": response,
        }
    except Exception as e:
        return {
            "sql": "",
            "reasoning": "",
            "error": f"NL2SQL generation failed: {e}",
            "raw_response": None,
        }


def validate_nl2sql_sql(sql: str) -> Tuple[bool, str, Optional[str]]:
    raw = (sql or "").strip()
    if not raw:
        return False, "", "Generated SQL is empty."

    if "--" in raw or "/*" in raw or "*/" in raw:
        return False, "", "SQL comments are not allowed."

    statements = [p.strip() for p in raw.split(";") if p.strip()]
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
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(max_rows)
    except Exception as e:
        return [], f"SQL execution failed: {e}"

    if not cur.description:
        return [], "SQL did not return result columns."

    col_map = {d[0].lower(): d[0] for d in cur.description if d and d[0]}
    required = ["msigdb_name", "collection", "description"]
    missing = [c for c in required if c not in col_map]
    if missing:
        return [], f"SQL result missing required aliases: {', '.join(missing)}"

    score_col = col_map.get("sql_score")
    out: List[Dict[str, Any]] = []
    for row in rows:
        try:
            name = str(row[col_map["msigdb_name"]] or "").strip()
            if not name:
                continue
            collection = str(row[col_map["collection"]] or "").strip()
            description = str(row[col_map["description"]] or "").strip()
            sql_score = 0.0
            if score_col:
                raw_score = row[score_col]
                if raw_score is not None:
                    try:
                        sql_score = float(raw_score)
                    except Exception:
                        sql_score = 0.0

            out.append(
                {
                    "msigdb_name": name,
                    "collection": collection,
                    "description": description,
                    "sql_score": sql_score,
                }
            )
        except Exception:
            continue

    return out, None


def filter_sql_candidates_by_collection(
    candidates: List[Dict[str, Any]],
    excluded_collections: set,
) -> Tuple[List[Dict[str, Any]], int]:
    filtered: List[Dict[str, Any]] = []
    excluded_count = 0

    for c in candidates:
        collection = str(c.get("collection", ""))
        if is_collection_excluded(collection, excluded_collections):
            excluded_count += 1
        else:
            filtered.append(c)

    return filtered, excluded_count


def filter_sql_candidates_to_known_msigdb(
    candidates: List[Dict[str, Any]],
    msigdb_lookup: Dict[str, MSigDBRow],
) -> Tuple[List[Dict[str, Any]], int]:
    filtered: List[Dict[str, Any]] = []
    dropped_unknown = 0

    for c in candidates:
        name = str(c.get("msigdb_name", "")).strip()
        if not validate_msigdb_name(name, msigdb_lookup):
            dropped_unknown += 1
            continue
        filtered.append(c)

    return filtered, dropped_unknown


def reduce_candidate_for_trace(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "msigdb_name": c.get("msigdb_name"),
        "collection": c.get("collection"),
        "sql_score": c.get("sql_score", 0.0),
        "overlap_score": c.get("overlap_score", 0.0),
        "priority_rank": c.get("priority_rank", len(PATHWAY_PRIORITY)),
        "priority_bonus": c.get("priority_bonus", 0.0),
        "ranking_score": c.get("ranking_score", 0.0),
        "description": short(str(c.get("description", "")), 200),
    }

def nl2sql_map_single_row(
    row_key: str,
    entry: Dict[str, Any],
    conn: sqlite3.Connection,
    schema_context: str,
    msigdb_lookup: Dict[str, MSigDBRow],
    nl2sql_model: str,
    top_k: int = TOP_CANDIDATES_FOR_MAPPING,
) -> Dict[str, Any]:
    original_pathway = get_pathway_name(entry)
    rationale = str(entry.get("Rationale", ""))

    result: Dict[str, Any] = {
        "row_key": row_key,
        "verdict": "unmapped",
        "mapped_msigdb_name": "UNMAPPED",
        "generated_sql": None,
        "nl2sql_reasoning": None,
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

    nl2sql_out = generate_nl2sql_query(
        pathway_name=original_pathway,
        rationale=rationale,
        schema_context=schema_context,
        model=nl2sql_model,
        top_k=top_k,
    )
    result["generated_sql"] = nl2sql_out.get("sql")
    result["nl2sql_reasoning"] = nl2sql_out.get("reasoning")

    if nl2sql_out.get("error"):
        result["failure_reason"] = str(nl2sql_out["error"])
        result["failure_type"] = "sql_generation_error"
        return result

    ok, safe_sql, validation_error = validate_nl2sql_sql(str(nl2sql_out.get("sql", "")))
    if not ok:
        result["failure_reason"] = validation_error
        result["failure_type"] = "sql_validation_error"
        return result

    candidates_raw, exec_error = execute_candidate_sql(conn, safe_sql, max_rows=max(top_k * 5, 50))
    if exec_error:
        result["failure_reason"] = exec_error
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
    top = ranked[: top_k if top_k > 0 else 1]
    result["top_candidates"] = [reduce_candidate_for_trace(c) for c in top[:10]]

    best = top[0]
    result["verdict"] = "mapped"
    result["mapped_msigdb_name"] = str(best.get("msigdb_name", "UNMAPPED"))
    result["selected_candidate"] = reduce_candidate_for_trace(best)
    result["decision_reason"] = (
        "Selected highest deterministic ranking score using overlap + priority bonus, "
        "then tie-break by priority, sql_score, and pathway name."
    )

    return result


# Main verification pipeline
def run_verification_pipeline(
    input_file: Path,
    msigdb_sqlite_path: str,
    msigdb_lookup: Dict[str, MSigDBRow],
    nl2sql_model: str,
    out_final_dir: Path,
    out_trace_dir: Path,
    out_pathways_dir: Path,
) -> Tuple[str, str, str]:
    drug_name = input_file.stem

    print(f"\n{'='*70}")
    print(f"Verifying: {drug_name}")
    print(f"Input: {input_file}")
    print(f"{'='*70}")

    input_data = load_json(str(input_file))
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

    dropped_by_include = len(input_data) - len(included_data)
    dropped_by_relationship_class = len(included_data) - len(filtered_data)

    print(f"Rows retained for mapping: {len(filtered_data)}")
    print(f"Rows dropped by include filter: {dropped_by_include}")
    print(f"Rows dropped by relationship class filter: {dropped_by_relationship_class}")

    final_data: Dict[str, Dict[str, Any]] = {}
    trace_data: List[Dict[str, Any]] = []

    mapped_count = 0
    unmapped_count = 0
    sql_generation_errors = 0
    sql_validation_errors = 0
    sql_execution_errors = 0

    sorted_rows = sorted(filtered_data.items(), key=lambda x: row_order(x[0]))
    total_rows = len(sorted_rows)

    conn = sqlite3.connect(msigdb_sqlite_path)
    conn.row_factory = sqlite3.Row
    schema_context = build_schema_context(conn)

    try:
        for i, (row_key, entry) in enumerate(sorted_rows):
            if PRINT_VERIFICATION_PROGRESS:
                print(f"  [{i+1}/{total_rows}] Mapping {row_key}: {short(get_pathway_name(entry), 55)}...")

            mapping = nl2sql_map_single_row(
                row_key=row_key,
                entry=entry,
                conn=conn,
                schema_context=schema_context,
                msigdb_lookup=msigdb_lookup,
                nl2sql_model=nl2sql_model,
                top_k=TOP_CANDIDATES_FOR_MAPPING,
            )

            if mapping["verdict"] == "mapped":
                mapped_count += 1
                print(f"    -> MAPPED: {mapping['mapped_msigdb_name']}")
            else:
                unmapped_count += 1
                print(f"    -> UNMAPPED ({mapping.get('failure_type')})")

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
                "Pathwayâ€“drug relationship classification": entry.get(
                    "Pathwayâ€“drug relationship classification",
                    entry.get(
                        "Pathway-drug relationship classification",
                        entry.get("PathwayÃ¢â‚¬â€œdrug relationship classification", ""),
                    ),
                ),
                "References": listify_refs(entry.get("References")),
                "verdict": mapping["verdict"],
                "mapping_method": "nl2sql_deterministic_rank",
                "nl2sql_generated_sql": mapping.get("generated_sql"),
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
                    "nl2sql_reasoning": mapping.get("nl2sql_reasoning"),
                    "candidate_count_raw": mapping.get("candidate_count_raw"),
                    "candidate_count_after_collection_filter": mapping.get(
                        "candidate_count_after_collection_filter"
                    ),
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

            sleep(ROW_PROCESS_DELAY_SECONDS)
    finally:
        conn.close()

    final_path = str(out_final_dir / f"{drug_name}.json")
    trace_path = str(out_trace_dir / f"{drug_name}_trace_pathway_mapping.json")
    pathways_txt_path = str(out_pathways_dir / f"{drug_name}_pathways.txt")

    save_json(final_data, final_path)

    mapped_pathways = [
        row["Mapped MSigDB Pathway Name"]
        for _, row in sorted(final_data.items(), key=lambda x: row_order(x[0]))
    ]
    save_pathways_txt(mapped_pathways, pathways_txt_path)

    trace_output = {
        "summary": {
            "drug_name": drug_name,
            "input_file": str(input_file),
            "rows_before_filter": len(input_data),
            "rows_after_include_filter": len(included_data),
            "rows_after_filter": total_rows,
            "rows_dropped_by_include_filter": dropped_by_include,
            "rows_dropped_by_relationship_class_filter": dropped_by_relationship_class,
            "mapped_count": mapped_count,
            "unmapped_count": unmapped_count,
            "sql_generation_errors": sql_generation_errors,
            "sql_validation_errors": sql_validation_errors,
            "sql_execution_errors": sql_execution_errors,
            "nl2sql_model": nl2sql_model,
        },
        "verifications": trace_data,
    }
    save_json(trace_output, trace_path)

    if PRINT_SUMMARY:
        print(f"\n--- MAPPING SUMMARY: {drug_name} ---")
        print(f"Total rows mapped: {total_rows}")
        print(f"Mapped: {mapped_count}")
        print(f"Unmapped: {unmapped_count}")
        print(f"SQL generation errors: {sql_generation_errors}")
        print(f"SQL validation errors: {sql_validation_errors}")
        print(f"SQL execution errors: {sql_execution_errors}")
        print("\nOutputs:")
        print(f"  Final: {final_path}")
        print(f"  Trace: {trace_path}")
        print(f"  Pathways TXT: {pathways_txt_path}")

    return final_path, trace_path, pathways_txt_path


# CLI
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MSigDB pathway mapper with NL2SQL candidate retrieval + deterministic ranking."
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        required=True,
        help="Input directory containing JSON files to process.",
    )
    parser.add_argument(
        "--out-final-dir",
        required=True,
        help="Output directory for <drug>.json files.",
    )
    parser.add_argument(
        "--out-trace-dir",
        required=True,
        help="Output directory for <drug>_trace_pathway_mapping.json files.",
    )
    parser.add_argument(
        "--out-pathways-dir",
        required=True,
        help="Output directory for <drug>_pathways.txt files.",
    )
    parser.add_argument(
        "--msigdb-sqlite-path",
        required=True,
        help="Path to MSigDB SQLite database file.",
    )
    parser.add_argument(
        "--nl2sql-model",
        default=DEFAULT_NL2SQL_MODEL,
        help=f"Model name for NL2SQL generation (default: {DEFAULT_NL2SQL_MODEL}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = parse_args()
    input_dir = Path(args.input_dir)
    out_final_dir = Path(args.out_final_dir)
    out_trace_dir = Path(args.out_trace_dir)
    out_pathways_dir = Path(args.out_pathways_dir)
    msigdb_sqlite_path = args.msigdb_sqlite_path
    nl2sql_model = args.nl2sql_model

    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")

    out_final_dir.mkdir(parents=True, exist_ok=True)
    out_trace_dir.mkdir(parents=True, exist_ok=True)
    out_pathways_dir.mkdir(parents=True, exist_ok=True)

    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise RuntimeError(f"No .json files found in input directory: {input_dir}")

    print("Loading MSigDB database...")
    msig_rows_all = load_msigdb_metadata(msigdb_sqlite_path)
    print(f"Loaded {len(msig_rows_all)} MSigDB pathways total")

    print(f"\nFiltering out collections (applied at candidate stage): {', '.join(sorted(EXCLUDED_COLLECTIONS))}")
    _ = filter_msigdb_by_collection(msig_rows_all, EXCLUDED_COLLECTIONS)

    msigdb_lookup = build_msigdb_lookup(msig_rows_all)

    init_openai_client()

    print(f"\nUsing NL2SQL model: {nl2sql_model}")
    print(f"Found {len(input_files)} input file(s) to process:")
    for f in input_files:
        print(f"  - {f.name}")

    outputs = []
    for input_file in input_files:
        try:
            final_path, trace_path, pathways_txt_path = run_verification_pipeline(
                input_file=input_file,
                msigdb_sqlite_path=msigdb_sqlite_path,
                msigdb_lookup=msigdb_lookup,
                nl2sql_model=nl2sql_model,
                out_final_dir=out_final_dir,
                out_trace_dir=out_trace_dir,
                out_pathways_dir=out_pathways_dir,
            )
            outputs.append((final_path, trace_path, pathways_txt_path))
        except Exception as e:
            print(f"ERROR processing {input_file}: {e}")
            import traceback

            traceback.print_exc()

    print(f"\n{'='*70}")
    print("VERIFICATION PIPELINE COMPLETE")
    print(f"{'='*70}")
    print(f"Total files processed: {len(outputs)}")
    print("Final JSON outputs saved to:", out_final_dir)
    print("Trace JSON outputs saved to:", out_trace_dir)
    print("Pathways TXT outputs saved to:", out_pathways_dir)
