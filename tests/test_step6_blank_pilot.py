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
    _print_report,
    _to_bank_item,
    _to_rejected_record,
)
from src.contracts import BankItem
from src.generation.batch_client import RejectedCandidateRecord
from src.generation.blanking import sentence_source
from src.generation.blanking.pipeline import DroppedItem, blank_sentences
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


class _FixedSentenceGenerator:
    """A ``sentence_source.SentenceGenerator`` that always returns the same
    fixed list, regardless of theme/person/tense/register/structure --
    lets a test pin exactly which raw sentences ``generate_sentence_pool``
    has to work with, including a deliberately ungrammatical one, without
    depending on the offline mock pool's own (unrelated) content."""

    def __init__(self, sentences: list[str]) -> None:
        self._sentences = sentences

    def generate(
        self,
        cefr: str,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
    ) -> list[str]:
        return list(self._sentences)


def test_main_uses_generate_sentence_pool_not_a_single_flat_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pilot must draw from ``sentence_source.generate_sentence_pool``
    (many small, varied batches), never a single ``generator.generate(...)``
    call for the whole requested count -- the ~60-uniform-sentence pilot
    skew this script existed to fix (module docstring)."""
    calls: list[dict[str, object]] = []
    real_pool_fn = sentence_source.generate_sentence_pool

    def _spy_generate_sentence_pool(
        generator: object, cefr: str, total: int, **kwargs: object
    ) -> object:
        calls.append({"cefr": cefr, "total": total, **kwargs})
        return real_pool_fn(generator, cefr, total, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(step6, "load_env_file", lambda *a, **k: {})
    monkeypatch.setattr(sentence_source, "generate_sentence_pool", _spy_generate_sentence_pool)
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step6_blank_pilot.py",
            "--sentences",
            "10",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
        ],
    )

    exit_code = step6.main()

    assert exit_code == 0
    assert len(calls) == 1, "exactly one generate_sentence_pool call, not one per sentence"
    assert calls[0]["total"] == 10


def test_main_gates_a_carrier_invalid_sentence_before_it_reaches_a_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ungrammatical generated sentence ("kauft ich" -- subject/verb
    disagreement, carrier_validation's own worked example) must never
    produce or contribute to an item: the pilot report's carrier-rejection
    count must include it, and no accepted item's prompt may contain its
    text."""
    bad_sentence = "Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch ein."
    good_sentence = "Der Hund läuft schnell durch den Park."
    fixed_generator = _FixedSentenceGenerator([bad_sentence, good_sentence])

    monkeypatch.setattr(step6, "load_env_file", lambda *a, **k: {})
    monkeypatch.setattr(sentence_source, "build_sentence_generator", lambda client: fixed_generator)
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    review_path = tmp_path / "review.jsonl"
    rejected_path = tmp_path / "rejected.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step6_blank_pilot.py",
            "--sentences",
            "2",
            "--review-file",
            str(review_path),
            "--rejected-file",
            str(rejected_path),
        ],
    )

    exit_code = step6.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Rejected by carrier validation:   1" in captured.out
    assert "subject_verb_disagreement" in captured.out

    review_rows = [json.loads(line) for line in review_path.read_text().splitlines() if line]
    for row in review_rows:
        assert "kauft ich" not in row["prompt"]


def test_print_report_shows_pool_and_cap_and_dedup_sections_distinctly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed report must surface every category task 4 asks for, and
    keep balance-driven drops (caps, cross-topic duplicates) visibly
    separate from quality-driven skips -- a silent cap is exactly the "quiet
    truncation" this project has been bitten by before."""
    report = blank_sentences(["Ich stehe jeden Morgen um sechs Uhr auf."])
    pool = sentence_source.SentencePool(
        sentences=["Ich stehe jeden Morgen um sechs Uhr auf."],
        requested=5,
        raw_generated=6,
        duplicates_skipped=1,
        batches_run=1,
    )
    pool.rejected_by_reason["subject_verb_disagreement"] = 4

    _print_report(report, pool, ran_live=False)
    out = capsys.readouterr().out

    assert "Rejected by carrier validation:   4" in out
    assert "subject_verb_disagreement: 4" in out
    assert "Items dropped as cross-topic duplicates" in out
    assert "verb_praesens_regelm: 1" in out
    assert "Items dropped to the per-topic cap" in out
    assert "Items dropped to the per-source-sentence cap" in out
    assert "Skips by reason" in out


