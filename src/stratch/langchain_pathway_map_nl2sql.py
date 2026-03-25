"""LangGraph-based Step 3 pathway mapping pipeline.

This module preserves the Step 3 pathway-mapping contract while replacing the
direct NL2SQL call path with a custom LangGraph SQL agent inspired by:
https://docs.langchain.com/oss/python/langgraph/sql-agent
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple
from urllib.parse import urlparse

from langchain.chat_models import init_chat_model
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
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
SEMANTIC_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
SEMANTIC_CACHE_DIR = Path(".cache") / "langchain_pathway_map_nl2sql"

INCLUDED_COLLECTIONS_SQL = (
    "gs.collection_name = 'H' OR "
    "gs.collection_name LIKE 'C2:CP%' OR "
    "gs.collection_name = 'C5:GO:BP'"
)

ALLOWED_COLLECTIONS_WHERE_SQL = (
    "collection = 'H' OR "
    "collection LIKE 'C2:CP%' OR "
    "collection = 'C5:GO:BP'"
)

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

BIO_ENTITY_ALIASES = {
    "EGFR": ["EGFR", "ERBB1"],
    "ERBB": ["ERBB"],
    "ERBB2": ["ERBB2", "HER2"],
    "ERBB3": ["ERBB3", "HER3"],
    "ERBB4": ["ERBB4", "HER4"],
    "MET": ["MET"],
    "HGF": ["HGF"],
    "STAT3": ["STAT3"],
    "VEGF": ["VEGF", "VEGFA"],
    "PDL1": ["PD-L1", "PDL1", "CD274"],
    "KRAS": ["KRAS"],
    "NRAS": ["NRAS"],
    "BRAF": ["BRAF"],
    "RAS": ["RAS"],
    "RAF": ["RAF"],
    "MEK": ["MEK", "MAP2K"],
    "ERK": ["ERK", "MAPK1", "MAPK3"],
    "MAPK": ["MAPK"],
    "PI3K": ["PI3K", "PIK3", "PIK3CA"],
    "AKT": ["AKT", "AKT1", "AKT2", "AKT3"],
    "MTOR": ["MTOR", "mTOR"],
}

BIO_CONCEPT_PATTERNS = {
    "ERBB_SIGNALING": [r"\bERBB\b", r"\bEGFR SIGNALING\b", r"\bERBB SIGNALING\b"],
    "MAPK_CASCADE": [r"\bMAPK\b", r"\bRAS\b", r"\bRAF\b", r"\bMEK\b", r"\bERK\b"],
    "PI3K_AKT": [r"\bPI3K\b", r"\bAKT\b"],
    "ANGIOGENESIS": [r"\bANGIOGENESIS\b", r"\bVEGF\b"],
    "IMMUNE_EVASION": [r"IMMUNE EVASION", r"PD L1", r"PDL1", r"CD274"],
    "BYPASS_SIGNALING": [r"\bBYPASS\b", r"\bRESISTANCE\b"],
    "FUSION_KINASE": [r"FUSION KINASE", r"\bFUSION\b"],
    "MUTATION_ACTIVATED": [r"MUTATION ACTIVATED", r"\bMUTANT\b", r"\bACTIVATING\b"],
    "SURVIVAL": [r"\bSURVIVAL\b", r"\bANTI APOPTOTIC\b", r"\bCELL SURVIVAL\b"],
    "CELL_CYCLE": [r"\bCELL CYCLE\b", r"\bMITOSIS\b"],
}

OFF_TARGET_CONTEXT_PATTERNS = {
    "CARDIAC": [r"\bCARDIAC\b", r"\bMYOCYTE", r"\bMYOCYTES\b"],
    "LYMPHOID": [r"\bB CELL\b", r"\bB LYMPH", r"\bLYMPHOCYTE\b", r"\bLYMPHOCYTES\b"],
    "NEURONAL": [r"\bNEURON", r"\bBRAIN\b", r"\bSYNAP"],
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


@dataclass(frozen=True)
class BiologyFeatures:
    """Normalized biological entities and concepts extracted from text."""

    entities: frozenset[str]
    concepts: frozenset[str]
    normalized_text: str


def load_json(path: str | Path) -> Dict[str, Any]:
    """Load a UTF-8 JSON file."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(obj: Any, path: str | Path) -> None:
    """Write a UTF-8 JSON file with indentation."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)


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


def collect_pathway_sets(final_rows: Dict[str, Dict[str, Any]]) -> List[str]:
    """Collect unique mapped pathway names in row order, excluding UNMAPPED."""
    ordered_names: List[str] = []
    seen: set[str] = set()
    for _, row in sorted(final_rows.items(), key=lambda item: row_order(item[0])):
        name = str(row.get("Mapped MSigDB Pathway Name", "")).strip()
        if not name or name == "UNMAPPED" or name in seen:
            continue
        seen.add(name)
        ordered_names.append(name)
    return ordered_names


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
            query = f"""
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                CASE
                    WHEN NULLIF(gsd.description_full, '') IS NOT NULL
                         AND NULLIF(gsd.description_brief, '') IS NOT NULL
                        THEN gsd.description_full || ' ' || gsd.description_brief
                    WHEN NULLIF(gsd.description_full, '') IS NOT NULL
                        THEN gsd.description_full
                    WHEN NULLIF(gsd.description_brief, '') IS NOT NULL
                        THEN gsd.description_brief
                    ELSE ''
                END AS description,
                ns.label AS source
            FROM gene_set gs
            LEFT JOIN gene_set_details gsd
                ON gsd.gene_set_id = gs.id
            LEFT JOIN namespace ns
                ON ns.id = gsd.primary_namespace_id
            WHERE gsd.source_species_code = 'HS'
              AND ({INCLUDED_COLLECTIONS_SQL})
            """
        else:
            query = f"""
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                CASE
                    WHEN NULLIF(gsd.description_full, '') IS NOT NULL
                         AND NULLIF(gsd.description_brief, '') IS NOT NULL
                        THEN gsd.description_full || ' ' || gsd.description_brief
                    WHEN NULLIF(gsd.description_full, '') IS NOT NULL
                        THEN gsd.description_full
                    WHEN NULLIF(gsd.description_brief, '') IS NOT NULL
                        THEN gsd.description_brief
                    ELSE ''
                END AS description,
                NULL AS source
            FROM gene_set gs
            LEFT JOIN gene_set_details gsd
                ON gsd.gene_set_id = gs.id
            WHERE gsd.source_species_code = 'HS'
              AND ({INCLUDED_COLLECTIONS_SQL})
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


