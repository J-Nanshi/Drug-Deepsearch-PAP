"""Step 2 pathway extraction and fact-check pipeline.

Overview:
This script batch-processes Step-1 drug markdown reports and generates one Step-2 JSON
file per drug. It is designed to work on section-based report structure and focuses on
the pathway evidence table in section 9.

Inputs:
- CLI arguments:
  - `-i/--input-dir`: Directory containing Step-1 `.md` drug files.
  - `-o/--output-dir`: Directory where `<drug>.json` files are written.
  - `--cancer`: Cancer context injected into LLM prompts.
  - LLM/runtime flags: `--model`, `--temperature`, `--api-base`, `--no-llm`,
    `--sleep-seconds`, `--max-retries`.
  - Cache/status flags: `--cache-dir`, `--cache-file`, `--status-file`.
- Environment variable:
  - `OPENAI_API_KEY` is required unless `--no-llm` is enabled.
- Per-file input:
  - Drug name is derived from markdown filename stem.
  - Section expected: `## 9. Pathway Evidence Table`, with an immediate in-section
    `### Sources` block.

Extraction Logic:
1. Locate section `## 9. Pathway Evidence Table`.
2. Extract markdown table headers and rows from that section.
3. Resolve key columns by keyword matching:
   pathway, regulation, effect, rationale, references, optional row index.
4. Convert each row into a `TableRow` object with cleaned markdown text.

Normalization Logic:
- Reference cell normalization:
  - Handles non-uniform citation formats such as:
    `[1], [2]`, `[2][3][7]`, `[1] [3] [4]`, `[6]-[8]`, `[12–14]`, and mixed forms.
  - Unicode dashes are normalized to ASCII `-`.
  - Ranges are expanded to individual ids.
  - Duplicate ids are removed while preserving order.
- URL normalization:
  - URLs extracted from sources are canonicalized for dedupe stability
    (trim punctuation/trailing slash).

Mapping Logic:
1. Parse citation-to-URL mapping only from the section-9 `### Sources` block.
2. For each row, map normalized citation ids to source URLs.
3. Keep unique URLs in first-seen order.
4. Track citation ids that are not found in sources and log warnings, but continue.

Validation Logic (LLM mode):
- Per row, call LLM to:
  - fact-check regulation/effect/rationale,
  - normalize labels (regulation, baseline effect),
  - classify pathway-drug relationship,
  - decide include/exclude with reasoning.
- Enforce strict schema validation on returned JSON.
- Retry once with a constrained fix prompt if schema validation fails.
- Cache successful row-level LLM outputs using SHA1 key:
  `prompt_version + drug + model + row payload`.

Batch Control and Status:
- Successful drug runs are recorded in status cache and skipped on rerun.
- Failed drugs are not marked successful and are retried in subsequent runs.
- Continue-on-error behavior is used across files with end-of-run summary.

Outputs:
- Per-drug JSON file: `<output-dir>/<drug>.json`.
- Optional cache artifacts:
  - LLM row cache JSON (`--cache-file` under `--cache-dir` by default).
  - Successful-drug status JSON (`--status-file` under `--cache-dir` by default).
- Console logs:
  - section/source detection line numbers,
  - row-level citation normalization and mapping counts,
  - LLM/cache status,
  - missing citation warnings,
  - batch summary (success/skip/failure counts).
"""

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROMPT_VERSION = "v3_include_decision"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_CACHE_DIR = ".cache/step2"
DEFAULT_CACHE_FILE = "llm_cache.json"
DEFAULT_STATUS_FILE = "drug_status.json"


@dataclass
class TableRow:
    idx: int
    pathway: str
    regulation: str
    effect: str
    rationale: str
    key_refs_raw: str

    def to_compact_dict(self) -> Dict[str, str]:
        """Return normalized row payload used in prompts and cache-key generation."""
        return {
            "Pathway ID/Name": self.pathway,
            "Regulation (raw)": self.regulation,
            "Effect (raw)": self.effect,
            "Rationale (raw)": self.rationale,
            "Key references (raw)": self.key_refs_raw,
        }


DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2212"
DASH_RE = re.compile(f"[{DASH_CHARS}]")
URL_RE = re.compile(r"(https?://[^\s)>\]]+)", re.IGNORECASE)

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


# -------------------------
# Minimal .env loader (no dependency)
# -------------------------
def _load_dotenv_locations() -> List[Path]:
    """Return candidate .env locations checked by the loader."""
    paths = [Path(".").resolve() / ".env"]
    try:
        paths.append(Path(__file__).resolve().parent / ".env")
    except Exception:
        pass
    return paths


def load_dotenv_if_exists() -> None:
    """Load environment variables from first existing local .env file."""
    def _parse_env_file(p: Path) -> Dict[str, str]:
        """Parse simple KEY=VALUE lines from an env file into a dictionary."""
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


# -------------------------
# Markdown parsing + reference helpers
# -------------------------
def _normalize_dashes(s: str) -> str:
    """Normalize unicode dash variants to ASCII hyphen for parsing stability."""
    return DASH_RE.sub("-", s or "")


def _strip_md(s: str) -> str:
    """Remove simple markdown formatting and normalize whitespace."""
    s = (s or "").strip()
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonicalize_url(url: str) -> str:
    """Normalize URL strings for consistent deduplication."""
    u = (url or "").strip().rstrip(".,;")
    if u.endswith("/"):
        u = u.rstrip("/")
    return u


def parse_reference_numbers(ref_cell: str) -> List[int]:
    """Parse and normalize citation ids/ranges from a references cell."""
    if not ref_cell:
        return []

    s = _normalize_dashes(ref_cell)
    nums: List[int] = []

    pattern = re.compile(r"\[(\d+)\]\s*-\s*\[(\d+)\]|\[(\d+\s*-\s*\d+)\]|\[(\d+)\]")
    for m in pattern.finditer(s):
        if m.group(1) and m.group(2):
            start, end = int(m.group(1)), int(m.group(2))
            if start <= end:
                nums.extend(range(start, end + 1))
            else:
                nums.extend([start, end])
            continue

        if m.group(3):
            a, b = re.split(r"\s*-\s*", m.group(3))
            if a.isdigit() and b.isdigit():
                start, end = int(a), int(b)
                if start <= end:
                    nums.extend(range(start, end + 1))
                else:
                    nums.extend([start, end])
            continue

        if m.group(4):
            nums.append(int(m.group(4)))

    if not nums:
        for part in re.split(r"[,\s;]+", s):
            token = re.sub(r"[^\d\-]", "", part)
            if not token:
                continue
            if "-" in token:
                a, b = token.split("-", 1)
                if a.isdigit() and b.isdigit():
                    start, end = int(a), int(b)
                    if start <= end:
                        nums.extend(range(start, end + 1))
                    else:
                        nums.extend([start, end])
            elif token.isdigit():
                nums.append(int(token))

    seen = set()
    out: List[int] = []
    for n in nums:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


def parse_sources_map_from_section(sources_text: str) -> Dict[int, List[str]]:
    """Build citation-number to URL map from section-9 Sources text."""
    sources: Dict[int, List[str]] = {}
    for line in sources_text.splitlines():
        line = line.strip()
        m = re.match(r"^\[(\d+)\]\s+(.*)$", line)
        if not m:
            continue
        n = int(m.group(1))
        rest = m.group(2)

        urls = URL_RE.findall(rest) or []
        urls = [_canonicalize_url(u) for u in urls]

        uniq: List[str] = []
        seen = set()
        for u in urls:
            if u not in seen:
                uniq.append(u)
                seen.add(u)
        if uniq:
            sources[n] = uniq
    return sources


def _find_section_block(md_text: str, heading_pattern: re.Pattern) -> Tuple[str, int, int, int]:
    """Find a markdown heading block and return text plus position metadata."""
    m = heading_pattern.search(md_text)
    if not m:
        return ("", -1, -1, -1)

    level = len(m.group("hashes"))
    start = m.end()
    start_line = md_text.count("\n", 0, m.start()) + 1
    next_pat = re.compile(rf"^#{{1,{level}}}\s+\S.*$", re.MULTILINE)
    m2 = next_pat.search(md_text, pos=start)
    end = m2.start() if m2 else len(md_text)
    return (md_text[start:end].strip("\n"), start_line, m.start(), end)


