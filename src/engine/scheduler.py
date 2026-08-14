"""Round scheduler enforcing interleaving, sibling groups, heavy limits, and forecast caps."""

from collections import defaultdict
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
    SPLIT_ACCURACY_GAP,
    SPLIT_MIN_ATTEMPTS_PER_FACET,
    BankItem,
    DayBudget,
    ReviewMode,
    TagStateModel,
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


class SplitCandidate(BaseModel):
    """Candidate topic identified for morphological split."""

    model_config = ConfigDict(frozen=True)
    topic_id: str
    axis: str
    facet_accuracies: dict[str, float]
    gap: float


class LearningScheduler:
    """Orchestrates item selection, round size constraints, pacing, and forecast limits."""

    def __init__(
        self,
        round_size: int = ROUND_SIZE_DEFAULT,
        forecast_threshold: int = FORECAST_LOAD_THRESHOLD_DEFAULT,
        bank: SqliteItemBank | None = None,
    ) -> None:
        self.round_size = round_size
        self.forecast_threshold = forecast_threshold
        self.bank = bank

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

    def forecast(
        self,
        states: list[TagStateModel],
        now: datetime,
        horizon_days: int = FORECAST_HORIZON_DAYS,
    ) -> list[int]:
        """Project daily review counts over horizon days."""
        daily_counts = [0] * horizon_days
        for s in states:
            if s.due_at <= now + timedelta(days=horizon_days):
                day_offset = (s.due_at.date() - now.date()).days
                if 0 <= day_offset < horizon_days:
                    daily_counts[day_offset] += 1
        return daily_counts

    def day_budget(self, states: list[TagStateModel], now: datetime) -> DayBudget:
        """Compute the day's due count, ceiling, and allowed new topic introductions."""
        fc = self.forecast(states, now, FORECAST_HORIZON_DAYS)
        due_today = sum(1 for s in states if s.due_at.date() <= now.date())
        overload = max(fc) > self.forecast_threshold if fc else False
        new_allowed = 0 if overload else MAX_NEW_TOPICS_PER_DAY
        ceiling = due_today + new_allowed * self.round_size + 6

        return DayBudget(
            due_count=due_today,
            ceiling=ceiling,
            new_topics_allowed=new_allowed,
            forecast=fc,
        )

    def build_duel(
        self,
        confusion_group: str,
        now: datetime | None = None,
        bank: SqliteItemBank | None = None,
    ) -> list[BankItem]:
        """Construct a blocked duel round contrasting members of a confusion group."""
        active_bank = bank or self.bank
        if not active_bank:
            return []
        all_items = active_bank.get_all_items()
        duel_items = [it for it in all_items if it.confusion_group == confusion_group]
        if len(duel_items) < DUEL_LENGTH:
            duel_items.extend([it for it in all_items if it not in duel_items])
        return self._interleave_items(duel_items[:DUEL_LENGTH])

    def build_recalibration(
        self,
        states: list[TagStateModel],
        now: datetime | None = None,
        bank: SqliteItemBank | None = None,
    ) -> list[BankItem]:
        """Sample high-stability and active learning items to rebuild decayed queue."""
        active_bank = bank or self.bank
        if not active_bank:
            return []
        all_items = active_bank.get_all_items()
        return self._interleave_items(all_items[:RECALIBRATION_ROUND_SIZE])

    @staticmethod
    def _interleave_items(items: list[BankItem]) -> list[BankItem]:
        """Interleave items so that no two consecutive items share tag_id or sibling_group."""
        if len(items) <= 1:
            return items

        topic_buckets: dict[str, list[BankItem]] = defaultdict(list)
        for it in items:
            key = it.confusion_group or it.topic_id
            topic_buckets[key].append(it)

        interleaved: list[BankItem] = []
        buckets_list = sorted(topic_buckets.values(), key=len, reverse=True)

        while any(buckets_list):
            for bucket in buckets_list:
                if bucket:
                    cand = bucket[0]
                    cand_key = cand.confusion_group or cand.topic_id
                    last_key = (
                        (interleaved[-1].confusion_group or interleaved[-1].topic_id)
                        if interleaved
                        else None
                    )

                    if not interleaved or last_key != cand_key:
                        interleaved.append(bucket.pop(0))
                    elif len(buckets_list) > 1:
                        alt_found = False
                        for alt_bucket in buckets_list:
                            if alt_bucket:
                                alt_cand = alt_bucket[0]
                                alt_key = alt_cand.confusion_group or alt_cand.topic_id
                                if alt_key != last_key:
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
        confusion_group: str | None = None,
    ) -> RoundPlan:
        """Generate a constrained, interleaved set of items for the practice round."""
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
            raw_items = self.build_recalibration(
                states=list(topic_manager.states.values()), now=ref_time, bank=bank
            )
            return RoundPlan(items=raw_items, mode="recalibration", notes=notes)

        if mode == "duel":
            raw_items = self.build_duel(confusion_group or "", now=ref_time, bank=bank)
            return RoundPlan(items=raw_items, mode="duel", notes=notes)

        # 4. Standard Review Round: prioritize due items ordered by overdue ratio
        planned_items: list[BankItem] = []
        new_topic_id: str | None = None
        heavy_count = 0

        due_records = [r for r in fsrs_records.values() if r.due <= ref_time]
        due_records.sort(
            key=lambda r: (ref_time - r.due).total_seconds() / max(r.stability or 1.0, 0.1),
            reverse=True,
        )

        for r in due_records:
            item = bank.get_item(r.card_id)
            if item:
                # Heavy item constraint: paragraph cloze OR production
                is_heavy = item.type in ("paragraph_cloze", "production")
                if is_heavy and heavy_count >= MAX_HEAVY_PER_ROUND:
                    continue
                if is_heavy:
                    heavy_count += 1
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

        # 6. Fill remaining quota
        if len(planned_items) < self.round_size:
            all_bank_items = bank.get_all_items()
            planned_ids = {it.id for it in planned_items}
            for it in all_bank_items:
                if it.id not in planned_ids:
                    is_heavy = it.type in ("paragraph_cloze", "production")
                    if is_heavy and heavy_count >= MAX_HEAVY_PER_ROUND:
                        continue
                    if is_heavy:
                        heavy_count += 1
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

    def detect_split_candidates(
        self,
        review_history: list[dict[str, str | bool | None]],
    ) -> list[SplitCandidate]:
        """Detect topics where facet accuracy spread exceeds SPLIT_ACCURACY_GAP."""
        facet_stats: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
        for row in review_history:
            t_id = str(row.get("topic_id"))
            facet = str(row.get("facet") or "default")
            is_corr = bool(row.get("is_correct", False))
            facet_stats[t_id][facet].append(is_corr)

        candidates: list[SplitCandidate] = []
        for t_id, facets in facet_stats.items():
            qualifying_facets = {
                f: results
                for f, results in facets.items()
                if len(results) >= SPLIT_MIN_ATTEMPTS_PER_FACET
            }
            if len(qualifying_facets) >= 2:
                accuracies = {f: sum(res) / len(res) for f, res in qualifying_facets.items()}
                max_acc = max(accuracies.values())
                min_acc = min(accuracies.values())
                gap = max_acc - min_acc
                if gap >= SPLIT_ACCURACY_GAP:
                    candidates.append(
                        SplitCandidate(
                            topic_id=t_id,
                            axis="Morphology",
                            facet_accuracies=accuracies,
                            gap=round(gap, 3),
                        )
                    )
        return candidates
