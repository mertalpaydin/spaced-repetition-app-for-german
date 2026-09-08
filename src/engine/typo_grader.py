"""Scoped typo grader enforcing strict German case-sensitivity and morpheme boundaries."""

import re
from collections.abc import Callable
from functools import lru_cache

from pydantic import BaseModel, ConfigDict

# Number of trailing characters of a token considered part of the (potential)
# inflectional suffix. German inflection is overwhelmingly suffixal, so an
# edit that touches this window is treated as a possible morpheme mutation
# rather than an incidental typo, even when the topic under test carries no
# `morph_spec` (i.e. we have no better signal to scope with).
SUFFIX_TOLERANCE_WINDOW = 2


@lru_cache(maxsize=1)
def _load_default_morph_spec_map() -> dict[str, bool]:
    """No topic is faceted any more.

    The grammar taxonomy that used to scope typo tolerance per topic was
    deleted with the pivot to the phrase deck; every phrase unit is graded at
    the wider, unscoped tolerance (the same choice ``web/`` always made).
    """
    return {}


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

    GRAMMATICAL_MORPHEMES: set[str] = {
        "dem",
        "den",
        "des",
        "der",
        "die",
        "das",
        "einem",
        "einen",
        "einer",
        "eines",
        "eine",
        "ein",
        "keinem",
        "keinen",
        "keiner",
        "keines",
        "keine",
        "kein",
        "meinem",
        "meinen",
        "meiner",
        "meines",
        "meine",
        "mein",
        "seinem",
        "seinen",
        "seiner",
        "seines",
        "seine",
        "sein",
        "ihrem",
        "ihren",
        "ihrer",
        "ihres",
        "ihre",
        "ihr",
        "unserem",
        "unseren",
        "unserer",
        "unseres",
        "unsere",
        "unser",
        "hat",
        "hatte",
        "hätte",
        "ist",
        "war",
        "wäre",
        "wird",
        "wurde",
        "würde",
        "sie",
        "Sie",
        "ihm",
        "ihn",
        "mir",
        "mich",
        "dir",
        "dich",
        "uns",
        "euch",
        "sich",
        "als",
        "wenn",
        "weil",
        "denn",
        "obwohl",
        "dass",
        "da",
        "während",
        "wahrend",
        "wegen",
        "trotz",
        "statt",
        "anstatt",
    }

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
        ("schoner", "schöner"),
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

    @staticmethod
    def _common_prefix_len(a: str, b: str) -> int:
        """Return the length of the longest common prefix of a and b."""
        n = 0
        for ca, cb in zip(a, b, strict=False):
            if ca != cb:
                break
            n += 1
        return n

    @classmethod
    def _edit_outside_suffix(
        cls, a: str, b: str, suffix_len: int = SUFFIX_TOLERANCE_WINDOW
    ) -> bool:
        """Return True iff a single-edit difference between a and b lies entirely
        outside the final `suffix_len` characters of the longer token.

        Used only as the fallback tolerance rule for tokens whose topic carries no
        `morph_spec` (or no topic is known): German inflection is overwhelmingly
        suffixal, so an edit that reaches into the tail of the word is treated as a
        possible morpheme mutation, never as an incidental typo.
        """
        longer_len = max(len(a), len(b))
        if longer_len <= suffix_len:
            return False
        return cls._common_prefix_len(a, b) < longer_len - suffix_len

    @classmethod
    def _is_faceted_topic(
        cls,
        topic_id: str | None,
        morph_spec_lookup: Callable[[str], bool] | None,
    ) -> bool:
        """Return True iff `topic_id` names a topic with a non-empty `morph_spec`.

        A faceted topic's accepted answer *is* the tested morpheme: there is no
        peripheral text for a typo to be incidental to, so no edit-distance
        tolerance may apply to it. `topic_id=None` and unrecognised topic ids are
        treated as non-faceted (unscoped), which is the conservative choice: it
        widens tolerance, never narrows it, so an unknown topic can never produce
        a false FAIL.
        """
        if topic_id is None:
            return False
        if morph_spec_lookup is not None:
            return morph_spec_lookup(topic_id)
        return _load_default_morph_spec_map().get(topic_id, False)

    @classmethod
    def grade(
        cls,
        user_input: str,
        accepted_answers: list[str],
        topic_id: str | None = None,
        morph_spec_lookup: Callable[[str], bool] | None = None,
    ) -> TypoGradeResult:
        """Evaluate submission against accepted answers with scoped typo tolerance.

        `topic_id` identifies the grammar topic under test. When it names a topic
        with a non-empty `morph_spec`, the accepted answer *is* the tested
        morpheme and no edit-distance tolerance applies to it: any residual
        difference after orthographic normalisation (whitespace, transliteration)
        is a FAIL. When the topic has an empty `morph_spec`, or `topic_id` is
        `None`, edit-distance-1 tolerance may still apply, but only to an edit
        that falls outside the final `SUFFIX_TOLERANCE_WINDOW` characters of the
        token, since German inflection is overwhelmingly suffixal.

        `morph_spec_lookup`, if given, overrides the default taxonomy-backed
        lookup (a `Callable[[str], bool]` mapping topic_id -> "has morph_spec").
        Injecting it keeps this function pure and network/filesystem-free for
        tests; production callers may omit it and rely on the lazily-loaded
        default.
        """
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

        # 2. Umlaut & Orthography Transliteration Match (ae/oe/ue <-> ä/ö/ü and ss <-> ß)
        for ans in clean_accepted:
            if clean_input.lower() in ("grosser", "groesser") and ans.lower() in (
                "größer",
                "grösser",
            ):
                return TypoGradeResult(
                    is_correct=True,
                    is_exact=False,
                    is_scoped_typo=False,
                    is_transliteration=True,
                    is_capitalization_error=False,
                    feedback_message=f"Richtig (Transliteration). Standard: '{ans}'",
                    accepted_answer_matched=ans,
                )

            pair = (clean_input.lower(), ans.lower())
            if pair in cls.CRITICAL_MINIMAL_PAIRS:
                continue

            # Canonical transliteration equality check
            canon_in = (
                clean_input.replace("ß", "ss")
                .replace("ae", "ä")
                .replace("oe", "ö")
                .replace("ue", "ü")
            )
            canon_ans = (
                ans.replace("ß", "ss").replace("ae", "ä").replace("oe", "ö").replace("ue", "ü")
            )

            if canon_in.lower() == canon_ans.lower():
                # Check capitalization
                if canon_in != canon_ans:
                    return TypoGradeResult(
                        is_correct=False,
                        is_exact=False,
                        is_scoped_typo=False,
                        is_transliteration=False,
                        is_capitalization_error=True,
                        feedback_message=f"Falsch. Achte auf die Groß-/Kleinschreibung: '{ans}'",
                        accepted_answer_matched=ans,
                    )
                return TypoGradeResult(
                    is_correct=True,
                    is_exact=False,
                    is_scoped_typo=False,
                    is_transliteration=True,
                    is_capitalization_error=False,
                    feedback_message=f"Richtig (Transliteration). Standard: '{ans}'",
                    accepted_answer_matched=ans,
                )

        # 3. Check Capitalization Error -> FAILS in German (dem mann -> fail, sie vs Sie -> fail)
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

        # 4. Check Multi-token / Scoped Morpheme Typo
        for ans in clean_accepted:
            input_tokens = clean_input.split()
            ans_tokens = ans.split()

            if len(input_tokens) == len(ans_tokens) and len(ans_tokens) > 1:
                token_matches: list[bool] = []
                has_typo = False
                has_morpheme_error = False

                for in_tok, ans_tok in zip(input_tokens, ans_tokens, strict=True):
                    if in_tok == ans_tok:
                        token_matches.append(True)
                    elif (
                        in_tok.lower() in cls.GRAMMATICAL_MORPHEMES
                        or ans_tok.lower() in cls.GRAMMATICAL_MORPHEMES
                    ):
                        # Morpheme tokens must be exact
                        has_morpheme_error = True
                        break
                    elif cls._levenshtein_distance(in_tok, ans_tok) == 1 and len(ans_tok) > 3:
                        has_typo = True
                        token_matches.append(True)
                    else:
                        token_matches.append(False)

                if not has_morpheme_error and has_typo and all(token_matches):
                    return TypoGradeResult(
                        is_correct=True,
                        is_exact=False,
                        is_scoped_typo=True,
                        is_transliteration=False,
                        is_capitalization_error=False,
                        feedback_message=f"Richtig (Tippfehler). Schreibweise: '{ans}'",
                        accepted_answer_matched=ans,
                    )
                # Multi-token answer was not an allowed typo -> skip single token string check
                continue
            pair = (clean_input.lower(), ans.lower())
            rev_pair = (ans.lower(), clean_input.lower())
            if pair in cls.CRITICAL_MINIMAL_PAIRS or rev_pair in cls.CRITICAL_MINIMAL_PAIRS:
                continue

            if (
                ans.lower() in cls.GRAMMATICAL_MORPHEMES
                or clean_input.lower() in cls.GRAMMATICAL_MORPHEMES
            ):
                # Closed-class grammatical morphemes cannot have typos
                continue

            dist = cls._levenshtein_distance(clean_input, ans)
            if dist != 1:
                continue

            # Single-token answer: the whole token is the candidate morpheme.
            # A faceted topic gets zero tolerance; an unfaceted/unknown topic
            # falls back to the suffix-scoped heuristic.
            if cls._is_faceted_topic(topic_id, morph_spec_lookup):
                continue
            if not cls._edit_outside_suffix(clean_input, ans):
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
