import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


EXCLUDED_COLLECTION_PREFIXES = (
    "C1",
    "C2:CGP",
    "C3",
    "C4",
    "C7",
    "C8",
)

COLLECTION_PRIORITY = [
    "HALLMARK",
    "REACTOME",
    "KEGG_MEDICUS",
    "KEGG",
    "GOBP",
    "GO",
    "BIOCARTA",
]

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "axis",
    "baseline",
    "by",
    "cascade",
    "complex",
    "contributing",
    "downstream",
    "for",
    "from",
    "in",
    "include",
    "including",
    "into",
    "is",
    "leading",
    "mechanism",
    "of",
    "on",
    "or",
    "pathway",
    "response",
    "sensitivity",
    "signaling",
    "survival",
    "that",
    "the",
    "to",
    "upregulated",
    "via",
    "with",
}

CONCEPT_SYNONYMS = {
    "erbb": {"egfr", "erbb", "erbb1", "erbb2", "erbb3", "erbb4", "her1", "her2", "her3", "her4"},
    "mapk": {"mapk", "ras", "raf", "mek", "erk", "erk1", "erk2", "kras", "braf", "raf1", "map2k1", "map2k2", "mapk1", "mapk3"},
    "pi3k_akt": {"pi3k", "akt", "akt1", "akt2", "akt3", "mtor", "pik3ca", "pik3cb", "pik3cd", "pik3cg", "pik3r1", "pik3r2", "pik3r3", "pik3r5", "pten", "pdpk1"},
    "met_hgf": {"met", "cmet", "c-met", "hgf"},
    "vegf_angiogenesis": {"vegf", "vegfa", "angiogenesis", "flt1", "flt4", "kdr", "vascular"},
    "stat3_il6": {"stat3", "il6", "il-6", "jak", "jak1", "jak2", "bcl2", "bclxl"},
    "pdl1_immune": {"pdl1", "pd-l1", "cd274", "immune", "evasion"},
    "nsclc": {"nsclc", "non", "small", "cell", "lung", "cancer", "carcinoma"},
    "elk1": {"elk1"},
    "cip2a": {"cip2a"},
    "apoptosis_survival": {"survival", "apoptosis", "bad", "casp9"},
}

CONCEPT_GENE_HINTS = {
    "erbb": ["EGFR", "ERBB2", "ERBB3", "ERBB4"],
    "mapk": ["KRAS", "BRAF", "RAF1", "MAP2K1", "MAPK1", "ELK1"],
    "pi3k_akt": ["AKT1", "AKT2", "AKT3", "PIK3CA", "PIK3R1"],
    "met_hgf": ["MET", "HGF"],
    "vegf_angiogenesis": ["VEGFA", "FLT1", "FLT4", "KDR"],
    "stat3_il6": ["STAT3", "JAK1", "JAK2", "IL6"],
    "pdl1_immune": ["CD274"],
    "nsclc": ["EGFR", "KRAS", "BRAF", "AKT1"],
    "elk1": ["ELK1"],
    "cip2a": ["CIP2A"],
    "apoptosis_survival": ["BAD", "CASP9"],
}

WEAK_MAPPING_THRESHOLD = 60
OUT_OF_CONTEXT_TERMS = (
    "variant",
    "overexpression",
    "pathogen",
    "infection",
    "viral",
    "virus",
    "hbv",
    "hcv",
    "insulin",
    "diabetes",
)
DISALLOWED_SUGGESTION_TERMS = (
    "variant",
    "pathogen",
    "reference",
)


@dataclass
class MSigDBRecord:
    name: str
    collection: str
    description: str


@dataclass
class RowContext:
    row_key: str
    original_pathway: str
    rationale: str
    pathway_concepts: Set[str]
    rationale_concepts: Set[str]
    pathway_tokens: Set[str]
    rationale_tokens: Set[str]
    expected_genes: List[str]


@dataclass
class MappingScore:
    mapped_name: str
    collection: str
    description: str
    genes: List[str]
    name_fidelity: int
    rationale_alignment: int
    gene_evidence: int
    specificity: int
    total: int
    matched_concepts: List[str]
    matched_genes: List[str]
    issues: List[str]


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def row_order(key: str) -> Tuple[int, str]:
    match = re.search(r"(\d+)", key)
    return (int(match.group(1)) if match else 10**9, key)


