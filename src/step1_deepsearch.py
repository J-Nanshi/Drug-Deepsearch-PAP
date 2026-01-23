"""
Step 1: Deep Search for 30 Drugs
Queries the Deep Search API and saves markdown reports
"""

import requests
import time
import os
import argparse
from pathlib import Path

# API Configuration
API_BASE_URL = "http://localhost:8009"

# Create output directory
# Project root = parent of this src file
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output" / "step1_deepsearch"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def load_drug_list(drug_file_path):
    """Load drug names from the text file."""
    try:
        drug_file = Path(drug_file_path)
        # If relative path, make it relative to current working directory
        if not drug_file.is_absolute():
            drug_file = Path.cwd() / drug_file
        
        with open(drug_file, 'r') as f:
            drugs = [line.strip() for line in f if line.strip()]
        print(f"✅ Loaded {len(drugs)} drugs from {drug_file}")
        return drugs
    except FileNotFoundError:
        print(f"❌ Drug list file not found: {drug_file_path}")
        print("Using fallback drug list...")
        # Fallback list if file not found
        return [
            "Abemaciclib", "Alpelisib", "Anastrozole", "Atezolizumab", "Bevacizumab",
            "Capivasertib", "Dostarlimab", "Elacestrant", "Entrectinib", "Everolimus",
            "Exemestane", "Fulvestrant", "Lapatinib", "Larotrectinib", "Letrozole",
            "Margetuximab", "Neratinib", "Olaparib", "Paclitaxel", "Palbociclib",
            "Pembrolizumab", "Pertuzumab", "Ribociclib", "Sacituzumab govitecan",
            "Selpercatinib", "Talazoparib", "Tamoxifen", "Toremifene", "Trastuzumab",
            "Trastuzumab deruxtecan", "Trastuzumab emtansine", "Tucatinib"
        ]

def start_research(drug_name):
    """Start research for a drug."""
    print(f"\n{'='*80}")
    print(f"🔬 Starting research for: {drug_name}")
    print(f"{'='*80}")
    
    # Convert drug name to lowercase for API request
    drug_name_lower = drug_name.lower()
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/start_research",
            json={"topic": drug_name_lower},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        research_id = data.get("research_id")
        print(f"✅ Research started - ID: {research_id}")
        return research_id
    except Exception as e:
        print(f"❌ Error starting research: {e}")
        return None

def check_progress(research_id):
    """Check progress of research."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/progress/{research_id}",
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"❌ Error checking progress: {e}")
        return None

def download_report(research_id, drug_name):
    """Download the markdown report."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/download/{research_id}",
            timeout=30
        )
        response.raise_for_status()
        
        # Sanitize filename and convert to lowercase
        safe_name = "".join(c for c in drug_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_name = safe_name.replace(' ', '_').lower()
        
        # Save file
        output_path = OUTPUT_DIR / f"{safe_name}.md"
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        print(f"💾 Saved report to: {output_path}")
        return True
    except Exception as e:
        print(f"❌ Error downloading report: {e}")
        return False

def wait_for_completion(research_id, drug_name, max_wait_minutes=30):
    """Wait for research to complete with progress updates."""
    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60
    last_progress = 0
    
    print(f"⏳ Waiting for completion (max {max_wait_minutes} minutes)...")
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > max_wait_seconds:
            print(f"⚠️ Timeout after {max_wait_minutes} minutes")
            return False
        
        progress_data = check_progress(research_id)
        if not progress_data:
            time.sleep(10)
            continue
        
        status = progress_data.get("status")
        progress = progress_data.get("progress", 0)
        stage = progress_data.get("stage", "Processing...")
        elapsed_time = progress_data.get("elapsed_time", "")
        
        # Print progress if changed
        if progress != last_progress:
            print(f"   📊 Progress: {progress}% - {stage} [{elapsed_time}]")
            last_progress = progress
        
        if status == "complete":
            print(f"✅ Research complete! Total time: {elapsed_time}")
            return True
        elif status == "error":
            error = progress_data.get("error", "Unknown error")
            print(f"❌ Research failed: {error}")
            return False
        
        # Wait before next check
        time.sleep(10)

def process_drug(drug_name, index, total):
    """Process a single drug."""
    print(f"\n\n{'#'*80}")
    print(f"# Drug {index}/{total}: {drug_name}")
    print(f"{'#'*80}")
    
    # Check if already exists (use lowercase filename)
    safe_name = "".join(c for c in drug_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
    safe_name = safe_name.replace(' ', '_').lower()
    output_path = OUTPUT_DIR / f"{safe_name}.md"
    
    if output_path.exists():
        print(f"⏭️ Skipping {drug_name} - report already exists at {output_path}")
        return True
    
    # Start research
    research_id = start_research(drug_name)
    if not research_id:
        return False
    
    # Wait for completion
    success = wait_for_completion(research_id, drug_name, max_wait_minutes=30)
    if not success:
        return False
    
    # Download report
    success = download_report(research_id, drug_name)
    
    # Small delay between drugs
    time.sleep(5)
    
    return success

def main():
    """Main execution function."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Deep Search Batch Processor for Drug Research')
    parser.add_argument('-i', '--input', 
                        required=True,
                        help='Path to input file containing drug names (one per line)')
    args = parser.parse_args()
    
    # Load drug list from specified file
    DRUG_LIST = load_drug_list(args.input)
    
    print("\n" + "="*80)
    print("🚀 DEEP SEARCH BATCH PROCESSOR")
    print("="*80)
    print(f"Total drugs to process: {len(DRUG_LIST)}")
    print(f"Output directory: {OUTPUT_DIR.absolute()}")
    print(f"API endpoint: {API_BASE_URL}")
    print("="*80 + "\n")
    
    # Statistics
    total = len(DRUG_LIST)
    successful = 0
    failed = 0
    skipped = 0
    
    start_time = time.time()
    
    # Process each drug
    for index, drug_name in enumerate(DRUG_LIST, 1):
        # Check if exists first (use lowercase filename)
        safe_name = "".join(c for c in drug_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_name = safe_name.replace(' ', '_').lower()
        output_path = OUTPUT_DIR / f"{safe_name}.md"
        
        if output_path.exists():
            skipped += 1
            print(f"\n[{index}/{total}] ⏭️ Skipping {drug_name} - already exists")
            continue
        
        success = process_drug(drug_name, index, total)
        
        if success:
            successful += 1
        else:
            failed += 1
            print(f"⚠️ Failed to process {drug_name}")
    
    # Final summary
    total_time = time.time() - start_time
    minutes = int(total_time // 60)
    seconds = int(total_time % 60)
    
    print("\n\n" + "="*80)
    print("📊 BATCH PROCESSING COMPLETE")
    print("="*80)
    print(f"Total drugs: {total}")
    print(f"✅ Successful: {successful}")
    print(f"❌ Failed: {failed}")
    print(f"⏭️ Skipped (already exist): {skipped}")
    print(f"⏱️ Total time: {minutes}m {seconds}s")
    print(f"📁 Reports saved to: {OUTPUT_DIR.absolute()}")
    print("="*80 + "\n")

if __name__ == "__main__":
    main()
