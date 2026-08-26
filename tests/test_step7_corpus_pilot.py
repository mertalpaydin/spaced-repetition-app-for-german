"""Tests for scripts/step7_corpus_pilot.py: the verify-only pilot over
corpus-extracted sentences (TODO 4). Per this task's own brief: unit tests
never touch the network, so the sampling, the provenance plumbing, the
per-topic CEFR filtering and the report writing are all exercised offline,
with a fake verifier standing in for the model."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
import scripts.step7_corpus_pilot as step7
from scripts.build_translations import TranslationRecord, _load_store
from scripts.step7_corpus_pilot import (
    CorpusProvenance,
    TopicSampleResult,
    _carrier_hash_id,
    _cefr_rejection_to_record,
    _filter_candidates_by_topic_cefr,
    _gloss_rejection_to_record,
    _lemma_key,
    _populate_glosses,
    _run_gloss_check,
    _sample_per_topic,
    _to_bank_item,
)
from src.contracts import CEFR, BankItem, CandidateItem, Topic
from src.generation.blanking.pipeline import TOPIC_IDS, blank_sentences
from src.generation.blanking.sentence_tagger import analysis_available
from src.lexicon.vocabulary import VocabularyStore
from src.llm.translation import TranslationError
from src.taxonomy.loader import load_taxonomy
from src.verification.pipeline import GLOSS_REJECTION_PREFIX

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
    # TODO.md 2.1b: main() now also builds a TRANSLATOR from the environment
    # (``build_translations.translator_from_env`` -> ``azure_from_env``,
    # which reads ``os.environ`` directly). Leaving this key set would let a
    # developer whose shell happens to carry it turn these offline tests
    # into real Azure HTTP calls, which CLAUDE.md section 7 forbids
    # outright. Cleared here rather than in each test so no future test can
    # forget it.
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
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
            # TODO.md 2.1b: never the real default store -- a developer with
            # a populated data/fixtures/translations/de_en.jsonl would
            # otherwise get glosses (and therefore live gloss checks) these
            # tests do not control.
            "--translations",
            str(tmp_path / "translations.jsonl"),
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
            # TODO.md 2.1b: never the real default store -- a developer with
            # a populated data/fixtures/translations/de_en.jsonl would
            # otherwise get glosses (and therefore live gloss checks) these
            # tests do not control.
            "--translations",
            str(tmp_path / "translations.jsonl"),
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
        items: list[BankItem],
        llm_client: object,
        *,
        batch_size: int = 20,
        use_cache: bool = True,
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
            # TODO.md 2.1b: never the real default store -- a developer with
            # a populated data/fixtures/translations/de_en.jsonl would
            # otherwise get glosses (and therefore live gloss checks) these
            # tests do not control.
            "--translations",
            str(tmp_path / "translations.jsonl"),
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
            # TODO.md 2.1b: never the real default store -- a developer with
            # a populated data/fixtures/translations/de_en.jsonl would
            # otherwise get glosses (and therefore live gloss checks) these
            # tests do not control.
            "--translations",
            str(tmp_path / "translations.jsonl"),
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


# --------------------------------------------------------------------------
# TODO.md 2.1b: the English gloss
#
# Every test here is offline. The translator is a fake that records what it
# was asked to translate (CLAUDE.md section 7: a unit test that makes a real
# API call is a defect), and the store is a tmp_path file so the real
# data/fixtures/translations/de_en.jsonl is never read or written.
# --------------------------------------------------------------------------

_CARRIER = "Der Hund läuft schnell durch den Park."
_BLANKED_PROMPT = "Der Hund ___ schnell durch den Park."


class _FakeTranslator:
    """Records every batch it was handed and answers deterministically.

    ``fail_on`` makes ``translate`` raise ``TranslationError`` for any batch
    containing that sentence, which is how the degrade path is exercised
    without a provider.
    """

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.batches: list[list[str]] = []
        self.fail_on = fail_on

    def translate(self, sentences: Sequence[str]) -> list[str]:
        batch = list(sentences)
        self.batches.append(batch)
        if self.fail_on is not None and self.fail_on in batch:
            raise TranslationError("fake provider refused this batch")
        return [f"EN::{s}" for s in batch]

    @property
    def sentences_seen(self) -> list[str]:
        return [s for batch in self.batches for s in batch]


def _stored(store_path: Path, records: dict[str, str], source: str = "tatoeba") -> None:
    """Write a translation store containing exactly ``records``."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        TranslationRecord(
            german=german,
            english=english,
            source=source,  # type: ignore[arg-type]
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        ).model_dump_json()
        for german, english in records.items()
    ]
    store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _glossable_item(
    *, prompt: str = _BLANKED_PROMPT, carrier: str = _CARRIER, topic_id: str = "topic_a"
) -> tuple[BankItem, dict[str, CorpusProvenance]]:
    """One BankItem plus the provenance map that carries ITS OWN carrier
    sentence, joined the way ``main()`` joins them (the carrier's content
    hash, not a corpus id)."""
    source_id = _carrier_hash_id(carrier)
    item = BankItem(
        id="corpus_test",
        topic_id=topic_id,
        tag_id=topic_id,
        type="cloze_free",
        difficulty=1,
        cefr="A1",
        prompt=prompt,
        accepted_answers=["läuft"],
        source_sentence_id=source_id,
    )
    return item, {source_id: CorpusProvenance("tatoeba", "42", carrier)}


