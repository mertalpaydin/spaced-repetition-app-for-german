"""Unit tests for Cloudflare Worker D1 sync protocol, schema, and client serialization."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.contracts import TagStateModel
from src.engine.fsrs import FSRSRecord
from src.sync.client import SyncClient


def test_worker_schema_and_config_exist() -> None:
    """Verify worker files, D1 schema, and wrangler.toml configuration."""
    worker_dir = Path("worker")
    assert (worker_dir / "wrangler.toml").exists()
    assert (worker_dir / "schema.sql").exists()
    assert (worker_dir / "src" / "index.js").exists()

    schema_text = (worker_dir / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS user_topic_states" in schema_text
    assert "CREATE TABLE IF NOT EXISTS user_fsrs_cards" in schema_text


def test_sync_client_payload_serialization() -> None:
    """Test building outbound sync payload from TagStateModel and FSRSRecord."""
    now = datetime.now(UTC)
    topic_states = {
        "pronomen_personal_nom": TagStateModel(
            tag_id="pronomen_personal_nom",
            state="acquired",
            promotion_consecutive_passes=3,
            promotion_distinct_facets=["sg1", "sg2"],
            last_review_at=now,
        )
    }

    fsrs_records = {
        "item_001": FSRSRecord(
            card_id="item_001",
            state="review",
            due=now + timedelta(days=3),
            stability=3.5,
            difficulty=2.1,
            reps=2,
            lapses=0,
            last_review=now,
        )
    }

    payload = SyncClient.build_payload(
        user_id="user_test_123",
        topic_states=topic_states,
        fsrs_records=fsrs_records,
    )

    assert payload.user_id == "user_test_123"
    assert len(payload.topic_states) == 1
    assert payload.topic_states[0].topic_id == "pronomen_personal_nom"
    assert payload.topic_states[0].state == "acquired"
    assert len(payload.fsrs_cards) == 1
    assert payload.fsrs_cards[0].card_id == "item_001"
    assert payload.fsrs_cards[0].stability == 3.5


def test_sync_merge_inbound_conflict_resolution() -> None:
    """Test that newer inbound timestamps overwrite older local state."""
    old_time = datetime.now(UTC) - timedelta(days=2)
    new_time = datetime.now(UTC)

    local_states = {
        "pronomen_personal_nom": TagStateModel(
            tag_id="pronomen_personal_nom",
            state="learning",
            last_review_at=old_time,
        )
    }

    inbound = [
        {
            "topic_id": "pronomen_personal_nom",
            "state": "acquired",
            "consecutive_passes": 3,
            "distinct_facets": ["sg1", "sg2"],
            "acquired_via": "earned",
            "last_review_at": new_time.isoformat(),
        },
        {
            "topic_id": "dativ_nach_praeposition",
            "state": "ready",
            "last_review_at": None,
        },
    ]

    merged = SyncClient.merge_inbound_topics(local_states, inbound)

    assert merged["pronomen_personal_nom"].state == "acquired"
    assert merged["pronomen_personal_nom"].promotion_consecutive_passes == 3
    assert "dativ_nach_praeposition" in merged
    assert merged["dativ_nach_praeposition"].state == "ready"