def normalize_biology_text(text: str) -> str:
    """Normalize text for biological pattern matching."""
    return re.sub(r"[^A-Z0-9]+", " ", str(text or "").upper()).strip()


def build_semantic_query_text(pathway_name: str, rationale: str) -> str:
    """Create the merged query text used for retrieval and reranking."""
    pathway = norm_text(pathway_name)
    rationale_text = norm_text(rationale)
    if rationale_text:
        return f"Pathway: {pathway}. Rationale: {rationale_text}"
    return f"Pathway: {pathway}"


def build_candidate_corpus_text(
    msigdb_name: str,
    collection: Optional[str],
    description: str,
) -> str:
    """Create a rich text representation of one MSigDB candidate."""
    parts = [
        f"Pathway: {norm_text(msigdb_name)}",
        f"Collection: {norm_text(collection)}" if norm_text(collection) else "",
        f"Description: {norm_text(description)}" if norm_text(description) else "",
    ]
    return " ".join(part for part in parts if part)


def extract_biology_features(text: str) -> BiologyFeatures:
    """Extract normalized entities and concepts from free text."""
    normalized = normalize_biology_text(text)
    entities: set[str] = set()
    concepts: set[str] = set()

    for canonical_name, aliases in BIO_ENTITY_ALIASES.items():
        if any(re.search(rf"(?<![A-Z0-9]){re.escape(normalize_biology_text(alias))}(?![A-Z0-9])", normalized) for alias in aliases):
            entities.add(canonical_name)

    for concept_name, patterns in BIO_CONCEPT_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            concepts.add(concept_name)

    if {"RAS", "RAF", "MEK", "ERK"} & entities:
        concepts.add("MAPK_CASCADE")
    if {"PI3K", "AKT"} & entities:
        concepts.add("PI3K_AKT")
    if {"EGFR", "ERBB", "ERBB2", "ERBB3", "ERBB4"} & entities:
        concepts.add("ERBB_SIGNALING")
    if {"VEGF"} & entities:
        concepts.add("ANGIOGENESIS")
    if {"PDL1"} & entities:
        concepts.add("IMMUNE_EVASION")

    return BiologyFeatures(
        entities=frozenset(entities),
        concepts=frozenset(concepts),
        normalized_text=normalized,
    )