def test_populate_glosses_carrier_in_store_uses_that_gloss_and_calls_no_translator(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.jsonl"
    _stored(store_path, {_CARRIER: "The dog runs quickly through the park."})
    item, provenance = _glossable_item()
    translator = _FakeTranslator()

    glossed, report = _populate_glosses(
        [item],
        provenance,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert glossed[0].gloss_en == "The dog runs quickly through the park."
    assert translator.batches == [], "a carrier already in the store must never be translated"
    assert report.gloss_from_store == 1
    assert report.gloss_newly_translated == 0
    assert report.gloss_missing == 0
    assert report.characters_spent == 0
    assert report.carriers_needing_translation == 0


def test_populate_glosses_carrier_missing_from_store_is_translated_and_written_back(
    tmp_path: Path,
) -> None:
    """The store is the resumability mechanism (build_translations.py's own
    'the store itself is the pointer'): a gloss this run had to pay for must
    be on disk afterwards, with the right ``source``, so the next run gets it
    for free."""
    store_path = tmp_path / "store.jsonl"
    item, provenance = _glossable_item()
    translator = _FakeTranslator()

    glossed, report = _populate_glosses(
        [item],
        provenance,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert glossed[0].gloss_en == f"EN::{_CARRIER}"
    assert translator.sentences_seen == [_CARRIER]
    assert report.gloss_newly_translated == 1
    assert report.gloss_from_store == 0
    assert report.gloss_missing == 0
    assert report.characters_spent == len(_CARRIER)

    written = _load_store(store_path)
    assert set(written) == {_CARRIER}
    assert written[_CARRIER].english == f"EN::{_CARRIER}"
    # Azure was the primary and it did not fail over, so the record must say
    # azure -- not "gemini", and not the untouched "tatoeba" default.
    assert written[_CARRIER].source == "azure"


def test_populate_glosses_gemini_only_mode_records_gemini_as_the_source(tmp_path: Path) -> None:
    store_path = tmp_path / "store.jsonl"
    item, provenance = _glossable_item()

    _populate_glosses(
        [item],
        provenance,
        store_path=store_path,
        translator=_FakeTranslator(),
        translator_mode="gemini_only",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
        batch_size=1,
    )

    assert _load_store(store_path)[_CARRIER].source == "gemini"


def test_populate_glosses_no_translator_leaves_a_store_missing_item_at_none(
    tmp_path: Path,
) -> None:
    """The ``--no-translate`` path: main() passes ``translator=None``, the
    store is still consulted, and anything it lacks stays ``None`` rather
    than being invented or crashing."""
    store_path = tmp_path / "store.jsonl"
    _stored(store_path, {"Ein ganz anderer Satz.": "A completely different sentence."})
    item, provenance = _glossable_item()

    glossed, report = _populate_glosses(
        [item],
        provenance,
        store_path=store_path,
        translator=None,
        translator_mode="none",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert glossed[0].gloss_en is None
    assert report.gloss_missing == 1
    assert report.gloss_from_store == 0
    assert report.gloss_newly_translated == 0
    assert report.characters_spent == 0
    assert report.skipped_for_budget == 1
    # The store must be left exactly as it was -- nothing to add, so nothing
    # rewritten.
    assert set(_load_store(store_path)) == {"Ein ganz anderer Satz."}


def test_populate_glosses_translation_error_leaves_that_batch_at_none_without_raising(
    tmp_path: Path,
) -> None:
    """A refused batch must degrade, never crash: those items keep
    ``gloss_en=None``, the failure is counted with an example message, and
    every OTHER batch's translations still land."""
    store_path = tmp_path / "store.jsonl"
    other_carrier = "Die Sonne scheint heute hell über der Stadt."
    bad_item, bad_provenance = _glossable_item()
    good_item, good_provenance = _glossable_item(
        prompt="Die Sonne ___ heute hell über der Stadt.", carrier=other_carrier
    )
    provenance = {**bad_provenance, **good_provenance}
    translator = _FakeTranslator(fail_on=_CARRIER)

    glossed, report = _populate_glosses(
        [bad_item, good_item],
        provenance,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
        # One carrier per batch, so the refusal is isolated to the first.
        batch_size=1,
    )

    assert glossed[0].gloss_en is None
    assert glossed[1].gloss_en == f"EN::{other_carrier}"
    assert report.translation_failures == 1
    assert report.translation_failure_examples
    assert "refused" in report.translation_failure_examples[0]
    assert report.gloss_missing == 1
    assert report.gloss_newly_translated == 1
    # A refused batch spent no real quota, so it is not charged.
    assert report.characters_spent == len(other_carrier)
    assert set(_load_store(store_path)) == {other_carrier}


def test_populate_glosses_character_budget_stops_at_a_whole_batch_boundary(
    tmp_path: Path,
) -> None:
    """build_translations.py's own rule, inherited unchanged: a run stops
    once the NEXT batch would exceed the budget, and never attempts a partial
    batch to top the remainder up."""
    store_path = tmp_path / "store.jsonl"
    carriers = [f"Der Hund laeuft heute wirklich sehr schnell Nummer {i}." for i in range(6)]
    items: list[BankItem] = []
    provenance: dict[str, CorpusProvenance] = {}
    for i, carrier in enumerate(carriers):
        item, prov = _glossable_item(carrier=carrier)
        items.append(item.model_copy(update={"id": f"corpus_{i}"}))
        provenance.update(prov)

    per_batch = sum(len(c) for c in carriers[:2])
    translator = _FakeTranslator()

    glossed, report = _populate_glosses(
        items,
        provenance,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        # Room for two whole batches of 2 and then some, but not for a
        # third: the third must not be started, and must NOT be trimmed to
        # whatever the remainder would afford.
        max_characters=per_batch * 2 + 10,
        now=datetime(2026, 8, 24, tzinfo=UTC),
        batch_size=2,
    )

    assert [len(batch) for batch in translator.batches] == [2, 2]
    assert report.gloss_newly_translated == 4
    assert report.gloss_missing == 2
    assert report.skipped_for_budget == 2
    assert report.characters_spent == per_batch * 2
    assert [g.gloss_en is not None for g in glossed] == [True, True, True, True, False, False]


def test_populate_glosses_glosses_the_carrier_sentence_never_the_blanked_prompt(
    tmp_path: Path,
) -> None:
    """THE test for this task.

    ``item.prompt`` is the carrier with the answer word replaced by ``___``.
    Translating THAT would attach fluent English for a sentence missing
    exactly the word the exercise is about, to every item in the bank -- a
    defect that is invisible in every count and wrong in every row. Pinned
    from both directions at once:

    - the store is seeded with a DECOY record keyed on the blanked prompt,
      so a lookup that used the prompt would find it and silently win;
    - the translator records what it was asked for, so a run that fell
      through to translation with the prompt would be caught there too.
    """
    store_path = tmp_path / "store.jsonl"
    _stored(
        store_path,
        {
            _CARRIER: "THE CARRIER GLOSS",
            _BLANKED_PROMPT: "THE PROMPT GLOSS (must never be chosen)",
        },
    )
    item, provenance = _glossable_item()
    translator = _FakeTranslator()

    glossed, _report = _populate_glosses(
        [item],
        provenance,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert glossed[0].gloss_en == "THE CARRIER GLOSS"
    assert glossed[0].prompt == _BLANKED_PROMPT, "the prompt itself must be left alone"
    assert translator.batches == []

    # And the same thing again with an empty store, so the translation path
    # is checked too rather than only the lookup path.
    empty_store = tmp_path / "empty.jsonl"
    fresh_translator = _FakeTranslator()
    glossed_fresh, _ = _populate_glosses(
        [item],
        provenance,
        store_path=empty_store,
        translator=fresh_translator,
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert fresh_translator.sentences_seen == [_CARRIER]
    assert all("___" not in s for s in fresh_translator.sentences_seen)
    assert glossed_fresh[0].gloss_en == f"EN::{_CARRIER}"


def test_populate_glosses_item_without_provenance_stays_at_none(tmp_path: Path) -> None:
    item, _ = _glossable_item()
    glossed, report = _populate_glosses(
        [item],
        {},
        store_path=tmp_path / "store.jsonl",
        translator=_FakeTranslator(),
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert glossed[0].gloss_en is None
    assert report.gloss_missing == 1


def test_populate_glosses_fill_counters_are_disjoint_and_sum_to_items_total(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "store.jsonl"
    _stored(store_path, {_CARRIER: "The dog runs."})
    stored_item, stored_prov = _glossable_item()
    new_carrier = "Ein Mann steht vor der Tuer und wartet."
    new_item, new_prov = _glossable_item(prompt="Ein Mann ___ vor der Tuer.", carrier=new_carrier)
    orphan_item, _ = _glossable_item(carrier="Diesen Satz kennt niemand.")

    _glossed, report = _populate_glosses(
        [stored_item, new_item, orphan_item],
        {**stored_prov, **new_prov},
        store_path=store_path,
        translator=_FakeTranslator(),
        translator_mode="fallback",
        max_characters=100_000,
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )

    assert report.items_total == 3
    assert report.gloss_from_store == 1
    assert report.gloss_newly_translated == 1
    assert report.gloss_missing == 1
    assert (
        report.gloss_from_store + report.gloss_newly_translated + report.gloss_missing
        == report.items_total
    )


# --------------------------------------------------------------------------
# TODO.md 2.1b: the gloss consistency check, and its reporting
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _real_topics() -> dict[str, Topic]:
    return {t.id: t for t in load_taxonomy()}


def _futur_item(gloss_en: str | None) -> BankItem:
    """A Futur I item whose topic fixes ``Tense``, so the gloss check has
    something determinate to check (see gloss_validation's own tests, which
    use this exact carrier/answer/gloss pair for the same reason)."""
    return BankItem(
        id="corpus_futur",
        topic_id="futur_i",
        tag_id="futur_i",
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Nächstes Jahr ___ (werden) ich nach Spanien reisen.",
        accepted_answers=["werde"],
        gloss_en=gloss_en,
    )


def test_run_gloss_check_counts_a_contradicting_gloss_as_a_rejection(
    _real_topics: dict[str, Topic],
) -> None:
    """A present-tense gloss for a future-tense target contradicts the
    answer, and a wrong gloss actively teaches the wrong thing -- so it is a
    rejection, carrying the shared prefix a caller counts on."""
    outcomes = _run_gloss_check([_futur_item("I travel to Spain.")], _real_topics)

    assert len(outcomes) == 1
    assert outcomes[0].checked is True
    assert outcomes[0].reason is not None
    assert outcomes[0].reason.startswith(GLOSS_REJECTION_PREFIX)
    assert outcomes[0].error_type == "pedagogical_flaw"
    assert outcomes[0].topic_id == "futur_i"


def test_run_gloss_check_accepts_a_consistent_gloss(_real_topics: dict[str, Topic]) -> None:
    outcomes = _run_gloss_check([_futur_item("Next year I will travel to Spain.")], _real_topics)
    assert outcomes[0].reason is None
    assert outcomes[0].checked is True


def test_run_gloss_check_is_a_no_op_for_an_item_with_no_gloss(
    _real_topics: dict[str, Topic],
) -> None:
    """The pre-2.1b state of every corpus item: no gloss, nothing checked,
    nothing rejected. This is the baseline the whole task moves away from."""
    outcomes = _run_gloss_check([_futur_item(None)], _real_topics)
    assert outcomes[0].checked is False
    assert outcomes[0].reason is None
    assert outcomes[0].unverified_dimensions == 0


def test_gloss_rejection_to_record_carries_the_offending_gloss() -> None:
    item = _futur_item("I travel to Spain.")
    outcome = _run_gloss_check([item], {t.id: t for t in load_taxonomy()})[0]
    record = _gloss_rejection_to_record(item, outcome)

    assert record.gloss_en == "I travel to Spain."
    assert record.reason is not None
    assert record.reason.startswith(GLOSS_REJECTION_PREFIX)
    assert record.topic_id == "futur_i"


def _run_main_with_glosses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corpora: tuple[Path, Path],
    *,
    store: dict[str, str],
    extra_args: list[str] | None = None,
    batch_sizes: list[int] | None = None,
    use_cache_values: list[bool] | None = None,
    reject_per_pass: list[dict[int, str]] | None = None,
    pass_errors: dict[int, Exception] | None = None,
) -> dict[str, object]:
    """Run ``main()`` end to end, offline, against a controlled store and a
    fake verifier that accepts everything, and return the written report.

    ``batch_sizes``, when given, receives the ``batch_size`` this run actually
    handed ``verify_items``. The fake verifier has to be the one recording it:
    a test that patches ``step7.verify_items`` itself before calling this
    helper is silently overwritten by the patch below, which is exactly the
    kind of test that passes while measuring nothing.

    ``use_cache_values`` records the same way, for the ``use_cache`` argument
    each pass was called with (TODO.md 2.3): pass 1 must get ``True`` and
    every later pass ``False``, or a repeated pass just replays the cached
    verdict and measures nothing at all.

    ``reject_per_pass``, when given, makes each pass reject a chosen set of
    items instead of accepting everything: entry ``i`` is consulted for pass
    ``i + 1`` and maps an item's POSITION in the item list to that pass's own
    rejection reason. A position absent from the mapping is verified by that
    pass. Position, not prompt text, because the corpus fixture's own item
    set is derived by the real pipeline and a test should not have to predict
    which sentence becomes which item to make two passes disagree. Every pass
    receives the same list in the same order, so a position means the same
    item in every pass.

    ``pass_errors`` maps a 1-based pass number to an exception that pass
    raises, for the degrade-honestly cases."""
    from src.generation.blanking.model_verification import (
        ItemVerdict,
        ModelRejection,
        VerificationReport,
    )

    tatoeba, leipzig = corpora
    _clear_gemini_env(monkeypatch)
    store_path = tmp_path / "store.jsonl"
    _stored(store_path, store)

    calls: list[int] = []

    def _fake_verify_items(
        items: list[BankItem],
        llm_client: object,
        *,
        batch_size: int = 20,
        use_cache: bool = True,
    ) -> VerificationReport:
        pass_number = len(calls) + 1
        calls.append(pass_number)
        if batch_sizes is not None:
            batch_sizes.append(batch_size)
        if use_cache_values is not None:
            use_cache_values.append(use_cache)
        if pass_errors is not None and pass_number in pass_errors:
            raise pass_errors[pass_number]

        rejections_for_this_pass: dict[int, str] = {}
        if reject_per_pass is not None and pass_number <= len(reject_per_pass):
            rejections_for_this_pass = reject_per_pass[pass_number - 1]

        verdicts: list[ItemVerdict] = []
        rejections: list[ModelRejection] = []
        for position, item in enumerate(items):
            if position in rejections_for_this_pass:
                reason = rejections_for_this_pass[position]
                verdicts.append(ItemVerdict(outcome="rejected", reason=reason))
                rejections.append(
                    ModelRejection(
                        topic_id=item.topic_id,
                        prompt=item.prompt,
                        accepted_answers=tuple(item.accepted_answers),
                        reason=reason,
                    )
                )
            else:
                verdicts.append(ItemVerdict(outcome="verified"))
        return VerificationReport(attempted=True, verdicts=verdicts, rejections=rejections)

    monkeypatch.setattr(step7, "verify_items", _fake_verify_items)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--translations",
            str(store_path),
            "--no-translate",
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
            *(extra_args or []),
        ],
    )
    assert step7.main() == 0
    loaded: dict[str, object] = json.loads((tmp_path / "report.json").read_text())
    return loaded


def test_main_no_translate_reports_a_gloss_section_and_calls_no_translator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """``--no-translate`` with an empty store: every item keeps
    ``gloss_en=None`` (the pre-2.1b state), and the report says so in its own
    section instead of leaving it unstated."""
    report = _run_main_with_glosses(tmp_path, monkeypatch, _tiny_corpora, store={})
    gloss = report["gloss"]
    assert isinstance(gloss, dict)

    assert gloss["translator_mode"] == "none"
    assert gloss["gloss_from_store"] == 0
    assert gloss["gloss_newly_translated"] == 0
    assert gloss["gloss_missing"] == gloss["items_total"]
    assert gloss["characters_spent"] == 0
    assert gloss["rejected_by_gloss_check"] == 0
    assert gloss["items_gloss_checked"] == 0

    review_rows = [
        json.loads(line) for line in (tmp_path / "review.jsonl").read_text().splitlines() if line
    ]
    assert review_rows
    for row in review_rows:
        assert "gloss_en" in row
        assert row["gloss_en"] is None


def test_main_store_hit_attaches_the_gloss_to_the_review_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """A carrier the store knows must reach the review file's own
    ``gloss_en``, which is what the learner-facing bank actually reads."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={_CARRIER: "The dog runs quickly through the park."},
    )
    gloss = report["gloss"]
    assert isinstance(gloss, dict)
    assert isinstance(gloss["gloss_from_store"], int)
    assert gloss["gloss_from_store"] >= 1

    review_rows = [
        json.loads(line) for line in (tmp_path / "review.jsonl").read_text().splitlines() if line
    ]
    glossed = [r for r in review_rows if r["gloss_en"] is not None]
    assert glossed, "expected at least one item drawn from the glossed carrier"
    for row in glossed:
        assert row["gloss_en"] == "The dog runs quickly through the park."


def test_main_gloss_check_rejects_a_contradicting_gloss_and_counts_it_by_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The risk this task exists to make visible: the moment a gloss is
    present, a machine translation that contradicts the answer's tense or
    person REJECTS an item that previously passed. That rejection must be
    counted under its own name and broken down by topic, never folded into
    the model verifier's rejection reasons.

    Enforcing is opt-in, so this test asks for it explicitly. The default is
    measure-only, covered by the next test."""
    # A past-tense English gloss for a present-tense carrier: consistent
    # German, contradicting English.
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={_CARRIER: "The dog was running and it did not stop."},
        extra_args=["--enforce-gloss-check"],
    )
    gloss = report["gloss"]
    assert isinstance(gloss, dict)
    by_topic = gloss["rejected_by_gloss_check_by_topic"]
    assert isinstance(by_topic, dict)
    assert isinstance(gloss["rejected_by_gloss_check"], int)

    assert gloss["gloss_check_enforced"] is True
    assert gloss["rejected_by_gloss_check"] >= 1
    assert sum(by_topic.values()) == gloss["rejected_by_gloss_check"]

    rejected_rows = [
        json.loads(line) for line in (tmp_path / "rejected.jsonl").read_text().splitlines() if line
    ]
    gloss_rows = [
        r for r in rejected_rows if (r["reason"] or "").startswith(GLOSS_REJECTION_PREFIX)
    ]
    assert len(gloss_rows) == gloss["rejected_by_gloss_check"]
    for row in gloss_rows:
        assert row["gloss_en"] == "The dog was running and it did not stop."

    # An unrelated rejection must NOT be counted here: this run's other
    # rejected rows (CEFR ceiling, uniqueness, cross-topic duplicates) all
    # carry a reason that is not a gloss reason.
    unrelated = [r for r in rejected_rows if r not in gloss_rows]
    assert all(not (r["reason"] or "").startswith(GLOSS_REJECTION_PREFIX) for r in unrelated)

    # And a rejected item must not also appear in the review file.
    review_prompts = {
        json.loads(line)["prompt"]
        for line in (tmp_path / "review.jsonl").read_text().splitlines()
        if line
    }
    assert all(row["prompt"] not in review_prompts for row in gloss_rows)


def test_main_gloss_check_defaults_to_measuring_without_rejecting_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The default, and the reversibility seam: same store, same
    contradicting gloss, but the check does not act. The gloss still reaches
    the learner, no item is dropped, and the number of items the check WOULD
    have rejected is still reported. Enforcing must never be a leap in the
    dark, and a brand new rejection path must not switch itself on before a
    run has priced it."""
    enforced = _run_main_with_glosses(
        tmp_path / "on",
        monkeypatch,
        _tiny_corpora,
        store={_CARRIER: "The dog was running and it did not stop."},
        extra_args=["--enforce-gloss-check"],
    )
    relaxed = _run_main_with_glosses(
        tmp_path / "off",
        monkeypatch,
        _tiny_corpora,
        store={_CARRIER: "The dog was running and it did not stop."},
    )

    on_gloss = enforced["gloss"]
    off_gloss = relaxed["gloss"]
    assert isinstance(on_gloss, dict) and isinstance(off_gloss, dict)

    assert off_gloss["gloss_check_enforced"] is False
    # Measured identically...
    assert off_gloss["rejected_by_gloss_check"] == on_gloss["rejected_by_gloss_check"]
    assert (
        off_gloss["rejected_by_gloss_check_by_topic"]
        == on_gloss["rejected_by_gloss_check_by_topic"]
    )
    assert isinstance(off_gloss["rejected_by_gloss_check"], int)
    assert off_gloss["rejected_by_gloss_check"] >= 1
    # ...but acted on only when enforcing.
    assert isinstance(enforced["accepted_total"], int)
    assert isinstance(relaxed["accepted_total"], int)
    assert relaxed["accepted_total"] > enforced["accepted_total"]

    rejected_rows = [
        json.loads(line)
        for line in (tmp_path / "off" / "rejected.jsonl").read_text().splitlines()
        if line
    ]
    assert not [r for r in rejected_rows if (r["reason"] or "").startswith(GLOSS_REJECTION_PREFIX)]


def test_main_prints_the_gloss_rejection_count_prominently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _tiny_corpora: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """This task's own instruction: the gloss numbers must be visible in the
    printed output, not only in the JSON, and must not be buried inside the
    model-verification block."""
    _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={_CARRIER: "The dog was running and it did not stop."},
    )
    out = capsys.readouterr().out

    assert "REJECTED BY GLOSS:" in out
    assert "Gloss consistency check:" in out
    assert "English gloss (TODO.md 2.1b):" in out
    assert out.index("Gloss consistency check:") < out.index("Model verification pass:")


def test_main_translation_failure_still_reaches_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """A refused translation batch must not stop the pilot: the items it
    could not gloss stay at ``None`` and the run still verifies and writes
    every file, exactly as a missing corpus file already degrades."""
    from src.generation.blanking.model_verification import ItemVerdict, VerificationReport

    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)
    verified: list[int] = []

    def _fake_verify_items(
        items: list[BankItem],
        llm_client: object,
        *,
        batch_size: int = 20,
        use_cache: bool = True,
    ) -> VerificationReport:
        verified.append(len(items))
        return VerificationReport(
            attempted=True, verdicts=[ItemVerdict(outcome="verified") for _ in items]
        )

    always_fails = _FakeTranslator(fail_on=None)

    def _boom(sentences: Sequence[str]) -> list[str]:
        always_fails.batches.append(list(sentences))
        raise TranslationError("provider is down")

    always_fails.translate = _boom  # type: ignore[method-assign]

    monkeypatch.setattr(step7, "verify_items", _fake_verify_items)
    monkeypatch.setattr(step7, "translator_from_env", lambda client: (always_fails, "fallback"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--translations",
            str(tmp_path / "store.jsonl"),
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

    assert step7.main() == 0

    report = json.loads((tmp_path / "report.json").read_text())
    gloss = report["gloss"]
    assert always_fails.batches, "the translator really was called"
    assert gloss["translation_failures"] > 0
    assert gloss["translation_failure_examples"]
    assert gloss["gloss_missing"] == gloss["items_total"]
    assert gloss["characters_spent"] == 0
    # The pilot reached verification anyway, with every item still in hand.
    assert verified == [report["sampled_total"]]
    assert report["accepted_total"] == report["sampled_total"]
    # Nothing was written to the store: a failed batch stores nothing.
    assert not (tmp_path / "store.jsonl").exists()


# ==============================================================================
# --verification-batch-size (TODO.md 2.3, and 2.1c which made it urgent)
#
# Cycle 13 and cycle 14 ran the identical 475 candidates. 8 items that cycle 13
# correctly rejected for bad German were accepted by cycle 14, and the English
# gloss added in between says nothing about German naturalness, so the verifier
# simply changed its mind: it agrees with itself about 92% of the time on that
# judgment. The first hypothesis is that items late in a 20-item prompt get
# less scrutiny than early ones, and the way to settle it is to hold everything
# else fixed and vary only this. That is not possible without the flag.
# ==============================================================================


def test_main_verification_batch_size_defaults_to_the_module_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    from src.generation.blanking.model_verification import DEFAULT_VERIFICATION_BATCH_SIZE

    report = _run_main_with_glosses(tmp_path, monkeypatch, _tiny_corpora, store={})
    run = report["run"]
    assert isinstance(run, dict)
    assert run["verification_batch_size"] == DEFAULT_VERIFICATION_BATCH_SIZE


def test_main_verification_batch_size_is_passed_through_to_verify_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The flag has to reach the call, not just the report. A flag that is
    recorded but not applied would make the A/B look like it ran while both
    arms actually used the default, which is the worst possible outcome for an
    experiment whose whole purpose is to compare two batch sizes."""
    seen: list[int] = []
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-batch-size", "5"],
        batch_sizes=seen,
    )

    assert seen == [5]
    run = report["run"]
    assert isinstance(run, dict)
    assert run["verification_batch_size"] == 5


# ==============================================================================
# --verification-passes (TODO.md 2.3, 2.1c)
#
# The owner ran the identical 475 candidates at batch size 20 and at batch size
# 5, everything else held fixed: 444 accepted / 31 rejected against 438
# accepted / 37 rejected. Diffed item by item, 9 items were accepted at 20 and
# rejected at 5 and 3 went the other way, and all 12 were read by hand and all
# 12 are genuinely bad items. Neither run catches everything and the union of
# two catches all of them, which is what this flag builds. The tests below pin
# the union rule, the disagreement count that measures why it is needed, the
# cache bypass without which a repeated pass measures nothing, and the
# degrade-honestly behaviour of a pass that cannot run.
# ==============================================================================


def _multi_pass(report: dict[str, object]) -> dict[str, object]:
    verification = report["verification"]
    assert isinstance(verification, dict)
    multi_pass = verification["multi_pass"]
    assert isinstance(multi_pass, dict)
    return multi_pass


def test_main_verification_passes_defaults_to_one_cached_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The default must be today's behaviour exactly: one pass, cache ON,
    nothing extra bought. Every additional pass is another full cycle's spend
    against a 5 EUR/month ceiling, so this default is a cost guarantee, not
    just a convenience."""
    from scripts.step7_corpus_pilot import DEFAULT_VERIFICATION_PASSES

    seen_cache: list[bool] = []
    report = _run_main_with_glosses(
        tmp_path, monkeypatch, _tiny_corpora, store={}, use_cache_values=seen_cache
    )

    assert DEFAULT_VERIFICATION_PASSES == 1
    assert seen_cache == [True]
    run = report["run"]
    assert isinstance(run, dict)
    assert run["verification_passes"] == 1

    multi_pass = _multi_pass(report)
    assert multi_pass["passes_requested"] == 1
    assert multi_pass["pass_disagreements"] == 0
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert len(per_pass) == 1
    assert per_pass[0]["use_cache"] is True


def test_main_verification_passes_bypasses_the_cache_on_every_pass_after_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The trap this whole feature can fall into. ``src/llm/cache.py`` is
    content-addressed on a hash of the full request, so pass 2 over identical
    items builds a byte-identical prompt. With the cache left on it would hit,
    return pass 1's verdict verbatim, log a ``lane="cache"`` row and report
    zero disagreements no matter how unstable the verifier is -- a feature one
    default argument away from being silently inert. Pass 1 keeps the cache
    (CLAUDE.md 9: a rerun after a crash must still be cheap); every later pass
    must not."""
    seen_cache: list[bool] = []
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "3"],
        use_cache_values=seen_cache,
    )

    assert seen_cache == [True, False, False]
    per_pass = _multi_pass(report)["per_pass"]
    assert isinstance(per_pass, list)
    assert [p["use_cache"] for p in per_pass] == [True, False, False]


def test_run_verification_passes_reaches_generate_many_with_the_right_cache_flag() -> None:
    """The same guarantee as above, but end to end through the REAL
    ``verify_items`` and down to the client seam, with nothing about the cache
    faked out in between. The test above proves the runner asks for the right
    thing; this one proves ``verify_items`` does not swallow it on the way to
    ``generate_many``, which is the exact regression a default argument
    produces."""
    items = [
        BankItem(
            id="pass_item_1",
            topic_id="verb_praesens_regelm",
            tag_id="verb_praesens_regelm",
            type="cloze_free",
            difficulty=1,
            cefr="A2",
            prompt="Ich ___ jeden Morgen Kaffee.",
            accepted_answers=["trinke"],
        )
    ]

    class _CacheRecordingClient:
        def __init__(self) -> None:
            self.use_cache_seen: list[bool] = []

        def generate_many(
            self, prompts: list[str], model: str, purpose: str, use_cache: bool = True
        ) -> list[str]:
            self.use_cache_seen.append(use_cache)
            return [
                json.dumps(
                    {
                        "verdicts": [
                            {
                                "index": i + 1,
                                "valid": True,
                                "woerter_echt": True,
                                "hinweis_korrekt": True,
                                "reason": None,
                            }
                            for i in range(prompt.count("Lücke: "))
                        ]
                    }
                )
                for prompt in prompts
            ]

    client = _CacheRecordingClient()
    result = step7.run_verification_passes(
        items,
        client,  # type: ignore[arg-type]
        batch_size=20,
        passes=2,
    )

    assert client.use_cache_seen == [True, False]
    assert result.combined.verified_count == 1


def test_main_two_passes_reject_an_item_only_the_second_pass_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """Union of rejections. This is the 9-versus-3 finding's own shape: an
    item pass 1 waved through and pass 2 caught is a defect either way, and
    the run must reject it."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[{}, {0: "nur Durchgang 2 hat es gesehen"}],
    )

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["rejected_count"] == 1
    assert verification["rejected_reasons"] == {"nur Durchgang 2 hat es gesehen": 1}

    multi_pass = _multi_pass(report)
    assert multi_pass["rejected_by_any_pass"] == 1
    assert multi_pass["pass_disagreements"] == 1
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[0]["rejected"] == 0
    assert per_pass[1]["rejected"] == 1
    assert per_pass[1]["rejected_only_by_this_pass"] == 1

    # The union rule has to reach the bank, not just the counters: the item
    # only pass 2 rejected must be absent from the review file the run ships.
    sampled_total = report["sampled_total"]
    assert isinstance(sampled_total, int)
    review_rows = [
        line for line in (tmp_path / "review.jsonl").read_text().splitlines() if line.strip()
    ]
    assert len(review_rows) == sampled_total - 1
    assert report["accepted_total"] == sampled_total - 1


def test_main_two_passes_reject_an_item_only_the_first_pass_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The other direction of the same rule, and the reason it is its own
    test: a union implemented as "whatever the last pass said" would pass the
    test above and fail this one."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[{0: "nur Durchgang 1 hat es gesehen"}, {}],
    )

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["rejected_count"] == 1
    assert verification["rejected_reasons"] == {"nur Durchgang 1 hat es gesehen": 1}

    multi_pass = _multi_pass(report)
    assert multi_pass["rejected_by_any_pass"] == 1
    assert multi_pass["pass_disagreements"] == 1
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[0]["rejected_only_by_this_pass"] == 1
    assert per_pass[1]["rejected_only_by_this_pass"] == 0


def test_main_two_passes_keep_an_item_both_passes_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """Intersection of acceptances. Two passes must not invent a rejection
    out of agreement."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
    )

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["rejected_count"] == 0
    assert verification["not_run_count"] == 0
    assert verification["verified_count"] == report["sampled_total"]
    assert report["accepted_total"] == report["sampled_total"]

    multi_pass = _multi_pass(report)
    assert multi_pass["rejected_by_any_pass"] == 0
    assert multi_pass["pass_disagreements"] == 0


