# %% [markdown]
# # Ribociclib Pathway Evidence Table — Extract + Baseline Fact-check + Normalize + Include/Exclude (Cell-based .py)
#
# Adds an LLM decision on whether each pathway row **should be included** in the final output
# (based on baseline breast cancer + ribociclib relevance + internal consistency).
#
# Output is a dict matching the strict schema per row, including:
# - Verdict (correct/corrected)
# - Normalized Regulation & Baseline effect
# - Classification + reasoning
# - Include decision + reasoning
# - References resolved to URLs from Sources section

# %%
# -------------------------
# Imports
# -------------------------
import json
import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# %%
# -------------------------
# Minimal .env loader (no dependency)
# -------------------------
def _load_dotenv_locations() -> List[Path]:
    paths = [Path(".").resolve() / ".env"]
    try:
        paths.append(Path(__file__).resolve().parent / ".env")
    except Exception:
        pass
    return paths


def load_dotenv_if_exists() -> None:
    def _parse_env_file(p: Path) -> Dict[str, str]:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return {}
        out: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            out[k] = v
        return out

    for p in _load_dotenv_locations():
        if p.exists():
            envs = _parse_env_file(p)
            for k, v in envs.items():
                if os.getenv(k) is None:
                    os.environ[k] = v
            break

# %%
# -------------------------
# Config (EDIT THIS CELL)
# -------------------------
INPUT_MD = "Ribociclib_Report.md"
DRUG_NAME = "Ribociclib"
MODEL = "gpt-5.1"
TEMPERATURE = 0.2
OUT_JSON = "ribociclib_step2_trial2_out.json"   # "" disables save
CACHE_PATH = ".cache_ribociclib_llm.json" # "" disables cache
API_BASE = ""                             # optional OpenAI-compatible base URL
NO_LLM = False                            # True = extract + resolve refs only

PROMPT_VERSION = "v3_include_decision"    # bump to invalidate cache after prompt/schema edits

# %%
# -------------------------
# Data structures
# -------------------------
@dataclass
class TableRow:
    idx: int
    pathway: str
    regulation: str
    effect: str
    rationale: str
    key_refs_raw: str

    def to_compact_dict(self) -> Dict[str, str]:
        return {
            "Pathway ID/Name": self.pathway,
            "Regulation (raw)": self.regulation,
            "Effect (raw)": self.effect,
            "Rationale (raw)": self.rationale,
            "Key references (raw)": self.key_refs_raw,
        }

# %%
# -------------------------
# Markdown parsing + reference helpers
# -------------------------
DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2212"
DASH_RE = re.compile(f"[{DASH_CHARS}]")
URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

def _normalize_dashes(s: str) -> str:
    return DASH_RE.sub("-", s or "")

def _strip_md(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def parse_reference_numbers(ref_cell: str) -> List[int]:
    if not ref_cell:
        return []
    s = _normalize_dashes(ref_cell)
    m = re.search(r"\[([^\]]+)\]", s)
    inside = m.group(1) if m else s

    nums: List[int] = []
    for part in inside.split(","):
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"[^\d\-]", "", part)
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                start, end = int(a), int(b)
                if start <= end:
                    nums.extend(range(start, end + 1))
                else:
                    nums.extend([start, end])
        else:
            if part.isdigit():
                nums.append(int(part))

    seen = set()
    out: List[int] = []
    for n in nums:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out

