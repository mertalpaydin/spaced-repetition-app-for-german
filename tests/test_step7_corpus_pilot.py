"""Tests for scripts/step7_corpus_pilot.py: the verify-only pilot over
corpus-extracted sentences (TODO 4). Per this task's own brief: unit tests
never touch the network, so the sampling, the provenance plumbing, the
per-topic CEFR filtering and the report writing are all exercised offline,
with a fake verifier standing in for the model."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest
import scripts.step7_corpus_pilot as step7
from scripts.step7_corpus_pilot import (
    CorpusProvenance,
    TopicSampleResult,
    _carrier_hash_id,
    _cefr_rejection_to_record,
    _filter_candidates_by_topic_cefr,
    _lemma_key,
    _sample_per_topic,
    _to_bank_item,
)
from src.contracts import CEFR, BankItem, CandidateItem, Topic
from src.generation.blanking.pipeline import TOPIC_IDS, blank_sentences
from src.generation.blanking.sentence_tagger import analysis_available
from src.lexicon.vocabulary import VocabularyStore

pytestmark = pytest.mark.skipif(
    not analysis_available(),
    reason="spaCy's de_core_news_sm model is not installed in this environment",
)

_SENTENCE = "Der Hund läuft schnell durch den Park."


def _topic(topic_id: str, cefr: CEFR) -> Topic:
    return Topic(id=topic_id, name_de=topic_id, cefr=cefr, description="test topic")


def _candidate(
    topic_id: str,
    *,
    source_sentence_id: str = "sent_abc",
    prompt: str = "p",
    answer: str = "a",
    lemma: str | None = None,
) -> CandidateItem:
    return CandidateItem(
        topic_id=topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=prompt,
        proposed_answer=answer,
        source_sentence_id=source_sentence_id,
        blanked_lemma=lemma,
    )


# --------------------------------------------------------------------------
# Provenance plumbing: the hash join key
# --------------------------------------------------------------------------


def test_carrier_hash_id_matches_the_source_sentence_id_blank_sentences_stamps() -> None:
    """``_carrier_hash_id`` is a deliberate small duplicate of
    ``blanker._source_sentence_id``'s one-line formula (module docstring's
    own "corpus provenance" section explains why it cannot just import it).
    This pins the two never drifting apart: running the real pipeline over a
    known sentence must produce an item whose own ``source_sentence_id``
    equals what this script's own hash function computes for that exact
    sentence text."""
    report = blank_sentences([_SENTENCE])
    assert report.items, "expected at least one item from this fixed sentence"

    expected = _carrier_hash_id(_SENTENCE)
    for item in report.items:
        assert item.source_sentence_id == expected


def test_carrier_hash_id_is_a_stable_deterministic_function_of_text() -> None:
    a = _carrier_hash_id(_SENTENCE)
    b = _carrier_hash_id(_SENTENCE)
    c = _carrier_hash_id("Ein ganz anderer Satz.")
    assert a == b
    assert a != c
    assert a.startswith("sent_")


# --------------------------------------------------------------------------
# Per-topic CEFR filtering
# --------------------------------------------------------------------------


def test_filter_candidates_by_topic_cefr_same_sentence_different_verdict_per_topic() -> None:
    """docs/audits/corpus-coverage.md's own correction, the reason this
    filter exists: the exact same carrier sentence can be an eligible
    candidate for a B1 topic and an ineligible one for an A1 topic, because
    the ceiling is applied against each item's OWN topic, never one global
    ceiling for the whole run."""
    vocab_store = VocabularyStore(vocab={"xyzwort": "B1"})
    sentence = "Die Xyzwort war heute wirklich sehr gut."
    source_id = _carrier_hash_id(sentence)
    provenance_by_hash = {source_id: CorpusProvenance("tatoeba", "1", sentence)}
    topics_by_id = {
        "topic_a1": _topic("topic_a1", "A1"),
        "topic_b1": _topic("topic_b1", "B1"),
    }
    item_a1 = _candidate("topic_a1", source_sentence_id=source_id)
    item_b1 = _candidate("topic_b1", source_sentence_id=source_id)

    result = _filter_candidates_by_topic_cefr(
        [item_a1, item_b1], topics_by_id, provenance_by_hash, vocab_store
    )

    assert result.items_by_topic == {"topic_b1": [item_b1]}
    assert result.cefr_rejected == {"topic_a1": 1}
    assert result.candidates_before_cefr == {"topic_a1": 1, "topic_b1": 1}
    assert len(result.rejection_records) == 1
    assert result.rejection_records[0].topic_id == "topic_a1"
    assert result.rejection_records[0].error_type == "cefr_ceiling_violation"
    reason = result.rejection_records[0].reason
    assert reason is not None
    assert "xyzwort" in reason.lower()


def test_filter_candidates_by_topic_cefr_missing_provenance_is_counted_not_silently_dropped() -> (
    None
):
    topics_by_id = {"topic_a1": _topic("topic_a1", "A1")}
    item = _candidate("topic_a1", source_sentence_id="sent_unknown_hash")

    result = _filter_candidates_by_topic_cefr([item], topics_by_id, {}, VocabularyStore())

    assert result.items_by_topic == {}
    assert result.provenance_missing == 1
    assert result.candidates_before_cefr == {"topic_a1": 1}


def test_filter_candidates_by_topic_cefr_unknown_topic_is_skipped() -> None:
    item = _candidate("not_a_real_topic")
    result = _filter_candidates_by_topic_cefr([item], {}, {}, VocabularyStore())
    assert result.items_by_topic == {}
    assert result.candidates_before_cefr == {}


def test_cefr_rejection_to_record_shape() -> None:
    item = _candidate("topic_a1", prompt="Die ___ war gut.", answer="Xyzwort")
    topic = _topic("topic_a1", "A1")
    record = _cefr_rejection_to_record(item, ["Xyzwort"], topic)

    assert record.topic_id == "topic_a1"
    assert record.error_type == "cefr_ceiling_violation"
    reason = record.reason
    assert reason is not None
    assert "A1" in reason
    assert "Xyzwort" in reason
    assert record.proposed_answer == "Xyzwort"


# --------------------------------------------------------------------------
# Balanced sampling
# --------------------------------------------------------------------------


def test_sample_per_topic_takes_all_when_under_quota() -> None:
    candidates = [_candidate("topic_a", source_sentence_id=f"sent_{i}") for i in range(3)]
    items_by_topic = {"topic_a": candidates}
    topics_by_id = {"topic_a": _topic("topic_a", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["topic_a"], quota=10, seed=1
    )

    assert sampled == candidates
    assert len(results) == 1
    assert results[0].candidates == 3
    assert results[0].sampled == 3
    assert results[0].shortfall == 7


def test_sample_per_topic_reports_a_topic_with_zero_candidates() -> None:
    topics_by_id = {"topic_empty": _topic("topic_empty", "B2")}

    sampled, results = _sample_per_topic(
        {}, {}, {}, topics_by_id, ["topic_empty"], quota=10, seed=1
    )

    assert sampled == []
    assert len(results) == 1
    result = results[0]
    assert result.topic_id == "topic_empty"
    assert result.candidates == 0
    assert result.sampled == 0
    assert result.shortfall == 10


def test_sample_per_topic_caps_at_quota_when_over() -> None:
    candidates = [_candidate("topic_a", source_sentence_id=f"sent_{i}") for i in range(20)]
    items_by_topic = {"topic_a": candidates}
    topics_by_id = {"topic_a": _topic("topic_a", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["topic_a"], quota=5, seed=1
    )

    assert len(sampled) == 5
    assert results[0].shortfall == 0
    # every sampled item really did come from the candidate pool
    assert {id(s) for s in sampled} <= {id(c) for c in candidates}


def test_sample_per_topic_is_deterministic_for_the_same_seed() -> None:
    candidates = [_candidate("topic_a", source_sentence_id=f"sent_{i}") for i in range(20)]
    items_by_topic = {"topic_a": candidates}
    topics_by_id = {"topic_a": _topic("topic_a", "A1")}

    first, _ = _sample_per_topic(items_by_topic, {}, {}, topics_by_id, ["topic_a"], quota=5, seed=7)
    second, _ = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["topic_a"], quota=5, seed=7
    )
    third, _ = _sample_per_topic(items_by_topic, {}, {}, topics_by_id, ["topic_a"], quota=5, seed=8)

    assert [c.prompt for c in first] == [c.prompt for c in second]
    assert [c.source_sentence_id for c in first] == [c.source_sentence_id for c in second]
    # A different seed should (overwhelmingly likely, sampling 5 of 20)
    # choose a different subset.
    assert {c.source_sentence_id for c in first} != {c.source_sentence_id for c in third}


def test_sample_per_topic_covers_every_requested_topic_even_with_no_candidates_anywhere() -> None:
    topics_by_id = {t: _topic(t, "A1") for t in ["topic_a", "topic_b", "topic_c"]}
    _, results = _sample_per_topic({}, {}, {}, topics_by_id, list(topics_by_id), quota=10, seed=1)
    assert {r.topic_id for r in results} == {"topic_a", "topic_b", "topic_c"}
    assert all(r.sampled == 0 and r.shortfall == 10 for r in results)


def test_topic_sample_result_shortfall_is_never_negative() -> None:
    result = TopicSampleResult(
        topic_id="t",
        cefr="A1",
        candidates_before_cefr=5,
        cefr_rejected=0,
        candidates=12,
        quota=10,
        sampled=10,
    )
    assert result.shortfall == 0


# --------------------------------------------------------------------------
# TODO.md 8.11: the per-lemma diversity cap, with backfill and a floor
# --------------------------------------------------------------------------


def test_lemma_key_uses_blanked_lemma_when_present() -> None:
    item = _candidate("t", answer="läuft", lemma="laufen")
    assert _lemma_key(item) == "laufen"


def test_lemma_key_falls_back_to_lowercased_answer_when_no_lemma() -> None:
    """The rare candidate whose token lemmatised to nothing at all still
    needs a usable grouping key -- the lowercased surface answer, not a
    shared placeholder that would wrongly lump every lemma-less candidate
    into one artificial 'lemma'."""
    item = _candidate("t", answer="Foo", lemma=None)
    assert _lemma_key(item) == "foo"


def test_sample_per_topic_caps_items_per_lemma_when_diversity_allows_it() -> None:
    """3 distinct lemmas, 5 candidates each (15 total), quota 6, cap 2:
    3 lemmas x cap 2 = 6 = quota exactly, so the cap should be fully
    honoured with no floor backfill needed at all."""
    candidates = []
    for lemma in ("laufen", "geben", "nehmen"):
        for i in range(5):
            candidates.append(_candidate("t", source_sentence_id=f"{lemma}_{i}", lemma=lemma))
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=6, seed=1, max_items_per_lemma=2
    )

    assert len(sampled) == 6
    counts = Counter(_lemma_key(item) for item in sampled)
    assert all(c <= 2 for c in counts.values())
    assert results[0].distinct_lemmas_sampled == 3
    assert results[0].max_lemma_share_sampled == 2
    assert results[0].lemma_diversity_capped is False


def test_sample_per_topic_backfills_freed_slots_to_still_meet_quota() -> None:
    """2 distinct lemmas, 5 candidates each (10 total), quota 8, cap 2:
    the cap alone (2 lemmas x cap 2 = 4) cannot reach quota 8, so the freed
    slots must be backfilled from the SAME two lemmas (there is nothing
    else to draw from) rather than leaving the topic short at 4."""
    candidates = []
    for lemma in ("laufen", "geben"):
        for i in range(5):
            candidates.append(_candidate("t", source_sentence_id=f"{lemma}_{i}", lemma=lemma))
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=8, seed=1, max_items_per_lemma=2
    )

    # Capping without backfilling would have shrunk this topic to 4 -- the
    # quota (8) must still be met from the two lemmas available.
    assert len(sampled) == 8
    assert results[0].sampled == 8
    assert results[0].lemma_diversity_capped is True
    counts = Counter(_lemma_key(item) for item in sampled)
    assert set(counts) == {"laufen", "geben"}


def test_sample_per_topic_never_starves_a_topic_with_one_dominant_lemma() -> None:
    """The brief's own named condition: a topic whose entire candidate pool
    is one lemma (e.g. a determiner-family topic, where every answer
    lemmatises to the family's own citation form) must still fill its
    quota -- never cut down to ``cap`` items just because diversity is
    impossible."""
    candidates = [_candidate("t", source_sentence_id=f"s{i}", lemma="der") for i in range(20)]
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=10, seed=1, max_items_per_lemma=3
    )

    assert len(sampled) == 10
    assert results[0].sampled == 10
    assert results[0].shortfall == 0
    assert results[0].distinct_lemmas_sampled == 1
    assert results[0].max_lemma_share_sampled == 10
    assert results[0].lemma_diversity_capped is True


def test_sample_per_topic_lemma_cap_does_not_apply_when_already_under_quota() -> None:
    """A topic at or under quota already takes everything it has (the
    pre-existing behaviour) -- the lemma cap must never additionally shrink
    a topic that had nothing spare to diversify with in the first place."""
    candidates = [_candidate("t", source_sentence_id=f"s{i}", lemma="der") for i in range(4)]
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    sampled, results = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=10, seed=1, max_items_per_lemma=1
    )

    assert sampled == candidates
    assert results[0].lemma_diversity_capped is False


def test_sample_per_topic_lemma_cap_is_deterministic_for_the_same_seed() -> None:
    candidates = []
    for lemma in ("laufen", "geben", "nehmen", "helfen"):
        for i in range(6):
            candidates.append(_candidate("t", source_sentence_id=f"{lemma}_{i}", lemma=lemma))
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    first, _ = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=8, seed=7, max_items_per_lemma=2
    )
    second, _ = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=8, seed=7, max_items_per_lemma=2
    )
    third, _ = _sample_per_topic(
        items_by_topic, {}, {}, topics_by_id, ["t"], quota=8, seed=8, max_items_per_lemma=2
    )

    assert [c.source_sentence_id for c in first] == [c.source_sentence_id for c in second]
    assert {c.source_sentence_id for c in first} != {c.source_sentence_id for c in third}


def test_sample_per_topic_default_lemma_cap_still_reports_distinct_lemmas() -> None:
    """Every topic gets the lemma-diversity fields on its own
    ``TopicSampleResult``, even a caller that does not pass
    ``max_items_per_lemma`` explicitly (the CLI default applies)."""
    candidates = [_candidate("t", source_sentence_id=f"s{i}", lemma="x") for i in range(3)]
    items_by_topic = {"t": candidates}
    topics_by_id = {"t": _topic("t", "A1")}

    _, results = _sample_per_topic(items_by_topic, {}, {}, topics_by_id, ["t"], quota=10, seed=1)

    assert results[0].distinct_lemmas_in_pool == 1
    assert results[0].distinct_lemmas_sampled == 1
    assert results[0].max_lemma_share_sampled == 3


# --------------------------------------------------------------------------
# Provenance survives onto the BankItem
# --------------------------------------------------------------------------


def test_to_bank_item_carries_corpus_provenance_without_overloading_source_sentence_id() -> None:
    report = blank_sentences([_SENTENCE])
    assert report.items
    item = report.items[0]
    topic = _topic(item.topic_id, "A1")

    bank_item = _to_bank_item(item, topic, "tatoeba", "12345")

    assert isinstance(bank_item, BankItem)
    dumped = bank_item.model_dump(mode="json")
    assert dumped["corpus_source"] == "tatoeba"
    assert dumped["corpus_line_id"] == "12345"
    # source_sentence_id keeps its OWN, pre-existing meaning -- the carrier's
    # content hash, not a corpus locator (module docstring's own point).
    assert dumped["source_sentence_id"] == item.source_sentence_id
    assert dumped["source_sentence_id"] != "12345"
    assert bank_item.prompt == item.prompt
    assert bank_item.accepted_answers == [item.proposed_answer]


def test_to_bank_item_id_is_prefixed_corpus() -> None:
    report = blank_sentences([_SENTENCE])
    assert report.items
    item = report.items[0]
    topic = _topic(item.topic_id, "A1")

    bank_item = _to_bank_item(item, topic, "leipzig", "9")
    assert bank_item.id.startswith("corpus_")


# --------------------------------------------------------------------------
# End-to-end, offline: main()
# --------------------------------------------------------------------------


@pytest.fixture()
def _tiny_corpora(tmp_path: Path) -> tuple[Path, Path]:
    tatoeba = tmp_path / "tatoeba.tsv"
    tatoeba.write_text(
        "\n".join(
            [
                "1\tdeu\tDer Hund läuft schnell durch den Park.",
                "2\tdeu\tDie Sonne scheint heute hell über der Stadt.",
                "3\tdeu\tEin Mann steht vor der Tür und wartet.",
                "4\tdeu\tIch stehe jeden Morgen um sechs Uhr auf.",
                "5\tdeu\tNachdem er die Tür geschlossen hatte, setzte er sich hin.",
            ]
        ),
        encoding="utf-8",
    )
    leipzig = tmp_path / "leipzig.txt"
    leipzig.write_text(
        "\n".join(
            [
                "101\tDas Fest wird jedes Jahr im Park organisiert.",
                "102\tDie Tür ist schon repariert.",
                "103\tMeine Schwester ist gestern nach Berlin gefahren.",
            ]
        ),
        encoding="utf-8",
    )
    return tatoeba, leipzig


def _clear_gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    # main() calls load_env_file() unconditionally, which would otherwise
    # refill the just-cleared vars from this container's real .env (same
    # reasoning as tests/test_step6_blank_pilot.py's identical no-op).
    monkeypatch.setattr(step7, "load_env_file", lambda *a, **k: {})


def test_main_offline_writes_review_rejected_and_report_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)

    review_path = tmp_path / "review.jsonl"
    rejected_path = tmp_path / "rejected.jsonl"
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--tatoeba",
            str(tatoeba),
            "--leipzig",
            str(leipzig),
            "--limit",
            "100",
            "--per-topic-quota",
            "2",
            "--review-file",
            str(review_path),
            "--rejected-file",
            str(rejected_path),
            "--report-file",
            str(report_path),
        ],
    )

    exit_code = step7.main()

    # No API key configured -> verification never ran -> not a valid run.
    assert exit_code != 0
    assert review_path.exists()
    assert rejected_path.exists()
    assert report_path.exists()

    review_rows = [json.loads(line) for line in review_path.read_text().splitlines() if line]
    assert review_rows, "expected at least one accepted item from this fixed corpus"
    for row in review_rows:
        assert row["corpus_source"] in ("tatoeba", "leipzig")
        assert row["corpus_line_id"]
        assert row["id"].startswith("corpus_")

    report = json.loads(report_path.read_text())
    assert report["run"]["ran_live"] is False
    assert report["run"]["per_topic_quota"] == 2
    assert len(report["per_topic"]) == len(TOPIC_IDS)
    assert {t["topic_id"] for t in report["per_topic"]} == set(TOPIC_IDS)
    assert report["verification"]["attempted"] is False
    assert report["verification"]["not_run_count"] == report["sampled_total"]
    assert report["output_files"]["review"] == str(review_path)
    assert report["output_files"]["rejected"] == str(rejected_path)


def test_main_all_49_topics_represented_even_with_shortfalls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The balanced sampler's own promise (module docstring): every one of
    the 49 topics gets a row, whether or not this tiny corpus actually has a
    candidate for it -- a shortfall is reported, never silently absorbed."""
    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--tatoeba",
            str(tatoeba),
            "--leipzig",
            str(leipzig),
            "--limit",
            "100",
            "--per-topic-quota",
            "10",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
    )

    step7.main()

    report = json.loads((tmp_path / "report.json").read_text())
    per_topic = {t["topic_id"]: t for t in report["per_topic"]}
    assert len(per_topic) == 49
    # This tiny corpus cannot possibly fill every topic's quota of 10 --
    # confirm the shortfall is visible, not hidden.
    assert any(t["shortfall"] > 0 for t in per_topic.values())
    for t in per_topic.values():
        assert t["quota"] == 10
        assert t["sampled"] <= t["quota"]
        assert t["sampled"] == t["candidates"] - max(0, t["candidates"] - t["quota"])


