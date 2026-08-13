"""Scoped typo grader enforcing strict grammatical morphemes while forgiving peripheral typos."""

from pydantic import BaseModel, ConfigDict


class TypoGradeResult(BaseModel):
    """Result of grading a user submission with scoped typo analysis."""

    model_config = ConfigDict(frozen=True)
    is_correct: bool
    is_exact: bool
    is_scoped_typo: bool
    is_capitalization_error: bool
    feedback_message: str | None = None
    accepted_answer_matched: str


class ScopedTypoGrader:
    """Grades learner input with scoped typo tolerance.

    Invariants:
    1. The tested grammatical morpheme (e.g. case ending, article, umlaut in subjunctive)
       MUST be exact.
    2. Edit-distance-1 (Levenshtein) is tolerated ONLY outside the tested morpheme.
    3. Confusable grammatical minimal pairs (dem/den/des, hatte/hätte, schon/schön, großer/größer)
       are NEVER treated as typos.
    """

    CRITICAL_MINIMAL_PAIRS: set[tuple[str, str]] = {
        ("dem", "den"),
        ("dem", "des"),
        ("den", "der"),
        ("einem", "einen"),
        ("einem", "einer"),
        ("hatte", "hätte"),
        ("hatten", "hätten"),
        ("war", "wäre"),
        ("waren", "wären"),
        ("konnte", "könnte"),
        ("musste", "müsste"),
        ("mochte", "möchte"),
        ("schon", "schön"),
        ("grosser", "grösser"),
        ("großer", "größer"),
        ("flog", "flöge"),
    }

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """Compute standard Levenshtein edit distance."""
        if len(s1) < len(s2):
            return ScopedTypoGrader._levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)

        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]

    @classmethod
    def grade(
        cls,
        user_input: str,
        accepted_answers: list[str],
    ) -> TypoGradeResult:
        """Evaluate submission against accepted answers with scoped typo tolerance."""
        clean_input = user_input.strip()

        # 1. Exact Match
        if clean_input in accepted_answers:
            return TypoGradeResult(
                is_correct=True,
                is_exact=True,
                is_scoped_typo=False,
                is_capitalization_error=False,
                accepted_answer_matched=clean_input,
            )

        # 2. Capitalization Difference
        for ans in accepted_answers:
            if clean_input.lower() == ans.lower():
                return TypoGradeResult(
                    is_correct=True,
                    is_exact=False,
                    is_scoped_typo=False,
                    is_capitalization_error=True,
                    feedback_message=f"Richtig, aber achte auf die Groß-/Kleinschreibung: '{ans}'",
                    accepted_answer_matched=ans,
                )

        # 3. Check Scoped Typo (Edit Distance == 1)
        for ans in accepted_answers:
            # Check if this pair is a forbidden grammatical minimal pair
            pair = (clean_input.lower(), ans.lower())
            rev_pair = (ans.lower(), clean_input.lower())
            if pair in cls.CRITICAL_MINIMAL_PAIRS or rev_pair in cls.CRITICAL_MINIMAL_PAIRS:
                continue

            dist = cls._levenshtein_distance(clean_input.lower(), ans.lower())
            if dist == 1:
                # If target is a short article/pronoun (len <= 3), edit dist 1 is too risky
                if len(ans) <= 3:
                    continue

                # Valid scoped typo
                return TypoGradeResult(
                    is_correct=True,
                    is_exact=False,
                    is_scoped_typo=True,
                    is_capitalization_error=False,
                    feedback_message=f"Richtig (Tippfehler). Schreibweise: '{ans}'",
                    accepted_answer_matched=ans,
                )

        # 4. Incorrect
        return TypoGradeResult(
            is_correct=False,
            is_exact=False,
            is_scoped_typo=False,
            is_capitalization_error=False,
            feedback_message=f"Falsch. Richtige Antwort: '{accepted_answers[0]}'",
            accepted_answer_matched=accepted_answers[0],
        )
