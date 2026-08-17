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
# Cycle 3. pronomen_personal_nom / _akk / _dat -- personal pronoun by case.
# ==============================================================================


def test_pronomen_personal_nom_finds_subject_pronouns() -> None:
    er = _blank("pronomen_personal_nom", "Er kommt morgen aus Berlin.")
    assert er.prompt == "___ kommt morgen aus Berlin."
    assert er.proposed_answer == "Er"
    sie = _blank("pronomen_personal_nom", "Sie sind sehr müde.")
    assert sie.proposed_answer == "Sie"


def test_pronomen_personal_akk_finds_object_pronouns() -> None:
    item = _blank("pronomen_personal_akk", "Ich sehe ihn jeden Tag.")
    assert item.prompt == "Ich sehe ___ jeden Tag."
    assert item.proposed_answer == "ihn"


def test_pronomen_personal_akk_fires_on_a_non_reflexive_mich_because_the_subject_differs() -> None:
    """ "mich" is ambiguous between a plain accusative object pronoun and a
    reflexive -- the subject here is "Er" (3rd person), which cannot be the
    antecedent of a 1st-person "mich", so this cannot be reflexive and the
    plain-pronoun topic may safely fire."""
    item = _blank("pronomen_personal_akk", "Er sieht mich nicht.")
    assert item.proposed_answer == "mich"


def test_pronomen_personal_akk_rejects_mich_when_the_subject_could_make_it_reflexive() -> None:
    """ "Ich sehe mich im Spiegel." -- subject and "mich" agree in person and
    number, so this token could be the reflexive object of "sehen", not a
    plain personal pronoun; the topic must not guess which reading is meant
    and produces nothing."""
    _, candidates = _select("pronomen_personal_akk", "Ich sehe mich im Spiegel.")
    assert candidates == []


def test_pronomen_personal_dat_finds_indirect_object_pronouns() -> None:
    item = _blank("pronomen_personal_dat", "Ich helfe ihm gern.")
    assert item.proposed_answer == "ihm"
    assert _blank("pronomen_personal_dat", "Er gibt ihr das Buch.").proposed_answer == "ihr"


# ==============================================================================
# Cycle 3. verben_reflexiv_akk / _dat -- reflexive pronoun case, derived
# structurally rather than trusted from tagger Case morphology (see
# ``_reflexive_case``'s docstring for why the tagger cannot be trusted here).
# ==============================================================================


def test_verben_reflexiv_akk_finds_an_unambiguous_accusative_reflexive_form() -> None:
    item = _blank("verben_reflexiv_akk", "Ich freue mich auf die Ferien.")
    assert item.prompt == "Ich freue ___ auf die Ferien."
    assert item.proposed_answer == "mich"


def test_verben_reflexiv_dat_finds_an_unambiguous_dative_reflexive_form() -> None:
    item = _blank("verben_reflexiv_dat", "Ich kaufe mir einen neuen Laptop.")
    assert item.proposed_answer == "mir"


def test_verben_reflexiv_akk_resolves_ambiguous_sich_via_absence_of_a_bare_object() -> None:
    """ "sich" itself carries no case marking; "schämen" takes no further
    object, so the absence of any other bare accusative elsewhere in the
    clause (the temporal "jeden Morgen" is exempted, "für sein Verhalten"
    is prepositionally governed) is what forces the Accusative reading."""
    item = _blank("verben_reflexiv_akk", "Er schämt sich jeden Morgen für sein Verhalten.")
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select(
        "verben_reflexiv_dat", "Er schämt sich jeden Morgen für sein Verhalten."
    )
    assert dat_candidates == []


def test_verben_reflexiv_dat_resolves_ambiguous_sich_via_a_bare_accusative_object() -> None:
    """Regression pin for a real defect found while building this selector:
    spaCy tags the ENTIRE object NP "ein neues Auto" as ``Case=Nom``
    (subject-shaped), not ``Acc``, so the Case-based bare-object check alone
    finds nothing and would silently default to the wrong (Accusative)
    reading. Word order -- an explicit-determiner NP immediately after
    "sich" with nothing between -- is the independent structural signal that
    catches this; see ``_immediately_followed_by_object_np``'s docstring."""
    item = _blank("verben_reflexiv_dat", "Er kauft sich ein neues Auto.")
    assert item.proposed_answer == "sich"
    _, akk_candidates = _select("verben_reflexiv_akk", "Er kauft sich ein neues Auto.")
    assert akk_candidates == []


