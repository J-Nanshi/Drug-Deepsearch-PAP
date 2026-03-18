# Afatinib Pathway Mapping Comparison

This report compares the 5 common mapped rows shared by the semantic+LLM and NL2SQL outputs. The original fact-check JSON contains 12 rows, so 7 rows are not shown here because they were filtered out upstream before mapping.

| Row | Original pathway | Sem_LLM mapped pathway | Sem_LLM accuracy | NL2SQL mapped pathway | NL2SQL accuracy | Best accurately mapped | Reason for accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Row1 | EGFR/ErbB signaling (activating EGFR mutations) | KEGG_ERBB_SIGNALING_PATHWAY | 75 | GOBP_ERBB2_EGFR_SIGNALING_PATHWAY | 65 | Sem_LLM | Sem_LLM aligns better. Winner: concept fit: erbb, nsclc; supporting genes: EGFR, ERBB2, ERBB3, ERBB4, KRAS, BRAF; description fit: The ErbB family of receptor tyrosine kinases (RTKs) couples binding of extracellular growth factor ligands to intracellu. NL2SQL is weaker because concept fit: erbb, nsclc; supporting genes: EGFR, ERBB2; description fit: The series of molecular signals initiated by binding of a ligand to an epidermal growth factor receptor (EGFR/ERBB1) on . |
| Row4 | RAS–RAF–MEK–ERK (MAPK) cascade | KEGG_MAPK_SIGNALING_PATHWAY | 68 | BIOCARTA_RAS_PATHWAY | 62 | Sem_LLM | Sem_LLM aligns better. Winner: concept fit: mapk, nsclc; supporting genes: KRAS, BRAF, RAF1; description fit: The mitogen-activated protein kinase (MAPK) cascade is a highly conserved module that is involved in various cellular fu. NL2SQL is weaker because concept fit: mapk, nsclc; supporting genes: RAF1; description fit: Ras activates many signaling cascades. Here we illustrate some of the well-characterized cascades in a generic compilati; gap: gene-set support is limited for the key nodes in the rationale. |
| Row5 | MET/HGF pathway | REACTOME_MET_ACTIVATES_PI3K_AKT_SIGNALING | 53 | UNMAPPED | 0 | Neither (suggested: BIOCARTA_MET_PATHWAY) | Both current mappings are weak. Sem_LLM: concept fit: met_hgf, pi3k_akt; supporting genes: MET, HGF; description fit: MET activates PI3K/AKT signaling; gap: description misses major rationale elements. NL2SQL: no canonical pathway was produced. Better canonical candidate: concept fit: mapk, met_hgf, nsclc, pi3k_akt; supporting genes: MET, HGF; description fit: The hepatocyte growth factor receptor, also called c-Met, is activated by HGF and stimulates proliferation of hepatocyte. |
| Row9 | EGFR–STAT3–VEGF/angiogenesis and PD‑L1/immune evasion | REACTOME_SIGNALING_BY_VEGF | 19 | BIOCARTA_VEGF_PATHWAY | 18 | Sem_LLM | Sem_LLM aligns better. Winner: concept fit: vegf_angiogenesis; description fit: Signaling by VEGF; gap: candidate name only captures part of the stated pathway. NL2SQL is weaker because concept fit: vegf_angiogenesis; description fit: Vascular endothelial growth factor (VEGF) plays a key role in physiological blood vessel formation and pathological angi; gap: candidate name only captures part of the stated pathway. |
| Row12 | ELK1–CIP2A–AKT survival axis (downstream of ERK) | KEGG_NON_SMALL_CELL_LUNG_CANCER | 26 | UNMAPPED | 0 | Neither (suggested: BIOCARTA_RAS_PATHWAY) | Both current mappings are weak. Sem_LLM: concept fit: apoptosis_survival, mapk, nsclc, pi3k_akt; supporting genes: BAD, CASP9, KRAS, BRAF; description fit: Non-small-cell lung cancer (NSCLC) accounts for approximately 80% of lung cancer and represents a heterogeneous group of; gap: candidate name only captures part of the stated pathway. NL2SQL: no canonical pathway was produced. Better canonical candidate: concept fit: apoptosis_survival, elk1, mapk, nsclc; supporting genes: ELK1, BAD, CASP9; description fit: Ras activates many signaling cascades. Here we illustrate some of the well-characterized cascades in a generic compilati; gap: candidate name only captures part of the stated pathway. |

## Row Notes

### Row1
- Original pathway: `EGFR/ErbB signaling (activating EGFR mutations)`
- Sem_LLM `KEGG_ERBB_SIGNALING_PATHWAY` scored 75/100 (name 29, rationale 23, genes 15, specificity 8).
  Reason: concept fit: erbb, nsclc; supporting genes: EGFR, ERBB2, ERBB3, ERBB4, KRAS, BRAF; description fit: The ErbB family of receptor tyrosine kinases (RTKs) couples binding of extracellular growth factor ligands to intracellu.
