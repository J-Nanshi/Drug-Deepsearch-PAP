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
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

AGENT_MODEL = "gpt-5.4-mini"
VERBOSE_LOGS = True
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
ACTIVE_MSIGDB_ROWS: list[dict[str, str]] = []


# %%
class RoundDecision(BaseModel):
    action: Literal["promote", "keep_current", "no_match"]
    selected_pathway: Optional[str] = None
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    round_number: int = 0


RoundDecision.model_rebuild()


def log(msg: str) -> None:
    if VERBOSE_LOGS:
        print(msg)


# %%
def load_json(path: str | Path) -> dict[str, Any]:
    log(f"[load_json] reading: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    log(f"[load_json] loaded keys: {len(data)}")
    return data


def save_json(obj: Any, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    log(f"[save_json] writing: {path}")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
    log("[save_json] write complete")


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def short(value: Any, max_len: int = 220) -> str:
    text = norm_text(value)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


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
            entry.get("Pathwayâ€“drug relationship classification", ""),
        )
    )


def filter_rows_for_mapping(input_data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    log(f"[filter_rows_for_mapping] input rows: {len(input_data)}")
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
    stats = {
        "rows_before_filter": len(input_data),
        "rows_after_include_filter": len(included),
        "rows_after_filter": len(filtered),
        "rows_dropped_by_include_filter": len(input_data) - len(included),
        "rows_dropped_by_relationship_class_filter": len(included) - len(filtered),
    }
    log(f"[filter_rows_for_mapping] stats: {stats}")
    return filtered, stats


def collect_pathway_sets(final_rows: dict[str, dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for _, row in sorted(final_rows.items(), key=lambda item: row_order(item[0])):
        name = str(row.get("Mapped MSigDB Pathway Name", "")).strip()
        if name and name != "UNMAPPED" and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _extract_json_object(text: str) -> dict[str, Any]:
    text = norm_text(text)
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return {}
    return {}


# %%
def load_filtered_msigdb_rows(db_path: str | Path) -> list[dict[str, str]]:
    log(f"[load_filtered_msigdb_rows] connecting DB: {db_path}")
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
            WHERE gsd.source_species_code = 'HS'
              AND ({ALLOWED_COLLECTIONS_SQL})
            ORDER BY gs.standard_name, gs.id
            """
        ).fetchall()
        out = [
            {
                "msigdb_name": str(row["msigdb_name"] or ""),
                "collection": str(row["collection"] or ""),
                "description": norm_text(row["description"]),
            }
            for row in rows
            if norm_text(row["msigdb_name"])
        ]
        log(f"[load_filtered_msigdb_rows] loaded filtered rows: {len(out)}")
        return out
    finally:
        conn.close()
        log("[load_filtered_msigdb_rows] DB connection closed")


def _scan_window_impl(query_text: str, offset: int, window_size: int, shortlist_size: int = 8) -> dict[str, Any]:
    log(f"[scan_window_impl] offset={offset} window_size={window_size} shortlist={shortlist_size}")
    window_rows = ACTIVE_MSIGDB_ROWS[offset : offset + max(1, window_size)]
    base = {
        "offset": offset,
        "end_offset": min(offset + max(1, window_size), len(ACTIVE_MSIGDB_ROWS)),
        "inspected_rows": len(window_rows),
        "preview_candidates": [],
    }
    if not window_rows:
        log("[scan_window_impl] empty window")
        return base

    rows_block = "\n".join(
        f"{idx}. {row['msigdb_name']} | {row['collection']} | {short(row['description'], 260)}"
        for idx, row in enumerate(window_rows, start=1)
    )
    prompt = (
        "Score each row confidence in [0,1] for biological+semantic match to the query.\n"
        "Return strict JSON with key 'scores': [{row_index:int, confidence:float, rationale:str}].\n"
        f"Query:\n{query_text}\n\nRows:\n{rows_block}\n"
    )
    scores_payload: dict[str, Any] = {}
    try:
        llm = ChatOpenAI(model=AGENT_MODEL, temperature=0)
        response = llm.invoke(prompt)
        content = response.content if isinstance(response.content, str) else json.dumps(response.content)
        scores_payload = _extract_json_object(content)
    except Exception:
        scores_payload = {}
        log("[scan_window_impl] LLM score call failed; continuing with empty scores")

    raw_scores = scores_payload.get("scores", []) if isinstance(scores_payload, dict) else []
    score_map: dict[int, float] = {}
    for item in raw_scores if isinstance(raw_scores, list) else []:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("row_index", 0) or 0)
        conf = max(0.0, min(1.0, float(item.get("confidence", 0.0) or 0.0)))
        if 1 <= idx <= len(window_rows):
            score_map[idx] = conf

    candidates = [
        {
            "msigdb_name": row["msigdb_name"],
            "collection": row["collection"],
            "description": short(row["description"]),
            "pre_score": round(score_map.get(idx, 0.0), 6),
            "matched_terms": [],
        }
        for idx, row in enumerate(window_rows, start=1)
        if score_map.get(idx, 0.0) >= 0.2
    ]
    candidates.sort(key=lambda c: (-float(c.get("pre_score", 0.0)), str(c.get("msigdb_name", ""))))
    base["preview_candidates"] = candidates[: max(1, shortlist_size)]
    log(
        f"[scan_window_impl] inspected={base['inspected_rows']} "
        f"kept={len(base['preview_candidates'])}"
    )
    return base


@tool
def scan_window(
    query_text: str,
    offset: int,
    window_size: int,
    shortlist_size: int = 8,
) -> dict[str, Any]:
    """Scan a fixed slice of filtered MSigDB rows and return top preview candidates."""
    return _scan_window_impl(query_text, offset, window_size, shortlist_size)


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
    log(
        "[judge_candidates] comparing "
        f"current={current_best_name} challenger={challenger_name}"
    )
    prompt = (
        "Choose better pathway for the query. Return strict JSON with keys winner(current_best|challenger), selected_pathway, rationale, confidence.\n"
        f"Query:\n{query_text}\n\n"
        f"Current:\n- {current_best_name} ({current_best_collection})\n- {current_best_description}\n\n"
        f"Challenger:\n- {challenger_name} ({challenger_collection})\n- {challenger_description}\n"
    )
    try:
        llm = ChatOpenAI(model=AGENT_MODEL, temperature=0)
        response = llm.invoke(prompt)
        content = response.content if isinstance(response.content, str) else json.dumps(response.content)
        payload = _extract_json_object(content)
    except Exception as exc:
        payload = {"winner": "current_best", "selected_pathway": current_best_name, "rationale": f"judge_error_keep_current: {short(exc, 180)}", "confidence": 0.0}
        log(f"[judge_candidates] error -> keep current: {short(exc, 120)}")

    winner = str(payload.get("winner", "current_best"))
    if winner not in {"current_best", "challenger"}:
        winner = "current_best"
    selected_pathway = str(payload.get("selected_pathway", current_best_name if winner == "current_best" else challenger_name))
    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0)))
    out = {
        "winner": winner,
        "selected_pathway": selected_pathway,
        "rationale": str(payload.get("rationale", "")),
        "confidence": confidence,
    }
    log(
        "[judge_candidates] result "
        f"winner={out['winner']} selected={out['selected_pathway']} conf={out['confidence']:.3f}"
    )
    return out


# %%
def build_iteration_agent(model_name: str = AGENT_MODEL):
    log(f"[build_iteration_agent] building agent with model={model_name}")
    prompt = (
        "You are a sliding-window pathway-mapping agent.\n"
        "For each round: call scan_window once.\n"
        "If no preview candidates, return action=no_match.\n"
        "If no current champion and candidates exist, choose one and action=promote.\n"
        "If champion exists, optionally call judge_candidates and then return action promote/keep_current/no_match.\n"
        "Return only structured RoundDecision. selected_pathway must be a preview candidate or current champion."
    )
    agent = create_agent(
        model=ChatOpenAI(model=model_name, temperature=0),
        tools=[scan_window, judge_candidates],
        system_prompt=prompt,
        response_format=ToolStrategy(RoundDecision),
        name="pathway_sliding_window_agent",
    )
    log("[build_iteration_agent] agent ready")
    return agent


def map_row_with_sliding_agent(
    row_key: str,
    entry: dict[str, Any],
    agent: Any,
    window_size: int,
    shortlist_size: int = 8,
) -> dict[str, Any]:
    pathway_name = norm_text(get_pathway_name(entry))
    rationale = norm_text(entry.get("Rationale", ""))
    log(f"[map_row] {row_key} pathway={short(pathway_name, 70)}")
    if not pathway_name:
        return {
            "Row": row_key,
            "Original Pathway Name": "",
            "Rationale": rationale,
            "Final Mapped MSigDB": "UNMAPPED",
            "verdict": "unmapped",
            "selected_candidate": None,
            "top_candidates": [],
            "window_trace": [],
            "decision_reason": None,
            "failure_type": "missing_pathway_name",
            "failure_reason": "Original pathway name is missing.",
        }

    query_text = f"Pathway: {pathway_name}\nRationale: {rationale or 'N/A'}"
    champion: Optional[dict[str, Any]] = None
    window_trace: list[dict[str, Any]] = []
    seen_candidates: dict[str, dict[str, Any]] = {}
    iteration_failures = 0

    for window_index, offset in enumerate(range(0, len(ACTIVE_MSIGDB_ROWS), max(1, window_size)), start=1):
        log(f"[map_row] {row_key} round={window_index} offset={offset}")
        payload = _scan_window_impl(query_text, offset, window_size, shortlist_size)
        preview = payload.get("preview_candidates", [])
        preview_lookup = {str(candidate.get("msigdb_name", "")): candidate for candidate in preview}
        seen_candidates.update({k: v for k, v in preview_lookup.items() if k})
        champion_name = champion.get("msigdb_name", "") if champion else ""
        log(
            f"[map_row] {row_key} round={window_index} "
            f"preview_candidates={len(preview)} champion={champion_name or 'NONE'}"
        )

        try:
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"query_text: {query_text}\n"
                                f"round_number: {window_index}\n"
                                f"offset: {offset}\nwindow_size: {window_size}\nshortlist_size: {shortlist_size}\n"
                                f"current_champion: {champion_name or 'NONE'}"
                            ),
                        }
                    ]
                }
            )
            structured = result.get("structured_response")
            decision = structured if isinstance(structured, RoundDecision) else RoundDecision.model_validate(structured)
            selected_pathway = norm_text(decision.selected_pathway)
            if decision.action == "promote" and selected_pathway in preview_lookup:
                champion = preview_lookup[selected_pathway]
            elif decision.action == "promote" and champion and selected_pathway == champion.get("msigdb_name", ""):
                champion = champion
            elif decision.action == "keep_current":
                champion = champion
            log(
                f"[map_row] {row_key} round={window_index} action={decision.action} "
                f"selected={selected_pathway or 'NONE'} conf={decision.confidence:.3f} "
                f"champion={(champion or {}).get('msigdb_name', 'NONE')}"
            )

            window_trace.append(
                {
                    "round_number": window_index,
                    "offset": payload.get("offset", offset),
                    "end_offset": payload.get("end_offset", min(offset + window_size, len(ACTIVE_MSIGDB_ROWS))),
                    "inspected_rows": payload.get("inspected_rows", 0),
                    "selected_pathway": selected_pathway or None,
                    "rationale": decision.rationale,
                    "confidence": round(float(decision.confidence or 0.0), 6),
                    "action": decision.action,
                    "preview_candidates": preview,
                }
            )
        except Exception as exc:
            iteration_failures += 1
            log(f"[map_row] {row_key} round={window_index} iteration error: {short(exc, 120)}")
            window_trace.append(
                {
                    "round_number": window_index,
                    "offset": payload.get("offset", offset),
                    "end_offset": payload.get("end_offset", min(offset + window_size, len(ACTIVE_MSIGDB_ROWS))),
                    "inspected_rows": payload.get("inspected_rows", 0),
                    "selected_pathway": None,
                    "rationale": f"no_match: iteration_agent_error: {short(exc, 180)}",
                    "confidence": 0.0,
                    "action": "no_match",
                    "preview_candidates": preview,
                }
            )

    top_candidates = sorted(seen_candidates.values(), key=lambda c: (-float(c.get("pre_score", 0.0)), str(c.get("msigdb_name", ""))))[:10]
    verdict = "mapped" if champion else "unmapped"
    failure_type = None if champion else ("iteration_agent_error" if iteration_failures else "no_candidate_found")
    failure_reason = None if champion else (
        "All rounds ended without a valid champion and at least one agent iteration failed."
        if iteration_failures
        else "No champion remained after all sliding-window rounds."
    )
    decision_reason = (
        f"Selected after {len(window_trace)} sliding-window rounds with final pathway {champion.get('msigdb_name', '')}."
        if champion
        else None
    )
    log(
        f"[map_row] {row_key} verdict={verdict} final={(champion or {}).get('msigdb_name', 'UNMAPPED')} "
        f"top_candidates={len(top_candidates)}"
    )
    return {
        "Row": row_key,
        "Original Pathway Name": pathway_name,
        "Rationale": rationale,
        "Final Mapped MSigDB": champion.get("msigdb_name", "UNMAPPED") if champion else "UNMAPPED",
        "verdict": verdict,
        "selected_candidate": champion,
        "top_candidates": top_candidates,
        "window_trace": window_trace,
        "decision_reason": decision_reason,
        "failure_type": failure_type,
        "failure_reason": failure_reason,
    }


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

    log("[run_file] starting run")
    log(f"[run_file] input_file={input_file}")
    log(f"[run_file] db_path={db_path}")
    input_file = Path(input_file)
    out_final_dir = Path(out_final_dir)
    out_trace_dir = Path(out_trace_dir)
    ACTIVE_MSIGDB_ROWS = load_filtered_msigdb_rows(db_path)
    log(f"[run_file] ACTIVE_MSIGDB_ROWS={len(ACTIVE_MSIGDB_ROWS)}")

    input_data = load_json(input_file)
    filtered_rows, stats = filter_rows_for_mapping(input_data)
    window_size = max(1, window_size_override or math.ceil(len(ACTIVE_MSIGDB_ROWS) * 0.01))
    log(f"[run_file] window_size={window_size} (override={window_size_override})")
    agent = build_iteration_agent(model_name)

    final_rows: dict[str, dict[str, Any]] = {}
    verifications: list[dict[str, Any]] = []
    mapped_count = 0

    for row_key, entry in sorted(filtered_rows.items(), key=lambda item: row_order(item[0])):
        log(f"[run_file] processing {row_key}")
        verification = map_row_with_sliding_agent(row_key, entry, agent, window_size, shortlist_size)
        if verification["verdict"] == "mapped":
            mapped_count += 1

        final_rows[row_key] = {
            "Mapped MSigDB Pathway Name": verification["Final Mapped MSigDB"],
            "Original Pathway Name": get_pathway_name(entry),
            "Regulation": str(entry.get("Regulation", "")),
            "Baseline effect": str(entry.get("Baseline effect", "")),
            "Rationale": str(entry.get("Rationale", "")),
            "Pathway-drug relationship classification": get_relationship_classification(entry),
            "References": listify_refs(entry.get("References")),
            "verdict": verification["verdict"],
            "mapping_method": "langchain_agentic_sliding_window",
            "agent_decision_reason": verification.get("decision_reason"),
            "agent_failure_reason": verification.get("failure_reason"),
        }
        verifications.append(verification)

    final_output: dict[str, Any] = {"pathway_sets": collect_pathway_sets(final_rows)}
    final_output.update(final_rows)
    trace_output = {
        "summary": {
            "drug_name": input_file.stem,
            "input_file": str(input_file),
            "rows_before_filter": stats["rows_before_filter"],
            "rows_after_include_filter": stats["rows_after_include_filter"],
            "rows_after_filter": stats["rows_after_filter"],
            "rows_dropped_by_include_filter": stats["rows_dropped_by_include_filter"],
            "rows_dropped_by_relationship_class_filter": stats["rows_dropped_by_relationship_class_filter"],
            "mapped_count": mapped_count,
            "unmapped_count": len(verifications) - mapped_count,
            "agent_model": model_name,
            "filtered_msigdb_rows": len(ACTIVE_MSIGDB_ROWS),
            "window_size": window_size,
        },
        "verifications": verifications,
    }

    final_path = out_final_dir / f"{input_file.stem}.json"
    trace_path = out_trace_dir / f"{input_file.stem}_trace_pathway_mapping.json"
    save_json(final_output, final_path)
    save_json(trace_output, trace_path)
    log(f"[run_file] done final={final_path}")
    log(f"[run_file] done trace={trace_path}")
    return final_path, trace_path


# %%
if __name__ == "__main__":
   final_path, trace_path = run_file(
    input_file=Path(r"D:\GS\Drug-Deepsearch-PAP\output\lung_cancer\step2_factcheck_json\afatinib.json"),
    db_path=Path(r"D:\GS\Drug-Deepsearch-PAP\utils\msigdb_v2025.1.Hs.db"),
    out_final_dir=Path(r"D:\GS\Drug-Deepsearch-PAP\output\lung_cancer\trial\langchain_agentic_sliding\mapping"),
    out_trace_dir=Path(r"D:\GS\Drug-Deepsearch-PAP\output\lung_cancer\trial\langchain_agentic_sliding\trace"),
    window_size_override=350
    )
print(final_path)
print(trace_path)

# %%
