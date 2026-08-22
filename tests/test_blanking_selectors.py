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
from src.generation.blanking import selectors, sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.pipeline import blank_sentences
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
# artikel_bestimmt_nom -- definite article, Nominative. Each of these three
# topics now requires a genuine forcing anchor (see selectors.py's own
# section for the three of them, added alongside this test rewrite): a bare
# Nominative sentence with no anchor is unsolvable ("Der/Ein/Kein/Mein Hund
# schläft im Garten" are all grammatical), so it must no longer yield a
# candidate at all -- the positive cases below all add the anchor their own
# topic needs; the negative cases at the end of each section pin down that a
# bare, unanchored sentence -- including every sentence this section used to
# test with before the anchor requirement existed -- now yields nothing.
# ==============================================================================


def test_artikel_bestimmt_nom_finds_masculine_definite_article_via_relative_clause() -> None:
    item = _blank(
        "artikel_bestimmt_nom", "Der Hund, den ich gestern gekauft habe, schläft im Garten."
    )
    assert item.prompt == "___ Hund, den ich gestern gekauft habe, schläft im Garten."
    assert item.proposed_answer == "Der"


def test_artikel_bestimmt_nom_finds_feminine_and_neuter_via_relative_clause() -> None:
    fem = _blank("artikel_bestimmt_nom", "Die Frau, die neben mir wohnt, ist Ärztin.")
    assert fem.proposed_answer == "Die"
    neut = _blank("artikel_bestimmt_nom", "Das Kind, das im Garten spielt, lacht laut.")
    assert neut.proposed_answer == "Das"


def test_artikel_bestimmt_nom_finds_it_via_a_superlative_adjective() -> None:
    item = _blank("artikel_bestimmt_nom", "Der größte Baum im Park ist über hundert Jahre alt.")
    assert item.proposed_answer == "Der"


def test_artikel_bestimmt_nom_finds_it_via_an_ordinal_adjective() -> None:
    item = _blank("artikel_bestimmt_nom", "Der letzte Tag im Urlaub war wunderschön.")
    assert item.proposed_answer == "Der"


def test_artikel_bestimmt_nom_ignores_the_accusative_article_in_the_same_sentence() -> None:
    """ "den Park" is Accusative, not Nominative -- only the anchored "Der
    Hund" qualifies."""
    _, candidates = _select(
        "artikel_bestimmt_nom", "Der Hund, den ich gestern gekauft habe, schläft im Garten."
    )
    assert len(candidates) == 1


def test_artikel_bestimmt_nom_rejects_an_accusative_object_with_an_earlier_subject() -> None:
    """docs/audits/cycle-07-report.md defect 4: "Der nette Nachbar ... hat
    die schwerste Kiste getragen." -- "die schwerste Kiste" is Accusative
    (the direct object of "getragen"), morphologically indistinguishable
    from Nominative on a feminine definite article, and the superlative
    anchor ("schwerste") that would otherwise force a determiner reading
    fires regardless of case. The sentence already has its own subject
    ("Der nette Nachbar", earlier and genuinely Nominative), so this must
    not also offer "die" as an artikel_bestimmt_nom candidate."""
    _, candidates = _select(
        "artikel_bestimmt_nom",
        "Der nette Nachbar von nebenan, der uns gestern geholfen hat, hat die schwerste "
        "Kiste getragen.",
    )
    assert candidates == []


def test_artikel_bestimmt_nom_finds_nothing_without_a_uniqueness_anchor() -> None:
    """The negative case that proves the anchor is doing the work: this is
    the exact sentence ``test_artikel_bestimmt_nom_ignores_the_accusative_
    article_in_the_same_sentence`` used before the anchor requirement
    existed, and it is real, grammatical German -- "Der/Ein/Kein/Mein Hund
    läuft schnell durch den Park" are all equally valid completions with
    nothing here to force "Der" specifically, so it must now yield no
    candidate at all."""
    _, candidates = _select("artikel_bestimmt_nom", "Der Hund läuft schnell durch den Park.")
    assert candidates == []


# ==============================================================================
# artikel_unbestimmt_kein_nom -- negative article ("kein"/"keine"), Nominative.
# Scoped to "kein" only, not plain "ein" (selectors.py's own module comment
# for why): a "weil"-clause is the one single-sentence device that forces a
# negation, and no equivalent device forces a bare affirmative indefinite.
# ==============================================================================


def test_artikel_unbestimmt_kein_nom_finds_it_in_either_clause_order() -> None:
    weil_first = _blank(
        "artikel_unbestimmt_kein_nom", "Weil kein Bus fährt, kommen wir heute zu spät."
    )
    assert weil_first.proposed_answer == "kein"
    weil_second = _blank(
        "artikel_unbestimmt_kein_nom",
        "Wir kaufen das Brot woanders, weil keine Bäckerei heute geöffnet hat.",
    )
    assert weil_second.proposed_answer == "keine"


def test_artikel_unbestimmt_kein_nom_rejects_plural_kein_as_a_known_paradigm_gap() -> None:
    """ "Keine Kinder" (plural Nominative "kein") is genuine, grammatical
    German, but the reused ein-word ending paradigm has no Nom/Acc plural
    row at all (built for "ein", which has no plural) -- see
    ``paradigms.py``'s module docstring. The selector still finds the
    token; the blanker's own paradigm-reconstruction check is what refuses
    rather than guesses."""
    tagged, candidates = _select(
        "artikel_unbestimmt_kein_nom", "Weil keine Kinder draußen spielen, ist es ruhig."
    )
    assert len(candidates) == 1
    outcome = blank_candidate("artikel_unbestimmt_kein_nom", tagged, candidates[0])
    assert outcome.item is None
    assert outcome.skip_reason == "determiner_cell_uncovered_by_paradigm"


def test_artikel_unbestimmt_kein_nom_finds_a_candidate_without_a_weil_clause() -> None:
    """TODO.md 2.3 (owner's decision, cycle 9): the causal "weil"-clause
    anchor used to GATE candidacy entirely, because with no cue "Kein/Der/
    Ein/Mein Bus fährt heute" are all equally valid completions and nothing
    forces "kein" specifically. Under the invariant-citation cue (TODO.md
    2.1) the cue always names the family ("kein") directly, so that
    forcing is no longer needed -- the anchor is still recorded (``False``
    here, no "weil" clause), but no longer withholds the candidate."""
    _, candidates = _select("artikel_unbestimmt_kein_nom", "Kein Bus fährt heute.")
    assert len(candidates) == 1
    assert candidates[0].lexeme_anchored is False
    assert candidates[0].cue == "Kein"


def test_artikel_unbestimmt_kein_nom_finds_nothing_for_a_bare_indefinite() -> None:
    """Plain "ein" (no negation) is out of this topic's anchored scope
    entirely -- see selectors.py's own module comment: nothing forces a bare
    affirmative indefinite over "der"/"kein"/a possessive the way a "weil"
    clause forces a negation."""
    _, candidates = _select("artikel_unbestimmt_kein_nom", "Ein Mann steht vor der Tür.")
    assert candidates == []


# ==============================================================================
# artikel_possessiv_nom -- possessive determiner, Nominative.
# ==============================================================================


def test_artikel_possessiv_nom_finds_different_persons_via_relative_clause() -> None:
    mein = _blank(
        "artikel_possessiv_nom",
        "Meine Großmutter, die ich jedes Wochenende besuche, wohnt in München.",
    )
    assert mein.proposed_answer == "Meine"
    dein = _blank(
        "artikel_possessiv_nom", "Dein Bruder, den du gestern angerufen hast, wohnt in Berlin."
    )
    assert dein.proposed_answer == "Dein"
    sein = _blank(
        "artikel_possessiv_nom",
        "Sein Onkel, den ich letzten Sommer kennengelernt habe, lebt in Hamburg.",
    )
    assert sein.proposed_answer == "Sein"


def test_artikel_possessiv_nom_finds_a_candidate_without_the_person_anchor() -> None:
    """TODO.md 2.3 (owner's decision, cycle 9): the kinship-noun-plus-
    relative-clause person anchor used to GATE candidacy entirely -- real,
    but narrow enough that this topic produced nothing across three
    cycles. Under the invariant-citation cue (TODO.md 2.1) the cue always
    names WHICH possessive stem is meant, so that forcing is no longer
    needed for every Nominative possessive, not only the kinship-plus-
    relative-clause shape -- the anchor is still recorded (``False`` here,
    no relative clause at all), but no longer withholds the candidate."""
    _, candidates = _select("artikel_possessiv_nom", "Mein Vater kocht heute Abend.")
    assert len(candidates) == 1
    assert candidates[0].lexeme_anchored is False
    assert candidates[0].cue == "Mein"


def test_artikel_possessiv_nom_finds_a_candidate_for_a_body_part_noun() -> None:
    """Body-part nouns (module docstring above selectors.SELECTORS) never
    satisfy the narrower kinship-plus-relative-clause anchor -- German
    idiomatically prefers the definite article there ("Ich wasche mir die
    Hände") -- but TODO.md 2.3 no longer gates candidacy on that anchor at
    all, so this still yields a candidate, unanchored."""
    _, candidates = _select(
        "artikel_possessiv_nom", "Meine Hand, die ich mir verletzt habe, tut noch weh."
    )
    assert len(candidates) == 1
    assert candidates[0].lexeme_anchored is False


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


def test_kasus_dativ_formen_finds_a_ditransitive_verbs_indirect_object() -> None:
    """ "geben" takes both a Dative recipient and an Accusative direct
    object -- forced here because "ein Buch" (Accusative, determiner
    present) sits in the same clause; see ``paradigms.DITRANSITIVE_
    DATIVE_VERBS``."""
    item = _blank("kasus_dativ_formen", "Er gibt seiner Schwester ein Buch.")
    assert item.prompt == "Er gibt ___ Schwester ein Buch."
    assert item.proposed_answer == "seiner"


# ==============================================================================
# TODO.md 8.5 / docs/audits/cycle-10-corpus-report.md: three items that are
# not Dative at all -- a Nominative apposition, an Accusative direct
# object, a Genitive noun complement -- but were all tagged ``Case=Dat``
# and accepted by the old tag-only check. ``kasus_dativ_formen`` now
# requires the Dative reading to be FORCED by the clause's own governing
# verb (``selectors._select_kasus_dativ_formen``); none of these three has
# one, so all three now correctly yield no candidate at all.
# ==============================================================================


def test_kasus_dativ_formen_rejects_a_nominative_apposition_tagged_dative() -> None:
    """ "einer nach dem anderen" ("one after another") is a fixed Nominative
    apposition to the subject "Busse" -- "fahren ab" (to depart) governs no
    Dative object at all, so this candidate is skipped: not in
    ``DATIVE_ONLY_VERBS``, not in ``DITRANSITIVE_DATIVE_VERBS``."""
    _, candidates = _select("kasus_dativ_formen", "Die Busse fuhren einer nach dem anderen ab.")
    assert candidates == []


def test_kasus_dativ_formen_rejects_an_accusative_direct_object_tagged_dative() -> None:
    """ "den Murks" is the Accusative direct object of "gelesen" (lesen) --
    "lesen" takes no Dative object at all, so this candidate is skipped
    even though the tagger heads "den" ``Case=Dat``."""
    _, candidates = _select(
        "kasus_dativ_formen",
        "Ich weiß, obwohl natürlich alle den Murks längst gelesen haben, dass es stimmt.",
    )
    assert candidates == []


def test_kasus_dativ_formen_rejects_a_genitive_noun_complement_tagged_dative() -> None:
    """ "der Band" is a Genitive complement of the noun "Schlagzeuger"
    ("drummer of the band"), not governed by any verb at all -- the
    clause's own governing verb is the copula "ist" with a predicate
    adjective ("bekannt"), which has no participle/infinitive complement
    for ``_governing_verb_lemma`` to resolve through, so it correctly
    returns ``None`` and this candidate is skipped."""
    _, candidates = _select(
        "kasus_dativ_formen",
        "Miley Cyrus und Maxx Morando, der als Schlagzeuger der Band Liily bekannt ist, "
        "sind seit 2021 ein Paar.",
    )
    assert candidates == []


# ==============================================================================
# TODO 8.1: ``_select_kasus_dativ_formen``'s forcing check now consults the
# corpus-built lexicon (``verb_government.object_verdict``) before falling
# back to ``paradigms.DATIVE_ONLY_VERBS`` -- these are verbs TODO 8.5's own
# 37-verb hand list never named, all forced by corpus evidence alone.
# Sourced from the TODO 8.1 acceptance sentences.
# ==============================================================================


def test_kasus_dativ_formen_finds_glauben_a_dative_only_verb_the_lexicon_learned() -> None:
    item = _blank("kasus_dativ_formen", "Ich glaube meinem Bruder jedes Wort.")
    assert item.proposed_answer == "meinem"


def test_kasus_dativ_formen_finds_raten_with_a_prepositional_complement() -> None:
    """ "raten" governs a bare Dative object ("ihrer Freundin") plus a
    prepositional phrase ("zu einem Anwalt") -- not a bare Accusative
    object, so this is forced purely by "raten" itself being Dative-only
    by corpus evidence, the same way ``DATIVE_ONLY_VERBS`` members always
    were, never by the ditransitive accompanying-object check."""
    item = _blank("kasus_dativ_formen", "Sie riet ihrer Freundin zu einem Anwalt.")
    assert item.proposed_answer == "ihrer"


def test_kasus_dativ_formen_finds_vorlesen_and_reichen_ditransitive_by_corpus_evidence() -> None:
    """ "vorlesen" and "reichen" are not on ``DITRANSITIVE_DATIVE_VERBS``
    either -- the corpus lexicon forces both directly (no accompanying-
    object check needed), because their own Dative-pronoun evidence is
    itself strongly one-sided (see the fixture's own counts)."""
    item = _blank("kasus_dativ_formen", "Die Mutter las ihrem Sohn eine Geschichte vor.")
    assert item.proposed_answer == "ihrem"
    item2 = _blank("kasus_dativ_formen", "Er reichte seinem Nachbarn die Hand.")
    assert item2.proposed_answer == "seinem"


