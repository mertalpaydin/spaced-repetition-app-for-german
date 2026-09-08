"""Tests for src/generation/blanking/carrier_validation.py.

Skipped whole-file when spaCy's de_core_news_sm is not installed, mirroring
tests/test_blanking_pipeline.py -- every real check here needs the parser.
"""

import json
from pathlib import Path

import pytest
from src.phrases import carrier_validation as cv

pytestmark = pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)

_KNOWN_BAD_CARRIERS_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "fixtures"
    / "carrier_validation"
    / "known_bad_carriers.jsonl"
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


def test_validate_carrier_accepts_all_third_person_coordinated_subject() -> None:
    """CLAUDE.md rule 7: this replaces (does not weaken) a prior test that
    pinned coordinated subjects as ALWAYS undecidable. The carrier-
    validation audit (2026-08-20) found that blanket rule was itself the
    false positive: when every conjunct of an "und"-coordinated subject is
    grammatically 3rd person (the overwhelming common case -- "der Mann und
    die Frau", "Tom und Maria", "Polizei und Staatsanwaltschaft" -- none of
    them "ich"/"du"/"wir"/"ihr"), German has no genuine ambiguity to
    resolve: two or more distinct 3rd-person entities joined by "und" are
    always 3rd-person-PLURAL, categorically. "Der Mann und die Frau tanzen
    zusammen." is correct German and is now correctly accepted rather than
    discarded as undecidable."""
    result = cv.validate_carrier("Der Mann und die Frau tanzen zusammen.")
    assert result.accepted, result.reason


def test_validate_carrier_catches_disagreement_on_coordinated_subject() -> None:
    """The flip side of the fix above, and why it is a strict improvement
    rather than merely a loosening: because the coordinated subject's
    resolved Person/Number still goes through the ordinary comparison, a
    genuine number defect on a coordinated subject is now actively CAUGHT,
    where before it was silently waved through as undecidable and
    discarded either way."""
    result = cv.validate_carrier("Der Mann und die Frau tanzt zusammen.")
    assert not result.accepted
    assert result.reason == cv.REASON_SUBJECT_VERB_DISAGREEMENT


def test_validate_carrier_still_treats_mixed_person_coordination_as_undecidable() -> None:
    """The one case the fix above deliberately still declines to resolve:
    a coordinated subject with a genuine 1st- or 2nd-person conjunct ("du
    und ich" -> "wir"-agreement) needs German's real coordinate-subject
    person-resolution rule, which this module still does not implement --
    see the module docstring. Guessing here risks the opposite mistake
    (assuming 3rd person when a real defect swapped the wrong pronoun in),
    so this stays undecidable on purpose."""
    result = cv.validate_carrier("Du und ich gehen jetzt nach Hause.")
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
    Swiss-spelling check is scoped to a closed list of "heißen" (and, as of
    cycle 6, "groß") forms only and must never fire on these."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, result.reason


# -- Swiss orthography, cycle 6: "gross" for "groß" --------------------------
#
# The dictionary-based general rule the cycle-6 report proposed was verified
# against the actual vendored file and found unable to support it (see the
# module docstring, section 7): every entry was written through
# ``normalise``, which maps ß to ss, so the file has zero ß characters at
# all and cannot tell "grossen" and "großen" apart. These tests pin the
# closed-list fallback that was shipped instead.


