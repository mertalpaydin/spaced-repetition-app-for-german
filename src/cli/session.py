"""Interactive practice session orchestrator with hint stepping and FSRS updates."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import BankItem, FsrsRating, HintLevel
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy
from src.engine.scheduler import RoundPlan
from src.engine.topic_state import TopicStateManager
from src.engine.typo_grader import ScopedTypoGrader


class AttemptResult(BaseModel):
    """Result of practicing a single exercise item."""

    model_config = ConfigDict(frozen=True)
    item_id: str
    topic_id: str
    user_answer: str
    is_correct: bool
    is_scoped_typo: bool = False
    is_capitalization_error: bool = False
    feedback_message: str | None = None
    hint_level: HintLevel
    fsrs_rating: FsrsRating
    is_unhinted_pass: bool


class RoundSummary(BaseModel):
    """Summary of a completed practice round."""

    model_config = ConfigDict(frozen=True)
    total_items: int
    correct_items: int
    accuracy: float
    unhinted_passes: int
    attempts: list[AttemptResult] = Field(default_factory=list)


class InteractiveSession:
    """Controls the turn-by-turn execution of a learning round."""

    def __init__(
        self,
        round_plan: RoundPlan,
        topic_manager: TopicStateManager,
        fsrs_engine: FSRSEngine,
        fsrs_records: dict[str, FSRSRecord],
    ) -> None:
        self.round_plan = round_plan
        self.topic_manager = topic_manager
        self.fsrs_engine = fsrs_engine
        self.fsrs_records = fsrs_records
        self.attempts: list[AttemptResult] = []

    def process_item_attempt(
        self,
        item: BankItem,
        user_answer: str,
        hint_level: HintLevel = 0,
        is_fast: bool = False,
    ) -> AttemptResult:
        """Evaluate user answer with scoped typo tolerance and update FSRS record."""
        grade_res = ScopedTypoGrader.grade(user_answer, item.accepted_answers)
        is_correct = grade_res.is_correct

        is_unhinted = HintPolicy.is_unhinted_pass(hint_level=hint_level, is_correct=is_correct)
        rating = HintPolicy.evaluate_attempt(
            hint_level=hint_level, is_correct=is_correct, is_fast=is_fast
        )

        now = datetime.now(UTC)

        # Update FSRS card
        card_record = self.fsrs_records.get(
            item.id,
            FSRSRecord(card_id=item.id, due=now),
        )
        updated_card = self.fsrs_engine.schedule_review(card_record, rating=rating, now=now)
        self.fsrs_records[item.id] = updated_card

        # Update Topic State Machine
        self.topic_manager.record_attempt(
            topic_id=item.topic_id,
            is_unhinted_pass=is_unhinted,
            facet=item.facet,
        )

        result = AttemptResult(
            item_id=item.id,
            topic_id=item.topic_id,
            user_answer=user_answer,
            is_correct=is_correct,
            is_scoped_typo=grade_res.is_scoped_typo,
            is_capitalization_error=grade_res.is_capitalization_error,
            feedback_message=grade_res.feedback_message,
            hint_level=hint_level,
            fsrs_rating=rating,
            is_unhinted_pass=is_unhinted,
        )
        self.attempts.append(result)
        return result

    def get_summary(self) -> RoundSummary:
        """Calculate summary statistics for the completed session."""
        total = len(self.attempts)
        correct = sum(1 for a in self.attempts if a.is_correct)
        unhinted = sum(1 for a in self.attempts if a.is_unhinted_pass)
        accuracy = round(correct / total, 3) if total > 0 else 0.0

        return RoundSummary(
            total_items=total,
            correct_items=correct,
            accuracy=accuracy,
            unhinted_passes=unhinted,
            attempts=self.attempts,
        )
