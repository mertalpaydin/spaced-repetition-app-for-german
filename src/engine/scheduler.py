"""Session scheduler and round pacing engine."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from src.bank.storage import SqliteItemBank
from src.contracts import (
    DORMANCY_DAYS,
    DUEL_LENGTH,
    FORECAST_HORIZON_DAYS,
    FORECAST_LOAD_THRESHOLD_DEFAULT,
    MAX_HEAVY_PER_ROUND,
    MAX_NEW_TOPICS_PER_DAY,
    RECALIBRATION_ROUND_SIZE,
    ROUND_SIZE_DEFAULT,
    BankItem,
    ReviewMode,
)
from src.engine.fsrs import FSRSRecord
from src.engine.topic_state import TopicStateManager


class RoundPlan(BaseModel):
    """Execution plan for a learning round."""

    model_config = ConfigDict(frozen=True)
    items: list[BankItem]
    mode: ReviewMode
    new_topic_id: str | None = None
    is_overload_blocked: bool = False
    notes: list[str] = Field(default_factory=list)


class LearningScheduler:
    """Orchestrates item selection, round size constraints, pacing, and forecast limits."""

    def __init__(
        self,
        round_size: int = ROUND_SIZE_DEFAULT,
        forecast_threshold: int = FORECAST_LOAD_THRESHOLD_DEFAULT,
    ) -> None:
        self.round_size = round_size
        self.forecast_threshold = forecast_threshold

    def forecast_7day_load(
        self,
        records: list[FSRSRecord],
        now: datetime | None = None,
    ) -> int:
        """Calculate total reviews due over the next 7 days."""
        ref_time = now or datetime.now(UTC)
        horizon = ref_time + timedelta(days=FORECAST_HORIZON_DAYS)
        return sum(1 for r in records if r.due <= horizon)

    def plan_next_round(
        self,
        bank: SqliteItemBank,
        topic_manager: TopicStateManager,
        fsrs_records: dict[str, FSRSRecord],
        mode: ReviewMode = "review",
        day_new_topics_count: int = 0,
        now: datetime | None = None,
        last_active_date: datetime | None = None,
    ) -> RoundPlan:
        """Generate a constrained set of items for the upcoming practice session."""
        ref_time = now or datetime.now(UTC)
        notes: list[str] = []

        # 1. Dormancy check -> switch to recalibration mode
        if last_active_date and (ref_time - last_active_date).days >= DORMANCY_DAYS:
            mode = "recalibration"
            notes.append(
                f"Dormancy detected (>= {DORMANCY_DAYS} days). Switching to Recalibration mode."
            )

        # 2. Check 7-day overload forecast limit
        forecast_load = self.forecast_7day_load(list(fsrs_records.values()), now=ref_time)
        overload_blocked = forecast_load > self.forecast_threshold
        if overload_blocked:
            notes.append(
                f"7-day review forecast ({forecast_load}) "
                f"exceeds threshold ({self.forecast_threshold})."
            )

        # 3. Handle specific review modes
        if mode == "recalibration":
            items = bank.get_all_items()[:RECALIBRATION_ROUND_SIZE]
            return RoundPlan(items=items, mode="recalibration", notes=notes)

        if mode == "duel":
            items = bank.get_all_items()[:DUEL_LENGTH]
            return RoundPlan(items=items, mode="duel", notes=notes)

        # 4. Standard Review Round: prioritize due items, inject 1 new topic if eligible
        planned_items: list[BankItem] = []
        new_topic_id: str | None = None

        # Find due FSRS cards
        due_card_ids = [r.card_id for r in fsrs_records.values() if r.due <= ref_time]
        for c_id in due_card_ids:
            it = bank.get_item(c_id)
            if it:
                planned_items.append(it)
            if len(planned_items) >= self.round_size:
                break

        # If capacity remains and not overload blocked, inject from a 'ready' topic
        if (
            len(planned_items) < self.round_size
            and not overload_blocked
            and day_new_topics_count < MAX_NEW_TOPICS_PER_DAY
        ):
            ready_topics = [t_id for t_id, s in topic_manager.states.items() if s.state == "ready"]
            if ready_topics:
                new_topic_id = ready_topics[0]
                new_items = bank.query_by_topic(
                    new_topic_id, max_count=self.round_size - len(planned_items)
                )
                planned_items.extend(new_items)
                notes.append(f"Introducing new topic: '{new_topic_id}'.")

        # Fill remaining slots with review items from active topics
        if len(planned_items) < self.round_size:
            all_bank = bank.get_all_items()
            for it in all_bank:
                if it not in planned_items:
                    planned_items.append(it)
                if len(planned_items) >= self.round_size:
                    break

        # Heavy item constraint: max 1 heavy item per round
        heavy_count = 0
        final_items: list[BankItem] = []
        for it in planned_items:
            is_heavy = it.type == "paragraph_cloze" or it.block_id is not None
            if is_heavy:
                if heavy_count < MAX_HEAVY_PER_ROUND:
                    heavy_count += 1
                    final_items.append(it)
            else:
                final_items.append(it)

        return RoundPlan(
            items=final_items[: self.round_size],
            mode=mode,
            new_topic_id=new_topic_id,
            is_overload_blocked=overload_blocked,
            notes=notes,
        )
