"""Hand-checked test corpus for src/generation/blanking/selectors.py and
blanker.py, run together through real spaCy analysis (no mocking of the
tagger: this is the actual deliverable of cycle 2 -- proof the selectors are
right on genuine German sentences, not toy strings).

Every sentence below is real, grammatical German. Every expected prompt,
answer, and (where asserted) distractor set was hand-verified against the
grammar before being written here, then cross-checked against the actual
selector/blanker output (see the module docstrings in ``selectors.py`` and
``blanker.py`` for the linguistic reasoning each check encodes). A handful
of sentences are deliberately negative: real, grammatical German that a
selector must NOT fire on, or must reject at the blanking stage rather than
guess -- these pin the "reject rather than guess" behaviour as concretely as
the positive cases pin the "select this" behaviour.

Skipped whole-module if spaCy's ``de_core_news_sm`` model is not installed
in this environment (mirrors ``tests/test_tagger.py``'s own guard).
"""

import pytest
from src.generation.blanking import sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.selectors import SELECTORS

pytestmark = pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)


def _select(topic_id: str, sentence: str):
    tagged = sentence_tagger.tag_sentence(sentence)
    assert tagged is not None
    return tagged, SELECTORS[topic_id](tagged)


def _blank(topic_id: str, sentence: str, candidate_index: int = 0):
    tagged, candidates = _select(topic_id, sentence)
    assert candidates, f"expected at least one candidate for {topic_id!r} in {sentence!r}"
    outcome = blank_candidate(topic_id, tagged, candidates[candidate_index])
    assert outcome.item is not None, (
        f"expected an item for {topic_id!r} in {sentence!r}, got skip={outcome.skip_reason!r}"
    )
    return outcome.item


# ==============================================================================
# artikel_bestimmt_nom -- definite article, Nominative.
# ==============================================================================


def test_artikel_bestimmt_nom_finds_masculine_definite_article() -> None:
    item = _blank("artikel_bestimmt_nom", "Der Hund läuft schnell durch den Park.")
    assert item.prompt == "___ Hund läuft schnell durch den Park."
    assert item.proposed_answer == "Der"


def test_artikel_bestimmt_nom_finds_feminine_and_neuter() -> None:
    fem = _blank("artikel_bestimmt_nom", "Die Sonne scheint heute hell.")
    assert fem.proposed_answer == "Die"
    neut = _blank("artikel_bestimmt_nom", "Das Kind spielt im Garten.")
    assert neut.proposed_answer == "Das"


def test_artikel_bestimmt_nom_ignores_the_accusative_article_in_the_same_sentence() -> None:
    """ "den Park" is Accusative, not Nominative -- only "Der Hund" qualifies."""
    _, candidates = _select("artikel_bestimmt_nom", "Der Hund läuft schnell durch den Park.")
    assert len(candidates) == 1


# ==============================================================================
# artikel_unbestimmt_kein_nom -- indefinite/negative article, Nominative.
# ==============================================================================


def test_artikel_unbestimmt_kein_nom_finds_indefinite_and_negative() -> None:
    ind = _blank("artikel_unbestimmt_kein_nom", "Ein Mann steht vor der Tür.")
    assert ind.proposed_answer == "Ein"
    neg = _blank("artikel_unbestimmt_kein_nom", "Keine Katze mag Wasser.")
    assert neg.proposed_answer == "Keine"


def test_artikel_unbestimmt_kein_nom_rejects_plural_kein_as_a_known_paradigm_gap() -> None:
    """ "Keine Kinder" (plural Nominative "kein") is genuine, grammatical
    German, but the reused ein-word ending paradigm has no Nom/Acc plural
    row at all (built for "ein", which has no plural) -- see
    ``paradigms.py``'s module docstring. The selector still finds the
    token; the blanker's own paradigm-reconstruction check is what refuses
    rather than guesses."""
    tagged, candidates = _select("artikel_unbestimmt_kein_nom", "Keine Kinder mögen Regen.")
    assert len(candidates) == 1
    outcome = blank_candidate("artikel_unbestimmt_kein_nom", tagged, candidates[0])
    assert outcome.item is None
    assert outcome.skip_reason == "determiner_cell_uncovered_by_paradigm"


