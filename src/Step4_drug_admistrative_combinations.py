# [markdown]
# Step 4: Drug Administration Pathway Combinations
# ------------------------------------------------------------------------------------------------
# This script takes the final_trial5.json output and generates pathway-drug interaction
# combinations for before and after drug administration.
#
# For each pathway, it generates 8 combinations:
# - Before Administration: (sensitive/resistant) × (upregulation/downregulation) = 4
# - After Administration: (sensitive/resistant) × (upregulation/downregulation) = 4
#
# Input:
#   - *_final_trial5.json from mapped_pathway_json/
#   - prompt3a.docx template
#
# Output:
#   - <drug>_administration_combinations.json

#Imports
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from time import sleep

# For reading .docx files
try:
    from docx import Document
except ImportError:
    raise ImportError("python-docx is required. Install with: pip install python-docx")

#Config
MSIGDB_SQLITE_PATH = r"msigdb_v2025.1.Hs.db/msigdb_v2025.1.Hs.db"

# OpenAI Configuration
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY not found. Please set it using one of these methods:\n"
        "1. Environment variable: $env:OPENAI_API_KEY = 'your-key'\n"
        "2. Create a .env file with: OPENAI_API_KEY=your-key\n"
        "3. Set directly in code (not recommended)"
    )

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# LLM Model Configuration
LLM_MODEL = "gpt-5.1"  # or "gpt-4-turbo", "gpt-4", etc.
LLM_TEMPERATURE = 0.4  # Low temperature for consistent responses
LLM_MAX_TOKENS = 4000

# --- Input/Output Paths ---
INPUT_DIR = r"mapped_pathway_json"
PROMPT_DOCX_PATH = r"D:\GS\pathway-enrichment-pipeline\Step3a\input\prompt3a.docx"
OUTPUT_DIR = r"mapped_pathway_json"

# Auto-detect trial5 final files
TRIAL5_FILES = list(Path(INPUT_DIR).glob("*_final_trial5.json"))

# Display controls
PRINT_PROGRESS = True

#Helpers
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def read_docx(path: str) -> str:
    """Read text content from a .docx file."""
    doc = Document(path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return "\n".join(full_text)

def extract_drug_name(filename: str) -> str:
    """Extract drug name from filename like 'ribociclib_step2_trial2_out_final_trial5.json'"""
    # Try to extract the first word before underscore
    stem = Path(filename).stem
    parts = stem.split("_")
    if parts:
        return parts[0].capitalize()
    return "Unknown"

def extract_pathway_list(trial5_data: Dict[str, Any]) -> List[str]:
    """Extract unique 'Mapped MSigDB Pathway Name' values from trial5 output."""
    pathways = []
    for row_key, row_data in trial5_data.items():
        pathway_name = row_data.get("Mapped MSigDB Pathway Name", "")
        if pathway_name and pathway_name not in pathways:
            pathways.append(pathway_name)
    return pathways

# LLM Helper
def call_openai_with_retry(messages: List[Dict[str, str]], max_retries: int = 3) -> str:
    """Call OpenAI API with retry logic."""
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
            print(f"OpenAI API error (attempt {attempt+1}/{max_retries}): {e}")

            # Handle max_tokens parameter issues
            if "max_tokens" in err and "not supported" in err and attempt < max_retries - 1:
                try:
                    kwargs.pop("max_tokens", None)
                    kwargs["max_completion_tokens"] = LLM_MAX_TOKENS
                    response = client.chat.completions.create(**kwargs)
                    return response.choices[0].message.content.strip()
                except Exception as e2:
                    print(f"Retry with max_completion_tokens also failed: {e2}")

            if attempt < max_retries - 1:
                sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
    return ""

# Validation tags for mechanistic classification
VALIDATION_TAGS = [
    "mechanistically accurate and clinically validated",
    "mechanistically accurate only",
    "mechanistically rare",
    "mechanistically not possible"
]

# Generate Combinations for a Single Pathway
def generate_pathway_combinations(
    drug_name: str,
    pathway_name: str,
    base_prompt: str,
    retry_on_parse_error: bool = True,
) -> Dict[str, Any]:
    """
    Generate before/after administration combinations for a single pathway.
    
    Returns a dict with:
    - pathway_name
    - before_administration: {sensitive_upregulation, sensitive_downregulation, 
                              resistant_upregulation, resistant_downregulation}
      Each contains: description, validation_tag
    - after_administration: {sensitive_upregulation, sensitive_downregulation,
                             resistant_upregulation, resistant_downregulation}
      Each contains: description, validation_tag
    """
    
    # Sanitize pathway name for prompt (remove special chars that might confuse JSON)
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
            "content": "You are an expert in cancer biology, pharmacology, and pathway analysis. Provide precise, scientifically accurate descriptions of pathway-drug interactions. Return ONLY valid JSON without any markdown formatting or code blocks."
        },
        {"role": "user", "content": prompt}
    ]
    
    max_parse_attempts = 2 if retry_on_parse_error else 1
    last_error = None
    raw_response = ""
    
    for parse_attempt in range(max_parse_attempts):
        try:
            response = call_openai_with_retry(messages)
            raw_response = response
            
            # Clean up response - remove markdown code blocks if present
            response = response.strip()
            
            # Remove markdown code blocks (```json ... ``` or ``` ... ```)
            if response.startswith("```"):
                response = re.sub(r"^```(?:json)?\s*\n?", "", response)
                response = re.sub(r"\n?```\s*$", "", response)
            
            # Try to find JSON object in response if it has extra text
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                response = json_match.group(0)
            
            # Fix common JSON issues
            # Remove trailing commas before } or ]
            response = re.sub(r',(\s*[}\]])', r'\1', response)
            
            result = json.loads(response)
            
            # Validate the result has expected structure
            if "before_administration" not in result or "after_administration" not in result:
                raise ValueError("Response missing required fields (before_administration, after_administration)")
            
            return result
            
        except json.JSONDecodeError as e:
            last_error = f"JSON parsing failed: {str(e)}"
            print(f"  WARNING: Parse attempt {parse_attempt+1}/{max_parse_attempts} failed for {pathway_name}: {e}")
            if parse_attempt < max_parse_attempts - 1:
                print(f"  Retrying with simplified prompt...")
                # Add instruction to be more careful with JSON
                messages[1]["content"] += "\n\nIMPORTANT: Your previous response had JSON formatting errors. Please return ONLY valid JSON, no other text."
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
    
    # Return error result with diagnostic info
    print(f"  ERROR: All attempts failed for {pathway_name}")
    return {
        "pathway_name": pathway_name,
        "drug_name": drug_name,
        "error": last_error,
        "raw_response_preview": raw_response[:500] if raw_response else "No response",
        "before_administration": create_error_placeholder("LLM parsing failed"),
        "after_administration": create_error_placeholder("LLM parsing failed")
    }