def clean_text(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "â€“": "-",
        "â€”": "-",
        "â€‘": "-",
        "â€™": "'",
        "â€œ": '"',
        "â€": '"',
        "Î²": "beta",
        "Îº": "kappa",
        "Ã—": "x",
        "Pathwayâ€“drug": "Pathway-drug",
        "PathwayÃ¢â‚¬â€œdrug": "Pathway-drug",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text).strip()


def normalize_tokens(text: str) -> Set[str]:
    cleaned = clean_text(text).lower()
    cleaned = cleaned.replace("pd-l1", "pdl1")
    cleaned = cleaned.replace("il-6", "il6")
    cleaned = cleaned.replace("non-small-cell", "non small cell")
    tokens = set(re.findall(r"[a-z0-9]+", cleaned))
    return {token for token in tokens if len(token) >= 2 and token not in STOPWORDS}


def content_tokens(text: str) -> Set[str]:
    return {token for token in normalize_tokens(text) if token not in {"pathway", "signaling", "signal", "response", "baseline"}}


def extract_gene_symbols(text: str) -> List[str]:
    cleaned = clean_text(text)
    cleaned = cleaned.replace("PD-L1", "CD274")
    cleaned = cleaned.replace("HER2", "ERBB2")
    cleaned = cleaned.replace("HER3", "ERBB3")
    cleaned = cleaned.replace("HER4", "ERBB4")
    cleaned = cleaned.replace("HER1", "EGFR")
    raw = re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", cleaned)
    genes: List[str] = []
    for token in raw:
        normalized = token.replace("-", "")
        if normalized in {"NSCLC", "TKIS"}:
            continue
        genes.append(normalized)
    unique = []
    seen = set()
    for gene in genes:
        if gene not in seen:
            seen.add(gene)
            unique.append(gene)
    return unique


def detect_concepts(text: str) -> Set[str]:
    normalized = normalize_tokens(text)
    joined = " ".join(sorted(normalized))
    concepts = set()
    for concept, synonyms in CONCEPT_SYNONYMS.items():
        for synonym in synonyms:
            if " " in synonym:
                if synonym in joined:
                    concepts.add(concept)
                    break
            elif synonym in normalized:
                concepts.add(concept)
                break
    return concepts


def weighted_coverage(required: Set[str], actual: Set[str]) -> float:
    if not required:
        return 0.0
    return len(required & actual) / len(required)


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collection_priority(name: str) -> int:
    upper = (name or "").upper()
    for index, prefix in enumerate(COLLECTION_PRIORITY):
        if upper.startswith(prefix):
            return index
    return len(COLLECTION_PRIORITY)


def is_collection_allowed(collection: str) -> bool:
    for prefix in EXCLUDED_COLLECTION_PREFIXES:
        if collection == prefix or collection.startswith(prefix + ":"):
            return False
    return True


def build_shared_row_keys(*datasets: Dict[str, Any]) -> List[str]:
    if not datasets:
        return []
    shared = set(datasets[0].keys())
    for dataset in datasets[1:]:
        shared &= set(dataset.keys())
    return sorted(shared, key=row_order)


def load_msigdb_metadata(conn: sqlite3.Connection) -> List[MSigDBRecord]:
    query = """
        SELECT
            gs.standard_name,
            gs.collection_name,
            COALESCE(NULLIF(gsd.description_full, ''), NULLIF(gsd.description_brief, ''), '') AS description
        FROM gene_set gs
        LEFT JOIN gene_set_details gsd
            ON gsd.gene_set_id = gs.id
    """
    rows = conn.execute(query).fetchall()
    return [
        MSigDBRecord(
            name=str(row[0]),
            collection=str(row[1] or ""),
            description=clean_text(row[2]),
        )
        for row in rows
        if row[0] and is_collection_allowed(str(row[1] or ""))
    ]


def get_gene_symbols(
    conn: sqlite3.Connection,
    gene_cache: Dict[str, List[str]],
    pathway_name: str,
) -> List[str]:
    if pathway_name in gene_cache:
        return gene_cache[pathway_name]

    query = """
        SELECT gsym.symbol
        FROM gene_set gs
        JOIN gene_set_gene_symbol gsgs
            ON gsgs.gene_set_id = gs.id
        JOIN gene_symbol gsym
            ON gsym.id = gsgs.gene_symbol_id
        WHERE gs.standard_name = ?
        ORDER BY gsym.symbol
    """
    rows = conn.execute(query, (pathway_name,)).fetchall()
    genes = [str(row[0]) for row in rows if row[0]]
    gene_cache[pathway_name] = genes
    return genes


