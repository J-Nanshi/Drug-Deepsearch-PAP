"""Step 4 drug administration pathway combinations pipeline.

Overview:
This script processes Step-3 mapping JSON files, extracts mapped MSigDB pathways,
generates before/after administration pathway-drug combinations with an LLM,
and writes one per-drug Step-4 JSON output.

Inputs:
- CLI:
  - `--input-dir`
  - `--output-dir`
  - `--prompt-path`
  - `--cancer-type`
  - `--file-pattern` (optional, default: `*.json`)
- Environment:
  - `OPENAI_API_KEY`
- Per-row fields (from Step-3 mapping JSON):
  - `Mapped MSigDB Pathway Name`

Logic:
1. Load prompt template text from `--prompt-path`.
2. Apply cancer-type substitution (`breast cancer`/`breast`) using `--cancer-type`.
3. Read Step-3 mapping JSON files from `--input-dir` matching `--file-pattern`.
4. For each file:
   - infer drug name from filename stem,
   - extract unique mapped MSigDB pathways,
   - generate 8 scenario combinations per pathway (before/after administration),
   - parse/validate JSON responses with retry logic,
   - aggregate results and compute summary counts.
5. Persist output JSON as `<drug>.json` in `--output-dir`.

Outputs:
- `<drug>.json` (Step-4 administration combinations per drug)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from time import sleep
from typing import Any, Dict, List, Optional

from openai import OpenAI

# OpenAI Configuration
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

client: Optional[OpenAI] = None

# LLM Model Configuration
LLM_MODEL = "gpt-4o"
LLM_TEMPERATURE = 0.3
LLM_MAX_TOKENS = 4000

# Display controls
PRINT_PROGRESS = True

# Validation tags for mechanistic classification
VALIDATION_TAGS = [
    "mechanistically accurate and clinically validated",
    "mechanistically accurate only",
    "mechanistically rare",
    "mechanistically not possible",
]


def init_openai_client() -> None:
    """Initialize OpenAI client from environment variable."""
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


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def read_prompt_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def normalize_cancer_terms(cancer_type: str) -> Dict[str, str]:
    """Build cancer phrase/label for prompt substitutions."""
    cleaned = cancer_type.strip()
    if not cleaned:
        raise ValueError("--cancer-type cannot be empty.")

    if re.search(r"\bcancer\b", cleaned, flags=re.IGNORECASE):
        cancer_phrase = cleaned
    else:
        cancer_phrase = f"{cleaned} cancer"

    # Remove only trailing 'cancer' for single-word replacement.
    cancer_label = re.sub(r"\s*cancer\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    if not cancer_label:
        cancer_label = cleaned

    return {"cancer_phrase": cancer_phrase, "cancer_label": cancer_label}


def apply_cancer_type_to_prompt(prompt_text: str, cancer_type: str) -> str:
    """Replace breast-specific wording in prompt using the selected cancer type."""
    normalized = normalize_cancer_terms(cancer_type)
    transformed = re.sub(
        r"breast cancer",
        normalized["cancer_phrase"],
        prompt_text,
        flags=re.IGNORECASE,
    )
    transformed = re.sub(
        r"\bbreast\b",
        normalized["cancer_label"],
        transformed,
        flags=re.IGNORECASE,
    )
    return transformed


def extract_pathway_list(step3_data: Dict[str, Any]) -> List[str]:
    """Extract unique 'Mapped MSigDB Pathway Name' values from Step 3 output."""
    pathways: List[str] = []
    seen = set()
    for _, row_data in step3_data.items():
        if not isinstance(row_data, dict):
            continue
        pathway_name = row_data.get("Mapped MSigDB Pathway Name")
        if not isinstance(pathway_name, str):
            continue
        pathway_name = pathway_name.strip()
        if pathway_name and pathway_name not in seen:
            seen.add(pathway_name)
            pathways.append(pathway_name)
    return pathways


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
            print(f"OpenAI API error (attempt {attempt + 1}/{max_retries}): {e}")
            if "max_tokens" in err and "not supported" in err and attempt < max_retries - 1:
                try:
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = LLM_MAX_TOKENS
                    response = client.chat.completions.create(**kwargs)
                    return response.choices[0].message.content.strip()
                except Exception as e2:
                    print(f"Retry with max_completion_tokens also failed: {e2}")

            if attempt < max_retries - 1:
                sleep(2**attempt)
            else:
                raise
    return ""


def generate_pathway_combinations(
    drug_name: str,
    pathway_name: str,
    base_prompt: str,
    retry_on_parse_error: bool = True,
) -> Dict[str, Any]:
    """Generate before/after administration combinations for a single pathway."""
    safe_pathway_name = pathway_name.replace('"', '\\"')

    prompt = f"""{base_prompt}

DRUG NAME: {drug_name}

PATHWAY: {pathway_name}

TASK:
For the pathway "{safe_pathway_name}" and drug "{drug_name}", provide detailed biological descriptions for each of the following 8 scenarios.

