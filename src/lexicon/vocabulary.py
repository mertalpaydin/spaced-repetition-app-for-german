"""VocabularyStore for CEFR vocabulary ceiling management and sentence validation."""

import json
import re
from pathlib import Path
from typing import ClassVar

from src.contracts import CEFR


class VocabularyStore:
    """Stores vocabulary mappings and validates sentence tokens against CEFR ceilings."""

    LEVEL_RANKS: ClassVar[dict[CEFR, int]] = {
        "A1": 1,
        "A2": 2,
        "B1": 3,
        "B2": 4,
    }

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
        self.vocab: dict[str, CEFR] = {k.lower(): v for k, v in (vocab or {}).items()}

    def get_level(self, word: str) -> CEFR | None:
        """Get the assigned CEFR level for a German word."""
        normalized = word.lower().strip()
        if normalized in self.FUNCTION_WORDS:
            return "A1"
        return self.vocab.get(normalized)

    def is_within_ceiling(self, word: str, ceiling: CEFR) -> bool:
        """Check if a word is within or below the specified CEFR ceiling."""
        level = self.get_level(word)
        if level is None:
            # Word not found in lexicon - fail conservatively
            return False
        return self.LEVEL_RANKS[level] <= self.LEVEL_RANKS[ceiling]

    def validate_sentence(self, sentence: str, ceiling: CEFR) -> list[str]:
        """Return a list of words in the sentence that violate the CEFR ceiling."""
        tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]{3,}\b", sentence)
        violations: list[str] = []

        for token in tokens:
            normalized = token.lower()
            if normalized in self.FUNCTION_WORDS:
                continue

            level = self.get_level(normalized)
            if level is None or self.LEVEL_RANKS[level] > self.LEVEL_RANKS[ceiling]:
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