def build_row_context(row_key: str, fact_entry: Dict[str, Any]) -> RowContext:
    original_pathway = clean_text(fact_entry.get("Pathway ID/Name", fact_entry.get("Original Pathway Name", "")))
    rationale = clean_text(fact_entry.get("Rationale", ""))

    pathway_concepts = detect_concepts(original_pathway)
    rationale_concepts = detect_concepts(f"{original_pathway} {rationale}")

    explicit_genes = extract_gene_symbols(f"{original_pathway} {rationale}")
    expected_genes: List[str] = list(explicit_genes)
    for concept in sorted(rationale_concepts, key=str):
        for gene in CONCEPT_GENE_HINTS.get(concept, []):
            if gene not in expected_genes:
                expected_genes.append(gene)
    expected_genes = expected_genes[:8]

    return RowContext(
        row_key=row_key,
        original_pathway=original_pathway,
        rationale=rationale,
        pathway_concepts=pathway_concepts,
        rationale_concepts=rationale_concepts,
        pathway_tokens=content_tokens(original_pathway),
        rationale_tokens=content_tokens(f"{original_pathway} {rationale}"),
        expected_genes=expected_genes,
    )


def score_specificity(
    context: RowContext,
    mapped_name: str,
    matched_pathway_coverage: float,
    candidate_concepts: Set[str],
) -> int:
    if mapped_name == "UNMAPPED":
        return 0

    score = 6
    if matched_pathway_coverage >= 0.75:
        score += 2
    elif matched_pathway_coverage >= 0.5:
        score += 1

    if collection_priority(mapped_name) <= 2:
        score += 1

    lower_name = mapped_name.lower()
    broad_disease_terms = ("cancer", "carcinoma", "tumor", "neoplasm")
    if any(term in lower_name for term in broad_disease_terms) and (context.pathway_concepts - {"nsclc"}):
        score -= 3

    mechanistic_concepts = context.pathway_concepts - {"nsclc", "apoptosis_survival"}
    if len(mechanistic_concepts) >= 2 and len(candidate_concepts & mechanistic_concepts) <= 1:
        score -= 2

    return max(0, min(10, score))