def test_verben_reflexiv_dat_finds_sich_before_a_bare_noun_object_with_no_determiner() -> None:
    item = _blank("verben_reflexiv_dat", "Sie wäscht sich die Hände.")
    assert item.proposed_answer == "sich"
    _, akk_candidates = _select("verben_reflexiv_akk", "Sie wäscht sich die Hände.")
    assert akk_candidates == []


def test_verben_reflexiv_akk_rejects_sich_followed_by_a_prepositional_phrase() -> None:
    """ "für Musik" is governed by "für", not a bare object -- must not be
    mistaken for the accusative-object evidence that would force Dative."""
    item = _blank("verben_reflexiv_akk", "Er interessiert sich sehr für Musik.")
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", "Er interessiert sich sehr für Musik.")
    assert dat_candidates == []


def test_verben_reflexiv_dat_rejects_a_reflexive_capable_form_governed_by_a_preposition() -> None:
    """Live-pilot defect: "zu mir" in "Ich lade meine besten Freunde zu mir
    nach Hause ein." is an ordinary prepositional phrase ("to my place"),
    not a reflexive dative object of "einladen" -- it does not corefer with
    the subject as an ARGUMENT of the verb, it is governed by "zu". The
    unrelated accusative object "meine besten Freunde" elsewhere in the
    clause was previously enough to satisfy verben_reflexiv_dat's own
    accusative-object requirement and wrongly select "mir" anyway."""
    _, candidates = _select(
        "verben_reflexiv_dat", "Ich lade meine besten Freunde zu mir nach Hause ein."
    )
    assert candidates == []


# ==============================================================================
# Cycle 3. relativsatz_nom_akk / _dativ / _genitiv -- relative pronoun by case.
# ==============================================================================


def test_relativsatz_nom_akk_finds_nominative_and_accusative_relative_pronouns() -> None:
    nom = _blank("relativsatz_nom_akk", "Der Mann, der dort steht, ist mein Onkel.")
    assert nom.prompt == "Der Mann, ___ dort steht, ist mein Onkel."
    assert nom.proposed_answer == "der"
    akk = _blank("relativsatz_nom_akk", "Das Buch, das ich lese, ist spannend.")
    assert akk.proposed_answer == "das"


def test_relativsatz_dativ_finds_dative_relative_pronouns() -> None:
    item = _blank("relativsatz_dativ", "Der Mann, dem ich geholfen habe, ist mein Nachbar.")
    assert item.prompt == "Der Mann, ___ ich geholfen habe, ist mein Nachbar."
    assert item.proposed_answer == "dem"
    plural = _blank("relativsatz_dativ", "Die Kinder, denen wir geholfen haben, sind glücklich.")
    assert plural.proposed_answer == "denen"


def test_relativsatz_genitiv_finds_dessen_and_deren() -> None:
    item = _blank("relativsatz_genitiv", "Der Mann, dessen Auto kaputt ist, wartet hier.")
    assert item.prompt == "Der Mann, ___ Auto kaputt ist, wartet hier."
    assert item.proposed_answer == "dessen"


# ==============================================================================
# Cycle 3. verb_sein_haben / verb_praesens_regelm / verb_praesens_vokalwechsel
# / modalverben_praesens / verben_trennbar_praesens -- present tense, split by
# conjugation family, each family disjoint from the others by lemma set.
# ==============================================================================


def test_verb_sein_haben_finds_present_tense_sein_and_haben() -> None:
    bin_ = _blank("verb_sein_haben", "Ich bin heute müde.")
    assert bin_.prompt == "Ich ___ heute müde."
    assert bin_.proposed_answer == "bin"
    haben = _blank("verb_sein_haben", "Wir haben ein neues Auto.")
    assert haben.proposed_answer == "haben"


def test_verb_praesens_regelm_finds_a_regular_present_tense_verb() -> None:
    item = _blank("verb_praesens_regelm", "Ich mache heute meine Hausaufgaben.")
    assert item.prompt == "Ich ___ heute meine Hausaufgaben."
    assert item.proposed_answer == "mache"