def parse_sources_map(md_text: str) -> Dict[int, List[str]]:
    sources: Dict[int, List[str]] = {}
    for line in md_text.splitlines():
        line = line.strip()
        m = re.match(r"^\[(\d+)\]\s+(.*)$", line)
        if not m:
            continue
        n = int(m.group(1))
        rest = m.group(2)

        urls = URL_RE.findall(rest) or []
        urls = [u.rstrip(".,;") for u in urls]

        if not urls:
            pmid_m = re.search(r"\bPMID[:\s]*([0-9]{4,10})\b", rest, flags=re.IGNORECASE)
            if pmid_m:
                pmid = pmid_m.group(1)
                urls = [f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"]

        uniq: List[str] = []
        seen = set()
        for u in urls:
            if u not in seen:
                uniq.append(u)
                seen.add(u)
        if uniq:
            sources[n] = uniq
    return sources

def _find_section_block(md_text: str, heading: str) -> str:
    pat = re.compile(rf"^(?P<hashes>#+)\s+{re.escape(heading)}\s*$", re.MULTILINE)
    m = pat.search(md_text)
    if not m:
        return ""
    level = len(m.group("hashes"))
    start = m.end()
    next_pat = re.compile(rf"^#{{1,{level}}}\s+\S.*$", re.MULTILINE)
    m2 = next_pat.search(md_text, pos=start)
    end = m2.start() if m2 else len(md_text)
    return md_text[start:end].strip("\n")

def extract_markdown_table_from_section(section_text: str) -> Tuple[List[str], List[List[str]]]:
    lines = section_text.splitlines()
    header_i = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and "Pathway" in line:
            if i + 1 < len(lines) and re.match(r"^\s*\|?\s*[-: ]+\|", lines[i + 1].strip()):
                header_i = i
                break
    if header_i is None:
        return ([], [])

    header_line = lines[header_i].strip()
    sep_line = lines[header_i + 1].strip()

    headers = [h.strip() for h in header_line.strip("|").split("|")]
    ncols = len(headers)

    rows: List[List[str]] = []
    buf = ""
    for j in range(header_i + 2, len(lines)):
        line = lines[j]
        if not line.strip().startswith("|"):
            if buf.strip():
                cells = [c.strip() for c in buf.strip().strip("|").split("|")]
                if len(cells) >= ncols:
                    rows.append(cells[:ncols])
            break

        if not buf:
            buf = line.strip()
        else:
            buf = buf.rstrip() + " " + line.strip().lstrip("|").strip()

        if buf.count("|") >= ncols + 1:
            cells = [c.strip() for c in buf.strip().strip("|").split("|")]
            if len(cells) >= ncols:
                rows.append(cells[:ncols])
            buf = ""

    if buf.strip():
        cells = [c.strip() for c in buf.strip().strip("|").split("|")]
        if len(cells) >= ncols:
            rows.append(cells[:ncols])

    if not re.match(r"^\s*\|?\s*[-: ]+\|", sep_line):
        return ([], [])
    return (headers, rows)

def _pick_column(headers: List[str], keywords: List[str]) -> Optional[int]:
    hnorm = [h.lower() for h in headers]
    for k in keywords:
        k = k.lower()
        for i, h in enumerate(hnorm):
            if k in h:
                return i
    return None

def extract_pathway_evidence_table(md_text: str) -> List[TableRow]:
    section = _find_section_block(md_text, "Pathway Evidence Table (Main Focus)") or md_text
    headers, rows = extract_markdown_table_from_section(section)
    if not headers or not rows:
        raise ValueError('Could not locate markdown table under "Pathway Evidence Table (Main Focus)".')

    col_path = _pick_column(headers, ["pathway"])
    col_reg  = _pick_column(headers, ["regulation"])
    col_eff  = _pick_column(headers, ["effect"])
    col_rat  = _pick_column(headers, ["rationale"])
    col_ref  = _pick_column(headers, ["key ref", "reference", "ref"])
    col_idx  = _pick_column(headers, ["#", "no."])

    if col_path is None or col_reg is None or col_eff is None or col_rat is None:
        raise ValueError(f"Missing expected columns. Headers found: {headers}")

    out: List[TableRow] = []
    for r in rows:
        if all(not c.strip() for c in r):
            continue

        ridx = None
        if col_idx is not None and col_idx < len(r):
            s = re.sub(r"[^\d]", "", r[col_idx])
            if s.isdigit():
                ridx = int(s)
        if ridx is None:
            ridx = len(out) + 1

        out.append(
            TableRow(
                idx=ridx,
                pathway=_strip_md(r[col_path]) if col_path < len(r) else "",
                regulation=_strip_md(r[col_reg]) if col_reg < len(r) else "",
                effect=_strip_md(r[col_eff]) if col_eff < len(r) else "",
                rationale=_strip_md(r[col_rat]) if col_rat < len(r) else "",
                key_refs_raw=_strip_md(r[col_ref]) if (col_ref is not None and col_ref < len(r)) else "",
            )
        )
    return out

def resolve_refs_to_urls(ref_nums: List[int], sources_map: Dict[int, List[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for n in ref_nums:
        for u in sources_map.get(n, []):
            if u and u not in seen:
                out.append(u)
                seen.add(u)
    return out

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

# %%
# -------------------------
# LLM: validate + normalize + include/exclude decision
# -------------------------
ALLOWED_VERDICTS = {"correct", "corrected"}
ALLOWED_INCORRECT = {"regulation", "effect"}
ALLOWED_REG = {"upregulation", "downregulation"}
ALLOWED_EFF = {"sensitive", "resistant"}
ALLOWED_CLASS = {
    "mechanistically accurate",
    "clinically validated",
    "inferred from mechanistic evidence",
    "experimental (clinical trials)",
    "possibly related",
    "not needed",
}
ALLOWED_INCLUDE = {"include", "exclude"}

def llm_validate_normalize_and_include(
    *,
    row: TableRow,
    drug_name: str,
    model: str,
    temperature: float,
    api_base: Optional[str] = None,
    max_retries: int = 1,
) -> Dict:
    """
    LLM must return JSON with keys:
      - Verdict: correct|corrected
      - Incorrect_entries: [] or ["regulation"] or ["effect"] or ["regulation","effect"]
      - Regulation: upregulation|downregulation
      - Baseline effect: sensitive|resistant
      - Rationale
      - Pathway–drug relationship classification (allowed)
      - Classification reasoning (1–2 sentences)
      - Include decision: include|exclude
      - Inclusion reasoning (1–2 sentences)
    """
    from openai import OpenAI
    client = OpenAI(base_url=api_base) if api_base else OpenAI()

    system = (
        "SYSTEM ROLE:\n"
        "You are an expert in breast cancer molecular biology, CDK4/6 inhibitor pharmacology, "
        "and clinical translational oncology. You are strictly evidence-driven and baseline-aware.\n\n"
        "STRICT ASSUMPTIONS:\n"
        "• Disease context: Human breast cancer ONLY\n"
        f"• Drug context: {drug_name} (CDK4/6 inhibitor)\n"
        "• Temporal context: BASELINE pathway biology (pre-treatment)\n"
        "• Evidence hierarchy: clinical > translational > mechanistic > inferential\n\n"
        "NON-NEGOTIABLE RULES:\n"
        "- Baseline means BEFORE ribociclib is administered.\n"
        "- Do NOT treat post-treatment/adaptive changes as baseline unless explicitly stated.\n"
        "- Normalize to SINGLE-WORD values:\n"
        "  Regulation: upregulation|downregulation\n"
        "  Baseline effect: sensitive|resistant\n"
        "- Verdict:\n"
        "  correct: baseline-accurate and consistent\n"
        "  corrected: regulation and/or effect needed correction\n"
        "- If Verdict=corrected -> Incorrect_entries MUST be non-empty.\n"
        "- Decide whether the pathway row should be INCLUDED in final output:\n"
        "  include: relevant/needed for baseline ribociclib response in breast cancer\n"
        "  exclude: not needed, not relevant, speculative, or post-treatment framed\n"
        "- Output JSON only.\n"
    )

    allowed_class_list = sorted(ALLOWED_CLASS)
    allowed_inc_list = sorted(ALLOWED_INCLUDE)

    base_prompt = (
        "Given one extracted row, do ALL of the following under baseline breast cancer + ribociclib:\n"
        "1) Fact-check regulation/effect/rationale.\n"
        "2) Assign Verdict (correct|corrected).\n"
        "3) If corrected, set Incorrect_entries to list which were wrong: regulation/effect.\n"
        "4) Normalize Regulation and Baseline effect to allowed single-word values.\n"
        "5) Classify pathway–drug relationship using ONE allowed category.\n"
        "6) Decide Include decision (include|exclude) for whether this row should appear in final report.\n\n"
        f"Allowed classifications: {allowed_class_list}\n"
        f"Allowed include decisions: {allowed_inc_list}\n\n"
        "Return STRICT JSON with EXACT keys:\n"
        "{\n"
        '  "Verdict": "correct" | "corrected",\n'
        '  "Incorrect_entries": [],\n'
        '  "Regulation": "upregulation" | "downregulation",\n'
        '  "Baseline effect": "sensitive" | "resistant",\n'
        '  "Rationale": "<final baseline-accurate rationale>",\n'
        '  "Pathway–drug relationship classification": "<one allowed category>",\n'
        '  "Classification reasoning": "<1–2 sentences>",\n'
        '  "Include decision": "include" | "exclude",\n'
        '  "Inclusion reasoning": "<1–2 sentences>"\n'
        "}\n\n"
        "Row:\n"
        f"{json.dumps(row.to_compact_dict(), ensure_ascii=False)}"
    )

    def _call_llm(user_prompt: str) -> Dict:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    def _validate(data: Dict) -> Dict:
        verdict = str(data.get("Verdict", "")).strip().lower()
        if verdict not in ALLOWED_VERDICTS:
            raise ValueError(f"Invalid Verdict (row {row.idx}): {data.get('Verdict')}")

        ie = data.get("Incorrect_entries")
        if not isinstance(ie, list):
            raise ValueError(f"Incorrect_entries must be list (row {row.idx}).")
        ie_norm: List[str] = []
        alias_map = {
            "reg": "regulation",
            "regulation": "regulation",
            "pathway regulation": "regulation",

            "effect": "effect",
            "baseline effect": "effect",
            "baseline_effect": "effect",
            "baseline-effect": "effect",
            "baselineeffect": "effect",
            "drug effect": "effect",
            "effect on drug": "effect",
            "effect on ribociclib response": "effect",
            "baseline effect on drug": "effect",
        }

        for x in ie:
            s = str(x).strip().lower()
            s = s.replace("_", " ").replace("-", " ")
            s = re.sub(r"\s+", " ", s).strip()
            s = alias_map.get(s, s)

            if s not in ALLOWED_INCORRECT:
                raise ValueError(f"Invalid Incorrect_entries item '{x}' (row {row.idx}). Allowed: {sorted(ALLOWED_INCORRECT)}")
            if s not in ie_norm:
                ie_norm.append(s)

        if verdict == "correct":
            ie_norm = []
        else:
            if len(ie_norm) == 0:
                raise ValueError(f"Verdict=corrected but Incorrect_entries empty (row {row.idx}).")

        reg = str(data.get("Regulation", "")).strip().lower()
        eff = str(data.get("Baseline effect", "")).strip().lower()
        if reg not in ALLOWED_REG:
            raise ValueError(f"Invalid Regulation '{data.get('Regulation')}' (row {row.idx}).")
        if eff not in ALLOWED_EFF:
            raise ValueError(f"Invalid Baseline effect '{data.get('Baseline effect')}' (row {row.idx}).")

        rat = data.get("Rationale", "")
        if not isinstance(rat, str) or not rat.strip():
            raise ValueError(f"Invalid Rationale (row {row.idx}).")

        cls = str(data.get("Pathway–drug relationship classification", "")).strip()
        if cls not in ALLOWED_CLASS:
            raise ValueError(f"Invalid classification '{cls}' (row {row.idx}).")

        cls_reason = data.get("Classification reasoning", "")
        if not isinstance(cls_reason, str) or not cls_reason.strip():
            raise ValueError(f"Invalid classification reasoning (row {row.idx}).")

        inc = str(data.get("Include decision", "")).strip().lower()
        if inc not in ALLOWED_INCLUDE:
            raise ValueError(f"Invalid Include decision '{data.get('Include decision')}' (row {row.idx}).")

        inc_reason = data.get("Inclusion reasoning", "")
        if not isinstance(inc_reason, str) or not inc_reason.strip():
            raise ValueError(f"Invalid Inclusion reasoning (row {row.idx}).")

        return {
            "Verdict": verdict,
            "Incorrect_entries": ie_norm,
            "Regulation": reg,
            "Baseline effect": eff,
            "Rationale": rat.strip(),
            "Pathway–drug relationship classification": cls,
            "Classification reasoning": cls_reason.strip(),
            "Include decision": inc,
            "Inclusion reasoning": inc_reason.strip(),
        }

    data = _call_llm(base_prompt)
    try:
        return _validate(data)
    except ValueError as e:
        if max_retries <= 0:
            raise

        fix_prompt = (
            "Your previous JSON violated constraints.\n"
            f"ERROR: {str(e)}\n\n"
            "Fix the JSON ONLY to be schema-compliant. Do NOT add keys.\n"
            "Important:\n"
            '- If Verdict="corrected" -> Incorrect_entries MUST be non-empty.\n'
            '- Include decision MUST be "include" or "exclude".\n'
            "Return corrected JSON only.\n\n"
            "Row:\n"
            f"{json.dumps(row.to_compact_dict(), ensure_ascii=False)}\n\n"
            "Your previous JSON:\n"
            f"{json.dumps(data, ensure_ascii=False)}"
        )
        data2 = _call_llm(fix_prompt)
        return _validate(data2)

# %%
# -------------------------
# Cache helpers
# -------------------------
def load_cache(cache_file: str) -> Dict[str, Dict]:
    if not cache_file:
        return {}
    p = Path(cache_file)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_cache(cache_file: str, cache: Dict[str, Dict]) -> None:
    if not cache_file:
        return
    p = Path(cache_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

# %%
# -------------------------
# Pipeline runner (final STRICT schema per row)
# -------------------------
def run_pipeline_cellbased(
    input_md: str,
    drug_name: str,
    model: str,
    temperature: float,
    out_json: str = "",
    cache_file: str = "",
    api_base: str = "",
    no_llm: bool = False,
) -> Dict[str, Dict]:
    md_path = Path(input_md)
    if not md_path.exists():
        raise FileNotFoundError(f"Input file not found: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    sources_map = parse_sources_map(md_text)
    rows = extract_pathway_evidence_table(md_text)

    api_base = api_base.strip() or None
    cache = load_cache(cache_file) if cache_file else {}

    result: Dict[str, Dict] = {}

    for i, r in enumerate(rows, start=1):
        ref_nums = parse_reference_numbers(r.key_refs_raw)
        resolved_urls = resolve_refs_to_urls(ref_nums, sources_map)

        if no_llm:
            row_out = {
                "Pathway ID/Name": r.pathway,
                "Verdict": "correct",
                "Incorrect_entries": [],
                "Regulation": "",
                "Baseline effect": "",
                "Rationale": r.rationale,
                "Pathway–drug relationship classification": "possibly related",
                "Classification reasoning": "NO_LLM mode: not validated.",
                "Include decision": "include",
                "Inclusion reasoning": "NO_LLM mode: inclusion not validated.",
                "References": resolved_urls,
            }
        else:
            cache_key = _sha1(json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "drug": drug_name,
                    "model": model,
                    "row": r.to_compact_dict(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ))

            llm_out = cache.get(cache_key)
            if llm_out is None:
                llm_out = llm_validate_normalize_and_include(
                    row=r,
                    drug_name=drug_name,
                    model=model,
                    temperature=temperature,
                    api_base=api_base,
                    max_retries=1,
                )
                if cache_file:
                    cache[cache_key] = llm_out
                    save_cache(cache_file, cache)

            row_out = {
                "Pathway ID/Name": r.pathway,
                "Verdict": llm_out["Verdict"],
                "Incorrect_entries": llm_out["Incorrect_entries"],
                "Regulation": llm_out["Regulation"],
                "Baseline effect": llm_out["Baseline effect"],
                "Rationale": llm_out["Rationale"],
                "Pathway–drug relationship classification": llm_out["Pathway–drug relationship classification"],
                "Classification reasoning": llm_out["Classification reasoning"],
                "Include decision": llm_out["Include decision"],
                "Inclusion reasoning": llm_out["Inclusion reasoning"],
                "References": resolved_urls,  # URLs resolved ONLY from Sources section
            }

        result[f"Row{i}"] = row_out

    if out_json:
        outp = Path(out_json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result

# %%
# -------------------------
# RUN (execute this cell)
# -------------------------
load_dotenv_if_exists()
if not NO_LLM and not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY is not set. Set it or set NO_LLM=True.")

result = run_pipeline_cellbased(
    input_md=INPUT_MD,
    drug_name=DRUG_NAME,
    model=MODEL,
    temperature=TEMPERATURE,
    out_json=OUT_JSON,
    cache_file=CACHE_PATH,
    api_base=API_BASE,
    no_llm=NO_LLM,
)

result

# %%
# %%
# -------------------------
# Convert result JSON -> DataFrame + save CSV
# -------------------------
import pandas as pd

def result_to_dataframe(result: Dict[str, Dict]) -> pd.DataFrame:
    """
    Flattens the per-row JSON into a table.
    Lists (Incorrect_entries, References) are joined with '; '.
    """
    rows = []
    for row_key, row_obj in result.items():
        flat = {"Row": row_key}
        for k, v in row_obj.items():
            if isinstance(v, list):
                flat[k] = "; ".join(str(x) for x in v)
            else:
                flat[k] = v
        rows.append(flat)

    df = pd.DataFrame(rows)

    # Keep a stable column order if present
    preferred = [
        "Row",
        "Pathway ID/Name",
        "Verdict",
        "Include decision",
        "Incorrect_entries",
        "Regulation",
        "Baseline effect",
        "Pathway–drug relationship classification",
        "Classification reasoning",
        "Inclusion reasoning",
        "Rationale",
        "References",
    ]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    return df[cols]

df_result = result_to_dataframe(result)
df_result

# Save CSV
OUT_CSV = "ribociclib_step2_trial2_out.csv"
df_result.to_csv(OUT_CSV, index=False, encoding="utf-8")
OUT_CSV
# %%