def create_error_placeholder(error_msg: str) -> Dict[str, Dict[str, str]]:
    """Create placeholder structure for failed pathways."""
    placeholder = {
        "description": f"ERROR: {error_msg}",
        "validation_tag": "mechanistically not possible"
    }
    return {
        "sensitive_upregulation": placeholder.copy(),
        "sensitive_downregulation": placeholder.copy(),
        "resistant_upregulation": placeholder.copy(),
        "resistant_downregulation": placeholder.copy()
    }

# Main Pipeline
def run_administration_pipeline(
    input_file: Path,
    base_prompt: str,
) -> str:
    """
    Run the administration combinations pipeline for one trial5 file.
    
    Returns: output_path
    """
    # Extract drug name from filename
    drug_name = extract_drug_name(str(input_file))
    stem = input_file.stem.replace("_final_trial5", "")
    
    print(f"\n{'='*70}")
    print(f"Processing: {drug_name}")
    print(f"Input: {input_file}")
    print(f"{'='*70}")
    
    # Load trial5 output
    trial5_data = load_json(str(input_file))
    
    # Extract pathway list
    pathways = extract_pathway_list(trial5_data)
    print(f"Found {len(pathways)} unique pathways to process")
    
    # Process each pathway
    results = {
        "drug_name": drug_name,
        "total_pathways": len(pathways),
        "llm_model": LLM_MODEL,
        "validation_tag_definitions": {
            "mechanistically accurate and clinically validated": "Supported by clinical trial data and mechanistic understanding",
            "mechanistically accurate only": "Biologically plausible based on mechanism but lacks clinical validation",
            "mechanistically rare": "Possible but uncommon or unusual scenario",
            "mechanistically not possible": "Contradicts known biology or mechanism of action"
        },
        "pathways": {}
    }
    
    for i, pathway in enumerate(pathways):
        if PRINT_PROGRESS:
            print(f"  [{i+1}/{len(pathways)}] Processing: {pathway}...")
        
        # Generate combinations for this pathway
        combinations = generate_pathway_combinations(drug_name, pathway, base_prompt)
        results["pathways"][pathway] = combinations
        
        # Small delay to avoid rate limiting
        sleep(1.0)
    
    # Save output
    output_path = str(Path(OUTPUT_DIR) / f"{stem}_administration_combinations.json")
    save_json(results, output_path)
    
    print(f"\n--- SUMMARY: {drug_name} ---")
    print(f"Pathways processed: {len(pathways)}")
    print(f"Output saved to: {output_path}")
    
    # Count errors and validation tags
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
            # Count validation tags
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
    
    print(f"\n--- VALIDATION TAG DISTRIBUTION ---")
    for tag, count in validation_tag_counts.items():
        print(f"  {tag}: {count}")
    
    return output_path

# Main execution
if __name__ == "__main__":
    # Read base prompt from docx
    print("Loading prompt template...")
    if not Path(PROMPT_DOCX_PATH).exists():
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_DOCX_PATH}")
    
    base_prompt = read_docx(PROMPT_DOCX_PATH)
    print(f"Loaded prompt template ({len(base_prompt)} characters)")
    
    # Check for trial5 files
    if not TRIAL5_FILES:
        raise RuntimeError(
            f"No *_final_trial5.json files found in {INPUT_DIR}.\n"
            "Please run pathway_mapping_trial5.py first to generate input files."
        )
    
    print(f"\nFound {len(TRIAL5_FILES)} trial5 output file(s) to process:")
    for f in TRIAL5_FILES:
        print(f"  - {f.name}")
    
    # Run pipeline for each file
    outputs = []
    for trial5_file in TRIAL5_FILES:
        try:
            output_path = run_administration_pipeline(trial5_file, base_prompt)
            outputs.append(output_path)
        except Exception as e:
            print(f"ERROR processing {trial5_file}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*70}")
    print(f"PIPELINE COMPLETE")
    print(f"{'='*70}")
    print(f"Total files processed: {len(outputs)}")
    print("All outputs saved to:", OUTPUT_DIR)

