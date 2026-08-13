"""Topic state machine managing DAG progression, unhinted promotion gates, and states."""

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
                # Root topics (0 prerequisites) start in ready state, others locked
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
    ) -> TagStateModel:
        """Record an exercise attempt and update consecutive passes / promotion."""
        current = self.states[topic_id]
        now = datetime.now(UTC)

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
            else:
                distinct_facets.add("default")

            # Check promotion gate: >= 3 consecutive passes spanning >= 2 distinct facets
            if (
                new_state == "learning"
                and consecutive_passes >= PROMOTION_CONSECUTIVE_PASSES
                and len(distinct_facets) >= PROMOTION_MIN_DISTINCT_FACETS
            ):
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
            last_review_at=now,
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
        """Unlock topics to ready when all their prerequisites are acquired."""
        for topic_id, topic in self.topics.items():
            current = self.states[topic_id]
            if current.state == "locked":
                # Check if all prerequisites are acquired
                all_prereqs_acquired = all(
                    self.states[p].state == "acquired" for p in topic.prereqs if p in self.states
                )
                if all_prereqs_acquired:
                    self.states[topic_id] = TagStateModel(
                        tag_id=topic_id,
                        state="ready",
                        acquired_via=current.acquired_via,
                        promotion_consecutive_passes=0,
                        promotion_distinct_facets=[],
                    )
