"""Extractor for parsing CEFR vocabulary wordlists from Goethe and telc PDFs."""

import re
from pathlib import Path

from pypdf import PdfReader

from src.contracts import CEFR


class WordlistPdfExtractor:
    """Extracts German lemmas and assigns CEFR levels from raw PDF wordlists."""

    # Common German stop/function words and noise to filter from dictionary extraction
    NOISE_PATTERNS = [
        r"^\d+$",
        r"^www\.",
        r"^telc",
        r"^Goethe",
        r"^Seite \d+",
        r"^VS_\d+",
        r"^Wortliste",
        r"^Deutsch für",
    ]

    def __init__(self, raw_dir: Path | str | None = None) -> None:
        if raw_dir is None:
            raw_dir = Path(__file__).parent.parent.parent / "data" / "raw"
        self.raw_dir = Path(raw_dir)

    def extract_all(self) -> dict[str, CEFR]:
        """Extract vocabulary mapping {lemma: lowest_cefr_level} from the A1,
        A2 and B1 PDFs (Goethe's official wordlists, current only through
        B1, plus the matching "Einfach" course-book supplements).

        Deliberately extracts no B2. This method used to also scrape
        "Einfach_besser_400/500_Wortschatzliste_Englisch.pdf" for B2, on the
        reasoning that *some* B2 signal beats none. Two faults made that
        worse than none: (1) there is no official Goethe B2 wordlist to
        scrape in the first place, so the 808 lemmas it produced were never
        more than an unofficial course glossary's contents, not a
        representative B2 vocabulary; (2) that glossary's PDF is bilingual
        (its own filename says "Englisch"), and this extractor's
        token-grabbing regex, unlike its behaviour on the other bilingual
        source PDFs, picked up English translation-column words alongside
        the German headwords for this one -- "accompany", "administer",
        "advertisement" and dozens more English words were tagged as German
        B2 vocabulary. Both faults are specific to this one source and this
        one level; A1, A2 and B1 are unaffected (checked directly against
        the extracted output) and stay exactly as scraped.
        See ``src.lexicon.frequency.FrequencyBander.derive_b2_vocab`` and
        ``scripts/step1_extract_vocab.py`` for B2's replacement: a
        frequency-derived band built from a public, licence-documented
        corpus (``data/fixtures/corpus/frequency/PROVENANCE.md``) instead of
        this scrape.
        """
        vocab: dict[str, CEFR] = {}

        level_priority: dict[CEFR, int] = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}

        # 1. Parse A1 wordlists
        a1_files = [
            self.raw_dir / "Goethe-Zertifikat_A1_Wortliste.pdf",
            self.raw_dir / "Einfach_los_Wortschatzliste_A1.pdf",
        ]
        for f in a1_files:
            if f.exists():
                for word in self._extract_words_from_pdf(f):
                    vocab[word] = "A1"

        # 2. Parse A2 wordlists
        a2_files = [
            self.raw_dir / "Goethe-Zertifikat_A2_Wortliste.pdf",
            self.raw_dir / "Einfach_gut_A2.1_Wortschatzliste_Englisch.pdf",
            self.raw_dir / "Einfach_gut_A2.2_Wortschatzliste_Englisch.pdf",
        ]
        for f in a2_files:
            if f.exists():
                for word in self._extract_words_from_pdf(f):
                    if word not in vocab or level_priority["A2"] < level_priority[vocab[word]]:
                        if word not in vocab:
                            vocab[word] = "A2"

        # 3. Parse B1 wordlists
        b1_files = [
            self.raw_dir / "Goethe-Zertifikat_B1_Wortliste.pdf",
            self.raw_dir / "Einfach_besser_B1_Wortschatzliste_Englisch.pdf",
        ]
        for f in b1_files:
            if f.exists():
                for word in self._extract_words_from_pdf(f):
                    if word not in vocab:
                        vocab[word] = "B1"

        # No step 4: see the docstring above for why B2 is not scraped here.

        return vocab

    def _extract_words_from_pdf(self, pdf_path: Path) -> set[str]:
        """Extract clean German words/lemmas from a PDF document."""
        words: set[str] = set()
        reader = PdfReader(str(pdf_path))

        for page in reader.pages:
            text = page.extract_text()
            if not text:
                continue

            for line in text.splitlines():
                line = line.strip()
                if not line or any(
                    re.search(pat, line, re.IGNORECASE) for pat in self.NOISE_PATTERNS
                ):
                    continue

                # Match German words (nouns with/without articles, verbs, adjectives)
                tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]{2,}\b", line)
                for token in tokens:
                    # Ignore short grammatical particles, keep content words
                    if len(token) >= 3:
                        words.add(token.lower())

        return words
