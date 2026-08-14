"""Round scheduler enforcing interleaving, sibling groups, heavy limits, and forecast caps."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import (
    DORMANCY_DAYS,
    DUEL_LENGTH,
    DUEL_MIN_ATTEMPTS_TO_SUGGEST,
    FORECAST_HORIZON_DAYS,
    FORECAST_LOAD_THRESHOLD_DEFAULT,
    MAX_HEAVY_PER_ROUND,
    MAX_NEW_TOPICS_PER_DAY,
    MAX_REVIEWS_PER_DAY,
    MIN_PREREQ_STABILITY,
    RECALIBRATION_ROUND_SIZE,
    ROUND_SIZE_DEFAULT,
    SPLIT_ACCURACY_GAP,
    SPLIT_MIN_ATTEMPTS_PER_FACET,
    SUGGESTION_MIN_ACTIVE_DAYS,
    THRESHOLD_CLAMP,
    VOCAB_RATIO_DEFAULT,
    BankItem,
    DayBudget,
    Difficulty,
    ReviewMode,
    TagStateModel,
    ThresholdSuggestion,
    Topic,
)
from src.engine.fsrs import FSRSRecord
from src.engine.topic_state import TopicStateManager

# A `requires_context` topic due alongside others must be spread across days
# rather than stuffing every such topic into one round (rule 11). Mirrors the
# MAX_HEAVY_PER_ROUND mechanism but tracks distinct requires_context topics.
MAX_CONTEXT_TOPICS_PER_ROUND: int = 1

# review_log rows in these modes feed FSRS / tag_state and count toward the
# daily new-topic and backlog budgets; duel and challenge rows never do.
_MEASUREMENT_MODES: frozenset[str] = frozenset({"review", "recalibration"})


class ItemBankProtocol(Protocol):
    """Structural interface the scheduler needs from an item bank.

    Kept as a Protocol (rather than importing ``SqliteItemBank`` directly) so
    the scheduler stays decoupled from the storage layer and testable with a
    lightweight in-memory fake.
    """

    def get_item(self, item_id: str) -> BankItem | None: ...

    def query_by_topic(self, topic_id: str, max_count: int | None = None) -> list[BankItem]: ...

    def query_by_difficulty(self, topic_id: str, difficulty: Difficulty) -> list[BankItem]: ...

    def get_all_items(self) -> list[BankItem]: ...

    def get_review_logs(
        self, topic_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]: ...


def _parse_timestamp(value: object) -> datetime | None:
    """Best-effort parse of a review_log `created_at` value into a UTC datetime.

    Accepts a ``datetime`` (as tests may inject directly) or the strings
    SQLite actually stores: ISO-8601, or its own ``CURRENT_TIMESTAMP`` default
    of ``YYYY-MM-DD HH:MM:SS``.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        for candidate in (text, text.replace(" ", "T", 1)):
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


