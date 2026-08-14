"""Topic state machine managing learning status, promotions, and degradation."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import (
    INFERRED_STABILITY_CEILING_DAYS,
    PROMOTION_CONSECUTIVE_PASSES,
    PROMOTION_MIN_DISTINCT_FACETS,
    TagState,
    TagStateModel,
    Topic,
)


class AttemptRecord(BaseModel):
    """Record of a single exercise attempt for state transition computation."""

    model_config = ConfigDict(frozen=True)
    topic_id: str
    is_correct: bool
    hint_level: int = 0
    facet: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TopicStateManager:
    """Manages TagState lifecycle: unseen -> learning -> acquired (with demotion on relapse)."""

    def __init__(self, topics: list[Topic]) -> None:
        self.topics = {t.id: t for t in topics}
        self.states: dict[str, TagStateModel] = {}
        for t in topics:
            initial_state: TagState = "ready" if len(t.prereqs) == 0 else "locked"
            self.states[t.id] = TagStateModel(
                tag_id=t.id,
                state=initial_state,
                fsrs_stability=0.0,
                fsrs_difficulty=0.0,
                due_at=datetime.now(UTC),
            )

    def get_state(self, topic_id: str) -> TagStateModel:
        """Retrieve the TagStateModel for a given topic_id."""
        return self.states[topic_id]

    def mark_acquired_kalibrierung(
        self, topic_id: str, stability_days: float = 10.0, now: datetime | None = None
    ) -> None:
        """Mark topic as acquired directly from placement test diagnostic."""
        ref_time = now or datetime.now(UTC)
        self.states[topic_id] = self.states[topic_id].model_copy(
            update={
                "state": "acquired",
                "acquired_via": "kalibrierung",
                "fsrs_stability": stability_days,
                "due_at": ref_time,
                "consecutive_unhinted_passes": 0,
                "facets_seen_in_streak": set(),
                "promotion_distinct_facets": [],
            }
        )
        self._unlock_ready_descendants(ref_time)

    def mark_acquired_inferred(
        self, topic_id: str, stability_days: float = 4.0, now: datetime | None = None
    ) -> None:
        """Mark prerequisite as acquired via DAG inference, capped at 4.0 days stability."""
        ref_time = now or datetime.now(UTC)
        clamped_stability = min(stability_days, INFERRED_STABILITY_CEILING_DAYS)
        self.states[topic_id] = self.states[topic_id].model_copy(
            update={
                "state": "acquired",
                "acquired_via": "inferred",
                "fsrs_stability": clamped_stability,
                "inferred_stability_cap": INFERRED_STABILITY_CEILING_DAYS,
                "due_at": ref_time,
                "consecutive_unhinted_passes": 0,
                "facets_seen_in_streak": set(),
                "promotion_distinct_facets": [],
            }
        )
        self._unlock_ready_descendants(ref_time)

    def start_topic(self, topic_id: str, now: datetime | None = None) -> TagStateModel:
        """Transition a ready topic to learning upon first introduction."""
        ref_time = now or datetime.now(UTC)
        curr = self.states[topic_id]
        updated = curr.model_copy(
            update={
                "state": "learning",
                "introduced_at": ref_time,
                "due_at": ref_time,
            }
        )
        self.states[topic_id] = updated
        return updated

    def record_attempt(
        self,
        topic_id: str,
        is_correct: bool = True,
        hint_level: int = 0,
        facet: str | None = None,
        is_unhinted_pass: bool | None = None,
        now: datetime | None = None,
    ) -> TagStateModel:
        """Update topic state machine following an attempt."""
        if is_unhinted_pass is not None:
            is_correct = is_unhinted_pass
            hint_level = 0 if is_unhinted_pass else 1
        """Update topic state machine following an attempt.

        Promotion Rules (learning -> acquired):
        1. 3 consecutive unhinted passes (hint_level == 0).
        2. Evidence must span >= 2 distinct morphological facets (if topic is faceted).

        Demotion Rules (acquired -> learning):
        1. If acquired_via == "inferred": demote on 1st failure.
        2. If acquired_via == "kalibrierung" or "earned": demote on 2nd consecutive failure.

        Reset Rule:
        Any failure or hinted pass resets consecutive passes and facet streak counters.
        """
        ref_time = now or datetime.now(UTC)
        curr = self.states[topic_id]
        topic = self.topics.get(topic_id)
        is_unhinted_pass = is_correct and hint_level == 0

        # Compute streak updates
        new_consecutive_unhinted = curr.consecutive_unhinted_passes + 1 if is_unhinted_pass else 0
        new_facets = (
            set(curr.facets_seen_in_streak) | ({facet} if facet else set())
            if is_unhinted_pass
            else set()
        )
        new_distinct_facets = sorted(new_facets)

        new_consecutive_failures = 0 if is_correct else curr.consecutive_failures + 1
        new_state = curr.state
        new_acquired_via = curr.acquired_via

        # Demotion logic on failure
        if not is_correct and curr.state == "acquired":
            if curr.acquired_via == "inferred":
                # Inferred topic demotes immediately on 1st failure
                new_state = "learning"
                new_acquired_via = None
            elif new_consecutive_failures >= 2:
                # Earned / Kalibrierung demotes on 2nd consecutive failure
                new_state = "learning"
                new_acquired_via = None

        # Promotion logic (learning/ready -> acquired)
        has_morph_spec = bool(topic and topic.morph_spec)
        facet_condition_met = (
            len(new_facets) >= PROMOTION_MIN_DISTINCT_FACETS if has_morph_spec else True
        )

        if (
            curr.state in ("learning", "ready", "unseen")
            and is_unhinted_pass
            and new_consecutive_unhinted >= PROMOTION_CONSECUTIVE_PASSES
            and facet_condition_met
        ):
            new_state = "acquired"
            new_acquired_via = "earned"

        updated = curr.model_copy(
            update={
                "state": new_state,
                "acquired_via": new_acquired_via,
                "attempts": curr.attempts + 1,
                "correct": curr.correct + (1 if is_correct else 0),
                "consecutive_failures": new_consecutive_failures,
                "consecutive_unhinted_passes": new_consecutive_unhinted,
                "promotion_consecutive_passes": new_consecutive_unhinted,
                "promotion_distinct_facets": new_distinct_facets,
                "facets_seen_in_streak": new_facets,
                "last_review_at": ref_time,
            }
        )
        self.states[topic_id] = updated

        if new_state == "acquired" and curr.state != "acquired":
            self._unlock_ready_descendants(ref_time)

        return updated

    def _unlock_ready_descendants(self, now: datetime) -> None:
        """Check all locked topics and unlock those whose prerequisites are all acquired."""
        for t_id, topic in self.topics.items():
            if self.states[t_id].state == "locked":
                all_prereqs_met = all(
                    self.states[p].state == "acquired" for p in topic.prereqs if p in self.states
                )
                if all_prereqs_met:
                    self.states[t_id] = self.states[t_id].model_copy(
                        update={"state": "ready", "due_at": now}
                    )