def test_main_pass_disagreements_counts_only_the_items_the_passes_disagreed_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The number the whole feature exists to produce. Item 0 is rejected by
    both passes (agreement, not a disagreement), items 1 and 2 by exactly one
    pass each (two disagreements). Three rejections in the union, two
    disagreements -- a count that merely echoed the union would get this
    wrong."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[
            {0: "beide Durchgänge", 1: "nur Durchgang 1"},
            {0: "beide Durchgänge", 2: "nur Durchgang 2"},
        ],
    )

    multi_pass = _multi_pass(report)
    assert multi_pass["rejected_by_any_pass"] == 3
    assert multi_pass["pass_disagreements"] == 2
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[0]["rejected"] == 2
    assert per_pass[1]["rejected"] == 2
    assert per_pass[0]["rejected_only_by_this_pass"] == 1
    assert per_pass[1]["rejected_only_by_this_pass"] == 1

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["rejected_count"] == 3


def test_main_pass_disagreements_is_zero_when_both_passes_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """Zero disagreements is a real measurement (the verifier was stable on
    these items), not a missing one, so it must be reported rather than
    inferred from an absent key."""
    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[{0: "beide Durchgänge"}, {0: "beide Durchgänge"}],
    )

    multi_pass = _multi_pass(report)
    assert multi_pass["rejected_by_any_pass"] == 1
    assert multi_pass["pass_disagreements"] == 0
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[0]["rejected_only_by_this_pass"] == 0
    assert per_pass[1]["rejected_only_by_this_pass"] == 0


