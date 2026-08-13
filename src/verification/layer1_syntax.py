"""Layer 1: Determinism, Syntax, Length, Distractor Count, and Lexical Ceiling Validator."""

import re

from src.contracts import CandidateItem
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec
from src.lexicon.vocabulary import VocabularyStore


class Layer1SyntaxValidator:
    """Validates structural invariants: gaps, token limits, distractor counts, and CEFR ceiling."""

    def __init__(self, vocab_store: VocabularyStore | None = None) -> None:
        self.vocab_store = vocab_store

    def validate(
        self, item: CandidateItem, spec: TopicSpec | None = None
    ) -> tuple[bool, str | None]:
        """Validate candidate item against Layer 1 deterministic rules."""
        # 1. Gap presence
        if "___" not in item.prompt and not re.search(r"___\d+___", item.prompt):
            return False, "Missing gap placeholder '___' in prompt."

        # Multiple single gaps in non-paragraph cloze
        if item.type != "paragraph_cloze" and item.prompt.count("___") > 1:
            return False, "Multiple gap placeholders in single-sentence item."

        # 2. Distractor count check (must have exactly 3 distractors)
        if len(item.distractors) != 3:
            return False, f"Expected exactly 3 distractors, found {len(item.distractors)}."

        # Distractor non-emptiness and uniqueness
        distractor_texts = [d.text.strip().lower() for d in item.distractors]
        if any(not t for t in distractor_texts):
            return False, "Empty distractor text found."
        if len(set(distractor_texts)) != 3:
            return False, "Duplicate distractors found."
        if item.proposed_answer.strip().lower() in distractor_texts:
            return False, "Proposed answer is present among distractors."

        # 3. Topic leak check
        leaks = PromptBuilder.check_for_topic_leaks(item.prompt)
        if leaks:
            return False, f"Prompt contains forbidden grammatical terminology: {leaks}."

        # 4. Token length check
        max_tokens = spec.max_tokens_per_sentence if spec else 35
        tokens = re.findall(r"\b\w+\b", item.prompt)
        if len(tokens) > max_tokens and item.type != "paragraph_cloze":
            return False, f"Prompt length ({len(tokens)} tokens) exceeds limit ({max_tokens})."

        # 5. Vocabulary ceiling check
        if self.vocab_store and spec:
            violations = self.vocab_store.validate_sentence(item.prompt, spec.vocabulary_ceiling)
            if violations:
                return (
                    False,
                    f"Vocabulary ceiling ({spec.vocabulary_ceiling}) exceeded by: {violations}.",
                )

        return True, None
