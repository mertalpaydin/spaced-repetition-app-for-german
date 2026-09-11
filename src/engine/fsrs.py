"""FSRS algorithm wrapper for memory state tracking and review interval scheduling."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from fsrs import Card, Rating, Scheduler, State
from pydantic import BaseModel, ConfigDict

from src.contracts import FsrsRating


class FSRSRecord(BaseModel):
    """Memory state of a single card/item under the FSRS algorithm.

    ``due`` has no default: the clock is always injected by the caller rather
    than captured implicitly at construction time (see ``CLAUDE.md``).
    """

    model_config = ConfigDict(frozen=True)
    card_id: str
    state: str = "learning"  # learning, review, relearning
    due: datetime
    stability: float | None = None
    difficulty: float | None = None
    step: int | None = 0
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

    #: One learning step of ten minutes instead of the library's (1 min,
    #: 10 min): with two steps a new unit came back twice within a session,
    #: which the first learner found repetitive (feedback, 2026-09-11).
    DEFAULT_LEARNING_STEPS: tuple[timedelta, ...] = (timedelta(minutes=10),)

    def __init__(
        self,
        w: Sequence[float] | None = None,
        request_retention: float = 0.9,
        maximum_interval: int = 36500,
        learning_steps: Sequence[timedelta] | None = None,
    ) -> None:
        steps = tuple(learning_steps) if learning_steps is not None else self.DEFAULT_LEARNING_STEPS
        if w is not None:
            self.scheduler = Scheduler(
                parameters=tuple(w),
                desired_retention=request_retention,
                maximum_interval=maximum_interval,
                learning_steps=steps,
            )
        else:
            self.scheduler = Scheduler(
                desired_retention=request_retention,
                maximum_interval=maximum_interval,
                learning_steps=steps,
            )

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
        self, card: Card, card_id: str, reps: int = 0, lapses: int = 0
    ) -> FSRSRecord:
        """Convert an updated fsrs.Card back to a persistent FSRSRecord."""
        state_str = self.STATE_STR_MAP.get(card.state, "learning")
        return FSRSRecord(
            card_id=card_id,
            state=state_str,
            due=card.due,
            stability=card.stability,
            difficulty=card.difficulty,
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
        """Process an exercise attempt and compute new stability, difficulty, and due date."""
        fsrs_card = self.record_to_fsrs_card(record)
        fsrs_rating = self.RATING_MAP[rating]
        review_time = now or datetime.now(UTC)

        updated_card, _ = self.scheduler.review_card(fsrs_card, fsrs_rating, review_time)
        new_reps = record.reps + 1
        new_lapses = record.lapses + (1 if rating == "again" else 0)

        return self.fsrs_card_to_record(
            updated_card, card_id=record.card_id, reps=new_reps, lapses=new_lapses
        )

    def get_retrievability(self, record: FSRSRecord, now: datetime | None = None) -> float:
        """Estimate current retrievability probability R in [0.0, 1.0]."""
        if record.stability is None or record.last_review is None:
            return 1.0

        ref_time = now or datetime.now(UTC)
        fsrs_card = self.record_to_fsrs_card(record)
        ret = self.scheduler.get_card_retrievability(fsrs_card, ref_time)
        return float(round(float(ret or 1.0), 4))
