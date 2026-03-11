# [markdown]
# Step 6: Final JSON Merging
# ------------------------------------------------------------------------------------------------
# This script merges data from multiple sources:
#   - Ribociclib_structured_output.json (base structure from Step5)
#   - ribociclib_step2_trial2_out_administration_combinations.json (before/after administration data)
#   - ribociclib_step2_trial2_out_final_trial5.json (regulation and baseline effect for pathway filtering)
#
# Output:
#   - <drug>_final_merged.json with:
#     - pathway classification keys (sensitive/resistant × upregulation/downregulation)
#     - before_administration and after_administration under each pathway annotation

# Imports
import json
from pathlib import Path
from typing import Any, Dict, List

# Config
# --- Input/Output Paths ---
STRUCTURED_OUTPUT_PATH = r"D:\GS\pathway-enrichment-pipeline\my_project\mapped_pathway_json\Ribociclib_structured_output.json"
ADMINISTRATION_JSON_PATH = r"D:\GS\pathway-enrichment-pipeline\my_project\mapped_pathway_json\ribociclib_step2_trial2_out_administration_combinations.json"
TRIAL5_JSON_PATH = r"D:\GS\pathway-enrichment-pipeline\my_project\mapped_pathway_json\ribociclib_step2_trial2_out_final_trial5.json"
OUTPUT_DIR = r"D:\GS\pathway-enrichment-pipeline\my_project\mapped_pathway_json"

# Ensure output directory exists
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