def test_verb_praesens_regelm_excludes_a_vowel_change_verb() -> None:
    """ "fährt" (fahren, a-to-ä stem change) belongs to
    verb_praesens_vokalwechsel, not the plain-regular topic, even though its
    endings are otherwise regular."""
    _, candidates = _select("verb_praesens_regelm", "Er fährt jeden Tag mit dem Bus.")
    assert candidates == []


def test_verb_praesens_vokalwechsel_finds_stem_changing_present_tense_verbs() -> None:
    item = _blank("verb_praesens_vokalwechsel", "Er fährt jeden Tag mit dem Bus.")
    assert item.proposed_answer == "fährt"
    assert (
        _blank("verb_praesens_vokalwechsel", "Sie liest ein spannendes Buch.").proposed_answer
        == "liest"
    )


def test_verb_praesens_vokalwechsel_excludes_a_plain_regular_verb() -> None:
    _, candidates = _select("verb_praesens_vokalwechsel", "Ich mache heute meine Hausaufgaben.")
    assert candidates == []


def test_verb_praesens_regelm_excludes_an_inseparable_prefixed_vowel_change_verb() -> None:
    """Live-pilot defect: "verlasse" (1st singular of "verlassen") is not a
    direct key in VOKALWECHSEL_PRAESENS ("lassen" is), so it fell through to
    verb_praesens_regelm by default even though "verlassen" is strong
    ("du verlässt"), belonging to verb_praesens_vokalwechsel instead."""
    _, candidates = _select(
        "verb_praesens_regelm", "Um acht Uhr verlasse ich das Haus und gehe zur Bushaltestelle."
    )
    assert candidates == []


def test_verb_praesens_vokalwechsel_finds_an_inseparable_prefixed_verb() -> None:
    """ "verlassen" inherits "lassen"'s own stem-vowel change unchanged --
    "du verlässt" exactly like "du lässt"."""
    item = _blank(
        "verb_praesens_vokalwechsel",
        "Um acht Uhr verlasse ich das Haus und gehe zur Bushaltestelle.",
    )
    assert item.proposed_answer == "verlasse"


def test_modalverben_praesens_finds_present_tense_modals() -> None:
    item = _blank("modalverben_praesens", "Ich kann gut schwimmen.")
    assert item.proposed_answer == "kann"
    assert _blank("modalverben_praesens", "Wir müssen jetzt gehen.").proposed_answer == "müssen"


def test_verben_trennbar_praesens_finds_the_finite_verb_of_a_separable_verb() -> None:
    """The blank is the finite verb ("steht"), not the stranded particle
    ("auf") -- the particle stays in the prompt as the evidence that this is
    a separable verb at all."""
    item = _blank("verben_trennbar_praesens", "Er steht jeden Morgen früh auf.")
    assert item.prompt == "Er ___ jeden Morgen früh auf."
    assert item.proposed_answer == "steht"


def test_verben_trennbar_praesens_rejects_an_ordinary_preposition_after_the_verb() -> None:
    """ "aus Berlin" is a prepositional phrase, not a stranded separable
    particle -- there is no PTKVZ token here, so this must not fire."""
    _, candidates = _select("verben_trennbar_praesens", "Er kommt heute aus Berlin.")
    assert candidates == []


def test_verben_trennbar_praesens_rejects_a_particle_stranded_in_a_later_clause() -> None:
    """Live-pilot defect: the "ein" in "... und schlafe schnell ein." belongs
    to the SECOND clause's own verb ("schlafe" -> "einschlafen"), not to the
    first clause's "gehe" -- "gehen" is not itself separable, and the old
    unbounded "any PTKVZ later in the sentence" check crossed the "und"
    clause boundary to find one anyway."""
    _, candidates = _select(
        "verben_trennbar_praesens",
        "Um zehn Uhr müde gehe ich ins Schlafzimmer und schlafe schnell ein.",
    )
    assert candidates == []


# ==============================================================================
# Cycle 3. praeteritum_sein_haben_modal / praeteritum_vollverben -- simple
# past, split the same way as the present-tense topics above.
# ==============================================================================


def test_praeteritum_sein_haben_modal_finds_war_hatte_and_a_modal() -> None:
    war = _blank("praeteritum_sein_haben_modal", "Ich war gestern krank.")
    assert war.proposed_answer == "war"
    hatten = _blank("praeteritum_sein_haben_modal", "Wir hatten keine Zeit.")
    assert hatten.proposed_answer == "hatten"
    konnte = _blank("praeteritum_sein_haben_modal", "Er konnte nicht kommen.")
    assert konnte.proposed_answer == "konnte"


