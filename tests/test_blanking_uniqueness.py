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
    other member of an "auxiliary class" that could stand in this slot."""
    outcome = _check("verb_sein_haben", "Ich bin heute sehr müde.")
    assert outcome.unique is True
    assert outcome.reason is None


# ==============================================================================
# Personal pronouns: Nominative trusted unconditionally (verb agreement is
# the anchor); Accusative/Dative need a carrier-supplied anchor or are
# flagged.
# ==============================================================================


def test_pronomen_personal_nom_is_never_flagged_verb_agreement_is_the_anchor() -> None:
    outcome = _check("pronomen_personal_nom", "Ich sehe den Mann auf der anderen Straßenseite.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_pronomen_personal_dat_is_flagged_with_no_anchor() -> None:
    """docs/audits/cycle-04-report.md's own example: mir/ihm/ihr/uns/ihnen
    would all fit here just as well as "Ihnen"."""
    outcome = _check(
        "pronomen_personal_dat",
        "Das Restaurant hatte einen neuen Koch eingestellt, und das Essen schmeckte Ihnen "
        "ausgezeichnet.",
    )
    assert outcome.unique is False
    assert outcome.reason == "personal_pronoun_unanchored"


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


def test_pronomen_personal_akk_is_flagged_with_no_anchor() -> None:
    outcome = _check("pronomen_personal_akk", "Ich sehe ihn jeden Tag.")
    assert outcome.unique is False
    assert outcome.reason == "personal_pronoun_unanchored"


def test_pronomen_personal_akk_passes_when_a_matching_pronoun_anchors_it() -> None:
    outcome = _check(
        "pronomen_personal_akk",
        "Wir laden ihn herzlich ein, weil er unser bester Freund ist.",
    )
    assert outcome.unique is True
    assert outcome.reason is None


def test_ambiguous_stems_sein_and_ihr_are_not_trusted_as_anchors() -> None:
    """ "seinen" (his/its, syncretic between a masc./neut. possessor) must
    not be treated as an anchor even though it agrees in person on one
    reading -- a wrong anchor here is the exact failure mode this gate
    exists to prevent (module docstring)."""
    outcome = _check(
        "pronomen_personal_dat",
        "Der Chef überreichte ihm seinen Bonus vor der ganzen Abteilung.",
    )
    assert outcome.unique is False
    assert outcome.reason == "personal_pronoun_unanchored"


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
# Determiners and adjective declension: case/gender agreement with the
# governing noun/preposition already forces a unique cell, so these must
# pass unaffected -- the asymmetry the task that specified this module named
# explicitly.
# ==============================================================================


def test_determiner_topics_are_never_flagged() -> None:
    outcome = _check("artikel_bestimmt_nom", "Der Hund läuft schnell durch den Park.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_adjective_declension_topics_are_never_flagged() -> None:
    outcome = _check("adjektivdeklination_bestimmt", "Der alte Mann liest die neue Zeitung.")
    assert outcome.unique is True
    assert outcome.reason is None


def test_relative_pronoun_topics_are_never_flagged() -> None:
    outcome = _check(
        "relativsatz_nom_akk",
        "Wo hast du den Tennisschläger, den ich gestern in der Halle vergessen habe?",
    )
    assert outcome.unique is True
    assert outcome.reason is None