For EACH scenario, also assign a validation tag from these options:
- "mechanistically accurate and clinically validated" = supported by clinical trial data and mechanistic understanding
- "mechanistically accurate only" = biologically plausible based on mechanism but lacks clinical validation
- "mechanistically rare" = possible but uncommon or unusual scenario
- "mechanistically not possible" = contradicts known biology or mechanism of action

BEFORE DRUG ADMINISTRATION (baseline tumor state):
1. Sensitive + Upregulation: What does it mean when this pathway is upregulated at baseline and the tumor is sensitive to {drug_name}?
2. Sensitive + Downregulation: What does it mean when this pathway is downregulated at baseline and the tumor is sensitive to {drug_name}?
3. Resistant + Upregulation: What does it mean when this pathway is upregulated at baseline and the tumor is resistant to {drug_name}?
4. Resistant + Downregulation: What does it mean when this pathway is downregulated at baseline and the tumor is resistant to {drug_name}?

AFTER DRUG ADMINISTRATION (treatment effect):
5. Sensitive + Upregulation: What happens to this pathway (upregulated) after {drug_name} treatment in sensitive tumors?
6. Sensitive + Downregulation: What happens to this pathway (downregulated) after {drug_name} treatment in sensitive tumors?
7. Resistant + Upregulation: What happens to this pathway (upregulated) after {drug_name} treatment in resistant tumors?
8. Resistant + Downregulation: What happens to this pathway (downregulated) after {drug_name} treatment in resistant tumors?

