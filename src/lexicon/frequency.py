"""Frequency banding module for lexical difficulty control and tier assignment."""

from collections.abc import Collection

from src.contracts import Difficulty


class FrequencyBander:
    """Classifies German words into difficulty bands (1: basic, 2: intermediate, 3: advanced)."""

    def __init__(self, high_freq_words: Collection[str] | None = None) -> None:
        self.high_freq: set[str] = {w.lower() for w in (high_freq_words or [])}

    def classify_difficulty(self, words: list[str]) -> Difficulty:
        """Classify a list of words or carrier sentence lemmas into difficulty tier 1, 2, or 3."""
        if not words:
            return 1

        rare_count = 0
        long_words_count = 0

        for w in words:
            clean = w.lower().strip()
            if self.high_freq and clean not in self.high_freq:
                rare_count += 1
            if len(clean) >= 12:  # Long compound German nouns/words
                long_words_count += 1

        if long_words_count >= 2 or rare_count >= 3:
            return 3
        if long_words_count >= 1 or rare_count >= 1:
            return 2
        return 1
