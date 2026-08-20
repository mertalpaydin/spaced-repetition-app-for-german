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
            "Das ist wirklich nicht wahr, oder?",
        ]
    )
    assert report.sentences_requested == 2
    assert report.sentences_tagged == 2
    assert report.items_by_topic.get("verb_praesens_vokalwechsel", 0) >= 1
    assert report.total_items == sum(report.items_by_topic.values())
    assert report.skips_by_reason["no_candidate_for_topic"] > 0
    # every (sentence, topic) pair is accounted for exactly once, across the
    # FOUR distinct outcome buckets this module keeps (module docstring): an
    # item, an ordinary quality skip, a uniqueness skip, or (this fixture's
    # own case) a type-ineligibility skip.
    accounted = (
        report.total_items
        + sum(report.skips_by_reason.values())
        + sum(report.skips_by_uniqueness.values())
        + sum(report.skips_by_type_ineligibility.values())
    )
    assert accounted == report.sentences_requested * len(TOPIC_IDS)


def test_blank_sentences_produces_items_for_all_three_artikel_nom_topics_when_anchored() -> None:
    """docs/audits/cycle-06-modal-leak.md found that a single, standalone
    sentence can never honestly test definiteness/negation/possession on its
    own (nothing forces "Der" over "Ein"/"Mein"/"Kein" in a bare sentence),
    so these three topics used to report zero unconditionally, via the
    eligible_types check (``blanker.py``'s own module docstring, final
    section, and ``pipeline.py``'s module docstring, "a fourth problem").

    TODO.md 2.1-2.3 (owner's decision, cycle 9) superseded the intermediate
    per-topic-anchor fix a later cycle had added: every Nominative
    determiner candidate now carries the invariant-citation-form cue
    (``selectors._determiner_cue``), which by itself closes the free-
    lexical-choice-of-family gap the anchor used to be the only way to
    close -- so ``artikel_bestimmt_nom`` is no longer the odd one out
    (previously flagged ``determiner_family_interchangeable`` because its
    own blanked cell is already Nominative and used to have no cue at
    all); all three topics now produce an item from their own anchored
    sentence, run end to end through the full pipeline, not just a direct
    selector call. Every determiner candidate now carrying a cue also
    means every item these three topics build is typed ``cloze_cued``. not
    the ``cloze_free`` an earlier cycle's fix produced -- confirmed a real,
    live consequence of the cue rule while writing this test (the pipeline
    silently skipped every item as type-ineligible until ``data/
    taxonomy.yaml``'s own ``eligible_types`` for these three topics was
    widened to include it, alongside ``paragraph_cloze``/``cloze_free``)."""
    report = blank_sentences(
        [
            "Der Hund, den ich gestern gekauft habe, schläft im Garten.",
            "Wir kommen heute zu spät, weil kein Bus fährt.",
            "Meine Großmutter, die ich jedes Wochenende besuche, wohnt in München.",
        ]
    )
    assert report.items_by_topic.get("artikel_bestimmt_nom", 0) == 1
    assert report.items_by_topic.get("artikel_unbestimmt_kein_nom", 0) == 1
    assert report.items_by_topic.get("artikel_possessiv_nom", 0) == 1
    assert report.skips_by_type_ineligibility.get("artikel_bestimmt_nom", 0) == 0
    assert report.skips_by_type_ineligibility.get("artikel_unbestimmt_kein_nom", 0) == 0
    assert report.skips_by_type_ineligibility.get("artikel_possessiv_nom", 0) == 0

    bestimmt = next(i for i in report.items if i.topic_id == "artikel_bestimmt_nom")
    assert bestimmt.type == "cloze_cued"
    assert bestimmt.proposed_answer == "Der"
    assert bestimmt.cue == "Der"
    unbestimmt = next(i for i in report.items if i.topic_id == "artikel_unbestimmt_kein_nom")
    assert unbestimmt.type == "cloze_cued"
    assert unbestimmt.proposed_answer == "kein"
    possessiv = next(i for i in report.items if i.topic_id == "artikel_possessiv_nom")
    assert possessiv.type == "cloze_cued"
    assert possessiv.proposed_answer == "Meine"


