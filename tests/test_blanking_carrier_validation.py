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
