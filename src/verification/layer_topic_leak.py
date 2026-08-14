"""Topic-leak validator ensuring prompts do not inadvertently leak grammatical target solutions."""

from src.contracts import CandidateItem, ErrorTaxonomy
from src.generation.spec import TopicSpec


class TopicLeakValidator:
    """Detects whether candidate prompt leaks the target answer or violates topic isolation."""

    @classmethod
    def validate(
        cls,
        item: CandidateItem,
        spec: TopicSpec | None = None,
    ) -> tuple[bool, str | None, ErrorTaxonomy | None]:
        """Validate that prompt does not contain answer leak or forbidden topic cues.

        Both checks below use *word*-boundary matching, never substring matching:
        a raw ``ans_lower in part`` substring test would flag short answers such
        as "wo" or "an" as leaked whenever they occur as a prefix inside an
        unrelated longer word (e.g. "wo" inside "wohnst"), which is a false
        positive, not a topic leak.
        """
        prompt_lower = item.prompt.lower()
        ans_lower = item.proposed_answer.strip().lower()

        clean_prompt_parts = [p.strip() for p in prompt_lower.split("___") if p.strip()]

        def _answer_is_word_in(part: str) -> bool:
            if len(ans_lower) <= 2:
                return False
            tokens = [t.strip(".,!?:;\"'()") for t in part.split()]
            return ans_lower in tokens

        # 1. Prompt cannot contain the target answer elsewhere in the sentence
        for part in clean_prompt_parts:
            if _answer_is_word_in(part):
                return (
                    False,
                    f"Topic leak: answer '{item.proposed_answer}' appears in prompt",
                    "topic_leak",
                )

        # 2. Check spec forbidden constraints (same word-boundary rule, never substring)
        if spec and spec.forbidden:
            for rule in spec.forbidden:
                if "answer appearing elsewhere" in rule.lower():
                    for part in clean_prompt_parts:
                        if _answer_is_word_in(part):
                            return (
                                False,
                                f"Topic leak: answer appears in sentence part '{part}'",
                                "topic_leak",
                            )

        return True, None, None
