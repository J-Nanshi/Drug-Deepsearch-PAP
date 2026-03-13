"""Step 3 pathway mapping and verification pipeline.

Overview:
This script processes Step-2 drug JSON files, filters rows by inclusion and
relationship-class rules, maps retained pathways to MSigDB using semantic retrieval
plus LLM verification, and writes per-drug final/trace/pathway-list outputs.

Inputs:
- CLI:
  - `-i/--input-dir`
  - `--out-final-dir`
  - `--out-trace-dir`
  - `--out-pathways-dir`
  - `--msigdb-sqlite-path`
- Environment:
  - `OPENAI_API_KEY`
- Per-row fields:
  - pathway name (via key fallbacks), rationale, references, relationship class,
    include decision, optional mapped MSigDB name.

Logic:
1. Filter rows by include decision and allowed relationship classes.
2. Load/normalize MSigDB metadata and build lookup for validation.
3. Build semantic retrieval model (SentenceTransformer with cached embeddings in
   `.cache/step3_pathway_mapping`, or TF-IDF fallback).
4. For each row:
   - seed empty mapping with semantic top-1,
   - get semantic top candidates,
   - priority-rank candidates (HALLMARK > REACTOME > KEGG > GO > BIOCARTA),
   - verify/correct using LLM,
   - validate any corrected name against MSigDB.
5. Persist outputs and verification trace.

Outputs:
- `<drug>.json`
- `<drug>_trace_pathway_mapping.json`
- `<drug>_pathways.txt` (unique pathways, first-seen order)
"""

# [markdown]

#Imports
import json
import os
import re
import sqlite3
import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from time import sleep

# For semantic search (optional - will use TF-IDF fallback if not available)
try:
    import numpy as np
except ImportError:
    pass

# OpenAI Configuration
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

client: Optional[OpenAI] = None

# LLM Model Configuration
LLM_MODEL = "gpt-4o"  # or "gpt-4-turbo", "gpt-4", etc.
LLM_TEMPERATURE = 0.1  # Low temperature for consistent, factual responses
LLM_MAX_TOKENS = 2000

# Verification configuration
TOP_CANDIDATES_FOR_CORRECTION = 10  # How many MSigDB candidates to present to LLM

# MSigDB Collection Filter - exclude these collections from candidate search
# These collections are less relevant for pathway mapping:
# C1 = Positional gene sets, C2:CGP = Chemical and genetic perturbations,
# C3 = Regulatory target gene sets, C4 = Computational gene sets,
# C7 = Immunologic signatures, C8 = Cell type signatures
EXCLUDED_COLLECTIONS = {
    "C1",       # Positional gene sets
    "C2:CGP",   # Chemical and genetic perturbations (keep C2:CP which is Canonical pathways)
    "C3",       # Regulatory target gene sets (miRNA targets, TF targets)
    "C4",       # Computational gene sets
    "C7",       # Immunologic signature gene sets
    "C8",       # Cell type signature gene sets
}

# Pathway Priority Order for LLM (higher priority first)
# HALLMARK > REACTOME > KEGG_MEDICUS > GO > BIOCARTA
PATHWAY_PRIORITY = [
    "HALLMARK",
    "REACTOME",
    "KEGG_MEDICUS",
    "KEGG",
    "GO",
    "BIOCARTA",
]

# Display controls
PRINT_SUMMARY = True
PRINT_VERIFICATION_PROGRESS = True
CACHE_DIR = Path(".cache") / "step3_pathway_mapping"
REL_CLASS_FILTER_VALUES = {
     "mechanistically accurate",
     "clinically validated",
     "experimental (clinical trials)",
}

# Helpers (from trial3)
def load_json(path: str) -> Dict[str, Any]:
    """Load a UTF-8 JSON file into a Python dictionary."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(obj: Any, path: str) -> None:
    """Write a Python object to an indented UTF-8 JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def save_pathways_txt(pathways: List[str], path: str) -> None:
    """Write unique pathway names (first-seen order) to a text file."""
    unique_pathways = list(dict.fromkeys(pathways))
    with open(path, "w", encoding="utf-8") as f:
        for pathway in unique_pathways:
            f.write(f"{pathway}\n")