Return ONLY valid JSON (no markdown code blocks, no explanation outside JSON):
{{
  "pathway_name": "{safe_pathway_name}",
  "drug_name": "{drug_name}",
  "before_administration": {{
    "sensitive_upregulation": {{
      "description": "description of baseline sensitive + upregulated state",
      "validation_tag": "one of the four validation tags"
    }},
    "sensitive_downregulation": {{
      "description": "description of baseline sensitive + downregulated state",
      "validation_tag": "one of the four validation tags"
    }},
    "resistant_upregulation": {{
      "description": "description of baseline resistant + upregulated state",
      "validation_tag": "one of the four validation tags"
    }},
    "resistant_downregulation": {{
      "description": "description of baseline resistant + downregulated state",
      "validation_tag": "one of the four validation tags"
    }}
  }},
  "after_administration": {{
    "sensitive_upregulation": {{
      "description": "description of treatment effect on upregulated pathway in sensitive tumors",
      "validation_tag": "one of the four validation tags"
    }},
    "sensitive_downregulation": {{
      "description": "description of treatment effect on downregulated pathway in sensitive tumors",
      "validation_tag": "one of the four validation tags"
    }},
    "resistant_upregulation": {{
      "description": "description of treatment effect on upregulated pathway in resistant tumors",
      "validation_tag": "one of the four validation tags"
    }},
    "resistant_downregulation": {{
      "description": "description of treatment effect on downregulated pathway in resistant tumors",
      "validation_tag": "one of the four validation tags"
    }}
  }}
}}"""

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert in cancer biology, pharmacology, and pathway analysis. "
                "Provide precise, scientifically accurate descriptions of pathway-drug interactions. "
                "Return ONLY valid JSON without any markdown formatting or code blocks."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    max_parse_attempts = 2 if retry_on_parse_error else 1
    last_error = None
    raw_response = ""

    for parse_attempt in range(max_parse_attempts):
        try:
            response = call_openai_with_retry(messages)
            raw_response = response
            response = response.strip()

            if response.startswith("```"):
                response = re.sub(r"^```(?:json)?\s*\n?", "", response)
                response = re.sub(r"\n?```\s*$", "", response)

            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                response = json_match.group(0)

            response = re.sub(r",(\s*[}\]])", r"\1", response)
            result = json.loads(response)

            if "before_administration" not in result or "after_administration" not in result:
                raise ValueError("Response missing required fields (before_administration, after_administration)")

            return result

        except json.JSONDecodeError as e:
            last_error = f"JSON parsing failed: {str(e)}"
            print(f"  WARNING: Parse attempt {parse_attempt + 1}/{max_parse_attempts} failed for {pathway_name}: {e}")
            if parse_attempt < max_parse_attempts - 1:
                messages[1][
                    "content"
                ] += "\n\nIMPORTANT: Your previous response had JSON formatting errors. Please return ONLY valid JSON, no other text."
                sleep(1)
            continue

        except ValueError as e:
            last_error = str(e)
            print(f"  WARNING: Validation failed for {pathway_name}: {e}")
            break

        except Exception as e:
            last_error = str(e)
            print(f"  ERROR: LLM call failed for {pathway_name}: {e}")
            break

    print(f"  ERROR: All attempts failed for {pathway_name}")
    return {
        "pathway_name": pathway_name,
        "drug_name": drug_name,
        "error": last_error,
        "raw_response_preview": raw_response[:500] if raw_response else "No response",
        "before_administration": create_error_placeholder("LLM parsing failed"),
        "after_administration": create_error_placeholder("LLM parsing failed"),
    }


def create_error_placeholder(error_msg: str) -> Dict[str, Dict[str, str]]:
    """Create placeholder structure for failed pathways."""
    placeholder = {
        "description": f"ERROR: {error_msg}",
        "validation_tag": "mechanistically not possible",
    }
    return {
        "sensitive_upregulation": placeholder.copy(),
        "sensitive_downregulation": placeholder.copy(),
        "resistant_upregulation": placeholder.copy(),
        "resistant_downregulation": placeholder.copy(),
    }


def run_administration_pipeline(
    input_file: Path,
    base_prompt: str,
    output_dir: Path,
) -> str:
    """Run the administration combinations pipeline for one Step 3 mapping file."""
    drug_name = input_file.stem

    print(f"\n{'=' * 70}")
    print(f"Processing drug: {drug_name}")
    print(f"Input Step 3 JSON: {input_file}")
    print(f"{'=' * 70}")

    step3_data = load_json(str(input_file))
    pathways = extract_pathway_list(step3_data)
    print(f"Found {len(pathways)} unique mapped pathways")

    results = {
        "drug_name": drug_name,
        "input_file": str(input_file),
        "total_pathways": len(pathways),
        "llm_model": LLM_MODEL,
        "validation_tag_definitions": {
            "mechanistically accurate and clinically validated": "Supported by clinical trial data and mechanistic understanding",
            "mechanistically accurate only": "Biologically plausible based on mechanism but lacks clinical validation",
            "mechanistically rare": "Possible but uncommon or unusual scenario",
            "mechanistically not possible": "Contradicts known biology or mechanism of action",
        },
        "pathways": {},
    }

    for i, pathway in enumerate(pathways):
        if PRINT_PROGRESS:
            print(f"  [{i + 1}/{len(pathways)}] Processing: {pathway}...")
        combinations = generate_pathway_combinations(drug_name, pathway, base_prompt)
        results["pathways"][pathway] = combinations
        sleep(1.0)

    output_path = str(output_dir / f"{drug_name}.json")
    save_json(results, output_path)

    print(f"\n--- SUMMARY: {drug_name} ---")
    print(f"Pathways processed: {len(pathways)}")
    print(f"Output saved to: {output_path}")

    error_count = 0
    successful_count = 0
    validation_tag_counts = {tag: 0 for tag in VALIDATION_TAGS}
    failed_pathways = []

    for pathway_name, p in results["pathways"].items():
        if "error" in p:
            error_count += 1
            failed_pathways.append(pathway_name)
        else:
            successful_count += 1
            for admin_time in ["before_administration", "after_administration"]:
                if admin_time in p:
                    for combo in p[admin_time].values():
                        if isinstance(combo, dict) and "validation_tag" in combo:
                            tag = combo["validation_tag"]
                            if tag in validation_tag_counts:
                                validation_tag_counts[tag] += 1

    print(f"Successful: {successful_count}")
    if error_count > 0:
        print(f"Errors encountered: {error_count}")
        print(f"Failed pathways: {', '.join(failed_pathways)}")

    print("\n--- VALIDATION TAG DISTRIBUTION ---")
    for tag, count in validation_tag_counts.items():
        print(f"  {tag}: {count}")

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Step 4 drug-administration combinations from Step 3 mapping JSON."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing Step 3 mapping JSON files (<drug>.json).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where Step 4 output JSON files will be written.",
    )
    parser.add_argument(
        "--prompt-path",
        required=True,
        help="Path to prompt text file (e.g., prompts/prompt3b_before_after_adminstration_matrix.txt).",
    )
    parser.add_argument(
        "--cancer-type",
        required=True,
        help="Cancer type value used to replace breast-specific prompt text (e.g., lung, colorectal).",
    )
    parser.add_argument(
        "--file-pattern",
        default="*.json",
        help="Glob pattern for input files in --input-dir (default: *.json).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    prompt_path = Path(args.prompt_path)
    cancer_type = args.cancer_type
    file_pattern = args.file_pattern

    if not input_dir.is_dir():
        raise RuntimeError(f"Input directory does not exist: {input_dir}")
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading prompt template...")
    raw_prompt = read_prompt_text(prompt_path)
    base_prompt = apply_cancer_type_to_prompt(raw_prompt, cancer_type)
    print(f"Loaded prompt template ({len(raw_prompt)} chars)")
    print(f"Applied cancer-type substitutions using: {cancer_type}")

    input_files = sorted(input_dir.glob(file_pattern))
    if not input_files:
        raise RuntimeError(f"No files matched pattern '{file_pattern}' in {input_dir}")

    init_openai_client()

    print(f"\nFound {len(input_files)} input file(s) to process:")
    for f in input_files:
        print(f"  - {f.name}")

    outputs = []
    for input_file in input_files:
        try:
            output_path = run_administration_pipeline(input_file, base_prompt, output_dir)
            outputs.append(output_path)
        except Exception as e:
            print(f"ERROR processing {input_file}: {e}")
            import traceback

            traceback.print_exc()

    print(f"\n{'=' * 70}")
    print("PIPELINE COMPLETE")
    print(f"{'=' * 70}")
    print(f"Total files processed: {len(outputs)}")
    print("All outputs saved to:", output_dir)