def test_blank_sentences_reports_zero_for_artikel_bestimmt_nom_without_its_anchor() -> None:
    """The negative case, run through the FULL pipeline rather than a direct
    selector call (the positive test above's own honesty requirement).
    ``artikel_bestimmt_nom`` is the one of the three topics whose own
    forcing anchor (a uniqueness-making relative clause, superlative, or
    ordinal) still GATES candidacy -- TODO.md 2.1-2.3 only removed the
    anchor requirement for ``artikel_unbestimmt_kein_nom`` and
    ``artikel_possessiv_nom`` (both now produce an item from these same
    unanchored sentences instead; see the positive test above for their
    own anchored case, and ``test_blanking_selectors.py`` for their direct,
    unanchored-still-finds-a-candidate pins)."""
    report = blank_sentences(
        [
            "Der Hund läuft schnell durch den Park.",
            "Ein Mann steht vor der Tür.",
        ]
    )
    assert report.items_by_topic.get("artikel_bestimmt_nom", 0) == 0
    assert report.skips_by_reason["no_candidate_for_topic"] >= 1
    assert report.skips_by_type_ineligibility.get("artikel_bestimmt_nom", 0) == 0


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


def test_blank_sentences_futur_ii_beats_passiv_praesens_on_same_wird_token() -> None:
    """ "Er wird das Buch gelesen haben." blanks the exact same token
    ("wird") for both ``passiv_praesens`` (which stops looking after the
    transitive participle "gelesen") and ``futur_ii`` (which additionally
    requires, and finds, the trailing aux infinitive "haben") -- a real
    collision every genuine Futur II sentence with a transitive participle
    produces, since ``paradigms.TRANSITIVE_LEMMAS`` and the passive reading
    of "wird" always agree on the same token. Only the more specific topic
    (``futur_ii``) must keep the item; the general one is dropped and
    counted, not silently lost -- and the genuine Futur II item must never
    be the one that disappears."""
    report = blank_sentences(["Er wird das Buch gelesen haben."])
    assert report.items_by_topic.get("futur_ii", 0) == 1
    assert report.items_by_topic.get("passiv_praesens", 0) == 0
    assert report.cross_topic_duplicates_dropped["passiv_praesens"] == 1
    dropped = [d for d in report.dropped_details if d.reason == "cross_topic_duplicate"]
    assert len(dropped) == 1
    assert dropped[0].topic_id == "passiv_praesens"
    assert dropped[0].kept_topic_id == "futur_ii"


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


# ==============================================================================
# The uniqueness gate (docs/audits/cycle-04-report.md): a correctly-built
# item is skipped, under its own counter and detail list, when another
# member of the blanked token's closed class would also have been
# grammatical in the same slot. See test_blanking_uniqueness.py for the
# per-candidate-kind policy this exercises end to end.
# ==============================================================================


def test_blank_sentences_skips_a_modal_verb_item_with_no_cue_as_a_uniqueness_skip() -> None:
    """1st/3rd-plural present tense of a modal is spelled identically to its
    own infinitive ("wir müssen" == "müssen") -- ``selectors._citation_cue``
    withholds a cue there, so this one cell is still genuinely unrescuable
    and the item stays a uniqueness skip, not a kept item."""
    sentence = "Wir müssen jetzt gehen."
    report = blank_sentences([sentence])
    assert report.items_by_topic.get("modalverben_praesens", 0) == 0
    assert report.skips_by_uniqueness["modal_verb_interchangeable"] >= 1
    matching = [
        s
        for s in report.uniqueness_skips
        if s.topic_id == "modalverben_praesens" and s.reason == "modal_verb_interchangeable"
    ]
    assert len(matching) == 1
    assert matching[0].proposed_answer == "müssen"
    # never double-counted as an ordinary quality skip.
    assert report.skips_by_reason.get("modal_verb_interchangeable", 0) == 0