def test_kasus_dativ_formen_still_skips_a_free_dative_no_verb_forces() -> None:
    """ "Meiner Schwester ist es viel zu kalt." -- the Dative is governed by
    the predicate adjective "kalt", not by any verb ("ist" is a bare
    copula) -- outside what a VERB-government lexicon can ever reach, by
    construction, not a gap in this build. Confirmed left dropped, not
    guessed at."""
    _, candidates = _select("kasus_dativ_formen", "Meiner Schwester ist es viel zu kalt.")
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
    # "freundlichen", not the more obvious "netten": confirmed live that
    # this exact model mistags "netten" (but not "nette") ``Degree=Sup``
    # in every oblique-case sentence tried, a self-consistent tagger bug on
    # this one lemma's ``-en`` form, independent of and unrelated to the
    # cycle-06 comparative/superlative LEMMA mistags
    # (``_MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS``) -- docs/audits/
    # cycle-07-report.md section B's new Degree=Pos-only gate (added
    # alongside this test) would otherwise correctly-but-wrongly reject a
    # genuinely positive-degree adjective here. Not fixed in this cycle
    # (out of its own stated scope); "freundlichen" sidesteps it rather
    # than papering over it.
    item = _blank("adjektivdeklination_bestimmt", "Ich helfe dem freundlichen Nachbarn.")
    assert item.proposed_answer == "freundlichen"
    # Weak declension has exactly two endings total ("e"/"en"); at this cell
    # ("en") the only other possible surface form is the "e" variant.
    assert [d.text for d in item.distractors] == ["freundliche"]


def test_adjektivdeklination_bestimmt_rejects_a_determiner_from_an_unrelated_noun_phrase() -> None:
    """docs/audits/cycle-10-corpus-report.md 8.7: "Die Frisur hat bei den
    Studentinnen guten Anklang gefunden." -- "guten Anklang" has no article
    of its own; "den" belongs to "Studentinnen" inside the PP "bei den
    Studentinnen", an entirely different noun phrase. The far-trigger walk
    (``_find_governing_declension_trigger``) used to have no clause-
    boundary stop condition at all -- confirmed live, it crossed the
    finite AUX "hat" to reach the SENTENCE-INITIAL subject's own article
    ("Die"), wrongly reporting "weak" declension. Fixed by stopping the
    walk at ``_NOUN_PHRASE_BOUNDARY_POS`` (VERB/AUX/SCONJ/CCONJ/PUNCT), the
    same boundary set the null-article selector's own
    ``_noun_phrase_has_governing_determiner`` walk already uses -- a strict
    superset of the walk's previous ``("KON", "$,")``-only stop, so the
    positive far-trigger cases below (an inserted PP between a REAL
    governing determiner and its own participle) are unaffected.

    The item is DROPPED here, not rerouted to
    ``adjektivdeklination_nullartikel``: ``_noun_phrase_has_governing_
    determiner``'s own walk (unchanged by this fix) also finds "den" before
    reaching the same AUX boundary, and -- unlike the far-trigger walk
    above -- does not distinguish "a determiner belonging to an embedded
    PP's own object" from "a determiner governing THIS noun phrase", so it
    correctly-but-conservatively treats "Anklang" as not-provably-zero-
    article either. Dropping is the right call here: "reject rather than
    guess" applied to the SAME uncertainty this walk already accepts
    elsewhere, not a new gap this task introduced."""
    sentence = "Die Frisur hat bei den Studentinnen guten Anklang gefunden."
    _, bestimmt_candidates = _select("adjektivdeklination_bestimmt", sentence)
    assert bestimmt_candidates == []
    _, nullartikel_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert nullartikel_candidates == []


def test_adjektivdeklination_bestimmt_rejects_a_determiner_from_a_sibling_noun() -> None:
    """Found while verifying the 8.7 fix's own corpus impact, not one of
    the nine reported items itself: "Das Leben besteht aus kleinen
    Handlungen und die Tugend aus kleinen Siegen." -- the second "kleinen"
    governs "Siegen", which has no article of its own ("aus kleinen
    Siegen"). The far-trigger walk used to cross straight past "Tugend" (an
    entirely unrelated NOUN sitting on the walk's path back from "Siegen")
    to reach "die", "Tugend"'s OWN article, and wrongly report it as
    "Siegen"'s governing determiner too -- the exact same "determiner must
    be inside the head noun's own phrase" principle the 8.7 fix above
    already established, just crossed via a bare noun instead of a
    verb/AUX. Fixed by treating a NOUN the walk's own
    ``_skip_intervening_pp`` declines to consume (it does not terminate in
    an ADP within range, so it is not the "von einem Maler" shape) as a
    hard stop, same as the verb/AUX boundary. The first "kleinen"
    (governing "Handlungen", also article-less) is unaffected and still
    correctly resolves to ``adjektivdeklination_nullartikel``."""
    sentence = "Das Leben besteht aus kleinen Handlungen und die Tugend aus kleinen Siegen."
    _, bestimmt_candidates = _select("adjektivdeklination_bestimmt", sentence)
    assert bestimmt_candidates == []
    item = _blank("adjektivdeklination_nullartikel", sentence)
    assert item.proposed_answer == "kleinen"


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


def test_adjektivdeklination_nullartikel_does_not_fire_on_a_possessive_determiner() -> None:
    """docs/audits/cycle-07-report.md defect 2: "Gegen euer Unwohlsein ..."
    -- "euer" is the possessive determiner (an ein-word: eur-/euer stem),
    not a genuine attributive adjective, even though ``de_core_news_sm``
    tags it ADJA here. Treating it as one synthesises a stem ("eu") that
    is not a real adjective stem, so the whole candidate must be rejected,
    not merely have its distractors filtered afterward."""
    _, candidates = _select(
        "adjektivdeklination_nullartikel",
        "Gegen euer Unwohlsein wurde früher ganz sicher direkt eine warme Wärmflasche geholt.",
    )
    assert candidates == []


def test_adjective_outcome_drops_distractor_forms_that_are_not_real_words() -> None:
    """docs/audits/cycle-07-report.md defect 2, the blanker-level half of the
    same fix: even if a candidate were built pointing at "euer" (e.g. by a
    future, differently-scoped selector, or before the selector-level
    exclusion above existed), the synthesised distractors "eue"/"euem"/
    "euen" are not real German words and must be filtered out rather than
    shipped to a learner at hint level 2."""
    tagged = sentence_tagger.tag_sentence(
        "Gegen euer Unwohlsein wurde früher ganz sicher direkt eine warme Wärmflasche geholt."
    )
    assert tagged is not None
    index = next(i for i, t in enumerate(tagged.tokens) if t.text == "euer")
    candidate = selectors.Candidate(
        token_index=index,
        kind="adjective",
        declension="strong",
        cell=("Nom", "Masc", "Sing"),
    )
    outcome = blank_candidate("adjektivdeklination_nullartikel", tagged, candidate)
    assert outcome.item is not None
    distractor_texts = {d.text for d in outcome.item.distractors}
    assert distractor_texts.isdisjoint({"eue", "euem", "euen", "eues"})


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


def test_adjektivdeklination_nullartikel_does_not_fire_past_a_preceding_adjective() -> None:
    """docs/audits/cycle-06-report.md class E, item 1: "die erste ___
    Besprechung" -- the governing determiner ("die") sits one token further
    back than usual, behind another attributive adjective ("erste"), not a
    prepositional phrase. Before the far-scan's span check accepted a
    plain adjective chain (not only a PP), "wichtige" was wrongly selected
    as zero-article/strong."""
    sentence = (
        "Gemeinsam erreichten sie die Firma, wo schon die erste wichtige Besprechung wartete."
    )
    _, strong_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert strong_candidates == []
    weak_item = _blank("adjektivdeklination_bestimmt", sentence, candidate_index=1)
    assert weak_item.proposed_answer == "wichtige"


def test_adjektivdeklination_nullartikel_does_not_fire_past_a_preceding_quantifier() -> None:
    """docs/audits/cycle-06-report.md class E, item 2: "eine viel ___
    Zukunft" -- the governing determiner ("eine") sits one token further
    back behind a degree adverb ("viel") modifying the comparative
    adjective itself, not a prepositional phrase."""
    sentence = "Ich wünsche mir wirklich eine viel umweltfreundlichere Zukunft für unsere Stadt."
    _, strong_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert strong_candidates == []
    mixed_item = _blank("adjektivdeklination_unbestimmt", sentence)
    assert mixed_item.proposed_answer == "umweltfreundlichere"


def test_adjektivdeklination_nullartikel_does_not_fire_on_the_maler_sentence_live() -> None:
    """docs/audits/cycle-06-report.md class E, item 3: "das von einem Maler
    ___ Arbeitszimmer" -- reported fixed by a previous cycle and verified
    fixed, but still live, because the fix was verified only against an
    isolated call, on a sentence shaped so "das" is tagged ``ART``. In the
    actual full sentence a fronted clause precedes the finite verb, and
    ``de_core_news_sm`` -- confirmed directly -- then tags that same "das"
    ``PDS`` (demonstrative pronoun) instead, purely because of clause
    position, defeating the ``ART``-only determiner check.

    Pinned as the full, fronted-clause sentence (not the bare "Wir hatten
    das ..." fragment a prior cycle tested), and verified through the real
    pipeline (``blank_sentences``), not a direct selector call, since that
    is exactly the gap the audit named."""
    sentence = (
        "Nachdem der Umzug endlich abgeschlossen war, hatten wir das von einem "
        "Maler gestaltete Arbeitszimmer besichtigt."
    )
    tagged = sentence_tagger.tag_sentence(sentence)
    assert tagged is not None
    das = next(t for t in tagged.tokens if t.text == "das" and t.i > 0)
    assert das.tag == "PDS", "this regression pin depends on the live PDS mistag reproducing"

    _, strong_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert strong_candidates == []
    _, weak_candidates = _select("adjektivdeklination_bestimmt", sentence)
    assert [c.declension for c in weak_candidates] == ["weak"]

    report = blank_sentences([sentence])
    assert not any(item.topic_id == "adjektivdeklination_nullartikel" for item in report.items)
    # The pipeline does not go on to emit an ACCEPTED adjektivdeklination_
    # bestimmt item for this exact sentence either, but for an entirely
    # separate, pre-existing reason that has nothing to do with this fix
    # and is out of this module's scope: ``de_core_news_sm`` also mistags
    # "Arbeitszimmer"/"gestaltete" themselves as Masculine Plural (confirmed
    # even on the bare, subject-first, ART-tagged form of this sentence, so
    # it is independent of the PDS mistag above), which makes
    # ``blanker.py``'s own paradigm-reconstruction check correctly refuse to
    # emit an item ("reject rather than guess", the same safe failure
    # direction as everywhere else in this pipeline) rather than emit one
    # with a mismatched ending. Pinned here as a skip, not an item, so this
    # test states plainly what the pipeline actually does today.
    assert not any(item.topic_id == "adjektivdeklination_bestimmt" for item in report.items)
    assert any(
        d.topic_id == "adjektivdeklination_bestimmt" and d.reason == "adjective_ending_mismatch"
        for d in report.skip_details
    )


def test_adjektivdeklination_nullartikel_does_not_fire_when_a_determiner_precedes_the_clause() -> (
    None
):
    """docs/audits/cycle-07-report.md defect 3: "mit dem vor wenigen Wochen
    gekauften Ball" -- the head noun ("Ball") has a genuine determiner
    ("dem"), just with "vor wenigen Wochen" intervening between the
    determiner and the participle. The null-article selector must reject
    this regardless of how much material intervenes, because the head noun
    has a determiner child at all; ``adjektivdeklination_bestimmt`` is the
    correct topic and must still find the weak ending."""
    sentence = "Dort trainiere ich mit dem vor wenigen Wochen gekauften Ball."
    _, strong_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert strong_candidates == []
    weak_item = _blank("adjektivdeklination_bestimmt", sentence)
    assert weak_item.proposed_answer == "gekauften"


def test_adjektivdeklination_nullartikel_does_not_fire_past_a_second_preceding_adjective() -> None:
    """docs/audits/cycle-09-report.md 1.5, the fourth reported cycle for
    this class: "diese innovative ___ Lösung" -- "diese" is a genuine
    governing determiner, but a SECOND attributive adjective ("innovative")
    sits between it and the blanked one ("technische"), defeating every
    previous fix (which extended a backward SCAN one hop at a time: an
    intervening prepositional phrase, then an adverbial, then a quantifier
    -- see the three tests above and cycle-06-report.md class E). Fixed by
    walking the noun phrase from the head noun instead of extending the
    scan again (``selectors._noun_phrase_has_governing_determiner``): reject
    if ANY determiner exists at any distance, rather than checking a fixed
    number of hops back."""
    sentence = "Wir möchten gerne wissen, ob Ihnen diese innovative technische Lösung gefällt."
    _, strong_candidates = _select("adjektivdeklination_nullartikel", sentence)
    assert strong_candidates == []


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


# ==============================================================================
# adjektiv_komparativ_superlativ cue (docs/audits/cycle-06-modal-leak.md):
# without one, any comparative/superlative fits the slot -- the topic's own
# ``eligible_types`` is ``[cloze_cued]`` alone, never ``cloze_free``.
# ==============================================================================


def test_komparativ_superlativ_cue_is_the_positive_form() -> None:
    """spaCy's own lemmatiser correctly resolves even irregular comparatives
    (gut/besser, hoch/höher, nah/näher, groß/größer) back to their positive
    form -- confirmed by direct testing, see
    ``selectors._MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS`` for the confirmed
    exceptions."""
    item = _blank("adjektiv_komparativ_superlativ", "Das Zimmer ist größer als die Küche.")
    assert item.proposed_answer == "größer"
    assert item.cue == "groß"
    assert item.type == "cloze_cued"


def test_komparativ_superlativ_cue_is_none_for_the_confirmed_bad_gern_lemma() -> None:
    """ "gern - lieber - am liebsten" is a suppletive comparison: the positive
    form is a different word ("gern"), not "lieb". de_core_news_sm resolves
    the comparative/superlative to the real, but wrong, adjective "lieb"
    ("dear") -- cueing it would point the learner at the wrong word, so
    ``_degree_cue`` withholds it."""
    _, candidates = _select(
        "adjektiv_komparativ_superlativ", "Ich esse am liebsten italienische Gerichte."
    )
    assert len(candidates) == 1
    assert candidates[0].cue is None


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