# ==============================================================================
# artikel_possessiv_nom -- possessive determiner, Nominative.
# ==============================================================================


def test_artikel_possessiv_nom_finds_different_persons() -> None:
    mein = _blank("artikel_possessiv_nom", "Mein Vater kocht heute Abend.")
    assert mein.proposed_answer == "Mein"
    ihre = _blank("artikel_possessiv_nom", "Ihre Schwester lacht laut.")
    assert ihre.proposed_answer == "Ihre"
    sein = _blank("artikel_possessiv_nom", "Sein Bruder wohnt in Berlin.")
    assert sein.proposed_answer == "Sein"


# ==============================================================================
# kasus_akkusativ_formen / kasus_dativ_formen / kasus_genitiv_formen --
# bare-case article forms, never governed by a preposition.
# ==============================================================================


def test_kasus_akkusativ_formen_finds_direct_object_articles() -> None:
    item = _blank("kasus_akkusativ_formen", "Ich sehe den Mann.")
    assert item.prompt == "Ich sehe ___ Mann."
    assert item.proposed_answer == "den"
    assert _blank("kasus_akkusativ_formen", "Wir kaufen einen Tisch.").proposed_answer == "einen"


def test_kasus_akkusativ_formen_does_not_fire_on_a_dative_sentence() -> None:
    _, candidates = _select("kasus_akkusativ_formen", "Ich antworte dem Lehrer.")
    assert candidates == []


def test_kasus_dativ_formen_finds_indirect_object_articles() -> None:
    item = _blank("kasus_dativ_formen", "Ich antworte dem Lehrer.")
    assert item.proposed_answer == "dem"
    assert _blank("kasus_dativ_formen", "Sie hilft einer Frau.").proposed_answer == "einer"


def test_kasus_dativ_formen_excludes_a_prepositional_dative() -> None:
    """ "mit dem Bus" is governed by "mit" (a fixed-Dative preposition) --
    this is praepositionen_dativ's job, not the bare-case topic's; the
    ``preposition_gate="forbidden"`` check keeps the two topics disjoint."""
    _, candidates = _select("kasus_dativ_formen", "Ich fahre mit dem Bus.")
    assert candidates == []


def test_kasus_genitiv_formen_finds_possessive_genitive_articles() -> None:
    item = _blank("kasus_genitiv_formen", "Die Farbe der Blumen ist schön.")
    assert item.proposed_answer == "der"
    assert _blank("kasus_genitiv_formen", "Das Auto des Lehrers ist neu.").proposed_answer == "des"


def test_kasus_genitiv_formen_excludes_a_prepositional_genitive() -> None:
    """ "wegen des Regens" is governed by "wegen" -- praepositionen_genitiv's
    job, not this bare-case topic's."""
    _, candidates = _select("kasus_genitiv_formen", "Wegen des Regens bleiben wir zuhause.")
    assert candidates == []


# ==============================================================================
# akkusativ_nach_praeposition / dativ_nach_praeposition -- Wechselpräposition
# direction (Acc) vs static location (Dat), decided by the parser's own
# Case resolution, not a verb-cue heuristic.
# ==============================================================================


def test_akkusativ_nach_praeposition_finds_directional_wechselpraeposition() -> None:
    item = _blank("akkusativ_nach_praeposition", "Ich lege das Buch auf den Tisch.")
    assert item.prompt == "Ich lege das Buch auf ___ Tisch."
    assert item.proposed_answer == "den"
    assert _blank("akkusativ_nach_praeposition", "Er geht in die Küche.").proposed_answer == "die"


def test_dativ_nach_praeposition_finds_static_wechselpraeposition() -> None:
    item = _blank("dativ_nach_praeposition", "Das Buch liegt auf dem Tisch.")
    assert item.proposed_answer == "dem"
    assert _blank("dativ_nach_praeposition", "Er wartet an der Ecke.").proposed_answer == "der"


def test_the_two_wechselpraeposition_topics_never_both_fire_on_the_same_token() -> None:
    _, acc_candidates = _select("akkusativ_nach_praeposition", "Das Buch liegt auf dem Tisch.")
    _, dat_candidates = _select("dativ_nach_praeposition", "Das Buch liegt auf dem Tisch.")
    assert acc_candidates == []
    assert len(dat_candidates) == 1