def detect_off_target_context_penalty(query_text: str, candidate_text: str) -> float:
    """Penalize candidates with strong unrelated tissue/system context absent from query."""
    normalized_query = normalize_biology_text(query_text)
    normalized_candidate = normalize_biology_text(candidate_text)
    penalty = 0.0
    for patterns in OFF_TARGET_CONTEXT_PATTERNS.values():
        query_has_context = any(re.search(pattern, normalized_query) for pattern in patterns)
        candidate_has_context = any(re.search(pattern, normalized_candidate) for pattern in patterns)
        if candidate_has_context and not query_has_context:
            penalty += 0.03
    return penalty


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


class SimilarityModel:
    """Sentence-transformer retrieval with TF-IDF fallback."""

    def __init__(self, corpus_texts: Sequence[str], prefer_embeddings: bool = True) -> None:
        self.corpus_texts = [norm_text(text) for text in corpus_texts]
        self.use_embeddings = False
        self.embedding_model_name = SEMANTIC_EMBEDDING_MODEL

        if prefer_embeddings:
            try:
                clear_blackhole_proxy_env()
                from sentence_transformers import SentenceTransformer
                import numpy as np_local

                self._np = np_local
                self.embedder = SentenceTransformer(self.embedding_model_name)
                SEMANTIC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                corpus_hash = hashlib.sha256("\n".join(self.corpus_texts).encode("utf-8")).hexdigest()
                model_tag = self.embedding_model_name.replace("/", "_")
                emb_cache_path = SEMANTIC_CACHE_DIR / f"embeddings_{model_tag}_{corpus_hash}.npy"
                if emb_cache_path.exists():
                    self.corpus_emb = np_local.load(str(emb_cache_path))
                else:
                    self.corpus_emb = self.embedder.encode(
                        self.corpus_texts,
                        normalize_embeddings=True,
                        batch_size=64,
                        show_progress_bar=False,
                    )
                    np_local.save(str(emb_cache_path), self.corpus_emb)
                self.use_embeddings = True
                return
            except Exception as exc:
                LOGGER.warning(
                    "sentence-transformers unavailable for semantic reranking (%s); using TF-IDF fallback.",
                    exc,
                )

        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            min_df=1,
            max_df=0.95,
            stop_words="english",
        )
        self.cosine_similarity = cosine_similarity
        self.corpus_mat = self.vectorizer.fit_transform(self.corpus_texts)

    def topk(self, query_text: str, k: int = 10) -> List[Tuple[int, float]]:
        """Return top-k corpus matches."""
        text = norm_text(query_text)
        if not text:
            return []
        if self.use_embeddings:
            query_emb = self.embedder.encode([text], normalize_embeddings=True)
            scores = (self.corpus_emb @ query_emb[0]).astype(float)
            top_indices = scores.argsort()[::-1][:k]
            return [(int(index), float(scores[index])) for index in top_indices]

        query_vec = self.vectorizer.transform([text])
        scores = self.cosine_similarity(self.corpus_mat, query_vec).reshape(-1)
        top_indices = scores.argsort()[::-1][:k]
        return [(int(index), float(scores[index])) for index in top_indices]

    def score_pair(self, left_text: str, right_text: str) -> float:
        """Return cosine-style similarity for two arbitrary texts."""
        left = norm_text(left_text)
        right = norm_text(right_text)
        if not left or not right:
            return 0.0
        if self.use_embeddings:
            pair_embeddings = self.embedder.encode([left, right], normalize_embeddings=True)
            return float(pair_embeddings[0] @ pair_embeddings[1])

        left_vec = self.vectorizer.transform([left])
        right_vec = self.vectorizer.transform([right])
        return float(self.cosine_similarity(left_vec, right_vec)[0][0])