def test_blank_sentences_keeps_a_cued_modal_verb_item() -> None:
    """docs/audits/cycle-04-report.md's own worked example: "kann" used to
    be a uniqueness skip (another modal fits equally well); the selector's
    own cue ("können", the modal's own infinitive) now rescues it. Both
    ``modalverben_praesens`` and ``passiv_modalverben`` fire on the same
    "kann" token (module docstring); the cross-topic dedup keeps exactly one
    of them (``passiv_modalverben`` beats ``modalverben_praesens``,
    ``_SPECIFICITY_OVERRIDES``), not a uniqueness skip for either."""
    sentence = (
        "Das Fleisch kann scharf angebraten werden, wenn ein kräftiger Geschmack gewünscht wird."
    )
    report = blank_sentences([sentence])
    assert "modal_verb_interchangeable" not in report.skips_by_uniqueness
    assert report.items_by_topic.get("passiv_modalverben", 0) == 1
    kept = next(item for item in report.items if item.topic_id == "passiv_modalverben")
    assert kept.proposed_answer == "kann"
    assert kept.cue == "können"
    assert kept.type == "cloze_cued"
    dropped = [d for d in report.dropped_details if d.topic_id == "modalverben_praesens"]
    assert len(dropped) == 1
    assert dropped[0].reason == "cross_topic_duplicate"


def test_blank_sentences_skips_an_unanchored_dative_pronoun() -> None:
    sentence = (
        "Das Restaurant hatte einen neuen Koch eingestellt, und das Essen schmeckte "
        "Ihnen ausgezeichnet."
    )
    report = blank_sentences([sentence])
    assert report.items_by_topic.get("pronomen_personal_dat", 0) == 0
    assert report.skips_by_uniqueness["personal_pronoun_unanchored"] >= 1


def test_blank_sentences_keeps_a_carrier_anchored_dative_pronoun() -> None:
    """docs/audits/cycle-04-report.md's own solvable example: must survive
    the uniqueness gate, not be thrown away with every other dative pronoun."""
    sentence = "Wir erklären Ihnen den Fehler, weil Sie das System besser verstehen müssen."
    report = blank_sentences([sentence])
    assert report.items_by_topic.get("pronomen_personal_dat", 0) == 1
    assert "personal_pronoun_unanchored" not in report.skips_by_uniqueness


def test_blank_sentences_skips_a_plural_noun_item_with_no_cue() -> None:
    """ "Lehrer" is spelled identically singular and plural -- a cue there
    would hand over the answer verbatim, so ``selectors._plural_noun_cue``
    withholds it and the item stays a uniqueness skip."""
    sentence = "Die Lehrer unterrichten Mathematik."
    report = blank_sentences([sentence])
    assert report.items_by_topic.get("nomen_plural", 0) == 0
    assert report.skips_by_uniqueness["plural_noun_open_class"] >= 1


def test_blank_sentences_keeps_a_cued_plural_noun_item() -> None:
    """docs/audits/cycle-04-report.md's own worked example: "meine ___" used
    to be a uniqueness skip (Zähne/Hände/Schuhe/Haare all fit equally well);
    the selector's own cue ("Zahn", the derived singular citation form) now
    rescues it."""
    sentence = "Nach dem Frühstück putze ich gründlich meine Zähne."
    report = blank_sentences([sentence])
    assert "plural_noun_open_class" not in report.skips_by_uniqueness
    assert report.items_by_topic.get("nomen_plural", 0) == 1
    kept = next(item for item in report.items if item.topic_id == "nomen_plural")
    assert kept.proposed_answer == "Zähne"
    assert kept.cue == "Zahn"
    assert kept.type == "cloze_cued"


def test_blank_sentences_keeps_a_nominative_pronoun_item_unaffected() -> None:
    """Nominative pronouns are trusted unconditionally (verb agreement is
    the anchor) -- confirms the gate does not regress the existing
    pronomen_personal_nom coverage relied on elsewhere in this file (e.g.
    test_blank_sentences_max_items_per_topic_caps_a_dominant_topic)."""
    report = blank_sentences(["Ich sehe den Mann auf der anderen Straßenseite."])
    assert report.items_by_topic.get("pronomen_personal_nom", 0) == 1
    assert not report.skips_by_uniqueness
