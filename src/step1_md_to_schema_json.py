"""
Deterministic markdown -> harmonized JSON conversion for Step 1 deepsearch outputs.

The schema shape is aligned to prompt3a requirements but uses canonical snake_case keys.
No inference or hallucination is performed; missing fields are emitted as null/[]/{}
depending on expected type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
SECTION_RE = re.compile(r"^##\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
CITATION_RANGE_RE = re.compile(r"\[(\d+)\]\s*[-–—]\s*\[(\d+)\]|\[(\d+\s*[-–—]\s*\d+)\]|\[(\d+)\]")
GENE_RE = re.compile(r"\b[A-Z0-9]{2,12}\b")


# Variants observed in prompt3a doc and downstream docs.
FIELD_ALIASES: Dict[str, str] = {
    "drug name": "drug_name",
    "drug_name": "drug_name",
    "cancer indication": "cancer_indication",
    "cancer_indication": "cancer_indication",
    "drug category": "drug_category",
    "drug_category": "drug_category",
    "drug class": "drug_class",
    "drug_class": "drug_class",
    "moa": "moa",
    "chembl id": "chembl_id",
    "chembl_id": "chembl_id",
    "drugbank id": "drugbank_id",
    "drugbank_id": "drugbank_id",
    "synonyms": "synonyms",
    "primary targets": "primary_targets",
    "primary_targets": "primary_targets",
    "pathway sets": "pathway_sets",
    "pathway_sets": "pathway_sets",
    "pathway_sets_annotations": "pathway_sets_annotations",
    "sensitivity_genes_up": "sensitivity_genes_up",
    "sensitivity_genes_up_annotations": "sensitivity_genes_up_annotations",
    "sensitivity_genes_down": "sensitivity_genes_down",
    "sensitivity_genes_down_annotations": "sensitivity_genes_down_annotations",
    "resistance_genes_up": "resistance_genes_up",
    "resistance_genes_up_annotations": "resistance_genes_up_annotations",
    "resistance_genes_down": "resistance_genes_down",
    "resistance_genes_down_annotations": "resistance_genes_down_annotations",
    "kg_gene_relationships": "kg_gene_relationships",
    "kg relationships": "kg_gene_relationships",
    "contraindications": "contraindications",
    "citations": "citations",
    "notes": "notes",
}


CANONICAL_KEYS = [
    "drug_name",
    "cancer_indication",
    "drug_category",
    "drug_class",
    "moa",
    "chembl_id",
    "drugbank_id",
    "synonyms",
    "primary_targets",
    "pathway_sets",
    "pathway_sets_annotations",
    "sensitivity_genes_up",
    "sensitivity_genes_down",
    "resistance_genes_up",
    "resistance_genes_down",
    "sensitivity_genes_up_annotations",
    "sensitivity_genes_down_annotations",
    "resistance_genes_up_annotations",
    "resistance_genes_down_annotations",
    "kg_gene_relationships",
    "contraindications",
    "citations",
    "notes",
]


def _normalize_field_name(name: str) -> str:
    key = re.sub(r"[\s\"'`]+", " ", (name or "").strip().lower())
    key = key.replace("-", "_")
    key = re.sub(r"\s+", " ", key).strip()
    return FIELD_ALIASES.get(key, key.replace(" ", "_"))


def _strip_md(text: str) -> str:
    value = text or ""
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _empty_schema() -> Dict[str, Any]:
    return {
        "drug_name": None,
        "cancer_indication": None,
        "drug_category": None,
        "drug_class": None,
        "moa": None,
        "chembl_id": None,
        "drugbank_id": None,
        "synonyms": [],
        "primary_targets": [],
        "pathway_sets": [],
        "pathway_sets_annotations": {},
        "sensitivity_genes_up": [],
        "sensitivity_genes_down": [],
        "resistance_genes_up": [],
        "resistance_genes_down": [],
        "sensitivity_genes_up_annotations": [],
        "sensitivity_genes_down_annotations": [],
        "resistance_genes_up_annotations": [],
        "resistance_genes_down_annotations": [],
        "kg_gene_relationships": [],
        "contraindications": [],
        "citations": [],
        "notes": "",
    }


def _extract_sections(md_text: str) -> Dict[int, str]:
    sections: Dict[int, str] = {}
    matches = list(SECTION_RE.finditer(md_text))
    for idx, match in enumerate(matches):
        sec_no = int(match.group(1))
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md_text)
        content = md_text[start:end].strip()
        if sec_no not in sections:
            sections[sec_no] = content
        else:
            # Keep all explicit content deterministically if section repeated.
            sections[sec_no] = (sections[sec_no] + "\n\n" + content).strip()
    return sections


def _extract_table_lines(text: str) -> List[str]:
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            lines.append(line.rstrip())
    return lines


def _parse_markdown_table(text: str) -> Tuple[List[str], List[List[str]]]:
    lines = _extract_table_lines(text)
    if len(lines) < 2:
        return [], []

    header_idx = -1
    for i in range(len(lines) - 1):
        if re.match(r"^\s*\|?[\s:-]+\|", lines[i + 1].strip()):
            header_idx = i
            break
    if header_idx < 0:
        return [], []

    headers = [h.strip() for h in lines[header_idx].strip().strip("|").split("|")]
    rows: List[List[str]] = []
    for line in lines[header_idx + 2 :]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= len(headers):
            rows.append(cells[: len(headers)])
    return headers, rows


def _extract_reference_entries(reference_block: str) -> Dict[int, str]:
    entries: Dict[int, str] = {}
    starts = list(re.finditer(r"(?m)^\[(\d+)\]\s+", reference_block))
    for idx, match in enumerate(starts):
        ref_id = int(match.group(1))
        start = match.start()
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(reference_block)
        raw = reference_block[start:end].strip()
        entries[ref_id] = raw
    return entries


def _parse_citation_ids(text: str) -> List[int]:
    citation_ids: List[int] = []
    for m in CITATION_RANGE_RE.finditer(text):
        if m.group(1) and m.group(2):
            a, b = int(m.group(1)), int(m.group(2))
            lo, hi = (a, b) if a <= b else (b, a)
            citation_ids.extend(range(lo, hi + 1))
            continue
        if m.group(3):
            parts = re.split(r"\s*[-–—]\s*", m.group(3))
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                a, b = int(parts[0]), int(parts[1])
                lo, hi = (a, b) if a <= b else (b, a)
                citation_ids.extend(range(lo, hi + 1))
            continue
        if m.group(4):
            citation_ids.append(int(m.group(4)))
    out: List[int] = []
    seen: Set[int] = set()
    for n in citation_ids:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _extract_id(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).upper()


def _extract_drug_name(section1: str, fallback_name: Optional[str]) -> Optional[str]:
    if fallback_name:
        return fallback_name
    m = re.match(r"\s*([A-Z][a-zA-Z0-9_-]+)\s*\(", section1)
    if m:
        return m.group(1)
    m2 = re.match(r"\s*([A-Z][a-zA-Z0-9_-]+)\s+is\b", section1)
    return m2.group(1) if m2 else None


def _extract_synonyms(section2: str, drug_name: Optional[str]) -> List[str]:
    synonyms: List[str] = []

    m = re.search(r"\(([^)]+)\)", section2)
    if m:
        candidates = [c.strip() for c in re.split(r"[;,/]", m.group(1))]
        for c in candidates:
            if c and c not in synonyms:
                synonyms.append(c)

    for line in section2.splitlines():
        if ":" not in line:
            continue
        _, rhs = line.split(":", 1)
        rhs = _strip_md(rhs)
        for item in re.split(r"[;,/]", rhs):
            name = item.strip()
            if not name:
                continue
            if drug_name and name.lower() == drug_name.lower():
                continue
            if len(name) > 80:
                continue
            if name not in synonyms:
                synonyms.append(name)
    return synonyms


def _extract_primary_targets(section4: str) -> List[str]:
    headers, rows = _parse_markdown_table(section4)
    if not headers or not rows:
        return []

    gene_idx = None
    target_idx = None
    for i, h in enumerate(headers):
        key = h.lower()
        if "gene" in key and gene_idx is None:
            gene_idx = i
        if "target" in key or "protein" in key:
            target_idx = i if target_idx is None else target_idx

    genes: List[str] = []
    if gene_idx is not None:
        for row in rows:
            if gene_idx < len(row):
                for g in GENE_RE.findall(row[gene_idx]):
                    if g not in genes:
                        genes.append(g)
    elif target_idx is not None:
        for row in rows:
            if target_idx < len(row):
                for g in GENE_RE.findall(row[target_idx]):
                    if g not in genes:
                        genes.append(g)
    return genes


def _extract_contraindications(section7: str) -> List[str]:
    items: List[str] = []
    for line in section7.splitlines():
        clean = _strip_md(line)
        if clean.startswith("-"):
            clean = clean.lstrip("-").strip()
        if not clean:
            continue
        lower = clean.lower()
        if any(k in lower for k in ["contraindication", "avoid", "warning", "pregnan", "hepatic", "renal", "cyp"]):
            if clean not in items:
                items.append(clean)
    return items


def _section_slug(section_no: int) -> str:
    mapping = {
        1: "drug_summary",
        2: "identifiers_synonyms",
        3: "mechanism_of_action",
        4: "primary_targets",
        5: "pathways_overview",
        6: "subtype_evidence",
        7: "contraindications_and_safety",
        8: "clinical_trials",
        9: "pathway_evidence_table",
        10: "references",
    }
    return mapping.get(section_no, f"section_{section_no}")


def _extract_pathway_section_data(section9: str) -> Tuple[List[str], Dict[str, Dict[str, str]], List[int]]:
    headers, rows = _parse_markdown_table(section9)
    if not headers:
        return [], {}, []

    id_idx = None
    rationale_idx = None
    refs_idx = None
    for i, h in enumerate(headers):
        norm = h.lower()
        if ("pathway" in norm or "name" in norm) and id_idx is None:
            id_idx = i
        if "rationale" in norm and rationale_idx is None:
            rationale_idx = i
        if ("reference" in norm or "citation" in norm) and refs_idx is None:
            refs_idx = i

    pathways: List[str] = []
    annotations: Dict[str, Dict[str, str]] = {}
    cited: List[int] = []

    if id_idx is None:
        return [], {}, []

    for row in rows:
        pathway = _strip_md(row[id_idx]) if id_idx < len(row) else ""
        if not pathway:
            continue
        if pathway not in pathways:
            pathways.append(pathway)
        interpretation = ""
        if rationale_idx is not None and rationale_idx < len(row):
            interpretation = _strip_md(row[rationale_idx])
        annotations[pathway] = {
            "description": "Pathway listed in deepsearch pathway evidence table.",
            "interpretation": interpretation if interpretation else "No explicit interpretation provided.",
        }
        if refs_idx is not None and refs_idx < len(row):
            cited.extend(_parse_citation_ids(row[refs_idx]))

    return pathways, annotations, cited


def harmonize_output_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize keys to canonical snake_case names and drop unknown keys."""
    normalized: Dict[str, Any] = _empty_schema()
    for key, value in payload.items():
        canonical = _normalize_field_name(key)
        if canonical in normalized:
            normalized[canonical] = value
    return normalized