class SemanticMSigDBIndex:
    """Semantic retrieval and biological feature cache for allowed MSigDB rows."""

    def __init__(self, rows: Sequence[MSigDBRow], prefer_embeddings: bool = True) -> None:
        self.rows = list(rows)
        self.rows_by_name = {row.msigdb_name: row for row in self.rows}
        self.corpus_texts = [
            build_candidate_corpus_text(row.msigdb_name, row.collection, row.description)
            for row in self.rows
        ]
        self.corpus_text_by_name = {
            row.msigdb_name: build_candidate_corpus_text(row.msigdb_name, row.collection, row.description)
            for row in self.rows
        }
        self.features_by_name = {
            row.msigdb_name: extract_biology_features(self.corpus_text_by_name[row.msigdb_name])
            for row in self.rows
        }
        self.similarity_model = SimilarityModel(self.corpus_texts, prefer_embeddings=prefer_embeddings)

    def get_row(self, name: str) -> Optional[MSigDBRow]:
        """Return one cached row by MSigDB name."""
        return self.rows_by_name.get(name)

    def get_corpus_text(self, name: str, fallback_description: str = "", fallback_collection: str = "") -> str:
        """Return the canonical corpus text for a candidate name."""
        row = self.get_row(name)
        if row is not None:
            return self.corpus_text_by_name[name]
        return build_candidate_corpus_text(name, fallback_collection, fallback_description)

    def get_features(self, name: str, fallback_text: str = "") -> BiologyFeatures:
        """Return cached biological features for a candidate name."""
        row = self.get_row(name)
        if row is not None:
            return self.features_by_name[name]
        return extract_biology_features(fallback_text)

    def topk_candidates(self, query_text: str, k: int = 20) -> List[Dict[str, Any]]:
        """Return top-k semantic retrieval candidates over the filtered MSigDB corpus."""
        candidates: List[Dict[str, Any]] = []
        for index, score in self.similarity_model.topk(query_text, k=k):
            row = self.rows[index]
            candidates.append(
                {
                    "msigdb_name": row.msigdb_name,
                    "collection": row.collection or "",
                    "description": row.description,
                    "sql_score": 0.0,
                    "semantic_seed_score": round(float(score), 6),
                    "retrieval_source": "semantic_fallback",
                }
            )
        return candidates


def build_semantic_index(
    msig_rows: Sequence[MSigDBRow],
    prefer_embeddings: bool = True,
) -> SemanticMSigDBIndex:
    """Build the semantic search index for allowed MSigDB rows."""
    return SemanticMSigDBIndex(msig_rows, prefer_embeddings=prefer_embeddings)


