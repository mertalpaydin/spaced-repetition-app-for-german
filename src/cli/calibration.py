"""Adaptive Kalibrierung (initial assessment) protocol implementation.

The walk starts mid-taxonomy (B1), branches up or down a CEFR band on two
consecutive topic-level outcomes, propagates a pass down the prerequisite DAG
(inference) and a fail up the DAG as suspicion (prioritising a representative
descendant), requires 2 of 2 correct on a topic before crediting it directly,
and is hard-bounded at ``MAX_ITEMS`` regardless of ability profile.

``CalibrationRunner.next_item`` is pure: it recomputes the walk state from the
full ``answered`` history on every call rather than mutating hidden state, so
the same history always produces the same next probe (and tests can replay a
prefix without re-running the whole session). ``finalise`` performs the one
side-effecting pass over ``TopicStateManager`` once the walk is complete.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.bank.storage import SqliteItemBank
from src.contracts import CEFR, Answer, BankItem
from src.engine.topic_state import TopicStateManager
from src.taxonomy.validator import TaxonomyValidator

CEFR_ORDER: list[CEFR] = ["A1", "A2", "B1", "B2"]
START_LEVEL: CEFR = "B1"
MAX_ITEMS = 35
ITEMS_PER_TOPIC = 2
LEVEL_SHIFT_STREAK = 2

# FSRS seeding from Kalibrierung (docs/plan/german-grammar-app-plan.md #7):
#   2/2 correct  -> acquired (kalibrierung), ~10 days
#   1/2 correct  -> learning, ~7 days
#   0/2 correct  -> unseen, no stability
#   inferred     -> acquired (inferred), 4 day hard ceiling (topic_state.py)
KALIBRIERUNG_ACQUIRED_STABILITY_DAYS = 10.0
PARTIAL_LEARNING_STABILITY_DAYS = 7.0
INFERRED_ACQUIRED_STABILITY_DAYS = 4.0


class CalibrationResult(BaseModel):
    """Outcome summary of a completed Kalibrierung walk."""

    model_config = ConfigDict(frozen=True)
    estimated_cefr: CEFR
    total_items: int
    correct_count: int
    score: float
    acquired_topics: list[str] = Field(default_factory=list)
    learning_topics: list[str] = Field(default_factory=list)
    directly_tested_topics: list[str] = Field(default_factory=list)
    inferred_topics: list[str] = Field(default_factory=list)


@dataclass
class _WalkState:
    """Result of replaying an ``answered`` history through the adaptive walk."""

    level_idx: int
    resolved: set[str]
    topic_order: list[str]
    verdicts: dict[str, list[bool]]
    items_used: int
    pending_topic: str | None
    finished: bool
    inferred_from_pass: dict[str, str] = field(default_factory=dict)  # topic -> passing ancestor


class CalibrationRunner:
    """Runs an adaptive, DAG-aware diagnostic walk to seed initial topic states."""

    CEFR_ORDER: list[CEFR] = CEFR_ORDER

    def __init__(
        self,
        items: list[BankItem] | None = None,
        topic_manager: TopicStateManager | None = None,
        bank: SqliteItemBank | None = None,
    ) -> None:
        self.topic_manager = topic_manager

        if items is not None:
            self.items = items
        elif bank is not None:
            self.items = bank.get_all_items()
        else:
            self.items = []

        self._items_by_topic: dict[str, list[BankItem]] = defaultdict(list)
        for it in self.items:
            self._items_by_topic[it.topic_id].append(it)

        self.validator: TaxonomyValidator | None = None
        self._level_topics: dict[CEFR, list[str]] = {lvl: [] for lvl in CEFR_ORDER}
        if topic_manager is not None:
            topics = list(topic_manager.topics.values())
            validator = TaxonomyValidator(topics)
            self.validator = validator
            for lvl in CEFR_ORDER:
                topic_ids = [t.id for t in topics if t.cefr == lvl]
                # Test the highest-yield topics first: a pass on a topic with
                # many transitive prerequisites resolves the most ground by
                # inference per item spent.
                topic_ids.sort(key=lambda tid: (-len(validator.get_transitive_prereqs(tid)), tid))
                self._level_topics[lvl] = topic_ids

    # -- selection -----------------------------------------------------

    def next_item(self, answered: list[Answer]) -> BankItem | None:
        """Select the next diagnostic probe item given the full answered history so far."""
        state = self._replay(answered)
        if state.finished or state.pending_topic is None:
            return None
        answered_ids = {a.item_id for a in answered}
        topic_items = self._items_by_topic.get(state.pending_topic, [])
        candidates = [it for it in topic_items if it.id not in answered_ids]
        if not candidates:
            return None
        return candidates[0]

    # -- finalisation ----------------------------------------------------

    def finalise(self, answered: list[Answer]) -> CalibrationResult:
        """Apply the walk's verdicts to the topic manager and summarise the outcome."""
        state = self._replay(answered)

        acquired: list[str] = []
        learning: list[str] = []

        if self.topic_manager is not None:
            ref_time = datetime.now(UTC)

            for topic_id in state.topic_order:
                verdict = state.verdicts[topic_id]
                n_correct = sum(verdict)
                n_total = len(verdict)
                if n_total < ITEMS_PER_TOPIC:
                    # Never resolved within the item budget; leave state untouched.
                    continue

                if n_correct == ITEMS_PER_TOPIC:
                    self.topic_manager.mark_acquired_kalibrierung(
                        topic_id, stability_days=KALIBRIERUNG_ACQUIRED_STABILITY_DAYS, now=ref_time
                    )
                    acquired.append(topic_id)
                elif n_correct == 1:
                    self._mark_learning(topic_id, ref_time)
                    learning.append(topic_id)
                else:
                    self._mark_unseen(topic_id, ref_time)

            inferred_topics: list[str] = []
            for topic_id in sorted(state.inferred_from_pass):
                if topic_id in state.verdicts:
                    # Directly tested takes priority over inference.
                    continue
                self.topic_manager.mark_acquired_inferred(
                    topic_id, stability_days=INFERRED_ACQUIRED_STABILITY_DAYS, now=ref_time
                )
                inferred_topics.append(topic_id)
            acquired.extend(inferred_topics)
        else:
            inferred_topics = []

        total = state.items_used
        correct_count = sum(a.is_correct for a in answered)
        score = round(correct_count / total, 3) if total > 0 else 0.0

        return CalibrationResult(
            estimated_cefr=CEFR_ORDER[state.level_idx],
            total_items=total,
            correct_count=correct_count,
            score=score,
            acquired_topics=sorted(set(acquired)),
            learning_topics=sorted(set(learning)),
            directly_tested_topics=sorted(state.verdicts.keys()),
            inferred_topics=sorted(inferred_topics),
        )

    def _mark_learning(self, topic_id: str, ref_time: datetime) -> None:
        assert self.topic_manager is not None
        curr = self.topic_manager.states[topic_id]
        self.topic_manager.states[topic_id] = curr.model_copy(
            update={
                "state": "learning",
                "acquired_via": None,
                "fsrs_stability": PARTIAL_LEARNING_STABILITY_DAYS,
                "due_at": ref_time,
                "introduced_at": ref_time,
            }
        )

    def _mark_unseen(self, topic_id: str, ref_time: datetime) -> None:
        assert self.topic_manager is not None
        curr = self.topic_manager.states[topic_id]
        self.topic_manager.states[topic_id] = curr.model_copy(
            update={
                "state": "unseen",
                "acquired_via": None,
                "fsrs_stability": 0.0,
                "due_at": ref_time,
            }
        )

    # -- replay ------------------------------------------------------------

    def _replay(self, answered: list[Answer]) -> _WalkState:
        """Deterministically reconstruct the walk's state from an answer history."""
        topic_order: list[str] = []
        verdicts: dict[str, list[bool]] = {}
        for a in answered:
            if a.topic_id not in verdicts:
                verdicts[a.topic_id] = []
                topic_order.append(a.topic_id)
            verdicts[a.topic_id].append(a.is_correct)

        level_idx = CEFR_ORDER.index(START_LEVEL)
        resolved: set[str] = set()
        inferred_from_pass: dict[str, str] = {}
        suspicion_queue: list[str] = []
        consecutive_pass = 0
        consecutive_fail = 0
        items_used = 0
        mid_topic: str | None = None

        for topic_id in topic_order:
            verdict = verdicts[topic_id]
            n_correct = sum(verdict)
            n_total = len(verdict)
            available = self._items_by_topic.get(topic_id, [])
            exhausted = n_total >= ITEMS_PER_TOPIC or n_total >= len(available)

            if not exhausted:
                mid_topic = topic_id
                items_used += n_total
                continue

            mid_topic = None
            items_used += n_total
            resolved.add(topic_id)
            is_pass = n_correct == ITEMS_PER_TOPIC and n_total >= ITEMS_PER_TOPIC

            if is_pass:
                if self.validator is not None:
                    for prereq_id in self.validator.get_transitive_prereqs(topic_id):
                        resolved.add(prereq_id)
                        inferred_from_pass.setdefault(prereq_id, topic_id)
                consecutive_pass += 1
                consecutive_fail = 0
                if consecutive_pass >= LEVEL_SHIFT_STREAK:
                    level_idx = min(level_idx + 1, len(CEFR_ORDER) - 1)
                    consecutive_pass = 0
            else:
                consecutive_fail += 1
                consecutive_pass = 0
                if self.validator is not None:
                    # Fail casts suspicion upward: queue one representative
                    # descendant rather than testing every dependent topic.
                    for descendant_id in sorted(self.validator.get_descendants(topic_id)):
                        if descendant_id not in resolved and self._items_by_topic.get(
                            descendant_id
                        ):
                            suspicion_queue.append(descendant_id)
                            break
                if consecutive_fail >= LEVEL_SHIFT_STREAK:
                    level_idx = max(level_idx - 1, 0)
                    consecutive_fail = 0

        pending_topic: str | None = None
        finished = False

        if mid_topic is not None:
            if items_used < MAX_ITEMS:
                pending_topic = mid_topic
            else:
                finished = True
        elif items_used >= MAX_ITEMS:
            finished = True
        else:
            while suspicion_queue and pending_topic is None:
                candidate = suspicion_queue.pop(0)
                if candidate not in resolved and self._items_by_topic.get(candidate):
                    pending_topic = candidate

            if pending_topic is None:
                search_order = [level_idx] + [i for i in range(len(CEFR_ORDER)) if i != level_idx]
                for li in search_order:
                    lvl = CEFR_ORDER[li]
                    level_candidate = self._first_unresolved(lvl, resolved)
                    if level_candidate is not None:
                        pending_topic = level_candidate
                        level_idx = li
                        break

            if pending_topic is None:
                finished = True

        return _WalkState(
            level_idx=level_idx,
            resolved=resolved,
            topic_order=topic_order,
            verdicts=verdicts,
            items_used=items_used,
            pending_topic=pending_topic,
            finished=finished,
            inferred_from_pass=inferred_from_pass,
        )

    def _first_unresolved(self, level: CEFR, resolved: set[str]) -> str | None:
        for topic_id in self._level_topics.get(level, []):
            if topic_id not in resolved and self._items_by_topic.get(topic_id):
                return topic_id
        return None
