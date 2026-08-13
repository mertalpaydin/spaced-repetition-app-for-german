"""Scoped typo grader enforcing strict German case-sensitivity and morpheme boundaries."""

import re

from pydantic import BaseModel, ConfigDict


class TypoGradeResult(BaseModel):
    """Result of grading a user submission with scoped typo analysis."""

    model_config = ConfigDict(frozen=True)
    is_correct: bool
    is_exact: bool
    is_scoped_typo: bool
    is_transliteration: bool
    is_capitalization_error: bool
    feedback_message: str | None = None
    accepted_answer_matched: str


class ScopedTypoGrader:
    """Grades learner input with scoped typo tolerance and strict German grammar rules.

    Invariants:
    1. Capitalization is grammatically meaningful in German: wrong capitalization FAILS.
    2. The tested grammatical morpheme MUST be exact.
    3. Edit-distance-1 is tolerated ONLY on peripheral stems outside the tested morpheme.
    4. Umlaut transliterations (ae <-> ä, oe <-> ö, ue <-> ü, ss <-> ß) are accepted.
    5. Internal whitespace is normalized (e.g. 'dem   Mann' -> 'dem Mann').
    """

    TRANSLITERATION_PAIRS: list[tuple[str, str]] = [
        ("ae", "ä"),
        ("oe", "ö"),
        ("ue", "ü"),
        ("Ae", "Ä"),
        ("Oe", "Ö"),
        ("Ue", "Ü"),
        ("ss", "ß"),
    ]

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
    def normalize_whitespace(text: str) -> str:
        """Collapse multiple internal whitespace characters into single spaces."""
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def apply_transliteration(cls, text: str) -> str:
        """Convert ASCII umlaut transliterations (ae/oe/ue/ss) to standard German characters."""
        res = text
        for ascii_form, german_form in cls.TRANSLITERATION_PAIRS:
            res = res.replace(ascii_form, german_form)
        return res

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
        clean_input = cls.normalize_whitespace(user_input)
        clean_accepted = [cls.normalize_whitespace(a) for a in accepted_answers]

        # 1. Exact Match
        if clean_input in clean_accepted:
            return TypoGradeResult(
                is_correct=True,
                is_exact=True,
                is_scoped_typo=False,
                is_transliteration=False,
                is_capitalization_error=False,
                accepted_answer_matched=clean_input,
            )

        # 2. Umlaut Transliteration Match (e.g. 'schon' != 'schön', but 'schoen' == 'schön')
        trans_input = cls.apply_transliteration(clean_input)
        if trans_input in clean_accepted:
            return TypoGradeResult(
                is_correct=True,
                is_exact=False,
                is_scoped_typo=False,
                is_transliteration=True,
                is_capitalization_error=False,
                feedback_message=f"Richtig (Umlaut-Transliteration). Standard: '{trans_input}'",
                accepted_answer_matched=trans_input,
            )

        # 3. Check Capitalization Error -> FAILS in German (dem mann -> fail)
        for ans in clean_accepted:
            if clean_input.lower() == ans.lower() and clean_input != ans:
                return TypoGradeResult(
                    is_correct=False,  # Strict fail for capitalization in German
                    is_exact=False,
                    is_scoped_typo=False,
                    is_transliteration=False,
                    is_capitalization_error=True,
                    feedback_message=f"Falsch. Achte auf die Groß-/Kleinschreibung: '{ans}'",
                    accepted_answer_matched=ans,
                )

        # 4. Check Scoped Typo (Edit Distance == 1 on non-morpheme parts)
        for ans in clean_accepted:
            pair = (clean_input.lower(), ans.lower())
            rev_pair = (ans.lower(), clean_input.lower())
            if pair in cls.CRITICAL_MINIMAL_PAIRS or rev_pair in cls.CRITICAL_MINIMAL_PAIRS:
                continue

            dist = cls._levenshtein_distance(clean_input, ans)
            if dist == 1:
                # Disallow typos on short standalone grammatical tokens (length <= 3)
                if len(ans) <= 3:
                    continue

                return TypoGradeResult(
                    is_correct=True,
                    is_exact=False,
                    is_scoped_typo=True,
                    is_transliteration=False,
                    is_capitalization_error=False,
                    feedback_message=f"Richtig (Tippfehler). Schreibweise: '{ans}'",
                    accepted_answer_matched=ans,
                )

        # 5. Incorrect
        return TypoGradeResult(
            is_correct=False,
            is_exact=False,
            is_scoped_typo=False,
            is_transliteration=False,
            is_capitalization_error=False,
            feedback_message=f"Falsch. Richtige Antwort: '{clean_accepted[0]}'",
            accepted_answer_matched=clean_accepted[0],
        )