def compute_candidate_scores(
    pathway_name: str,
    rationale: str,
    candidate: Dict[str, Any],
    semantic_index: Optional[SemanticMSigDBIndex] = None,
    max_sql_score: float = 0.0,
) -> Dict[str, Any]:
    """Score one candidate using semantics, biology, SQL support, and priority."""
    query_text = build_semantic_query_text(pathway_name, rationale)
    query_name_tokens = tokenize_for_overlap(pathway_name)
    query_rationale_tokens = tokenize_for_overlap(rationale)
    candidate_name_tokens = tokenize_for_overlap(str(candidate.get("msigdb_name", "")))
    candidate_desc_tokens = tokenize_for_overlap(str(candidate.get("description", "")))

    lexical_overlap_score = (
        0.40 * jaccard(query_name_tokens, candidate_name_tokens)
        + 0.20 * jaccard(query_name_tokens, candidate_desc_tokens)
        + 0.15 * jaccard(query_rationale_tokens, candidate_name_tokens)
        + 0.25 * jaccard(query_rationale_tokens, candidate_desc_tokens)
    )

    candidate_name = str(candidate.get("msigdb_name", ""))
    candidate_description = str(candidate.get("description", ""))
    candidate_collection = str(candidate.get("collection", ""))
    candidate_text = build_candidate_corpus_text(candidate_name, candidate_collection, candidate_description)

    if semantic_index is not None:
        semantic_query_score = semantic_index.similarity_model.score_pair(query_text, candidate_text)
        semantic_name_score = semantic_index.similarity_model.score_pair(pathway_name, candidate_name)
        semantic_description_score = semantic_index.similarity_model.score_pair(
            rationale or pathway_name,
            candidate_description or candidate_text,
        )
        candidate_features = semantic_index.get_features(candidate_name, candidate_text)
    else:
        semantic_query_score = lexical_overlap_score
        semantic_name_score = jaccard(query_name_tokens, candidate_name_tokens)
        semantic_description_score = jaccard(query_rationale_tokens, candidate_desc_tokens)
        candidate_features = extract_biology_features(candidate_text)

    query_features = extract_biology_features(query_text)
    query_entity_count = max(len(query_features.entities), 1)
    query_concept_count = max(len(query_features.concepts), 1)
    shared_entities = query_features.entities & candidate_features.entities
    shared_concepts = query_features.concepts & candidate_features.concepts
    entity_match_score = len(shared_entities) / query_entity_count if query_features.entities else 0.0
    concept_match_score = len(shared_concepts) / query_concept_count if query_features.concepts else 0.0

    priority_rank = get_pathway_priority(str(candidate.get("msigdb_name", "")))
    priority_bonus = 0.0
    if priority_rank < len(PATHWAY_PRIORITY):
        priority_bonus = 0.05 * (len(PATHWAY_PRIORITY) - priority_rank) / len(PATHWAY_PRIORITY)

    sql_support_score = 0.0
    raw_sql_score = float(candidate.get("sql_score", 0.0) or 0.0)
    if max_sql_score > 0.0:
        sql_support_score = raw_sql_score / max_sql_score
    elif raw_sql_score > 0.0:
        sql_support_score = 1.0

    penalty = detect_off_target_context_penalty(query_text, candidate_text)
    if query_features.entities and entity_match_score == 0.0:
        penalty += 0.05
    elif len(query_features.entities) >= 2 and entity_match_score < 0.5:
        penalty += 0.02
    if query_features.concepts and concept_match_score == 0.0:
        penalty += 0.03

    ranking_score = (
        0.35 * semantic_query_score
        + 0.20 * semantic_name_score
        + 0.10 * semantic_description_score
        + 0.15 * entity_match_score
        + 0.10 * concept_match_score
        + 0.03 * lexical_overlap_score
        + 0.02 * sql_support_score
        + priority_bonus
        - penalty
    )

    enriched = dict(candidate)
    enriched["overlap_score"] = round(lexical_overlap_score, 6)
    enriched["semantic_query_score"] = round(semantic_query_score, 6)
    enriched["semantic_name_score"] = round(semantic_name_score, 6)
    enriched["semantic_description_score"] = round(semantic_description_score, 6)
    enriched["entity_match_score"] = round(entity_match_score, 6)
    enriched["concept_match_score"] = round(concept_match_score, 6)
    enriched["sql_support_score"] = round(sql_support_score, 6)
    enriched["priority_rank"] = priority_rank
    enriched["priority_bonus"] = round(priority_bonus, 6)
    enriched["off_target_context_penalty"] = round(penalty, 6)
    enriched["shared_entities"] = sorted(shared_entities)
    enriched["shared_concepts"] = sorted(shared_concepts)
    enriched["ranking_score"] = round(ranking_score, 6)
    return enriched


def get_candidate_rejection_reason(
    candidate: Dict[str, Any],
    query_features: BiologyFeatures,
) -> Optional[str]:
    """Return a human-readable reason when a candidate should be skipped."""
    reasons: List[str] = []
    if query_features.entities and float(candidate.get("entity_match_score", 0.0)) <= 0.0:
        reasons.append("missing_query_entities")
    if query_features.concepts and float(candidate.get("concept_match_score", 0.0)) <= 0.0:
        reasons.append("missing_query_concepts")
    if float(candidate.get("semantic_query_score", 0.0)) < 0.18:
        reasons.append("low_semantic_query_score")
    if float(candidate.get("off_target_context_penalty", 0.0)) >= 0.03:
        reasons.append("off_target_context")
    if float(candidate.get("ranking_score", 0.0)) < 0.12:
        reasons.append("low_ranking_score")
    return ", ".join(reasons) if reasons else None


