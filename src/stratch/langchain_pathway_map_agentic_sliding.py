from __future__ import annotations
# %%
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Literal, Optional
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field
try: from dotenv import load_dotenv; load_dotenv()
except ImportError: pass
AGENT_MODEL = "gpt-5.4-mini"
ALLOWED_REL_CLASSES = {
    "mechanistically accurate",
    "clinically validated",
    "experimental (clinical trials)",
}
ALLOWED_COLLECTIONS_SQL = "gs.collection_name = 'H' OR gs.collection_name LIKE 'C2:CP%' OR gs.collection_name = 'C5:GO:BP'"
DEFAULT_DB_PATH = Path("utils/msigdb_v2025.1.Hs.db")
DEFAULT_INPUT_PATH = Path("output/lung_cancer/step2_factcheck_json/afatinib.json")
DEFAULT_FINAL_DIR = Path("output/lung_cancer/trial/langchain_agentic_sliding/mapping")
DEFAULT_TRACE_DIR = Path("output/lung_cancer/trial/langchain_agentic_sliding/trace")
STOPWORDS = {"a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "into", "is", "of", "on", "or", "that", "the", "to", "via", "with"}
ACTIVE_MSIGDB_ROWS: list[dict[str, str]] = []

# %%
class CandidatePreview(BaseModel):
    msigdb_name: str
    collection: str
    description: str
    pre_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
class WindowScanPayload(BaseModel):
    offset: int
    end_offset: int
    inspected_rows: int
    preview_candidates: list[CandidatePreview] = Field(default_factory=list)
class JudgeDecision(BaseModel):
    winner: Literal["current_best", "challenger", "tie"]
    rationale: str
    confidence: float = 0.0
class IterationDecision(BaseModel):
    action: Literal["promote", "keep_current", "no_match"]
    window_selection: Optional[CandidatePreview] = None
    judge_result: Optional[JudgeDecision] = None
    champion_after_round: Optional[CandidatePreview] = None
    round_reason: str
class FinalRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mapped_msigdb_pathway_name: str = Field(alias="Mapped MSigDB Pathway Name")
    original_pathway_name: str = Field(alias="Original Pathway Name")
    regulation: str = Field(alias="Regulation")
    baseline_effect: str = Field(alias="Baseline effect")
    rationale: str = Field(alias="Rationale")
    relationship_classification: str = Field(alias="Pathway-drug relationship classification")
    references: list[str] = Field(default_factory=list, alias="References")
    verdict: str = Field(alias="verdict")
    mapping_method: str = Field(alias="mapping_method")
    agent_decision_reason: Optional[str] = Field(default=None, alias="agent_decision_reason")
    agent_failure_reason: Optional[str] = Field(default=None, alias="agent_failure_reason")
class FinalOutput(BaseModel):
    pathway_sets: list[str]
    rows: dict[str, FinalRow]
class TraceSummary(BaseModel):
    drug_name: str
    input_file: str
    rows_before_filter: int
    rows_after_include_filter: int
    rows_after_filter: int
    rows_dropped_by_include_filter: int
    rows_dropped_by_relationship_class_filter: int
    mapped_count: int
    unmapped_count: int
    agent_model: str
    filtered_msigdb_rows: int
    window_size: int
class WindowTraceEntry(BaseModel):
    window_index: int
    offset: int
    end_offset: int
    inspected_rows: int
    preview_candidates: list[CandidatePreview] = Field(default_factory=list)
    window_selection: Optional[CandidatePreview] = None
    judge_result: Optional[JudgeDecision] = None
    champion_after_round: Optional[CandidatePreview] = None
    round_reason: str
class TraceVerification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    row: str = Field(alias="Row")
    original_pathway_name: str = Field(alias="Original Pathway Name")
    rationale: str = Field(alias="Rationale")
    final_mapped_msigdb: str = Field(alias="Final Mapped MSigDB")
    verdict: str
    selected_candidate: Optional[CandidatePreview] = None
    top_candidates: list[CandidatePreview] = Field(default_factory=list)
    window_trace: list[WindowTraceEntry] = Field(default_factory=list)
    decision_reason: Optional[str] = None
    failure_type: Optional[str] = None
    failure_reason: Optional[str] = None
class TraceOutput(BaseModel):
    summary: TraceSummary
    verifications: list[TraceVerification]
for _model in (CandidatePreview, WindowScanPayload, JudgeDecision, IterationDecision, FinalRow, FinalOutput, TraceSummary, WindowTraceEntry, TraceVerification, TraceOutput):
    _model.model_rebuild()

# %%
def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
def save_json(obj: Any, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())
def short(value: Any, max_len: int = 220) -> str:
    text = norm_text(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
def tokenize(value: Any) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-/+:.]*", norm_text(value).lower()))
    return {token for token in tokens if token not in STOPWORDS and len(token) > 1}
def jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0
def row_order(key: str) -> tuple[int, str]:
    match = re.search(r"(\d+)", key)
    return (int(match.group(1)) if match else 10**9, key)
def listify_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if norm_text(item)]
    if isinstance(value, str):
        return [part for part in re.split(r"[;\n,]\s*", value.strip()) if part]
    return [str(value)]