def extract_immediate_sources_block_from_section9(section_text: str, section_heading_line: int) -> Tuple[str, int]:
    """Extract the immediate ### Sources block from section 9 text."""
    src_heading = re.compile(r"^###\s*Sources\s*$", re.IGNORECASE | re.MULTILINE)
    m = src_heading.search(section_text)
    if not m:
        raise ValueError('Could not locate "### Sources" within section "## 9. Pathway Evidence Table".')

    before = section_text[:m.start()]
    earlier_heading = re.search(r"^#{1,6}\s+\S.*$", before, re.MULTILINE)
    if earlier_heading:
        found = earlier_heading.group(0).strip()
        raise ValueError(f'Expected immediate "### Sources" context in section 9, found heading "{found}" before it.')

    sources_start_line = section_heading_line + before.count("\n") + 1
    sources_text = section_text[m.end():].strip("\n")
    return (sources_text, sources_start_line)


def extract_markdown_table_from_section(section_text: str) -> Tuple[List[str], List[List[str]]]:
    """Extract the first markdown table from a section body."""
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
    """Find first table-header index matching any provided keyword."""
    hnorm = [h.lower() for h in headers]
    for k in keywords:
        k = k.lower()
        for i, h in enumerate(hnorm):
            if k in h:
                return i
    return None