def rank_msigdb_candidates(
    pathway_name: str,
    rationale: str,
    candidates: Sequence[Dict[str, Any]],
    semantic_index: Optional[SemanticMSigDBIndex] = None,
) -> List[Dict[str, Any]]:
    """Rank candidates deterministically by semantic, biological, and SQL support signals."""
    max_sql_score = max((float(candidate.get("sql_score", 0.0) or 0.0) for candidate in candidates), default=0.0)
    scored = [
        compute_candidate_scores(
            pathway_name,
            rationale,
            candidate,
            semantic_index=semantic_index,
            max_sql_score=max_sql_score,
        )
        for candidate in candidates
    ]
    scored.sort(
        key=lambda candidate: (
            -float(candidate.get("ranking_score", 0.0)),
            -float(candidate.get("entity_match_score", 0.0)),
            -float(candidate.get("concept_match_score", 0.0)),
            int(candidate.get("priority_rank", len(PATHWAY_PRIORITY))),
            -float(candidate.get("sql_support_score", 0.0)),
            str(candidate.get("msigdb_name", "")).upper(),
        )
    )
    return scored


def should_trigger_semantic_fallback(
    ranked_candidates: Sequence[Dict[str, Any]],
    pathway_name: str,
    rationale: str,
) -> bool:
    """Decide whether SQL-only candidates are too weak to trust."""
    if not ranked_candidates:
        return True
    query_features = extract_biology_features(build_semantic_query_text(pathway_name, rationale))
    top = ranked_candidates[0]
    if get_candidate_rejection_reason(top, query_features):
        return True
    if query_features.entities and float(top.get("entity_match_score", 0.0)) < 0.5:
        return True
    return False


