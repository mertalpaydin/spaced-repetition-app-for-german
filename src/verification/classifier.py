"""Error classifier mapping verification-layer outputs to a canonical ErrorTaxonomy tag.

Each layer now returns its own structured ``ErrorTaxonomy`` code alongside the
human-readable message (see ``layer1_syntax.py``, ``layer2_morphology.py``,
``layer3_solver.py``, ``layer_topic_leak.py``). ``ErrorClassifier.classify``
trusts that code directly instead of re-deriving it from the free-text
message: keyword-sniffing free text is exactly what made three of the nine
``ErrorTaxonomy`` categories unreachable (a message could contain "topic" or
"distractor" as a substring of an earlier, unrelated branch's trigger word
and get shadowed before its real category was ever considered).

The keyword-based ``_classify_from_text`` fallback is kept only for reasons
that arrive without a structured code (e.g. the deduplication layer, which
sets its own literal ``"duplicate"`` code directly in ``pipeline.py`` and
never calls this classifier at all). It is deliberately not the primary path.
"""

from src.contracts import ErrorTaxonomy

_VALID_CODES: frozenset[str] = frozenset(
    (
        "structural_malformation",
        "topic_leak",
        "morphosyntactic_error",
        "morphological_defect",
        "register_mismatch",
        "ambiguity",
        "duplicate",
        "vocabulary_ceiling_violation",
        "pedagogical_flaw",
    )
)


class ErrorClassifier:
    """Classifies verification failure reasons into canonical ErrorTaxonomy tags."""

    @staticmethod
    def classify(
        layer: int,
        reason: str,
        code: ErrorTaxonomy | None = None,
    ) -> ErrorTaxonomy:
        """Return the canonical ``ErrorTaxonomy`` tag for a layer failure.

        ``code`` is the structured code the failing layer itself attached to
        the rejection; when present it is authoritative. ``layer`` and
        ``reason`` remain for the legacy keyword-based fallback and for
        diagnostics, but no longer participate in the primary decision.
        """
        if code is not None and code in _VALID_CODES:
            return code
        return ErrorClassifier._classify_from_text(reason)

    @staticmethod
    def _classify_from_text(reason: str) -> ErrorTaxonomy:
        """Legacy keyword-based fallback for reasons with no structured code."""
        r_lower = reason.lower()

        if "vocabulary ceiling" in r_lower or "exceeded by" in r_lower:
            return "vocabulary_ceiling_violation"

        if "topic leak" in r_lower or "forbidden grammatical terminology" in r_lower:
            return "topic_leak"

        if "distractor" in r_lower and "matches proposed answer" in r_lower:
            return "ambiguity"

        if (
            "gap" in r_lower
            or "distractor" in r_lower
            or "token length" in r_lower
            or "missing" in r_lower
        ):
            return "structural_malformation"

        if (
            "dativ" in r_lower
            or "akkusativ" in r_lower
            or "casing" in r_lower
            or "punctuation" in r_lower
            or "agreement" in r_lower
            or "ending" in r_lower
        ):
            return "morphosyntactic_error"

        if (
            "ambiguous" in r_lower
            or "under-constrained" in r_lower
            or "no governing" in r_lower
            or "determiner paradigm" in r_lower
        ):
            return "ambiguity"

        if "register" in r_lower or "colloquial" in r_lower or "slang" in r_lower:
            return "register_mismatch"

        if "duplicate" in r_lower:
            return "duplicate"

        return "pedagogical_flaw"
