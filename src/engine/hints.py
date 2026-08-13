"""Hint degradation policy mapping interactive hint levels to FSRS ratings."""

from src.contracts import FsrsRating, HintLevel


class HintPolicy:
    """Encapsulates the 4-tier hint ladder and translates hint usage into FSRS ratings."""

    @staticmethod
    def is_unhinted_pass(hint_level: HintLevel, is_correct: bool) -> bool:
        """Only Hint 0 (pure recall) without any clues counts toward promotion."""
        return hint_level == 0 and is_correct

    @classmethod
    def evaluate_attempt(
        cls,
        hint_level: HintLevel,
        is_correct: bool,
        is_fast: bool = False,
    ) -> FsrsRating:
        """Map user attempt accuracy and hint level to an FSRS rating."""
        if not is_correct or hint_level >= 3:
            # Failure, rule revealed, or answer given -> Again
            return "again"

        if hint_level in (1, 2):
            # Cued / Multiple-choice options selected -> Hard
            return "hard"

        # Hint 0 (Unhinted success)
        if is_fast:
            return "easy"
        return "good"