def test_print_report_shows_uniqueness_skips_distinctly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A modal item with no derivable cue is still a genuine uniqueness
    skip (docs/audits/cycle-04-report.md's own finding), and the report must
    show that skip under its own section, not folded into ordinary quality
    skips. 3rd-plural present "müssen" == its own infinitive, so
    ``selectors._citation_cue`` withholds the cue and both
    ``modalverben_praesens`` and ``passiv_modalverben`` -- which both fire on
    this same "müssen" token -- stay unrescued; "Fenster" is also an
    invariant plural (singular == plural), so ``nomen_plural`` is skipped
    too, for the same "no cue" reason, on the same sentence."""
    sentence = "Die Fenster müssen repariert werden."
    report = blank_sentences([sentence])
    pool = sentence_source.SentencePool(
        sentences=[sentence], requested=1, raw_generated=1, duplicates_skipped=0, batches_run=1
    )

    _print_report(report, pool, ran_live=False)
    out = capsys.readouterr().out

    assert "Skips by uniqueness reason" in out
    assert "modal_verb_interchangeable: 2" in out
    assert "plural_noun_open_class: 1" in out
    assert report.items_by_topic.get("modalverben_praesens", 0) == 0
    assert report.items_by_topic.get("passiv_modalverben", 0) == 0
    assert report.items_by_topic.get("nomen_plural", 0) == 0


def test_print_report_shows_a_cued_item_rescued_from_the_uniqueness_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The counterpart to the test above: the same modal slot, but with a
    form that differs from its own infinitive, gets a cue and is kept, not
    skipped."""
    sentence = (
        "Das Fleisch kann scharf angebraten werden, wenn ein kräftiger Geschmack gewünscht wird."
    )
    report = blank_sentences([sentence])
    pool = sentence_source.SentencePool(
        sentences=[sentence], requested=1, raw_generated=1, duplicates_skipped=0, batches_run=1
    )

    _print_report(report, pool, ran_live=False)
    out = capsys.readouterr().out

    assert "modal_verb_interchangeable" not in out
    assert report.items_by_topic.get("passiv_modalverben", 0) == 1


def test_uniqueness_skips_to_skips_preserves_reason_and_answer() -> None:
    skip = step6.UniquenessSkip(
        topic_id="modalverben_praesens",
        prompt="Das Fleisch ___ scharf angebraten werden.",
        proposed_answer="kann",
        reason="modal_verb_interchangeable",
    )
    skips = step6._uniqueness_skips_to_skips([skip])

    assert len(skips) == 1
    assert skips[0].topic_id == "modalverben_praesens"
    assert skips[0].sentence == "Das Fleisch ___ scharf angebraten werden."
    assert skips[0].reason == "modal_verb_interchangeable"
    assert skips[0].proposed_answer == "kann"

    record = _to_rejected_record(skips[0])
    assert record.proposed_answer == "kann"
    assert record.reason == "modal_verb_interchangeable"


def test_dropped_items_to_skips_preserves_reason_and_answer() -> None:
    dropped = [
        DroppedItem(
            topic_id="verb_praesens_regelm",
            prompt="Ich ___ jeden Morgen auf.",
            proposed_answer="stehe",
            reason="cross_topic_duplicate",
            kept_topic_id="verben_trennbar_praesens",
        )
    ]
    skips = step6._dropped_items_to_skips(dropped)

    assert len(skips) == 1
    assert skips[0].topic_id == "verb_praesens_regelm"
    assert skips[0].sentence == "Ich ___ jeden Morgen auf."
    assert skips[0].reason == "cross_topic_duplicate"
    assert skips[0].proposed_answer == "stehe"

    record = _to_rejected_record(skips[0])
    assert record.proposed_answer == "stehe"
    assert record.reason == "cross_topic_duplicate"
