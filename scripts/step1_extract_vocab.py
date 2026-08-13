"""Step 1: Extract German vocabulary from raw PDFs in data/raw/ to data/fixtures/corpus/vocab_levels.json.

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

import json
from pathlib import Path

from src.lexicon.extractor import WordlistPdfExtractor


def main() -> None:
    print("\n=======================================================")
    print("  Step 1: Extracting Vocabulary from PDFs")
    print("=======================================================\n")

    raw_dir = Path("data/raw")
    output_path = Path("data/fixtures/corpus/vocab_levels.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists() or not list(raw_dir.glob("*.pdf")):
        print(f"Warning: No PDF files found in {raw_dir}.")
        print("Please place your Goethe/telc PDF files in data/raw/")
        return

    print(f"Reading PDFs from: {raw_dir.resolve()} ...")
    extractor = WordlistPdfExtractor()
    extracted_vocab = extractor.extract_from_directory(raw_dir)

    print("\nExtraction Summary:")
    for level, words in sorted(extracted_vocab.items()):
        print(f"  - Level {level}: {len(words)} lemmas extracted")

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(extracted_vocab, f, indent=2, ensure_ascii=False)

    print(f"\nSuccessfully saved vocab dictionary to: {output_path.resolve()}\n")


if __name__ == "__main__":
    main()