def test_praeteritum_vollverben_finds_weak_and_strong_simple_past() -> None:
    weak = _blank("praeteritum_vollverben", "Er spielte gestern Fußball.")
    assert weak.prompt == "Er ___ gestern Fußball."
    assert weak.proposed_answer == "spielte"
    strong = _blank("praeteritum_vollverben", "Sie sprach lange mit ihm.")
    assert strong.proposed_answer == "sprach"
    irregular_stem = _blank("praeteritum_vollverben", "Ich ging langsam nach Hause.")
    assert irregular_stem.proposed_answer == "ging"


def test_praeteritum_vollverben_rejects_the_mislemmatised_schalte() -> None:
    """Live-pilot defect: "schalte" is the genuine PRESENT-tense 1st
    singular of "schalten"/"einschalten", but de_core_news_sm mislemmatises
    it to the unrelated verb "schalen" and mistags its own Tense as Past --
    reproduced even on the bare "Ich schalte den Computer ein." (see
    selectors.py). Rebuilding a weak Präteritum from the wrong lemma
    "schalen" reconstructs "schalte" exactly, so this cannot be caught by
    the reconstruction-vs-token check in blanker.py either; the selector
    itself must exclude the lemma."""
    _, candidates = _select(
        "praeteritum_vollverben",
        "Im Büro angekommen schalte ich zuerst meinen Computer ein.",
    )
    assert candidates == []


# ==============================================================================
# Cycle 3. nomen_plural -- trusted directly from the tagger with zero
# distractors, matching the precedent set by adjektiv_komparativ_superlativ
# (no German plural-formation rule/table exists to reconstruct from, so
# there is nothing to cross-check against and no safe way to generate a
# wrong-but-plausible distractor).
# ==============================================================================


def test_nomen_plural_finds_a_plural_noun_with_no_distractors() -> None:
    item = _blank("nomen_plural", "Die Kinder spielen im Garten.")
    assert item.prompt == "Die ___ spielen im Garten."
    assert item.proposed_answer == "Kinder"
    assert item.distractors == []


def test_nomen_plural_rejects_a_singular_noun_mistagged_plural_after_eine() -> None:
    """Live-pilot defect: de_core_news_sm tags "Tasse" (unambiguously
    singular here, governed by "eine") as ``Number=Plur`` in this exact
    sentence, even though "eine" right next to it is correctly tagged
    ``Number=Sing`` -- the noun's own morphology cannot be trusted alone."""
    _, candidates = _select(
        "nomen_plural",
        "Zum Frühstück trinke ich meistens eine Tasse Kaffee und esse ein Brötchen mit Butter.",
    )
    assert candidates == []


# ==============================================================================
# Cycle 3. perfekt_haben / perfekt_sein / plusquamperfekt -- compound past
# tenses. The aux/participle lemma consistency check (via AUX_SEIN_LEMMAS)
# and the anteriority-marker guard (nachdem/bevor) are what keep Perfekt and
# Plusquamperfekt from ever both accepting the same sentence -- this is the
# exact "Gestern ___ wir nach Berlin gefahren" defect named in the task brief.
# ==============================================================================


def test_perfekt_haben_finds_the_haben_auxiliary() -> None:
    item = _blank("perfekt_haben", "Ich habe das Buch gelesen.")
    assert item.prompt == "Ich ___ das Buch gelesen."
    assert item.proposed_answer == "habe"


def test_perfekt_sein_finds_the_sein_auxiliary_with_a_motion_verb() -> None:
    item = _blank("perfekt_sein", "Wir sind nach Berlin gefahren.")
    assert item.proposed_answer == "sind"


def test_perfekt_haben_and_perfekt_sein_are_disjoint_by_aux_participle_consistency() -> None:
    """ "gefahren" (fahren, a sein-verb) never pairs with "haben", and
    "gelesen" (lesen, a haben-verb) never pairs with "sein" -- each topic
    must reject the other's sentence outright, not merely fail to prefer it."""
    _, haben_candidates = _select("perfekt_haben", "Wir sind nach Berlin gefahren.")
    assert haben_candidates == []
    _, sein_candidates = _select("perfekt_sein", "Ich habe das Buch gelesen.")
    assert sein_candidates == []