def extract_pathway_evidence_table(md_text: str) -> Tuple[List[TableRow], int, int, int, bool, str]:
    """Parse section-9 pathway table into typed rows and metadata."""
    heading_pattern = re.compile(
        r"^(?P<hashes>#+)\s*9\.\s*Pathway Evidence Table\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    section, start_line, _section_start_pos, section_end_pos = _find_section_block(md_text, heading_pattern)
    if not section:
        raise ValueError('Could not locate section heading "## 9. Pathway Evidence Table".')

    headers, rows = extract_markdown_table_from_section(section)
    if not headers or not rows:
        raise ValueError('Could not locate markdown table under section "9. Pathway Evidence Table".')

    col_path = _pick_column(headers, ["pathway"])
    col_reg = _pick_column(headers, ["regulation"])
    col_eff = _pick_column(headers, ["effect"])
    col_rat = _pick_column(headers, ["rationale", "biological rationale"])
    col_ref = _pick_column(headers, ["key ref", "reference", "ref"])
    col_idx = _pick_column(headers, ["#", "no."])

    if col_path is None or col_reg is None or col_eff is None or col_rat is None:
        raise ValueError(f"Missing expected columns in pathway table. Headers found: {headers}")

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
    return (out, start_line, _section_start_pos, section_end_pos, col_ref is not None, section)


def resolve_refs_to_urls(ref_nums: List[int], sources_map: Dict[int, List[str]]) -> Tuple[List[str], List[int]]:
    """Resolve citation ids to unique URLs and return unresolved ids."""
    out: List[str] = []
    seen = set()
    missing: List[int] = []
    for n in ref_nums:
        urls = sources_map.get(n, [])
        if not urls:
            missing.append(n)
            continue
        for u in urls:
            key = _canonicalize_url(u)
            if key and key not in seen:
                out.append(u)
                seen.add(key)
    return (out, missing)


def _sha1(s: str) -> str:
    """Compute SHA1 hash used for deterministic cache keys."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# -------------------------
# LLM
# -------------------------
def llm_validate_normalize_and_include(
    *,
    row: TableRow,
    drug_name: str,
    cancer_name: str,
    model: str,
    temperature: float,
    api_base: Optional[str],
    max_retries: int,
    request_sleep_seconds: float,
) -> Dict:
    """Call LLM for row validation/normalization and enforce schema."""
    from openai import OpenAI

    client = OpenAI(base_url=api_base) if api_base else OpenAI()

    system = (
        "SYSTEM ROLE:\n"
        "You are an expert in molecular biology, cancer pharmacology, and clinical translational oncology. "
        "You are strictly evidence-driven and baseline-aware.\n\n"
        "STRICT ASSUMPTIONS:\n"
        f"- Disease context: Human {cancer_name} ONLY\n"
        f"- Drug context: {drug_name}\n"
        "- Temporal context: BASELINE pathway biology (pre-treatment)\n"
        "- Evidence hierarchy: clinical > translational > mechanistic > inferential\n\n"
        "NON-NEGOTIABLE RULES:\n"
        f"- Baseline means BEFORE {drug_name} is administered.\n"
        "- Do NOT treat post-treatment/adaptive changes as baseline unless explicitly stated.\n"
        "- Normalize to SINGLE-WORD values:\n"
        "  Regulation: upregulation|downregulation\n"
        "  Baseline effect: sensitive|resistant\n"
        "- Verdict:\n"
        "  correct: baseline-accurate and consistent\n"
        "  corrected: regulation and/or effect needed correction\n"
        "- If Verdict=corrected -> Incorrect_entries MUST be non-empty.\n"
        "- Decide whether the pathway row should be INCLUDED in final output:\n"
        f"  include: relevant/needed for baseline {drug_name} response in {cancer_name}\n"
        "  exclude: not needed, not relevant, speculative, or post-treatment framed\n"
        "- Output JSON only.\n"
    )

    allowed_class_list = sorted(ALLOWED_CLASS)
    allowed_inc_list = sorted(ALLOWED_INCLUDE)

    base_prompt = (
        f"Given one extracted row, do ALL of the following under baseline {cancer_name} + {drug_name}:\n"
        "1) Fact-check regulation/effect/rationale.\n"
        "2) Assign Verdict (correct|corrected).\n"
        "3) If corrected, set Incorrect_entries to list which were wrong: regulation/effect.\n"
        "4) Normalize Regulation and Baseline effect to allowed single-word values.\n"
        "5) Classify pathway-drug relationship using ONE allowed category.\n"
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
        '  "Pathway-drug relationship classification": "<one allowed category>",\n'
        '  "Classification reasoning": "<1-2 sentences>",\n'
        '  "Include decision": "include" | "exclude",\n'
        '  "Inclusion reasoning": "<1-2 sentences>"\n'
        "}\n\n"
        "Row:\n"
        f"{json.dumps(row.to_compact_dict(), ensure_ascii=False)}"
    )

    def _call_llm(user_prompt: str) -> Dict:
        """Send one request to the chat model and parse strict JSON response."""
        if request_sleep_seconds > 0:
            time.sleep(request_sleep_seconds)
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
        """Validate and normalize LLM JSON against the allowed Step-2 schema."""
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
            "effect on response": "effect",
            f"effect on {drug_name.lower()} response": "effect",
            "baseline effect on drug": "effect",
        }

        for x in ie:
            s = str(x).strip().lower()
            s = s.replace("_", " ").replace("-", " ")
            s = re.sub(r"\s+", " ", s).strip()
            s = alias_map.get(s, s)

            if s not in ALLOWED_INCORRECT:
                raise ValueError(
                    f"Invalid Incorrect_entries item '{x}' (row {row.idx}). Allowed: {sorted(ALLOWED_INCORRECT)}"
                )
            if s not in ie_norm:
                ie_norm.append(s)

        if verdict == "correct":
            ie_norm = []
        elif not ie_norm:
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

        cls = str(data.get("Pathway-drug relationship classification", "")).strip()
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
            "Pathway-drug relationship classification": cls,
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


# -------------------------
# Cache helpers
# -------------------------
def load_cache(cache_file: str) -> Dict[str, Dict]:
    """Load LLM row cache JSON from disk, returning empty cache on failure."""
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
    """Persist LLM row cache JSON to disk."""
    if not cache_file:
        return
    p = Path(cache_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def load_status(status_file: str) -> Dict[str, Dict]:
    """Load successful-drug status cache used for skip behavior."""
    p = Path(status_file)
    if not p.exists():
        return {"successful_drugs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"successful_drugs": {}}
        successful = data.get("successful_drugs")
        if not isinstance(successful, dict):
            return {"successful_drugs": {}}
        return {"successful_drugs": successful}
    except Exception:
        return {"successful_drugs": {}}


def save_status(status_file: str, status: Dict[str, Dict]) -> None:
    """Persist successful-drug status cache to disk."""
    p = Path(status_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline_for_md(
    *,
    md_path: Path,
    drug_name: str,
    cancer_name: str,
    model: str,
    temperature: float,
    api_base: Optional[str],
    no_llm: bool,
    cache: Dict[str, Dict],
    cache_file: str,
    max_retries: int,
    request_sleep_seconds: float,
) -> Dict[str, Dict]:
    """Process one markdown file into Step-2 row JSON output."""
    if not md_path.exists():
        raise FileNotFoundError(f"Input file not found: {md_path}")

    md_text = md_path.read_text(encoding="utf-8")
    print(f"{drug_name}: processing for extraction, factcheck and validation")
    print("  extracting ## 9. Pathway Evidence Table")
    rows, section_line, _section_start_pos, _section_end_pos, has_ref_col, section_text = extract_pathway_evidence_table(md_text)
    print(f"    found at line no. {section_line}")
    print(f"  references column detected: {'yes' if has_ref_col else 'no'}")
    print('  extracting immediate "### Sources" section within section 9')
    sources_text, sources_line = extract_immediate_sources_block_from_section9(section_text, section_line)
    print(f"    found at line no. {sources_line}")
    sources_map = parse_sources_map_from_section(sources_text)
    print(f"    source entries parsed: {len(sources_map)}")
    if no_llm:
        print("  NO_LLM mode enabled; extraction and reference resolution only")
    else:
        print("  Pathway rows being processed through LLM")

    result: Dict[str, Dict] = {}

    for i, r in enumerate(rows, start=1):
        ref_nums = parse_reference_numbers(r.key_refs_raw)
        print(f"    Row{i} citations normalized: {ref_nums}")
        resolved_urls, missing_refs = resolve_refs_to_urls(ref_nums, sources_map)
        print(f"    Row{i} mapped reference links: {len(resolved_urls)}")
        if missing_refs:
            print(f"    WARNING {drug_name} Row{i}: missing citation ids in ### Sources: {missing_refs}")

        if no_llm:
            row_out = {
                "Pathway ID/Name": r.pathway,
                "Verdict": "correct",
                "Incorrect_entries": [],
                "Regulation": "",
                "Baseline effect": "",
                "Rationale": r.rationale,
                "Pathway-drug relationship classification": "possibly related",
                "Classification reasoning": "NO_LLM mode: not validated.",
                "Include decision": "include",
                "Inclusion reasoning": "NO_LLM mode: inclusion not validated.",
                "References": resolved_urls,
            }
        else:
            cache_key = _sha1(
                json.dumps(
                    {
                        "prompt_version": PROMPT_VERSION,
                        "drug": drug_name,
                        "model": model,
                        "row": r.to_compact_dict(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

            llm_out = cache.get(cache_key)
            if llm_out is None:
                llm_out = llm_validate_normalize_and_include(
                    row=r,
                    drug_name=drug_name,
                    cancer_name=cancer_name,
                    model=model,
                    temperature=temperature,
                    api_base=api_base,
                    max_retries=max_retries,
                    request_sleep_seconds=request_sleep_seconds,
                )
                if cache_file:
                    cache[cache_key] = llm_out
                    save_cache(cache_file, cache)
                print(f"    Row{i} processed")
            else:
                print(f"    Row{i} retrieved from cache")

            row_out = {
                "Pathway ID/Name": r.pathway,
                "Verdict": llm_out["Verdict"],
                "Incorrect_entries": llm_out["Incorrect_entries"],
                "Regulation": llm_out["Regulation"],
                "Baseline effect": llm_out["Baseline effect"],
                "Rationale": llm_out["Rationale"],
                "Pathway-drug relationship classification": llm_out["Pathway-drug relationship classification"],
                "Classification reasoning": llm_out["Classification reasoning"],
                "Include decision": llm_out["Include decision"],
                "Inclusion reasoning": llm_out["Inclusion reasoning"],
                "References": resolved_urls,
            }

        result[f"Row{i}"] = row_out

    print("  Pathways being fact checked and validated")
    print(f"  Completed for {drug_name}")

    return result


def derive_drug_name_from_file(md_path: Path) -> str:
    """Derive drug identifier from markdown filename stem."""
    return md_path.stem


def run_batch(args: argparse.Namespace) -> Dict[str, object]:
    """Run batch processing across input markdown files and return summary."""
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No .md files found in input directory: {input_dir}")

    api_base = args.api_base.strip() or None

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_file = args.cache_file
    cache_path = Path(cache_file) if cache_file else None
    if cache_path is not None and not cache_path.is_absolute():
        cache_path = cache_dir / cache_path

    status_file = args.status_file
    status_path = Path(status_file) if status_file else (cache_dir / DEFAULT_STATUS_FILE)
    if not status_path.is_absolute():
        status_path = cache_dir / status_path

    cache = load_cache(str(cache_path)) if cache_path else {}
    status = load_status(str(status_path))
    successful_drugs: Dict[str, Dict] = status["successful_drugs"]

    failures: List[Dict[str, str]] = []
    success_count = 0
    skipped_count = 0

    for md_file in md_files:
        drug_name = derive_drug_name_from_file(md_file)
        out_json_path = output_dir / f"{drug_name}.json"

        if drug_name in successful_drugs and out_json_path.exists():
            skipped_count += 1
            print(f"SKIP: {drug_name} already completed in status cache")
            continue

        try:
            result = run_pipeline_for_md(
                md_path=md_file,
                drug_name=drug_name,
                cancer_name=args.cancer,
                model=args.model,
                temperature=args.temperature,
                api_base=api_base,
                no_llm=args.no_llm,
                cache=cache,
                cache_file=str(cache_path) if cache_path else "",
                max_retries=args.max_retries,
                request_sleep_seconds=args.sleep_seconds,
            )
            out_json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            success_count += 1
            successful_drugs[drug_name] = {
                "md_file": str(md_file),
                "output_json": str(out_json_path),
                "row_count": len(result),
                "completed_unix": int(time.time()),
            }
            save_status(str(status_path), status)
            print(f"OK: {md_file.name} -> {out_json_path.name} ({len(result)} rows)")
        except Exception as e:
            failures.append({"file": str(md_file), "error": f"{type(e).__name__}: {e}"})
            print(f"FAILED: {md_file.name} -> {type(e).__name__}: {e}")

    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "total_files": len(md_files),
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failure_count": len(failures),
        "failures": failures,
        "cache_file": str(cache_path) if cache_path else "",
        "status_file": str(status_path),
    }

    print("\nBatch summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for Step 2 execution."""
    parser = argparse.ArgumentParser(
        description="Batch pathway fact-check extraction over drug markdown files."
    )
    parser.add_argument("-i", "--input-dir", required=True, help="Input directory containing drug .md files")
    parser.add_argument("-o", "--output-dir", required=True, help="Output directory for per-drug JSON files")
    parser.add_argument("--cancer", required=True, help="Cancer name used in LLM prompt context")

    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"LLM model (default: {DEFAULT_MODEL})")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help="LLM temperature")
    parser.add_argument("--api-base", default="", help="Optional OpenAI-compatible API base URL")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM validation and output extracted rows only")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="Directory to store cache/status files")
    parser.add_argument(
        "--cache-file",
        default=DEFAULT_CACHE_FILE,
        help="Shared LLM cache filename or absolute path (empty to disable)",
    )
    parser.add_argument(
        "--status-file",
        default=DEFAULT_STATUS_FILE,
        help="Successful-drug status filename or absolute path",
    )
    parser.add_argument("--max-retries", type=int, default=1, help="LLM schema-fix retries")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Sleep before each LLM request. Set 0 to disable.",
    )

    return parser.parse_args()


def main() -> None:
    """Program entrypoint for environment setup and batch execution."""
    load_dotenv_if_exists()
    args = parse_args()

    if not args.no_llm and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set. Set it or use --no-llm.")

    summary = run_batch(args)
    if summary["failure_count"] > 0:
        raise RuntimeError(
            f"Batch completed with failures: {summary['failure_count']} of {summary['total_files']} files failed."
        )


if __name__ == "__main__":
    main()
