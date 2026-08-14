"""Python client for Cloudflare Worker D1 sync protocol."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import TagStateModel, Topic
from src.engine.fsrs import FSRSRecord
from src.engine.topic_state import TopicStateManager


class SyncTopicState(BaseModel):
    """Serialized topic state record for Cloudflare sync."""

    model_config = ConfigDict(frozen=True)
    topic_id: str
    state: str
    consecutive_passes: int = 0
    distinct_facets: list[str] = Field(default_factory=list)
    acquired_via: str | None = None
    last_review_at: str | None = None


class SyncFSRSCard(BaseModel):
    """Serialized FSRS card memory record for Cloudflare sync."""

    model_config = ConfigDict(frozen=True)
    card_id: str
    state: str
    due_at: str
    stability: float | None = None
    difficulty: float | None = None
    step: int = 0
    reps: int = 0
    lapses: int = 0
    last_review_at: str | None = None


class SyncPayload(BaseModel):
    """Encapsulates full outbound sync payload."""

    model_config = ConfigDict(frozen=True)
    user_id: str
    topic_states: list[SyncTopicState] = Field(default_factory=list)
    fsrs_cards: list[SyncFSRSCard] = Field(default_factory=list)


class ReviewLogEntry(BaseModel):
    """One immutable review event: the sync protocol's actual unit of truth.

    `review_log` is append-only (04-application.md stage 9), which is what
    makes merging it trivial and safe: union the rows, recompute everything
    derived from them. `tag_state` must never be merged directly.
    """

    model_config = ConfigDict(frozen=True)
    item_id: str
    topic_id: str
    is_correct: bool
    hint_level: int = 0
    facet: str | None = None
    timestamp: datetime

    @property
    def dedup_key(self) -> tuple[str, str]:
        """`(item_id, timestamp)` identifies a row for idempotent merging,
        mirroring the worker's `UNIQUE(user_id, item_id, created_at)`
        constraint on `review_logs`."""
        return (self.item_id, self.timestamp.isoformat())


class SyncClient:
    """Encapsulates payload serialization and conflict resolution for D1 sync."""

    @staticmethod
    def build_payload(
        user_id: str,
        topic_states: dict[str, TagStateModel],
        fsrs_records: dict[str, FSRSRecord],
    ) -> SyncPayload:
        """Serialize local state into Cloudflare SyncPayload."""
        serialized_topics = [
            SyncTopicState(
                topic_id=t_id,
                state=s.state,
                consecutive_passes=s.promotion_consecutive_passes,
                distinct_facets=s.promotion_distinct_facets,
                acquired_via=s.acquired_via,
                last_review_at=s.last_review_at.isoformat() if s.last_review_at else None,
            )
            for t_id, s in topic_states.items()
        ]

        serialized_cards = [
            SyncFSRSCard(
                card_id=c_id,
                state=r.state,
                due_at=r.due.isoformat(),
                stability=r.stability,
                difficulty=r.difficulty,
                step=r.step or 0,
                reps=r.reps,
                lapses=r.lapses,
                last_review_at=r.last_review.isoformat() if r.last_review else None,
            )
            for c_id, r in fsrs_records.items()
        ]

        return SyncPayload(
            user_id=user_id,
            topic_states=serialized_topics,
            fsrs_cards=serialized_cards,
        )

    @staticmethod
    def merge_review_logs(
        local_log: list[ReviewLogEntry] | list[dict[str, Any]],
        inbound_log: list[ReviewLogEntry] | list[dict[str, Any]],
    ) -> list[ReviewLogEntry]:
        """Union local and inbound review-log rows, deduplicated by `dedup_key`.

        `review_log` is append-only: a row that exists on either side exists
        in the merge exactly once, regardless of which side saw it first or
        how many times it was retried. The result is sorted by timestamp so
        that replaying it through `recompute_tag_states` is deterministic
        and, per the plan's "review_log union is order independent" test,
        gives the same `tag_state` no matter what order the two sides were
        combined in.
        """
        by_key: dict[tuple[str, str], ReviewLogEntry] = {}
        for raw in (*local_log, *inbound_log):
            entry = raw if isinstance(raw, ReviewLogEntry) else ReviewLogEntry(**raw)
            by_key[entry.dedup_key] = entry
        return sorted(by_key.values(), key=lambda e: (e.timestamp, e.item_id))

    @staticmethod
    def recompute_tag_states(
        review_log: list[ReviewLogEntry],
        topics: list[Topic],
    ) -> dict[str, TagStateModel]:
        """Recompute `tag_state` from scratch by replaying the merged review_log.

        04-application.md:363: "Never merge `tag_state` directly. It is
        derived data and merging derived data is how progress corrupts."
        `tag_state` is never assigned from a peer's or a file's own copy; it
        is always rebuilt from the one append-only source of truth by
        driving it through the same `TopicStateManager` the rest of the
        engine uses, in timestamp order.

        Rows naming a `topic_id` outside `topics` are skipped rather than
        raising: a client can be behind on the taxonomy, and a stale row
        must not fail the whole merge.
        """
        manager = TopicStateManager(topics)
        for entry in sorted(review_log, key=lambda e: (e.timestamp, e.item_id)):
            if entry.topic_id not in manager.states:
                continue
            manager.record_attempt(
                topic_id=entry.topic_id,
                is_correct=entry.is_correct,
                hint_level=entry.hint_level,
                facet=entry.facet,
                now=entry.timestamp,
            )
        return dict(manager.states)