def norm_text(s: str) -> str:
    """Trim and collapse whitespace for stable text comparisons."""
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def lower(s: str) -> str:
    """Return normalized lowercase text."""
    return norm_text(s).lower()

def listify_refs(x: Any) -> List[str]:
    """Normalize references into a list of non-empty strings."""
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i).strip()]
    if isinstance(x, str):
        parts = re.split(r"[;\n,]\s*", x.strip())
        return [p for p in parts if p]
    return [str(x)]

def is_row_included(entry: Dict[str, Any]) -> bool:
    """Return True when include decision contains include and not exclude."""
    decision = lower(str(entry.get("Include decision", entry.get(" Include decision", ""))))
    return "include" in decision and "exclude" not in decision

def get_relationship_classification(entry: Dict[str, Any]) -> str:
    """Extract pathway-drug relationship class from known key variants."""
    return str(
        entry.get(
            "Pathway–drug relationship classification",
            entry.get(
                "Pathway-drug relationship classification",
                entry.get("Pathwayâ€“drug relationship classification", ""),
            ),
        )
    )

def is_row_relationship_class_in_scope(entry: Dict[str, Any]) -> bool:
    """Return True when relationship class passes configured class filter."""
    return lower(get_relationship_classification(entry)) in REL_CLASS_FILTER_VALUES

def row_order(key: str) -> Tuple[int, str]:
    """Sort row keys numerically where possible (e.g., Row2 before Row10)."""
    m = re.search(r"(\d+)", key)
    return (int(m.group(1)) if m else 10**9, key)

def get_pathway_name(entry: Dict[str, Any]) -> str:
    """Extract pathway display name using fallback source field names."""
    return (
        entry.get("Original Pathway Name")
        or entry.get("Pathway")
        or entry.get("Pathway Name")
        or entry.get("Pathway ID/Name")
        or ""
    )

def short(s: str, n: int = 110) -> str:
    """Return a short normalized preview string for progress logging."""
    s = norm_text(s)
    return s if len(s) <= n else s[: n - 1] + "…"

#MSigDB loader (from trial3)
@dataclass
class MSigDBRow:
    """Canonical in-memory representation of one MSigDB pathway record."""
    msigdb_name: str
    collection: Optional[str]
    description: str
    source: Optional[str]

