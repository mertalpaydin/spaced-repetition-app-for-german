"""Unit tests for the TopicStateManager, LearningScheduler, pacing, and overload forecast."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, Distractor, Topic
from src.engine.fsrs import FSRSRecord
from src.engine.scheduler import LearningScheduler
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


@pytest.fixture
def taxonomy_topics() -> list[Topic]:
    return load_taxonomy()


@pytest.fixture
def topic_manager(taxonomy_topics: list[Topic]) -> TopicStateManager:
    return TopicStateManager(taxonomy_topics)


@pytest.fixture
def temp_bank(tmp_path: Path) -> SqliteItemBank:
    db_file = tmp_path / "scheduler_test_bank.db"
    bank = SqliteItemBank(db_file)

    # Insert sample bank items for testing
    for i in range(15):
        bank.insert_item(
            BankItem(
                id=f"item_{i:03d}",
                topic_id="kasus_dativ_formen" if i < 8 else "dativ_nach_praeposition",
                type="cloze_free",
                difficulty=1,
                cefr="A1" if i < 8 else "A2",
                prompt=f"Das ist Test Satz Nummer {i} mit ___ Lücke.",
                accepted_answers=["dem"],
                distractors=[
                    Distractor(text="den"),
                    Distractor(text="des"),
                    Distractor(text="das"),
                ],
            )
        )
    return bank


def test_topic_state_manager_initialization(topic_manager: TopicStateManager) -> None:
    """Root topics start ready; dependent topics start locked."""
    # Root topic (0 prereqs)
    root_state = topic_manager.get_state("pronomen_personal_nom")
    assert root_state.state == "ready"

    # Dependent topic (has prereqs)
    dep_state = topic_manager.get_state("dativ_nach_praeposition")
    assert dep_state.state == "locked"


def test_topic_promotion_on_unhinted_passes(topic_manager: TopicStateManager) -> None:
    """Topic promotes to acquired on 3 consecutive passes spanning >= 2 facets."""
    topic_id = "pronomen_personal_nom"

    # Attempt 1: ready -> learning
    s1 = topic_manager.record_attempt(topic_id, is_unhinted_pass=True, facet="sg1")
    assert s1.state == "learning"
    assert s1.promotion_consecutive_passes == 1

    # Attempt 2: still learning
    s2 = topic_manager.record_attempt(topic_id, is_unhinted_pass=True, facet="sg2")
    assert s2.state == "learning"
    assert s2.promotion_consecutive_passes == 2

    # Attempt 3 with 2nd facet: promotes to acquired!
    s3 = topic_manager.record_attempt(topic_id, is_unhinted_pass=True, facet="sg2")
    assert s3.state == "acquired"
    assert s3.promotion_consecutive_passes == 3
    assert len(s3.promotion_distinct_facets) >= 2


def test_failed_attempt_resets_consecutive_streak(topic_manager: TopicStateManager) -> None:
    """A failure or hinted pass resets consecutive unhinted passes to 0."""
    topic_id = "pronomen_personal_nom"
    topic_manager.record_attempt(topic_id, is_unhinted_pass=True, facet="sg1")
    topic_manager.record_attempt(topic_id, is_unhinted_pass=True, facet="sg2")

    # Hinted or failed attempt
    s_failed = topic_manager.record_attempt(topic_id, is_unhinted_pass=False, facet="sg1")
    assert s_failed.promotion_consecutive_passes == 0
    assert s_failed.state == "learning"


def test_scheduler_round_pacing(
    temp_bank: SqliteItemBank, topic_manager: TopicStateManager
) -> None:
    """Scheduler produces rounds of exact configured size (default 6)."""
    scheduler = LearningScheduler(round_size=6)
    fsrs_records: dict[str, FSRSRecord] = {}

    plan = scheduler.plan_next_round(
        bank=temp_bank,
        topic_manager=topic_manager,
        fsrs_records=fsrs_records,
    )

    assert len(plan.items) == 6
    assert plan.mode == "review"
    assert plan.is_overload_blocked is False


def test_7day_overload_forecast_blocks_new_topics(
    temp_bank: SqliteItemBank, topic_manager: TopicStateManager
) -> None:
    """When 7-day review forecast exceeds threshold (> 50), new topics are paused."""
    scheduler = LearningScheduler(forecast_threshold=50)
    now = datetime.now(UTC)

    # Simulate 55 cards due within 7 days
    fsrs_records = {
        f"card_{i}": FSRSRecord(card_id=f"card_{i}", due=now + timedelta(days=2)) for i in range(55)
    }

    forecast = scheduler.forecast_7day_load(list(fsrs_records.values()), now=now)
    assert forecast == 55

    plan = scheduler.plan_next_round(
        bank=temp_bank,
        topic_manager=topic_manager,
        fsrs_records=fsrs_records,
        now=now,
    )

    assert plan.is_overload_blocked is True
    assert plan.new_topic_id is None


def test_dormancy_triggers_recalibration_mode(
    temp_bank: SqliteItemBank, topic_manager: TopicStateManager
) -> None:
    """User returning after >= 21 days inactivity automatically receives a recalibration round."""
    scheduler = LearningScheduler()
    now = datetime.now(UTC)
    last_active = now - timedelta(days=25)

    plan = scheduler.plan_next_round(
        bank=temp_bank,
        topic_manager=topic_manager,
        fsrs_records={},
        now=now,
        last_active_date=last_active,
    )

    assert plan.mode == "recalibration"
    assert len(plan.items) == 10  # RECALIBRATION_ROUND_SIZE
