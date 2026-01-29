#%% [markdown]
# Step 5: JSON Creation from Drug Report
# ------------------------------------------------------------------------------------------------
# This script reads a prompt template (docx), a drug report (markdown), and a
# pathway list (JSON) and feeds them to GPT-5.1 to generate structured JSON output.
#
# Input:
#   - prompt3a.docx (prompt template)
#   - Ribociclib_Report.md (drug report)
#   - mapped_pathway_json/*_final_trial5.json (pathway list with "Mapped MSigDB Pathway Name")
#
# Output:
#   - <drug>_structured_output.json

#%% Imports
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from openai import OpenAI
from time import sleep

# For reading .docx files
try:
    from docx import Document
except ImportError:
    raise ImportError("python-docx is required. Install with: pip install python-docx")

#%% Config
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

# LLM Model Configuration - Using GPT-5.1 as specified
LLM_MODEL = "gpt-5.1"
LLM_TEMPERATURE = 0.2  # Low temperature for consistent, factual responses
LLM_MAX_TOKENS = 16000  # Large token limit for comprehensive JSON output

# --- Input/Output Paths ---
PROMPT_DOCX_PATH = r"D:\GS\pathway-enrichment-pipeline\Step3a\input\prompt3a.docx"
DRUG_REPORT_PATH = r"D:\GS\pathway-enrichment-pipeline\my_project\Ribociclib_Report.md"
PATHWAY_JSON_PATH = r"D:\GS\pathway-enrichment-pipeline\my_project\mapped_pathway_json\ribociclib_step2_trial2_out_final_trial5.json"
OUTPUT_DIR = r"mapped_pathway_json"

# Ensure output directory exists
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

#%% Helpers
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
    # Also read tables if present
    for table in doc.tables:
        for row in table.rows:
            row_text = [cell.text for cell in row.cells]
            full_text.append(" | ".join(row_text))
    return "\n".join(full_text)

def read_markdown(path: str) -> str:
    """Read text content from a markdown file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def extract_drug_name_from_report(report_content: str) -> str:
    """Extract drug name from the report content."""
    # Try to find drug name in the first few lines
    lines = report_content.split("\n")[:20]
    for line in lines:
        # Look for patterns like "Ribociclib is" or "# Drug Summary" followed by drug name
        match = re.search(r'^(?:#\s*)?(?:Drug\s+Summary\s*)?(\w+)\s+is\s+', line, re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
    # Fallback - try to find capitalized drug names
    match = re.search(r'\b(Ribociclib|Palbociclib|Abemaciclib|Pembrolizumab|Trastuzumab|Dostarlimab|Tucatinib)\b', 
                      report_content[:2000], re.IGNORECASE)
    if match:
        return match.group(1).capitalize()
    return "Unknown"

def extract_pathway_names(pathway_json_path: str) -> List[str]:
    """
    Extract only the 'Mapped MSigDB Pathway Name' values from the pathway JSON file.
    
    Args:
        pathway_json_path: Path to the mapped pathway JSON file
    
    Returns:
        List of pathway names
    """
    pathway_data = load_json(pathway_json_path)
    pathway_names = []
    
    for row_key, row_data in pathway_data.items():
        if isinstance(row_data, dict) and "Mapped MSigDB Pathway Name" in row_data:
            pathway_name = row_data["Mapped MSigDB Pathway Name"]
            if pathway_name and pathway_name not in pathway_names:
                pathway_names.append(pathway_name)
    
    return pathway_names

#%% LLM Helper
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

            # Handle max_tokens parameter issues for newer models
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

def parse_json_response(response: str) -> Dict[str, Any]:
    """Parse JSON from LLM response, handling common issues."""
    response = response.strip()
    
    # Remove markdown code blocks if present
    if response.startswith("```"):
        response = re.sub(r"^```(?:json)?\s*\n?", "", response)
        response = re.sub(r"\n?```\s*$", "", response)
    
    # Try to find JSON object in response if it has extra text
    json_match = re.search(r'\{[\s\S]*\}', response)
    if json_match:
        response = json_match.group(0)
    
    # Fix common JSON issues - trailing commas before } or ]
    response = re.sub(r',(\s*[}\]])', r'\1', response)
    
    return json.loads(response)

#%% Main Generation Function
def generate_structured_json(
    prompt_template: str,
    drug_report: str,
    drug_name: str,
    pathway_list: List[str],
) -> Dict[str, Any]:
    """
    Generate structured JSON output from prompt template and drug report.
    
    Args:
        prompt_template: Content from prompt3a.docx
        drug_report: Content from drug report markdown
        drug_name: Name of the drug
        pathway_list: List of mapped MSigDB pathway names
    
    Returns:
        Structured JSON output
    """
    
    # Format pathway list as a numbered list
    pathway_list_str = "\n".join([f"  {i+1}. {name}" for i, name in enumerate(pathway_list)])
    
    user_prompt = f"""{prompt_template}

---

DRUG NAME: {drug_name}

<PATHWAY_LIST>
{pathway_list_str}
</PATHWAY_LIST>

DRUG REPORT:
{drug_report}

