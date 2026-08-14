"""Answer-set expander identifying valid morphological and syntactic filler variants."""

from src.contracts import CandidateItem


class AnswerSetExpander:
    """Expands valid German grammatical variants (e.g. contractions, capitalization)."""

    CONTRACTION_MAP: dict[str, list[str]] = {
        "ans": ["an das"],
        "an das": ["ans"],
        "aufs": ["auf das"],
        "auf das": ["aufs"],
        "beim": ["bei dem"],
        "bei dem": ["beim"],
        "im": ["in dem"],
        "in dem": ["im"],
        "ins": ["in das"],
        "in das": ["ins"],
        "vom": ["von dem"],
        "von dem": ["vom"],
        "zum": ["zu dem"],
        "zu dem": ["zum"],
        "zur": ["zu der"],
        "zu der": ["zur"],
    }

    @classmethod
    def expand_answers(cls, item: CandidateItem) -> list[str]:
        """Expand proposed answer into deduplicated accepted answers set."""
        accepted = [item.proposed_answer]
        raw_ans = item.proposed_answer.strip()

        # 1. Contraction expansion
        lower_ans = raw_ans.lower()
        if lower_ans in cls.CONTRACTION_MAP:
            for variant in cls.CONTRACTION_MAP[lower_ans]:
                if raw_ans[0].isupper():
                    expanded_v = variant.capitalize()
                else:
                    expanded_v = variant
                if expanded_v not in accepted:
                    accepted.append(expanded_v)

        # 2. Sentence initial position check
        if item.prompt.strip().startswith("___"):
            capitalized = raw_ans.capitalize()
            if capitalized not in accepted:
                accepted.append(capitalized)

        return accepted