def _list_tables(conn: sqlite3.Connection) -> List[str]:
    """List all table names present in a SQLite database connection."""
    cur = conn.cursor()
    return [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

def _has_tables(conn: sqlite3.Connection, names: List[str]) -> bool:
    """Check if all required table names exist in the SQLite schema."""
    tset = set(_list_tables(conn))
    return all(n in tset for n in names)

def load_msigdb_metadata(db_path: str) -> List[MSigDBRow]:
    """Load MSigDB metadata rows from SQLite after schema compatibility checks."""
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

# MSigDB Lookup Helpers
def build_msigdb_lookup(msig_rows: List[MSigDBRow]) -> Dict[str, MSigDBRow]:
    """Build a dictionary for O(1) lookup of MSigDB rows by name."""
    return {row.msigdb_name: row for row in msig_rows}

def get_msigdb_description(msigdb_name: str, msigdb_lookup: Dict[str, MSigDBRow]) -> Optional[str]:
    """Get the description for an MSigDB pathway name."""
    row = msigdb_lookup.get(msigdb_name)
    return row.description if row else None

def validate_msigdb_name(name: str, msigdb_lookup: Dict[str, MSigDBRow]) -> bool:
    """Check if a pathway name exists in MSigDB."""
    return name in msigdb_lookup

def filter_msigdb_by_collection(
    msig_rows: List[MSigDBRow],
    excluded_collections: set
) -> List[MSigDBRow]:
    """
    Filter out MSigDB rows from excluded collections.
    
    Collection names in MSigDB are like: C1, C2:CGP, C2:CP, C3, H, etc.
    This function filters by exact match or prefix match (e.g., "C3" matches "C3:MIR:MIRDB").
    """
    filtered = []
    excluded_count = 0
    
    for row in msig_rows:
        collection = row.collection or ""
        
        # Check if collection matches any excluded pattern
        is_excluded = False
        for excl in excluded_collections:
            # Exact match
            if collection == excl:
                is_excluded = True
                break
            # Prefix match (e.g., "C3" matches "C3:MIR:MIRDB")
            if collection.startswith(excl + ":"):
                is_excluded = True
                break
            # Also check if excl is a sub-collection match (e.g., "C2:CGP" in collection)
            if excl in collection and ":" in excl:
                is_excluded = True
                break
        
        if not is_excluded:
            filtered.append(row)
        else:
            excluded_count += 1
    
    print(f"  Filtered {excluded_count} pathways from excluded collections")
    print(f"  Remaining {len(filtered)} pathways for candidate search")
    
    return filtered

# Similarity Model for Semantic Search (from trial3)
class SimilarityModel:
    """Semantic similarity model using sentence-transformers or TF-IDF fallback."""
    
    def __init__(self, corpus_texts: List[str]):
        """Build retrieval backend and load/build cached embeddings when available."""
        self.corpus_texts = corpus_texts
        self.use_embeddings = False
        self.embedding_model_name = "all-MiniLM-L6-v2"

        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np_local
            print("  Using sentence-transformers for semantic search...")
            self.embedder = SentenceTransformer(self.embedding_model_name)

            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            corpus_hash = hashlib.sha256("\n".join(self.corpus_texts).encode("utf-8")).hexdigest()
            model_tag = self.embedding_model_name.replace("/", "_")
            emb_cache_path = CACHE_DIR / f"embeddings_{model_tag}_{corpus_hash}.npy"

            if emb_cache_path.exists():
                self.corpus_emb = np_local.load(str(emb_cache_path))
                if len(self.corpus_emb) != len(self.corpus_texts):
                    print("  Cached embeddings shape mismatch; rebuilding cache...")
                    self.corpus_emb = self.embedder.encode(
                        self.corpus_texts, normalize_embeddings=True, batch_size=64, show_progress_bar=True
                    )
                    np_local.save(str(emb_cache_path), self.corpus_emb)
                else:
                    print(f"  Loaded cached embeddings: {emb_cache_path}")
            else:
                self.corpus_emb = self.embedder.encode(
                    self.corpus_texts, normalize_embeddings=True, batch_size=64, show_progress_bar=True
                )
                np_local.save(str(emb_cache_path), self.corpus_emb)
                print(f"  Saved embeddings cache: {emb_cache_path}")
            self.use_embeddings = True
        except Exception as e:
            print(f"  sentence-transformers not available ({e}), using TF-IDF fallback...")
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            self.cosine_similarity = cosine_similarity
            self.vectorizer = TfidfVectorizer(
                ngram_range=(1, 2),
                min_df=1,
                max_df=0.95,
                stop_words="english",
            )
            self.corpus_mat = self.vectorizer.fit_transform(self.corpus_texts)

    def topk(self, query_text: str, k: int = 10) -> List[Tuple[int, float]]:
        """Return top-k matches as (index, score) tuples."""
        query_text = norm_text(query_text)
        if not query_text:
            return []
        if self.use_embeddings:
            q = self.embedder.encode([query_text], normalize_embeddings=True)
            scores = (self.corpus_emb @ q[0]).astype(float)
            idx = scores.argsort()[::-1][:k]
            return [(int(i), float(scores[i])) for i in idx]
        qv = self.vectorizer.transform([query_text])
        scores = self.cosine_similarity(self.corpus_mat, qv).reshape(-1)
        idx = scores.argsort()[::-1][:k]
        return [(int(i), float(scores[i])) for i in idx]

# Top Candidates Helper - Semantic Search Version
def get_top_msigdb_candidates_semantic(
    pathway_name: str,
    rationale: str,
    msig_rows: List[MSigDBRow],
    similarity_model: 'SimilarityModel',
    current_mapped: str,
    top_k: int = 10
) -> List[Dict[str, Any]]:
    """
    Get top candidate MSigDB pathways using semantic similarity.
    Excludes the current mapped pathway from candidates.
    Returns list of dicts with name, collection, description, and score.
    """
    # Build query from pathway name and rationale
    query = f"Pathway: {pathway_name}. Rationale: {rationale}"
    
    # Get more candidates than needed to filter out current mapping
    candidates_raw = similarity_model.topk(query, k=top_k + 5)
    
    candidates = []
    for idx, score in candidates_raw:
        row = msig_rows[idx]
        if row.msigdb_name == current_mapped:
            continue  # Skip current mapping
        
        candidates.append({
            "msigdb_name": row.msigdb_name,
            "collection": row.collection or "",
            "description": short(row.description, 200),
            "score": float(score),
        })
        
        if len(candidates) >= top_k:
            break
    
    return candidates

def get_pathway_priority(msigdb_name: str) -> int:
    """
    Get priority score for a pathway based on naming convention.
    Lower number = higher priority.
    """
    name_upper = msigdb_name.upper()
    for i, prefix in enumerate(PATHWAY_PRIORITY):
        if name_upper.startswith(prefix):
            return i
    return len(PATHWAY_PRIORITY)  # Lowest priority for unknown

def init_openai_client() -> None:
    """Initialize global OpenAI client from environment API key."""
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

# LLM Helper Functions (from trial4)
def call_openai_with_retry(messages: List[Dict[str, str]], max_retries: int = 3) -> str:
    """Call OpenAI API with retry logic."""
    if client is None:
        raise RuntimeError("OpenAI client is not initialized. Call init_openai_client() first.")
    for attempt in range(max_retries):
        try:
            kwargs = {
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS,
            }
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            print(f"OpenAI API error (attempt {attempt+1}/{max_retries}): {e}")

            # If the error indicates that `max_tokens` is unsupported, retry with max_completion_tokens
            if "max_tokens" in err and "not supported" in err and attempt < max_retries - 1:
                try:
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = LLM_MAX_TOKENS
                    response = client.chat.completions.create(**kwargs)
                    return response.choices[0].message.content.strip()
                except Exception as e2:
                    print(f"Retry with max_completion_tokens also failed: {e2}")

            if attempt < max_retries - 1:
                sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
    return ""

#LLM Verification Function
def llm_verify_single_mapping(
    row_key: str,
    entry: Dict[str, Any],
    msig_rows: List[MSigDBRow],
    msigdb_lookup: Dict[str, MSigDBRow],
    similarity_model: 'SimilarityModel',
) -> Dict[str, Any]:
    """
    Verify a single MSigDB pathway mapping using LLM with semantic search candidates.
    
    Returns a dict with:
    - verdict: "correct" or "mapping_corrected"
    - corrected_msigdb_name: null if correct, new name if corrected
    - original_mapped_name: null if correct, old name if corrected
    - effective_current_mapped_name: current mapping used for verification
    - llm_reasoning: explanation from LLM
    """
    current_mapped = entry.get("Mapped MSigDB Pathway Name", "")
    original_pathway = get_pathway_name(entry)
    rationale = entry.get("Rationale", "")

    # If current mapping is missing, bootstrap it with semantic top-1 candidate.
    if not norm_text(current_mapped):
        semantic_seed = get_top_msigdb_candidates_semantic(
            original_pathway,
            rationale,
            msig_rows,
            similarity_model,
            current_mapped="",
            top_k=1,
        )
        if semantic_seed:
            current_mapped = semantic_seed[0]["msigdb_name"]
    
    # Get current MSigDB description
    current_description = get_msigdb_description(current_mapped, msigdb_lookup) or "No description available"
    current_priority = get_pathway_priority(current_mapped)
    
    # Get top candidates using semantic search
    candidates = get_top_msigdb_candidates_semantic(
        original_pathway,
        rationale,
        msig_rows,
        similarity_model,
        current_mapped,
        TOP_CANDIDATES_FOR_CORRECTION
    )
    
    # Sort candidates by priority (HALLMARK first) then by semantic score
    candidates_with_priority = []
    for c in candidates:
        priority = get_pathway_priority(c['msigdb_name'])
        candidates_with_priority.append((priority, c['score'], c))
    
    # Sort: lower priority number first, then higher score
    candidates_with_priority.sort(key=lambda x: (x[0], -x[1]))
    candidates = [c for _, _, c in candidates_with_priority]
    
    # Build candidate list string for prompt with priority info
    priority_labels = {i: p for i, p in enumerate(PATHWAY_PRIORITY)}
    candidate_str = "\n".join([
        f"  {i+1}. {c['msigdb_name']} ({c['collection']}, score={c['score']:.3f}): {c['description']}"
        for i, c in enumerate(candidates)
    ])
    
    # Construct LLM prompt with priority ordering
    prompt = f"""You are an expert in cancer biology and pathway databases. Your task is to verify if an MSigDB pathway mapping is correct.

ORIGINAL PATHWAY NAME: {original_pathway}

RATIONALE (biological context):
{rationale}

CURRENT MAPPING: {current_mapped}
CURRENT MAPPING DESCRIPTION: {current_description}

ALTERNATIVE CANDIDATES (semantic search results, sorted by priority):
{candidate_str}

PATHWAY PRIORITY ORDER (prefer higher priority when mappings are equally valid):
1. HALLMARK_ pathways (highest priority - curated hallmark gene sets)
2. REACTOME_ pathways (comprehensive pathway database)
3. KEGG_MEDICUS_ / KEGG_ pathways (metabolic and signaling pathways)
4. GO_ pathways (Gene Ontology terms)
5. BIOCARTA_ pathways (legacy pathway collection)

TASK:
1. Analyze whether "{current_mapped}" correctly represents the biological process described in the original pathway name and rationale.
2. Consider the priority order: If a HALLMARK pathway captures the biology equally well as the current mapping, prefer the HALLMARK pathway.
3. If CORRECT (and optimal given priority): Return verdict "correct"
4. If INCORRECT or a higher-priority pathway is more appropriate: Return verdict "mapping_corrected" and select a better pathway from the candidates above.

IMPORTANT:
- Only suggest a correction if the current mapping is clearly wrong OR if a higher-priority pathway (e.g., HALLMARK over BIOCARTA) captures the biology equally well or better.
- If correcting, you MUST choose from the candidate list above (these are valid MSigDB names).
- Provide clear reasoning for your decision, mentioning priority if relevant.

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "verdict": "correct" or "mapping_corrected",
  "corrected_msigdb_name": null or "PATHWAY_NAME_FROM_CANDIDATES",
  "llm_reasoning": "brief explanation of why mapping is correct OR why it needed correction and what changed"
}}"""

    messages = [
        {"role": "system", "content": "You are an expert in cancer biology, pathway analysis, and MSigDB. Provide precise, factual analysis. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ]
    
    try:
        response = call_openai_with_retry(messages)
        
        # Parse JSON response
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r"^```(?:json)?\s*", "", response)
            response = re.sub(r"\s*```$", "", response)
        
        result = json.loads(response)
        
        verdict = result.get("verdict", "correct")
        corrected_name = result.get("corrected_msigdb_name")
        reasoning = result.get("llm_reasoning", "")
        
        # Validate corrected name if provided
        if verdict == "mapping_corrected" and corrected_name:
            if not validate_msigdb_name(corrected_name, msigdb_lookup):
                # Reject LLM suggestion - not in database
                print(f"  WARNING: LLM suggested '{corrected_name}' but it's not in MSigDB. Keeping original mapping.")
                return {
                    "verdict": "correct",
                    "corrected_msigdb_name": None,
                    "original_mapped_name": None,
                    "effective_current_mapped_name": current_mapped,
                    "llm_reasoning": f"LLM suggested '{corrected_name}' but rejected (not in MSigDB database). Original mapping retained. LLM reasoning: {reasoning}",
                    "llm_suggestion_rejected": True,
                    "rejected_suggestion": corrected_name,
                }
        
        if verdict == "correct":
            return {
                "verdict": "correct",
                "corrected_msigdb_name": None,
                "original_mapped_name": None,
                "effective_current_mapped_name": current_mapped,
                "llm_reasoning": reasoning,
            }
        else:
            return {
                "verdict": "mapping_corrected",
                "corrected_msigdb_name": corrected_name,
                "original_mapped_name": current_mapped,
                "effective_current_mapped_name": current_mapped,
                "llm_reasoning": reasoning,
            }
    
    except json.JSONDecodeError as e:
        print(f"  ERROR: Failed to parse LLM response as JSON for {row_key}: {e}")
        return {
            "verdict": "correct",
            "corrected_msigdb_name": None,
            "original_mapped_name": None,
            "effective_current_mapped_name": current_mapped,
            "llm_reasoning": f"LLM response parsing failed. Original mapping retained. Error: {str(e)}",
            "parse_error": True,
        }
    except Exception as e:
        print(f"  ERROR: LLM verification failed for {row_key}: {e}")
        return {
            "verdict": "correct",
            "corrected_msigdb_name": None,
            "original_mapped_name": None,
            "effective_current_mapped_name": current_mapped,
            "llm_reasoning": f"LLM verification failed. Original mapping retained. Error: {str(e)}",
            "verification_error": True,
        }

#Main Verification Pipeline
def run_verification_pipeline(
    input_file: Path,
    msig_rows: List[MSigDBRow],
    msigdb_lookup: Dict[str, MSigDBRow],
    similarity_model: 'SimilarityModel',
    out_final_dir: Path,
    out_trace_dir: Path,
    out_pathways_dir: Path,
) -> Tuple[str, str, str]:
    """
    Run verification pipeline for one input file.
    
    Returns: (final_path, trace_path, pathways_txt_path)
    """
    # Extract drug name from filename
    drug_name = input_file.stem
    
    print(f"\n{'='*70}")
    print(f"Verifying: {drug_name}")
    print(f"Input: {input_file}")
    print(f"{'='*70}")
    
    # Load input and apply row filters
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
    
    # Process each row
    final_data: Dict[str, Dict[str, Any]] = {}
    trace_data: List[Dict[str, Any]] = []
    
    correct_count = 0
    corrected_count = 0
    rejected_count = 0
    error_count = 0
    
    sorted_rows = sorted(filtered_data.items(), key=lambda x: row_order(x[0]))
    total_rows = len(sorted_rows)
    
    for i, (row_key, entry) in enumerate(sorted_rows):
        if PRINT_VERIFICATION_PROGRESS:
            print(f"  [{i+1}/{total_rows}] Verifying {row_key}: {short(get_pathway_name(entry), 50)}...")
        
        # Call LLM verification with semantic search
        verification = llm_verify_single_mapping(row_key, entry, msig_rows, msigdb_lookup, similarity_model)
        
        # Build enriched row
        enriched_row = {
            "Mapped MSigDB Pathway Name": verification.get(
                "effective_current_mapped_name",
                entry.get("Mapped MSigDB Pathway Name", ""),
            ),
            "Original Pathway Name": get_pathway_name(entry),
            "Regulation": entry.get("Regulation", ""),
            "Baseline effect": entry.get("Baseline effect", ""),
            "Rationale": entry.get("Rationale", ""),
            "Pathway–drug relationship classification": entry.get(
                "Pathway–drug relationship classification",
                entry.get(
                    "Pathway-drug relationship classification",
                    entry.get("Pathwayâ€“drug relationship classification", ""),
                )
            ),
            "References": listify_refs(entry.get("References")),
            "verdict": verification["verdict"],
            "corrected_msigdb_name": verification["corrected_msigdb_name"],
            "original_mapped_name": verification["original_mapped_name"],
            "llm_reasoning": verification["llm_reasoning"],
        }
        
        # Update mapped name if corrected
        if verification["verdict"] == "mapping_corrected" and verification["corrected_msigdb_name"]:
            enriched_row["Mapped MSigDB Pathway Name"] = verification["corrected_msigdb_name"]
            corrected_count += 1
            print(f"    -> CORRECTED: {verification['original_mapped_name']} -> {verification['corrected_msigdb_name']}")
        elif verification.get("llm_suggestion_rejected"):
            rejected_count += 1
            print(f"    -> REJECTED suggestion: {verification.get('rejected_suggestion')}")
        elif verification.get("parse_error") or verification.get("verification_error"):
            error_count += 1
        else:
            correct_count += 1
            print(f"    -> CORRECT")
        
        final_data[row_key] = enriched_row
        
        # Add to trace
        trace_data.append({
            "Row": row_key,
            "Original Pathway Name": get_pathway_name(entry),
            "Original Mapped MSigDB": verification.get(
                "effective_current_mapped_name",
                entry.get("Mapped MSigDB Pathway Name", ""),
            ),
            "Final Mapped MSigDB": enriched_row["Mapped MSigDB Pathway Name"],
            **verification,
        })
        
        # Small delay to avoid rate limiting
        sleep(0.5)
    
    # Write outputs
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
            "total_rows": total_rows,
            "correct_count": correct_count,
            "corrected_count": corrected_count,
            "rejected_suggestions": rejected_count,
            "errors": error_count,
            "llm_model": LLM_MODEL,
        },
        "verifications": trace_data,
    }
    save_json(trace_output, trace_path)
    
    # Print summary
    if PRINT_SUMMARY:
        print(f"\n--- VERIFICATION SUMMARY: {drug_name} ---")
        print(f"Total rows verified: {total_rows}")
        print(f"Correct mappings: {correct_count}")
        print(f"Corrected mappings: {corrected_count}")
        print(f"Rejected LLM suggestions: {rejected_count}")
        print(f"Errors: {error_count}")
        print(f"\nOutputs:")
        print(f"  Final: {final_path}")
        print(f"  Trace: {trace_path}")
        print(f"  Pathways TXT: {pathways_txt_path}")
        
        if corrected_count > 0:
            print(f"\n--- CORRECTIONS MADE ---")
            for t in trace_data:
                if t["verdict"] == "mapping_corrected":
                    print(f"  {t['Row']}: {t['original_mapped_name']} -> {t['corrected_msigdb_name']}")
                    print(f"    Reason: {short(t['llm_reasoning'], 100)}")
    
    return final_path, trace_path, pathways_txt_path