# ==============================================================================
# praepositionen_akkusativ / praepositionen_dativ / praepositionen_genitiv --
# fixed-case prepositions, disjoint from the Wechselpräpositionen above by
# preposition set, not by case.
# ==============================================================================


def test_praepositionen_akkusativ_finds_dogfu_prepositions() -> None:
    item = _blank("praepositionen_akkusativ", "Wir fahren durch den Tunnel.")
    assert item.proposed_answer == "den"
    für_item = _blank("praepositionen_akkusativ", "Das Geschenk ist für meinen Vater.")
    assert für_item.proposed_answer == "meinen"


def test_praepositionen_akkusativ_does_not_fire_after_a_dative_preposition() -> None:
    """ "mit" is a fixed-Dative preposition -- disjoint preposition sets keep
    this from ever firing on "mit dem Bus"."""
    _, candidates = _select("praepositionen_akkusativ", "Ich fahre mit dem Bus.")
    assert candidates == []


def test_praepositionen_dativ_finds_fixed_dative_prepositions() -> None:
    item = _blank("praepositionen_dativ", "Ich fahre mit dem Bus.")
    assert item.proposed_answer == "dem"
    assert (
        _blank("praepositionen_dativ", "Sie wohnt seit einem Jahr hier.").proposed_answer == "einem"
    )


def test_praepositionen_genitiv_finds_all_three_genitive_prepositions() -> None:
    waehrend = _blank("praepositionen_genitiv", "Während der Prüfung darf man nicht sprechen.")
    assert waehrend.proposed_answer == "der"
    statt = _blank("praepositionen_genitiv", "Statt eines Autos kaufte er ein Fahrrad.")
    assert statt.proposed_answer == "eines"
    wegen = _blank("praepositionen_genitiv", "Wegen des schlechten Wetters bleiben wir zuhause.")
    assert wegen.proposed_answer == "des"


# ==============================================================================
# adjektivdeklination_bestimmt / _unbestimmt / _nullartikel -- weak, mixed,
# strong attributive adjective declension.
# ==============================================================================


def test_adjektivdeklination_bestimmt_finds_weak_endings_after_definite_article() -> None:
    tagged, candidates = _select(
        "adjektivdeklination_bestimmt", "Der alte Mann liest die neue Zeitung."
    )
    assert len(candidates) == 2
    first = blank_candidate("adjektivdeklination_bestimmt", tagged, candidates[0]).item
    assert first is not None
    assert first.prompt == "Der ___ Mann liest die neue Zeitung."
    assert first.proposed_answer == "alte"
    second = blank_candidate("adjektivdeklination_bestimmt", tagged, candidates[1]).item
    assert second is not None
    assert second.proposed_answer == "neue"


def test_adjektivdeklination_bestimmt_dative_ending_and_its_only_distractor() -> None:
    item = _blank("adjektivdeklination_bestimmt", "Ich helfe dem netten Nachbarn.")
    assert item.proposed_answer == "netten"
    # Weak declension has exactly two endings total ("e"/"en"); at this cell
    # ("en") the only other possible surface form is the "e" variant.
    assert [d.text for d in item.distractors] == ["nette"]


def test_adjektivdeklination_unbestimmt_finds_mixed_endings() -> None:
    assert (
        _blank("adjektivdeklination_unbestimmt", "Ein alter Baum steht im Garten.").proposed_answer
        == "alter"
    )
    assert (
        _blank("adjektivdeklination_unbestimmt", "Sie kauft ein rotes Kleid.").proposed_answer
        == "rotes"
    )


def test_adjektivdeklination_unbestimmt_covers_the_negative_article_too() -> None:
    """ "kein" declines its following adjective on the same mixed paradigm as
    "ein" -- this is exactly why ArtType Neg is included alongside Ind."""
    item = _blank("adjektivdeklination_unbestimmt", "Kein netter Mensch würde das tun.")
    assert item.proposed_answer == "netter"


