"""Unit tests for the FSRS engine and hint degradation policy."""

from datetime import UTC, datetime

import pytest
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy


@pytest.fixture
def fsrs_engine() -> FSRSEngine:
    return FSRSEngine()


@pytest.fixture
def initial_record() -> FSRSRecord:
    return FSRSRecord(card_id="card_001")


def test_fsrs_initial_state_and_first_review(
    fsrs_engine: FSRSEngine, initial_record: FSRSRecord
) -> None:
    """Test first review progression."""
    assert initial_record.state == "learning"
    assert initial_record.reps == 0

    now = datetime.now(UTC)
    updated = fsrs_engine.schedule_review(initial_record, rating="good", now=now)

    assert updated.card_id == "card_001"
    assert updated.reps == 1
    assert updated.stability is not None
    assert updated.stability > 0.0
    assert updated.due > now


def test_fsrs_lapse_and_relearning(fsrs_engine: FSRSEngine, initial_record: FSRSRecord) -> None:
    """Test failure rating resets stability and increments lapses."""
    now = datetime.now(UTC)
    # Pass first
    r1 = fsrs_engine.schedule_review(initial_record, rating="good", now=now)
    # Then fail
    r2 = fsrs_engine.schedule_review(r1, rating="again", now=now)

    assert r2.lapses == 1
    assert r2.reps == 2


def test_hint_policy_evaluations() -> None:
    """Test hint level translation to FSRS ratings."""
    # Hint 0 + correct -> good (or easy if fast)
    assert HintPolicy.evaluate_attempt(hint_level=0, is_correct=True, is_fast=False) == "good"
    assert HintPolicy.evaluate_attempt(hint_level=0, is_correct=True, is_fast=True) == "easy"

    # Hint 1 or 2 + correct -> hard
    assert HintPolicy.evaluate_attempt(hint_level=1, is_correct=True) == "hard"
    assert HintPolicy.evaluate_attempt(hint_level=2, is_correct=True) == "hard"

    # Hint 3 (rule stated) or Hint 4 (revealed) -> again
    assert HintPolicy.evaluate_attempt(hint_level=3, is_correct=True) == "again"
    assert HintPolicy.evaluate_attempt(hint_level=4, is_correct=True) == "again"

    # Incorrect attempt always -> again
    assert HintPolicy.evaluate_attempt(hint_level=0, is_correct=False) == "again"
    assert HintPolicy.evaluate_attempt(hint_level=1, is_correct=False) == "again"


def test_unhinted_pass_check() -> None:
    """Test unhinted pass qualification."""
    assert HintPolicy.is_unhinted_pass(hint_level=0, is_correct=True) is True
    assert HintPolicy.is_unhinted_pass(hint_level=1, is_correct=True) is False
    assert HintPolicy.is_unhinted_pass(hint_level=0, is_correct=False) is False