def parse_args() -> argparse.Namespace:
    """Parse and return required CLI arguments for batch pathway verification."""
    parser = argparse.ArgumentParser(
        description="MSigDB pathway mapper with semantic search + LLM verification."
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
    return parser.parse_args()

# Main execution
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
    
    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")
    
    out_final_dir.mkdir(parents=True, exist_ok=True)
    out_trace_dir.mkdir(parents=True, exist_ok=True)
    out_pathways_dir.mkdir(parents=True, exist_ok=True)
    
    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise RuntimeError(f"No .json files found in input directory: {input_dir}")

    # Load MSigDB once
    print("Loading MSigDB database...")
    msig_rows_all = load_msigdb_metadata(msigdb_sqlite_path)
    print(f"Loaded {len(msig_rows_all)} MSigDB pathways total")
    
    # Build full lookup for validation (includes all collections)
    msigdb_lookup = build_msigdb_lookup(msig_rows_all)
    
    # Filter rows for candidate search (excludes C1, C2:CGP, C3, C4, C7, C8)
    print(f"\nFiltering out collections: {', '.join(sorted(EXCLUDED_COLLECTIONS))}")
    msig_rows = filter_msigdb_by_collection(msig_rows_all, EXCLUDED_COLLECTIONS)
    
    # Build semantic similarity model for filtered pathways
    print("\nBuilding semantic similarity model...")
    msig_texts = [f"{r.msigdb_name}. {r.description}" for r in msig_rows]
    similarity_model = SimilarityModel(msig_texts)
    print("Semantic search model ready.")
    
    # Initialize OpenAI client after local setup checks
    init_openai_client()
    
    print(f"\nFound {len(input_files)} input file(s) to process:")
    for f in input_files:
        print(f"  - {f.name}")
    
    # Run verification for each input file
    outputs = []
    for input_file in input_files:
        try:
            final_path, trace_path, pathways_txt_path = run_verification_pipeline(
                input_file,
                msig_rows,
                msigdb_lookup,
                similarity_model,
                out_final_dir,
                out_trace_dir,
                out_pathways_dir,
            )
            outputs.append((final_path, trace_path, pathways_txt_path))
        except Exception as e:
            print(f"ERROR processing {input_file}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*70}")
    print(f"VERIFICATION PIPELINE COMPLETE")
    print(f"{'='*70}")
    print(f"Total files processed: {len(outputs)}")
    print("Final JSON outputs saved to:", out_final_dir)
    print("Trace JSON outputs saved to:", out_trace_dir)
    print("Pathways TXT outputs saved to:", out_pathways_dir)