---

Based on the prompt instructions above, the pathway list, and the drug report provided, generate a comprehensive structured JSON output. 
The JSON should capture all relevant pathway-drug interactions, mechanisms, clinical evidence, and classifications from the report.
Use the pathway names from <PATHWAY_LIST> as the canonical pathway identifiers.

Return ONLY valid JSON (no markdown code blocks, no explanation outside JSON).
"""

    messages = [
        {
            "role": "system", 
            "content": """You are an expert in cancer biology, pharmacology, and pathway analysis. 
Your task is to extract and structure information from drug reports into comprehensive JSON format.
Be precise, scientifically accurate, and ensure the JSON is valid and well-structured.
Include all relevant pathways, their regulations, effects on drug response, rationale, and classifications.
Return ONLY valid JSON without any markdown formatting."""
        },
        {"role": "user", "content": user_prompt}
    ]
    
    print(f"Sending request to {LLM_MODEL}...")
    print(f"Prompt length: {len(user_prompt)} characters")
    
    try:
        response = call_openai_with_retry(messages)
        print(f"Response received: {len(response)} characters")
        
        result = parse_json_response(response)
        print("JSON parsed successfully")
        return result
        
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse LLM response as JSON: {e}")
        print(f"Response preview: {response[:500] if response else 'No response'}...")
        return {
            "drug_name": drug_name,
            "error": f"JSON parsing failed: {str(e)}",
            "raw_response_preview": response[:2000] if response else "No response"
        }
    except Exception as e:
        print(f"ERROR: LLM call failed: {e}")
        return {
            "drug_name": drug_name,
            "error": str(e)
        }

#%% Main Pipeline
def run_json_creation_pipeline(
    prompt_path: str,
    report_path: str,
    pathway_json_path: str,
) -> str:
    """
    Run the JSON creation pipeline.
    
    Args:
        prompt_path: Path to prompt3a.docx
        report_path: Path to drug report markdown
        pathway_json_path: Path to mapped pathway JSON file
    
    Returns:
        Output file path
    """
    print(f"\n{'='*70}")
    print("Step 5: JSON Creation from Drug Report")
    print(f"{'='*70}")
    
    # Read prompt template
    print(f"\nReading prompt template: {prompt_path}")
    if not Path(prompt_path).exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    prompt_template = read_docx(prompt_path)
    print(f"Prompt template loaded: {len(prompt_template)} characters")
    
    # Read drug report
    print(f"\nReading drug report: {report_path}")
    if not Path(report_path).exists():
        raise FileNotFoundError(f"Drug report not found: {report_path}")
    drug_report = read_markdown(report_path)
    print(f"Drug report loaded: {len(drug_report)} characters")
    
    # Load pathway list
    print(f"\nLoading pathway list: {pathway_json_path}")
    if not Path(pathway_json_path).exists():
        raise FileNotFoundError(f"Pathway JSON not found: {pathway_json_path}")
    pathway_list = extract_pathway_names(pathway_json_path)
    print(f"Pathway list loaded: {len(pathway_list)} pathways")
    for i, name in enumerate(pathway_list, 1):
        print(f"  {i}. {name}")
    
    # Extract drug name
    drug_name = extract_drug_name_from_report(drug_report)
    print(f"\nDrug name detected: {drug_name}")
    
    # Generate structured JSON
    print(f"\n--- Generating Structured JSON for {drug_name} ---")
    result = generate_structured_json(prompt_template, drug_report, drug_name, pathway_list)
    
    # Output directly without metadata wrapper
    output = result
    
    # Save output
    output_filename = f"{drug_name}_structured_output.json"
    output_path = str(Path(OUTPUT_DIR) / output_filename)
    save_json(output, output_path)
    
    print(f"\n--- SUMMARY ---")
    print(f"Drug: {drug_name}")
    print(f"Output saved to: {output_path}")
    
    if "error" in result:
        print(f"WARNING: Generation encountered errors")
    else:
        # Count top-level keys in result
        if isinstance(result, dict):
            print(f"Top-level keys in output: {list(result.keys())}")
    
    return output_path

#%% Main execution
if __name__ == "__main__":
    print("=" * 70)
    print("Step 5: JSON Creation Pipeline")
    print("=" * 70)
    print(f"LLM Model: {LLM_MODEL}")
    print(f"Prompt file: {PROMPT_DOCX_PATH}")
    print(f"Report file: {DRUG_REPORT_PATH}")
    print(f"Pathway JSON: {PATHWAY_JSON_PATH}")
    
    try:
        output_path = run_json_creation_pipeline(
            PROMPT_DOCX_PATH,
            DRUG_REPORT_PATH,
            PATHWAY_JSON_PATH
        )
        
        print(f"\n{'='*70}")
        print("PIPELINE COMPLETE")
        print(f"{'='*70}")
        print(f"Output: {output_path}")
        
    except FileNotFoundError as e:
        print(f"\nERROR: File not found - {e}")
    except Exception as e:
        print(f"\nERROR: Pipeline failed - {e}")
        import traceback
        traceback.print_exc()

# %%
