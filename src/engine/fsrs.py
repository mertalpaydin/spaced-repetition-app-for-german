"""FSRS algorithm wrapper for memory state tracking and review interval scheduling."""

from datetime import UTC, datetime

from fsrs import Card, Rating, Scheduler, State
from pydantic import BaseModel, ConfigDict, Field

from src.contracts import FsrsRating


class FSRSRecord(BaseModel):
    """Memory state of a single card/item under the FSRS algorithm."""

    model_config = ConfigDict(frozen=True)
    card_id: str
    state: str = "learning"  # learning, review, relearning
    due: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stability: float | None = None
    difficulty: float | None = None
    step: int = 0
    reps: int = 0
    lapses: int = 0
    last_review: datetime | None = None


class FSRSEngine:
    """Provides card scheduling and review processing using FSRS-4.5."""

    RATING_MAP = {
        "again": Rating.Again,
        "hard": Rating.Hard,
        "good": Rating.Good,
        "easy": Rating.Easy,
    }

    STATE_STR_MAP = {
        State.Learning: "learning",
        State.Review: "review",
        State.Relearning: "relearning",
    }

    STATE_OBJ_MAP = {
        "learning": State.Learning,
        "review": State.Review,
        "relearning": State.Relearning,
    }

    def __init__(self) -> None:
        self.scheduler = Scheduler()

    def record_to_fsrs_card(self, record: FSRSRecord) -> Card:
        """Convert a serializable FSRSRecord to an fsrs.Card instance."""
        card_num = int(record.card_id) if record.card_id.isdigit() else None
        card = Card(card_id=card_num)
        card.due = record.due
        card.stability = record.stability
        card.difficulty = record.difficulty
        card.step = record.step
        card.state = self.STATE_OBJ_MAP.get(record.state, State.Learning)
        card.last_review = record.last_review
        return card

    def fsrs_card_to_record(
        self, card_id: str, card: Card, reps: int = 0, lapses: int = 0
    ) -> FSRSRecord:
        """Convert an fsrs.Card back to an immutable FSRSRecord."""
        return FSRSRecord(
            card_id=card_id,
            state=self.STATE_STR_MAP.get(card.state, "learning"),
            due=card.due,
            stability=round(card.stability, 4) if card.stability is not None else None,
            difficulty=round(card.difficulty, 4) if card.difficulty is not None else None,
            step=card.step or 0,
            reps=reps,
            lapses=lapses,
            last_review=card.last_review,
        )

    def schedule_review(
        self,
        record: FSRSRecord,
        rating: FsrsRating,
        now: datetime | None = None,
    ) -> FSRSRecord:
        """Process a review response and return the updated FSRSRecord."""
        review_time = now or datetime.now(UTC)
        card = self.record_to_fsrs_card(record)
        fsrs_rating = self.RATING_MAP[rating]

        # Review card through FSRS scheduler
        updated_card, _ = self.scheduler.review_card(card, fsrs_rating, review_time)

        reps = record.reps + 1
        lapses = record.lapses + (1 if rating == "again" else 0)

        return self.fsrs_card_to_record(record.card_id, updated_card, reps=reps, lapses=lapses)

    def get_due_items(
        self,
        records: list[FSRSRecord],
        now: datetime | None = None,
    ) -> list[FSRSRecord]:
        """Filter records that are due for review relative to the current timestamp."""
        check_time = now or datetime.now(UTC)
        return [r for r in records if r.due <= check_time]