def test_perfekt_sein_fires_on_the_named_perfekt_plusquamperfekt_ambiguity_sentence() -> None:
    """The exact sentence named in this cycle's brief as a real, previously
    audited defect: "Gestern ___ wir nach Berlin gefahren" wrongly accepted
    both "sind" (Perfekt) and "waren" (Plusquamperfekt). With a fronted
    temporal adverb and no anteriority marker (nachdem/bevor), this can only
    be Perfekt -- plusquamperfekt must reject it outright (see the next
    test), not merely decline to prefer it."""
    item = _blank("perfekt_sein", "Gestern sind wir nach Berlin gefahren.")
    assert item.prompt == "Gestern ___ wir nach Berlin gefahren."
    assert item.proposed_answer == "sind"


def test_plusquamperfekt_rejects_the_same_sentence_shape_without_an_anteriority_marker() -> None:
    """ "Gestern waren wir nach Berlin gefahren." is the Plusquamperfekt
    surface form, but with no "nachdem"/"bevor" anywhere in the sentence
    there is nothing distinguishing it from an isolated, contextless simple
    past reading -- the selector must not guess and produces nothing,
    exactly mirroring the previous audit's finding for this construction."""
    _, plusquamperfekt_candidates = _select(
        "plusquamperfekt", "Gestern waren wir nach Berlin gefahren."
    )
    assert plusquamperfekt_candidates == []
    _, perfekt_sein_candidates = _select("perfekt_sein", "Gestern waren wir nach Berlin gefahren.")
    assert perfekt_sein_candidates == []  # "waren" is not a Perfekt auxiliary form either


def test_plusquamperfekt_fires_with_an_explicit_anteriority_marker() -> None:
    """ "nachdem" makes the anteriority explicit, and licenses the
    Plusquamperfekt reading even in the verb-final subordinate-clause word
    order, where the participle precedes rather than follows its auxiliary."""
    item = _blank("plusquamperfekt", "Nachdem wir gegessen hatten, gingen wir spazieren.")
    assert item.prompt == "Nachdem wir gegessen ___, gingen wir spazieren."
    assert item.proposed_answer == "hatten"


# ==============================================================================
# Cycle 3. konjunktiv_ii_hoeflichkeit / _irreal_gegenwart / _vergangenheit --
# Konjunktiv II, split by clause shape (a polite question, a wenn-clause
# describing the present, a wenn-clause describing the past).
# ==============================================================================


def test_konjunktiv_ii_hoeflichkeit_finds_a_polite_question() -> None:
    item = _blank("konjunktiv_ii_hoeflichkeit", "Könnten Sie mir bitte helfen?")
    assert item.prompt == "___ Sie mir bitte helfen?"
    assert item.proposed_answer == "Könnten"


def test_konjunktiv_ii_hoeflichkeit_does_not_fire_on_a_wenn_clause() -> None:
    _, candidates = _select("konjunktiv_ii_hoeflichkeit", "Wenn ich Zeit hätte, würde ich kommen.")
    assert candidates == []


def test_konjunktiv_ii_irreal_gegenwart_finds_both_the_haette_and_the_wuerde() -> None:
    tagged, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart", "Wenn ich Zeit hätte, würde ich kommen."
    )
    assert len(candidates) == 2
    first = blank_candidate("konjunktiv_ii_irreal_gegenwart", tagged, candidates[0]).item
    assert first is not None
    assert first.proposed_answer == "hätte"
    second = blank_candidate("konjunktiv_ii_irreal_gegenwart", tagged, candidates[1]).item
    assert second is not None
    assert second.proposed_answer == "würde"


def test_konjunktiv_ii_irreal_gegenwart_does_not_fire_without_a_wenn_clause() -> None:
    _, candidates = _select("konjunktiv_ii_irreal_gegenwart", "Könnten Sie mir bitte helfen?")
    assert candidates == []


def test_konjunktiv_ii_vergangenheit_finds_a_past_counterfactual() -> None:
    item = _blank(
        "konjunktiv_ii_vergangenheit", "Wenn ich das gewusst hätte, wäre ich nicht gekommen."
    )
    assert item.proposed_answer == "wäre"