def score_mapped_pathway(
    context: RowContext,
    mapped_name: str,
    lookup: Dict[str, MSigDBRecord],
    conn: sqlite3.Connection,
    gene_cache: Dict[str, List[str]],
) -> MappingScore:
    if not mapped_name or mapped_name == "UNMAPPED" or mapped_name not in lookup:
        issues = ["No canonical pathway was available for scoring."]
        return MappingScore(
            mapped_name="UNMAPPED",
            collection="",
            description="",
            genes=[],
            name_fidelity=0,
            rationale_alignment=0,
            gene_evidence=0,
            specificity=0,
            total=0,
            matched_concepts=[],
            matched_genes=[],
            issues=issues,
        )

    record = lookup[mapped_name]
    genes = get_gene_symbols(conn, gene_cache, mapped_name)
    candidate_name_concepts = detect_concepts(record.name)
    candidate_concepts = detect_concepts(f"{record.name} {record.description} {' '.join(genes)}")

    pathway_coverage = weighted_coverage(context.pathway_concepts, candidate_name_concepts)
    pathway_token_overlap = jaccard(context.pathway_tokens, content_tokens(record.name))
    name_fidelity = round(35 * ((0.8 * pathway_coverage) + (0.2 * pathway_token_overlap)))

    rationale_coverage = weighted_coverage(context.rationale_concepts, candidate_concepts)
    rationale_token_overlap = jaccard(context.rationale_tokens, content_tokens(f"{record.name} {record.description}"))
    rationale_alignment = round(35 * ((0.65 * rationale_coverage) + (0.35 * rationale_token_overlap)))

    gene_set = set(genes)
    matched_genes = [gene for gene in context.expected_genes if gene in gene_set]
    gene_evidence = round(20 * (len(matched_genes) / len(context.expected_genes))) if context.expected_genes else 0

    specificity = score_specificity(context, mapped_name, pathway_coverage, candidate_concepts)
    total = name_fidelity + rationale_alignment + gene_evidence + specificity

    candidate_text_lower = f"{record.name} {record.description}".lower()
    context_text_lower = f"{context.original_pathway} {context.rationale}".lower()
    context_penalty = 0
    for term in OUT_OF_CONTEXT_TERMS:
        if term in candidate_text_lower and term not in context_text_lower:
            context_penalty += 3

    total = max(0, min(100, total - context_penalty))

    issues: List[str] = []
    if pathway_coverage < 0.5:
        issues.append("candidate name only captures part of the stated pathway")
    if rationale_coverage < 0.5:
        issues.append("description misses major rationale elements")
    if context.expected_genes and len(matched_genes) < max(1, min(2, len(context.expected_genes) // 2)):
        issues.append("gene-set support is limited for the key nodes in the rationale")
    if specificity <= 4 and mapped_name != "UNMAPPED":
        issues.append("candidate is broad relative to the requested mechanism")

    return MappingScore(
        mapped_name=record.name,
        collection=record.collection,
        description=record.description,
        genes=genes,
        name_fidelity=name_fidelity,
        rationale_alignment=rationale_alignment,
        gene_evidence=gene_evidence,
        specificity=specificity,
        total=total,
        matched_concepts=sorted(candidate_concepts & context.rationale_concepts),
        matched_genes=matched_genes,
        issues=issues,
    )


def rough_candidate_score(context: RowContext, record: MSigDBRecord) -> float:
    name_concepts = detect_concepts(record.name)
    concepts = detect_concepts(f"{record.name} {record.description}")
    pathway_coverage = weighted_coverage(context.pathway_concepts, name_concepts)
    rationale_coverage = weighted_coverage(context.rationale_concepts, concepts)
    token_overlap = jaccard(context.pathway_tokens, content_tokens(record.name))
    disease_penalty = 0.08 if "cancer" in record.name.lower() and (context.pathway_concepts - {"nsclc"}) else 0.0
    context_penalty = 0.0
    lowered = f"{record.name} {record.description}".lower()
    context_text = f"{context.original_pathway} {context.rationale}".lower()
    for term in OUT_OF_CONTEXT_TERMS:
        if term in lowered and term not in context_text:
            context_penalty += 0.08
    priority_bonus = max(0.0, 0.03 * (len(COLLECTION_PRIORITY) - collection_priority(record.name)))
    return (0.50 * pathway_coverage) + (0.30 * rationale_coverage) + (0.20 * token_overlap) + priority_bonus - disease_penalty - context_penalty


def is_suggestion_allowed(context: RowContext, record: MSigDBRecord) -> bool:
    candidate_text = f"{record.name} {record.description}".lower()
    context_text = f"{context.original_pathway} {context.rationale}".lower()
    for term in DISALLOWED_SUGGESTION_TERMS:
        if term in candidate_text and term not in context_text:
            return False
    return True


def suggest_better_candidate(
    context: RowContext,
    records: Sequence[MSigDBRecord],
    lookup: Dict[str, MSigDBRecord],
    conn: sqlite3.Connection,
    gene_cache: Dict[str, List[str]],
    excluded_names: Set[str],
) -> Optional[MappingScore]:
    rough_ranked = sorted(
        (
            (rough_candidate_score(context, record), collection_priority(record.name), record.name)
            for record in records
            if record.name not in excluded_names and is_suggestion_allowed(context, record)
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    shortlisted = [name for rough, _, name in rough_ranked[:35] if rough > 0]
    if not shortlisted:
        return None

    scored = [score_mapped_pathway(context, name, lookup, conn, gene_cache) for name in shortlisted]
    scored = [item for item in scored if item.total > 0]
    if not scored:
        return None

    scored.sort(key=lambda item: (-item.total, collection_priority(item.mapped_name), item.mapped_name))
    return scored[0]


def choose_best_mapping(
    sem_score: MappingScore,
    nl_score: MappingScore,
    suggestion: Optional[MappingScore],
) -> str:
    if suggestion and max(sem_score.total, nl_score.total) < WEAK_MAPPING_THRESHOLD:
        if suggestion.total >= 40 and suggestion.total >= max(sem_score.total, nl_score.total) + 6:
            return f"Neither (suggested: {suggestion.mapped_name})"

    if sem_score.total == nl_score.total:
        sem_priority = collection_priority(sem_score.mapped_name)
        nl_priority = collection_priority(nl_score.mapped_name)
        if sem_priority <= nl_priority:
            return "Sem_LLM"
        return "NL2SQL"

    return "Sem_LLM" if sem_score.total > nl_score.total else "NL2SQL"


def summarize_mapping(score: MappingScore, fallback_reason: Optional[str] = None) -> str:
    if score.mapped_name == "UNMAPPED":
        return fallback_reason or "no canonical pathway was produced"

    reason_parts = []
    if score.matched_concepts:
        reason_parts.append(f"concept fit: {', '.join(score.matched_concepts[:4])}")
    if score.matched_genes:
        reason_parts.append(f"supporting genes: {', '.join(score.matched_genes[:6])}")
    if score.description:
        reason_parts.append(f"description fit: {clean_text(score.description)[:120]}")
    if score.issues:
        reason_parts.append(f"gap: {score.issues[0]}")
    return "; ".join(reason_parts[:4])


def reason_for_row(
    best_label: str,
    sem_score: MappingScore,
    nl_score: MappingScore,
    suggestion: Optional[MappingScore],
) -> str:
    if best_label.startswith("Neither"):
        suggestion_summary = summarize_mapping(suggestion) if suggestion else "a stronger canonical alternative was found"
        return (
            f"Both current mappings are weak. Sem_LLM: {summarize_mapping(sem_score)}. "
            f"NL2SQL: {summarize_mapping(nl_score)}. Better canonical candidate: {suggestion_summary}."
        )

    winner = sem_score if best_label == "Sem_LLM" else nl_score
    loser = nl_score if best_label == "Sem_LLM" else sem_score
    loser_name = "NL2SQL" if best_label == "Sem_LLM" else "Sem_LLM"
    return (
        f"{best_label} aligns better. Winner: {summarize_mapping(winner)}. "
        f"{loser_name} is weaker because {summarize_mapping(loser)}."
    )


def markdown_escape(text: str) -> str:
    cleaned = clean_text(text)
    return cleaned.replace("|", "\\|")


def load_sem_trace(trace_path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not trace_path:
        return {}
    data = load_json(trace_path)
    verifications = data.get("verifications", [])
    return {
        str(item.get("Row")): item
        for item in verifications
        if isinstance(item, dict) and item.get("Row")
    }


def render_markdown_report(
    shared_rows: Sequence[str],
    row_results: Sequence[Dict[str, Any]],
    dropped_rows_count: int,
) -> str:
    lines = [
        "# Afatinib Pathway Mapping Comparison",
        "",
        f"This report compares the {len(shared_rows)} common mapped rows shared by the semantic+LLM and NL2SQL outputs. "
        f"The original fact-check JSON contains 12 rows, so {dropped_rows_count} rows are not shown here because "
        "they were filtered out upstream before mapping.",
        "",
        "| Row | Original pathway | Sem_LLM mapped pathway | Sem_LLM accuracy | NL2SQL mapped pathway | NL2SQL accuracy | Best accurately mapped | Reason for accuracy |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for result in row_results:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(result["row_key"]),
                    markdown_escape(result["original_pathway"]),
                    markdown_escape(result["sem_name"]),
                    str(result["sem_score"]),
                    markdown_escape(result["nl_name"]),
                    str(result["nl_score"]),
                    markdown_escape(result["best_label"]),
                    markdown_escape(result["reason"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Row Notes", ""])

    for result in row_results:
        lines.append(f"### {result['row_key']}")
        lines.append(f"- Original pathway: `{markdown_escape(result['original_pathway'])}`")
        lines.append(
            f"- Sem_LLM `{result['sem_name']}` scored {result['sem_score']}/100 "
            f"(name {result['sem_eval'].name_fidelity}, rationale {result['sem_eval'].rationale_alignment}, "
            f"genes {result['sem_eval'].gene_evidence}, specificity {result['sem_eval'].specificity})."
        )
        lines.append(f"  Reason: {summarize_mapping(result['sem_eval'], result.get('sem_failure_reason'))}.")
        if result.get("sem_trace_note"):
            lines.append(f"  Trace note: {markdown_escape(result['sem_trace_note'])}.")
        lines.append(
            f"- NL2SQL `{result['nl_name']}` scored {result['nl_score']}/100 "
            f"(name {result['nl_eval'].name_fidelity}, rationale {result['nl_eval'].rationale_alignment}, "
            f"genes {result['nl_eval'].gene_evidence}, specificity {result['nl_eval'].specificity})."
        )
        lines.append(f"  Reason: {summarize_mapping(result['nl_eval'], result.get('nl_failure_reason'))}.")
        if result["best_label"].startswith("Neither") and result.get("suggestion_name"):
            lines.append(
                f"- Suggested alternative: `{result['suggestion_name']}` scored {result['suggestion_score']}/100. "
                f"Reason: {summarize_mapping(result['suggestion_eval'])}."
            )
        lines.append(f"- Verdict: {markdown_escape(result['best_label'])}.")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def compare_pathway_mappings(
    factcheck_json: str,
    sem_json: str,
    nl2sql_json: str,
    msigdb_db: str,
    out_md: str,
    sem_trace_json: Optional[str] = None,
) -> Path:
    factcheck = load_json(factcheck_json)
    sem_map = load_json(sem_json)
    nl2sql_map = load_json(nl2sql_json)
    sem_trace = load_sem_trace(sem_trace_json)

    shared_rows = build_shared_row_keys(factcheck, sem_map, nl2sql_map)
    conn = sqlite3.connect(msigdb_db)
    try:
        records = load_msigdb_metadata(conn)
        lookup = {record.name: record for record in records}
        gene_cache: Dict[str, List[str]] = {}

        row_results: List[Dict[str, Any]] = []
        for row_key in shared_rows:
            context = build_row_context(row_key, factcheck[row_key])
            sem_entry = sem_map[row_key]
            nl_entry = nl2sql_map[row_key]

            sem_name = clean_text(sem_entry.get("Mapped MSigDB Pathway Name", "UNMAPPED")) or "UNMAPPED"
            nl_name = clean_text(nl_entry.get("Mapped MSigDB Pathway Name", "UNMAPPED")) or "UNMAPPED"

            sem_eval = score_mapped_pathway(context, sem_name, lookup, conn, gene_cache)
            nl_eval = score_mapped_pathway(context, nl_name, lookup, conn, gene_cache)

            suggestion_eval = suggest_better_candidate(
                context=context,
                records=records,
                lookup=lookup,
                conn=conn,
                gene_cache=gene_cache,
                excluded_names={sem_eval.mapped_name, nl_eval.mapped_name, "UNMAPPED"},
            )
            best_label = choose_best_mapping(sem_eval, nl_eval, suggestion_eval)

            trace_entry = sem_trace.get(row_key, {})
            trace_note = ""
            if trace_entry.get("original_mapped_name") and trace_entry.get("Final Mapped MSigDB"):
                trace_note = (
                    f"semantic+LLM corrected {clean_text(trace_entry['original_mapped_name'])} "
                    f"to {clean_text(trace_entry['Final Mapped MSigDB'])}"
                )

            row_result = {
                "row_key": row_key,
                "original_pathway": context.original_pathway,
                "sem_name": sem_eval.mapped_name,
                "sem_score": sem_eval.total,
                "sem_eval": sem_eval,
                "sem_failure_reason": clean_text(sem_entry.get("llm_reasoning", "")) or None,
                "nl_name": nl_eval.mapped_name,
                "nl_score": nl_eval.total,
                "nl_eval": nl_eval,
                "nl_failure_reason": clean_text(nl_entry.get("nl2sql_failure_reason", "")) or None,
                "suggestion_name": suggestion_eval.mapped_name if suggestion_eval else None,
                "suggestion_score": suggestion_eval.total if suggestion_eval else None,
                "suggestion_eval": suggestion_eval,
                "best_label": best_label,
                "sem_trace_note": trace_note,
            }
            row_result["reason"] = reason_for_row(best_label, sem_eval, nl_eval, suggestion_eval)
            row_results.append(row_result)

        markdown = render_markdown_report(
            shared_rows=shared_rows,
            row_results=row_results,
            dropped_rows_count=len(factcheck) - len(shared_rows),
        )
        out_path = Path(out_md)
        save_text(out_path, markdown)
        return out_path
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare semantic+LLM and NL2SQL MSigDB mappings against fact-check rationale and MSigDB evidence."
    )
    parser.add_argument("--factcheck-json", required=True)
    parser.add_argument("--sem-json", required=True)
    parser.add_argument("--nl2sql-json", required=True)
    parser.add_argument("--msigdb-db", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--sem-trace-json", required=False, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_path = compare_pathway_mappings(
        factcheck_json=args.factcheck_json,
        sem_json=args.sem_json,
        nl2sql_json=args.nl2sql_json,
        msigdb_db=args.msigdb_db,
        out_md=args.out_md,
        sem_trace_json=args.sem_trace_json,
    )
    print(f"Comparison report written to: {out_path}")


if __name__ == "__main__":
    main()