def get_pathway_name(entry: dict[str, Any]) -> str:
    return str(
        entry.get("Original Pathway Name")
        or entry.get("Pathway")
        or entry.get("Pathway Name")
        or entry.get("Pathway ID/Name")
        or ""
    )
def is_row_included(entry: dict[str, Any]) -> bool:
    decision = norm_text(entry.get("Include decision", "")).lower()
    return "include" in decision and "exclude" not in decision
def get_relationship_classification(entry: dict[str, Any]) -> str:
    return str(
        entry.get(
            "Pathway-drug relationship classification",
            entry.get("Pathway–drug relationship classification", ""),
        )
    )
def filter_rows_for_mapping(input_data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    included = {
        row_key: row
        for row_key, row in input_data.items()
        if isinstance(row, dict) and is_row_included(row)
    }
    filtered = {
        row_key: row
        for row_key, row in included.items()
        if norm_text(get_relationship_classification(row)).lower() in ALLOWED_REL_CLASSES
    }
    return filtered, {
        "rows_before_filter": len(input_data),
        "rows_after_include_filter": len(included),
        "rows_after_filter": len(filtered),
        "rows_dropped_by_include_filter": len(input_data) - len(included),
        "rows_dropped_by_relationship_class_filter": len(included) - len(filtered),
    }
def collect_pathway_sets(final_rows: dict[str, FinalRow]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for _, row in sorted(final_rows.items(), key=lambda item: row_order(item[0])):
        name = row.mapped_msigdb_pathway_name
        if name and name != "UNMAPPED" and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered
def final_output_to_dict(model: FinalOutput) -> dict[str, Any]:
    data = {"pathway_sets": model.pathway_sets}
    data.update({key: row.model_dump(by_alias=True, mode="json") for key, row in model.rows.items()})
    return data


# %%
def load_filtered_msigdb_rows(db_path: str | Path) -> list[dict[str, str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gene_set" not in tables or "gene_set_details" not in tables:
            raise RuntimeError("Unsupported MSigDB SQLite schema: expected gene_set + gene_set_details.")
        rows = conn.execute(
            f"""
            SELECT
                gs.standard_name AS msigdb_name,
                gs.collection_name AS collection,
                TRIM(COALESCE(gsd.description_brief, '') || ' ' || COALESCE(gsd.description_full, '')) AS description
            FROM gene_set gs
            JOIN gene_set_details gsd ON gsd.gene_set_id = gs.id
            WHERE {ALLOWED_COLLECTIONS_SQL}
            ORDER BY gs.standard_name, gs.id
            """
        ).fetchall()
        return [
            {
                "msigdb_name": str(row["msigdb_name"] or ""),
                "collection": str(row["collection"] or ""),
                "description": norm_text(row["description"]),
            }
            for row in rows
            if norm_text(row["msigdb_name"])
        ]
    finally:
        conn.close()
def _scan_window_impl(query_text: str, offset: int, window_size: int, shortlist_size: int = 8) -> WindowScanPayload:
    pathway_name = norm_text(query_text.split("Rationale:", 1)[0].replace("Pathway:", ""))
    rationale = norm_text(query_text.split("Rationale:", 1)[1] if "Rationale:" in query_text else "")
    pathway_tokens = tokenize(pathway_name)
    rationale_tokens = tokenize(rationale)
    window_rows = ACTIVE_MSIGDB_ROWS[offset : offset + max(1, window_size)]
    scored: list[CandidatePreview] = []
    for row in window_rows:
        name_tokens = tokenize(row["msigdb_name"])
        desc_tokens = tokenize(row["description"])
        pre_score = 0.55 * jaccard(pathway_tokens, name_tokens) + 0.20 * jaccard(pathway_tokens, desc_tokens) + 0.10 * jaccard(rationale_tokens, name_tokens) + 0.15 * jaccard(rationale_tokens, desc_tokens)
        matched_terms = sorted((pathway_tokens | rationale_tokens) & (name_tokens | desc_tokens))[:8]
        if pre_score >= 0.03:
            scored.append(
                CandidatePreview(
                    msigdb_name=row["msigdb_name"],
                    collection=row["collection"],
                    description=short(row["description"]),
                    pre_score=round(pre_score, 6),
                    matched_terms=matched_terms,
                )
            )
    scored.sort(key=lambda candidate: (-candidate.pre_score, candidate.msigdb_name))
    return WindowScanPayload(
        offset=offset,
        end_offset=min(offset + max(1, window_size), len(ACTIVE_MSIGDB_ROWS)),
        inspected_rows=len(window_rows),
        preview_candidates=scored[: max(1, shortlist_size)],
    )


@tool
def scan_window(
    query_text: str,
    offset: int,
    window_size: int,
    shortlist_size: int = 8,
) -> dict[str, Any]:
    """Scan one fixed slice of filtered MSigDB rows and return top preview candidates."""
    return _scan_window_impl(query_text, offset, window_size, shortlist_size).model_dump(mode="json")
def _judge_candidates_impl(
    query_text: str,
    current_best_name: str,
    current_best_collection: str,
    current_best_description: str,
    challenger_name: str,
    challenger_collection: str,
    challenger_description: str,
) -> JudgeDecision:
    prompt = (
        "Compare two MSigDB pathway candidates for the same drug-pathway query.\n"
        "Pick the better biological and semantic match to the query.\nPrefer direct pathway/process relevance over generic term overlap.\n"
        "Return current_best if the challenger is not clearly better.\n\n"
        f"Query:\n{query_text}\n\nCurrent best:\n- {current_best_name} ({current_best_collection})\n- {current_best_description}\n\n"
        f"Challenger:\n- {challenger_name} ({challenger_collection})\n- {challenger_description}\n"
    )
    try:
        llm = ChatOpenAI(model=AGENT_MODEL, temperature=0).with_structured_output(JudgeDecision)
        return llm.invoke(prompt)
    except Exception as exc:
        return JudgeDecision(
            winner="current_best",
            rationale=f"judge_error_keep_current: {short(exc, 180)}",
            confidence=0.0,
        )


@tool
def judge_candidates(
    query_text: str,
    current_best_name: str,
    current_best_collection: str,
    current_best_description: str,
    challenger_name: str,
    challenger_collection: str,
    challenger_description: str,
) -> dict[str, Any]:
    """Judge which of two candidate pathways better matches the query."""
    return _judge_candidates_impl(
        query_text,
        current_best_name,
        current_best_collection,
        current_best_description,
        challenger_name,
        challenger_collection,
        challenger_description,
    ).model_dump(mode="json")


# %%
def build_iteration_agent(model_name: str = AGENT_MODEL):
    prompt = (
        "You are a sliding-window MSigDB pathway-mapping agent.\n"
        "For each request, call scan_window exactly once.\n"
        "If preview_candidates is empty, return action=no_match.\n"
        "If there is a plausible candidate and no current champion, promote the best preview candidate.\n"
        "If there is a current champion and a plausible challenger, call judge_candidates exactly once.\n"
        "Return only a structured IterationDecision.\n"
        "Do not invent candidates that were not supplied by scan_window or the current champion."
    )
    return create_agent(
        model=ChatOpenAI(model=model_name, temperature=0),
        tools=[scan_window, judge_candidates],
        system_prompt=prompt,
        response_format=ToolStrategy(IterationDecision),
        name="pathway_sliding_window_agent",
    )
def _coerce_candidate(candidate: Optional[CandidatePreview | dict[str, Any]], lookup: dict[str, CandidatePreview]) -> Optional[CandidatePreview]:
    if candidate is None:
        return None
    candidate = candidate if isinstance(candidate, CandidatePreview) else CandidatePreview.model_validate(candidate)
    return lookup.get(candidate.msigdb_name, candidate)
def map_row_with_sliding_agent(
    row_key: str,
    entry: dict[str, Any],
    agent: Any,
    window_size: int,
    shortlist_size: int = 8,
) -> TraceVerification:
    pathway_name = norm_text(get_pathway_name(entry))
    rationale = norm_text(entry.get("Rationale", ""))
    if not pathway_name:
        return TraceVerification(
            row=row_key,
            original_pathway_name="",
            rationale=rationale,
            final_mapped_msigdb="UNMAPPED",
            verdict="unmapped",
            decision_reason=None,
            failure_type="missing_pathway_name",
            failure_reason="Original pathway name is missing.",
        )

    query_text = f"Pathway: {pathway_name}\nRationale: {rationale or 'N/A'}"
    champion: Optional[CandidatePreview] = None
    window_trace: list[WindowTraceEntry] = []
    seen_candidates: dict[str, CandidatePreview] = {}
    iteration_failures = 0

    for window_index, offset in enumerate(range(0, len(ACTIVE_MSIGDB_ROWS), max(1, window_size)), start=1):
        payload = _scan_window_impl(query_text, offset, window_size, shortlist_size)
        preview_lookup = {candidate.msigdb_name: candidate for candidate in payload.preview_candidates}
        seen_candidates.update(preview_lookup)
        current_blob = champion.model_dump(mode="json") if champion else None
        try:
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"query_text: {query_text}\n"
                                f"offset: {offset}\nwindow_size: {window_size}\nshortlist_size: {shortlist_size}\n"
                                f"current_champion: {json.dumps(current_blob, ensure_ascii=False)}"
                            ),
                        }
                    ]
                }
            )
            decision = IterationDecision.model_validate(result["structured_response"])
            selection = _coerce_candidate(decision.window_selection, preview_lookup)
            judge_result = decision.judge_result
            if champion is None:
                champion = selection if decision.action == "promote" else None
            elif selection is not None:
                champion_lookup = {champion.msigdb_name: champion, **preview_lookup}
                champion = _coerce_candidate(decision.champion_after_round, champion_lookup) or champion
            window_trace.append(
                WindowTraceEntry(
                    window_index=window_index,
                    offset=payload.offset,
                    end_offset=payload.end_offset,
                    inspected_rows=payload.inspected_rows,
                    preview_candidates=payload.preview_candidates,
                    window_selection=selection,
                    judge_result=judge_result,
                    champion_after_round=champion,
                    round_reason=decision.round_reason,
                )
            )
        except Exception as exc:
            iteration_failures += 1
            window_trace.append(
                WindowTraceEntry(
                    window_index=window_index,
                    offset=payload.offset,
                    end_offset=payload.end_offset,
                    inspected_rows=payload.inspected_rows,
                    preview_candidates=payload.preview_candidates,
                    window_selection=None,
                    judge_result=None,
                    champion_after_round=champion,
                    round_reason=f"no_match: iteration_agent_error: {short(exc, 180)}",
                )
            )

    top_candidates = sorted(seen_candidates.values(), key=lambda candidate: (-candidate.pre_score, candidate.msigdb_name))[:10]
    verdict = "mapped" if champion else "unmapped"
    failure_type = None if champion else "no_candidate_found"
    failure_reason = None if champion else "No champion remained after all sliding-window rounds."
    if not champion and iteration_failures:
        failure_type = "iteration_agent_error"
        failure_reason = "All rounds ended without a valid champion and at least one agent iteration failed."
    decision_reason = (
        f"Selected after {len(window_trace)} sliding-window rounds with a final champion of {champion.msigdb_name}."
        if champion
        else None
    )
    return TraceVerification(
        row=row_key,
        original_pathway_name=pathway_name,
        rationale=rationale,
        final_mapped_msigdb=champion.msigdb_name if champion else "UNMAPPED",
        verdict=verdict,
        selected_candidate=champion,
        top_candidates=top_candidates,
        window_trace=window_trace,
        decision_reason=decision_reason,
        failure_type=failure_type,
        failure_reason=failure_reason,
    )
