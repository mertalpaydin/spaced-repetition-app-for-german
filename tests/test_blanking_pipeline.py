"""Tests for src/generation/blanking/pipeline.py: orchestration, dedup, and
the degrade-cleanly-with-no-spaCy path."""

import pytest
from src.generation.blanking import sentence_tagger
from src.generation.blanking.pipeline import TOPIC_IDS, blank_sentences

pytestmark = pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)


def test_topic_ids_covers_every_cycle_2_and_cycle_3_topic() -> None:
    """Cycle 2 built the 15 article/adjective-declension topics; cycle 3
    (docs/audits/generation-track-plan.md) extends coverage to every
    remaining ``verification_class: computable`` topic in
    ``data/taxonomy.yaml`` that this cycle's report judged tractable --
    49 of the 52 computable topics in total. The three left out
    (``imperativ``, ``passiv_unpersoenlich``, ``relativsatz_was_wo``) are
    each documented with a concrete, tagger-level reason in that report
    rather than silently missing."""
    expected = {
        # Cycle 2.
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
        # Cycle 3: pronouns.
        "pronomen_personal_nom",
        "pronomen_personal_akk",
        "pronomen_personal_dat",
        "verben_reflexiv_akk",
        "verben_reflexiv_dat",
        "relativsatz_nom_akk",
        "relativsatz_dativ",
        "relativsatz_genitiv",
        # Cycle 3: verb conjugation.
        "verb_sein_haben",
        "verb_praesens_regelm",
        "verb_praesens_vokalwechsel",
        "modalverben_praesens",
        "verben_trennbar_praesens",
        "praeteritum_sein_haben_modal",
        "praeteritum_vollverben",
        "nomen_plural",
        # Cycle 3: compound tenses, passive, Konjunktiv II.
        "perfekt_haben",
        "perfekt_sein",
        "plusquamperfekt",
        "konjunktiv_ii_hoeflichkeit",
        "konjunktiv_ii_irreal_gegenwart",
        "konjunktiv_ii_vergangenheit",
        "passiv_praesens",
        "passiv_praeteritum",
        "passiv_modalverben",
        "zustandspassiv",
        "zustandspassiv_zeiten",
        "futur_i",
        "futur_ii",
        # Cycle 3: infinitive/participle constructions and one more
        # closed-list preposition topic (shares _determiner_selector with
        # cycle 2's praepositionen_genitiv).
        "infinitiv_mit_zu",
        "infinitiv_um_zu",
        "partizip_i_attributiv",
        "partizip_ii_attributiv_erweitert",
        "praepositionen_genitiv_gehoben",
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


def test_blank_sentences_deduplicates_cross_topic_same_prompt_and_answer() -> None:
    """ "Ich stehe jeden Morgen um sechs Uhr auf." blanks the exact same
    token ("stehe") for both ``verb_praesens_regelm`` (a regular
    present-tense verb, which does not check for a separable particle) and
    ``verben_trennbar_praesens`` (which additionally requires one, "auf" at
    the end of the clause) -- the audited real-world case this dedup rule
    exists for. Only the more specific topic (the separable-verb one) keeps
    the item; the general one is dropped and counted, not silently lost."""
    report = blank_sentences(["Ich stehe jeden Morgen um sechs Uhr auf."])
    assert report.items_by_topic.get("verben_trennbar_praesens", 0) == 1
    assert report.items_by_topic.get("verb_praesens_regelm", 0) == 0
    assert report.cross_topic_duplicates_dropped["verb_praesens_regelm"] == 1
    kept_prompts = {item.prompt for item in report.items}
    dropped = [d for d in report.dropped_details if d.reason == "cross_topic_duplicate"]
    assert len(dropped) == 1
    assert dropped[0].topic_id == "verb_praesens_regelm"
    assert dropped[0].kept_topic_id == "verben_trennbar_praesens"
    # the winning item's own prompt is exactly what was kept -- no item is
    # lost outright, only the losing topic's claim on it.
    assert dropped[0].prompt in kept_prompts


def test_blank_sentences_max_items_per_topic_caps_a_dominant_topic() -> None:
    """Five different sentences all trigger ``pronomen_personal_nom``; a cap
    of 2 must keep exactly 2 and count the other 3 as dropped by the cap,
    distinct from ``skips_by_reason`` (module docstring: a cap drop is a
    balance decision, not a quality judgment)."""
    sentences = [
        "Ich sehe den Mann auf der anderen Straßenseite.",
        "Ich antworte dem Lehrer sehr höflich.",
        "Ich lege das Buch auf den Tisch im Wohnzimmer.",
        "Ich fahre jeden Tag mit dem Bus zur Arbeit.",
        "Ich kaufe samstags immer auf dem Wochenmarkt ein.",
    ]
    report = blank_sentences(sentences, max_items_per_topic=2, max_items_per_sentence=100)
    assert report.items_by_topic.get("pronomen_personal_nom", 0) == 2
    assert report.items_dropped_by_topic_cap["pronomen_personal_nom"] == 3
    assert report.skips_by_reason.get("pronomen_personal_nom", 0) == 0
    cap_dropped = [d for d in report.dropped_details if d.reason == "topic_cap"]
    assert len(cap_dropped) == 3
    assert all(d.topic_id == "pronomen_personal_nom" for d in cap_dropped)


def test_blank_sentences_max_items_per_sentence_caps_one_carrier() -> None:
    """ "Der alte Mann liest die neue Zeitung." legitimately matches several
    different topics (article, several adjective-declension slots, a
    pronoun-free verb...); a per-sentence cap of 1 must keep only one item
    from it, regardless of how many topics matched, and count the rest under
    the sentence cap, not as a quality skip."""
    sentence = "Der alte Mann liest die neue Zeitung."
    uncapped = blank_sentences([sentence], max_items_per_sentence=100)
    total_uncapped = uncapped.total_items
    assert total_uncapped > 1, "fixture must legitimately match more than one topic"

    capped = blank_sentences([sentence], max_items_per_sentence=1)
    assert capped.total_items == 1
    assert sum(capped.items_dropped_by_sentence_cap.values()) == total_uncapped - 1
    sentence_cap_dropped = [d for d in capped.dropped_details if d.reason == "sentence_cap"]
    assert len(sentence_cap_dropped) == total_uncapped - 1


def test_blank_sentences_default_caps_are_generous_enough_for_a_single_sentence() -> None:
    """The default caps must not silently truncate an ordinary small run --
    they exist to bound a large pool, not to interfere with a handful of
    sentences (confirms the defaults chosen in this module do not change
    behaviour for every pre-existing small-fixture test in this file)."""
    report = blank_sentences(["Der alte Mann liest die neue Zeitung."])
    assert not report.items_dropped_by_topic_cap
    assert not report.items_dropped_by_sentence_cap
    assert not report.cross_topic_duplicates_dropped