def test_verben_reflexiv_akk_rejects_sich_when_the_clause_has_a_direct_object() -> None:
    """docs/audits/cycle-07-report.md defect 8: "..., den er sich frisch
    gekocht hatte." -- the relative pronoun "den" IS the direct object of
    "gekocht" (a benefactive-dative "sich": he cooked it FOR himself), so
    this "sich" is Dative, not Accusative, even though "kochen" is not on
    any closed dative-reflexive-verb list. A clause with a direct object
    already present means a reflexive "sich" alongside it can only be the
    dative benefactive, never the accusative object itself (that slot is
    taken); verben_reflexiv_akk must not fire here, and verben_reflexiv_dat
    must find "sich" instead."""
    sentence = "Danach trank er gemütlich einen Kaffee, den er sich frisch gekocht hatte."
    _, akk_candidates = _select("verben_reflexiv_akk", sentence)
    assert akk_candidates == []
    item = _blank("verben_reflexiv_dat", sentence)
    assert item.proposed_answer == "sich"


def test_verben_reflexiv_dat_rejects_a_dative_pronoun_governed_by_another_verb() -> None:
    """docs/audits/cycle-07-report.md defect 9: "...dem mir oft helfenden
    Trainer..." -- "mir" is the dative object of the ATTRIBUTIVE participle
    "helfenden" ("the trainer who often helps me"), not a reflexive
    pronoun of the sentence's own finite verb ("helfe"); it does not
    corefer with the subject of ITS OWN governing verb ("helfenden",
    whose implicit subject is "Trainer", not "ich"). Must not be mistaken
    for a reflexive dative just because the sentence elsewhere has a
    first-person subject and a form of "helfen"."""
    _, candidates = _select(
        "verben_reflexiv_dat", "Im Verein helfe ich dem mir oft helfenden Trainer beim Aufräumen."
    )
    assert candidates == []


# ==============================================================================
# Dative-reflexive routing is decided by the GOVERNING VERB's own argument
# structure (docs/audits/cycle-04-report.md's second finding), not by
# whether some accusative object happens to sit anywhere in the sentence.
# Three of the report's own four sampled ``verben_reflexiv_dat`` items were
# actually Accusative-reflexive verbs the old whole-sentence object scan
# wrongly promoted to Dative; these pin the fix on the report's own
# examples.
# ==============================================================================


def test_verben_reflexiv_akk_routes_sich_freuen_despite_an_object_in_another_clause() -> None:
    """docs/audits/cycle-04-report.md's own example: "Wir würden uns
    freuen, wenn Sie unsere Fragen beantworten." -- "unsere Fragen" is an
    accusative object of "beantworten" in the SUBORDINATE clause, not of
    "freuen"; a whole-sentence object scan wrongly promoted this to Dative,
    but "freuen" is not on the dative-reflexive verb list at all."""
    item = _blank(
        "verben_reflexiv_akk",
        "Wir würden uns freuen, wenn Sie unsere Fragen beantworten.",
    )
    assert item.proposed_answer == "uns"
    _, dat_candidates = _select(
        "verben_reflexiv_dat", "Wir würden uns freuen, wenn Sie unsere Fragen beantworten."
    )
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_treffen_with_no_object_anywhere() -> None:
    """docs/audits/cycle-04-report.md's own example: "Wir treffen uns jeden
    Samstag im Park." -- "treffen" is not on the dative-reflexive verb
    list, so it routes Accusative regardless of any object heuristic."""
    item = _blank("verben_reflexiv_akk", "Wir treffen uns jeden Samstag im Park.")
    assert item.proposed_answer == "uns"
    _, dat_candidates = _select("verben_reflexiv_dat", "Wir treffen uns jeden Samstag im Park.")
    assert dat_candidates == []


def test_verben_reflexiv_dat_routes_sich_wuenschen_with_a_dass_clause_object() -> None:
    """docs/audits/cycle-09-report.md 1.1, all three of "sich (Dat) etwas
    wünschen"'s reported items: the object is a "dass" clause, not a noun
    phrase, so neither ``_has_bare_accusative_object`` nor
    ``_immediately_followed_by_object_np`` sees it at all -- closed by
    ``_followed_by_dass_clause_object``."""
    item = _blank(
        "verben_reflexiv_dat", "Wir wünschen uns, dass Sie uns Ihre ehrliche Meinung mitteilen."
    )
    assert item.proposed_answer == "uns"
    item2 = _blank(
        "verben_reflexiv_dat", "Er wünscht sich sehr, dass er bald bessere Noten schreibt."
    )
    assert item2.proposed_answer == "sich"
    item3 = _blank(
        "verben_reflexiv_dat", "Sie wünscht sich, dass das teure Obst günstiger verkauft wird."
    )
    assert item3.proposed_answer == "sich"


def test_verben_reflexiv_akk_routes_sich_treffen_past_an_apposition() -> None:
    """docs/audits/cycle-09-report.md 1.1: "Um zwei Uhr treffen wir uns alle
    vor dem Haupteingang." -- "alle" is an apposition to the subject
    ("wir alle"), tagged ``Case=Acc`` by agreement with the reflexive
    rather than the Nominative subject it restates, and the old rule
    wrongly counted it as "treffen"'s own direct object. Closed by
    ``_APPOSITIONAL_QUANTIFIER_LEMMAS``."""
    item = _blank("verben_reflexiv_akk", "Um zwei Uhr treffen wir uns alle vor dem Haupteingang.")
    assert item.proposed_answer == "uns"
    _, dat_candidates = _select(
        "verben_reflexiv_dat", "Um zwei Uhr treffen wir uns alle vor dem Haupteingang."
    )
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_treffen_past_a_temporal_accusative() -> None:
    """docs/audits/cycle-09-report.md 1.1: "Sie treffen sich heute Nachmittag
    mit Ihrer Freundin." -- "heute Nachmittag" is an accusative time
    adverbial, not an object, wrongly counted as one by the old rule.
    Closed by reusing ``paradigms.TEMPORAL_ANCHOR_LEMMAS`` (this task's own
    instruction: reuse the existing list rather than a second one)."""
    item = _blank("verben_reflexiv_akk", "Sie treffen sich heute Nachmittag mit Ihrer Freundin.")
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select(
        "verben_reflexiv_dat", "Sie treffen sich heute Nachmittag mit Ihrer Freundin."
    )
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_ansammeln_past_a_mistagged_subject() -> None:
    """docs/audits/cycle-09-report.md 1.1: "...weil sich darauf viel Staub
    angesammelt hat." -- "viel Staub" is the clause's genuine Nominative
    SUBJECT, but spaCy persistently mistags it ``Case=Acc`` in this exact
    postposed-subject-after-fronted-reflexive word order (confirmed against
    several rewordings), so the old rule counted it as an object. Closed by
    ``paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS`` -- "ansammeln" is
    genuinely accusative-reflexive-only, so no object scan is needed (or
    safe) for it at all."""
    sentence = "Der Keller wurde feucht, weil sich darauf viel Staub angesammelt hat."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_aendern_in_a_subordinate_clause() -> None:
    """docs/audits/cycle-04-report.md's own example (paraphrased to a
    complete sentence): "ändern" is not on the dative-reflexive verb list,
    so the subordinate clause's own "sich" routes Accusative -- this also
    pins that the governing-verb lookup is scoped to "hat" being the one
    finite verb of the "dass" clause itself, not the main clause's."""
    item = _blank("verben_reflexiv_akk", "Er sagte, dass sich die Situation geändert hat.")
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select(
        "verben_reflexiv_dat", "Er sagte, dass sich die Situation geändert hat."
    )
    assert dat_candidates == []


def test_verben_reflexiv_dat_routes_sich_helfen_with_no_object_required() -> None:
    """docs/audits/cycle-04-report.md's own sole genuinely-dative example:
    "Wir helfen uns gegenseitig." -- "helfen" governs the Dative on any
    object at all, so no co-occurring accusative is required, unlike every
    other verb on the dative-reflexive list."""
    item = _blank("verben_reflexiv_dat", "Wir helfen uns gegenseitig.")
    assert item.proposed_answer == "uns"
    _, akk_candidates = _select("verben_reflexiv_akk", "Wir helfen uns gegenseitig.")
    assert akk_candidates == []


def test_verben_reflexiv_dat_routes_a_closed_list_verb_with_its_own_accusative_object() -> None:
    """ "merken" (docs/audits/cycle-04-report.md's own named example, "sich
    etwas merken") is on the dative-reflexive-with-object list, and "die
    Telefonnummer" is its own accusative object in the same clause."""
    item = _blank("verben_reflexiv_dat", "Ich merke mir die Telefonnummer.")
    assert item.proposed_answer == "mir"
    _, akk_candidates = _select("verben_reflexiv_akk", "Ich merke mir die Telefonnummer.")
    assert akk_candidates == []


def test_verben_reflexiv_dat_reconstructs_a_separable_verbs_split_prefix() -> None:
    """ "ansehen" ("sich etwas ansehen") splits into "sehe ... an" in plain
    present-tense word order; confirmed empirically that spaCy's own lemma
    for "sehe" alone drops the prefix ("sehen", not "ansehen"). The
    separated ``PTKVZ`` token "an" is reconstructed onto it rather than
    trusting that bare, confirmed-wrong lemma -- see
    ``_governing_verb_lemma``'s own docstring."""
    item = _blank("verben_reflexiv_dat", "Ich sehe mir den Film an.")
    assert item.proposed_answer == "mir"
    _, akk_candidates = _select("verben_reflexiv_akk", "Ich sehe mir den Film an.")
    assert akk_candidates == []


def test_verben_reflexiv_selectors_skip_when_the_governing_verb_is_undetermined() -> None:
    """ "Er kommt und wäscht sich." has no comma before "und", so the clause
    span this module derives from commas alone (no dependency parser is
    available, see ``_clause_span``'s own docstring) contains BOTH finite
    verbs ("kommt" and "wäscht") -- "reject rather than guess" applies to
    the governing verb itself, not only to which case it implies, so this
    candidate is skipped on EITHER topic rather than guessed at."""
    _, akk_candidates = _select("verben_reflexiv_akk", "Er kommt und wäscht sich.")
    assert akk_candidates == []
    _, dat_candidates = _select("verben_reflexiv_dat", "Er kommt und wäscht sich.")
    assert dat_candidates == []


# ==============================================================================
# TODO 8.1 / docs/audits/cycle-10-corpus-report.md: reflexive case forced by
# the corpus-built lexicon (data/fixtures/verb_government/lexicon.v1.jsonl,
# scripts/build_verb_government.py), which replaces
# ``paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT``/``ACCUSATIVE_ONLY_
# REFLEXIVE_VERBS`` as the FIRST thing consulted for a verb not on
# ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` (``_reflexive_case``), falling back
# to the same structural object-presence scan only when the lexicon has no
# opinion. The hand-listed verbs above (helfen/freuen/treffen/ändern/
# ansammeln/beeilen) still resolve identically -- the fixture's own
# ``final`` field is the merge, hand list winning on conflict -- so this
# section pins genuinely NEW coverage the lexicon adds past those six.
# ==============================================================================


def test_verben_reflexiv_akk_routes_a_verb_the_lexicon_learned_from_the_corpus() -> None:
    """ "schämen" is on no hand list at all; TODO 8.1's harvest found it
    strongly Accusative-only from real corpus evidence (unlike the pre-
    lexicon code, which reached the same answer here only via the
    structural object-absence fallback -- see the ``schämt``/"für sein
    Verhalten" test above). Confirms the lexicon path and the structural
    fallback agree, not merely that one of them fires."""
    item = _blank("verben_reflexiv_akk", "Er schämt sich für sein Verhalten.")
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", "Er schämt sich für sein Verhalten.")
    assert dat_candidates == []


def test_verben_reflexiv_akk_flips_sich_zur_wahl_stellen_cycle_10_regression() -> None:
    """docs/audits/cycle-10-corpus-report.md: "Man muss sich jedes Mal
    wieder zur Wahl stellen." wrongly landed in ``verben_reflexiv_dat``
    before this fix. "stellen" itself is genuinely mixed-government by
    corpus evidence (both "sich hinstellen"-type Accusative and "sich
    (Dat) eine Frage stellen"-type Dative constructions are real, and the
    harvest confirms both occur), so the lexicon correctly leaves it
    unforced ("mixed, stay mixed") -- what actually closes this defect is
    the structural fallback it falls through to, now that "mal" ("jedes
    Mal") is exempted from the bare-accusative-object scan the same way
    "Uhr"/"Nachmittag"/the weekday names already are (a gap found while
    verifying this exact sentence, not a case-government list problem)."""
    sentence = "Man muss sich jedes Mal wieder zur Wahl stellen."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_ueberzeugen_lassen_cycle_10_regression() -> None:
    """docs/audits/cycle-10-corpus-report.md's third reported item: "...
    lassen Sie sich von der knusprigen Textur überzeugen." was already
    correctly Accusative before this task (the governing-verb lookup
    resolves to "lassen", not "überzeugen", which the corpus lexicon
    confirms is itself genuinely mixed-government and so correctly leaves
    unforced -- see ``data/fixtures/verb_government/lexicon.v1.jsonl``'s
    own "lassen" record) -- pinned here so TODO 8.1's wiring change is
    confirmed not to have disturbed it."""
    sentence = "... lassen Sie sich von der knusprigen Textur überzeugen."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


def test_verben_reflexiv_akk_routes_sich_verbeugen_leftover_from_8_1() -> None:
    """TODO.md 8.1's own leftover, never fixed in that pass: "Tom verbeugte
    sich und küsste Maria die Hand." still landed in ``verben_reflexiv_dat``
    -- the corpus lexicon (TODO 8.1) reaches zero usable evidence for
    "verbeugen" at all (confirmed by grepping both staged corpora directly),
    so it fell through to the structural fallback, which had no forced
    verdict either. Fixed by adding "verbeugen" to
    ``paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS`` by name, per this task's
    own instruction -- legitimate here, not the list treadmill TODO 8.1
    retired, since it is one verb named by a hand audit with confirmed zero
    corpus evidence, sourced the same way (Dreyer/Schmitt, Duden) every
    other member of that list already is."""
    sentence = "Tom verbeugte sich und küsste Maria die Hand."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.prompt == "Tom verbeugte ___ und küsste Maria die Hand."
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


