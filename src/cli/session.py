"""Interactive practice session orchestrator with hint stepping and FSRS updates."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import BankItem, FsrsRating, HintLevel
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy
from src.engine.scheduler import RoundPlan
from src.engine.topic_state import TopicStateManager


class AttemptResult(BaseModel):
    """Result of practicing a single exercise item."""

    model_config = ConfigDict(frozen=True)
    item_id: str
    topic_id: str
    user_answer: str
    is_correct: bool
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
        """Evaluate user answer, update topic state machine and FSRS record."""
        clean_ans = user_answer.strip()
        is_correct = clean_ans in item.accepted_answers or clean_ans.lower() in [
            a.lower() for a in item.accepted_answers
        ]

        is_unhinted = HintPolicy.is_unhinted_pass(hint_level=hint_level, is_correct=is_correct)
        rating = HintPolicy.evaluate_attempt(
            hint_level=hint_level, is_correct=is_correct, is_fast=is_fast
        )

        # 1. Update Topic State Manager
        self.topic_manager.record_attempt(
            topic_id=item.topic_id,
            is_unhinted_pass=is_unhinted,
            facet=item.facet,
        )

        # 2. Update FSRS memory record
        current_record = self.fsrs_records.get(
            item.id,
            FSRSRecord(card_id=item.id),
        )
        updated_record = self.fsrs_engine.schedule_review(
            record=current_record,
            rating=rating,
            now=datetime.now(UTC),
        )
        self.fsrs_records[item.id] = updated_record

        result = AttemptResult(
            item_id=item.id,
            topic_id=item.topic_id,
            user_answer=clean_ans,
            is_correct=is_correct,
            hint_level=hint_level,
            fsrs_rating=rating,
            is_unhinted_pass=is_unhinted,
        )
        self.attempts.append(result)
        return result

    def get_summary(self) -> RoundSummary:
        """Compute end-of-round performance metrics."""
        total = len(self.attempts)
        if total == 0:
            return RoundSummary(total_items=0, correct_items=0, accuracy=0.0, unhinted_passes=0)

        correct = sum(1 for a in self.attempts if a.is_correct)
        unhinted = sum(1 for a in self.attempts if a.is_unhinted_pass)

        return RoundSummary(
            total_items=total,
            correct_items=correct,
            accuracy=round(correct / total, 2),
            unhinted_passes=unhinted,
            attempts=self.attempts,
        )
