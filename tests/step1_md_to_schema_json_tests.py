import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.step1_md_to_schema_json import CANONICAL_KEYS, convert_markdown_to_schema_json


SYNTHETIC_MD = """
## 1. Drug Summary
Adagrasib is a KRAS G12C inhibitor.[1]

## 2. Identifiers & Synonyms
- Generic: adagrasib
- Synonyms: MRTX849; Krazati
- ChEMBL ID: CHEMBL123456
- DrugBank ID: DB01234

## 3. Mechanism of Action
Adagrasib covalently inhibits KRAS G12C.[1]

## 4. Primary Targets (Human)
| Target / Protein | Gene | Notes |
| --- | --- | --- |
| KRAS G12C | KRAS | Direct target |

## 7. Contraindications and Safety
- Avoid strong CYP3A inhibitors.

## 9. Pathway Evidence Table
| Pathway ID/Name | Regulation (Up/Down) | Effect (Sensitive/Resistant) | Biological Rationale | References |
| --- | --- | --- | --- | --- |
| KEGG: hsa04014 - Ras signaling pathway | Down | Sensitive | Direct KRAS pathway suppression. | [1] |

## 10. References
[1] Example source - https://pubmed.ncbi.nlm.nih.gov/12345678/
""".strip()


class TestStep1MdToSchemaJson(unittest.TestCase):
    def test_contract_keys_and_types(self):
        data = convert_markdown_to_schema_json(
            SYNTHETIC_MD,
            drug_name="adagrasib",
            cancer_name="lung",
        )

        self.assertEqual(set(data.keys()), set(CANONICAL_KEYS))
        self.assertIsInstance(data["synonyms"], list)
        self.assertIsInstance(data["primary_targets"], list)
        self.assertIsInstance(data["pathway_sets"], list)
        self.assertIsInstance(data["pathway_sets_annotations"], dict)
        self.assertIsInstance(data["citations"], list)
        self.assertEqual(data["drug_name"], "adagrasib")
        self.assertEqual(data["cancer_indication"], "lung cancer")

    def test_reference_and_citation_range_parsing(self):
        md = SYNTHETIC_MD.replace("| [1] |", "| [1]-[1] |")
        data = convert_markdown_to_schema_json(md, drug_name="adagrasib", cancer_name="lung")
        self.assertEqual(len(data["citations"]), 1)
        self.assertEqual(data["citations"][0]["citation_id"], 1)
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/12345678/", data["citations"][0]["urls"])
        self.assertIn("pathway_evidence_table", data["citations"][0]["sections"])

    def test_golden_file_shape(self):
        sample = Path("output/lung_cancer/step1_deepsearch/adagrasib.md")
        if not sample.exists():
            self.skipTest(f"Golden markdown missing: {sample}")

        data = convert_markdown_to_schema_json(
            sample.read_text(encoding="utf-8"),
            drug_name="adagrasib",
            cancer_name="lung",
        )

        self.assertEqual(set(data.keys()), set(CANONICAL_KEYS))
        self.assertIsInstance(data["citations"], list)
        self.assertTrue(all("citation_id" in c and "raw_text" in c and "urls" in c and "sections" in c for c in data["citations"]))
        json.dumps(data, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