# ==============================================================================
# Cycle 3. relativsatz_nom_akk / _dativ / _genitiv -- relative pronoun by case.
# ==============================================================================


def test_relativsatz_nom_akk_finds_nominative_and_accusative_relative_pronouns() -> None:
    nom = _blank("relativsatz_nom_akk", "Der Mann, der dort steht, ist mein Onkel.")
    assert nom.prompt == "Der Mann, ___ dort steht, ist mein Onkel."
    assert nom.proposed_answer == "der"
    akk = _blank("relativsatz_nom_akk", "Das Buch, das ich lese, ist spannend.")
    assert akk.proposed_answer == "das"


def test_relativsatz_nom_akk_rejects_an_article_inside_an_infinitive_clause() -> None:
    """docs/audits/cycle-10-corpus-report.md 8.6: "Seit über einem
    Jahrzehnt fordern Experten und Strategen, die US-Seltene-Erden-
    Industrie wiederzubeleben, um die Abhängigkeit von China zu
    verringern." -- "die" is tagged ``PRELS`` (the relative-pronoun tag),
    but it is an ordinary accusative article governing "Industrie" inside
    an INFINITIVE clause ("wiederzubeleben"), not a relative pronoun at
    all: there is no antecedent for it and no finite verb anywhere in its
    own clause. German has no infinitival relative clause, so requiring a
    genuine (non-infinitival) verb in the clause the pronoun introduces
    closes this."""
    sentence = (
        "Seit über einem Jahrzehnt fordern Experten und Strategen, die "
        "US-Seltene-Erden-Industrie wiederzubeleben, um die Abhängigkeit von China "
        "zu verringern."
    )
    _, candidates = _select("relativsatz_nom_akk", sentence)
    assert candidates == []


def test_relativsatz_nom_akk_still_fires_when_the_tagger_mistags_the_own_verb_non_finite() -> None:
    """The negative counterpart of the 8.6 fix, confirmed necessary while
    writing it: this tagger mistags a GENUINE relative-clause verb as
    non-finite far more often than the module's own tagger-accuracy audit
    would suggest -- "die im Garten spielen" tags "spielen" ``VVINF``
    (3rd-plural present, spelled identically to the infinitive), not
    ``VVFIN``. A ``VerbForm=="Fin"``-only fix for 8.6 would have wrongly
    rejected this genuine relative clause; the fix instead checks for the
    ABSENCE of a zu-infinitive marking, not the PRESENCE of ``VerbForm=
    Fin``, which still correctly finds "spielen" as this clause's own
    qualifying verb."""
    item = _blank("relativsatz_nom_akk", "Ich mag die Kinder, die im Garten spielen.")
    assert item.prompt == "Ich mag die Kinder, ___ im Garten spielen."
    assert item.proposed_answer == "die"


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


def test_verb_praesens_vokalwechsel_excludes_first_person_forms() -> None:
    """docs/audits/cycle-06-report.md class D: German's present-tense stem-
    vowel change only surfaces in 2nd/3rd person singular ("du isst",
    "er isst") -- 1st singular and every plural form of a vokalwechsel verb
    is spelled exactly like a regular verb ("ich esse", "wir essen"), so
    blanking one of those cells would exercise nothing this topic is about.
    7 of 8 live-pilot items audited were exactly this defect."""
    for sentence in (
        "Mittags esse ich meistens ein leckeres Sandwich.",
        "Wir essen oft Pizza am Freitag.",
        "Morgen helfe ich meinem besten Freund beim Umzug.",
    ):
        _, candidates = _select("verb_praesens_vokalwechsel", sentence)
        assert candidates == [], f"{sentence!r} must not yield a vokalwechsel candidate"


def test_verb_praesens_vokalwechsel_still_finds_the_second_and_third_singular() -> None:
    """The positive counterpart of the test above: the SAME family's
    2nd/3rd-singular cell, where the vowel change actually shows, must
    still be selected."""
    assert _blank("verb_praesens_vokalwechsel", "Du liest zu wenig.").proposed_answer == "liest"
    assert (
        _blank("verb_praesens_vokalwechsel", "Er hilft mir oft beim Umzug.").proposed_answer
        == "hilft"
    )


def test_verb_praesens_regelm_excludes_a_mislemmatised_modal_konjunktiv_ii_form() -> None:
    """Live-pilot defect (docs/audits/cycle-06-modal-leak.md): "solltet" is
    Konjunktiv II of the MODAL "sollen", but de_core_news_sm mislemmatises
    it to "sollten" (itself an inflected Präteritum/Konjunktiv-II form, not
    an infinitive) and simultaneously mistags it ``VVFIN``/Tense=Pres/
    Mood=Ind -- both wrong, and self-consistently so, so it used to slip
    past the modal exclusion (keyed on the infinitives "sollen" etc., which
    "sollten" is not) straight into this topic. "sollten" is one of
    ``paradigms.IRREGULAR_FINITE_INFLECTED_FORMS`` (an inflected form of
    "sollen" no genuine infinitive could ever coincide with), which is what
    now catches it."""
    for sentence in (
        "Ihr solltet bei diesem Sturm besser zuhause bleiben.",
        "Ihr solltet eure Ideen offener im Team teilen.",
        "Ihr solltet eure Passwörter regelmäßig ändern.",
    ):
        _, candidates = _select("verb_praesens_regelm", sentence)
        assert candidates == [], f"{sentence!r} must not yield a verb_praesens_regelm candidate"


def test_verb_praesens_regelm_excludes_a_hallucinated_non_verb() -> None:
    """docs/audits/cycle-06-report.md task 2's confirmed live example:
    "treue" is not a German verb at all, but de_core_news_sm tags it
    ``VVFIN``, ``Person=1|Number=Sing``, agreeing with "ich" -- every
    STRUCTURAL check in this module passes it, and even the dictionary check
    ``_cue_is_real_word`` uses does too ("treuen", the tagger's own wrong
    lemma, is a real word -- the dative-plural inflection of the adjective
    "treu" -- just never as a verb). ``_lexical_verb_answer_is_plausible``
    catches it instead: the regularly-constructed "-t" form ("treut") is not
    attested anywhere in the vendored frequency list, unlike a genuine verb's
    own always-regular 2nd-plural-shaped cell."""
    _, candidates = _select(
        "verb_praesens_regelm",
        "Nach der Arbeit treue ich mich mit Lisa auf einen Kaffee in der Stadt.",
    )
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
    """ "entsprechen" inherits "sprechen"'s own stem-vowel change unchanged --
    "er entspricht" exactly like "er spricht".

    Deliberately 3rd singular, not 1st ("ich entspreche"/"ich verlasse"):
    docs/audits/cycle-06-report.md class D means this selector now requires
    the blanked cell to actually SHOW the vowel change (see
    ``_select_verb_praesens_vokalwechsel``'s own docstring), which 1st
    singular never does. A 1st-singular inseparable example ("verlasse")
    was this test's original carrier before that fix; the base verb changed
    from "verlassen" to "entsprechen" as well because ``de_core_news_sm``'s
    lemmatiser -- confirmed directly -- only reduces "verlässt" back to its
    infinitive incorrectly (it stays "verlässt", not "verlassen"), so no
    3rd-singular "verlassen" sentence can pass this selector's own,
    unrelated lemma-membership check at all; "entspricht" lemmatises
    correctly, so it exercises the SAME inseparable-prefix inheritance this
    test has always been about, on a cell that is actually in scope now."""
    item = _blank(
        "verb_praesens_vokalwechsel",
        "Er entspricht genau meinen Erwartungen.",
    )
    assert item.proposed_answer == "entspricht"


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


def test_perfekt_sein_does_not_reach_into_an_unrelated_clauses_participle() -> None:
    """Found while verifying the TODO.md 8.4 VAPP fix against the corpus,
    not part of the reported defect itself: ``_select_perfekt`` used the
    unbounded ``_participle_after``, not the clause-bounded ``_in_clause``
    variant every other participle-reading selector in this module
    (Plusquamperfekt, Konjunktiv II Vergangenheit, the passives) already
    uses. This almost never mattered before, because a present-tense
    Perfekt's own participle is overwhelmingly in the SAME clause as its
    aux -- until ``_is_participle`` learned to recognise ``VAPP`` ("gehabt",
    "gewesen", "geworden" -- an auxiliary-class lemma's own Partizip II),
    which gave the unbounded search a NEW token to wrongly reach for the
    first time. "Ich bin erstaunt, dass es heute so warm geworden ist." has
    no Perfekt at all: "bin" is an ordinary present-tense copula with a
    predicate adjective ("erstaunt"), and "geworden" belongs to the
    UNRELATED "dass"-clause's own "ist". Confirmed empirically against this
    exact sentence (a genuine corpus item, not synthesised) before writing
    the clause-bounding fix, not guessed."""
    _, candidates = _select("perfekt_sein", "Ich bin erstaunt, dass es heute so warm geworden ist.")
    assert candidates == []


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


def test_konjunktiv_ii_hoeflichkeit_does_not_fire_on_a_past_ersatzinfinitiv_question() -> None:
    """A bonus finding while verifying the TODO.md 8.4 Ersatzinfinitiv fix
    against the corpus, not one of the three reported items itself:
    "Hätte die Polizei die Morde verhindern können?" is a question-shaped
    sentence with no "wenn", which used to satisfy every one of this
    topic's own gates and land here as if it were a polite request ("Could
    you help me?") -- but it is asking about PAST ability ("Could the
    police HAVE prevented the murders?"), the exact same Ersatzinfinitiv
    shape ("hätte ... verhindern können") as the three reported items, just
    in a question. The fix generalises correctly: it lives in the shared
    ``_select_konjunktiv_ii_base``, so it closes this shape for
    ``konjunktiv_ii_hoeflichkeit`` too, not only for
    ``konjunktiv_ii_irreal_gegenwart``."""
    _, candidates = _select(
        "konjunktiv_ii_hoeflichkeit", "Hätte die Polizei die Morde verhindern können?"
    )
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


def test_konjunktiv_ii_irreal_gegenwart_does_not_fire_on_an_indicative_wenn_clause() -> None:
    """docs/audits/cycle-06-report.md class F, item 1: "wenn Sie unsere
    Fragen beantworten" is a plain present-indicative "wenn" clause, used
    inside a "wir würden uns freuen, wenn ..." polite-request formula, not a
    hypothetical condition -- the topic this belongs to is
    konjunktiv_ii_hoeflichkeit, not this one."""
    _, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart",
        "Wir würden uns freuen, wenn Sie unsere Fragen beantworten.",
    )
    assert candidates == []


def test_konjunktiv_ii_irreal_gegenwart_does_not_fire_on_a_wenn_clause_with_koennte() -> None:
    """docs/audits/cycle-06-report.md class F, item 2: "wenn Sie mir dabei
    helfen könnten" IS grammatically Konjunktiv II throughout ("könnten" is
    tagged Mood=Sub, confirmed directly), unlike item 1 above -- so "the
    wenn-clause has SOME Konjunktiv II verb" is not by itself enough to
    distinguish this from politeness. "können"/"werden" are exactly the two
    lemmas konjunktiv_ii_hoeflichkeit's own rule_hint names as its
    politeness markers ("könnten Sie", "würden Sie"), so a wenn-clause whose
    only Konjunktiv II verb is one of those two must not license this
    topic either."""
    _, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart",
        "Ich würde mich sehr freuen, wenn Sie mir dabei helfen könnten.",
    )
    assert candidates == []


def test_konjunktiv_ii_irreal_gegenwart_still_fires_on_a_genuine_hypothetical_state() -> None:
    """The positive counterpart of the two tests above: a wenn-clause whose
    own Konjunktiv II verb is a genuine hypothetical-state verb ("hätte")
    still licenses this topic, both for the canonical "wenn X, würde Y"
    order and this exact live example fires unchanged from before."""
    _, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart", "Wenn ich Zeit hätte, würde ich kommen."
    )
    assert len(candidates) == 2
    _, sein_candidates = _select(
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich reich wäre, würde ich eine Weltreise machen.",
    )
    assert len(sein_candidates) == 2


def test_konjunktiv_ii_vergangenheit_finds_a_past_counterfactual() -> None:
    """docs/audits/cycle-09-report.md 1.7's own bidirectional-participle fix
    (``_select_konjunktiv_ii_base``/``_select_konjunktiv_ii_vergangenheit``,
    both now checking BEFORE the aux as well as after, clause-bounded) has
    a positive side effect on this exact sentence: "Wenn ich das gewusst
    ___, wäre ich nicht gekommen." has TWO genuine Konjunktiv II
    Vergangenheit slots, not one -- "hätte" (wenn-clause, its own participle
    "gewusst" BEFORE it) and "wäre" (main clause, its own participle
    "gekommen" after it). Before this fix, "hätte" was accidentally
    excluded: the old unbounded, after-only participle search from "hätte"
    reached across the comma into the MAIN clause's "gekommen" and,
    because "gekommen" (a sein-verb) mismatched "hätte"'s own haben-aux,
    was filtered out by what turned out to be a correct exclusion for the
    wrong reason -- masking, not fixing, the missing before-check this task
    closes. Both members are pinned here instead of assuming a single
    candidate."""
    tagged, candidates = _select(
        "konjunktiv_ii_vergangenheit", "Wenn ich das gewusst hätte, wäre ich nicht gekommen."
    )
    surface = {tagged.tokens[c.token_index].text for c in candidates}
    assert surface == {"hätte", "wäre"}


