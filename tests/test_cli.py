"""Unit tests for the CLI session, diagnostic calibration, and command dispatchers."""

from pathlib import Path

import pytest
from src.cli.app import run_cli
from src.cli.calibration import CalibrationRunner
from src.cli.session import InteractiveSession
from src.contracts import BankItem, Distractor, Topic
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.scheduler import RoundPlan
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


@pytest.fixture
def taxonomy_topics() -> list[Topic]:
    return load_taxonomy()


@pytest.fixture
def topic_manager(taxonomy_topics: list[Topic]) -> TopicStateManager:
    return TopicStateManager(taxonomy_topics)


@pytest.fixture
def sample_items() -> list[BankItem]:
    return [
        BankItem(
            id="item_a1",
            topic_id="pronomen_personal_nom",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="___ heiße Max.",
            accepted_answers=["Ich", "ich"],
            distractors=[Distractor(text="Du"), Distractor(text="Er"), Distractor(text="Wir")],
        ),
        BankItem(
            id="item_a2",
            topic_id="dativ_nach_praeposition",
            type="cloze_free",
            difficulty=1,
            cefr="A2",
            prompt="Das Buch liegt auf ___ Tisch.",
            accepted_answers=["dem"],
            distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
        ),
    ]


def test_calibration_runner_evaluates_diagnostic_responses(
    topic_manager: TopicStateManager, sample_items: list[BankItem]
) -> None:
    """Test diagnostic evaluation and initial topic acquisition via kalibrierung."""
    responses = [
        (sample_items[0], "Ich"),  # A1 correct
        (sample_items[1], "dem"),  # A2 correct
    ]

    res = CalibrationRunner.evaluate_diagnostic(responses, topic_manager)

    assert res.total_items == 2
    assert res.correct_count == 2
    assert res.score == 1.0
    assert "pronomen_personal_nom" in res.acquired_topics
    assert "dativ_nach_praeposition" in res.acquired_topics

    # Check that topic state was updated
    s = topic_manager.get_state("pronomen_personal_nom")
    assert s.state == "acquired"
    assert s.acquired_via == "inferred"


def test_interactive_session_lifecycle(
    topic_manager: TopicStateManager, sample_items: list[BankItem]
) -> None:
    """Test session attempt processing, hint degradation, and summary generation."""
    fsrs_engine = FSRSEngine()
    fsrs_records: dict[str, FSRSRecord] = {}

    plan = RoundPlan(
        items=sample_items,
        mode="review",
    )

    session = InteractiveSession(
        round_plan=plan,
        topic_manager=topic_manager,
        fsrs_engine=fsrs_engine,
        fsrs_records=fsrs_records,
    )

    # Attempt 1: unhinted correct
    att1 = session.process_item_attempt(
        item=sample_items[0],
        user_answer="Ich",
        hint_level=0,
    )
    assert att1.is_correct is True
    assert att1.is_unhinted_pass is True
    assert att1.fsrs_rating == "good"

    # Check FSRS record was created
    assert "item_a1" in fsrs_records
    assert fsrs_records["item_a1"].reps == 1

    # Attempt 2: hinted correct (hint level 2: options)
    att2 = session.process_item_attempt(
        item=sample_items[1],
        user_answer="dem",
        hint_level=2,
    )
    assert att2.is_correct is True
    assert att2.is_unhinted_pass is False
    assert att2.fsrs_rating == "hard"

    summary = session.get_summary()
    assert summary.total_items == 2
    assert summary.correct_items == 2
    assert summary.accuracy == 1.0
    assert summary.unhinted_passes == 1


def test_cli_subcommands(tmp_path: Path) -> None:
    """Test CLI dispatcher commands (stats, topics, export, help)."""
    assert run_cli([]) == 0
    assert run_cli(["stats"]) == 0
    assert run_cli(["topics"]) == 0

    export_out = str(tmp_path / "cli_export")
    assert run_cli(["export", "--out", export_out]) == 0
