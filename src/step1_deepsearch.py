"""
Step 1: Deep Search for batch drug processing.
Queries the Deep Search API and saves markdown reports.
"""

import argparse
import time
from pathlib import Path

import requests

# API Configuration
API_BASE_URL = "http://localhost:8009"


def resolve_output_dir(output_path: str) -> Path:
    """Resolve and create output directory path."""
    output_dir = Path(output_path)
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_drug_list(drug_file_path: str):
    """Load drug names from the text file."""
    try:
        drug_file = Path(drug_file_path)
        if not drug_file.is_absolute():
            drug_file = Path.cwd() / drug_file

        with open(drug_file, "r", encoding="utf-8") as f:
            drugs = [line.strip() for line in f if line.strip()]

        print(f"Loaded {len(drugs)} drugs from {drug_file}")
        return drugs
    except FileNotFoundError:
        print(f"Drug list file not found: {drug_file_path}")
        return None
    except Exception as exc:
        print(f"Failed to read drug list file '{drug_file_path}': {exc}")
        return None


def start_research(drug_name: str):
    """Start research for a drug."""
    print(f"\n{'=' * 80}")
    print(f"Starting research for: {drug_name}")
    print(f"{'=' * 80}")

    drug_name_lower = drug_name.lower()

    try:
        response = requests.post(
            f"{API_BASE_URL}/api/start_research",
            json={"topic": drug_name_lower},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        research_id = data.get("research_id")
        print(f"Research started - ID: {research_id}")
        return research_id
    except Exception as exc:
        print(f"Error starting research: {exc}")
        return None


def check_progress(research_id: str):
    """Check progress of research."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/progress/{research_id}",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"Error checking progress: {exc}")
        return None


def download_report(research_id: str, drug_name: str, output_dir: Path):
    """Download the markdown report."""
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/download/{research_id}",
            timeout=30,
        )
        response.raise_for_status()

        safe_name = "".join(c for c in drug_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_name = safe_name.replace(" ", "_").lower()

        output_path = output_dir / f"{safe_name}.md"
        with open(output_path, "wb") as f:
            f.write(response.content)

        print(f"Saved report to: {output_path}")
        return True
    except Exception as exc:
        print(f"Error downloading report: {exc}")
        return False


def wait_for_completion(research_id: str, max_wait_minutes: int = 30):
    """Wait for research to complete with progress updates."""
    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60
    last_progress = 0

    print(f"Waiting for completion (max {max_wait_minutes} minutes)...")

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_wait_seconds:
            print(f"Timeout after {max_wait_minutes} minutes")
            return False

        progress_data = check_progress(research_id)
        if not progress_data:
            time.sleep(10)
            continue

        status = progress_data.get("status")
        progress = progress_data.get("progress", 0)
        stage = progress_data.get("stage", "Processing...")
        elapsed_time = progress_data.get("elapsed_time", "")

        if progress != last_progress:
            print(f"   Progress: {progress}% - {stage} [{elapsed_time}]")
            last_progress = progress

        if status == "complete":
            print(f"Research complete. Total time: {elapsed_time}")
            return True
        if status == "error":
            error = progress_data.get("error", "Unknown error")
            print(f"Research failed: {error}")
            return False

        time.sleep(10)


def process_drug(drug_name: str, index: int, total: int, output_dir: Path):
    """Process a single drug."""
    print(f"\n\n{'#' * 80}")
    print(f"# Drug {index}/{total}: {drug_name}")
    print(f"{'#' * 80}")

    safe_name = "".join(c for c in drug_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    safe_name = safe_name.replace(" ", "_").lower()
    output_path = output_dir / f"{safe_name}.md"

    if output_path.exists():
        print(f"Skipping {drug_name} - report already exists at {output_path}")
        return True

    research_id = start_research(drug_name)
    if not research_id:
        return False

    success = wait_for_completion(research_id, max_wait_minutes=30)
    if not success:
        return False

    success = download_report(research_id, drug_name, output_dir)
    time.sleep(5)
    return success


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(description="Deep Search Batch Processor for Drug Research")
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="Path to input file containing drug names (one per line)",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output directory path where markdown reports will be saved",
    )
    parser.add_argument(
        "-c",
        "--cancer",
        required=True,
        help="Cancer name context for startup message",
    )
    args = parser.parse_args()

    cancer_name = (args.cancer or "").strip().lower()
    if not cancer_name:
        parser.error("Cancer name cannot be empty. Pass -c/--cancer with a valid value.")

    output_dir = resolve_output_dir(args.output)
    drug_list = load_drug_list(args.input)
    if drug_list is None:
        raise SystemExit(1)
    if len(drug_list) == 0:
        print("No drugs found in input file. Nothing to process.")
        raise SystemExit(0)

    print("\n" + "=" * 80)
    print("DEEP SEARCH BATCH PROCESSOR")
    print("=" * 80)
    print(f"{cancer_name} cancer deepsearch for {len(drug_list)} drugs processing")
    print(f"Total drugs to process: {len(drug_list)}")
    print(f"Output directory: {output_dir.absolute()}")
    print(f"API endpoint: {API_BASE_URL}")
    print("=" * 80 + "\n")

    total = len(drug_list)
    successful = 0
    failed = 0
    skipped = 0
    start_time = time.time()

    for index, drug_name in enumerate(drug_list, 1):
        safe_name = "".join(c for c in drug_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_name = safe_name.replace(" ", "_").lower()
        output_path = output_dir / f"{safe_name}.md"

        if output_path.exists():
            skipped += 1
            print(f"\n[{index}/{total}] Skipping {drug_name} - already exists")
            continue

        success = process_drug(drug_name, index, total, output_dir)
        if success:
            successful += 1
        else:
            failed += 1
            print(f"Failed to process {drug_name}")

    total_time = time.time() - start_time
    minutes = int(total_time // 60)
    seconds = int(total_time % 60)

    print("\n\n" + "=" * 80)
    print("BATCH PROCESSING COMPLETE")
    print("=" * 80)
    print(f"Total drugs: {total}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Skipped (already exist): {skipped}")
    print(f"Total time: {minutes}m {seconds}s")
    print(f"Reports saved to: {output_dir.absolute()}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