@pytest.mark.parametrize(
    "sentence",
    [
        "In der grossen Pause hatte unser Klassenlehrer mir den Schlüssel gegeben.",
        "Vor der grossen Prüfung hatte mein bester Freund mir seine Notizen gezeigt.",
        "Das ist ein grosses Problem.",
        "Er ist ein grosser Mann.",
    ],
)
def test_validate_carrier_rejects_swiss_spelled_gross(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_SWISS_SPELLING


def test_validate_carrier_accepts_standard_spelled_gross() -> None:
    result = cv.validate_carrier(
        "In der großen Pause hatte unser Klassenlehrer mir den Schlüssel gegeben."
    )
    assert result.accepted, result.reason


def test_validate_carrier_does_not_flag_the_surname_gross() -> None:
    """The "gross" pattern is matched case-sensitively, lowercase only, so
    that a capitalised "Gross" -- a real German surname -- is never mistaken
    for the adjective. See module docstring section 7."""
    result = cv.validate_carrier("Frau Gross wohnt seit zehn Jahren in dieser Straße.")
    assert result.accepted, result.reason


# -- Swiss orthography, cycle 9 task 1.4: the general diphthong rule ---------
#
# docs/audits/cycle-09-report.md 1.4: the closed-list approach above caught
# the class exactly once in a full pilot run. These tests pin the general
# rule (module docstring section 11): a diphthong ("ei"/"eu"/"äu"/"ie")
# immediately followed by "ss" is always Swiss, plus a closed list for the
# long-vowel-single-LETTER words ("groß" already had, extended here) and
# for "au" (kept a closed list on purpose -- see the module docstring for
# why a general "au" rule would reject "aussteigen"/"ausschließlich" and
# dozens more genuinely standard words).


@pytest.mark.parametrize(
    "sentence",
    [
        "Schliesslich war es doch noch möglich.",
        "Die Kinder spielen draussen im Garten.",
        "Es heisst, dass er krank ist.",
        "Der Schnee war ganz weiss.",
        "Wir gingen die Strasse entlang.",
        "Er hat einen verletzten Fuss.",
        "Das ist das falsche Mass für diese Aufgabe.",
        "Das war ein grosser Spass für alle.",
    ],
)
def test_validate_carrier_rejects_swiss_spelled_diphthong_and_long_vowel_words(
    sentence: str,
) -> None:
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_SWISS_SPELLING


@pytest.mark.parametrize(
    "sentence",
    [
        "Ich muss jetzt nach Hause gehen.",
        "Sie musste gestern lange arbeiten.",
        "Der Fluss ist heute sehr breit.",
        "Er gab mir gestern einen Kuss.",
        "Ich weiß die Antwort nicht genau.",
        "Wir essen heute Abend zusammen.",
        "Bitte lassen Sie mich in Ruhe.",
    ],
)
def test_validate_carrier_does_not_flag_correct_short_vowel_words_general_rule(
    sentence: str,
) -> None:
    """The general diphthong rule and its long-vowel closed-list siblings
    must never fire on any of these -- none contains a diphthong or a
    listed long-vowel stem before "ss"."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, result.reason


def test_validate_carrier_does_not_flag_diesseits_compound_boundary() -> None:
    """ "diesseits" ("dies" + "seits") contains "ie" immediately followed by
    "ss" but is standard German, never spelled with "ß" -- a genuine
    compound-boundary "ss", not a diphthong-plus-eszett word. Verified
    against the vendored dictionary and excluded by name (module docstring
    section 11)."""
    result = cv.validate_carrier("Diesseits des Flusses liegt das kleine Dorf.")
    assert result.accepted, result.reason


def test_validate_carrier_does_not_flag_the_surname_weiss() -> None:
    """ "weiss" is matched case-sensitively, lowercase only, for the same
    surname-protection reason "gross" already is -- "Weiss" is an attested
    German surname."""
    result = cv.validate_carrier("Herr Weiss kommt heute Nachmittag vorbei.")
    assert result.accepted, result.reason


def test_validate_carrier_does_not_flag_masse_the_standard_word() -> None:
    """ "Masse"/"Massen" ("mass, crowd") is itself standard German, unrelated
    in meaning to "Maß" -- the long-vowel closed list is deliberately
    bounded to the bare word "mass" so it never collides with this one
    (module docstring section 11's own recorded cost)."""
    result = cv.validate_carrier("Eine große Menschenmasse strömte auf den Platz.")
    assert result.accepted, result.reason


# -- "dass" after a physical-action matrix verb, cycle 6 ---------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "Sie laden das Betriebssystem herunter, dass Ihr Computer wieder sicher ist.",
        "Wir hatten der netten Dame geholfen, dass sie ihren Koffer schnell fand.",
        "Sie kaufen ein neues Smartphone, dass Ihre alte Technik zu langsam ist.",
    ],
)
def test_validate_carrier_rejects_dass_after_a_physical_action_matrix_verb(sentence: str) -> None:
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_DASS_AFTER_PHYSICAL_ACTION_VERB


