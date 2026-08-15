"""VocabularyStore for CEFR vocabulary ceiling management and sentence validation."""

import json
import re
from pathlib import Path
from typing import ClassVar

from src.contracts import CEFR
from src.lexicon.lemmatizer import lemma_candidates, normalise


class VocabularyStore:
    """Stores vocabulary mappings and validates sentence tokens against CEFR ceilings."""

    LEVEL_RANKS: ClassVar[dict[CEFR, int]] = {
        "A1": 1,
        "A2": 2,
        "B1": 3,
        "B2": 4,
    }

    # Proper nouns (given names) are excluded from the CEFR ceiling entirely,
    # the same way a learner's own name would be -- they carry no vocabulary
    # difficulty of their own. This is a deliberately small, explicit
    # whitelist of common German first names, kept separate from the
    # scraped vocabulary data: names are not "vocabulary" in the CEFR sense
    # and do not belong in vocab_levels.json (data/fixtures/corpus). A
    # generic "capitalised word => proper noun" heuristic would silently
    # defeat the ceiling check, since every German noun is capitalised.
    PROPER_NOUNS: ClassVar[frozenset[str]] = frozenset(
        {
            "anna",
            "lisa",
            "lena",
            "laura",
            "julia",
            "sophie",
            "sarah",
            "nina",
            "maria",
            "petra",
            "sabine",
            "katrin",
            "andrea",
            "ursula",
            "max",
            "paul",
            "peter",
            "thomas",
            "michael",
            "stefan",
            "markus",
            "klaus",
            "werner",
            "tim",
            "jan",
            "lukas",
            "felix",
            "julian",
            # Country and city names: the same "carries no vocabulary
            # difficulty of its own" reasoning as a given name -- confirmed
            # live: "Schweden" (Sweden) was rejected as violating a B1
            # ceiling for a futur_i item ("...nach Schweden reisen"), a
            # travel-topic sentence pattern common across every CEFR level.
            "deutschland",
            "österreich",
            "schweiz",
            "frankreich",
            "spanien",
            "italien",
            "england",
            "amerika",
            "schweden",
            "polen",
            "türkei",
            "griechenland",
            "portugal",
            "russland",
            "china",
            "japan",
            "berlin",
            "münchen",
            "hamburg",
            "köln",
            "frankfurt",
            "wien",
            "zürich",
            "europa",
            "asien",
            "afrika",
        }
    )

    # High frequency function words inherently permissible at all levels
    FUNCTION_WORDS: ClassVar[set[str]] = {
        "der",
        "die",
        "das",
        "dem",
        "den",
        "des",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "eines",
        "kein",
        "keine",
        "keinen",
        "keinem",
        "keiner",
        "keines",
        "ich",
        "du",
        "er",
        "sie",
        "es",
        "wir",
        "ihr",
        "mich",
        "dich",
        "ihn",
        "uns",
        "euch",
        "ihnen",
        "mir",
        "dir",
        "ihm",
        "mein",
        "dein",
        "sein",
        "unser",
        "euer",
        "und",
        "oder",
        "aber",
        "denn",
        "weil",
        "da",
        "dass",
        "wenn",
        "als",
        "ob",
        "obwohl",
        "trotzdem",
        "deshalb",
        "ist",
        "sind",
        "war",
        "waren",
        "hat",
        "haben",
        "hatte",
        "hatten",
        "wird",
        "werden",
        "wurde",
        "wurden",
        "kann",
        "können",
        "muss",
        "müssen",
        "will",
        "wollen",
        "darf",
        "dürfen",
        "soll",
        "sollen",
        "nicht",
        "sehr",
        "hier",
        "dort",
        "heute",
        "gestern",
        "morgen",
        "in",
        "an",
        "auf",
        "neben",
        "hinter",
        "über",
        "unter",
        "vor",
        "zwischen",
        "mit",
        "nach",
        "bei",
        "seit",
        "von",
        "zu",
        "aus",
        "durch",
        "für",
        "gegen",
        "ohne",
        "um",
        "wie",
        "so",
        "ja",
        "nein",
        "auch",
    }

    def __init__(self, vocab: dict[str, CEFR] | None = None) -> None:
        self.vocab: dict[str, CEFR] = {normalise(k): v for k, v in (vocab or {}).items()}

    def get_level(self, word: str) -> CEFR | None:
        """Get the assigned CEFR level for a German word.

        Tries the surface form first (direct dictionary hit), then falls
        back to progressively reduced morphological candidates (see
        ``src.lexicon.lemmatizer``) so that an inflected form absent from
        the scraped wordlist -- but whose lemma is present -- still
        resolves. Returns the level of the first candidate that is found,
        or ``None`` if nothing resolves.
        """
        normalized = normalise(word)
        if normalized in self.FUNCTION_WORDS:
            return "A1"
        for candidate in lemma_candidates(normalized):
            level = self.vocab.get(candidate)
            if level is not None:
                return level
        return None

    def is_within_ceiling(
        self, word: str, ceiling: CEFR, context_tokens: list[str] | None = None
    ) -> bool:
        """Check if a word is within or below the specified CEFR ceiling.

        A word is considered within ceiling if *any* morphological reading
        of it (surface form or a reduced candidate lemma, optionally
        combined with a separable-verb prefix found in ``context_tokens``)
        resolves to a level at or below ``ceiling``. This is deliberately
        more generous than a first-match lookup: the underlying vocabulary
        data is scraped surface forms with arbitrary per-form level
        assignment, so an inflected surface form can be mis-leveled even
        though its lemma is not. A word is only rejected when no candidate
        resolves within the ceiling at all.
        """
        normalized = normalise(word)
        if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
            return True

        resolved_any = False
        for candidate in lemma_candidates(normalized, context_tokens):
            level = self.vocab.get(candidate)
            if level is None:
                continue
            resolved_any = True
            if self.LEVEL_RANKS[level] <= self.LEVEL_RANKS[ceiling]:
                return True

        # docs/audits/stage-04-recovery-plan.md fix C. This used to return
        # False here, treating "absent from the wordlist" as identical to
        # "above the ceiling". It is not. The list holds 11,633 entries and
        # does not contain "Vorstand", "Projektleiter", "Analyse" or "These",
        # all of which are ordinary B2 words; every one of them was rejected
        # as too hard for B2.
        #
        # A wordlist is evidence a word is EASY. Its silence is not evidence
        # that a word is hard, and no scrape will ever be complete enough to
        # make it so. An unresolved word is unknown, and unknown passes --
        # counted, via ``unknown_words``, so the list's coverage stays
        # visible instead of silently punitive.
        if not resolved_any:
            return True
        return False

    def unknown_words(self, sentence: str) -> list[str]:
        """Return tokens that resolve to no level at all.

        Reported by the pilot so wordlist coverage is measurable. A rising
        unknown rate means the list needs extending; it does not mean the
        generated items got harder.
        """
        tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]{3,}\b", sentence)
        all_tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]+\b", sentence)
        unknown: list[str] = []
        for token in tokens:
            normalized = normalise(token)
            if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
                continue
            if all(self.vocab.get(c) is None for c in lemma_candidates(normalized, all_tokens)):
                unknown.append(token)
        return unknown

    def validate_sentence(self, sentence: str, ceiling: CEFR) -> list[str]:
        """Return a list of words in the sentence that violate the CEFR ceiling."""
        tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]{3,}\b", sentence)
        all_tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]+\b", sentence)
        violations: list[str] = []

        for token in tokens:
            normalized = normalise(token)
            if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
                continue

            if not self.is_within_ceiling(token, ceiling, context_tokens=all_tokens):
                violations.append(token)

        return violations

    def save(self, path: Path | str) -> None:
        """Save vocabulary store to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(self.vocab, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path | str) -> "VocabularyStore":
        """Load vocabulary store from JSON."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Vocabulary file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(vocab=data)
