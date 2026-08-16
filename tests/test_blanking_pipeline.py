"""Tests for src/generation/blanking/pipeline.py: orchestration, dedup, and
the degrade-cleanly-with-no-spaCy path."""

import pytest
from src.generation.blanking import sentence_tagger
from src.generation.blanking.pipeline import TOPIC_IDS, blank_sentences

pytestmark = pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)


def test_topic_ids_covers_all_fifteen_in_scope_topics() -> None:
    expected = {
        "artikel_bestimmt_nom",
        "artikel_unbestimmt_kein_nom",
        "artikel_possessiv_nom",
        "adjektivdeklination_bestimmt",
        "adjektivdeklination_unbestimmt",
        "adjektivdeklination_nullartikel",
        "kasus_akkusativ_formen",
        "kasus_dativ_formen",
        "kasus_genitiv_formen",
        "akkusativ_nach_praeposition",
        "dativ_nach_praeposition",
        "praepositionen_akkusativ",
        "praepositionen_dativ",
        "praepositionen_genitiv",
        "adjektiv_komparativ_superlativ",
    }
    assert set(TOPIC_IDS) == expected


def test_blank_sentences_produces_at_most_one_item_per_sentence_per_topic() -> None:
    """ "Der alte Mann liest die neue Zeitung." has TWO weak-declension
    adjectives ("alte", "neue"); the pipeline must still only ever spend one
    of them on adjektivdeklination_bestimmt for this sentence."""
    report = blank_sentences(["Der alte Mann liest die neue Zeitung."])
    assert report.sentences_tagged == 1
    assert report.items_by_topic.get("adjektivdeklination_bestimmt", 0) == 1


def test_blank_sentences_reports_items_by_topic_and_skips_by_reason() -> None:
    report = blank_sentences(
        [
            "Der Hund läuft schnell durch den Park.",
            "Das ist wirklich nicht wahr, oder?",  # should yield nothing at all
        ]
    )
    assert report.sentences_requested == 2
    assert report.sentences_tagged == 2
    assert report.items_by_topic.get("artikel_bestimmt_nom", 0) >= 1
    assert report.total_items == sum(report.items_by_topic.values())
    assert report.skips_by_reason["no_candidate_for_topic"] > 0
    # every (sentence, topic) pair is accounted for exactly once, either as
    # an item or as a skip.
    accounted = report.total_items + sum(report.skips_by_reason.values())
    assert accounted == report.sentences_requested * len(TOPIC_IDS)


def test_blank_sentences_handles_an_empty_sentence_list() -> None:
    report = blank_sentences([])
    assert report.sentences_requested == 0
    assert report.sentences_tagged == 0
    assert report.total_items == 0


def test_blank_sentences_degrades_cleanly_when_spacy_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sentence_tagger, "analysis_available", lambda: False)
    report = blank_sentences(["Der Hund läuft schnell durch den Park."])
    assert report.sentences_tagged == 0
    assert report.total_items == 0
    assert report.skips_by_reason["spacy_unavailable"] == len(TOPIC_IDS)