# ==============================================================================
# TODO.md 8.4 / docs/audits/cycle-10-corpus-report.md: three past Konjunktiv
# II items that landed in konjunktiv_ii_irreal_gegenwart instead of
# konjunktiv_ii_vergangenheit.
#
# Two are the Ersatzinfinitiv ("hätte gehen wollen", "hätte gewinnen
# können"): a modal governing another infinitive elides its OWN past
# participle in favour of its bare infinitive, so the clause has no
# participle at all for a participle test to find -- closed by
# ``_ersatzinfinitiv_in_clause``, a new, independent marker.
#
# The third ("gehabt ___", the participle sitting BEFORE the aux in a
# verb-final "wenn"-clause) is the one the task brief asked to be
# diagnosed, not just patched. Root cause, confirmed directly against the
# tagger, not guessed: "gehabt" (the past participle of "haben" used as an
# ordinary content verb, not as an auxiliary) is tagged ``VAPP`` by
# de_core_news_sm, not ``VVPP``. ``_is_participle``'s trustworthy-tag
# branch checked ONLY ``token.tag == "VVPP"`` before this fix, and its
# shape fallback is gated on ``PARTICIPLE_CONFUSABLE_TAGS`` (``VVIZU``
# only, TODO 1.2's own unrelated fix for a different mistagging pattern) --
# ``VAPP`` was a member of neither, so ``_is_participle("gehabt")`` was
# simply ``False``, and cycle 11's clause-bounded bidirectional participle
# search, which correctly reached "gehabt"'s own position, never
# recognised what it found there. There was no deliberate exclusion of an
# auxiliary's own participle anywhere in this module, and the clause
# boundary was already computed correctly -- confirmed by testing both
# alternatives directly (see ``selectors._is_participle``'s own docstring
# for the full trace) before writing this fix, per the task brief's own
# instruction not to guess.
# ==============================================================================


def test_konjunktiv_ii_vergangenheit_finds_the_ersatzinfinitiv_with_a_modal() -> None:
    """ "Wenn ich hätte gehen wollen, hätte ich's gesagt." -- the FIRST
    "hätte" ("hätte gehen wollen") is the Ersatzinfinitiv: two bare
    infinitives, no participle anywhere in its own clause. Cycle 10's own
    reported item blanks exactly this one, not the sentence's second
    "hätte" (an ordinary participle-after case, already correctly
    vergangenheit before this fix)."""
    item = _blank("konjunktiv_ii_vergangenheit", "Wenn ich hätte gehen wollen, hätte ich's gesagt.")
    assert item.prompt == "Wenn ich ___ gehen wollen, hätte ich's gesagt."
    assert item.proposed_answer == "hätte"


def test_konjunktiv_ii_vergangenheit_finds_the_ersatzinfinitiv_in_a_dass_clause() -> None:
    """ "Tom glaubt, dass er das Rennen hätte gewinnen können, wenn er sich
    etwas mehr angestrengt hätte." -- the FIRST "hätte" ("hätte gewinnen
    können") is the Ersatzinfinitiv; the sentence's second "hätte" (wenn-
    clause, participle "angestrengt" before it) is an ordinary, already-
    correct vergangenheit candidate in its own right, pinned separately by
    ``candidate_index=0`` here to confirm which one this test means."""
    item = _blank(
        "konjunktiv_ii_vergangenheit",
        "Tom glaubt, dass er das Rennen hätte gewinnen können, wenn er sich etwas mehr "
        "angestrengt hätte.",
        candidate_index=0,
    )
    assert item.prompt == (
        "Tom glaubt, dass er das Rennen ___ gewinnen können, wenn er sich etwas mehr "
        "angestrengt hätte."
    )
    assert item.proposed_answer == "hätte"


def test_konjunktiv_ii_vergangenheit_finds_an_auxiliarys_own_participle_before_the_aux() -> None:
    """ "Ich wäre gerne ins Kino gegangen, wenn ich die Zeit dazu gehabt
    hätte." -- root cause: "gehabt" is tagged VAPP (an auxiliary-class
    lemma's own Partizip II), not VVPP, so it was invisible to
    ``_is_participle`` before this fix even though the clause-bounded
    backward search already reached it. The sentence's first candidate is
    "wäre" (main clause, "gegangen" after it, unaffected by this fix);
    ``candidate_index=1`` selects "hätte", the one this task's own brief
    asked to be diagnosed."""
    item = _blank(
        "konjunktiv_ii_vergangenheit",
        "Ich wäre gerne ins Kino gegangen, wenn ich die Zeit dazu gehabt hätte.",
        candidate_index=1,
    )
    assert item.prompt == "Ich wäre gerne ins Kino gegangen, wenn ich die Zeit dazu gehabt ___."
    assert item.proposed_answer == "hätte"


def test_konjunktiv_ii_irreal_gegenwart_does_not_fire_on_the_ersatzinfinitiv() -> None:
    """The negative counterpart of the Ersatzinfinitiv fix: neither of the
    two reported Ersatzinfinitiv sentences should produce ANY
    konjunktiv_ii_irreal_gegenwart candidate at all once the past marker is
    recognised."""
    _, candidates_a = _select(
        "konjunktiv_ii_irreal_gegenwart", "Wenn ich hätte gehen wollen, hätte ich's gesagt."
    )
    assert candidates_a == []
    _, candidates_c = _select(
        "konjunktiv_ii_irreal_gegenwart",
        "Tom glaubt, dass er das Rennen hätte gewinnen können, wenn er sich etwas mehr "
        "angestrengt hätte.",
    )
    assert candidates_c == []


def test_konjunktiv_ii_irreal_gegenwart_does_not_fire_on_a_haben_participle() -> None:
    """The negative counterpart of the VAPP fix: "gehabt hätte" must no
    longer be reachable through konjunktiv_ii_irreal_gegenwart at all --
    without the fix, "hätte" survived the (blind) participle exclusion
    check and was wrongly admitted here."""
    _, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart",
        "Ich wäre gerne ins Kino gegangen, wenn ich die Zeit dazu gehabt hätte.",
    )
    assert candidates == []


def test_konjunktiv_ii_irreal_gegenwart_still_fires_without_a_past_marker() -> None:
    """The over-firing risk the task brief named directly: a naive
    double-infinitive or participle-absence rule could swallow a genuine
    present-tense Konjunktiv II built on "hätte" with no participle and no
    Ersatzinfinitiv anywhere -- "Wenn ich Zeit hätte, hätte ich gern ein
    Bier." has neither, and both of its "hätte"s must survive as
    konjunktiv_ii_irreal_gegenwart candidates, unchanged from before this
    fix. "Er könnte kommen, wenn er wollte." is the brief's own second
    named example: a single bare infinitive after a modal is not the
    Ersatzinfinitiv's own two-infinitive shape, so it is untouched by the
    ``lemma == "haben"``-gated exclusion either way."""
    _, candidates = _select(
        "konjunktiv_ii_irreal_gegenwart", "Wenn ich Zeit hätte, hätte ich gern ein Bier."
    )
    assert len(candidates) == 2
    _, koennte_candidates = _select(
        "konjunktiv_ii_irreal_gegenwart", "Er könnte kommen, wenn er wollte."
    )
    assert koennte_candidates == []


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


# TODO.md 8.3 / docs/audits/cycle-10-corpus-report.md: "Ich werde nie
# vergessen, wie ich dich zum ersten Mal gesehen habe." and its twin read
# as passiv_praesens only because "vergessen"'s infinitive and past
# participle are spelled identically. General rule, not a special case for
# "vergessen": a German passive cannot take an accusative object, and the
# "wie ..."-clause here is that object -- gated on the participle's own
# surface being identical to its infinitive (the only shape this ambiguity
# can arise for at all), so an ordinary, unambiguous participle candidate
# is never at risk of the checks below.
def test_passiv_praesens_rejects_vergessen_taking_an_embedded_question_object() -> None:
    sentence_a = "Ich werde nie vergessen, wie ich dich zum ersten Mal gesehen habe."
    _, candidates_a = _select("passiv_praesens", sentence_a)
    assert candidates_a == []
    sentence_b = "Ich werde nie vergessen, wie ich mit ihr Hawaii besucht habe."
    _, candidates_b = _select("passiv_praesens", sentence_b)
    assert candidates_b == []


def test_passiv_praesens_rejects_a_syncretic_participle_with_ordinary_accusative() -> None:
    """The same general rule, an ordinary noun-phrase object rather than an
    embedded-question clause: "seinen Geburtstag" is a genuine same-clause
    accusative object, so this is Futur I ("he will forget his birthday"),
    not a passive ("he will be forgotten"). "vergessen" is, per
    ``paradigms.TRANSITIVE_LEMMAS``, currently the only verb this
    selector's own syncretic-participle gate can reach at all (confirmed
    directly: no other member of that closed list has a participle
    identical to its own infinitive) -- the CHECK itself is general (the
    same mechanism the module comment above ``_clause_takes_accusative_
    object`` describes for "bekommen"/"erhalten"/"verlassen"), only its
    current real-world reach is narrow, and that narrowness is reported
    honestly rather than tested against a verb the selector cannot
    currently reach."""
    _, candidates = _select("passiv_praesens", "Er wird seinen Geburtstag vergessen.")
    assert candidates == []


def test_passiv_praesens_still_accepts_a_syncretic_participle_with_a_dative() -> None:
    """Guards the false-rejection risk this task's own brief named
    directly for a Dative co-occurring with a genuine passive. "Das wird
    dir nie vergessen." is the real, idiomatic German construction
    ("jemandem etwas nicht vergessen" -- to hold something against/never
    forget of someone) in its genuine passive form: "dir" is Dative, never
    counted by ``_clause_takes_accusative_object`` (which only ever checks
    ``Case=="Acc"``), so this must still be accepted."""
    item = _blank("passiv_praesens", "Das wird dir nie vergessen.")
    assert item.proposed_answer == "wird"


def test_passiv_praesens_known_residual_dass_clause_object_of_vergessen_not_covered() -> None:
    """An honest, documented residual, not a silent gap: "Er wird nie
    vergessen, dass er sie liebt." has the identical ambiguity as the two
    reported "wie"-clause items (a "dass"-clause is just as much
    "vergessen"'s own sole accusative-object argument here), but "dass" is
    deliberately excluded from ``_EMBEDDED_QUESTION_INTRODUCER_LEMMAS`` --
    see that set's own comment for why (a "dass"-clause is also how a
    ditransitive-shaped verb like "informieren" keeps a genuine SEPARATE
    complement alongside an already-passivised accusative person, and this
    task's own two reported items are both "wie", never "dass"). Pinned
    here as a known, deliberately out-of-scope limitation rather than left
    undocumented."""
    item = _blank("passiv_praesens", "Er wird nie vergessen, dass er sie liebt.")
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


def test_zustandspassiv_rejects_a_copula_plus_adjective_with_no_passive_at_all() -> None:
    """docs/audits/cycle-07-report.md defect 5: "Es ist sehr wichtig, dass
    die Suppe langsam gekocht wird." -- "ist" here is a plain copula in
    front of a genuine adjective ("wichtig"), not the "sein" auxiliary of a
    Zustandspassiv; there is no participle following it at all. Must not
    be mistaken for zustandspassiv just because "sein" appears somewhere
    in the sentence (the actual passive here is Vorgangspassiv, "wird
    gekocht", an entirely different construction with its own topic)."""
    _, candidates = _select(
        "zustandspassiv", "Es ist sehr wichtig, dass die Suppe langsam gekocht wird."
    )
    assert candidates == []


def test_zustandspassiv_rejects_a_vorgangspassiv_perfekt_with_worden() -> None:
    """docs/audits/cycle-07-report.md defect 7: "...dass ein wichtiges
    Ereignis angekündigt worden ist..." -- this is Vorgangspassiv in the
    Perfekt ("worden ist", the auxiliary-of-the-passive-auxiliary
    construction), not Zustandspassiv, even though "ist" is immediately
    followed by a genuine transitive past participle ("angekündigt") in
    its own clause. The presence of "worden" anywhere in the clause is
    what distinguishes the two; must reject rather than mistake this for a
    present-tense Zustandspassiv reading of "ist"."""
    _, candidates = _select(
        "zustandspassiv",
        "Manchmal erfahren Sie erst spät, dass ein wichtiges Ereignis angekündigt "
        "worden ist, obwohl wir sofort darüber geschrieben haben.",
    )
    assert candidates == []


def test_zustandspassiv_rejects_the_perfekt_of_a_sein_motion_verb() -> None:
    """docs/audits/cycle-10-corpus-report.md 8.2: "Es ist fünf Jahre her,
    dass wir hierher gezogen sind." and "Ich kann mich nicht erinnern,
    wann er nach Boston gezogen ist." are both the Perfekt of "ziehen"
    (moved house), not Zustandspassiv -- "ziehen" clears
    ``TRANSITIVE_LEMMAS`` (it genuinely is transitive in its OTHER reading,
    "einen Wagen ziehen"), so a transitivity test alone cannot decide it.
    "ziehen" is deliberately NOT added to the shared ``paradigms.
    AUX_SEIN_LEMMAS`` for this fix (see ``_ZUSTANDSPASSIV_PERFEKT_MIT_
    SEIN_LEMMAS``'s own module comment) -- only a LOCAL extension used by
    the two Zustandspassiv selectors."""
    _, candidates_a = _select(
        "zustandspassiv", "Es ist fünf Jahre her, dass wir hierher gezogen sind."
    )
    assert candidates_a == []
    _, candidates_b = _select(
        "zustandspassiv",
        "Ich kann mich nicht erinnern, wann er nach Boston gezogen ist.",
    )
    assert candidates_b == []


def test_zustandspassiv_still_finds_a_genuine_transitive_participle() -> None:
    """The negative counterpart of the 8.2 fix: an ordinary Zustandspassiv
    of a verb NOT in ``AUX_SEIN_LEMMAS`` at all must still fire, unaffected
    by the new exclusion."""
    item = _blank("zustandspassiv", "Das Auto ist repariert.")
    assert item.proposed_answer == "ist"


