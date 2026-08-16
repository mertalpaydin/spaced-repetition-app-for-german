"""Tests for scripts/step6_blank_pilot.py's persistence: writing the
generate-then-blank pilot's accepted items and skipped/rejected candidates to
JSONL files in the same shape as data/pilot_review.jsonl and
data/pilot_rejected.jsonl (docs/audits cycle-2's blocking gap: the script
printed a report and wrote nothing)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import scripts.step6_blank_pilot as step6
from scripts.step6_blank_pilot import (
    _blank_pilot_run_id,
    _blank_sentences_with_skip_detail,
    _DetailedSkip,
    _to_bank_item,
    _to_rejected_record,
)
from src.contracts import BankItem
from src.generation.batch_client import RejectedCandidateRecord
from src.taxonomy.loader import load_taxonomy

_SENTENCES = [
    "Der Hund läuft schnell durch den Park.",
    "Die Sonne scheint heute hell über der Stadt.",
    "Ein Mann steht vor der Tür und wartet.",
]


def test_blank_sentences_with_skip_detail_matches_aggregate_counts() -> None:
    """The per-item skip list this function adds must sum to exactly the
    same totals as ``BlankingReport.skips_by_reason`` (the aggregate the
    real, unmodified ``blank_sentences`` also produces) and ``report.items``
    must match ``items_by_topic``'s total -- this is a faithful mirror of
    that loop, not an independent, possibly-diverging reimplementation."""
    report, skips = _blank_sentences_with_skip_detail(_SENTENCES)

    assert len(report.items) == sum(report.items_by_topic.values())
    assert len(skips) == sum(report.skips_by_reason.values())

    skip_counts: dict[str, int] = {}
    for skip in skips:
        skip_counts[skip.reason] = skip_counts.get(skip.reason, 0) + 1
    assert skip_counts == dict(report.skips_by_reason)


def test_to_bank_item_derives_cefr_facet_and_confusion_group() -> None:
    """Mapping a generate-then-blank ``CandidateItem`` onto the
    ``BankItem`` shape stamps ``cefr``/``confusion_group`` straight off the
    taxonomy and derives ``facet`` the same way the LLM-direct pipeline's
    bank ingest does, so the two review files carry genuinely comparable
    data, not just the same JSON keys."""
    report, _ = _blank_sentences_with_skip_detail(_SENTENCES)
    assert report.items, "expected at least one produced item for this fixed sentence set"

    topics_by_id = {t.id: t for t in load_taxonomy()}
    item = report.items[0]
    topic = topics_by_id[item.topic_id]

    bank_item = _to_bank_item(item, topics_by_id)

    assert bank_item is not None
    assert bank_item.topic_id == item.topic_id
    assert bank_item.tag_id == item.topic_id
    assert bank_item.dimension == "grammar"
    assert bank_item.cefr == topic.cefr
    assert bank_item.confusion_group == topic.confusion_group
    assert bank_item.accepted_answers == [item.proposed_answer]
    assert bank_item.prompt == item.prompt
    assert bank_item.id.startswith("blank_")
    # facet is derived (may legitimately be None for a topic with an empty
    # facet space), never just copied from the (always-None) CandidateItem
    # default -- confirmed by checking against a topic known to have one.
    if topic.morph_spec or topic.syntax_tags:
        assert bank_item.facet is not None


def test_to_bank_item_returns_none_for_unknown_topic() -> None:
    """A topic_id outside the loaded taxonomy is not silently stamped with
    empty facet/confusion_group -- the caller is told to record it as a skip
    instead."""
    report, _ = _blank_sentences_with_skip_detail(_SENTENCES)
    assert report.items
    item = report.items[0].model_copy(update={"topic_id": "not_a_real_topic"})

    assert _to_bank_item(item, {}) is None


def test_to_rejected_record_shape_matches_pilot_rejected_fields() -> None:
    """``_to_rejected_record`` produces exactly the field set
    ``data/pilot_rejected.jsonl`` uses."""
    skip = _DetailedSkip(topic_id="artikel_bestimmt_nom", sentence="Ein Satz.", reason="x_reason")
    record = _to_rejected_record(skip)

    assert isinstance(record, RejectedCandidateRecord)
    assert record.topic_id == "artikel_bestimmt_nom"
    assert record.prompt == "Ein Satz."
    assert record.proposed_answer == ""
    assert record.layer_failed is None
    assert record.error_type == "x_reason"
    assert record.reason == "x_reason"


def test_blank_pilot_run_id_is_stable_for_the_same_inputs() -> None:
    id_a = _blank_pilot_run_id(_SENTENCES, "A2", "Alltag")
    id_b = _blank_pilot_run_id(_SENTENCES, "A2", "Alltag")
    id_c = _blank_pilot_run_id(_SENTENCES, "B1", "Alltag")

    assert id_a == id_b
    assert id_a != id_c
    assert id_a.startswith("blank_batch_")


def test_main_writes_review_and_rejected_files_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, offline (no API key): the script writes both files, and
    every row in each carries the exact field set of the LLM-direct
    pipeline's review/rejected files, plus a consistent ``_pilot_batch_id``."""
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # main() calls load_env_file() unconditionally, which would otherwise
    # refill the just-cleared vars from this container's real .env
    # (override=False only skips vars that are ALREADY set, and delenv just
    # unset them) and turn this into a live call. No-op it instead.
    monkeypatch.setattr(step6, "load_env_file", lambda *a, **k: {})

    review_path = tmp_path / "review.jsonl"
    rejected_path = tmp_path / "rejected.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step6_blank_pilot.py",
            "--sentences",
            "10",
            "--review-file",
            str(review_path),
            "--rejected-file",
            str(rejected_path),
        ],
    )

    exit_code = step6.main()

    assert exit_code == 0
    assert review_path.exists()
    assert rejected_path.exists()

    review_rows = [json.loads(line) for line in review_path.read_text().splitlines() if line]
    rejected_rows = [json.loads(line) for line in rejected_path.read_text().splitlines() if line]

    assert review_rows, "expected at least one accepted item from the mock sentence pool"
    assert rejected_rows, "expected at least one skip from the mock sentence pool"

    expected_review_keys = set(BankItem.model_fields) | {"_pilot_batch_id"}
    expected_rejected_keys = set(RejectedCandidateRecord.model_fields) | {"_pilot_batch_id"}

    batch_ids = {row["_pilot_batch_id"] for row in review_rows} | {
        row["_pilot_batch_id"] for row in rejected_rows
    }
    assert len(batch_ids) == 1, "every row across both files must share one run id"

    for row in review_rows:
        assert set(row) == expected_review_keys
        assert row["id"].startswith("blank_")
        assert row["accepted_answers"]

    for row in rejected_rows:
        assert set(row) == expected_rejected_keys
        assert row["reason"]