- NL2SQL `GOBP_ERBB2_EGFR_SIGNALING_PATHWAY` scored 65/100 (name 29, rationale 23, genes 5, specificity 8).
  Reason: concept fit: erbb, nsclc; supporting genes: EGFR, ERBB2; description fit: The series of molecular signals initiated by binding of a ligand to an epidermal growth factor receptor (EGFR/ERBB1) on .
- Verdict: Sem_LLM.

### Row4
- Original pathway: `RAS–RAF–MEK–ERK (MAPK) cascade`
- Sem_LLM `KEGG_MAPK_SIGNALING_PATHWAY` scored 68/100 (name 29, rationale 23, genes 8, specificity 8).
  Reason: concept fit: mapk, nsclc; supporting genes: KRAS, BRAF, RAF1; description fit: The mitogen-activated protein kinase (MAPK) cascade is a highly conserved module that is involved in various cellular fu.
  Trace note: semantic+LLM corrected BIOCARTA_CDK5_PATHWAY to KEGG_MAPK_SIGNALING_PATHWAY.
- NL2SQL `BIOCARTA_RAS_PATHWAY` scored 62/100 (name 29, rationale 23, genes 2, specificity 8).
  Reason: concept fit: mapk, nsclc; supporting genes: RAF1; description fit: Ras activates many signaling cascades. Here we illustrate some of the well-characterized cascades in a generic compilati; gap: gene-set support is limited for the key nodes in the rationale.
- Verdict: Sem_LLM.

### Row5
- Original pathway: `MET/HGF pathway`
- Sem_LLM `REACTOME_MET_ACTIVATES_PI3K_AKT_SIGNALING` scored 53/100 (name 29, rationale 10, genes 5, specificity 9).
  Reason: concept fit: met_hgf, pi3k_akt; supporting genes: MET, HGF; description fit: MET activates PI3K/AKT signaling; gap: description misses major rationale elements.
  Trace note: semantic+LLM corrected BIOCARTA_MET_PATHWAY to REACTOME_MET_ACTIVATES_PI3K_AKT_SIGNALING.
- NL2SQL `UNMAPPED` scored 0/100 (name 0, rationale 0, genes 0, specificity 0).
  Reason: No valid candidates returned after filtering/validation..
- Suggested alternative: `BIOCARTA_MET_PATHWAY` scored 62/100. Reason: concept fit: mapk, met_hgf, nsclc, pi3k_akt; supporting genes: MET, HGF; description fit: The hepatocyte growth factor receptor, also called c-Met, is activated by HGF and stimulates proliferation of hepatocyte.
- Verdict: Neither (suggested: BIOCARTA_MET_PATHWAY).

### Row9
- Original pathway: `EGFR–STAT3–VEGF/angiogenesis and PD‑L1/immune evasion`
- Sem_LLM `REACTOME_SIGNALING_BY_VEGF` scored 19/100 (name 8, rationale 6, genes 0, specificity 5).
  Reason: concept fit: vegf_angiogenesis; description fit: Signaling by VEGF; gap: candidate name only captures part of the stated pathway.
  Trace note: semantic+LLM corrected PID_LYMPH_ANGIOGENESIS_PATHWAY to REACTOME_SIGNALING_BY_VEGF.
- NL2SQL `BIOCARTA_VEGF_PATHWAY` scored 18/100 (name 8, rationale 6, genes 0, specificity 4).
  Reason: concept fit: vegf_angiogenesis; description fit: Vascular endothelial growth factor (VEGF) plays a key role in physiological blood vessel formation and pathological angi; gap: candidate name only captures part of the stated pathway.
- Verdict: Sem_LLM.

### Row12
- Original pathway: `ELK1–CIP2A–AKT survival axis (downstream of ERK)`
- Sem_LLM `KEGG_NON_SMALL_CELL_LUNG_CANCER` scored 26/100 (name 0, rationale 16, genes 10, specificity 3).
  Reason: concept fit: apoptosis_survival, mapk, nsclc, pi3k_akt; supporting genes: BAD, CASP9, KRAS, BRAF; description fit: Non-small-cell lung cancer (NSCLC) accounts for approximately 80% of lung cancer and represents a heterogeneous group of; gap: candidate name only captures part of the stated pathway.
  Trace note: semantic+LLM corrected KEGG_MEDICUS_REFERENCE_EGF_EGFR_PLCG_ERK_SIGNALING_PATHWAY to KEGG_NON_SMALL_CELL_LUNG_CANCER.
- NL2SQL `UNMAPPED` scored 0/100 (name 0, rationale 0, genes 0, specificity 0).
  Reason: No valid candidates returned after filtering/validation..
- Suggested alternative: `BIOCARTA_RAS_PATHWAY` scored 40/100. Reason: concept fit: apoptosis_survival, elk1, mapk, nsclc; supporting genes: ELK1, BAD, CASP9; description fit: Ras activates many signaling cascades. Here we illustrate some of the well-characterized cascades in a generic compilati; gap: candidate name only captures part of the stated pathway.
- Verdict: Neither (suggested: BIOCARTA_RAS_PATHWAY).