def test_perfekt_haben_still_accepts_ziehen_in_its_transitive_reading() -> None:
    """Confirms the 8.2 fix did NOT add "ziehen" to the shared
    ``paradigms.AUX_SEIN_LEMMAS`` -- ``_select_perfekt``'s own haben/sein
    branch (``paradigms.py``'s own module comment above ``AUX_SEIN_
    LEMMAS``) relies on that set staying disjoint from ``TRANSITIVE_
    LEMMAS`` to keep a genuine transitive, haben-Perfekt "ziehen" ("to
    pull/tow") correctly routed to ``perfekt_haben``, not silently
    rerouted to ``perfekt_sein`` by a shared-set change made for an
    unrelated selector."""
    item = _blank("perfekt_haben", "Ich habe den Wagen gezogen.")
    assert item.proposed_answer == "habe"
    _, sein_candidates = _select("perfekt_sein", "Ich habe den Wagen gezogen.")
    assert sein_candidates == []


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
# TODO.md 1.2's own caveat: de_core_news_sm sometimes tags a separable-prefix
# past participle ("angebraten", "aufgeladen") ``VVIZU`` (the zu-infinitive
# tag) instead of ``VVPP`` in exactly the passive construction every
# participle-reading selector searches for. Confirmed at scale in
# docs/audits/corpus-coverage.md: passiv_praesens/passiv_praeteritum were
# two of the LOWEST-yield topics out of 49 on both corpora despite the
# present passive being one of the commonest constructions in German.
#
# Fixed with a shape-based participle test (``paradigms.is_participle_
# shape``, wired in through ``selectors._is_participle``) that does not
# trust the tagger's opinion when the fact is derivable from the word's own
# spelling -- a known separable prefix, plus "ge", plus a participle ending,
# is a past participle regardless of tag, and the "zu" infix (never "ge") is
# what tells a genuine zu-infinitive apart from it. Applied to every
# selector that reads through ``_participle_after``/``_before`` (and their
# ``_in_clause`` variants) or ``futur_i``'s own inline participle exclusion:
# passiv_praesens, passiv_praeteritum, passiv_modalverben, zustandspassiv,
# zustandspassiv_zeiten, perfekt_haben, perfekt_sein, plusquamperfekt,
# konjunktiv_ii_vergangenheit, futur_i, futur_ii. NOT
# partizip_ii_attributiv/partizip_ii_attributiv_erweitert -- confirmed
# empirically that an ATTRIBUTIVE separable-prefix participle ("das
# gebratene Fleisch", "der aufgeladene Akku") is tagged ``ADJA``, not
# ``VVIZU``, by this exact tagger, so that pair never hits the mistagging
# this fix closes.
# ==============================================================================


def test_passiv_praesens_finds_a_separable_prefix_participle_mistagged_vviz() -> None:
    """The two exact TODO.md 1.2 sentences. Both were a known, documented
    miss until this fix; both must now produce a real item."""
    item = _blank(
        "passiv_praesens",
        "Es muss unbedingt beachtet werden, dass das Fleisch scharf "
        "angebraten wird, bevor man es serviert.",
    )
    assert item.proposed_answer == "wird"
    assert "angebraten" in item.prompt

    item = _blank(
        "passiv_praesens",
        "Das Smartphone wird jetzt aufgeladen, damit Sie es am Abend sofort nutzen können.",
    )
    assert item.prompt == (
        "Das Smartphone ___ jetzt aufgeladen, damit Sie es am Abend sofort nutzen können."
    )
    assert item.proposed_answer == "wird"


def test_passiv_praesens_finds_a_subordinate_clause_passive_participle_before_wird() -> None:
    """A second, distinct gap the first reported sentence also exposed:
    ``_select_passiv`` searched forward-only, unbounded, for its
    participle -- which finds nothing in a verb-final subordinate clause
    ("..., dass das Fleisch scharf angebraten wird, ...") where the
    participle sits BEFORE "wird", not after it, exactly the word-order fact
    ``_select_plusquamperfekt``/``_select_konjunktiv_ii_vergangenheit``
    already had to handle for their own aux. Isolated here from the tagging
    fix above with a plain, correctly-VVPP-tagged participle, so this test
    fails on the old forward-only search even without any ``VVIZU``
    mistagging in the picture at all."""
    item = _blank(
        "passiv_praesens", "Der Kellner sagt, dass die Suppe jeden Abend frisch gekocht wird."
    )
    assert item.proposed_answer == "wird"
    assert item.prompt.endswith("gekocht ___.")


def test_passiv_praesens_does_not_claim_a_modal_passives_own_clause_final_werden() -> None:
    """A defect the bidirectional fix above would otherwise introduce,
    found by hand-checking newly admitted corpus items for this task:
    confirmed empirically that de_core_news_sm sometimes tags a modal
    passive's own clause-final "werden" ("... können keine Häuser gebaut
    werden.") ``VAFIN`` (finite) even though it is really the INVARIANT
    bare infinitive "können" governs, not a form that agrees with its
    subject. The old forward-only participle search never found this shape
    (the participle sits BEFORE the clause-final "werden"), so it was
    invisible before the bidirectional fix; blanking it as if it inflected
    for Person/Number would build a broken item. ``passiv_modalverben``
    already owns this construction (it blanks the modal itself, not
    "werden") -- though the same ``VAFIN`` mistag also happens to defeat
    ``passiv_modalverben``'s own infinitive-tag check on this exact
    sentence, so this asserts only what this fix is actually responsible
    for: passiv_praesens must not wrongly claim it, not that some other
    topic picks it up instead."""
    _, candidates = _select(
        "passiv_praesens", "In diesem Bereich können keine Häuser gebaut werden."
    )
    assert candidates == []


def test_futur_i_participle_exclusion_does_not_mistake_a_genuine_zu_infinitiv_for_one() -> None:
    """The discriminator ``is_participle_shape`` must get right: "zu" is
    infixed, not "ge" -- "aufzuladen" (confirmed tagged ``VVIZU`` in this
    exact clause, same tag as the mistagged participle case this fix
    targets) is a genuine zu-infinitive of the same separable verb
    ("aufladen") as the reported participle ("aufgeladen"), never a
    participle. A false accept here would wrongly exclude a genuine Futur I
    clause built on this exact verb."""
    item = _blank("futur_i", "Er wird das Handy aufzuladen versuchen.")
    assert item.proposed_answer == "wird"


def test_perfekt_haben_finds_a_separable_prefix_participle_mistagged_vvizu() -> None:
    item = _blank("perfekt_haben", "Sie hat das Gemüse schon eingepackt.")
    assert item.proposed_answer == "hat"


def test_plusquamperfekt_finds_a_separable_prefix_participle_mistagged_vvizu() -> None:
    item = _blank(
        "plusquamperfekt",
        "Nachdem sie das Fleisch scharf angebraten hatte, servierte sie es sofort.",
    )
    assert item.proposed_answer == "hatte"


def test_zustandspassiv_finds_a_separable_prefix_participle_mistagged_vvizu() -> None:
    item = _blank("zustandspassiv", "Das Handy ist schon aufgeladen.")
    assert item.proposed_answer == "ist"


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


def test_futur_i_does_not_fire_on_a_present_passive() -> None:
    """docs/audits/cycle-09-report.md 1.2: "werden" plus a past participle is
    the present passive (Vorgangspassiv), not Futur I -- the old check
    accepted it merely because no participle happened to follow "wird"
    (Futur II's own shape), never checking whether a genuine INFINITIVE was
    actually present either. Fixed by requiring a real infinitive
    (``_MODAL_INFINITIVE_TAGS``) in the clause and rejecting outright if any
    participle (``_is_participle``: tag OR shape) shares it, closing the gap
    that also starved ``passiv_praesens`` (both are one selector bug)."""
    item = _blank("futur_i", "Ich werde morgen ins Kino gehen.")
    assert item.proposed_answer == "werde"
    _, candidates = _select("futur_i", "Die Suppe wird jeden Tag frisch gekocht.")
    assert candidates == []
    passiv_item = _blank("passiv_praesens", "Die Suppe wird jeden Tag frisch gekocht.")
    assert passiv_item.proposed_answer == "wird"

    # The exact two reported sentences: futur_i no longer wrongly claims
    # either, and -- updated from the previous version of this test, per
    # CLAUDE.md rule 7 -- ``passiv_praesens`` now DOES produce an item for
    # both. Previously it did not: both participles are separable-prefix
    # verbs ("anbraten", "aufladen") that de_core_news_sm mistags ``VVIZU``
    # instead of ``VVPP`` in this exact context (confirmed directly), which
    # is the tag ``passiv_praesens``'s own participle search required and a
    # gap a later task (the ``VVIZU`` shape-based participle test,
    # ``paradigms.is_participle_shape``) closed.
    for sentence in (
        "Das Fleisch wird scharf angebraten, wenn ein tolles Aroma entstehen soll.",
        "Das Smartphone wird jetzt aufgeladen, damit Sie es am Abend sofort nutzen können.",
    ):
        _, futur_candidates = _select("futur_i", sentence)
        assert futur_candidates == []
        passiv_item = _blank("passiv_praesens", sentence)
        assert passiv_item.proposed_answer == "wird"


def test_futur_ii_finds_werden_plus_participle_plus_haben() -> None:
    item = _blank("futur_ii", "Er wird das Buch gelesen haben.")
    assert item.proposed_answer == "wird"


def test_futur_ii_rejects_a_present_passive_with_a_coordinated_second_clause() -> None:
    """docs/audits/cycle-10-corpus-report.md 8.9: "werden" plus a participle
    used to be enough for futur_ii even when no "haben"/"sein" infinitive
    ever followed -- here the participle ("verkauft") belongs to an ordinary
    present passive, and the sentence's real "sein"-infinitive-looking word
    ("zu haben") belongs to a second, "und"-coordinated clause with its own
    subject ("ist ... zu haben"), not to the first clause's "wird" at all.
    Fixed by requiring the full shape (werden + participle + haben/sein
    infinitive) inside the same clause, additionally stopping the forward
    search at the first coordinating conjunction because ``_clause_span``'s
    comma-only boundary does not see "und" as a boundary on its own."""
    sentence = (
        "Die Konstruktion wird nach dem Fest an den Meistbietenden verkauft "
        "und ist ab 1,5 Millionen Franken zu haben."
    )
    _, candidates = _select("futur_ii", sentence)
    assert candidates == []
    passiv_item = _blank("passiv_praesens", sentence)
    assert passiv_item.proposed_answer == "wird"


def test_futur_ii_still_finds_a_genuine_case_with_a_coordinated_second_clause() -> None:
    """Negative counterpart of the 8.9 fix: a real Futur II (werden +
    participle + haben/sein infinitive, all before any "und") must still be
    found even when a second, unrelated clause is coordinated after it."""
    item = _blank(
        "futur_ii",
        "Er wird das Buch gelesen haben, und sie wird es auch bald lesen.",
    )
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


def test_partizip_i_attributiv_cue_is_the_underlying_infinitive() -> None:
    """docs/audits/cycle-06-modal-leak.md: the topic's own ``eligible_types``
    is ``[cloze_cued, transformation]``, never ``cloze_free`` -- without a
    cue, any attributive participle-shaped adjective would fit the slot."""
    item = _blank("partizip_i_attributiv", "Das schlafende Kind liegt im Bett.")
    assert item.proposed_answer == "schlafende"
    assert item.cue == "schlafen"
    assert item.type == "cloze_cued"


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


def test_partizip_ii_attributiv_erweitert_cue_is_the_underlying_infinitive() -> None:
    """docs/audits/cycle-06-modal-leak.md: same reasoning as
    ``partizip_i_attributiv``'s own cue test -- the topic's own
    ``eligible_types`` is ``[cloze_cued]`` alone."""
    item = _blank(
        "partizip_ii_attributiv_erweitert", "Das von Experten entwickelte Programm ist erfolgreich."
    )
    assert item.proposed_answer == "entwickelte"
    assert item.cue == "entwickeln"
    assert item.type == "cloze_cued"


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
    # A relative clause right after "Mann" is added purely to give
    # ``artikel_bestimmt_nom`` the uniqueness anchor its own selector now
    # requires (see that topic's section above); it does not disturb either
    # adjective-declension reading or the accusative object.
    sentence = "Der alte Mann, der hier wohnt, trinkt einen starken Kaffee."
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


# ==============================================================================
# Cue generation (docs/audits/cycle-04-report.md recommendation 5, and its
# headline finding): a bracketed citation-form cue for the three topics the
# uniqueness gate would otherwise always skip as interchangeable --
# ``nomen_plural`` (singular citation form), ``modalverben_praesens`` and
# ``passiv_modalverben`` (the modal's own infinitive). ``Candidate.cue`` is
# additive and not read by ``blanker.py``/``blank_candidate`` in this cycle,
# so these tests go straight at the selector's own ``Candidate`` output.
# ==============================================================================


def test_nomen_plural_cue_is_the_singular_citation_form() -> None:
    """docs/audits/cycle-04-report.md's own example: "meine ___" (Zähne)
    would otherwise accept Hände/Schuhe/Haare too -- "(Zahn)" rescues it."""
    _, candidates = _select("nomen_plural", "Nach dem Frühstück putze ich gründlich meine Zähne.")
    assert len(candidates) == 1
    assert candidates[0].cue == "Zahn"


def test_nomen_plural_cue_is_none_for_a_genuinely_invariant_plural() -> None:
    """ "Lehrer" is spelled identically in the singular and the plural -- a
    cue here would hand over the answer, so ``_citation_cue`` withholds it
    rather than leak (rule: the cue must never equal the answer)."""
    _, candidates = _select("nomen_plural", "Die Lehrer unterrichten Mathematik.")
    assert len(candidates) == 1
    assert candidates[0].cue is None


def test_nomen_plural_cue_is_none_when_it_does_not_round_trip_to_the_answer() -> None:
    """docs/audits/cycle-07-report.md defect 1: "die neuen Tabletten" --
    ``de_core_news_sm`` lemmatises "Tabletten" to "Tablett" (the wrong
    lexeme: a tablet/tray, singular "das Tablett", not "die Tablette"), so
    the bracketed cue would show the learner a citation form that does not
    even belong to the same word as the answer. ``_plural_noun_cue`` must
    round-trip the cue lemma through ``plausible_noun_plurals`` and require
    it to reproduce the actual answer text before offering it; "Tablett"
    does not plausibly pluralise to "Tabletten" (that would need a bare
    "-en" ending on a consonant-final lemma, a plural class excluded on
    purpose), so the cue is withheld rather than shown wrong."""
    _, candidates = _select(
        "nomen_plural", "Im Wohnzimmer erklärt sie der Nachbarin die neuen Tabletten."
    )
    assert len(candidates) == 1
    assert candidates[0].cue is None


def test_nomen_plural_cue_is_none_for_a_dative_plural() -> None:
    """Confirmed empirically that ``de_core_news_sm`` mislemmatises a
    Dative plural of this noun class ("Müttern" -> "Müttern", entirely
    unreduced, not "Mutter") -- see ``_plural_noun_cue``'s own docstring.
    The candidate itself is still produced (the topic is unaffected), only
    the cue is withheld."""
    _, candidates = _select("nomen_plural", "Sie dankten den Müttern für alles.")
    assert len(candidates) == 1
    assert candidates[0].cue is None


