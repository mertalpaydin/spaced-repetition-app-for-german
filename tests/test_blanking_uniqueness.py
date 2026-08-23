"""Tests for src/generation/blanking/uniqueness.py, run against real spaCy
analysis (same posture as test_blanking_selectors.py: this is proof against
genuine German sentences, not toy strings).

Every sentence below is real, grammatical German, drawn from or modelled on
docs/audits/cycle-04-report.md's own worked examples -- the modal-verb, the
carrier-anchored and carrier-unanchored dative pronoun, and the open-class
plural-noun cases that report names explicitly.

Skipped whole-module if spaCy's ``de_core_news_sm`` model is not installed
in this environment (mirrors every other real-tagger test in this package).
"""

import pytest
from src.generation.blanking import sentence_tagger
from src.generation.blanking.selectors import SELECTORS
from src.generation.blanking.uniqueness import UniquenessOutcome, check_uniqueness

pytestmark = pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)


def _check(topic_id: str, sentence: str, candidate_index: int = 0) -> UniquenessOutcome:
    tagged = sentence_tagger.tag_sentence(sentence)
    assert tagged is not None
    candidates = SELECTORS[topic_id](tagged)
    assert candidates, f"expected at least one candidate for {topic_id!r} in {sentence!r}"
    return check_uniqueness(tagged, candidates[candidate_index])


# ==============================================================================
# Modal verbs: essentially always interchangeable, so always flagged, on
# every topic whose selector can produce a modal-lemma candidate.
# ==============================================================================


def test_modalverben_praesens_is_flagged_ambiguous_with_no_cue() -> None:
    """1st/3rd-plural present tense of a modal is spelled identically to its
    own infinitive ("wir müssen" == "müssen") -- ``selectors._citation_cue``
    withholds a cue there rather than hand over the answer verbatim (see
    ``test_blanking_selectors.test_modalverben_praesens_cue_is_none_when_it_
    would_equal_the_answer``), so this one cell is still genuinely
    unrescuable and stays flagged exactly as before the cue mechanism
    existed."""
    outcome = _check("modalverben_praesens", "Wir müssen jetzt gehen.")
    assert outcome.unique is False
    assert outcome.reason == "modal_verb_interchangeable"