def test_main_a_later_pass_that_cannot_run_degrades_without_losing_the_passes_that_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """``BudgetExceeded`` on pass 2 against a 5 EUR/month ceiling is the
    expected way this flag stops, not an exotic one. The run must report what
    it managed and say so: pass 1's real verdicts survive, pass 2 is recorded
    as not-run, ``passes_completed`` falls below ``passes_requested``, and the
    run still writes every file and exits 0 because every item really was
    verified once."""
    from src.llm.client import BudgetExceeded

    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[{0: "Durchgang 1 hat es abgelehnt"}, {}],
        pass_errors={2: BudgetExceeded("ceiling reached")},
    )

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["rejected_count"] == 1
    assert verification["not_run_count"] == 0
    assert verification["rejected_reasons"] == {"Durchgang 1 hat es abgelehnt": 1}

    multi_pass = _multi_pass(report)
    assert multi_pass["passes_requested"] == 2
    assert multi_pass["passes_completed"] == 1
    assert multi_pass["rejected_by_any_pass"] == 1
    assert multi_pass["pass_disagreements"] == 0
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[1]["not_run"] == report["sampled_total"]
    assert per_pass[1]["not_run_reasons"] == {
        "transport_error:BudgetExceeded": report["sampled_total"]
    }


def test_main_a_first_pass_that_cannot_run_still_uses_the_pass_that_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """Symmetric to the test above. An item pass 1 could not judge and pass 2
    verified has still been verified once, so it is verified -- reporting it
    as not-run would be the same conflation of "nothing objected" and "a model
    read this" that ``model_verification`` exists to prevent, only inverted."""
    from src.llm.client import ServerUnavailableError

    report = _run_main_with_glosses(
        tmp_path,
        monkeypatch,
        _tiny_corpora,
        store={},
        extra_args=["--verification-passes", "2"],
        reject_per_pass=[{}, {0: "Durchgang 2 hat es abgelehnt"}],
        pass_errors={1: ServerUnavailableError("503")},
    )

    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["not_run_count"] == 0
    assert verification["rejected_count"] == 1

    multi_pass = _multi_pass(report)
    assert multi_pass["passes_completed"] == 1
    per_pass = multi_pass["per_pass"]
    assert isinstance(per_pass, list)
    assert per_pass[0]["not_run"] == report["sampled_total"]