# ==============================================================================
# docs/audits/cycle-06-report.md class A: a cue must resolve to a real
# German word, checked against the vendored dictionary
# (``data/fixtures/corpus/frequency/de_dictionary_filter.txt``), rather than
# extending ``_MISLEMMATIZED_VERB_LEMMAS`` one confirmed-bad lemma at a
# time. Each test below reproduces the mechanism behind one of the four
# nonsense cues the audit reported live, then confirms it degrades to no
# cue rather than either leaking the nonsense or dropping the item's own
# grammar-topic classification.
# ==============================================================================


def test_praeteritum_vollverben_cue_is_none_for_the_mislemmatised_modal_mussten() -> None:
    """Live-pilot defect: "..., dass wir die Reise stornieren mussten." --
    de_core_news_sm mislemmatises "mussten" (müssen's own Präteritum) to
    "mussen" (missing the umlaut, and reconstructing the exact right
    surface form by coincidence, the same self-consistent-bug shape as
    every other entry in ``_MISLEMMATIZED_VERB_LEMMAS``). "mussen" is not
    itself in that hand list, and it is not the correctly-spelled "müssen"
    either, so the modal exclusion this selector's own docstring describes
    does not catch it: the token is still selected as a (mislabelled)
    ``praeteritum_vollverben`` candidate, but the dictionary check must
    withhold its cue -- "mussen" is not a real German word."""
    _, candidates = _select(
        "praeteritum_vollverben", "Sie hatte gesagt, dass wir die Reise stornieren mussten."
    )
    assert len(candidates) == 1
    assert candidates[0].lemma == "mussen"
    assert candidates[0].cue is None


def test_verben_trennbar_praesens_cue_is_none_for_the_mislemmatised_packsen() -> None:
    """Live-pilot defect: "Morgen früh ___ du deine Schultasche ein." --
    de_core_news_sm mislemmatises "packst" to "packsen" (a phantom "s"
    inside the stem, the same failure shape as the already-documented
    "frühstücksen"/"rufsen"/"streichsen"), which the dictionary check now
    catches generically instead of needing "packsen" added to
    ``_MISLEMMATIZED_VERB_LEMMAS`` by hand."""
    _, candidates = _select(
        "verben_trennbar_praesens", "Morgen früh packst du deine Schultasche ein."
    )
    assert len(candidates) == 1
    assert candidates[0].lemma == "packsen"
    assert candidates[0].cue is None


def test_nomen_plural_cue_is_none_for_the_truncated_plastiktuet() -> None:
    """Live-pilot defect: "...beim Einkaufen auf ___ zu verzichten." --
    de_core_news_sm's own lemma for "Plastiktüten" is "Plastiktüt", missing
    the trailing "e" -- not a real word either as a direct entry or as a
    compound split ("plastiktüt" does not resolve as a compound tail with
    any real head either)."""
    _, candidates = _select("nomen_plural", "Er versucht, auf Plastiktüten zu verzichten.")
    assert len(candidates) == 1
    assert candidates[0].cue is None


def test_cue_is_real_word_accepts_a_compound_absent_from_the_direct_wordlist() -> None:
    """The "do not over-reject" half of the same check, exercised directly
    against ``_cue_is_real_word`` rather than through a full sentence: a
    small ``de_core_news_sm`` model does not reliably lemmatise or
    Number-tag every uncommon compound correctly, which makes a compound
    cue hard to reproduce end-to-end through a real sentence, but the
    dictionary-lookup mechanism itself is exactly what
    ``_citation_cue``/every selector's cue path shares, so testing it here
    is testing the real code path, not a re-implementation of it.
    "Familienurlaub" is not a direct entry in the 37,567-word list
    (confirmed directly against the fixture), but it is perfectly good
    German -- a real compound of two words that ARE both direct entries
    ("Familie", "Urlaub"). The two-part compound split fallback must accept
    it, not silently drop a good cue because a general wordlist happens not
    to spell out every compound."""
    assert selectors._cue_is_real_word("Familienurlaub") is True
    assert selectors._citation_cue("Familienurlaub", "Familienurlauben") == "Familienurlaub"


def test_cue_is_real_word_accepts_a_compound_with_both_halves_short_but_high_frequency() -> None:
    """docs/audits/cycle-06-report.md task 3: "Radweg" is correct German, not
    a direct dictionary entry, and its own two halves ("Rad", "weg") are
    both only 3 characters -- one short of the standard 4-character compound
    floor, so the ordinary compound fallback above cannot rescue it. Both
    halves ARE high-frequency words (top 5,000 of the vendored frequency
    list), which is exactly what now rescues it without lowering the
    4-character floor across the board -- see the regression right below
    this test for why a blanket floor drop is not the fix."""
    assert selectors._cue_is_real_word("Radweg") is True


def test_cue_is_real_word_rejects_a_string_that_is_not_german_at_all() -> None:
    """Neither a direct hit nor any two-part split resolves -- unlike the
    compound tests above, this is not a real word under any avenue this
    check has, so it must be rejected outright. "mussen" is the pinned
    regression for docs/audits/cycle-06-report.md task 3 specifically: its
    own 3-character split ("mus"+"sen") is a real dictionary pair, exactly
    like "Radweg"'s, but NEITHER half is a high-frequency word (rank
    ~38,400/~29,100 of 50,000, versus "rad"/"weg"'s ~3,800/~130), so the
    frequency gate that rescues "Radweg" must not rescue this one too."""
    assert selectors._cue_is_real_word("mussen") is False
    assert selectors._cue_is_real_word("einpacksen") is False
    assert selectors._cue_is_real_word("Plastiktüt") is False


def test_modalverben_praesens_cue_is_the_modals_own_infinitive() -> None:
    """docs/audits/cycle-04-report.md's own example: "kann" is one of
    several equally grammatical modals in this slot -- "(können)" rescues
    it, naming the lexeme, never the grammar category (CLAUDE.md rule 2)."""
    _, candidates = _select(
        "modalverben_praesens",
        "Das Fleisch kann scharf angebraten werden, wenn ein kräftiger Geschmack gewünscht wird.",
    )
    assert len(candidates) == 1
    assert candidates[0].cue == "können"


def test_modalverben_praesens_cue_handles_the_frozen_moechten_lemma() -> None:
    _, candidates = _select("modalverben_praesens", "Ich möchte gern ein Eis.")
    assert len(candidates) == 1
    assert candidates[0].cue == "möchten"


def test_modalverben_praesens_cue_is_none_when_it_would_equal_the_answer() -> None:
    """1st-plural present tense of a modal is spelled identically to its own
    infinitive ("wir müssen" == "müssen") -- a cue here would hand over the
    answer verbatim, so it is withheld rather than leaked."""
    _, candidates = _select("modalverben_praesens", "Wir müssen jetzt gehen.")
    assert len(candidates) == 1
    assert candidates[0].cue is None


def test_passiv_modalverben_cue_is_the_modals_own_infinitive() -> None:
    _, candidates = _select("passiv_modalverben", "Das Fenster kann nicht mehr repariert werden.")
    assert len(candidates) == 1
    assert candidates[0].cue == "können"


def test_verb_sein_haben_cue_is_the_lexemes_own_infinitive() -> None:
    """docs/audits/cycle-06-modal-leak.md: ``verb_sein_haben``'s own
    ``eligible_types`` is ``[cloze_cued]`` alone, so a cue-less item from it
    was never a valid item type at all -- confirmed live, 16 of 174 pilot
    items this way. ``verb_sein_haben`` shares ``_irregular_finite_selector``
    with ``modalverben_praesens``; "haben" is not a modal, and the
    uniqueness gate never flags it either way (its own identity is
    structurally fixed, see ``uniqueness.py``'s policy table) -- but
    ``lemma in lemmas`` (this selector's own closed target set) already
    means "haben" is exactly as trustworthy a citation form as a modal's own
    infinitive, so it is cued unconditionally now, the same as every other
    candidate this factory produces."""
    _, candidates = _select("verb_sein_haben", "Ich habe einen Hund.")
    assert len(candidates) == 1
    assert candidates[0].cue == "haben"


def test_verb_sein_haben_cue_is_none_when_it_would_equal_the_answer() -> None:
    """1st/3rd-plural present tense of "haben" is spelled identically to its
    own infinitive ("wir/sie haben" == "haben") -- a cue here would hand
    over the answer verbatim, so it is withheld exactly like the equivalent
    modal cell (``test_modalverben_praesens_cue_is_none_when_it_would_equal_
    the_answer``)."""
    _, candidates = _select("verb_sein_haben", "Wir haben ein neues Auto.")
    assert len(candidates) == 1
    assert candidates[0].cue is None


def test_praeteritum_sein_haben_modal_cues_every_candidate_now() -> None:
    """The same selector also matches bare sein/haben Präteritum alongside
    modals -- both are cued now (docs/audits/cycle-06-modal-leak.md extends
    the cue mechanism to the auxiliary half of this topic, not only the
    modal half), because both are members of this selector's own closed
    target set (``frozenset({"sein", "haben"}) | MODAL_LEMMAS``) and a
    citation form derived from a closed set is trustworthy regardless of
    which member it is.

    This does NOT make the item fully unambiguous on its own: a cue supplies
    the LEXEME ("sein"), never the TENSE, and nothing in "Er ___ (sein)
    gestern sehr müde." rules out "ist" over "war" the way the cue rules out
    every other auxiliary/modal -- see ``blanker.py``'s own module docstring
    for this honestly-reported, unresolved limitation."""
    _, modal_candidates = _select("praeteritum_sein_haben_modal", "Er konnte gestern nicht kommen.")
    assert len(modal_candidates) == 1
    assert modal_candidates[0].cue == "können"

    _, aux_candidates = _select("praeteritum_sein_haben_modal", "Er war gestern sehr müde.")
    assert len(aux_candidates) == 1
    assert aux_candidates[0].cue == "sein"


# ==============================================================================
# docs/audits/cycle-08-report.md's capitalisation trap: a cue's own
# first-letter case must match the accepted answer's, so a learner who types
# exactly what the bracketed cue shows is never marked wrong for a case
# mismatch the cue itself introduced. Two independent sources, closed by the
# same rule at the same choke point (``selectors._citation_cue``, via
# ``_cue_case_matched_to_answer``): the always-capitalised formal
# ``Sie``-family possessive ("Ihr"/"Ihre"/"Ihren"/"Ihrer", ...), whose
# paradigm is stored lowercase (``paradigms.family_forms`` lower-cases every
# ein-word stem), and a gap sitting at the very start of a sentence, whose
# answer is capitalised by German orthography rather than by anything about
# the word itself. All four sentences below are the exact carriers
# docs/audits/cycle-08-report.md named as live defects; each is pinned here
# against the real spaCy pipeline, not a synthetic unit case, so a
# regression in either the selector or ``blank_candidate`` itself is caught.
# ==============================================================================


def test_cue_case_matched_to_answer_uppercases_a_lowercase_cue() -> None:
    assert selectors._cue_case_matched_to_answer("ihre", "Ihrer") == "Ihre"


def test_cue_case_matched_to_answer_lowercases_an_uppercase_cue() -> None:
    assert selectors._cue_case_matched_to_answer("Alt", "alte") == "alt"


def test_cue_case_matched_to_answer_leaves_matching_case_alone() -> None:
    assert selectors._cue_case_matched_to_answer("ihre", "ihrer") == "ihre"
    assert selectors._cue_case_matched_to_answer("Zahn", "Zähne") == "Zahn"


def test_cue_case_matched_to_answer_tolerates_an_empty_cue() -> None:
    assert selectors._cue_case_matched_to_answer("", "Ihrer") == ""


# -- TODO.md 1.3: the determiner cue no longer reads the head noun's gender
# at all --------------------------------------------------------------------
#
# docs/audits/cycle-09-report.md's own two examples: the OLD design read the
# cue's gender off the head noun's own tagged ``Gender``, which can disagree
# with the determiner's own (agreement-consistent) tag on the identical noun
# phrase -- "einen ... Orangensaft" (masculine) got cued "(eine)" because
# "Orangensaft" itself was independently mistagged ``Gender=Fem``, and "das
# ... Zimmer" (neuter) got cued "(die)" the same way. TODO.md 2.1 (Part A,
# owner's decision) does not merely correct the lookup -- it removes the
# lookup entirely: ``_determiner_cue`` never reads ANY noun's gender, head or
# otherwise, so this defect class cannot recur structurally, not just by a
# fixed bug. These tests pin that per gender, not just assume Part A makes
# task 1.3 moot (this task's own explicit instruction), using the exact
# reported sentences plus one direct case per gender.


def test_determiner_cue_is_invariant_for_the_reported_orangensaft_sentence() -> None:
    """The exact cycle-9 example: "Orangensaft" is masculine and correctly
    tagged so on its own governing determiner ("einen"), but was
    independently mistagged ``Gender=Fem`` on the noun itself -- the old,
    now-removed head-noun lookup would have cued "eine". The invariant cue
    is "ein" regardless, because it never reads the noun at all."""
    tagged, candidates = _select(
        "kasus_akkusativ_formen",
        "Zum Frühstück trinkt er immer einen frisch gepressten Orangensaft.",
    )
    einen = next(c for c in candidates if tagged.tokens[c.token_index].text == "einen")
    assert einen.cue == "ein"


def test_determiner_cue_is_invariant_for_the_reported_zimmer_sentence() -> None:
    """The exact cycle-9 example: "Zimmer" is neuter; the old, now-removed
    head-noun lookup cued "die" for the definite article "das" here. The
    invariant cue is "der" (the definite family's own citation form)
    regardless of the noun's gender, correct or mistagged."""
    tagged, candidates = _select(
        "kasus_akkusativ_formen",
        "Morgen möchte ich gerne den schweren Schrank in das andere Zimmer schieben.",
    )
    den = next(c for c in candidates if tagged.tokens[c.token_index].text == "den")
    assert den.cue == "der"


