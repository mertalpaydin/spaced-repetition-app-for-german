"""Python client for Cloudflare Worker D1 sync protocol."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import TagStateModel
from src.engine.fsrs import FSRSRecord


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
    def merge_inbound_topics(
        local_states: dict[str, TagStateModel],
        inbound_topics: list[dict[str, Any]],
    ) -> dict[str, TagStateModel]:
        """Merge inbound topic records into local state with timestamp-based conflict resolution."""
        merged = dict(local_states)

        for in_t in inbound_topics:
            t_id = in_t["topic_id"]
            in_review_str = in_t.get("last_review_at")
            in_review_dt = datetime.fromisoformat(in_review_str) if in_review_str else None

            if t_id in merged:
                local_review_dt = merged[t_id].last_review_at
                # Only overwrite if inbound is newer or local has no review timestamp
                if in_review_dt and (not local_review_dt or in_review_dt > local_review_dt):
                    merged[t_id] = TagStateModel(
                        tag_id=t_id,
                        state=in_t["state"],
                        acquired_via=in_t.get("acquired_via"),
                        promotion_consecutive_passes=in_t.get("consecutive_passes", 0),
                        promotion_distinct_facets=in_t.get("distinct_facets", []),
                        last_review_at=in_review_dt,
                    )
            else:
                merged[t_id] = TagStateModel(
                    tag_id=t_id,
                    state=in_t["state"],
                    acquired_via=in_t.get("acquired_via"),
                    promotion_consecutive_passes=in_t.get("consecutive_passes", 0),
                    promotion_distinct_facets=in_t.get("distinct_facets", []),
                    last_review_at=in_review_dt,
                )

        return merged