@pytest.mark.parametrize(
    "sentence",
    [
        "Sie haben der Reiseleitung eine Nachricht geschickt, "
        "dass der Bus pünktlich angekommen ist.",
        "Wir schicken dem Hotelier eine Nachricht, dass wir am Abend ankommen.",
    ],
)
def test_validate_carrier_accepts_a_genuine_content_clause_after_schicken(sentence: str) -> None:
    """ "schicken" looks superficially like the disallowed physical-action
    verbs (it also transfers an object), but "eine Nachricht schicken,
    dass ..." is a genuine communication act and must not be flagged --
    the blocklist is deliberately closed and does not include it."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, result.reason


def test_validate_carrier_does_not_flag_a_plain_laden_without_herunter() -> None:
    """Only the audited "herunterladen" reading is in scope; plain "laden"
    is a different verb and must not be caught by accident."""
    result = cv.validate_carrier("Ich weiß, dass er den Wagen lädt.")
    assert result.accepted, result.reason


def test_validate_carrier_does_not_catch_the_extraposed_es_waere_shape() -> None:
    """Documented, deliberate gap (module docstring section 8): an
    extraposed "es" subject with a predicate adjective ("hilfreich") is a
    different construction from a matrix VERB, and this check does not walk
    into it. The real defect here needs "wenn", not "dass"."""
    result = cv.validate_carrier(
        "Es wäre sehr hilfreich, dass Sie dem Reiseleiter Ihre Wünsche mitteilen."
    )
    assert result.accepted, result.reason


# -- Finite-verb lexical reality, cycle 6 -------------------------------------


def test_validate_carrier_rejects_a_hallucinated_first_person_singular_verb() -> None:
    """ "musse" is not a real German verb (the real modal is "müssen", with
    an umlaut the dictionary does not lose -- unlike the ß/ss case, this is
    a genuine absence, not a normalisation collision)."""
    result = cv.validate_carrier("Ich musse das unbedingt heute noch erledigen.")
    assert not result.accepted
    assert result.reason == cv.REASON_FINITE_VERB_NOT_A_REAL_WORD


def test_validate_carrier_treue_hole_remains_a_documented_gap() -> None:
    """Pins the exact boundary documented in the module docstring (section
    9): this sentence is NOT sound German ("treue" should be "treffe"), and
    it is NOT caught, because "treuen" already exists in the vendored
    dictionary as a real inflection of the adjective "treu", so section
    10's lexical-reality check cannot distinguish it from a real verb
    lemma without part-of-speech information the dictionary does not
    carry. This test exists so a future change that silently starts (or
    stops) catching this sentence is noticed and the docstring is
    revisited, not so this sentence is endorsed as correct German."""
    result = cv.validate_carrier(
        "Nach der Arbeit treue ich mich mit Lisa auf einen Kaffee in der Stadt."
    )
    assert result.accepted, (
        "if this now fails, section 10 has changed behaviour -- update the "
        "module docstring's section 9 accordingly rather than only this test"
    )


def test_validate_carrier_accepts_separable_prefix_first_person_singular() -> None:
    """The lexical-reality check must re-attach a separable prefix before
    checking the dictionary ("stehe...auf" -> "aufstehen"), not just check
    the bare stem."""
    result = cv.validate_carrier("Morgens stehe ich meistens um sechs Uhr auf.")
    assert result.accepted, result.reason


def test_validate_carrier_does_not_check_modal_or_auxiliary_verbs_for_lexical_reality() -> None:
    """Modals and auxiliaries have irregular 1st-singular-present forms
    ("bin", "habe", "kann", ...) not covered by the weak-verb "-e" rule;
    the check is scoped to VVFIN/VERB only and must never touch these."""
    for sentence in ["Ich habe Hunger.", "Ich bin müde.", "Ich kann gut kochen."]:
        result = cv.validate_carrier(sentence)
        assert result.accepted, f"{sentence!r}: {result.reason}"


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


# -- cycle-07 defect 6: a non-finite verb form must be a real German word ---


def test_validate_carrier_rejects_the_pilot_heilgemacht_bug() -> None:
    """docs/audits/cycle-07-report.md defect 6: the sentence's OWN blanked
    slot ("ist ... repariert", Zustandspassiv) is genuinely correct German;
    the fault is elsewhere in the same carrier, the participle
    "heilgemacht" ("heil" + "gemacht" fused as if it were a separable
    verb), which is not standard German. This must be rejected as a
    carrier before it ever reaches the blanking stage, regardless of which
    token would be blanked."""
    result = cv.validate_carrier(
        "Der Computer ist jetzt wieder repariert, weil der Hausmeister ihn gestern "
        "schnell heilgemacht hat."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_CONTENT_WORD_NOT_A_REAL_WORD


def test_validate_carrier_rejects_the_mislemmatised_einpacksen_participle() -> None:
    """Same mechanism as the mislemmatised-verb cue checks in
    ``selectors.py`` (``mussen``, ``packsen``), but at participle level:
    confirmed no real dictionary entry and no valid prefix-stripped
    reading exists, so this must stay rejected."""
    result = cv.validate_carrier("Er hat die Koffer schon einpacksen.")
    assert not result.accepted
    assert result.reason == cv.REASON_CONTENT_WORD_NOT_A_REAL_WORD


def test_validate_carrier_accepts_a_genuine_noun_compound_absent_from_the_wordlist() -> None:
    """ "Radweg" (bike path) is not a direct dictionary entry but resolves
    as a real compound ("Rad" + "Weg", both real words); confirms the new
    non-finite-verb check does not touch noun validation at all, so this
    kind of legitimate compound stays accepted exactly as before."""
    result = cv.validate_carrier("Der Radweg ist heute gesperrt.")
    assert result.accepted, result.reason


# -- Colon-joined fragments: cycle-11 corpus report section 4 -------------
#
# Four of the six carrier defects that audit found share one shape: a colon
# joining a fragment that is not a sentence (a caption glued onto a
# headline, a page heading glued onto body text, a trailing quoted stub, a
# headline glued onto its subheading). Each is answerable and each answer
# is correct, so these are carrier-quality defects, not wrong-answer
# defects -- carrier_validation's job either way.


def test_validate_carrier_rejects_colon_glued_photo_caption() -> None:
    result = cv.validate_carrier(
        "Ein lachendes Mädchen auf einem Sofa (Symbolbild): Die Möbelhauskette "
        "Møbelkompagniet eröffnet ihre erste Filiale in Deutschland."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_COLON_JOINED_FRAGMENT


def test_validate_carrier_rejects_colon_glued_page_heading() -> None:
    result = cv.validate_carrier(
        "Mobilitätslösungen: Während Ihres Werkstattaufenthalts stellen wir "
        "Ihnen kostenlose Mobilitätslösungen zur Verfügung."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_COLON_JOINED_FRAGMENT


def test_validate_carrier_rejects_colon_glued_quoted_stub() -> None:
    result = cv.validate_carrier(
        "Der letzte Eintrag wurde in der vergangenen Saison gemacht: „2025 “."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_COLON_JOINED_FRAGMENT


def test_validate_carrier_rejects_colon_glued_headline_and_subheading() -> None:
    result = cv.validate_carrier(
        "Auch beim Fahrrad ist der Bremsweg länger als gedacht: Reaktionstest "
        "bei der Verkehrswacht."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_COLON_JOINED_FRAGMENT


@pytest.mark.parametrize(
    "sentence",
    [
        # The only accepted zustandspassiv item in the cycle 11 run.
        "Aber: Das Thema ist damit nicht beendet, sondern geht jetzt erst richtig los!",
        # An elliptical predicate complement after the colon.
        "Der Himmel Cuscos ist wie seine Frauen: völlig unberechenbar!",
        # A colon introducing the alternatives an earlier clause asked about.
        "Tom weiß nicht, wem er glauben soll: Johannes oder Maria.",
        "Ich weiß nicht so recht, wem ich das Geschenk geben soll: dem Mädchen oder dem Jungen.",
        # A determiner-less nominal AFTER the colon is ordinary elaboration.
        "Für fünftausend Forint habe ich den Kindern Vögel gekauft: Kanarienvögel und Buchfinken.",
        "Uns fehlt nur eine Kleinigkeit, um so frei zu sein, wie die Vögel sind: nur Zeit.",
        # A determined noun phrase, before or after the colon.
        "Für das Können gibt es nur einen Beweis: das Tun.",
        "Der kleine Unterschied: Er denkt beim Lieben, sie liebt beim Denken.",
        "Eine Ansage an alle Klassen: Der Unterricht fällt heute ab der dritten Stunde aus.",
        "Er glaubt, von allen der Begabteste zu sein, ist es aber nicht: "
        "ein typischer Fall von chronischer Selbstüberschätzung.",
        # A prepositional phrase whose head noun does carry a determiner.
        "Doch still, mich dünkt, ich wittre Morgenluft: kurz lass mich sein.",
        # Quoted speech introduced by a colon.
        "Als ich sie fragte, ob sie von der langen Wanderung müde war, sagte sie: "
        '"irgendwie schon".',
    ],
)
def test_validate_carrier_accepts_ordinary_german_colon_sentences(sentence: str) -> None:
    """A colon is not itself a defect. The first version of this check
    required every colon-delimited segment to be a clause; measured over
    6,000 Tatoeba lines it rejected 12, and hand-checking every one found 11
    to be ordinary correct German using a colon exactly as German uses it,
    to introduce an elaboration of a clause that is already complete. Those
    eleven, plus the "Aber:" sentence the original rule had to special-case,
    are pinned here: a filter for scraped-web junk must not buy its
    precision with correct German. See
    ``_colon_joined_fragment_reason``'s own docstring.

    Asserts on the colon reason specifically, not on overall acceptance:
    several of these are independently rejected by older checks
    (``missing_clause_connector``, ``no_subject_found``) for reasons that
    have nothing to do with the colon, and pinning overall acceptance would
    make this test fail whenever one of those unrelated checks changes."""
    result = cv.validate_carrier(sentence)
    assert result.reason != cv.REASON_COLON_JOINED_FRAGMENT


def test_validate_carrier_rejects_a_parenthesised_stock_photo_marker_without_a_colon() -> None:
    """The stock-photo marker is decisive on its own, colon or not: no
    German sentence says "(Archivbild)". Found in the same Leipzig sweep as
    the four colon shapes above."""
    result = cv.validate_carrier(
        "Seit dem 14. Februar liegt der Papst im Krankenhaus (Archivbild)."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_COLON_JOINED_FRAGMENT


def test_validate_carrier_does_not_flag_an_ordinary_colon_free_sentence() -> None:
    """No colon at all means ``_colon_joined_fragment_reason`` has nothing to
    split on and must not touch the verdict."""
    result = cv.validate_carrier("Der Zug fährt jeden Morgen pünktlich ab.")
    assert result.accepted, result.reason


# -- Cycle 13: a carrier that opens inside a quotation ---------------------
#
# Module docstring section 13. The cycle 13 pilot accepted eight items the
# verifier had correctly rejected the run before; this is one of the three
# with a mechanical shape. Measured over the real staged corpora (seed 7,
# 40,000 lines per source): 0 Tatoeba rejections, 378 Leipzig. Every GOOD
# carrier pinned below is a real Tatoeba line from that same sample, not an
# invented one.


def test_validate_carrier_rejects_a_carrier_that_opens_inside_a_quotation() -> None:
    """The pilot carrier itself. The verifier's own words last run: "Am
    Satzanfang fehlt das öffnende Anführungszeichen"."""
    result = cv.validate_carrier(
        'Ja", gesteht Norris, der aber auch betont, dass das eben ein "Risiko" '
        "mit sich gebracht hätte."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_OPENS_MID_QUOTATION


@pytest.mark.parametrize(
    "sentence",
    [
        # A quotation opened with a straight '"' at position 0 and closed
        # with the same character mid-sentence -- the exact shape a naive
        # "is there a closing quote before an opening one" test gets wrong.
        '"Gut, in Ordnung", stimmte Willie endlich zu.',
        # Balanced straight quotes around a title, mid-sentence.
        'Die Dinosaurier in dem Film "Jurassic Park" waren lebensecht.',
        # German low-9 opening quote, sentence-initial.
        "„Würde“ ist die konditionale Form von dem, was einer ist.",
        # German low-9 opening quote, mid-sentence.
        "Slowenien heißt auf Slowenisch „Slowenija“.",
        # Guillemets, which German uses opening-first ("»...«").
        "Nach Duden soll man »heute Morgen« schreiben, auch wenn das wohl "
        "reichlich bescheuert sein dürfte.",
        # Mixed conventions: German „ opening, English ” closing. The rule
        # must read the FIRST mark, which is unambiguously an opener.
        "Diese Zeitung wurde von westeuropäischen Deutschen wegen ihrer "
        "„schlampigen, vereinfachten und russifizierten Sprache” scharf kritisiert.",
    ],
)
def test_validate_carrier_accepts_real_tatoeba_quotation_sentences(sentence: str) -> None:
    """Six real Tatoeba lines carrying quotation marks, all correct German.
    Asserts overall acceptance, since none of them trips any other check
    either."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, f"expected accept, got reason={result.reason!r}"


def test_validate_carrier_does_not_count_quote_parity() -> None:
    """The measured counterexample to the weaker variant of this rule.
    Counting straight '"' characters and rejecting an odd total rejects this
    real Tatoeba line, which is correct German that simply mixes the German
    opening mark with the straight closing one. This is why
    ``_opens_mid_quotation`` reads the first mark's typographic ROLE instead
    of counting -- see module docstring section 13."""
    result = cv.validate_carrier('„Komm auf die Erde zurück!", flüsterte sie ihm ins Ohr.')
    assert result.accepted, result.reason


def test_validate_carrier_does_not_read_an_apostrophe_as_a_quotation_mark() -> None:
    """German writes the apostrophe with the same glyph as a single quote,
    so the single-quote characters are excluded from ``_DOUBLE_QUOTE_CHARS``
    entirely. Real Tatoeba line."""
    result = cv.validate_carrier("Mach' es so, wie man es dir sagte.")
    assert result.reason != cv.REASON_OPENS_MID_QUOTATION


def test_validate_carrier_leaves_a_trailing_unclosed_quotation_alone() -> None:
    """The deliberate other half of the decision, pinned so a later
    widening has to argue with it: a sentence that OPENS a quotation and
    never closes it (the closing mark fell on a later sentence in the
    source) is grammatically complete German, so it is left accepted.
    Rejecting this shape as well costs 645 more Leipzig lines and buys no
    correctness -- module docstring section 13."""
    result = cv.validate_carrier("«Es gab überall Kellner in der Villa.")
    assert result.reason != cv.REASON_OPENS_MID_QUOTATION


# -- Cycle 13: a headline with no main clause ------------------------------
#
# Module docstring section 14, and the one TODO.md section 1 had already
# recorded as a cycle 12 defect, so it had been caught twice. Measured over
# the real staged corpora (seed 7, 40,000 lines per source): 2 Tatoeba
# rejections, 71 Leipzig. One of the two Tatoeba lines is already rejected
# by an older check, so this rule newly rejects exactly one Tatoeba line in
# 40,000. Every GOOD carrier pinned below is a real Tatoeba line from that
# same sample.


def test_validate_carrier_rejects_a_headline_with_no_main_clause() -> None:
    """The pilot carrier itself. The verifier's own words: "Es handelt sich
    nicht um einen vollständigen Hauptsatz, sondern um ein Satzfragment ohne
    finites Vollverb im übergeordneten Satz." ``REASON_NO_FINITE_VERB`` does
    not catch it, because the relative clause supplies a finite verb."""
    result = cv.validate_carrier(
        "Ein Film, der die Frage aufwirft, wie man sich im Jahr 2025 "
        "eigentlich richtig hassen kann."
    )
    assert not result.accepted
    assert result.reason == cv.REASON_NO_MAIN_CLAUSE_VERB


@pytest.mark.parametrize(
    "sentence",
    [
        "Die Frau, die gestern angerufen hat, ist schon da.",
        "Der Mann, der bei mir nebenan wohnt, ist Arzt.",
        "Ein Kind, das einen Elternteil verloren hat, nennt man Halbwaise.",
        "Die Studentin, die da hinten lernt, ist eine Freundin von mir.",
        "Ein Mensch, der die Fähigkeit zum Staunen verloren hat, ist so gut wie tot.",
        "Der Junge, den ich liebe, liebt mich nicht.",
        "Dieses Buch, das ich zweimal gelesen habe, war ein Geschenk von Peter.",
    ],
)
def test_validate_carrier_accepts_a_relative_clause_with_a_real_main_clause(
    sentence: str,
) -> None:
    """The structurally identical GOOD shape: the same determiner + noun +
    relative clause opening, followed by a main clause that this rule must
    find. Seven real Tatoeba lines."""
    result = cv.validate_carrier(sentence)
    assert result.accepted, f"expected accept, got reason={result.reason!r}"


@pytest.mark.parametrize(
    "sentence",
    [
        # de_core_news_sm mistags a bare informal imperative as a noun and
        # roots the sentence on the OBJECT, which is why this rule requires
        # the sentence's first token to belong to the ROOT's own noun
        # phrase: here it is "Mach"/"Tadele"/"Küsse", an "sb"/"mo" child.
        "Mach das Beste, was du kannst!",
        "Tadele nicht den Ofen, in den du deine Erbtante geschoben hast.",
        "Küsse deinen Vati, wie es sich gehört!",
        # Left dislocation: a resumptive DEMONSTRATIVE sits where a relative
        # pronoun would, and the parser labels the following main clause
        # "rc". The relative-pronoun tag test is what saves these.
        "Die Zunge, die ist biegsam, eigenwillig und nicht fügsam.",
        "Die Kuh, die zuerst kommt, die trinkt sauberes Wasser!",
        # The main clause parsed as a conjunct of the ROOT noun.
        "Mein Vater, der noch lebt, und mein Großvater waren Sprachlehrer.",
        # pos_="NOUN" with tag_="VVFIN", a contradiction this module already
        # refuses to act on elsewhere (``_is_finite``).
        "Das war's, was ich von meiner Tochter erwartet habe.",
    ],
)
def test_validate_carrier_does_not_flag_measured_headline_false_positives(
    sentence: str,
) -> None:
    """Six of the seven false positives that the unguarded version of this
    check produced on 40,000 real Tatoeba lines, one per guard the shipped
    version carries. Asserts on the headline reason specifically rather than
    on overall acceptance: two of them are independently rejected by older,
    unrelated checks, and pinning acceptance would make this test fail
    whenever one of those changes."""
    result = cv.validate_carrier(sentence)
    assert result.reason != cv.REASON_NO_MAIN_CLAUSE_VERB


def test_validate_carrier_headline_rule_costs_one_tatoeba_aphorism() -> None:
    """The single measured cost of this rule across 40,000 Tatoeba lines,
    recorded rather than hidden. This aphorism is well-formed written German
    as an aphorism, and it genuinely is a noun phrase with a relative clause
    and no main clause, which is exactly what the rule says about it. Pinned
    so the cost stays visible and a later change to it is noticed."""
    result = cv.validate_carrier("Die einzige Waffe, die keine Waffe der Gewalt ist: die Wahrheit.")
    assert not result.accepted
    assert result.reason == cv.REASON_NO_MAIN_CLAUSE_VERB


# -- Cycle 13: the caption-parenthetical rule, measured and NOT shipped -----


def test_validate_carrier_caption_parenthetical_remains_a_documented_gap() -> None:
    """Module docstring section 15. "(am Ball)" is a sports-caption position
    marker, so this carrier is scraped captioning furniture rather than
    German sentence material, and it is NOT caught. The structural rule
    proposed for it -- a short verbless parenthesised insert between a
    proper-noun subject and its finite verb -- was measured over 40,000
    Leipzig lines: 150 of its 209 hits are carriers this module otherwise
    accepts, and hand-reading all 150 found 23 caption position markers
    against 127 ordinary grammatical journalistic appositions ("(CDU)",
    "(43)", "(MCP)", "(82.)"). Five and a half correct sentences discarded
    per piece of junk caught is the same trade the colon rule was thrown
    away for, so no rule ships and this stays the verifier's job.

    This test exists so a future change that silently starts catching this
    sentence is noticed and section 15 is revisited, not so this sentence is
    endorsed as good carrier material."""
    result = cv.validate_carrier(
        "Masi Pfand (am Ball) befindet sich aktuell in einer sehr guten Form."
    )
    assert result.accepted, (
        "if this now fails, a caption-parenthetical rule has appeared -- "
        "re-run the section 15 measurement and update the docstring, not "
        "only this test"
    )


def test_new_cycle_13_reason_constants_are_exported_with_stable_values() -> None:
    """Callers and tests key off these strings, exactly as they do for every
    older reason constant in this module."""
    assert cv.REASON_OPENS_MID_QUOTATION == "opens_mid_quotation"
    assert cv.REASON_NO_MAIN_CLAUSE_VERB == "no_main_clause_verb"


# -- Regression: the new cycle-5 checks must not reject known-good German ---


# -- The false-negative guard: every bad carrier a previous audit found -----
#
# data/fixtures/carrier_validation/known_bad_carriers.jsonl is the standing
# regression fixture the carrier-validation audit (2026-08-20) built per its
# own task instruction: every model-written carrier docs/audits/cycle-03
# through cycle-09-report.md hand-confirmed was bad German (or a documented,
# pre-existing known miss) must never silently start being accepted (or, for
# a known miss, never silently start being caught without the docstring
# being updated to say so). Run BEFORE a loosening to record the baseline and
# AFTER to prove nothing regressed -- a loosening that flips a "rejected"
# record to accepted is wrong and must be reverted, per the task's own
# standing instruction ("do not increase the false negative rate").


def _load_known_bad_carriers() -> list[dict]:
    records = []
    with _KNOWN_BAD_CARRIERS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("_meta"):
                continue
            records.append(record)
    return records


_KNOWN_BAD_CARRIERS = _load_known_bad_carriers()


@pytest.mark.parametrize("record", _KNOWN_BAD_CARRIERS, ids=[r["id"] for r in _KNOWN_BAD_CARRIERS])
def test_validate_carrier_known_bad_carriers_regression(record: dict) -> None:
    result = cv.validate_carrier(record["sentence"])
    if record["expected_outcome"] == "rejected":
        assert not result.accepted, (
            f"{record['id']} ({record['sentence']!r}) must stay rejected -- "
            f"a loosening admitted it. {record['defect']}"
        )
        assert result.reason == record["expected_reason"], (
            f"{record['id']}: expected reason {record['expected_reason']!r}, got {result.reason!r}"
        )
    else:
        assert record.get("documented_known_miss") is True, (
            f"{record['id']}: an 'accepted' record must be a documented known "
            "miss, never a silent gap"
        )
        assert result.accepted, (
            f"{record['id']} ({record['sentence']!r}) is a documented known "
            "miss and was expected to still be accepted; if this now fails, "
            "carrier_validation started catching it -- update this fixture's "
            "expected_outcome and the module docstring together, do not "
            "just flip this assertion"
        )


def test_known_bad_carriers_fixture_has_at_least_the_eight_named_sentences() -> None:
    """Pins the fixture's own minimum coverage: the audit task named eight
    sentences explicitly ('at minimum'). This does not replace the
    parametrized per-record test above; it guards against the fixture file
    itself being trimmed."""
    assert len(_KNOWN_BAD_CARRIERS) >= 8


# -- Swiss number formatting ("15'000" for "15.000") -------------------------
# Cycle 28. The apostrophe thousands separator is Swiss, and unlike every other
# Swiss check in this file it is not about "ss" for "ss": it is punctuation
# between digits, so none of the existing patterns could ever have matched it.
# Found in an ACCEPTED item, where it was rejected only because a later
# verification pass happened to object to the sentence's Zustandspassiv on
# entirely unrelated grounds.


@pytest.mark.parametrize(
    "sentence",
    [
        "Gestern wurden 15'000 Tickets verkauft.",
        "Die Stadt hat 1'200'000 Einwohner.",
        "Er zahlte 2'500 Franken dafuer.",
        # The typographic apostrophe, which is what a real Swiss publication
        # actually sets and what scraped text therefore often carries.
        "Gestern wurden 15\u2019000 Tickets verkauft.",
    ],
)
def test_validate_carrier_rejects_swiss_number_formatting(sentence: str) -> None:
    """A learner shown "15'000" is shown a number written the Swiss way. The
    German thousands separator is a full stop or a narrow space."""
    result = cv.validate_carrier(sentence)
    assert not result.accepted
    assert result.reason == cv.REASON_SWISS_SPELLING


@pytest.mark.parametrize(
    "sentence",
    [
        # German thousands separator: correct, must survive.
        "Gestern wurden 15.000 Tickets verkauft.",
        # No separator at all: correct.
        "Gestern wurden 15000 Tickets verkauft.",
        # An apostrophe that is not between digits is ordinary punctuation and
        # says nothing about Swiss orthography.
        "Er sagte: 'Das ist gut.'",
        "Wie geht's dir heute?",
        # A decimal comma, which is German and must not be confused for it.
        "Der Preis betraegt 15,50 Euro heute.",
    ],
)
def test_validate_carrier_keeps_sentences_without_a_swiss_number(sentence: str) -> None:
    """The check must fire on the separator, not on apostrophes generally."""
    result = cv.validate_carrier(sentence)
    assert result.reason != cv.REASON_SWISS_SPELLING
