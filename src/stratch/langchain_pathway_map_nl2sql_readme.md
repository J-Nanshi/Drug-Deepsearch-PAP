# LangGraph Pathway Map NL2SQL

## Overview
`langchain_pathway_map_nl2sql.py` is a trial Step 3 mapper that keeps the current pathway-mapping contract but replaces the direct NL2SQL generation flow with a custom LangGraph SQL agent.

The implementation follows the LangGraph SQL-agent pattern from the LangChain docs:
- list tables
- inspect schema
- generate SQL
- check SQL
- run SQL through an enforced collection-constrained wrapper

After SQL execution, the script keeps the deterministic post-processing from the existing Step 3 flow:
- validate returned names against the prefiltered MSigDB lookup
- build a semantic reranking layer over the allowed MSigDB corpus
- score candidates using semantic similarity, biological entity/concept coverage, SQL support, and pathway-family priority
- use semantic fallback retrieval when SQL candidates are weak
- apply biologically meaningful tie-breaking before any alphabetical fallback
- choose one final mapped MSigDB pathway or `UNMAPPED`

## What It Preserves
- Step 2 row filtering by `Include decision`
- relationship-class filtering
- final output key `Mapped MSigDB Pathway Name`
- top-level final output key `pathway_sets`
- per-drug final JSON output
- per-drug trace JSON output

## Main Logic
1. Load Step 2 JSON rows.
2. Keep only rows that are included and in allowed relationship classes.
3. Load MSigDB metadata from the SQLite database, filtered to:
   - `source_species_code = 'HS'`
   - included collections only: `H`, `C2:CP%`, `C5:GO:BP`
4. For each retained row:
   - build a pathway-mapping request from pathway name + rationale
   - run the custom LangGraph SQL agent
   - constrain executed SQL to allowed collections only: `H`, `C2:CP%`, `C5:GO:BP`
   - capture generated SQL, checked SQL, and tool output in trace fields
   - validate the checked SQL as a single read-only `SELECT`
   - execute the query against SQLite
   - filter invalid candidates
   - rerank SQL candidates using semantic similarity plus biological entity/concept coverage
   - trigger semantic fallback retrieval when SQL candidates are weak or biologically off-target
   - select the best biologically relevant candidate deterministically
   - write the selected pathway into the final output row
5. Save:
   - `<drug>.json`
   - `<drug>_trace_pathway_mapping.json`

## Output Files
### Final JSON
The final mapping JSON contains a top-level:
- `pathway_sets`

This is the unique list of mapped MSigDB pathway names in row order, excluding `UNMAPPED`.

Each retained row contains:
- `Mapped MSigDB Pathway Name`
- original pathway context fields
- `mapping_method`
- generated and checked SQL trace fields
- failure reason when no mapping is produced

### Trace JSON
The trace file includes:
- summary counts
- generated SQL
- checked SQL
- raw SQL-tool output
- candidate counts before and after validation
- semantic and biological ranking details for selected/top candidates
- `selection_stage` (`sql_rerank` or `semantic_fallback`)
- candidate rejection reasons when a stronger candidate is preferred
- chosen candidate
- top ranked candidates
- failure type and reason

## How To Run
```powershell
python src/stratch/langchain_pathway_map_nl2sql.py `
  --input-dir output/lung_cancer/step2_factcheck_json `
  --out-final-dir output/lung_cancer/trial/langgraph_mapping `
  --out-trace-dir output/lung_cancer/trial/langgraph_trace `
  --msigdb-sqlite-path utils/msigdb_v2025.1.Hs.db `
  --nl2sql-model gpt-4o
```

## Requirements
- `OPENAI_API_KEY` must be available in the environment or `.env`
- LangChain, LangGraph, and LangChain Community packages must be installed
- the MSigDB SQLite database must contain:
  - `gene_set`
  - `gene_set_details`
  - optional `namespace`

## Notes
- This script is intended as a cleaner, production-oriented trial implementation.
- Logging replaces print-control globals such as `PRINT_SUMMARY` and `PRINT_VERIFICATION_PROGRESS`.
- The LangGraph agent is non-interactive by design so it can run in batch mode.
- SQL is used for candidate generation, while final pathway selection is driven by semantic and biological reranking.
