"""Error classifier mapping rejection messages to structured ErrorTaxonomy."""

from src.contracts import ErrorTaxonomy


class ErrorClassifier:
    """Classifies verification failure reasons into canonical ErrorTaxonomy tags."""

    @staticmethod
    def classify(layer: int, reason: str) -> ErrorTaxonomy:
        """Map failure layer and reason message to an ErrorTaxonomy tag."""
        r_lower = reason.lower()

        if "vocabulary ceiling" in r_lower or "exceeded by" in r_lower:
            return "vocabulary_ceiling_violation"

        if (
            "gap" in r_lower
            or "distractor" in r_lower
            or "token length" in r_lower
            or "missing" in r_lower
        ):
            if "distractor" in r_lower and ("duplicate" in r_lower or "count" in r_lower):
                return "pedagogical_flaw"
            return "pedagogical_flaw"

        if "topic" in r_lower or "forbidden" in r_lower:
            return "pedagogical_flaw"

        if (
            "dativ" in r_lower
            or "akkusativ" in r_lower
            or "casing" in r_lower
            or "punctuation" in r_lower
        ):
            return "morphological_defect"

        if (
            "ambiguous" in r_lower
            or "under-constrained" in r_lower
            or "matches proposed answer" in r_lower
        ):
            return "ambiguity"

        if "register" in r_lower or "colloquial" in r_lower or "slang" in r_lower:
            return "register_mismatch"

        return "pedagogical_flaw"
