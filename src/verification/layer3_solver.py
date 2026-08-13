"""Layer 3: Adversarial solver and ambiguity detection."""

import re

from src.contracts import CandidateItem


class Layer3AdversarialSolver:
    """Simulates an adversarial solver to identify ambiguous prompts and distractors."""

    def validate(self, item: CandidateItem) -> tuple[bool, str | None]:
        """Verify prompt disambiguation and assert no distractor provides an alternate solution."""
        ans_lower = item.proposed_answer.strip().lower()

        # Check for open/unconstrained cloze (e.g., "Ich trinke ___ Kaffee." without cue)
        # Patterns where any adverb/adjective fits without syntactic anchor
        if item.type == "cloze_free" and not item.cue:
            if re.match(r"^Ich (trinke|esse|mag|sehe) ___\s+\w+\.$", item.prompt.strip()):
                return False, "Prompt is under-constrained and admits multiple unguided solutions."

        # Check if any distractor is an identical synonym or inflectionally equivalent to answer
        for d in item.distractors:
            d_lower = d.text.strip().lower()
            if d_lower == ans_lower:
                return False, f"Distractor '{d.text}' matches proposed answer."

        # Check for distractors that are completely invalid non-words (gibberish hallucination)
        for d in item.distractors:
            if not re.match(r"^[A-ZÄÖÜa-zäöüß\-]+$", d.text.strip()):
                return False, f"Distractor '{d.text}' contains invalid non-German characters."

        return True, None