def test_modalverben_praesens_is_rescued_by_its_own_cue() -> None:
    """docs/audits/cycle-04-report.md's own example: "kann" used to be one
    of several equally grammatical modals here (muss/soll/sollte/darf) with
    nothing to rule the others out. The selector now supplies "(können)" --
    the modal's own infinitive -- and that is exactly the fact that turns a
    free lexical choice back into a forced one, the same way a carrier
    anchor does for an accusative/dative pronoun."""
    outcome = _check(
        "modalverben_praesens",
        "Das Fleisch kann scharf angebraten werden, wenn ein kräftiger Geschmack gewünscht wird.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_passiv_modalverben_is_flagged_ambiguous_with_no_cue() -> None:
    """Same no-cue cell as ``modalverben_praesens`` above, reached through
    this topic's own selector: 3rd-plural present "müssen" == its own
    infinitive, so no cue is supplied and the item stays unrescuable."""
    outcome = _check("passiv_modalverben", "Die Fenster müssen repariert werden.")
    assert outcome.unique is False
    assert outcome.reason == "modal_verb_interchangeable"


def test_passiv_modalverben_is_rescued_by_its_own_cue() -> None:
    outcome = _check("passiv_modalverben", "Das Fenster kann nicht mehr repariert werden.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_praeteritum_sein_haben_modal_rescues_only_its_modal_candidates() -> None:
    """The same topic's selector also matches bare sein/haben Präteritum,
    which is NOT a modal and was never gated in the first place (the gate is
    a property of the candidate's own lemma, not of the topic name) -- it
    passes structurally, not because of a cue. The modal candidate now also
    passes, but for the OTHER reason this module's policy table names: every
    Präteritum modal form differs from its own infinitive (the weak
    preterite always adds "-te" and shifts the stem vowel), so
    ``selectors._citation_cue`` can always supply one here, unlike the
    present-tense 1st/3rd-plural cell above."""
    modal_outcome = _check("praeteritum_sein_haben_modal", "Er konnte gestern nicht kommen.")
    assert modal_outcome.unique is True
    assert modal_outcome.reason is None

    aux_outcome = _check("praeteritum_sein_haben_modal", "Er war gestern sehr müde.")
    assert aux_outcome.unique is True
    assert aux_outcome.reason is None


def test_verb_sein_haben_is_not_flagged_the_auxiliary_is_structurally_fixed() -> None:
    """Unlike a modal, sein/haben is not a free lexical choice -- Perfekt
    picks its auxiliary by verb, Passiv always takes werden -- so there is no
    other LEXEME that could stand in this slot. That alone is not the whole
    story any more (docs/audits/cycle-06-report.md task 1): the sentence's
    own "heute" is what anchors the TENSE ("bin" vs "war"), a separate
    question from lexeme choice -- see the ``auxiliary_tense_unanchored``
    tests below for the case where that anchor is missing."""
    outcome = _check("verb_sein_haben", "Ich bin heute sehr müde.")
    assert outcome.unique is True
    assert outcome.reason is None


# ==============================================================================
# Auxiliary tense anchoring (docs/audits/cycle-06-report.md task 1): a bare
# sein/haben/werden finite form is ambiguous over TENSE, not lexeme, unless
# the carrier anchors it -- an explicit time expression, a genuinely
# subordinated sibling clause with matching tense, or a construction (Futur,
# Konjunktiv II, Perfekt/Plusquamperfekt) whose own shape already fixes it.
# ==============================================================================


def test_verb_sein_haben_is_flagged_with_no_tense_anchor() -> None:
    outcome = _check("verb_sein_haben", "Das Wetter ist schön.")
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_zustandspassiv_is_flagged_with_no_tense_anchor() -> None:
    """The report's own worked example: "ist" and "war" both fit here
    equally well, and "aber" coordinates two independent main clauses, so
    the present-tense "müssen" in the second clause is not a genuine
    tense-agreement anchor for the first (see
    ``uniqueness._sibling_clause_tense_anchor``'s own docstring)."""
    outcome = _check(
        "zustandspassiv",
        "Das Buffet ist bereits gut geplant, aber die Getränke müssen wir noch einkaufen.",
    )
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_verb_sein_haben_passes_when_a_subordinated_sibling_clause_anchors_the_tense() -> None:
    """The report's own other worked example: the present-tense matrix
    clause ("bitten wir") forces "sind", not "waren", because the "obwohl"
    clause describes the SAME present state -- genuine subordination, unlike
    the "aber" case above."""
    outcome = _check(
        "verb_sein_haben",
        "Obwohl die neuen Vorschriften sehr streng sind, bitten wir um Ihr Verständnis.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_praeteritum_sein_haben_modal_aux_is_flagged_with_no_tense_anchor() -> None:
    outcome = _check("praeteritum_sein_haben_modal", "Das Wetter war schön.")
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_passiv_praesens_is_flagged_with_no_tense_anchor() -> None:
    outcome = _check("passiv_praesens", "Das Auto wird repariert.")
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_passiv_praeteritum_passes_with_an_explicit_time_expression_anchor() -> None:
    outcome = _check("passiv_praeteritum", "Das Auto wurde gestern repariert.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_perfekt_haben_is_not_flagged_the_participle_fixes_the_construction() -> None:
    """No anchor needed: a haben+Partizip-II candidate is unambiguously
    Perfekt (haben is never a Zustandspassiv auxiliary), so there is no
    rival-tense reading of this exact shape to rule out."""
    outcome = _check("perfekt_haben", "Ich habe das Buch gelesen.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_plusquamperfekt_is_not_flagged_the_anteriority_marker_fixes_the_construction() -> None:
    outcome = _check("plusquamperfekt", "Nachdem wir gegessen hatten, gingen wir spazieren.")
    assert outcome.unique is True
    assert outcome.reason is None


# -- TODO.md 1.6: a Plusquamperfekt candidate needs its OWN, narrower anchor
# (a bevor/nachdem clause, or a genuine second past-tense event), never the
# generic vague-adverb list ------------------------------------------------
#
# docs/audits/cycle-09-report.md 1.6: the ``auxiliary_tense_unanchored`` gate
# let a Plusquamperfekt candidate through whenever ANY of
# ``paradigms.TEMPORAL_ANCHOR_LEMMAS``'s bare adverbs was present anywhere
# in the sentence, including "zuvor"/"davor"/"vorher" -- none of which
# actually states that one event precedes another, only WHEN something
# happened. The verifier caught this 15 times in one pilot run: "hatte" vs
# "habe", "war" vs "bin", "hatten" vs "haben". These pin the exact rejected
# sentences (from the staged pilot review data) plus the exact accepted
# ones, both from the same run, so the fix is shown to close the reported
# gap without also closing the population that was already fine.


def test_praeteritum_sein_haben_modal_plusquamperfekt_shape_is_flagged_on_a_bare_adverb() -> None:
    """The verifier's own rejected item (docs/audits/cycle-09-report.md
    1.6): "Kurz zuvor war ich schnellen Schrittes in das leise Gebäude
    gelaufen." -- "zuvor" alone (no bevor/nachdem clause, no second past
    event) is not a real anchor; "bin" is exactly as natural here."""
    outcome = _check(
        "praeteritum_sein_haben_modal",
        "Kurz zuvor war ich schnellen Schrittes in das leise Gebäude gelaufen.",
    )
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_praeteritum_sein_haben_modal_plusquamperfekt_shape_is_flagged_on_gestern_alone() -> None:
    """A second rejected shape from the same run: "gestern" IS in
    ``paradigms.TEMPORAL_ANCHOR_LEMMAS`` and used to anchor this
    unconditionally, but it states WHEN, not that one event precedes
    another -- with no bevor/nachdem clause and no second past-tense event
    in the other clause, "hatte" is not the only natural reading."""
    outcome = _check(
        "praeteritum_sein_haben_modal",
        "Die kleine Bäckerei an der Ecke, die jeden Morgen frischen Kuchen backt, "
        "hatte gestern leider schon geschlossen.",
    )
    assert outcome.unique is False
    assert outcome.reason == "auxiliary_tense_unanchored"


def test_praeteritum_sein_haben_modal_plusquamperfekt_shape_passes_on_a_bevor_clause() -> None:
    """The accepted counterpart, same run, same construction: an explicit
    "bevor" clause is a real anchor."""
    outcome = _check(
        "praeteritum_sein_haben_modal",
        "Bevor der Unterricht an diesem Tag anfing, hatte ich meinen schweren "
        "Rucksack eilig in die hinterste Ecke des Klassenzimmers gestellt.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_praeteritum_sein_haben_modal_plusquamperfekt_shape_passes_on_a_second_past_event() -> None:
    """The accepted counterpart's other shape: no bevor/nachdem clause at
    all, but a genuine second past-tense event in the other clause
    ("hielt") -- the narrative-past pattern ("matrix clause narrates a
    single past moment; the relative clause's own Plusquamperfekt precedes
    it") this task's own ``_has_anteriority_marker`` docstring names."""
    outcome = _check(
        "praeteritum_sein_haben_modal",
        "In der Hand hielt sie das Portemonnaie, das ihr Onkel zum Geburtstag geschenkt hatte.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_konjunktiv_ii_irreal_gegenwart_is_not_flagged_no_rival_tense_form_exists() -> None:
    outcome = _check("konjunktiv_ii_irreal_gegenwart", "Wenn ich Zeit hätte, würde ich kommen.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_futur_i_is_not_flagged_no_rival_construction_exists() -> None:
    outcome = _check("futur_i", "Ich werde morgen ins Kino gehen.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_futur_i_is_flagged_when_interchangeable_with_a_modal_with_no_time_anchor() -> None:
    """docs/audits/cycle-07-report.md defect 12: "werden Sie den Termin
    einhalten" reads just as naturally as "müssen/sollen/können Sie den
    Termin einhalten" -- nothing in the sentence forces a future reading
    over a modal one, so the item is unsolvable by typing and must be
    skipped rather than accepted as futur_i."""
    outcome = _check(
        "futur_i",
        "Obwohl die Bearbeitungszeit kurz ist, werden Sie den Termin einhalten.",
    )
    assert outcome.unique is False
    # Cycle 11, decision D1: the reason changed from
    # "futur_i_modal_interchangeable" to "auxiliary_lexeme_uncued", which
    # fires first and names the same defect more directly. "Sie werden"
    # is 3rd plural, where "werden" the citation form and "werden" the
    # answer are the same string, so no cue can be given and the modal
    # choice this test is about stays open. The verdict is unchanged and
    # is what the test is for; only the label moved.
    assert outcome.reason == "auxiliary_lexeme_uncued"


def test_futur_ii_is_not_flagged_no_rival_construction_exists() -> None:
    outcome = _check("futur_ii", "Er wird das Buch gelesen haben.")
    assert outcome.unique is True
    assert outcome.reason is None


# ==============================================================================
# Personal pronouns: Nominative trusted on verb agreement alone UNLESS that
# verb form is itself syncretic across more than one pronoun
# (docs/audits/cycle-07-report.md section C, the sixth uniqueness reason);
# Accusative/Dative always need a carrier-supplied anchor or are flagged.
# ==============================================================================


def test_pronomen_personal_nom_is_never_flagged_verb_agreement_is_the_anchor() -> None:
    """1st singular Präsens ("sehe") is not syncretic with anything --
    ``_nominative_pronoun_syncretic`` returns ``False`` and this candidate
    passes exactly as it did before section C's fix existed."""
    outcome = _check("pronomen_personal_nom", "Ich sehe den Mann auf der anderen Straßenseite.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_nom_is_not_flagged_for_a_present_tense_singular() -> None:
    """docs/audits/cycle-07-report.md section C's own KEEP example: 1st
    singular Präsens is never syncretic with 3rd singular ("ich genieße" vs
    "er genießt" genuinely differ), unlike the SAME two persons in the
    Präteritum below."""
    outcome = _check("pronomen_personal_nom", "Nun genieße ich die ruhige Abendstunde.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_nom_is_flagged_for_the_wir_sie_sie_plural_syncretism() -> None:
    """docs/audits/cycle-07-report.md section C's own SKIP example: 1st and
    3rd person plural share one finite form in every German tense
    ("wir/sie/Sie haben"), and this tagger cannot tell 3rd-plural "sie" from
    formal "Sie" apart at all -- with no second matching pronoun anywhere in
    the sentence, the candidate stays genuinely unrescuable."""
    outcome = _check(
        "pronomen_personal_nom",
        "Sie haben im letzten Jahr vielen Touristen geholfen, weil die Gäste den Weg "
        "nicht gefunden haben.",
    )
    assert outcome.unique is False
    assert outcome.reason == "nominative_pronoun_syncretic"


def test_pronomen_personal_nom_is_not_rescued_by_a_second_pronoun_in_another_clause() -> None:
    """This test used to assert ``unique is True`` for this sentence, on the
    reasoning that a second "Sie" elsewhere in it anchors the blank. The
    cycle 11 audit shows that reasoning does not hold for a SUBJECT slot:
    "Wir haben den Urlaubern eine Nachricht geschickt, obwohl Sie damals
    selbst sehr müde waren." is equally good German, so the second "Sie"
    settles nothing about the first. The anchor rescue silently assumed
    coreference that a same-person pronoun in a different clause does not
    supply, and it is gone from the Nominative branch for that reason. The
    expectation is inverted here rather than the gate loosened, per
    CLAUDE.md rule 7; see ``uniqueness._nominative_pronoun_settled``."""
    outcome = _check(
        "pronomen_personal_nom",
        "Sie haben den Urlaubern eine Nachricht geschickt, obwohl Sie damals selbst "
        "sehr müde waren.",
    )
    assert outcome.unique is False
    assert outcome.reason == "nominative_pronoun_syncretic"


def test_pronomen_personal_nom_is_flagged_for_the_1st_3rd_singular_preterite_syncretism() -> None:
    """docs/audits/cycle-07-report.md section C: 1st and 3rd person singular
    share one finite form in the Präteritum ("ich arbeitete"/"er
    arbeitete") -- unlike the Präsens case above, this tagger surfaces both
    as ``Tense=Past``, so this candidate needs (and here has none of) the
    same carrier anchor."""
    outcome = _check("pronomen_personal_nom", "Gestern arbeitete er lange im Büro.")
    assert outcome.unique is False
    assert outcome.reason == "nominative_pronoun_syncretic"


def test_pronomen_personal_nom_is_flagged_for_a_second_1st_3rd_singular_preterite_verb() -> None:
    """A second, independently confirmed instance of the same Präteritum
    syncretism, with a different strong verb ("rief") -- pinned separately
    since ``_nominative_pronoun_syncretic`` reads the clause's own finite
    verb's ``Tense``, not a per-lemma fact, and a single example does not
    prove that generalises."""
    outcome = _check("pronomen_personal_nom", "Gestern rief sie ihre Mutter an.")
    assert outcome.unique is False
    assert outcome.reason == "nominative_pronoun_syncretic"


def test_pronomen_personal_dat_is_settled_by_its_cue_with_no_anchor() -> None:
    """docs/audits/cycle-04-report.md's own example: mir/ihm/ihr/uns/ihnen
    would all fit here just as well as "Ihnen".

    Cycle 11, owner decision D2: this topic now carries a cue, the
    Nominative form of the same pronoun, which names person, number and
    gender and leaves the learner only the case to supply. That settles the
    blank on its own, so the anchor this test was written about is no longer
    what decides the outcome. The expectation is inverted rather than the
    cue withheld: the anchor reasoning below is still correct about anchors,
    it is simply no longer the binding constraint. See
    ``uniqueness.check_uniqueness``'s ``personal_pronoun`` branch.
    """
    outcome = _check(
        "pronomen_personal_dat",
        "Das Restaurant hatte einen neuen Koch eingestellt, und das Essen schmeckte Ihnen "
        "ausgezeichnet.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_dat_passes_when_a_matching_pronoun_anchors_it() -> None:
    """docs/audits/cycle-04-report.md's own solvable counter-example: "Sie"
    (Person=3, Number=Plur) forces "Ihnen" (the same cell) -- must NOT be
    skipped."""
    outcome = _check(
        "pronomen_personal_dat",
        "Wir erklären Ihnen den Fehler, weil Sie das System besser verstehen müssen.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_dat_passes_when_a_matching_possessive_anchors_it() -> None:
    """docs/audits/cycle-04-report.md's own second solvable example: "meinen"
    (1st person singular possessor) forces "mir" -- must NOT be skipped."""
    outcome = _check(
        "pronomen_personal_dat",
        "Gegen Mittag hatte unsere Schulleiterin mir den ersten Preis für meinen Aufsatz "
        "überreicht.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_dat_cue_beats_a_subject_possessive() -> None:
    """docs/audits/cycle-07-report.md defects 10/11: "Mein bester Freund
    Timo" is the SUBJECT of "hat", not a co-referring argument of "mir" --
    the possessive-person anchor must not fire from the subject NP, so
    mir/ihm/ihr/uns/ihnen are all still equally plausible here.

    Cycle 11, owner decision D2: this topic now carries a cue, the
    Nominative form of the same pronoun, which names person, number and
    gender and leaves the learner only the case to supply. That settles the
    blank on its own, so the anchor this test was written about is no longer
    what decides the outcome. The expectation is inverted rather than the
    cue withheld: the anchor reasoning below is still correct about anchors,
    it is simply no longer the binding constraint. See
    ``uniqueness.check_uniqueness``'s ``personal_pronoun`` branch.
    """
    outcome = _check(
        "pronomen_personal_dat",
        "Mein bester Freund Timo hat mir gestern ein sehr gutes Buch geschenkt.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_dat_cue_beats_a_second_subject_possessive() -> None:
    """docs/audits/cycle-07-report.md defect 11: the same subject-anchor
    defect on a second, shorter sentence ("Mein Kollege hat mir ...").

    Cycle 11, owner decision D2: this topic now carries a cue, the
    Nominative form of the same pronoun, which names person, number and
    gender and leaves the learner only the case to supply. That settles the
    blank on its own, so the anchor this test was written about is no longer
    what decides the outcome. The expectation is inverted rather than the
    cue withheld: the anchor reasoning below is still correct about anchors,
    it is simply no longer the binding constraint. See
    ``uniqueness.check_uniqueness``'s ``personal_pronoun`` branch.
    """
    outcome = _check(
        "pronomen_personal_dat",
        "Mein Kollege hat mir heute einen leckeren Apfelkuchen mitgebracht.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_akk_is_settled_by_its_cue_with_no_anchor() -> None:
    """Cycle 11, owner decision D2: this topic now carries a cue, the
    Nominative form of the same pronoun, which names person, number and
    gender and leaves the learner only the case to supply. That settles the
    blank on its own, so the anchor this test was written about is no longer
    what decides the outcome. The expectation is inverted rather than the
    cue withheld: the anchor reasoning below is still correct about anchors,
    it is simply no longer the binding constraint. See
    ``uniqueness.check_uniqueness``'s ``personal_pronoun`` branch."""
    outcome = _check("pronomen_personal_akk", "Ich sehe ihn jeden Tag.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_akk_passes_when_a_matching_pronoun_anchors_it() -> None:
    outcome = _check(
        "pronomen_personal_akk",
        "Wir laden ihn herzlich ein, weil er unser bester Freund ist.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_ambiguous_stems_sein_and_ihr_are_still_not_anchors() -> None:
    """ "seinen" (his/its, syncretic between a masc./neut. possessor) must
    not be treated as an anchor even though it agrees in person on one
    reading -- a wrong anchor here is the exact failure mode this gate
    exists to prevent (module docstring).

    Cycle 11, owner decision D2: this topic now carries a cue, the
    Nominative form of the same pronoun, which names person, number and
    gender and leaves the learner only the case to supply. That settles the
    blank on its own, so the anchor this test was written about is no longer
    what decides the outcome. The expectation is inverted rather than the
    cue withheld: the anchor reasoning below is still correct about anchors,
    it is simply no longer the binding constraint. See
    ``uniqueness.check_uniqueness``'s ``personal_pronoun`` branch.
    """
    outcome = _check(
        "pronomen_personal_dat",
        "Der Chef überreichte ihm seinen Bonus vor der ganzen Abteilung.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


# ==============================================================================
# Plural nouns: open class -- flagged with no cue, rescued with one.
# ==============================================================================


def test_nomen_plural_is_flagged_with_no_cue() -> None:
    """ "Lehrer" is spelled identically singular and plural -- a cue there
    would hand over the answer verbatim, so ``selectors._plural_noun_cue``
    withholds it, and the candidate stays genuinely unrescuable."""
    outcome = _check("nomen_plural", "Die Lehrer unterrichten Mathematik.")
    assert outcome.unique is False
    assert outcome.reason == "plural_noun_open_class"


def test_nomen_plural_is_flagged_when_the_dative_lemma_cannot_be_trusted() -> None:
    """A second no-cue case, distinct from the invariant-plural one above:
    ``de_core_news_sm`` mislemmatises a Dative plural of this noun class
    ("Müttern" -> "Müttern", entirely unreduced), so
    ``selectors._plural_noun_cue`` withholds the cue rather than trust a
    lemma it cannot verify -- see that function's own docstring."""
    outcome = _check("nomen_plural", "Sie dankten den Müttern für alles.")
    assert outcome.unique is False
    assert outcome.reason == "plural_noun_open_class"


def test_nomen_plural_is_rescued_by_its_own_cue() -> None:
    """docs/audits/cycle-04-report.md's own example: "meine ___" used to
    accept Zähne/Hände/Schuhe/Haare equally well. The selector now supplies
    "(Zahn)" -- the derived singular citation form -- naming exactly the
    noun the gap tests."""
    outcome = _check("nomen_plural", "Nach dem Frühstück putze ich gründlich meine Zähne.")
    assert outcome.unique is True
    assert outcome.reason is None


# ==============================================================================
# Determiners and adjective declension (docs/audits/cycle-07-report.md
# sections A and B): case/gender agreement with the governing noun/
# preposition forces a unique CELL, but not a unique LEXEME -- a cue names
# the family/base form, rescuing the candidate the same way it already
# rescues a modal or a plural noun.
# ==============================================================================


def test_determiner_is_rescued_by_its_own_cue() -> None:
    """docs/audits/cycle-07-report.md's own worked example: "meiner",
    "dieser", "jeder" all fit "Nach ___ Arbeit" just as grammatically as
    "der" does -- the case (Dativ, forced by "nach") and gender (feminine,
    forced by "Arbeit") do not pick the definite article out from its
    rivals. The cue ("die", the definite article's own Nominative feminine
    singular form) names the family; only the case-form inflection is left
    for the learner."""
    outcome = _check(
        "praepositionen_dativ", "Nach der Arbeit treffe ich oft meine Nachbarin im Park."
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_determiner_with_a_nominative_cell_is_rescued_by_its_invariant_cue() -> None:
    """``artikel_bestimmt_nom``'s own anchor (a relative clause identifying
    the referent) rules out the indefinite family but not a possessive or
    demonstrative one ("Der/Mein Hund, den ich gestern gekauft habe,
    schläft im Garten." are both grammatical). Before TODO.md 2.1/2.2
    (owner's decision, cycle 9), this candidate's own blanked cell already
    being Nominative meant no cue could exist without handing over the
    answer verbatim, so it stayed genuinely unrescuable. Under the
    invariant-citation cue ("der", always, regardless of the blanked
    cell), the cue always exists, and TODO.md 2.2 explicitly permits cue
    and answer to coincide for a determiner slot ("cue being the answer is
    not a problem if the problem still requires student to identify case,
    declension etc.") -- the family question ("Der" vs. "Mein"/"Ein"/
    "Kein") is exactly the thing this cue closes, so the candidate now
    passes."""
    outcome = _check(
        "artikel_bestimmt_nom", "Der Hund, den ich gestern gekauft habe, schläft im Garten."
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_artikel_unbestimmt_kein_nom_passes_both_cued_and_lexeme_anchored() -> None:
    """docs/audits/cycle-07-report.md section A's causal ``weil``-clause
    anchor already rules out every rival family unconditionally ("weil
    der/ein/mein Bus fährt" reads backwards as an explanation for
    lateness), and is still recorded on ``lexeme_anchored`` here (TODO.md
    2.3: kept, un-deleted, no longer required). Under the invariant-
    citation cue (TODO.md 2.1) this candidate ALSO now carries a cue
    ("kein") -- unlike before this cycle, when the cue mechanism did not
    yet cover this topic -- so it passes for either reason, both true at
    once here."""
    tagged = sentence_tagger.tag_sentence("Weil kein Bus fährt, kommen wir heute zu spät.")
    assert tagged is not None
    candidates = SELECTORS["artikel_unbestimmt_kein_nom"](tagged)
    assert len(candidates) == 1
    assert candidates[0].cue == "kein"
    assert candidates[0].lexeme_anchored is True
    outcome = check_uniqueness(tagged, candidates[0])
    assert outcome.unique is True
    assert outcome.reason is None


def test_adjective_declension_is_rescued_by_its_own_cue() -> None:
    """docs/audits/cycle-07-report.md's own worked example: "eine ___ Tasse
    Tee" admits warme/große/volle/heiße/frische alike -- the weak/mixed/
    strong ENDING is forced by the governing determiner, but the adjective
    itself is open class. The cue ("warm", the adjective's own uninflected
    positive base form) names the lexeme."""
    outcome = _check("adjektivdeklination_unbestimmt", "Auf dem Tisch steht eine warme Tasse Tee.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_adjektivdeklination_bestimmt_is_still_rescued_on_a_second_sentence() -> None:
    outcome = _check("adjektivdeklination_bestimmt", "Der alte Mann liest die neue Zeitung.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_attributive_comparative_is_never_a_candidate_at_all() -> None:
    """docs/audits/cycle-07-report.md section B: "bester" and "guter" share
    the lemma "gut", so a "(gut)" cue would not distinguish them -- rather
    than emit an unrescuable candidate, the selector excludes a
    non-positive-degree attributive adjective from candidacy entirely. That
    ambiguity belongs to ``adjektiv_komparativ_superlativ`` instead. The
    same sentence's OTHER attributive adjective ("gutes", genuinely
    Degree=Pos) is unaffected and still produces its own, cue-rescued
    candidate -- this pins that the exclusion is per-token, not a
    whole-sentence reject."""
    tagged = sentence_tagger.tag_sentence(
        "Mein bester Freund Timo hat mir gestern ein sehr gutes Buch geschenkt."
    )
    assert tagged is not None
    candidates = SELECTORS["adjektivdeklination_unbestimmt"](tagged)
    assert [c.token_index for c in candidates] == [9]
    assert candidates[0].cue == "gut"
    outcome = check_uniqueness(tagged, candidates[0])
    assert outcome.unique is True
    assert outcome.reason is None


def test_relative_pronoun_topics_are_never_flagged() -> None:
    outcome = _check(
        "relativsatz_nom_akk",
        "Wo hast du den Tennisschläger, den ich gestern in der Halle vergessen habe?",
    )
    assert outcome.unique is True
    assert outcome.reason is None
