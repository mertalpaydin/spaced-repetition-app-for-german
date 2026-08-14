"""Unit tests for the FSRS engine, parity vectors, and hint policy."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.contracts import FsrsRating
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy


@pytest.fixture
def fsrs_engine() -> FSRSEngine:
    return FSRSEngine()


@pytest.fixture
def initial_record() -> FSRSRecord:
    return FSRSRecord(card_id="card_001", due=datetime.now(UTC))


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


def test_fsrs_parity_vectors() -> None:
    """Validate FSRS-4.5 implementation against canonical cross-language parity vectors."""
    vectors_file = Path("data/fixtures/fsrs/parity_vectors.json")
    assert vectors_file.exists(), "Parity vectors fixture must exist"

    with open(vectors_file, encoding="utf-8") as f:
        data = json.load(f)

    engine = FSRSEngine(
        w=data.get("w"),
        request_retention=data.get("request_retention", 0.9),
        maximum_interval=data.get("maximum_interval", 36500),
    )

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    rating_map: dict[int, FsrsRating] = {1: "again", 2: "hard", 3: "good", 4: "easy"}

    for tc in data["test_cases"]:
        card = FSRSRecord(card_id=tc["card_id"], due=base_time)
        curr_time = base_time

        for step in tc["history"]:
            curr_time = curr_time + timedelta(days=step["elapsed_days"])
            rating = rating_map[step["rating"]]
            card = engine.schedule_review(card, rating=rating, now=curr_time)
            assert card.state == step["expected_state"]
            assert card.stability is not None and card.stability > 0


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

    # Any incorrect answer -> again
    assert HintPolicy.evaluate_attempt(hint_level=0, is_correct=False) == "again"
    assert HintPolicy.evaluate_attempt(hint_level=2, is_correct=False) == "again"