def convert_markdown_to_schema_json(
    markdown_text: str,
    *,
    drug_name: Optional[str] = None,
    cancer_name: Optional[str] = None,
) -> Dict[str, Any]:
    sections = _extract_sections(markdown_text)
    out = _empty_schema()

    section1 = sections.get(1, "")
    section2 = sections.get(2, "")
    section3 = sections.get(3, "")
    section4 = sections.get(4, "")
    section7 = sections.get(7, "")
    section9 = sections.get(9, "")

    out["drug_name"] = _extract_drug_name(section1, drug_name)
    out["cancer_indication"] = f"{cancer_name} cancer" if cancer_name else None
    out["moa"] = _strip_md(section3) if section3 else None
    out["chembl_id"] = _extract_id(markdown_text, r"\b(CHEMBL\d+)\b")
    out["drugbank_id"] = _extract_id(markdown_text, r"\b(DB\d{4,6})\b")
    out["synonyms"] = _extract_synonyms(section2, out["drug_name"])
    out["primary_targets"] = _extract_primary_targets(section4)
    out["contraindications"] = _extract_contraindications(section7)
    out["drug_category"] = None
    out["drug_class"] = None

    pathway_sets, pathway_annotations, section9_citations = _extract_pathway_section_data(section9)
    out["pathway_sets"] = pathway_sets
    out["pathway_sets_annotations"] = pathway_annotations

    # Deterministic no-inference defaults for gene-direction fields.
    out["sensitivity_genes_up"] = []
    out["sensitivity_genes_down"] = []
    out["resistance_genes_up"] = []
    out["resistance_genes_down"] = []
    out["sensitivity_genes_up_annotations"] = []
    out["sensitivity_genes_down_annotations"] = []
    out["resistance_genes_up_annotations"] = []
    out["resistance_genes_down_annotations"] = []

    # Direct deterministic KG edges from explicit primary targets.
    if out["drug_name"]:
        out["kg_gene_relationships"] = [f"{out['drug_name']}|inhibits|{gene}" for gene in out["primary_targets"]]

    # Citation extraction: use final references section if present.
    reference_block = ""
    if 10 in sections:
        reference_block = sections[10]
    else:
        refs = list(re.finditer(r"(?im)^##\s*references\s*$", markdown_text))
        if refs:
            reference_block = markdown_text[refs[-1].end() :].strip()

    reference_entries = _extract_reference_entries(reference_block) if reference_block else {}
    cited_by_section: Dict[int, Set[str]] = {}
    for sec_no, text in sections.items():
        ids = _parse_citation_ids(text)
        slug = _section_slug(sec_no)
        for cid in ids:
            cited_by_section.setdefault(cid, set()).add(slug)
    for cid in section9_citations:
        cited_by_section.setdefault(cid, set()).add("pathway_evidence_table")

    citations_out: List[Dict[str, Any]] = []
    for cid in sorted(reference_entries.keys()):
        raw = reference_entries[cid]
        citation_obj = {
            "citation_id": cid,
            "raw_text": raw,
            "urls": URL_RE.findall(raw),
            "sections": sorted(cited_by_section.get(cid, set())),
        }
        citations_out.append(citation_obj)
    out["citations"] = citations_out

    notes: List[str] = []
    if not reference_block:
        notes.append("References section not found; citations left empty.")
    if not out["primary_targets"]:
        notes.append("Primary targets were not explicitly parseable from section 4 table.")
    if not out["pathway_sets"]:
        notes.append("Pathway evidence table could not be parsed or was missing.")
    out["notes"] = " ".join(notes).strip()

    return harmonize_output_keys(out)


def convert_md_file_to_json_file(
    md_path: Path,
    *,
    json_path: Optional[Path] = None,
    drug_name: Optional[str] = None,
    cancer_name: Optional[str] = None,
) -> Path:
    text = md_path.read_text(encoding="utf-8")
    payload = convert_markdown_to_schema_json(
        text,
        drug_name=drug_name,
        cancer_name=cancer_name,
    )
    out_path = json_path or md_path.with_suffix(".json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path