def test_main_every_pass_failing_still_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The existing fail-loudly rule is unchanged by this flag: an item NO
    pass could judge is not-run, and a run whose backstop did not execute is
    not a valid pilot run. ``_run_main_with_glosses`` asserts exit 0, so this
    one drives ``main()`` itself."""
    from src.generation.blanking.model_verification import VerificationReport
    from src.llm.client import BudgetExceeded

    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)

    def _always_fails(
        items: list[BankItem],
        llm_client: object,
        *,
        batch_size: int = 20,
        use_cache: bool = True,
    ) -> VerificationReport:
        raise BudgetExceeded("ceiling reached")

    monkeypatch.setattr(step7, "verify_items", _always_fails)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--translations",
            str(tmp_path / "translations.jsonl"),
            "--no-translate",
            "--tatoeba",
            str(tatoeba),
            "--leipzig",
            str(leipzig),
            "--limit",
            "100",
            "--per-topic-quota",
            "2",
            "--verification-passes",
            "2",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
    )

    assert step7.main() == 1

    report: dict[str, object] = json.loads((tmp_path / "report.json").read_text())
    verification = report["verification"]
    assert isinstance(verification, dict)
    assert verification["not_run_count"] == report["sampled_total"]
    multi_pass = _multi_pass(report)
    assert multi_pass["passes_completed"] == 0


def test_main_prints_every_pass_number_not_just_the_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _tiny_corpora: tuple[Path, Path]
) -> None:
    """The totals are exactly what hid the finding in the batch-size
    experiment: 444/31 against 438/37 reads as a 6-item difference and is
    actually a 12-item disagreement. So every per-pass number and the
    disagreement count go on the console, not only into the report file."""
    tatoeba, leipzig = _tiny_corpora
    _clear_gemini_env(monkeypatch)
    from src.generation.blanking.model_verification import ItemVerdict, VerificationReport

    def _fake_verify_items(
        items: list[BankItem],
        llm_client: object,
        *,
        batch_size: int = 20,
        use_cache: bool = True,
    ) -> VerificationReport:
        return VerificationReport(
            attempted=True, verdicts=[ItemVerdict(outcome="verified") for _ in items]
        )

    monkeypatch.setattr(step7, "verify_items", _fake_verify_items)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step7_corpus_pilot.py",
            "--translations",
            str(tmp_path / "translations.jsonl"),
            "--no-translate",
            "--tatoeba",
            str(tatoeba),
            "--leipzig",
            str(leipzig),
            "--limit",
            "100",
            "--per-topic-quota",
            "2",
            "--verification-passes",
            "2",
            "--review-file",
            str(tmp_path / "review.jsonl"),
            "--rejected-file",
            str(tmp_path / "rejected.jsonl"),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
    )

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = step7.main()
    out = buffer.getvalue()

    assert exit_code == 0
    assert "Verification passes (TODO.md 2.3): 2" in out
    assert "Pass 1 (cache on):" in out
    assert "Pass 2 (cache BYPASSED):" in out
    assert "REJECTED BY ANY PASS:" in out
    assert "PASS DISAGREEMENTS:" in out
