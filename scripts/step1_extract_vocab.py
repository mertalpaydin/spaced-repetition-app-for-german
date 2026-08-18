"""Step 1: Extract German vocabulary from raw PDFs in data/raw/ to vocab_levels.json.

A1, A2 and B1 come from the Goethe/"Einfach" wordlist PDFs in data/raw/ (see
WordlistPdfExtractor). B2 has no official Goethe wordlist to scrape -- see
WordlistPdfExtractor.extract_all's docstring for why this script no longer
scrapes one either -- so it is instead derived from a public, licence-
documented word-frequency corpus: the next FrequencyBander.DEFAULT_B2_BAND_SIZE
lemmas by frequency rank that are not already A1/A2/B1 (or a function word or
proper noun). See data/fixtures/corpus/frequency/PROVENANCE.md for exactly
what that corpus is, where it came from, and its licence.

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

import json
from pathlib import Path

from src.contracts import CEFR
from src.lexicon.extractor import WordlistPdfExtractor
from src.lexicon.frequency import FrequencyBander
from src.lexicon.vocabulary import VocabularyStore

FREQUENCY_RANKED_WORDS_PATH = Path("data/fixtures/corpus/frequency/de_opensubtitles2018_top50k.txt")
FREQUENCY_DICTIONARY_FILTER_PATH = Path("data/fixtures/corpus/frequency/de_dictionary_filter.txt")


def _add_frequency_derived_b2(vocab: dict[str, CEFR]) -> dict[str, CEFR]:
    """Extend ``vocab`` (A1/A2/B1, list-derived) with a frequency-derived B2
    band, in place, returning it for convenience. A no-op, with a printed
    warning, if the vendored frequency fixtures are missing -- this is a
    stated gap, not a silent one; see PROVENANCE.md and the task report for
    why no frequency data is invented when it cannot be obtained."""
    if not FREQUENCY_RANKED_WORDS_PATH.exists() or not FREQUENCY_DICTIONARY_FILTER_PATH.exists():
        print(
            "Warning: frequency fixtures not found under "
            f"{FREQUENCY_RANKED_WORDS_PATH.parent}; B2 will have no "
            "frequency-derived entries. See PROVENANCE.md in that directory."
        )
        return vocab

    ranked_words = FrequencyBander.load_ranked_words(FREQUENCY_RANKED_WORDS_PATH)
    dictionary_filter = FrequencyBander.load_dictionary_filter(FREQUENCY_DICTIONARY_FILTER_PATH)
    known_lemmas: set[str] = (
        set(vocab.keys()) | VocabularyStore.FUNCTION_WORDS | VocabularyStore.PROPER_NOUNS
    )
    b2_vocab = FrequencyBander.derive_b2_vocab(ranked_words, dictionary_filter, known_lemmas)

    for lemma, level in b2_vocab.items():
        vocab.setdefault(lemma, level)
    return vocab


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
    extractor = WordlistPdfExtractor(raw_dir=raw_dir)
    extracted_vocab = extractor.extract_all()

    print(f"\nExtracted {len(extracted_vocab)} list-derived lemmas (A1/A2/B1).")
    print("Deriving B2 from frequency rank (see PROVENANCE.md) ...")
    extracted_vocab = _add_frequency_derived_b2(extracted_vocab)

    print("\nExtraction Summary:")
    counts: dict[str, int] = {}
    for _word, level in extracted_vocab.items():
        lvl_str = str(level)
        counts[lvl_str] = counts.get(lvl_str, 0) + 1
    for lvl, count in sorted(counts.items()):
        print(f"  - Level {lvl}: {count} lemmas")

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(extracted_vocab, f, indent=2, ensure_ascii=False)

    print(f"\nSuccessfully saved vocab dictionary to: {output_path.resolve()}\n")


if __name__ == "__main__":
    main()
