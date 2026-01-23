"""
Setup NLTK data for the Deep Search application
Run this script to download required NLTK data
"""

import nltk
import os
from pathlib import Path

def setup_nltk_data():
    """Download required NLTK data packages."""
    print("Setting up NLTK data...")
    
    # Choose a writable NLTK data directory (project-local by default)
    project_root = Path(__file__).resolve().parent.parent
    default_data_dir = project_root / "nltk_data"
    data_dir = Path(os.environ.get("NLTK_DATA", str(default_data_dir)))
    data_dir.mkdir(parents=True, exist_ok=True)
    # Prepend to search path so verification finds the local data
    if str(data_dir) not in nltk.data.path:
        nltk.data.path.insert(0, str(data_dir))

    # Ensure 'punkt' and (optionally) 'punkt_tab' are installed.
    # NLTK 3.9+ splits resources; 'punkt_tab' lives under the 'punkt' folder
    # and nltk.data.find can raise OSError on Windows for that subfolder.
    # Safer approach: try to download explicitly and ignore if already present.

    for package in ("punkt", "punkt_tab"):
        try:
            print(f"📥 Ensuring {package} is installed (re-download is safe)...")
            nltk.download(package, download_dir=str(data_dir), quiet=False)
            print(f"✅ {package} ensured")
        except Exception as e:
            print(f"⚠️ Could not download {package}: {e}")
    
    # Verify installation
    print("\nVerifying NLTK data installation...")
    try:
        from nltk.tokenize import sent_tokenize
        test_text = "This is a test. It has two sentences."
        sentences = sent_tokenize(test_text)
        print(f"✅ NLTK tokenization working: {len(sentences)} sentences detected")
    except Exception as e:
        print(f"❌ NLTK tokenization test failed: {e}")
    
    print("\nNLTK data paths:")
    for path in nltk.data.path:
        print(f"  - {path}")

if __name__ == "__main__":
    setup_nltk_data()