# ==============================================================================
# Cycle 3. passiv_praesens / passiv_praeteritum / passiv_modalverben /
# zustandspassiv / zustandspassiv_zeiten -- Vorgangspassiv (werden) vs
# Zustandspassiv (sein), split further by tense; both gated on the
# participle's lemma being in TRANSITIVE_LEMMAS so an intransitive verb
# ("gefahren") is never mistaken for a passive participle.
# ==============================================================================


def test_passiv_praesens_finds_present_tense_vorgangspassiv() -> None:
    item = _blank("passiv_praesens", "Das Auto wird repariert.")
    assert item.prompt == "Das Auto ___ repariert."
    assert item.proposed_answer == "wird"


def test_passiv_praeteritum_finds_past_tense_vorgangspassiv() -> None:
    item = _blank("passiv_praeteritum", "Das Auto wurde repariert.")
    assert item.proposed_answer == "wurde"


def test_passiv_modalverben_finds_a_modal_plus_passive_infinitive() -> None:
    item = _blank("passiv_modalverben", "Das Auto kann repariert werden.")
    assert item.prompt == "Das Auto ___ repariert werden."
    assert item.proposed_answer == "kann"


def test_passiv_modalverben_skips_a_known_tagger_gap_on_muss() -> None:
    """Confirmed empirically: in exactly this construction ("muss" + past
    participle + "werden"), de_core_news_sm sometimes tags "muss" with
    almost no morphology at all (only ``{'Degree': 'Pos'}``, no
    VerbForm/Tense/Person/Number), which is not enough for the selector to
    place it in any conjugation cell. Other modals and other persons of
    "müssen" tag correctly in the same construction (see
    passiv_modalverben's positive test above and
    test_modalverben_praesens_finds_present_tense_modals); this is a narrow,
    documented gap in this one 3sg-present surface form, not a design flaw,
    and producing nothing here is the correct, safe behaviour."""
    _, candidates = _select("passiv_modalverben", "Das Auto muss repariert werden.")
    assert candidates == []


def test_zustandspassiv_finds_present_tense_sein_plus_participle() -> None:
    item = _blank("zustandspassiv", "Das Auto ist repariert.")
    assert item.proposed_answer == "ist"


def test_zustandspassiv_and_passiv_praesens_are_disjoint_by_auxiliary() -> None:
    _, zustandspassiv_candidates = _select("zustandspassiv", "Das Auto wird repariert.")
    assert zustandspassiv_candidates == []
    _, passiv_candidates = _select("passiv_praesens", "Das Auto ist repariert.")
    assert passiv_candidates == []


def test_zustandspassiv_zeiten_finds_past_tense_zustandspassiv() -> None:
    item = _blank("zustandspassiv_zeiten", "Das Auto war repariert.")
    assert item.proposed_answer == "war"


def test_zustandspassiv_zeiten_blanks_gewesen_for_the_perfekt_shaped_variant() -> None:
    """ "ist repariert gewesen" blanks the fixed word "gewesen", not "ist" --
    blanking "ist" here would be indistinguishable from plain present-tense
    Zustandspassiv ("Das Auto ist repariert.")."""
    item = _blank("zustandspassiv_zeiten", "Das Auto ist repariert gewesen.")
    assert item.prompt == "Das Auto ist repariert ___."
    assert item.proposed_answer == "gewesen"
    assert item.distractors == []


# ==============================================================================
# Cycle 3. futur_i / futur_ii -- werden + infinitive vs werden + participle
# + haben/sein infinitive, disjoint by the presence of a participle.
# ==============================================================================


def test_futur_i_finds_werden_plus_a_bare_infinitive() -> None:
    item = _blank("futur_i", "Ich werde morgen kommen.")
    assert item.prompt == "Ich ___ morgen kommen."
    assert item.proposed_answer == "werde"


def test_futur_i_does_not_fire_when_a_participle_follows() -> None:
    """That shape belongs to futur_ii, not futur_i."""
    _, candidates = _select("futur_i", "Er wird das Buch gelesen haben.")
    assert candidates == []


def test_futur_ii_finds_werden_plus_participle_plus_haben() -> None:
    item = _blank("futur_ii", "Er wird das Buch gelesen haben.")
    assert item.proposed_answer == "wird"


# ==============================================================================
# Cycle 3. infinitiv_mit_zu / infinitiv_um_zu -- "zu" before an infinitive,
# split by whether "um" precedes it in the same clause.
# ==============================================================================


