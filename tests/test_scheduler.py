"""Unit tests for TopicStateManager, LearningScheduler, and split detection."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, Distractor, TagStateModel, Topic
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
                confusion_group="wechselpraepositionen" if i % 2 == 0 else None,
            )
        )
    return bank


def test_topic_state_manager_initialization(topic_manager: TopicStateManager) -> None:
    """Root topics start ready; dependent topics start locked."""
    root_state = topic_manager.get_state("pronomen_personal_nom")
    assert root_state.state == "ready"

    dep_state = topic_manager.get_state("dativ_nach_praeposition")
    assert dep_state.state == "locked"


def test_interleaving_invariant_preserves_topic_separation() -> None:
    """Adjacent items in the round must not share the same topic_id."""
    items = [
        BankItem(
            id=f"t1_{i}",
            topic_id="topic_1",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="Satz",
            accepted_answers=["a"],
            distractors=[Distractor(text="b"), Distractor(text="c"), Distractor(text="d")],
        )
        for i in range(3)
    ] + [
        BankItem(
            id=f"t2_{i}",
            topic_id="topic_2",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="Satz",
            accepted_answers=["a"],
            distractors=[Distractor(text="b"), Distractor(text="c"), Distractor(text="d")],
        )
        for i in range(3)
    ]

    interleaved = LearningScheduler._interleave_items(items)
    for i in range(len(interleaved) - 1):
        assert interleaved[i].topic_id != interleaved[i + 1].topic_id


def test_forecast_and_day_budget() -> None:
    """Test forecast projecting daily counts and day_budget calculating obligations."""
    scheduler = LearningScheduler()
    now = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)

    states = [
        TagStateModel(tag_id="t1", due_at=now),
        TagStateModel(tag_id="t2", due_at=now + timedelta(days=1)),
        TagStateModel(tag_id="t3", due_at=now + timedelta(days=3)),
    ]

    fc = scheduler.forecast(states, now=now, horizon_days=7)
    assert len(fc) == 7
    assert fc[0] == 1  # due today
    assert fc[1] == 1  # due tomorrow
    assert fc[3] == 1

    budget = scheduler.day_budget(states, now=now)
    assert budget.due_count == 1
    assert budget.new_topics_allowed == 2
    assert budget.ceiling > budget.due_count


def test_duel_and_recalibration_protocol_methods(temp_bank: SqliteItemBank) -> None:
    """Test build_duel and build_recalibration methods."""
    scheduler = LearningScheduler(bank=temp_bank)
    now = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)

    duel_items = scheduler.build_duel("wechselpraepositionen", now=now)
    assert len(duel_items) <= 8

    recal_items = scheduler.build_recalibration(states=[], now=now)
    assert len(recal_items) <= 10


def test_detect_split_candidates() -> None:
    """Test detecting split candidate when facet accuracy gap >= 0.40."""
    scheduler = LearningScheduler()

    # 20 attempts on masc (100%), 20 on neut (50%) -> gap 0.50 >= 0.40
    history = (
        [{"topic_id": "dativ_art", "facet": "masc", "is_correct": True} for _ in range(20)]
        + [{"topic_id": "dativ_art", "facet": "neut", "is_correct": True} for _ in range(10)]
        + [{"topic_id": "dativ_art", "facet": "neut", "is_correct": False} for _ in range(10)]
    )

    candidates = scheduler.detect_split_candidates(history)
    assert len(candidates) == 1
    assert candidates[0].topic_id == "dativ_art"
    assert candidates[0].gap >= 0.40