@pytest.mark.parametrize(
    "sentence,answer_text,expected_cue",
    [
        # Masculine head noun ("der Tisch").
        ("Ich sehe den Tisch.", "den", "der"),
        # Feminine head noun ("die Lampe").
        ("Ich sehe die Lampe.", "die", "der"),
        # Neuter head noun ("das Auto").
        ("Ich sehe das Auto.", "das", "der"),
        # Plural head noun ("die Bücher").
        ("Ich sehe die Bücher.", "die", "der"),
    ],
)
def test_determiner_cue_is_invariant_der_regardless_of_head_noun_gender(
    sentence: str, answer_text: str, expected_cue: str
) -> None:
    """One case per gender (plus plural): the definite-article cue is
    always the literal invariant "der", never agreed to the head noun's
    own gender/number -- the class of defect TODO.md 1.3 reports is closed
    structurally, not merely for the two reported sentences."""
    tagged, candidates = _select("kasus_akkusativ_formen", sentence)
    target = next(c for c in candidates if tagged.tokens[c.token_index].text == answer_text)
    assert target.cue == expected_cue


def test_praepositionen_dativ_cue_matches_the_formal_sie_possessives_case() -> None:
    """docs/audits/cycle-08-report.md's own finding still holds -- the
    formal register capitalises the whole ``Ihr``-family, so the cue's
    case must match the answer's ('Ihrer'), never the paradigm table's own
    lowercase storage case -- but TODO.md 2.1 (owner's decision, cycle 9)
    changed WHAT the cue is: the invariant citation stem 'ihr', not the
    head-noun-gender-agreed form 'ihre' this test used to pin. 'Sie kochen
    oft zusammen mit ___ (ihre) Familie ...' -> 'Ihrer' now cues 'Ihr', not
    'Ihre' -- the learner works out the ending (gender AND case) instead of
    being handed the gender for free. Case-matching itself (capitalised for
    formal ``Sie``) is unchanged and still tested here."""
    item = _blank(
        "praepositionen_dativ",
        "Kochen Sie oft zusammen mit Ihrer Familie und laden Sie Freunde ein?",
    )
    assert item.proposed_answer == "Ihrer"
    assert item.cue == "Ihr"
    assert item.cue is not None
    assert item.cue[:1].isupper() == item.proposed_answer[:1].isupper()


def test_kasus_akkusativ_formen_cue_matches_the_formal_sie_possessives_case() -> None:
    """docs/audits/cycle-08-report.md: 'Ihr Schwiegersohn repariert ___
    Stuhl ...' (cue: ihr) -> 'Ihren'."""
    item = _blank(
        "kasus_akkusativ_formen",
        "Ihr Schwiegersohn repariert Ihren Stuhl jedes Wochenende, "
        "weil Sie das selbst nicht schaffen.",
    )
    assert item.proposed_answer == "Ihren"
    assert item.cue == "Ihr"
    assert item.cue is not None
    assert item.cue[:1].isupper() == item.proposed_answer[:1].isupper()


def test_adjektivdeklination_nullartikel_cue_matches_sentence_initial_capitalisation_alt() -> None:
    """docs/audits/cycle-08-report.md: '___ (alt) Batterien können im
    Supermarkt abgegeben werden.' -> 'Alte'. The gap opens the sentence, so
    German orthography alone capitalises the answer; the cue must follow,
    even though the adjective's own citation form ('alt') is ordinarily
    lowercase."""
    item = _blank(
        "adjektivdeklination_nullartikel", "Alte Batterien können im Supermarkt abgegeben werden."
    )
    assert item.proposed_answer == "Alte"
    assert item.cue == "Alt"
    assert item.cue is not None
    assert item.cue[:1].isupper() == item.proposed_answer[:1].isupper()


def test_adjektivdeklination_nullartikel_cue_matches_sentence_initial_capitalisation_letzte() -> (
    None
):
    """docs/audits/cycle-08-report.md: '___ (letzter) Woche hatte ich
    plötzlich fiese Bauchschmerzen.' -> 'Letzte'."""
    item = _blank(
        "adjektivdeklination_nullartikel", "Letzte Woche hatte ich plötzlich fiese Bauchschmerzen."
    )
    assert item.proposed_answer == "Letzte"
    assert item.cue == "Letzter"
    assert item.cue is not None
    assert item.cue[:1].isupper() == item.proposed_answer[:1].isupper()


# ==============================================================================
# TODO.md 1.1/1.2/1.3/1.4/1.5 -- six defects found while hand-checking the
# 8.1 survivors (docs/audits/fix-log.md 8.1's own six-item list), left out
# of scope at the time and closed here. 1.6 (the ``anpassen``/
# ``kasus_dativ_formen`` government-lexicon gap) is documented in
# docs/audits/fix-log.md as not fixable within scope -- no code changed for
# it, so no regression test for it either.
# ==============================================================================


def test_verben_reflexiv_dat_skips_a_temporal_np_to_find_the_real_object() -> None:
    """TODO.md 1.1: "Er kauft sich jeden Abend eine Flasche Bier ..." --
    ``_immediately_followed_by_object_np`` used to stop at "jeden Abend" (a
    temporal accusative, correctly excluded from counting as an object)
    instead of continuing on to "eine Flasche Bier", the real one, and so
    never saw the object that forces "kaufen"'s Dative reading here."""
    sentence = "Er kauft sich jeden Abend eine Flasche Bier zum Abendessen."
    item = _blank("verben_reflexiv_dat", sentence)
    assert item.proposed_answer == "sich"
    _, akk_candidates = _select("verben_reflexiv_akk", sentence)
    assert akk_candidates == []


def test_verben_reflexiv_akk_walks_past_a_coordinated_determiner_to_find_um() -> None:
    """TODO.md 1.2: "Ob es sich um ein und dasselbe Tier handelt ..." --
    ``_governed_by_adposition`` used to stop at the bare "und" inside "ein
    und dasselbe" and never reach "um", wrongly treating "Tier" as a bare
    accusative object of "handeln" and promoting "sich" to Dative."""
    sentence = "Ob es sich um ein und dasselbe Tier handelt, ist unklar."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


def test_verben_reflexiv_akk_walks_past_a_two_token_proper_name() -> None:
    """TODO.md 1.3: "Hier setzt er sich ... gegen Fatih Celiksoy durch." --
    ``_governed_by_adposition`` gave up at the first name token and never
    reached "gegen", wrongly treating the second name token as a bare
    accusative object and promoting "sich" to Dative. Confirmed present in
    both the before and after corpus samples of the verb-government
    lexicon work (docs/audits/fix-log.md 8.1), so this predates that fix
    rather than being caused by it. This exact sentence also needs the
    ``NN``-tagged-name-half fallback (see ``_governed_by_adposition``'s own
    docstring): this tagger tags "Fatih" plain ``NOUN``, not ``PROPN``, in
    this context."""
    sentence = "Hier setzt er sich im Finale gegen Fatih Celiksoy durch."
    item = _blank("verben_reflexiv_akk", sentence)
    assert item.proposed_answer == "sich"
    _, dat_candidates = _select("verben_reflexiv_dat", sentence)
    assert dat_candidates == []


def test_governed_by_adposition_does_not_cross_an_unrelated_determined_noun() -> None:
    """Negative control for TODO.md 1.3's own fix: walking past a bare
    common noun tagged ``NN`` is only trusted once a genuine ``PROPN`` has
    already been seen earlier in the SAME walk -- otherwise the walk must
    not cross into a wholly separate, determiner-headed noun phrase.
    "für seinen Bruder ein Auto": "Auto" is not governed by "für" ("Bruder"
    is, and it has its own determiner, "seinen") -- checked directly
    against the helper, not only through a selector's aggregate output."""
    tagged = sentence_tagger.tag_sentence(
        "Er kauft für seinen Bruder ein Auto und einen Fernseher."
    )
    assert tagged is not None
    auto = next(t for t in tagged.tokens if t.text == "Auto")
    bruder = next(t for t in tagged.tokens if t.text == "Bruder")
    assert selectors._governed_by_adposition(tagged, auto.i) is False
    assert selectors._governed_by_adposition(tagged, bruder.i) is True


# ------------------------------------------------------------------------
# TODO.md 1.4/1.5: a finite verb tagged Person=1 whose surface text ends in
# "-st"/"-ßt" is never actually 1st person -- see
# ``selectors._finite_verb_person``'s own module comment for the full
# reasoning and the confirmed Person=3 exception. Both reported sentences
# also carry OTHER, unrelated tagger defects (an unreduced lemma on both
# forms -- "Kannst"/"vergiltst" instead of "können"/"vergelten" -- and
# "vergiltst" additionally tagged Tense=Past instead of Pres) that keep
# them from reaching a shipped item through the full pipeline regardless of
# this fix; that is out of this task's six-defect scope, so the fix is
# pinned directly against the tagged token and the correction function
# rather than through a topic selector.
# ------------------------------------------------------------------------


def test_finite_verb_person_corrects_kannst_in_an_inverted_question() -> None:
    tagged = sentence_tagger.tag_sentence("Kannst du mich morgen im Krankenhaus besuchen?")
    assert tagged is not None
    kannst = tagged.tokens[0]
    assert kannst.text == "Kannst"
    assert kannst.morph.get("Person") == "1"  # the raw tagger bug, unchanged
    assert selectors._finite_verb_person(kannst) == "2"


def test_finite_verb_person_corrects_vergiltst_in_an_inverted_exclamation() -> None:
    tagged = sentence_tagger.tag_sentence("So also vergiltst du mir meine Freundlichkeit!")
    assert tagged is not None
    vergiltst = next(t for t in tagged.tokens if t.text == "vergiltst")
    assert vergiltst.morph.get("Person") == "1"  # the raw tagger bug, unchanged
    assert selectors._finite_verb_person(vergiltst) == "2"


def test_finite_verb_person_does_not_touch_a_correctly_tagged_person_3() -> None:
    """Every other tagged Person is returned unchanged, ``3`` included --
    see the "passt" sibilant-stem case documented on
    ``_finite_verb_person`` for why Person=3 is deliberately never
    corrected. Confirmed against this exact sentence: unconditionally
    overriding Person=3 broke ``verben_reflexiv_dat``'s own subject-
    agreement check for "sich" (which requires Person=3) during this fix's
    own development -- this is a regression guard for that, not a
    hypothetical."""
    tagged = sentence_tagger.tag_sentence("Der Körper passt sich schnell Temperaturänderungen an.")
    assert tagged is not None
    passt = next(t for t in tagged.tokens if t.text == "passt")
    assert passt.morph.get("Person") == "3"
    assert selectors._finite_verb_person(passt) == "3"


def test_finite_verb_person_leaves_a_correctly_tagged_person_2_alone() -> None:
    tagged = sentence_tagger.tag_sentence("Warum gehst du schon so früh nach Hause?")
    assert tagged is not None
    gehst = next(t for t in tagged.tokens if t.text == "gehst")
    assert gehst.morph.get("Person") == "2"
    assert selectors._finite_verb_person(gehst) == "2"


# TODO.md 1.6, reopened by the coordinator's own review after the six-item
# pass: "unfixable" was wrong. de_core_news_sm leaves "passt"'s own lemma
# completely unreduced ("passt", not "passen") rather than merely wrong, so
# ``_governing_verb_lemma`` used to concatenate the separable particle onto
# the BROKEN lemma ("an" + "passt" = "anpasst"), a lexicon key nothing could
# ever match. Fixed two ways, per the coordinator's own instruction and in
# the order given: (b) ``_reduce_unreduced_weak_finite_lemma`` repairs this
# one confirmed failure shape mechanically (the same forward rule
# ``paradigms.regular_praesens_form`` already conjugates with, reversed and
# cross-checked against the real-word dictionary), which turned out to
# benefit every weak verb sharing this failure, not only "anpassen" -- see
# docs/audits/fix-log.md's own rebuilt-lexicon diff for the measurement.
# (a) "anpassen" itself still clears no corpus threshold even after (b) (1
# occurrence in the full corpus, confirmed by rebuilding
# ``data/fixtures/verb_government/lexicon.v1.jsonl``), so it is also added
# to ``paradigms.DATIVE_ONLY_VERBS`` by name, on the same hand-audit basis
# TODO 8.1's own "verbeugen" precedent (commit fb3d721) already established.
#
# The reported sentence's own "..." elision hid a determiner: TODO.md 1.6's
# title itself, "determiner-less plural dative", describes a shape
# ``kasus_dativ_formen`` structurally cannot ever select from at all --
# ``_determiner_selector`` only ever considers ART/PIAT/PPOSAT tokens
# (``_DETERMINER_TAGS``), never a bare noun with no determiner whatsoever --
# so the reported item must have had one; "den" is the natural
# reconstruction, confirmed to reproduce the defect against the
# pre-this-fix code and lexicon (``[]``, git-stash-confirmed) and to be
# fixed against the current ones.
def test_reduce_unreduced_weak_finite_lemma_repairs_passt_to_passen() -> None:
    tagged = sentence_tagger.tag_sentence("Das passt mir nicht.")
    assert tagged is not None
    passt = next(t for t in tagged.tokens if t.text == "passt")
    assert passt.tag == "VVFIN"
    assert passt.lemma.lower() == "passt"
    assert selectors._reduce_unreduced_weak_finite_lemma(passt) == "passen"


def test_reduce_unreduced_weak_finite_lemma_declines_a_2sg_st_form() -> None:
    """Gated to (Person=3, Number=Sing) only -- a 2nd-singular "-st" form is
    a DIFFERENT, already-otherwise-handled tagger failure (TODO.md 1.4/1.5's
    own Person mistag), not this function's job."""
    tagged = sentence_tagger.tag_sentence("Du hilfst mir sehr.")
    assert tagged is not None
    hilfst = next(t for t in tagged.tokens if t.text == "hilfst")
    assert selectors._reduce_unreduced_weak_finite_lemma(hilfst) is None


def test_kasus_dativ_formen_reaches_anpassen_via_a_repaired_governing_lemma() -> None:
    sentence = "Der Körper passt sich schnell den Temperaturänderungen an."
    tagged = sentence_tagger.tag_sentence(sentence)
    assert tagged is not None
    passt = next(t for t in tagged.tokens if t.text == "passt")
    assert passt.lemma.lower() == "passt"  # the confirmed unreduced-lemma shape itself
    assert selectors._governing_verb_lemma(tagged, 0, len(tagged.tokens)) == "anpassen"
    item = _blank("kasus_dativ_formen", sentence)
    assert item.proposed_answer == "den"
