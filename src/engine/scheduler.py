"""Session scheduler enforcing interleaving, overdue sorting, and forecast caps."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from src.bank.storage import SqliteItemBank
from src.contracts import (
    DORMANCY_DAYS,
    DUEL_LENGTH,
    FORECAST_HORIZON_DAYS,
    FORECAST_LOAD_THRESHOLD_DEFAULT,
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
        """Calculate maximum single-day review load over the next 7 days."""
        ref_time = now or datetime.now(UTC)
        daily_counts: dict[str, int] = defaultdict(int)

        for d in range(FORECAST_HORIZON_DAYS):
            day_date = (ref_time + timedelta(days=d)).date().isoformat()
            daily_counts[day_date] = 0

        for r in records:
            if r.due <= ref_time + timedelta(days=FORECAST_HORIZON_DAYS):
                r_date = r.due.date().isoformat()
                daily_counts[r_date] += 1

        return max(daily_counts.values()) if daily_counts else 0

    @staticmethod
    def _interleave_items(items: list[BankItem]) -> list[BankItem]:
        """Interleave items so that no two consecutive items share the same topic_id."""
        if len(items) <= 1:
            return items

        topic_buckets: dict[str, list[BankItem]] = defaultdict(list)
        for it in items:
            topic_buckets[it.topic_id].append(it)

        interleaved: list[BankItem] = []
        buckets_list = sorted(topic_buckets.values(), key=len, reverse=True)

        while any(buckets_list):
            for bucket in buckets_list:
                if bucket:
                    # Avoid back-to-back same topic if possible
                    if not interleaved or interleaved[-1].topic_id != bucket[0].topic_id:
                        interleaved.append(bucket.pop(0))
                    elif len(buckets_list) > 1:
                        # Find an alternate bucket
                        alt_found = False
                        for alt_bucket in buckets_list:
                            if alt_bucket and alt_bucket[0].topic_id != interleaved[-1].topic_id:
                                interleaved.append(alt_bucket.pop(0))
                                alt_found = True
                                break
                        if not alt_found:
                            interleaved.append(bucket.pop(0))
                    else:
                        interleaved.append(bucket.pop(0))

        return interleaved

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
        """Generate a constrained, interleaved set of items for the practice session."""
        ref_time = now or datetime.now(UTC)
        notes: list[str] = []

        # 1. Dormancy check -> switch to recalibration mode
        if last_active_date and (ref_time - last_active_date).days >= DORMANCY_DAYS:
            mode = "recalibration"
            notes.append(
                f"Dormancy detected (>= {DORMANCY_DAYS} days). Switching to Recalibration mode."
            )

        # 2. Check 7-day overload forecast limit
        peak_daily_forecast = self.forecast_7day_load(list(fsrs_records.values()), now=ref_time)
        overload_blocked = peak_daily_forecast > self.forecast_threshold
        if overload_blocked:
            notes.append(
                f"Peak daily review forecast ({peak_daily_forecast}) "
                f"exceeds threshold ({self.forecast_threshold}). Halting new topic introductions."
            )

        # 3. Handle specific review modes
        if mode == "recalibration":
            raw_items = bank.get_all_items()[:RECALIBRATION_ROUND_SIZE]
            return RoundPlan(
                items=self._interleave_items(raw_items), mode="recalibration", notes=notes
            )

        if mode == "duel":
            raw_items = bank.get_all_items()[:DUEL_LENGTH]
            return RoundPlan(items=self._interleave_items(raw_items), mode="duel", notes=notes)

        # 4. Standard Review Round: prioritize due items ordered by overdue ratio
        planned_items: list[BankItem] = []
        new_topic_id: str | None = None

        due_records = [r for r in fsrs_records.values() if r.due <= ref_time]
        # Sort by overdue ratio: (now - due) / stability (highest overdue priority first)
        due_records.sort(
            key=lambda r: (ref_time - r.due).total_seconds() / max(r.stability or 1.0, 0.1),
            reverse=True,
        )

        for r in due_records:
            item = bank.get_item(r.card_id)
            if item:
                planned_items.append(item)
            if len(planned_items) >= self.round_size:
                break

        # 5. Inject 1 new topic if capacity remains and not overload blocked
        if (
            not overload_blocked
            and len(planned_items) < self.round_size
            and day_new_topics_count < MAX_NEW_TOPICS_PER_DAY
        ):
            ready_topics = [t_id for t_id, s in topic_manager.states.items() if s.state == "ready"]
            if ready_topics:
                candidate_topic = ready_topics[0]
                new_topic_items = bank.query_by_difficulty(candidate_topic, difficulty=1)
                if not new_topic_items:
                    new_topic_items = bank.query_by_topic(candidate_topic)

                if new_topic_items:
                    new_topic_id = candidate_topic
                    topic_manager.start_topic(candidate_topic)
                    planned_items.append(new_topic_items[0])
                    notes.append(f"Introduced new topic: '{candidate_topic}'")

        # 6. Fill remaining quota from active learning items
        if len(planned_items) < self.round_size:
            all_bank_items = bank.get_all_items()
            planned_ids = {it.id for it in planned_items}
            for it in all_bank_items:
                if it.id not in planned_ids:
                    planned_items.append(it)
                    planned_ids.add(it.id)
                if len(planned_items) >= self.round_size:
                    break

        interleaved_plan = self._interleave_items(planned_items)

        return RoundPlan(
            items=interleaved_plan,
            mode=mode,
            new_topic_id=new_topic_id,
            is_overload_blocked=overload_blocked,
            notes=notes,
        )