def test_infinitiv_mit_zu_finds_a_plain_zu_infinitive() -> None:
    item = _blank("infinitiv_mit_zu", "Er hat vergessen, das Fenster zu schließen.")
    assert item.prompt == "Er hat vergessen, das Fenster ___ schließen."
    assert item.proposed_answer == "zu"
    assert item.distractors == []


def test_infinitiv_mit_zu_does_not_fire_when_um_precedes_it() -> None:
    _, candidates = _select("infinitiv_mit_zu", "Sie lernt Deutsch, um in Berlin zu studieren.")
    assert candidates == []


def test_infinitiv_um_zu_finds_a_zu_infinitive_preceded_by_um() -> None:
    item = _blank("infinitiv_um_zu", "Sie lernt Deutsch, um in Berlin zu studieren.")
    assert item.prompt == "Sie lernt Deutsch, um in Berlin ___ studieren."
    assert item.proposed_answer == "zu"


# ==============================================================================
# Cycle 3. partizip_i_attributiv -- attributive present participle
# ("schlafende"), gated on a closed, hand-verified list of infinitives whose
# Partizip I is a plausible, testable adjective (not a "lemma ends in -d"
# heuristic, which would false-positive on genuine adjectives like "rund",
# "gesund", "fremd", "blind" -- see ``_PARTIZIP_I_VERBS``'s docstring).
# ==============================================================================


def test_partizip_i_attributiv_finds_an_attributive_present_participle() -> None:
    item = _blank("partizip_i_attributiv", "Das schlafende Kind liegt im Bett.")
    assert item.prompt == "Das ___ Kind liegt im Bett."
    assert item.proposed_answer == "schlafende"


def test_partizip_i_attributiv_does_not_fire_on_a_genuine_d_final_adjective() -> None:
    """ "gesund" is a plain adjective, not any verb's Partizip I -- it must
    not be guessed into this topic just because it ends in "d"."""
    _, candidates = _select("partizip_i_attributiv", "Das gesunde Kind spielt draußen.")
    assert candidates == []


# ==============================================================================
# Cycle 3. partizip_ii_attributiv_erweitert -- extended attributive participle
# ("das von Experten entwickelte Programm"), gated on a closed list of known
# participle forms and requiring an ADP inside the span as the evidence of
# actual extension (a bare attributive participle with no extension is
# already adjektivdeklination_bestimmt's territory, not this topic's).
# ==============================================================================


def test_partizip_ii_attributiv_erweitert_finds_an_extended_participle() -> None:
    item = _blank(
        "partizip_ii_attributiv_erweitert", "Das von Experten entwickelte Programm ist erfolgreich."
    )
    assert item.prompt == "Das von Experten ___ Programm ist erfolgreich."
    assert item.proposed_answer == "entwickelte"


def test_partizip_ii_attributiv_erweitert_does_not_fire_without_an_extension() -> None:
    """No preposition inside the span ("gut" is a plain adverb) -- this is
    not evidence of a genuine extended-participle construction."""
    _, candidates = _select(
        "partizip_ii_attributiv_erweitert", "Der gut geplante Ausflug war ein Erfolg."
    )
    assert candidates == []


def test_partizip_ii_attributiv_erweitert_does_not_fire_on_a_bare_participle() -> None:
    """No extension at all -- belongs to adjektivdeklination_bestimmt."""
    _, candidates = _select(
        "partizip_ii_attributiv_erweitert", "Das entwickelte Programm ist erfolgreich."
    )
    assert candidates == []


# ==============================================================================
# Cycle 3. praepositionen_genitiv_gehoben -- gehobene (elevated-register)
# genitive prepositions, reusing the same ``_determiner_selector`` factory as
# cycle 2's praepositionen_genitiv, just with a different preposition set.
# ==============================================================================


def test_praepositionen_genitiv_gehoben_finds_anhand_and_mangels() -> None:
    anhand = _blank(
        "praepositionen_genitiv_gehoben", "Anhand der Beweise konnte er die Tat rekonstruieren."
    )
    assert anhand.proposed_answer == "der"
    mangels = _blank(
        "praepositionen_genitiv_gehoben", "Mangels eines Beweises wurde er freigesprochen."
    )
    assert mangels.proposed_answer == "eines"


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
