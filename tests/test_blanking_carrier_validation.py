"""Tests for src/generation/blanking/carrier_validation.py.

Skipped whole-file when spaCy's de_core_news_sm is not installed, mirroring
tests/test_blanking_pipeline.py -- every real check here needs the parser.
"""

import pytest
from src.generation.blanking import carrier_validation as cv

pytestmark = pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)


# -- Sound carriers: every check must accept these -------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Auf dem Weg kaufe ich im Supermarkt frisches Gemüse und Milch ein.",
        "Der Hund läuft schnell durch den Park.",
        "Die Kinder spielen im Garten.",
        "Du gehst heute ins Kino.",
        "Wir kaufen ein neues Auto.",
        "Sie gehen morgen schwimmen.",
        "Es regnet heute stark.",
        "Ich glaube, dass er kommt.",
        "Weil es regnet, bleibe ich zuhause.",
        "Das Buch, das ich lese, ist spannend.",
        "Ich habe das Buch gelesen.",
        "Er ist müde, weil er lange gearbeitet hat.",
        "Um Deutsch zu lernen, übe ich jeden Tag.",
        "Kommen Sie bitte her.",
        "Obwohl er müde war, arbeitete er weiter.",
        "Ich möchte ein Buch kaufen.",
        "Ich koche, aber du isst nicht.",
    ],
)
def test_validate_carrier_accepts_sound_german(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert result.accepted, f"expected accept, got reason={result.reason!r}"
    assert result.reason is None


def test_validate_carrier_accepts_second_person_plural_present_tense() -> None:
    """The exact false-positive path this module was built to avoid: German
    present-tense 2nd-plural and 3rd-singular endings are surface-identical
    ("ihr macht" / "er macht"), and de_core_news_sm's morphologizer resolves
    that ambiguity toward 3rd-singular regardless of the real subject. A
    naive Person/Number comparison would reject every one of these, which
    would gut the pool's 2nd-person-plural coverage exactly as task 2
    describes for 1st-person-singular overrepresentation."""
    for sentence in [
        "Ihr geht morgen schwimmen.",
        "Ihr macht das gut.",
        "Ihr esst zu wenig Gemüse.",
        "Ihr kauft ein neues Auto.",
        "Ihr spielt im Garten.",
    ]:
        result = cv.validate_carrier(sentence)
        assert result.accepted, f"{sentence!r}: {result.reason}"


def test_validate_carrier_accepts_second_person_plural_irregular_forms() -> None:
    """ "haben"/"sein" are NOT surface-syncretic between 2nd-plural and
    3rd-singular ("ihr habt" vs "er hat", "ihr seid" vs "er ist"), so these
    should tag correctly and agree without needing the syncretism tolerance
    at all -- included to pin that the tolerance is not doing silent,
    unnecessary work here."""
    for sentence in ["Ihr habt viel Zeit.", "Ihr seid sehr nett."]:
        result = cv.validate_carrier(sentence)
        assert result.accepted, f"{sentence!r}: {result.reason}"


# -- The motivating bug: subject-verb disagreement --------------------------


def test_validate_carrier_rejects_the_pilot_kauft_ich_bug() -> None:
    """The exact real example from the pilot audit: "kauft" is 3rd-singular,
    "ich" is 1st-singular. This is the carrier defect this whole module
    exists to catch."""
    result = cv.validate_carrier(
        "Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch ein."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_SUBJECT_VERB_DISAGREEMENT


def test_validate_carrier_rejects_plural_subject_singular_verb() -> None:
    result = cv.validate_carrier("Die Kinder spielt im Garten.")
    assert not result.accepted
    assert result.reason == cv.REASON_SUBJECT_VERB_DISAGREEMENT


def test_validate_carrier_rejects_singular_subject_plural_verb() -> None:
    result = cv.validate_carrier("Der Hund laufen schnell durch den Park.")
    assert not result.accepted
    assert result.reason == cv.REASON_SUBJECT_VERB_DISAGREEMENT


def test_validate_carrier_word_order_does_not_fool_agreement_check() -> None:
    """German verb-second word order puts the subject after the verb in a
    subordinate clause; the check must read the dependency arc, not linear
    position, or it would mis-check (or fail to check) exactly the sentence
    shapes complex-structure topics need."""
    good = cv.validate_carrier("Weil ich müde bin, gehe ich früh schlafen.")
    assert good.accepted, good.reason
    bad = cv.validate_carrier("Weil ich müde bist, gehe ich früh schlafen.")
    assert not bad.accepted


# -- Finite verb count / clause structure -----------------------------------


def test_validate_carrier_rejects_a_sentence_with_no_finite_verb() -> None:
    result = cv.validate_carrier("Der Mann im Park.")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_FINITE_VERB


def test_validate_carrier_rejects_two_sentences_run_together() -> None:
    """The generator is asked for one plain sentence per list entry; if a
    call returns two sentences squashed into one string, that is not a
    single clean carrier."""
    result = cv.validate_carrier("Ich gehe nach Hause. Dann esse ich etwas.")
    assert not result.accepted
    assert result.reason == cv.REASON_MULTIPLE_SENTENCES


def test_validate_carrier_accepts_subject_gapped_coordination() -> None:
    """ "Ein Mann steht vor der Tür und wartet." has exactly ONE subject
    ("Mann"), shared by both coordinated verbs -- correct, common German,
    and the second conjunct must not be rejected for "having no subject"."""
    result = cv.validate_carrier("Ein Mann steht vor der Tür und wartet.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_indefinite_pronoun_subject() -> None:
    result = cv.validate_carrier("Während der Prüfung darf man nicht sprechen.")
    assert result.accepted, result.reason


def test_validate_carrier_rejects_a_run_on_missing_conjunction() -> None:
    """Two finite verbs, one attached directly to the other, with no
    coordinating or subordinating connector between them -- indistinguishable
    from a missing "und"/"weil"/"dass"."""
    result = cv.validate_carrier("Ich gehe, ich komme.")
    assert not result.accepted
    assert result.reason == cv.REASON_MISSING_CLAUSE_CONNECTOR


def test_validate_carrier_accepts_coordinated_main_clauses() -> None:
    result = cv.validate_carrier("Ich koche und du isst nicht.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_a_relative_clause() -> None:
    result = cv.validate_carrier("Der Mann, der dort steht, ist mein Vater.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_a_dass_complement_clause() -> None:
    result = cv.validate_carrier("Ich weiß nicht, ob er kommt.")
    assert result.accepted, result.reason


# -- Sentence completeness --------------------------------------------------


def test_validate_carrier_rejects_lowercase_start() -> None:
    result = cv.validate_carrier("der Hund läuft schnell.")
    assert not result.accepted
    assert result.reason == cv.REASON_NOT_CAPITALIZED


def test_validate_carrier_rejects_missing_terminal_punctuation() -> None:
    result = cv.validate_carrier("Der Hund läuft schnell durch den Park")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_TERMINAL_PUNCTUATION


def test_validate_carrier_rejects_a_dangling_fragment() -> None:
    result = cv.validate_carrier("Ich gehe heute in die")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_TERMINAL_PUNCTUATION


def test_validate_carrier_rejects_a_dangling_fragment_with_punctuation() -> None:
    """Terminal punctuation alone is not enough: a sentence that gets cut off
    right after a dangling conjunction is still a fragment even though it
    technically ends on a period."""
    result = cv.validate_carrier("Ich bleibe zuhause, weil.")
    assert not result.accepted
    assert result.reason == cv.REASON_DANGLING_FRAGMENT


def test_validate_carrier_rejects_a_sentence_too_short_to_be_a_sentence() -> None:
    result = cv.validate_carrier("Kurzer Satz.")
    assert not result.accepted
    assert result.reason == cv.REASON_FRAGMENT_TOO_SHORT


def test_validate_carrier_rejects_empty_string() -> None:
    result = cv.validate_carrier("")
    assert not result.accepted
    assert result.reason == cv.REASON_EMPTY_SENTENCE


def test_validate_carrier_rejects_whitespace_only() -> None:
    result = cv.validate_carrier("   \n\t  ")
    assert not result.accepted
    assert result.reason == cv.REASON_EMPTY_SENTENCE


# -- Documented conservative gaps -- discarding good sentences on purpose --


def test_validate_carrier_rejects_coordinated_subject_as_undecidable() -> None:
    """ "Der Mann und die Frau tanzen" is correct German (plural agreement
    with a coordinated subject); this module deliberately does not implement
    German's coordinate-subject person-resolution rule and rejects rather
    than guesses. See the module docstring."""
    result = cv.validate_carrier("Der Mann und die Frau tanzen zusammen.")
    assert not result.accepted
    assert result.reason == cv.REASON_AGREEMENT_UNDECIDABLE


def test_validate_carrier_rejects_subjectless_dative_experiencer() -> None:
    """ "Mir ist kalt" is correct, idiomatic German with no nominative or
    expletive subject at all; this module has nothing to check agreement
    against and conservatively rejects rather than assumes correctness."""
    result = cv.validate_carrier("Mir ist kalt.")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_SUBJECT_FOUND


def test_validate_carrier_rejects_bare_informal_imperative() -> None:
    """ "Geh nach Hause!" is correct German, but de_core_news_sm systematically
    mistags a subjectless second-person imperative as a noun rather than a
    finite verb, so it never reaches the agreement check at all. Formal
    imperatives with an explicit subject are unaffected (see the next
    test)."""
    result = cv.validate_carrier("Geh nach Hause!")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_FINITE_VERB


def test_validate_carrier_accepts_formal_imperative_with_explicit_subject() -> None:
    result = cv.validate_carrier("Kommen Sie bitte her.")
    assert result.accepted, result.reason


# -- Batch summary ------------------------------------------------------


def test_validate_carriers_splits_accepted_and_counts_rejections_by_reason() -> None:
    sentences = [
        "Der Hund läuft schnell durch den Park.",
        "Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch ein.",
        "der Hund läuft schnell.",
        "Die Kinder spielen im Garten.",
        "Der Mann im Park.",
    ]
    summary = cv.validate_carriers(sentences)
    assert summary.total == 5
    assert summary.accepted == [
        "Der Hund läuft schnell durch den Park.",
        "Die Kinder spielen im Garten.",
    ]
    assert summary.accepted_count == 2
    assert summary.rejected_count == 3
    assert summary.rejected_by_reason == {
        cv.REASON_SUBJECT_VERB_DISAGREEMENT: 1,
        cv.REASON_NOT_CAPITALIZED: 1,
        cv.REASON_NO_FINITE_VERB: 1,
    }


def test_validate_carriers_handles_an_empty_batch() -> None:
    summary = cv.validate_carriers([])
    assert summary.total == 0
    assert summary.accepted == []
    assert summary.rejected_by_reason == {}


def test_validate_carrier_never_raises_on_odd_input() -> None:
    for sentence in ["...", "1234", "Ä", "\x00\x01", "!" * 50]:
        result = cv.validate_carrier(sentence)
        assert isinstance(result.accepted, bool)


def test_analysis_available_reports_true_when_spacy_is_installed() -> None:
    # This module's own file-level skipif already gates the whole file on
    # this being true; asserting it directly documents the contract rather
    # than only relying on the skip.
    assert cv.analysis_available() is True