def test_main_uses_fake_verifier_and_reports_rejections_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """A fake, always-attempted verifier (this task's own brief: 'with a
    fake verifier') that rejects everything must still exit 0 -- a model
    rejection is not a run failure, only a skipped backstop is (mirrors
    step6_blank_pilot's identical rule)."""
    from src.generation.blanking.model_verification import (
        ItemVerdict,
        ModelRejection,
        VerificationReport,
    )

    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)

    def _fake_verify_items(
        items: list[BankItem], llm_client: object, *, batch_size: int = 20
    ) -> VerificationReport:
        verdicts = [ItemVerdict(outcome="rejected", reason="nicht plausibel") for _ in items]
        rejections = [
            ModelRejection(
                topic_id=item.topic_id,
                prompt=item.prompt,
                accepted_answers=tuple(item.accepted_answers),
                reason="nicht plausibel",
            )
            for item in items
        ]
        return VerificationReport(attempted=True, verdicts=verdicts, rejections=rejections)

    monkeypatch.setattr(step7, "verify_items", _fake_verify_items)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--tatoeba",
            str(tatoeba),
            "--leipzig",
            str(leipzig),
            "--limit",
            "100",
            "--per-topic-quota",
            "2",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
    )

    exit_code = step7.main()
    assert exit_code == 0

    report = json.loads((tmp_path / "report.json").read_text())
    assert report["verification"]["attempted"] is True
    assert report["verification"]["not_run_count"] == 0
    assert report["accepted_total"] == 0
    assert report["verification"]["rejected_count"] == report["sampled_total"]

    review_rows = [
        json.loads(line) for line in (tmp_path / "review.jsonl").read_text().splitlines() if line
    ]
    assert review_rows == []

    rejected_rows = [
        json.loads(line) for line in (tmp_path / "rejected.jsonl").read_text().splitlines() if line
    ]
    model_rejections = [
        r for r in rejected_rows if r["error_type"] == "model_verification_rejected"
    ]
    assert len(model_rejections) == report["sampled_total"]


def test_main_missing_corpus_file_degrades_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_gemini_env(monkeypatch)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--tatoeba",
            str(tmp_path / "does_not_exist.tsv"),
            "--skip-leipzig",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
    )

    exit_code = step7.main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "WARNING" in captured.out
    assert "nothing to do" in captured.out.lower()