# Helpers
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# Main Functions
def extract_pathway_classifications(trial5_data: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Extract pathway classifications based on Regulation and Baseline effect from trial5 data.
    
    Args:
        trial5_data: Data from ribociclib_step2_trial2_out_final_trial5.json
    
    Returns:
        Dictionary with 4 lists:
        - pathways_sensitive_upregulated
        - pathways_sensitive_downregulated
        - pathways_resistant_upregulated
        - pathways_resistant_downregulated
    """
    classifications = {
        "pathways_sensitive_upregulated": [],
        "pathways_sensitive_downregulated": [],
        "pathways_resistant_upregulated": [],
        "pathways_resistant_downregulated": [],
    }
    
    for row_key, row_data in trial5_data.items():
        if not isinstance(row_data, dict):
            continue
        
        pathway_name = row_data.get("Mapped MSigDB Pathway Name")
        regulation = row_data.get("Regulation", "").lower()
        baseline_effect = row_data.get("Baseline effect", "").lower()
        
        if not pathway_name:
            continue
        
        # Classify based on regulation and baseline effect
        if baseline_effect == "sensitive" and regulation == "upregulation":
            if pathway_name not in classifications["pathways_sensitive_upregulated"]:
                classifications["pathways_sensitive_upregulated"].append(pathway_name)
        elif baseline_effect == "sensitive" and regulation == "downregulation":
            if pathway_name not in classifications["pathways_sensitive_downregulated"]:
                classifications["pathways_sensitive_downregulated"].append(pathway_name)
        elif baseline_effect == "resistant" and regulation == "upregulation":
            if pathway_name not in classifications["pathways_resistant_upregulated"]:
                classifications["pathways_resistant_upregulated"].append(pathway_name)
        elif baseline_effect == "resistant" and regulation == "downregulation":
            if pathway_name not in classifications["pathways_resistant_downregulated"]:
                classifications["pathways_resistant_downregulated"].append(pathway_name)
    
    return classifications


def extract_administration_data(administration_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Extract before_administration and after_administration data for each pathway.
    
    Args:
        administration_data: Data from ribociclib_step2_trial2_out_administration_combinations.json
    
    Returns:
        Dictionary mapping pathway_name to {before_administration, after_administration}
    """
    result = {}
    
    pathways = administration_data.get("pathways", {})
    
    for pathway_name, pathway_data in pathways.items():
        if not isinstance(pathway_data, dict):
            continue
        
        result[pathway_name] = {
            "before_administration": pathway_data.get("before_administration", {}),
            "after_administration": pathway_data.get("after_administration", {}),
        }
    
    return result


def merge_final_json(
    structured_output: Dict[str, Any],
    classifications: Dict[str, List[str]],
    administration_by_pathway: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge all data into final JSON structure.
    
    Inserts:
    - pathways_sensitive_upregulated, pathways_sensitive_downregulated,
      pathways_resistant_upregulated, pathways_resistant_downregulated
      between pathway_sets and pathway_sets_annotations
    - before_administration and after_administration under each pathway in pathway_sets_annotations
    
    Args:
        structured_output: Base data from Ribociclib_structured_output.json
        classifications: Pathway classification lists
        administration_by_pathway: Before/after administration data by pathway
    
    Returns:
        Merged final JSON structure
    """
    # Create new ordered dictionary to maintain key order
    result = {}
    
    for key, value in structured_output.items():
        result[key] = value
        
        # Insert classification keys after pathway_sets
        if key == "pathway_sets":
            result["pathways_sensitive_upregulated"] = classifications["pathways_sensitive_upregulated"]
            result["pathways_sensitive_downregulated"] = classifications["pathways_sensitive_downregulated"]
            result["pathways_resistant_upregulated"] = classifications["pathways_resistant_upregulated"]
            result["pathways_resistant_downregulated"] = classifications["pathways_resistant_downregulated"]
        
        # Enhance pathway_sets_annotations with administration data
        if key == "pathway_sets_annotations":
            enhanced_annotations = {}
            for pathway_name, annotation in value.items():
                enhanced_annotation = dict(annotation)  # Copy original annotation
                
                # Add administration data if available
                if pathway_name in administration_by_pathway:
                    admin_data = administration_by_pathway[pathway_name]
                    enhanced_annotation["before_administration"] = admin_data["before_administration"]
                    enhanced_annotation["after_administration"] = admin_data["after_administration"]
                else:
                    # Add empty placeholders if no administration data
                    enhanced_annotation["before_administration"] = {}
                    enhanced_annotation["after_administration"] = {}
                
                enhanced_annotations[pathway_name] = enhanced_annotation
            
            result["pathway_sets_annotations"] = enhanced_annotations
    
    return result


# Main Pipeline
def run_final_merge_pipeline(
    structured_output_path: str,
    administration_path: str,
    trial5_path: str,
) -> str:
    """
    Run the final JSON merging pipeline.
    
    Args:
        structured_output_path: Path to Ribociclib_structured_output.json
        administration_path: Path to administration combinations JSON
        trial5_path: Path to trial5 JSON with regulation/baseline effect
    
    Returns:
        Output file path
    """
    print(f"\n{'='*70}")
    print("Step 6: Final JSON Merging")
    print(f"{'='*70}")
    
    # Load input files
    print(f"\nLoading structured output: {structured_output_path}")
    if not Path(structured_output_path).exists():
        raise FileNotFoundError(f"Structured output not found: {structured_output_path}")
    structured_output = load_json(structured_output_path)
    print(f"  Loaded with {len(structured_output.get('pathway_sets', []))} pathways")
    
    print(f"\nLoading administration data: {administration_path}")
    if not Path(administration_path).exists():
        raise FileNotFoundError(f"Administration JSON not found: {administration_path}")
    administration_data = load_json(administration_path)
    print(f"  Loaded with {administration_data.get('total_pathways', 0)} pathways")
    
    print(f"\nLoading trial5 data: {trial5_path}")
    if not Path(trial5_path).exists():
        raise FileNotFoundError(f"Trial5 JSON not found: {trial5_path}")
    trial5_data = load_json(trial5_path)
    print(f"  Loaded with {len(trial5_data)} rows")
    
    # Extract classifications from trial5
    print(f"\n--- Extracting Pathway Classifications ---")
    classifications = extract_pathway_classifications(trial5_data)
    print(f"  Sensitive + Upregulated: {len(classifications['pathways_sensitive_upregulated'])} pathways")
    for p in classifications['pathways_sensitive_upregulated']:
        print(f"    - {p}")
    print(f"  Sensitive + Downregulated: {len(classifications['pathways_sensitive_downregulated'])} pathways")
    for p in classifications['pathways_sensitive_downregulated']:
        print(f"    - {p}")
    print(f"  Resistant + Upregulated: {len(classifications['pathways_resistant_upregulated'])} pathways")
    for p in classifications['pathways_resistant_upregulated']:
        print(f"    - {p}")
    print(f"  Resistant + Downregulated: {len(classifications['pathways_resistant_downregulated'])} pathways")
    for p in classifications['pathways_resistant_downregulated']:
        print(f"    - {p}")
    
    # Extract administration data
    print(f"\n--- Extracting Administration Data ---")
    administration_by_pathway = extract_administration_data(administration_data)
    print(f"  Found administration data for {len(administration_by_pathway)} pathways")
    
    # Merge everything
    print(f"\n--- Merging Final JSON ---")
    drug_name = structured_output.get("drug_name", "Unknown")
    final_output = merge_final_json(structured_output, classifications, administration_by_pathway)
    
    # Save output
    output_filename = f"{drug_name}_final_merged.json"
    output_path = str(Path(OUTPUT_DIR) / output_filename)
    save_json(final_output, output_path)
    
    print(f"\n--- SUMMARY ---")
    print(f"Drug: {drug_name}")
    print(f"Output saved to: {output_path}")
    print(f"Total pathways in pathway_sets: {len(final_output.get('pathway_sets', []))}")
    print(f"Pathways with administration data: {len([p for p in final_output.get('pathway_sets_annotations', {}).values() if p.get('before_administration')])}")
    
    return output_path


# Main execution
if __name__ == "__main__":
    print("=" * 70)
    print("Step 6: Final JSON Merge Pipeline")
    print("=" * 70)
    print(f"Structured output: {STRUCTURED_OUTPUT_PATH}")
    print(f"Administration JSON: {ADMINISTRATION_JSON_PATH}")
    print(f"Trial5 JSON: {TRIAL5_JSON_PATH}")
    
    try:
        output_path = run_final_merge_pipeline(
            STRUCTURED_OUTPUT_PATH,
            ADMINISTRATION_JSON_PATH,
            TRIAL5_JSON_PATH
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