def test_adjektivdeklination_nullartikel_finds_strong_endings() -> None:
    deutscher = _blank("adjektivdeklination_nullartikel", "Deutscher Wein ist weltweit bekannt.")
    assert deutscher.proposed_answer == "Deutscher"
    frische = _blank("adjektivdeklination_nullartikel", "Wir trinken frische Milch.")
    assert frische.proposed_answer == "frische"


def test_adjektivdeklination_nullartikel_does_not_fire_after_a_fused_definite_preposition() -> None:
    """ "im großen Garten" ("im" = "in dem") is DEFINITE, hence weak
    declension, even though there is no separate DET token -- this is a
    regression pin for a real bug found while building this selector: before
    the fused-preposition check existed, "großen" here was wrongly selected
    as zero-article/strong."""
    _, strong_candidates = _select(
        "adjektivdeklination_nullartikel", "Die Kinder spielen im großen Garten."
    )
    assert strong_candidates == []
    weak_item = _blank("adjektivdeklination_bestimmt", "Die Kinder spielen im großen Garten.")
    assert weak_item.proposed_answer == "großen"


def test_adjective_declension_selectors_reject_an_unclassifiable_determiner() -> None:
    """ "jeder" is tagged the same (PIAT) as "kein", but its own declension
    trigger is out of this cycle's scope -- it must not be guessed as either
    weak, mixed, or strong."""
    for topic_id in (
        "adjektivdeklination_bestimmt",
        "adjektivdeklination_unbestimmt",
        "adjektivdeklination_nullartikel",
    ):
        _, candidates = _select(topic_id, "Jeder junge Student lernt Deutsch.")
        assert candidates == [], f"{topic_id} should not fire after an unrecognised determiner"


# ==============================================================================
# adjektiv_komparativ_superlativ -- predicative/adverbial comparative and
# superlative only (not the attributive, declined form).
# ==============================================================================


def test_komparativ_superlativ_finds_comparative_forced_by_als() -> None:
    item = _blank("adjektiv_komparativ_superlativ", "Er läuft schneller als sein Bruder.")
    assert item.prompt == "Er läuft ___ als sein Bruder."
    assert item.proposed_answer == "schneller"
    assert item.distractors == []


def test_komparativ_superlativ_finds_superlative_after_am() -> None:
    item = _blank("adjektiv_komparativ_superlativ", "Er läuft am schnellsten von allen.")
    assert item.prompt == "Er läuft am ___ von allen."
    assert item.proposed_answer == "schnellsten"


def test_komparativ_superlativ_rejects_a_bare_comparative_with_no_als() -> None:
    """No forced comparison in sight -- could be read as an intensified
    positive in casual speech, which this topic does not test."""
    _, candidates = _select("adjektiv_komparativ_superlativ", "Er ist sehr schnell.")
    assert candidates == []


def test_komparativ_superlativ_ignores_the_attributive_declined_superlative() -> None:
    """ "der höchste" is attributive and declines (Case/Gender/Number) --
    conflating it with this topic would blur the boundary with the
    adjective-declension topics above, so it is deliberately out of scope."""
    _, candidates = _select(
        "adjektiv_komparativ_superlativ", "Dieser Berg ist der höchste von allen."
    )
    assert candidates == []


# ==============================================================================
# One sentence can yield items for several different topics, but the
# selectors themselves never double-count within one topic.
# ==============================================================================


def test_one_sentence_yields_candidates_for_several_topics() -> None:
    sentence = "Der alte Mann trinkt einen starken Kaffee."
    tagged = sentence_tagger.tag_sentence(sentence)
    assert tagged is not None
    bestimmt = SELECTORS["adjektivdeklination_bestimmt"](tagged)
    unbestimmt = SELECTORS["adjektivdeklination_unbestimmt"](tagged)
    nom = SELECTORS["artikel_bestimmt_nom"](tagged)
    akkusativ = SELECTORS["kasus_akkusativ_formen"](tagged)
    assert len(bestimmt) == 1  # "alte" (weak, after "Der")
    assert len(unbestimmt) == 1  # "starken" (mixed, after "einen")
    assert len(nom) == 1  # "Der"
    assert len(akkusativ) == 1  # "einen"