def run_file(
    input_file: str | Path = DEFAULT_INPUT_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    out_final_dir: str | Path = DEFAULT_FINAL_DIR,
    out_trace_dir: str | Path = DEFAULT_TRACE_DIR,
    window_size_override: Optional[int] = None,
    shortlist_size: int = 8,
    model_name: str = AGENT_MODEL,
) -> tuple[Path, Path]:
    global ACTIVE_MSIGDB_ROWS

    input_file = Path(input_file)
    out_final_dir = Path(out_final_dir)
    out_trace_dir = Path(out_trace_dir)
    ACTIVE_MSIGDB_ROWS = load_filtered_msigdb_rows(db_path)
    input_data = load_json(input_file)
    filtered_rows, stats = filter_rows_for_mapping(input_data)
    window_size = max(1, window_size_override or math.ceil(len(ACTIVE_MSIGDB_ROWS) * 0.01))
    agent = build_iteration_agent(model_name)

    final_rows: dict[str, FinalRow] = {}
    verifications: list[TraceVerification] = []
    mapped_count = 0
    for row_key, entry in sorted(filtered_rows.items(), key=lambda item: row_order(item[0])):
        verification = map_row_with_sliding_agent(row_key, entry, agent, window_size, shortlist_size)
        if verification.verdict == "mapped":
            mapped_count += 1
        final_rows[row_key] = FinalRow(
            mapped_msigdb_pathway_name=verification.final_mapped_msigdb,
            original_pathway_name=get_pathway_name(entry),
            regulation=str(entry.get("Regulation", "")),
            baseline_effect=str(entry.get("Baseline effect", "")),
            rationale=str(entry.get("Rationale", "")),
            relationship_classification=get_relationship_classification(entry),
            references=listify_refs(entry.get("References")),
            verdict=verification.verdict,
            mapping_method="langchain_agentic_sliding_window",
            agent_decision_reason=verification.decision_reason,
            agent_failure_reason=verification.failure_reason,
        )
        verifications.append(verification)

    final_output = FinalOutput(pathway_sets=collect_pathway_sets(final_rows), rows=final_rows)
    trace_output = TraceOutput(
        summary=TraceSummary(
            drug_name=input_file.stem,
            input_file=str(input_file),
            rows_before_filter=stats["rows_before_filter"],
            rows_after_include_filter=stats["rows_after_include_filter"],
            rows_after_filter=stats["rows_after_filter"],
            rows_dropped_by_include_filter=stats["rows_dropped_by_include_filter"],
            rows_dropped_by_relationship_class_filter=stats["rows_dropped_by_relationship_class_filter"],
            mapped_count=mapped_count,
            unmapped_count=len(verifications) - mapped_count,
            agent_model=model_name,
            filtered_msigdb_rows=len(ACTIVE_MSIGDB_ROWS),
            window_size=window_size,
        ),
        verifications=verifications,
    )

    final_path = out_final_dir / f"{input_file.stem}.json"
    trace_path = out_trace_dir / f"{input_file.stem}_trace_pathway_mapping.json"
    save_json(final_output_to_dict(final_output), final_path)
    save_json(trace_output.model_dump(by_alias=True, mode="json"), trace_path)
    return final_path, trace_path


# %%
if __name__ == "__main__":
    final_path, trace_path = run_file(input_file=DEFAULT_INPUT_PATH, db_path=DEFAULT_DB_PATH, out_final_dir=DEFAULT_FINAL_DIR, out_trace_dir=DEFAULT_TRACE_DIR, window_size_override=3); print(final_path); print(trace_path)
