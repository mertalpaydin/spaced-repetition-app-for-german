"""Topic state machine managing lifecycle from locked to acquired with promotion gates."""

from datetime import UTC, datetime

from src.contracts import (
    INFERRED_STABILITY_CEILING_DAYS,
    PROMOTION_CONSECUTIVE_PASSES,
    PROMOTION_MIN_DISTINCT_FACETS,
    TagState,
    TagStateModel,
    Topic,
)
from src.taxonomy.validator import TaxonomyValidator


class TopicStateManager:
    """Manages the lifecycle of grammar topics from locked to acquired."""

    def __init__(
        self,
        topics: list[Topic],
        states: dict[str, TagStateModel] | None = None,
    ) -> None:
        self.topics = {t.id: t for t in topics}
        self.validator = TaxonomyValidator(topics)
        self.states: dict[str, TagStateModel] = states or {}

        # Initialize missing topics
        for topic_id, topic in self.topics.items():
            if topic_id not in self.states:
                initial_state: TagState = "ready" if len(topic.prereqs) == 0 else "locked"
                self.states[topic_id] = TagStateModel(
                    tag_id=topic_id,
                    state=initial_state,
                )

        self._refresh_ready_topics()

    def get_state(self, topic_id: str) -> TagStateModel:
        """Get the current state model for a topic."""
        return self.states[topic_id]

    def start_topic(self, topic_id: str) -> None:
        """Explicitly transition a ready topic to learning state."""
        if topic_id in self.states and self.states[topic_id].state == "ready":
            self.states[topic_id] = self.states[topic_id].model_copy(update={"state": "learning"})

    def record_attempt(
        self,
        topic_id: str,
        is_unhinted_pass: bool,
        facet: str | None = None,
        now: datetime | None = None,
    ) -> TagStateModel:
        """Record an exercise attempt and update consecutive passes / promotion."""
        current = self.states[topic_id]
        topic_obj = self.topics.get(topic_id)
        ref_time = now or datetime.now(UTC)

        # Transition ready -> learning on first practice
        new_state = current.state
        if new_state == "ready":
            new_state = "learning"

        consecutive_passes = current.promotion_consecutive_passes
        distinct_facets = set(current.promotion_distinct_facets)

        if is_unhinted_pass:
            consecutive_passes += 1
            if facet:
                distinct_facets.add(facet)

            # Check if topic is unfaceted (no morph_spec facets or facet is None)
            is_unfaceted = topic_obj is None or not topic_obj.morph_spec

            if new_state == "learning" and consecutive_passes >= PROMOTION_CONSECUTIVE_PASSES:
                if is_unfaceted or len(distinct_facets) >= PROMOTION_MIN_DISTINCT_FACETS:
                    new_state = "acquired"
        else:
            # Failure or hinted attempt resets consecutive pass streak
            consecutive_passes = 0

        updated = TagStateModel(
            tag_id=topic_id,
            state=new_state,
            acquired_via=current.acquired_via,
            promotion_consecutive_passes=consecutive_passes,
            promotion_distinct_facets=sorted(distinct_facets),
            last_review_at=ref_time,
        )
        self.states[topic_id] = updated

        if new_state == "acquired":
            self._refresh_ready_topics()

        return updated

    def mark_acquired_inferred(self, topic_id: str) -> TagStateModel:
        """Mark a topic as acquired via inference (downstream placement)."""
        updated = TagStateModel(
            tag_id=topic_id,
            state="acquired",
            acquired_via="inferred",
            inferred_stability_cap=INFERRED_STABILITY_CEILING_DAYS,
        )
        self.states[topic_id] = updated
        self._refresh_ready_topics()
        return updated

    def _refresh_ready_topics(self) -> None:
        """Check all locked topics and promote to ready if all prerequisites are acquired."""
        for topic_id, topic in self.topics.items():
            if self.states[topic_id].state == "locked":
                prereqs_met = all(
                    self.states.get(p_id) is not None and self.states[p_id].state == "acquired"
                    for p_id in topic.prereqs
                )
                if prereqs_met:
                    self.states[topic_id] = TagStateModel(
                        tag_id=topic_id,
                        state="ready",
                        acquired_via=self.states[topic_id].acquired_via,
                    )
