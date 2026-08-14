"""Deduplication engine for candidate items against existing bank items."""

import re


class ItemDeduplicator:
    """Detects exact and near-duplicate exercise prompts using token set and n-gram similarity."""

    @staticmethod
    def normalize_prompt(prompt: str) -> str:
        """Normalize prompt string: lowercase, remove punctuation, collapse spaces."""
        text = prompt.lower()
        # Preserve the gap ___ as a special token
        text = text.replace("___", " _GAP_ ")
        # Remove other punctuation
        text = re.sub(r"[^\w\s_GAP_]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def compute_jaccard_similarity(cls, text1: str, text2: str) -> float:
        """Compute Jaccard token similarity between two normalized prompt strings."""
        tokens1 = set(cls.normalize_prompt(text1).split())
        tokens2 = set(cls.normalize_prompt(text2).split())

        if not tokens1 and not tokens2:
            return 1.0
        if not tokens1 or not tokens2:
            return 0.0

        intersection = len(tokens1 & tokens2)
        union = len(tokens1 | tokens2)
        return intersection / union

    @classmethod
    def is_duplicate(
        cls,
        candidate_prompt: str,
        existing_prompts: list[str],
        threshold: float = 0.85,
    ) -> tuple[bool, str | None]:
        """Check if candidate prompt is too similar to any existing prompt in the bank."""
        cand_norm = cls.normalize_prompt(candidate_prompt)

        for existing in existing_prompts:
            exist_norm = cls.normalize_prompt(existing)
            # Exact match
            if cand_norm == exist_norm:
                return True, f"Exact duplicate of existing prompt: '{existing}'."

            # Near-duplicate match
            sim = cls.compute_jaccard_similarity(candidate_prompt, existing)
            if sim >= threshold:
                return True, f"Near-duplicate (similarity: {sim:.2f}) of: '{existing}'."

        return False, None