class RoundPlan(BaseModel):
    """Execution plan for a learning round."""

    model_config = ConfigDict(frozen=True)
    items: list[BankItem]
    mode: ReviewMode
    new_topic_id: str | None = None
    is_overload_blocked: bool = False
    is_bonus: bool = False
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

    # Stability (days) bands mapped to the three difficulty tiers. Not spec'd
    # as an exact number anywhere; chosen to roughly track FSRS's own early
    # review cadence (1, 3, 7, 16 days) so a topic graduates tiers alongside
    # its own memory strength.
    _STABILITY_DIFFICULTY_BANDS: tuple[float, float] = (3.0, 10.0)

    def __init__(
        self,
        round_size: int = ROUND_SIZE_DEFAULT,
        forecast_threshold: int = FORECAST_LOAD_THRESHOLD_DEFAULT,
        bank: ItemBankProtocol | None = None,
        vocab_ratio: float = VOCAB_RATIO_DEFAULT,
    ) -> None:
        self.round_size = round_size
        self.forecast_threshold = forecast_threshold
        self.bank = bank
        self.vocab_ratio = min(max(vocab_ratio, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Forecast / day budget
    # ------------------------------------------------------------------

    @staticmethod
    def _project_daily_counts(
        due_dates: Sequence[datetime], now: datetime, horizon_days: int
    ) -> list[int]:
        """Bucket due dates into daily counts over `horizon_days`.

        A single, shared definition of overdue handling: anything already due
        (offset < 0) collapses into *today's* bucket (offset 0) instead of
        being dropped or scattered across its own (past) date. Overdue
        backlog is load the user is carrying right now, not zero load and not
        load thinly spread across history.
        """
        counts = [0] * horizon_days
        for due in due_dates:
            offset = max((due.date() - now.date()).days, 0)
            if offset < horizon_days:
                counts[offset] += 1
        return counts

    def forecast(
        self,
        states: list[TagStateModel],
        now: datetime,
        horizon_days: int = FORECAST_HORIZON_DAYS,
    ) -> list[int]:
        """Project daily review counts over horizon days, overdue collapsed into today."""
        return self._project_daily_counts([s.due_at for s in states], now, horizon_days)

    def forecast_7day_load(
        self,
        records: list[FSRSRecord],
        now: datetime | None = None,
    ) -> int:
        """Calculate maximum single-day review load over the next 7 days."""
        ref_time = now or datetime.now(UTC)
        counts = self._project_daily_counts(
            [r.due for r in records], ref_time, FORECAST_HORIZON_DAYS
        )
        return max(counts) if counts else 0

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

    # ------------------------------------------------------------------
    # Daily budgets derived from persisted review_logs (never a trusted
    # per-call argument -- see 00-index.md: "a DAILY budget, never per round")
    # ------------------------------------------------------------------

    @staticmethod
    def count_new_topics_today(review_logs: Sequence[Mapping[str, Any]], now: datetime) -> int:
        """Count distinct topics whose first-ever review row falls on `now`'s date.

        Derived from persisted state rather than a caller-supplied counter, so
        the budget survives across rounds within a day and resets naturally
        the next calendar day. Only measurement rows (`review`,
        `recalibration`) count; duel/challenge exposure never introduces a
        topic in the FSRS sense.
        """
        first_seen: dict[str, datetime] = {}
        for row in review_logs:
            topic_id = row.get("topic_id")
            if not isinstance(topic_id, str):
                continue
            if row.get("mode", "review") not in _MEASUREMENT_MODES:
                continue
            ts = _parse_timestamp(row.get("created_at"))
            if ts is None:
                continue
            if topic_id not in first_seen or ts < first_seen[topic_id]:
                first_seen[topic_id] = ts
        today = now.date()
        return sum(1 for ts in first_seen.values() if ts.date() == today)

    @staticmethod
    def _count_reviews_today(review_logs: Sequence[Mapping[str, Any]], now: datetime) -> int:
        today = now.date()
        count = 0
        for row in review_logs:
            if row.get("mode", "review") not in _MEASUREMENT_MODES:
                continue
            ts = _parse_timestamp(row.get("created_at"))
            if ts is not None and ts.date() == today:
                count += 1
        return count

    def cap_daily_backlog(
        self,
        due_records: list[FSRSRecord],
        review_logs: Sequence[Mapping[str, Any]],
        now: datetime,
    ) -> list[FSRSRecord]:
        """Cap today's presentable backlog at MAX_REVIEWS_PER_DAY, most-overdue first.

        Reviews already logged today count against the cap. The remainder of
        an oversized backlog slips to a later day (it stays due, and more
        overdue, so it sorts first next time) rather than piling into one
        sitting. FSRS copes with lateness; the person does not.
        """
        already_done = self._count_reviews_today(review_logs, now)
        budget = max(MAX_REVIEWS_PER_DAY - already_done, 0)
        ordered = sorted(due_records, key=lambda r: r.due)
        return ordered[:budget]

    # ------------------------------------------------------------------
    # Difficulty tier
    # ------------------------------------------------------------------

    @classmethod
    def _difficulty_tier(cls, stability: float) -> Difficulty:
        """Map current FSRS stability (days) to the item difficulty tier to retrieve."""
        low, high = cls._STABILITY_DIFFICULTY_BANDS
        if stability < low:
            return 1
        if stability < high:
            return 2
        return 3

    @staticmethod
    def _prereqs_stable(topic: Topic, topic_manager: TopicStateManager) -> bool:
        """A topic's prerequisites must all hold at least MIN_PREREQ_STABILITY."""
        for prereq_id in topic.prereqs:
            prereq_state = topic_manager.states.get(prereq_id)
            if prereq_state is None or prereq_state.fsrs_stability < MIN_PREREQ_STABILITY:
                return False
        return True

    # ------------------------------------------------------------------
    # Duel
    # ------------------------------------------------------------------

    @staticmethod
    def is_duel_available(
        confusion_group: str, topic_manager: TopicStateManager, bank: ItemBankProtocol
    ) -> bool:
        """A duel is available only once every member topic has been introduced."""
        member_topics = {
            it.topic_id for it in bank.get_all_items() if it.confusion_group == confusion_group
        }
        if not member_topics:
            return False
        return all(
            topic_manager.states.get(t_id) is not None
            and topic_manager.states[t_id].state in ("learning", "acquired")
            for t_id in member_topics
        )

    def build_duel(
        self,
        confusion_group: str,
        now: datetime | None = None,
        bank: ItemBankProtocol | None = None,
        topic_manager: TopicStateManager | None = None,
        seen_items: set[str] | None = None,
    ) -> list[BankItem]:
        """Construct a blocked duel round contrasting members of a confusion group.

        Prefers minimal-pair items (whose distractors imply the sibling
        topic) and already-seen items, since unseen items are the
        measurement pool and duel demand must not burn them. Never pads with
        items unrelated to the confusion group.
        """
        active_bank = bank or self.bank
        if not active_bank:
            return []

        if topic_manager is not None and not self.is_duel_available(
            confusion_group, topic_manager, active_bank
        ):
            return []

        seen = seen_items or set()
        group_items = [
            it for it in active_bank.get_all_items() if it.confusion_group == confusion_group
        ]
        if not group_items:
            return []

        member_topics = {it.topic_id for it in group_items}

        def is_minimal_pair(it: BankItem) -> bool:
            return any(
                d.implied_topic_id in member_topics and d.implied_topic_id != it.topic_id
                for d in it.distractors
            )

        ranked = sorted(
            group_items,
            key=lambda it: (0 if is_minimal_pair(it) else 1, 0 if it.id in seen else 1),
        )
        return self._interleave_items(ranked[:DUEL_LENGTH])

    def rank_duel_suggestions(
        self,
        bank: ItemBankProtocol,
        review_logs: Sequence[Mapping[str, Any]],
    ) -> list[tuple[str, float]]:
        """Rank confusion groups by empirical confusion rate, most confusable first.

        Confusion rate is how often a wrong answer on the group matches a
        distractor whose `implied_topic_id` is the *other* member topic (A
        answered as B), not raw accuracy -- a hard topic can have low accuracy
        without any A/B confusion. Groups below DUEL_MIN_ATTEMPTS_TO_SUGGEST
        total attempts are suppressed as insufficient evidence.
        """
        items_by_id = {it.id: it for it in bank.get_all_items()}
        attempts: dict[str, int] = defaultdict(int)
        confusions: dict[str, int] = defaultdict(int)

        for row in review_logs:
            item_id = row.get("item_id")
            item = items_by_id.get(item_id) if isinstance(item_id, str) else None
            if item is None or not item.confusion_group:
                continue
            group = item.confusion_group
            attempts[group] += 1
            if row.get("is_correct"):
                continue
            answer = str(row.get("user_answer", "")).strip()
            for d in item.distractors:
                if (
                    d.implied_topic_id
                    and d.implied_topic_id != item.topic_id
                    and d.text.strip() == answer
                ):
                    confusions[group] += 1
                    break

        ranked = [
            (group, confusions[group] / total)
            for group, total in attempts.items()
            if total >= DUEL_MIN_ATTEMPTS_TO_SUGGEST
        ]
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked

    # ------------------------------------------------------------------
    # Recalibration
    # ------------------------------------------------------------------

    def build_recalibration(
        self,
        states: list[TagStateModel],
        now: datetime | None = None,
        bank: ItemBankProtocol | None = None,
        seen_items: set[str] | None = None,
    ) -> list[BankItem]:
        """Sample mostly high-stability topics plus a couple from `learning`.

        High-stability topics are the ones most likely to have decayed
        unnoticed during the dormancy gap; a couple of `learning` topics are
        mixed in since those are the most fragile. Results feed FSRS
        normally -- genuine reviews, merely reordered.
        """
        active_bank = bank or self.bank
        if not active_bank:
            return []
        seen = seen_items or set()

        acquired = sorted(
            (s for s in states if s.state == "acquired"),
            key=lambda s: s.fsrs_stability,
            reverse=True,
        )
        learning = [s for s in states if s.state == "learning"]

        learning_slots = min(2, len(learning), RECALIBRATION_ROUND_SIZE)
        acquired_slots = RECALIBRATION_ROUND_SIZE - learning_slots
        selected_topics = [s.tag_id for s in acquired[:acquired_slots]] + [
            s.tag_id for s in learning[:learning_slots]
        ]

        picked: list[BankItem] = []
        picked_ids: set[str] = set()
        for t_id in selected_topics:
            for it in active_bank.query_by_topic(t_id):
                if it.id in seen or it.id in picked_ids:
                    continue
                picked.append(it)
                picked_ids.add(it.id)
                break
            if len(picked) >= RECALIBRATION_ROUND_SIZE:
                break

        return self._interleave_items(picked)

    # ------------------------------------------------------------------
    # Daily challenge
    # ------------------------------------------------------------------

    def build_challenge(
        self,
        topic_manager: TopicStateManager,
        bank: ItemBankProtocol | None = None,
        now: datetime | None = None,
    ) -> BankItem | None:
        """Pick the daily free-writing challenge topic deterministically.

        Chosen from `learning` topics, falling back to topics acquired in the
        last week, rotated by calendar date (never randomness) so the same
        state snapshot always yields the same topic on a given day. This sits
        outside the interleaved retrieval loop -- the one place the prompt
        may name the grammar point -- and never enters a review round.
        """
        active_bank = bank or self.bank
        if not active_bank:
            return None
        ref_time = now or datetime.now(UTC)

        learning_topics = sorted(
            t_id for t_id, s in topic_manager.states.items() if s.state == "learning"
        )
        recent_cutoff = ref_time - timedelta(days=7)
        recent_acquired = sorted(
            t_id
            for t_id, s in topic_manager.states.items()
            if s.state == "acquired"
            and s.last_review_at is not None
            and s.last_review_at >= recent_cutoff
        )
        candidates = learning_topics or recent_acquired
        if not candidates:
            return None

        day_index = ref_time.date().toordinal()
        topic_id = candidates[day_index % len(candidates)]

        items = active_bank.query_by_topic(topic_id)
        if not items:
            return None
        return items[day_index % len(items)]

    # ------------------------------------------------------------------
    # Interleaving
    # ------------------------------------------------------------------

    @staticmethod
    def _interleave_items(
        items: list[BankItem],
        sibling_group_by_topic: dict[str, str] | None = None,
    ) -> list[BankItem]:
        """Order items so consecutive items never share a `tag_id` or `sibling_group`.

        `tag_id` here is `BankItem.topic_id`, the identifier every other part
        of the system calls a "tag". Separation is enforced on that key alone
        -- `confusion_group` is a *different* axis: same-confusion_group
        neighbours are preferred (adjacency is what teaches the contrast),
        subject to sibling separation always outranking that preference
        (siblings are one rule at two cells; adjacency there is massing, not
        teaching).
        """
        if len(items) <= 1:
            return list(items)

        sibling_lookup = sibling_group_by_topic or {}

        def sibling_of(it: BankItem) -> str | None:
            return sibling_lookup.get(it.topic_id)

        buckets: dict[str, list[BankItem]] = defaultdict(list)
        for it in items:
            buckets[it.topic_id].append(it)
        bucket_list = sorted(buckets.values(), key=len, reverse=True)

        interleaved: list[BankItem] = []
        remaining = len(items)

        def collides(cand: BankItem, last: BankItem | None) -> bool:
            if last is None:
                return False
            if cand.topic_id == last.topic_id:
                return True
            last_sib = sibling_of(last)
            return last_sib is not None and sibling_of(cand) == last_sib

        while remaining > 0:
            last = interleaved[-1] if interleaved else None
            last_confusion = last.confusion_group if last else None

            chosen_idx: int | None = None

            # Prefer a candidate sharing the previous item's confusion_group.
            if last_confusion:
                for idx, bucket in enumerate(bucket_list):
                    if (
                        bucket
                        and not collides(bucket[0], last)
                        and bucket[0].confusion_group == last_confusion
                    ):
                        chosen_idx = idx
                        break

            # Otherwise any non-colliding candidate.
            if chosen_idx is None:
                for idx, bucket in enumerate(bucket_list):
                    if bucket and not collides(bucket[0], last):
                        chosen_idx = idx
                        break

            # Fallback: every remaining candidate collides (e.g. one topic left).
            if chosen_idx is None:
                for idx, bucket in enumerate(bucket_list):
                    if bucket:
                        chosen_idx = idx
                        break

            assert chosen_idx is not None
            interleaved.append(bucket_list[chosen_idx].pop(0))
            remaining -= 1
            bucket_list.sort(key=len, reverse=True)

        return interleaved

    # ------------------------------------------------------------------
    # Round assembly
    # ------------------------------------------------------------------

    def plan_next_round(
        self,
        bank: ItemBankProtocol,
        topic_manager: TopicStateManager,
        fsrs_records: dict[str, FSRSRecord],
        mode: ReviewMode = "review",
        now: datetime | None = None,
        last_active_date: datetime | None = None,
        confusion_group: str | None = None,
        seen_items: set[str] | None = None,
        review_logs: Sequence[Mapping[str, Any]] | None = None,
    ) -> RoundPlan:
        """Generate a constrained, interleaved set of items for the practice round."""
        ref_time = now or datetime.now(UTC)
        notes: list[str] = []
        seen = seen_items or set()
        sibling_lookup = {
            t.id: t.sibling_group for t in topic_manager.topics.values() if t.sibling_group
        }

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
                states=list(topic_manager.states.values()),
                now=ref_time,
                bank=bank,
                seen_items=seen,
            )
            return RoundPlan(items=raw_items, mode="recalibration", notes=notes)

        if mode == "duel":
            raw_items = self.build_duel(
                confusion_group or "",
                now=ref_time,
                bank=bank,
                topic_manager=topic_manager,
                seen_items=seen,
            )
            if not raw_items:
                notes.append(
                    "Duel unavailable: unknown confusion group, or member topics "
                    "not yet introduced."
                )
            return RoundPlan(items=raw_items, mode="duel", notes=notes)

        if mode == "challenge":
            challenge_item = self.build_challenge(topic_manager, bank=bank, now=ref_time)
            challenge_items = [challenge_item] if challenge_item else []
            if not challenge_items:
                notes.append("No challenge topic available yet.")
            return RoundPlan(items=challenge_items, mode="challenge", notes=notes)

        # --- Standard review round ---
        effective_logs: Sequence[Mapping[str, Any]] = (
            review_logs if review_logs is not None else bank.get_review_logs(limit=10000)
        )

        planned_items: list[BankItem] = []
        planned_ids: set[str] = set()
        heavy_block_keys: set[str] = set()
        heavy_count = 0
        context_topic_ids: set[str] = set()
        vocab_count = 0
        target_vocab = round(self.round_size * self.vocab_ratio) if self.vocab_ratio > 0 else 0

        def try_add(
            item: BankItem, *, enforce_seen: bool = False, respect_vocab_cap: bool = True
        ) -> bool:
            nonlocal heavy_count, vocab_count
            if len(planned_items) >= self.round_size:
                return False
            if item.id in planned_ids:
                return False
            if enforce_seen and item.id in seen:
                return False
            # Rule 8: grammar/vocab ratio. 0.0 disables vocabulary entirely.
            if item.dimension == "vocab":
                if self.vocab_ratio <= 0:
                    return False
                if respect_vocab_cap and target_vocab > 0 and vocab_count >= target_vocab:
                    return False

            topic = topic_manager.topics.get(item.topic_id)
            if topic is not None:
                # Eligibility beats pacing: a hard correctness constraint.
                if topic.eligible_types and item.type not in topic.eligible_types:
                    return False
                # Rule 2: prerequisites must hold stability, not merely be "acquired".
                if not self._prereqs_stable(topic, topic_manager):
                    return False

            # Rule 9 + 10: a paragraph block (grouped by block_id) is ONE heavy
            # unit no matter how many gaps it has; a production item is its
            # own heavy unit. Each gap still counts individually toward
            # round_size once its block has been admitted.
            block_key: str | None = None
            if item.type == "production":
                block_key = f"prod:{item.id}"
            elif item.type == "paragraph_cloze":
                block_key = f"block:{item.block_id or item.id}"

            is_new_heavy_unit = block_key is not None and block_key not in heavy_block_keys
            if is_new_heavy_unit and heavy_count >= MAX_HEAVY_PER_ROUND:
                return False

            # Rule 11: at most one requires_context topic per round; the rest
            # spread across later rounds/days rather than clustering here.
            if (
                topic is not None
                and topic.requires_context
                and item.topic_id not in context_topic_ids
                and len(context_topic_ids) >= MAX_CONTEXT_TOPICS_PER_ROUND
            ):
                return False

            planned_items.append(item)
            planned_ids.add(item.id)
            if block_key is not None:
                if is_new_heavy_unit:
                    heavy_count += 1
                heavy_block_keys.add(block_key)
            if topic is not None and topic.requires_context:
                context_topic_ids.add(item.topic_id)
            if item.dimension == "vocab":
                vocab_count += 1
            return True

        # Rule 1: due tags, ordered by overdue ratio. Rule: backlog capped at
        # MAX_REVIEWS_PER_DAY across the whole day, most overdue first.
        due_records_raw = [r for r in fsrs_records.values() if r.due <= ref_time]
        due_records = self.cap_daily_backlog(due_records_raw, effective_logs, ref_time)
        due_records.sort(
            key=lambda r: (ref_time - r.due).total_seconds() / max(r.stability or 1.0, 0.1),
            reverse=True,
        )

        deferred_vocab_due: list[BankItem] = []
        for r in due_records:
            item = bank.get_item(r.card_id)
            if item is None:
                continue
            if not try_add(item) and item.dimension == "vocab" and self.vocab_ratio > 0:
                deferred_vocab_due.append(item)

        # Reclaim due vocab items skipped only by the soft ratio cap if
        # grammar due items didn't fill the round -- pacing is soft, due
        # obligations still take priority over leaving capacity unused.
        for item in deferred_vocab_due:
            if len(planned_items) >= self.round_size:
                break
            try_add(item, respect_vocab_cap=False)

        # 5. Inject 1 new topic if capacity remains and not overload blocked.
        # Rule 7: the introduction budget is a DAILY total derived from
        # review_logs, never a per-round counter.
        new_topic_id: str | None = None
        day_new_topics_count = self.count_new_topics_today(effective_logs, ref_time)
        if (
            not overload_blocked
            and len(planned_items) < self.round_size
            and day_new_topics_count < MAX_NEW_TOPICS_PER_DAY
        ):
            ready_topics = [
                t_id
                for t_id, s in topic_manager.states.items()
                if s.state == "ready"
                and (
                    t_id not in topic_manager.topics
                    or self._prereqs_stable(topic_manager.topics[t_id], topic_manager)
                )
            ]
            for candidate_topic in ready_topics:
                tier = self._difficulty_tier(topic_manager.states[candidate_topic].fsrs_stability)
                candidates = [
                    it
                    for it in bank.query_by_difficulty(candidate_topic, difficulty=tier)
                    if it.id not in seen
                ]
                if not candidates:
                    candidates = [
                        it for it in bank.query_by_topic(candidate_topic) if it.id not in seen
                    ]

                introduced = False
                for it in candidates:
                    if try_add(it, enforce_seen=True):
                        introduced = True
                        break
                if introduced:
                    new_topic_id = candidate_topic
                    topic_manager.start_topic(candidate_topic, now=ref_time)
                    notes.append(f"Introduced new topic: '{candidate_topic}'")
                    break

        # 12. Bonus rounds, past the due queue: least-stable already-introduced
        # topics, never pulling forward a card that already has a schedule.
        is_bonus = False
        if len(planned_items) < self.round_size:
            before_count = len(planned_items)
            bonus_states = sorted(
                (
                    s
                    for t_id, s in topic_manager.states.items()
                    if s.state in ("learning", "acquired")
                    and (
                        t_id not in topic_manager.topics
                        or self._prereqs_stable(topic_manager.topics[t_id], topic_manager)
                    )
                ),
                key=lambda s: s.fsrs_stability,
            )
            for s in bonus_states:
                if len(planned_items) >= self.round_size:
                    break
                for it in bank.query_by_topic(s.tag_id):
                    if it.id in fsrs_records:
                        continue
                    if try_add(it, enforce_seen=True):
                        break
            if len(planned_items) > before_count:
                is_bonus = True
                notes.append(
                    "Bonus round: due queue is empty, drawing extra practice "
                    "from least-stable topics."
                )

        interleaved_plan = self._interleave_items(
            planned_items, sibling_group_by_topic=sibling_lookup
        )

        return RoundPlan(
            items=interleaved_plan,
            mode=mode,
            new_topic_id=new_topic_id,
            is_overload_blocked=overload_blocked,
            is_bonus=is_bonus,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Threshold suggestion
    # ------------------------------------------------------------------

    def compute_threshold_suggestion(
        self,
        daily_completions: Mapping[date, int],
        daily_cleared: Mapping[date, bool],
        now: datetime,
        window_days: int = 14,
    ) -> ThresholdSuggestion | None:
        """Suggest a forecast-load threshold from recent history, or None if too thin.

        `daily_completions` maps a calendar date to items completed that day
        (only present for days with any activity). `daily_cleared` marks
        which of those days ran into a bonus round -- i.e. genuinely
        exhausted the queue rather than being censored by supply. A number
        derived from too few data points is worse than no number, so below
        SUGGESTION_MIN_ACTIVE_DAYS this returns `None` rather than a guess.
        """
        window_start = now.date() - timedelta(days=window_days)
        active = {
            d: c for d, c in daily_completions.items() if window_start <= d <= now.date() and c > 0
        }
        active_days = len(active)
        if active_days < SUGGESTION_MIN_ACTIVE_DAYS:
            return None

        # Censored by supply: a day's completed count only proves a lower
        # bound on capacity, but it is still usable data for the median.
        median_items = float(median(active.values()))
        # Zero-activity days are excluded from the median above but still
        # count in the denominator here, since skipped days concentrate load
        # into the sittings that do happen.
        active_day_rate = active_days / window_days

        cleared_count = sum(1 for d in active if daily_cleared.get(d, False))
        clear_rate = cleared_count / active_days

        if clear_rate >= 0.85:
            clear_adjustment = 1.1
        elif clear_rate >= 0.60:
            clear_adjustment = 1.0
        else:
            clear_adjustment = 0.8

        raw = median_items * active_day_rate * clear_adjustment
        low, high = THRESHOLD_CLAMP
        suggested_val = int(round(min(max(raw, low), high)))

        return ThresholdSuggestion(
            suggested=suggested_val,
            suggested_threshold=suggested_val,
            window_days=window_days,
            active_days=active_days,
            median_items_per_active_day=median_items,
            median_reviews_per_active_day=median_items,
            active_day_rate=round(active_day_rate, 4),
            clear_rate=round(clear_rate, 4),
            reason=(
                f"{active_days} active day(s) in the last {window_days}; "
                f"clear_rate={clear_rate:.2f}"
            ),
        )

    # ------------------------------------------------------------------
    # Split detection
    # ------------------------------------------------------------------

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
