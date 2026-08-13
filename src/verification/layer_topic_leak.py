"""Topic-leak validator ensuring prompts do not inadvertently leak grammatical target solutions."""

from src.contracts import CandidateItem
from src.generation.spec import TopicSpec


class TopicLeakValidator:
    """Detects whether candidate prompt leaks the target answer or violates topic isolation."""

    @classmethod
    def validate(
        cls,
        item: CandidateItem,
        spec: TopicSpec | None = None,
    ) -> tuple[bool, str | None]:
        """Validate that prompt does not contain answer leak or forbidden topic cues."""
        prompt_lower = item.prompt.lower()
        ans_lower = item.proposed_answer.lower()

        # 1. Prompt cannot contain the target answer elsewhere in the sentence
        clean_prompt_parts = [p.strip() for p in prompt_lower.split("___") if p.strip()]
        for part in clean_prompt_parts:
            tokens = [t.strip(".,!?:;\"'()") for t in part.split()]
            if len(ans_lower) > 2 and ans_lower in tokens:
                return (
                    False,
                    f"Topic leak: answer '{item.proposed_answer}' appears in prompt",
                )

        # 2. Check spec forbidden constraints
        if spec and spec.forbidden:
            for rule in spec.forbidden:
                if "answer appearing elsewhere" in rule.lower():
                    for part in clean_prompt_parts:
                        if ans_lower in part:
                            return False, f"Topic leak: answer appears in sentence part '{part}'"

        return True, None
