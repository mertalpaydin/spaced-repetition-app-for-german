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


# -- Adjective declension after its determiner (cycle-04-report.md,
# "carrier validation misses two error classes") ---------------------------


def test_validate_carrier_rejects_the_pilot_viele_nassen_bug() -> None:
    """The exact real example from the pilot audit: "viele" is a plural
    quantifier, which takes STRONG adjective endings ("viele nasse
    Blätter"), not the weak/mixed "-en" of "nassen"."""
    result = cv.validate_carrier(
        "Auf dem Boden lagen viele nassen Blätter, die ich heute Morgen sofort wegfegte."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


def test_validate_carrier_accepts_the_corrected_viele_nasse_sentence() -> None:
    result = cv.validate_carrier(
        "Auf dem Boden lagen viele nasse Blätter, die ich heute Morgen sofort wegfegte."
    )
    assert result.accepted, result.reason


@pytest.mark.parametrize(
    "sentence",
    [
        "Mehrere neue Autos stehen dort.",
        "Einige junge Männer kamen spät.",
        "Wenige gute Freunde bleiben treu.",
    ],
)
def test_validate_carrier_accepts_correct_strong_ending_after_quantifier(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert result.accepted, result.reason


@pytest.mark.parametrize(
    "sentence",
    [
        "Mehrere neuen Autos stehen dort.",
        "Einige jungen Männer kamen spät.",
        "Wenige guten Freunde bleiben treu.",
    ],
)
def test_validate_carrier_rejects_weak_ending_after_quantifier(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


def test_validate_carrier_rejects_wrong_ending_after_definite_article() -> None:
    """Weak declension: a definite article forces "-e" here, not "-en"."""
    result = cv.validate_carrier("Der alten Mann geht spazieren.")
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


def test_validate_carrier_rejects_wrong_ending_after_indefinite_article() -> None:
    """Mixed declension: "ein" (Nom Neut Sing) forces "-es" here, not the
    weak/plural-shaped "-e"."""
    result = cv.validate_carrier("Ein kleine Kind spielt im Garten.")
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


def test_validate_carrier_rejects_wrong_ending_with_no_determiner_at_all() -> None:
    """Strong declension applies with zero article; "Kaffee" is masculine,
    so "-es" (neuter) is impossible under every reading."""
    result = cv.validate_carrier("Frisches Kaffee schmeckt gut.")
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


def test_validate_carrier_rejects_wrong_ending_after_kein() -> None:
    """ "kein" tags as PIAT in de_core_news_sm (not ART), but still takes
    the "ein"-family mixed declension, not the neuter "-es" tried here on a
    masculine noun."""
    result = cv.validate_carrier("Kein nettes Mensch würde das tun.")
    assert not result.accepted
    assert result.reason == cv.REASON_ADJECTIVE_DECLENSION_MISMATCH


@pytest.mark.parametrize(
    "sentence",
    [
        # "die": Fem Sing Nom/Acc, or Plur Nom/Acc (any gender) -- genuinely
        # ambiguous from the determiner alone, resolved here by the noun's
        # own Number, and correct under either resulting reading.
        "Die junge Frau lächelt freundlich.",
        "Ich sehe die junge Frau im Park.",
        "Die jungen Frauen lächeln freundlich.",
        "Ich sehe die jungen Frauen im Park.",
        # "der": Masc Sing Nom, Fem Sing Dat/Gen, or Gen Plur (any gender) --
        # every one of those readings is correct here; none may be flagged.
        "Der junge Mann lächelt freundlich.",
        "Ich helfe der jungen Frau im Park.",
        "Die Farbe der jungen Frau gefällt mir.",
        "Die Farben der jungen Blumen gefallen mir.",
    ],
)
def test_validate_carrier_does_not_false_accuse_genuinely_ambiguous_determiners(
    sentence: str,
) -> None:
    """The false-accusation trap the task warned about: only reject a
    combination impossible under EVERY reading, never one that merely looks
    odd under the first reading tried."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, f"{sentence!r}: {result.reason}"


def test_validate_carrier_skips_coordinated_second_adjective_without_misfiring() -> None:
    """ "kluge" attaches to "und", not to "Mann", as a coordinated conjunct
    ("cj") rather than a direct "nk" child -- a known, documented gap (the
    second conjunct in a coordinated adjective pair is not checked at all).
    Whether or not it is checked, this correct sentence must not be
    rejected."""
    result = cv.validate_carrier("Der alte und kluge Mann lächelt freundlich.")
    assert result.accepted, result.reason


# -- "dass" versus "das" (cycle-04-report.md, same section) -----------------


def test_validate_carrier_rejects_the_pilot_dass_das_bug() -> None:
    """The exact real example from the pilot audit: "dass" cannot fill a
    grammatical role in its own clause, but "gekauft" here has no
    accusative object at all -- the gap the relative pronoun "das" should
    have filled."""
    result = cv.validate_carrier(
        "Sie haben Ihren Mitreisenden ein Souvenir geschenkt, "
        "dass Sie auf dem Markt gekauft hatten."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_DASS_CLAUSE_MISSING_OBJECT


def test_validate_carrier_accepts_the_corrected_das_relative_clause() -> None:
    result = cv.validate_carrier(
        "Sie haben Ihren Mitreisenden ein Souvenir geschenkt, das Sie auf dem Markt gekauft hatten."
    )
    assert result.accepted, result.reason


def test_validate_carrier_accepts_dass_clause_with_kaufen_when_object_present() -> None:
    """Same verb as the bug, but with its accusative object actually
    present -- a completely ordinary, correct "dass" complement clause."""
    result = cv.validate_carrier("Ich glaube, dass sie das Auto gestern gekauft hat.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_dass_clause_with_schenken_when_object_present() -> None:
    result = cv.validate_carrier("Ich weiß, dass er ihr ein Buch schenkt.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_dass_clause_with_separable_prefix_einkaufen() -> None:
    """ "einkaufen" (separable "kaufen" + "ein") is a different verb with
    different valency -- it is normal for it to appear with no accusative
    object, and must not be confused with a transitivity gap in "kaufen"
    itself."""
    result = cv.validate_carrier("Sie sagt, dass sie samstags immer dort einkauft.")
    assert result.accepted, result.reason


def test_validate_carrier_accepts_ordinary_dass_clauses_with_intransitive_verbs() -> None:
    """Verbs outside the closed two-verb list are never checked for a
    missing object at all -- German drops objects freely, and this check is
    deliberately narrow rather than guessing at general verb valency."""
    for sentence in [
        "Ich glaube, dass er kommt.",
        "Ich weiß, dass du gern liest.",
    ]:
        result = cv.validate_carrier(sentence)
        assert result.accepted, f"{sentence!r}: {result.reason}"


# -- Swiss orthography ("heisse" for "heiße") --------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Ich heisse Anna.",
        "Er hiess früher anders.",
        "Wie heisst du?",
    ],
)
def test_validate_carrier_rejects_swiss_spelled_heissen(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_SWISS_SPELLING


def test_validate_carrier_accepts_standard_spelled_heissen() -> None:
    result = cv.validate_carrier("Ich heiße Anna.")
    assert result.accepted, result.reason


@pytest.mark.parametrize(
    "sentence",
    [
        "Ich glaube, dass er kommt.",
        "Das Auto ist neu.",
        "Er isst jeden Tag Obst.",
        "Das Wasser im Fluss ist kalt.",
    ],
)
def test_validate_carrier_does_not_flag_correct_short_vowel_ss_words(sentence: str) -> None:
    """ "dass", "isst", "Fluss" are all correct WITH "ss" (short vowel); the
    Swiss-spelling check is scoped to a closed list of "heißen" forms only
    and must never fire on these."""
    result = cv.validate_carrier(sentence)
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


# -- Regression: the new cycle-5 checks must not reject known-good German ---


def test_validate_carrier_has_zero_false_positives_on_the_mock_sentence_pool() -> None:
    """``sentence_source._MOCK_SENTENCE_POOL`` is a corpus of hand-written or
    hand-reviewed known-good German (every entry was individually confirmed
    sound before being added, per that module's own docstring). Anything
    this module rejects from it is a false positive -- the exact risk the
    task warned about for the new adjective-declension and dass/das checks.
    This is a regression test, not a one-off measurement: it must stay at
    zero as both this module and the pool evolve."""
    from src.generation.blanking.sentence_source import _MOCK_SENTENCE_POOL

    rejected = [
        (sentence, result.reason)
        for sentence in _MOCK_SENTENCE_POOL
        if not (result := cv.validate_carrier(sentence)).accepted
    ]
    assert rejected == []
