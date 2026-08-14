"""Diagnostic placement test and Kalibrierung protocol implementation."""

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import CEFR, Answer, BankItem, TagStateModel
from src.engine.topic_state import TopicStateManager


class CalibrationResult(BaseModel):
    """Result of running diagnostic calibration."""

    model_config = ConfigDict(frozen=True)
    estimated_cefr: CEFR
    total_items: int
    correct_count: int
    score: float
    acquired_topics: list[str] = Field(default_factory=list)


class CalibrationRunner:
    """Runs a multi-tier diagnostic placement test to seed topic states."""

    CEFR_ORDER: list[CEFR] = ["A1", "A2", "B1", "B2"]

    def __init__(
        self,
        items: list[BankItem] | None = None,
        topic_manager: TopicStateManager | None = None,
    ) -> None:
        self.items = items or []
        self.topic_manager = topic_manager

    def next_item(self, answered: list[Answer]) -> BankItem | None:
        """Select next diagnostic probe item based on previous answers."""
        answered_ids = {a.item_id for a in answered}
        for it in self.items:
            if it.id not in answered_ids:
                return it
        return None

    def finalise(self, answered: list[Answer]) -> list[TagStateModel]:
        """Finalize placement assessment and return seeded tag states."""
        if not self.topic_manager:
            return []

        for ans in answered:
            if ans.is_correct:
                self.topic_manager.record_attempt(
                    topic_id=ans.topic_id,
                    is_unhinted_pass=True,
                )
                self.topic_manager.mark_acquired_inferred(ans.topic_id)
        return list(self.topic_manager.states.values())

    @classmethod
    def evaluate_diagnostic(
        cls,
        responses: list[tuple[BankItem, str]],  # (item, user_answer)
        topic_manager: TopicStateManager,
    ) -> CalibrationResult:
        """Evaluate user responses across diagnostic items with strict case sensitivity."""
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
            clean_ans = user_ans.strip()

            # Strict German case-sensitive verification
            is_correct = clean_ans in item.accepted_answers

            if is_correct:
                correct_count += 1
                cefr_correct[item.cefr] = cefr_correct.get(item.cefr, 0) + 1
                mastered_topics.add(item.topic_id)

        # Determine estimated CEFR level based on sequential mastery (>= 75% accuracy)
        estimated_level: CEFR = "A1"
        for level in cls.CEFR_ORDER:
            tot = cefr_totals.get(level, 0)
            corr = cefr_correct.get(level, 0)
            if tot > 0 and (corr / tot) >= 0.75:
                estimated_level = level
            else:
                break

        # Apply inferred mastery downstream in topic state manager
        for t_id in mastered_topics:
            topic_manager.record_attempt(t_id, is_unhinted_pass=True)
            topic_manager.mark_acquired_inferred(t_id)

        score = round(correct_count / total, 3) if total > 0 else 0.0
        return CalibrationResult(
            estimated_cefr=estimated_level,
            total_items=total,
            correct_count=correct_count,
            score=score,
            acquired_topics=sorted(mastered_topics),
        )
