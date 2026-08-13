"""Diagnostic calibration runner for initial CEFR placement and baseline acquisition."""

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import CEFR, BankItem
from src.engine.topic_state import TopicStateManager


class CalibrationResult(BaseModel):
    """Outcome of diagnostic placement trial."""

    model_config = ConfigDict(frozen=True)
    estimated_cefr: CEFR
    total_items: int
    correct_count: int
    score: float
    acquired_topics: list[str] = Field(default_factory=list)


class CalibrationRunner:
    """Runs a multi-tier diagnostic placement test to seed topic states."""

    CEFR_ORDER: list[CEFR] = ["A1", "A2", "B1", "B2"]

    @classmethod
    def evaluate_diagnostic(
        cls,
        responses: list[tuple[BankItem, str]],  # (item, user_answer)
        topic_manager: TopicStateManager,
    ) -> CalibrationResult:
        """Evaluate user responses across diagnostic items and mark mastered topics."""
        total = len(responses)
        if total == 0:
            return CalibrationResult(
                estimated_cefr="A1",
                total_items=0,
                correct_count=0,
                score=0.0,
            )

        correct_count = 0
        cefr_correct: dict[CEFR, int] = {"A1": 0, "A2": 0, "B1": 0, "B2": 0}
        cefr_totals: dict[CEFR, int] = {"A1": 0, "A2": 0, "B1": 0, "B2": 0}
        mastered_topics: set[str] = set()

        for item, user_ans in responses:
            cefr_totals[item.cefr] = cefr_totals.get(item.cefr, 0) + 1
            # Check answer
            clean_ans = user_ans.strip()
            is_correct = clean_ans in item.accepted_answers or clean_ans.lower() in [
                a.lower() for a in item.accepted_answers
            ]
            if is_correct:
                correct_count += 1
                cefr_correct[item.cefr] = cefr_correct.get(item.cefr, 0) + 1
                mastered_topics.add(item.topic_id)

        # Determine estimated CEFR level based on sequential mastery (>= 75% accuracy)
        estimated_level: CEFR = "A1"
        for level in cls.CEFR_ORDER:
            total_in_level = cefr_totals.get(level, 0)
            correct_in_level = cefr_correct.get(level, 0)
            if total_in_level > 0 and (correct_in_level / total_in_level) >= 0.75:
                estimated_level = level
            else:
                break

        # Apply calibration promotions in topic_manager for mastered topics at/below estimated level
        for topic_id in mastered_topics:
            if topic_id in topic_manager.states:
                topic_manager.mark_acquired_inferred(topic_id)

        score = round(correct_count / total, 2)
        return CalibrationResult(
            estimated_cefr=estimated_level,
            total_items=total,
            correct_count=correct_count,
            score=score,
            acquired_topics=sorted(mastered_topics),
        )