def merge_candidate_lists(
    sql_candidates: Sequence[Dict[str, Any]],
    semantic_candidates: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge SQL and semantic candidates by name while preserving the best available metadata."""
    merged: Dict[str, Dict[str, Any]] = {}
    for source_name, source_candidates in (("sql", sql_candidates), ("semantic_fallback", semantic_candidates)):
        for candidate in source_candidates:
            name = str(candidate.get("msigdb_name", "")).strip()
            if not name:
                continue
            current = merged.get(name)
            if current is None:
                current = dict(candidate)
                current["retrieval_sources"] = []
                merged[name] = current
            if source_name not in current["retrieval_sources"]:
                current["retrieval_sources"].append(source_name)
            if float(candidate.get("sql_score", 0.0) or 0.0) > float(current.get("sql_score", 0.0) or 0.0):
                current["sql_score"] = float(candidate.get("sql_score", 0.0) or 0.0)
            if not norm_text(current.get("description", "")) and norm_text(candidate.get("description", "")):
                current["description"] = candidate.get("description", "")
            if not norm_text(current.get("collection", "")) and norm_text(candidate.get("collection", "")):
                current["collection"] = candidate.get("collection", "")
            if "semantic_seed_score" in candidate:
                current["semantic_seed_score"] = max(
                    float(current.get("semantic_seed_score", 0.0) or 0.0),
                    float(candidate.get("semantic_seed_score", 0.0) or 0.0),
                )
    return list(merged.values())


def select_best_candidate(
    ranked_candidates: Sequence[Dict[str, Any]],
    pathway_name: str,
    rationale: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Select the best biologically relevant candidate and annotate rejected ones."""
    query_features = extract_biology_features(build_semantic_query_text(pathway_name, rationale))
    annotated: List[Dict[str, Any]] = []
    selected: Optional[Dict[str, Any]] = None
    for candidate in ranked_candidates:
        annotated_candidate = dict(candidate)
        rejection_reason = get_candidate_rejection_reason(annotated_candidate, query_features)
        annotated_candidate["candidate_rejection_reason"] = rejection_reason
        annotated.append(annotated_candidate)
        if selected is None and rejection_reason is None:
            selected = annotated_candidate
    return selected, annotated


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


def validate_candidate_generation_sql(sql: str) -> Optional[str]:
    """Return an error string when SQL is too weak for pathway candidate generation."""
    normalized = lower(sql)
    if " as msigdb_name" not in normalized or " as collection" not in normalized or " as description" not in normalized:
        return "SQL must project msigdb_name, collection, and description aliases."
    if "limit" not in normalized:
        return "SQL must bound candidate count with LIMIT."
    if "where" not in normalized and "order by" not in normalized:
        return "SQL must filter or rank candidate rows."
    return None


def constrain_query_to_allowed_collections(sql: str) -> str:
    """Wrap a query so only allowed collection families are returned."""
    normalized = (sql or "").strip().rstrip(";")
    if not normalized:
        return normalized
    if "AS allowed_candidates" in normalized and ALLOWED_COLLECTIONS_WHERE_SQL in normalized:
        return normalized
    return (
        "SELECT *\n"
        f"FROM (\n{normalized}\n) AS allowed_candidates\n"
        f"WHERE {ALLOWED_COLLECTIONS_WHERE_SQL}"
    )


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
        "semantic_query_score": candidate.get("semantic_query_score", 0.0),
        "semantic_name_score": candidate.get("semantic_name_score", 0.0),
        "semantic_description_score": candidate.get("semantic_description_score", 0.0),
        "entity_match_score": candidate.get("entity_match_score", 0.0),
        "concept_match_score": candidate.get("concept_match_score", 0.0),
        "sql_support_score": candidate.get("sql_support_score", 0.0),
        "priority_rank": candidate.get("priority_rank", len(PATHWAY_PRIORITY)),
        "priority_bonus": candidate.get("priority_bonus", 0.0),
        "ranking_score": candidate.get("ranking_score", 0.0),
        "shared_entities": candidate.get("shared_entities", []),
        "shared_concepts": candidate.get("shared_concepts", []),
        "candidate_rejection_reason": candidate.get("candidate_rejection_reason"),
        "retrieval_sources": candidate.get("retrieval_sources", []),
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

        base_run_query_tool = self.tools["sql_db_query"]

        @tool("sql_db_query")
        def run_query_tool(query: str) -> str:
            """Execute a read-only SQLite query constrained to allowed collections."""
            constrained_query = constrain_query_to_allowed_collections(query)
            return str(base_run_query_tool.invoke({"query": constrained_query}))

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
            "Use SQL only to generate a biologically plausible candidate pool; final ranking happens later. "
            "Prefer pathway/process relevance rather than broad incidental gene mentions. "
            "Avoid generic organ-, tissue-, or cell-type-specific pathways unless the input explicitly points there. "
            "Prefer candidate pathways whose names or descriptions encode mechanism, signaling cascade, mutation, "
            "angiogenesis, immune evasion, bypass signaling, or survival context when present in the input. "
            "The executed SQL will be constrained to allowed collections only: H, C2:CP*, and C5:GO:BP. "
            "Always expose the pathway collection through the alias named collection. "
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
            "Reject SQL that only does broad gene-token matching without pathway or process relevance. "
            "Make sure the query remains compatible with an outer collection filter that keeps only "
            "H, C2:CP*, and C5:GO:BP via the collection alias. "
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
            "- Allowed collections only: H, C2:CP*, C5:GO:BP.\n"
            "- Prefer biologically relevant pathway/process matches, not gene-name-only matches.\n"
            "- Avoid unrelated tissue- or organ-specific pathways unless the input explicitly says so.\n"
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
                    queries.append(str(query).strip().rstrip(";"))
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
    semantic_index: Optional[SemanticMSigDBIndex] = None,
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
        "candidate_count_after_validation": 0,
        "excluded_unknown_names": 0,
        "selected_candidate": None,
        "top_candidates": [],
        "selection_stage": None,
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

    candidate_sql_error = validate_candidate_generation_sql(safe_sql)
    if candidate_sql_error:
        result["failure_reason"] = candidate_sql_error
        result["failure_type"] = "sql_validation_error"
        return result

    constrained_sql = constrain_query_to_allowed_collections(safe_sql)
    result["checked_sql"] = constrained_sql

    candidates_raw, execution_error = execute_candidate_sql(conn, constrained_sql, max_rows=max(top_k * 5, 50))
    if execution_error:
        result["failure_reason"] = execution_error
        result["failure_type"] = "sql_execution_error"
        return result

    result["candidate_count_raw"] = len(candidates_raw)

    candidates_valid, unknown_count = filter_sql_candidates_to_known_msigdb(candidates_raw, msigdb_lookup)
    result["candidate_count_after_validation"] = len(candidates_valid)
    result["excluded_unknown_names"] = unknown_count

    if semantic_index is not None:
        ranked_sql = rank_msigdb_candidates(
            original_pathway,
            rationale,
            candidates_valid,
            semantic_index=semantic_index,
        )
        use_fallback = should_trigger_semantic_fallback(ranked_sql, original_pathway, rationale)
        semantic_candidates: List[Dict[str, Any]] = []
        if use_fallback:
            semantic_candidates = semantic_index.topk_candidates(
                build_semantic_query_text(original_pathway, rationale),
                k=max(top_k * 2, 20),
            )
        merged_candidates = merge_candidate_lists(candidates_valid, semantic_candidates)
        ranked = rank_msigdb_candidates(
            original_pathway,
            rationale,
            merged_candidates,
            semantic_index=semantic_index,
        )
        selected, annotated_candidates = select_best_candidate(ranked, original_pathway, rationale)
        result["top_candidates"] = [
            reduce_candidate_for_trace(candidate)
            for candidate in annotated_candidates[:10]
        ]
    else:
        if not candidates_valid:
            result["failure_reason"] = "No valid candidates returned after filtering/validation."
            result["failure_type"] = "no_valid_candidates"
            return result
        ranked = rank_msigdb_candidates(original_pathway, rationale, candidates_valid)
        selected, annotated_candidates = select_best_candidate(ranked, original_pathway, rationale)
        result["top_candidates"] = [
            reduce_candidate_for_trace(candidate)
            for candidate in annotated_candidates[:10]
        ]

    if selected is None:
        result["failure_reason"] = "No biologically relevant candidates cleared the ranking thresholds."
        result["failure_type"] = "no_valid_candidates"
        return result

    result["verdict"] = "mapped"
    result["mapped_msigdb_name"] = str(selected.get("msigdb_name", "UNMAPPED"))
    result["selected_candidate"] = reduce_candidate_for_trace(selected)
    result["selection_stage"] = (
        "semantic_fallback"
        if "semantic_fallback" in selected.get("retrieval_sources", [])
        and "sql" not in selected.get("retrieval_sources", [])
        else "sql_rerank"
    )
    result["decision_reason"] = (
        "Selected highest biologically relevant ranking score using semantic similarity, "
        "entity/concept coverage, pathway priority, and SQL support. "
        "Alphabetical ordering is used only as the last-resort deterministic fallback."
    )
    return result


def run_pathway_mapping_pipeline(
    input_file: Path,
    sql_agent: Any,
    msigdb_sqlite_path: str | Path,
    msigdb_lookup: Dict[str, MSigDBRow],
    semantic_index: Optional[SemanticMSigDBIndex],
    nl2sql_model: str,
    out_final_dir: Path,
    out_trace_dir: Path,
    top_k: int = TOP_CANDIDATES_FOR_MAPPING,
) -> Tuple[str, str]:
    """Process one input JSON file and write final and trace outputs."""
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
                semantic_index=semantic_index,
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
                "mapping_method": "langgraph_sql_agent_bio_semantic_rank",
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
                    "candidate_count_after_validation": mapping.get("candidate_count_after_validation"),
                    "excluded_unknown_names": mapping.get("excluded_unknown_names"),
                    "selection_stage": mapping.get("selection_stage"),
                    "selected_candidate": mapping.get("selected_candidate"),
                    "top_candidates": mapping.get("top_candidates"),
                    "decision_reason": mapping.get("decision_reason"),
                    "failure_type": mapping.get("failure_type"),
                    "failure_reason": mapping.get("failure_reason"),
                }
            )
    finally:
        conn.close()

    final_output: Dict[str, Any] = {"pathway_sets": collect_pathway_sets(final_data)}
    final_output.update(final_data)

    final_path = out_final_dir / f"{drug_name}.json"
    trace_path = out_trace_dir / f"{drug_name}_trace_pathway_mapping.json"

    save_json(final_output, final_path)
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

    return str(final_path), str(trace_path)


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
    LOGGER.info("Building semantic retrieval index...")
    semantic_index = build_semantic_index(msig_rows)

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
                    semantic_index=semantic_index,
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
