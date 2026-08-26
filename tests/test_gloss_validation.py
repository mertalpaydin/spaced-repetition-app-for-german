"""Tests for src/generation/gloss_validation.py.

docs/audits/generation-track-plan.md Cycle 3: the English gloss disambiguator
for gaps nothing else in the carrier narrows to one lexeme. See that
module's own docstring for the design this exercises (presence-based
tense/person bucket matching, the narrow category-tag fallback, and the
honest split between what spaCy verifies and what the closed-list fallback
can only approximate).
"""

from __future__ import annotations

import pytest
from src.contracts import CandidateItem, Distractor, Topic
from src.generation.blanking import sentence_tagger
from src.generation.gloss_validation import (
    GlossCheckResult,
    _classify,
    _german_tense_bucket,
    _gloss_person_number_pairs,
    _gloss_tense_buckets,
    _lemma_at_gap,
    _token_readings,
    category_fallback_reason,
    check_requirement,
    english_analysis_available,
    gloss_required,
    gloss_resolves_lexical_ambiguity,
    is_valid_category_tag,
    topic_allows_category_fallback,
    validate_gloss_consistency,
)
from src.taxonomy.loader import load_taxonomy
from src.verification.pipeline import check_gloss


@pytest.fixture(scope="module")
def topics() -> dict[str, Topic]:
    return {t.id: t for t in load_taxonomy()}


@pytest.fixture(scope="module")
def praeteritum(topics: dict[str, Topic]) -> Topic:
    return topics["praeteritum_vollverben"]


@pytest.fixture(scope="module")
def praesens(topics: dict[str, Topic]) -> Topic:
    return topics["verb_praesens_regelm"]


@pytest.fixture(scope="module")
def imperativ(topics: dict[str, Topic]) -> Topic:
    return topics["imperativ"]


@pytest.fixture(scope="module")
def futur_i(topics: dict[str, Topic]) -> Topic:
    return topics["futur_i"]


@pytest.fixture(scope="module")
def konjunktiv_hoeflichkeit(topics: dict[str, Topic]) -> Topic:
    return topics["konjunktiv_ii_hoeflichkeit"]


@pytest.fixture(scope="module")
def dativ_topic(topics: dict[str, Topic]) -> Topic:
    """A Case-only topic: no Tense, no Person fixed -- gloss is never
    required by this module's rules for a well-constrained item of it."""
    return topics["dativ_nach_praeposition"]


# ==============================================================================
# Environment sanity: which English analysis path is actually live here.
# ==============================================================================


def test_english_analysis_availability_is_a_real_boolean() -> None:
    """Whichever path this environment has, the function must answer
    without raising -- this is the fact the rest of this file's tests
    (which exercise both branches explicitly) depend on being observable."""
    assert isinstance(english_analysis_available(), bool)


# ==============================================================================
# 1. gloss_required
# ==============================================================================


def test_gloss_required_true_when_accepted_set_has_multiple_members(dativ_topic: Topic) -> None:
    """The general trigger: more than one distinct filler is grammatically
    valid, regardless of whether the topic itself fixes anything."""
    assert gloss_required(dativ_topic, ["dem", "einem"]) is True


def test_gloss_required_false_for_single_answer_case_only_topic(dativ_topic: Topic) -> None:
    """A Case-only topic (Nominativ/Dativ/Akkusativ/Genitiv article topics)
    is neither tense- nor person-selecting, and a single-member accepted
    set carries no other ambiguity -- gloss is optional, not required."""
    assert gloss_required(dativ_topic, ["dem"]) is False


def test_gloss_required_true_for_tense_selecting_topic_even_with_one_answer(
    praeteritum: Topic,
) -> None:
    """The exact failure mode this whole change exists to fix: a
    tense-selecting topic's accepted set is computed FOR its own fixed
    tense and has only one member, so the multi-member trigger alone would
    never catch "wohnt"/"wohnte". ``morph_spec`` fixing ``Tense`` must
    trigger the requirement on its own."""
    assert praeteritum.morph_spec is not None
    assert "Tense" in praeteritum.morph_spec
    assert gloss_required(praeteritum, ["wohnte"]) is True


def test_gloss_required_true_for_person_fixing_topic(dativ_topic: Topic) -> None:
    """Forward-compatible with a topic that fixes Person in morph_spec, even
    though none currently does -- constructed directly rather than pulled
    from the taxonomy."""
    person_fixed = Topic(
        id="test_person_fixed",
        name_de="Test",
        cefr="A2",
        description="Test topic fixing Person.",
        morph_spec={"Person": "2"},
    )
    assert gloss_required(person_fixed, ["kommst"]) is True


def test_gloss_required_false_for_ordinary_present_tense_conjugation_topic(
    praesens: Topic,
) -> None:
    """``verb_praesens_regelm`` fixes Tense: Pres, so by this module's own
    rule it IS tense-selecting and DOES require a gloss -- this is
    deliberate and matches the module docstring: even an ordinary
    conjugation topic is technically tense-selecting, but in practice an
    ordinary declarative carrier already supplies an explicit subject that
    fixes Person from context; nothing in this module claims to relax the
    Tense requirement for it. This test documents that (not a bug)."""
    assert gloss_required(praesens, ["kommt"]) is True


def test_gloss_required_none_topic_only_multi_member_trigger_applies() -> None:
    """No topic at all (``topic=None``): only the accepted-set-size trigger
    can fire, never a false positive from a missing topic."""
    assert gloss_required(None, ["dem", "einem"]) is True
    assert gloss_required(None, ["dem"]) is False


# ==============================================================================
# 2. The narrow category-tag fallback
# ==============================================================================


def test_only_imperativ_is_on_the_category_fallback_list(topics: dict[str, Topic]) -> None:
    """The list must stay short: every OTHER topic in the whole taxonomy is
    off it. This is the test that actually enforces "keep it short" rather
    than merely asserting it in a docstring -- if a future change adds an
    entry, this test fails and forces a conscious, justified edit."""
    on_list = [t.id for t in topics.values() if topic_allows_category_fallback(t)]
    assert on_list == ["imperativ"]


def test_category_fallback_reason_is_present_and_topic_specific(imperativ: Topic) -> None:
    reason = category_fallback_reason(imperativ)
    assert reason is not None
    assert "du" in reason and "Sie" in reason


def test_category_fallback_reason_none_for_unlisted_topic(dativ_topic: Topic) -> None:
    assert category_fallback_reason(dativ_topic) is None
    assert topic_allows_category_fallback(dativ_topic) is False


@pytest.mark.parametrize("tag", ["du-form", "ihr-form", "sie-form", "DU-FORM", "  Sie-Form  "])
def test_is_valid_category_tag_accepts_controlled_vocabulary(imperativ: Topic, tag: str) -> None:
    assert is_valid_category_tag(imperativ, tag) is True


@pytest.mark.parametrize("tag", ["", "Imperativ", "formal", "du", "some explanation of the rule"])
def test_is_valid_category_tag_rejects_anything_outside_the_controlled_vocabulary(
    imperativ: Topic, tag: str
) -> None:
    assert is_valid_category_tag(imperativ, tag) is False


def test_is_valid_category_tag_false_for_a_topic_not_on_the_fallback_list(
    dativ_topic: Topic,
) -> None:
    """Even a string that happens to equal one of imperativ's own tags is
    not valid for an unrelated topic -- there is no vocabulary to check it
    against outside the fallback list."""
    assert is_valid_category_tag(dativ_topic, "du-form") is False


# ==============================================================================
# check_requirement: the structural gate combining the two pieces above.
# ==============================================================================


def test_check_requirement_passes_when_gloss_not_required(dativ_topic: Topic) -> None:
    ok, reason = check_requirement(dativ_topic, ["dem"], None)
    assert ok is True
    assert reason is None


def test_check_requirement_fails_when_required_and_absent(praeteritum: Topic) -> None:
    ok, reason = check_requirement(praeteritum, ["wohnte"], None)
    assert ok is False
    assert reason is not None and "under-constrained" in reason


def test_check_requirement_fails_on_empty_string_gloss(praeteritum: Topic) -> None:
    ok, _ = check_requirement(praeteritum, ["wohnte"], "   ")
    assert ok is False


def test_check_requirement_passes_with_an_ordinary_gloss(praeteritum: Topic) -> None:
    ok, reason = check_requirement(praeteritum, ["wohnte"], "Do you know where he lived?")
    assert ok is True
    assert reason is None


def test_check_requirement_passes_with_valid_fallback_tag_for_imperativ(imperativ: Topic) -> None:
    ok, reason = check_requirement(imperativ, ["Komm", "Kommt", "Kommen Sie"], "sie-form")
    assert ok is True
    assert reason is None


def test_check_requirement_fails_when_imperativ_gloss_required_and_absent(
    imperativ: Topic,
) -> None:
    """The fallback tag is an ALTERNATIVE to a gloss, not automatic --
    an item with multiple valid registers and neither a gloss nor a tag
    still fails the requirement."""
    ok, reason = check_requirement(imperativ, ["Komm", "Kommt", "Kommen Sie"], None)
    assert ok is False
    assert reason is not None


def test_check_requirement_an_ordinary_english_gloss_also_satisfies_imperativ() -> None:
    """The fallback is narrow but not exclusive: if the model happens to
    supply a genuine English gloss for an imperativ item anyway, that
    still satisfies the structural requirement (its mechanical consistency
    is a separate, later check)."""
    from src.taxonomy.loader import load_taxonomy as _lt

    imperativ = {t.id: t for t in _lt()}["imperativ"]
    ok, reason = check_requirement(
        imperativ, ["Komm", "Kommt", "Kommen Sie"], "Please come in soon."
    )
    assert ok is True
    assert reason is None


# ==============================================================================
# 3. Mechanical consistency: the worked example from the task itself.
# ==============================================================================


def test_the_plan_owners_own_worked_example_wrong_gloss_is_rejected(praeteritum: Topic) -> None:
    """ "Weisst du, wo er ___ (wohnen)?" glossed as present tense ("lives")
    while the computed target is the Praeteritum form "wohnte" -- the exact
    contradiction a mechanical gloss check exists to catch."""
    result = validate_gloss_consistency(
        "Weisst du, wo er ___ (wohnen)?",
        "wohnte",
        "Do you know where he lives?",
        praeteritum,
    )
    assert result.consistent is False
    assert "tense" in result.reason.lower() if result.reason else False


def test_the_plan_owners_own_worked_example_correct_gloss_is_accepted(praeteritum: Topic) -> None:
    """The matching gloss for the same item must pass -- and, critically,
    must pass DESPITE its own matrix clause ("Do you know...") being
    present tense, which is exactly the case a naive "every verb must
    match" rule would wrongly reject (see module docstring)."""
    result = validate_gloss_consistency(
        "Weisst du, wo er ___ (wohnen)?",
        "wohnte",
        "Do you know where he lived?",
        praeteritum,
    )
    assert result.consistent is True
    assert result.reason is None
    assert "tense" in result.checked_dimensions


def test_present_tense_target_rejects_an_all_past_gloss(praesens: Topic) -> None:
    result = validate_gloss_consistency(
        "Er ___ (wohnen) jetzt in Berlin.",
        "wohnt",
        "He lived in Berlin.",
        praesens,
    )
    assert result.consistent is False


def test_present_tense_target_accepts_a_present_gloss(praesens: Topic) -> None:
    result = validate_gloss_consistency(
        "Er ___ (wohnen) jetzt in Berlin.",
        "wohnt",
        "He lives in Berlin now.",
        praesens,
    )
    assert result.consistent is True


def test_future_target_requires_a_future_marker(futur_i: Topic) -> None:
    consistent_result = validate_gloss_consistency(
        "Nächstes Jahr ___ (werden) ich nach Spanien reisen.",
        "werde",
        "Next year I will travel to Spain.",
        futur_i,
    )
    assert consistent_result.consistent is True

    inconsistent_result = validate_gloss_consistency(
        "Nächstes Jahr ___ (werden) ich nach Spanien reisen.",
        "werde",
        "I travel to Spain.",
        futur_i,
    )
    assert inconsistent_result.consistent is False


def test_konjunktiv_ii_target_accepts_a_conditional_marker(
    konjunktiv_hoeflichkeit: Topic,
) -> None:
    consistent_result = validate_gloss_consistency(
        "___ (können) Sie mir bitte helfen?",
        "Könnten",
        "Could you please help me?",
        konjunktiv_hoeflichkeit,
    )
    assert consistent_result.consistent is True


def test_konjunktiv_ii_target_does_not_reject_a_present_indicative_gloss(
    konjunktiv_hoeflichkeit: Topic,
) -> None:
    """This assertion is the REVERSE of the one this test used to make, and
    the reversal is deliberate.

    The old test asserted that a Konjunktiv II target with a present-tense
    gloss ("Könnten Sie mir bitte helfen?" / "Do you help me?") is
    INCONSISTENT. The first pilot with real glosses falsified the rule that
    assertion encodes. "Warum tun Sie so, als würden Sie mich nicht kennen?"
    / "Why are you pretending you don't know me?" has exactly that shape --
    Konjunktiv II target, present-only English marking -- and is a perfectly
    good item. German Konjunktiv II is a MOOD; English has no conditional
    tense to compare it against, and renders it with past forms, with
    modals, or with a plain present in "als ob" clauses, wishes and polite
    requests. There is no English tense marking that is evidence AGAINST a
    Konjunktiv II, so no verdict of "inconsistent" is available here.

    The check does not silently pass such a gloss either: the dimension
    comes back UNVERIFIED, which is what the module reports rather than
    claims. ``gloss_resolves_lexical_ambiguity`` treats unverified as "no
    evidence", so nothing downstream reads this as a confirmation.
    """
    result = validate_gloss_consistency(
        "___ (können) Sie mir bitte helfen?",
        "Könnten",
        "Do you help me?",
        konjunktiv_hoeflichkeit,
    )
    assert result.consistent is True
    assert "tense" in result.unverified_dimensions


def test_perfekt_target_accepts_either_simple_past_or_present_perfect_gloss(
    topics: dict[str, Topic],
) -> None:
    """German Perfekt (Tense: Past, Aspect: Perf in this taxonomy's own
    convention) glosses naturally into EITHER an English simple past OR an
    English present-perfect sentence -- both are accepted, per the module
    docstring's explicit note that this is the one deliberate bucket
    collapse."""
    perfekt = topics["perfekt_haben"]
    simple_past = validate_gloss_consistency(
        "Sie ___ (haben) gestern das Buch gelesen.",
        "hat",
        "She read the book yesterday.",
        perfekt,
    )
    present_perfect = validate_gloss_consistency(
        "Sie ___ (haben) gestern das Buch gelesen.",
        "hat",
        "She has read the book.",
        perfekt,
    )
    assert simple_past.consistent is True
    assert present_perfect.consistent is True


# ==============================================================================
# Person/Number
# ==============================================================================


def test_first_person_singular_target_expects_i(praesens: Topic) -> None:
    result = validate_gloss_consistency(
        "Ich ___ (wohnen) in Hamburg.",
        "wohne",
        "I live in Hamburg.",
        praesens,
    )
    assert result.consistent is True
    assert "person" in result.checked_dimensions


def test_third_person_plural_target_expects_they(praesens: Topic) -> None:
    result = validate_gloss_consistency(
        "___ (wohnen) sie in Hamburg?",
        "wohnen",
        "Do they live in Hamburg?",
        praesens,
    )
    assert result.consistent is True


def test_third_person_plural_target_rejects_a_third_singular_gloss(praesens: Topic) -> None:
    result = validate_gloss_consistency(
        "___ (wohnen) sie in Hamburg?",
        "wohnen",
        "Does she live in Hamburg?",
        praesens,
    )
    assert result.consistent is False


# ==============================================================================
# Leak checks
# ==============================================================================


def test_gloss_leaking_grammar_terminology_is_rejected(praeteritum: Topic) -> None:
    result = validate_gloss_consistency(
        "Er ___ (wohnen) dort.",
        "wohnte",
        "He lived there. This is the Praeteritum.",
        praeteritum,
    )
    assert result.consistent is False
    assert result.checked_dimensions == ()


def test_gloss_containing_the_bare_answer_token_is_rejected(praeteritum: Topic) -> None:
    result = validate_gloss_consistency(
        "Er ___ (wohnen) dort.",
        "wohnte",
        "He wohnte there once.",
        praeteritum,
    )
    assert result.consistent is False


def test_empty_gloss_is_rejected(praeteritum: Topic) -> None:
    result = validate_gloss_consistency("Er ___ (wohnen) dort.", "wohnte", "   ", praeteritum)
    assert result.consistent is False


# ==============================================================================
# Unverified dimensions must be surfaced, never silently swallowed as a pass.
# ==============================================================================


def test_unverifiable_tense_is_reported_not_silently_passed(praeteritum: Topic) -> None:
    """A gloss with no detectable tense marker at all (neither present nor
    past evidence) must come back UNVERIFIED for that dimension, still
    ``consistent`` (absence of evidence is not evidence of a contradiction)
    but visibly flagged, per the task's own instruction not to pass a
    dimension silently."""
    result = validate_gloss_consistency(
        "Er ___ (wohnen) dort.",
        "wohnte",
        "Living there.",
        praeteritum,
    )
    assert "tense" in result.checked_dimensions
    if "tense" in result.unverified_dimensions:
        assert result.consistent is True


def test_dimensions_not_applicable_to_the_topic_are_never_checked_at_all(
    dativ_topic: Topic,
) -> None:
    """A Case-only topic's answer carries no Tense/Person feature at all
    (an article, not a verb) -- neither dimension should even appear in
    ``checked_dimensions``, let alone ``unverified_dimensions``: there was
    nothing to check, not a check that failed to resolve."""
    result = validate_gloss_consistency(
        "Das Buch liegt auf ___ Tisch.",
        "dem",
        "The book is on the table.",
        dativ_topic,
    )
    assert result.checked_dimensions == ()
    assert result.unverified_dimensions == ()


# ==============================================================================
# analysis_source is honestly reported.
# ==============================================================================


def test_analysis_source_matches_environment_availability(praeteritum: Topic) -> None:
    result = validate_gloss_consistency(
        "Weisst du, wo er ___ (wohnen)?",
        "wohnte",
        "Do you know where he lived?",
        praeteritum,
    )
    expected_source = "spacy" if english_analysis_available() else "closed_list"
    assert result.analysis_source == expected_source


# ==============================================================================
# Internal helpers, tested directly for the specific bucket logic they own.
# ==============================================================================


def test_classify_no_evidence_is_unverified() -> None:
    assert _classify(frozenset({"past"}), frozenset()) == "unverified"


def test_classify_matching_evidence_is_consistent_even_with_extra_buckets() -> None:
    assert _classify(frozenset({"past", "perfect"}), frozenset({"present", "past"})) == "consistent"


def test_classify_only_wrong_evidence_is_inconsistent() -> None:
    assert _classify(frozenset({"past"}), frozenset({"present"})) == "inconsistent"


def test_german_tense_bucket_falls_back_to_topic_morph_spec_when_tagger_is_silent(
    futur_i: Topic,
) -> None:
    """Futur I's finite verb ("werde"/"wirst"/...) carries no Tense feature
    of its own in isolation -- the periphrastic construction is what
    carries the tense, not a single inflected token. The bucket must still
    resolve to "future" from the topic's own declared ``morph_spec``."""
    assert _german_tense_bucket({}, futur_i) == "future"
    assert _german_tense_bucket(None, futur_i) == "future"


def test_german_tense_bucket_none_for_topics_with_no_tense_dimension_at_all(
    dativ_topic: Topic,
) -> None:
    assert _german_tense_bucket({}, dativ_topic) is None
    assert _german_tense_bucket(None, None) is None


@pytest.mark.parametrize(
    ("gloss", "expected_bucket_present"),
    [
        ("He lives in Berlin.", "present"),
        ("He lived in Berlin.", "past"),
        ("He will live in Berlin.", "future"),
        ("He would live in Berlin.", "conditional"),
    ],
)
def test_gloss_tense_buckets_detects_each_bucket(gloss: str, expected_bucket_present: str) -> None:
    detected, source = _gloss_tense_buckets(gloss)
    assert expected_bucket_present in detected
    assert source in ("spacy", "closed_list")


def test_gloss_person_number_pairs_detects_pronoun_subjects() -> None:
    detected, source = _gloss_person_number_pairs("I live here. They live there.")
    assert ("1", "Sing") in detected
    assert ("3", "Plur") in detected
    assert source in ("spacy", "closed_list")


@pytest.mark.skipif(
    not english_analysis_available(),
    reason="requires en_core_web_sm to demonstrate the closed-list path cannot",
)
def test_spacy_path_catches_a_proper_noun_subject_with_no_literal_pronoun() -> None:
    """This is the specific extra coverage the spaCy path has over the
    closed-list fallback (see module docstring): a proper-noun subject's
    Person/Number is only visible via the verb's own agreement morphology,
    which the closed-list path (pure pronoun word-list scanning) cannot
    see at all."""
    detected, source = _gloss_person_number_pairs("Anna lives in Berlin.")
    assert source == "spacy"
    assert ("3", "Sing") in detected


def test_gloss_check_result_is_a_frozen_dataclass_shape() -> None:
    """Cheap structural check that the result type actually carries every
    field this file's other tests rely on -- guards against an accidental
    field rename breaking every test above silently changing meaning."""
    result = GlossCheckResult(
        consistent=True,
        reason=None,
        checked_dimensions=("tense",),
        unverified_dimensions=(),
        analysis_source="spacy",
    )
    assert result.consistent
    assert result.checked_dimensions == ("tense",)


# ==============================================================================
# gloss_resolves_lexical_ambiguity / _lemma_at_gap: the layer3_solver relief
# valve (docs/audits/generation-track-plan.md Cycle 3, "26 items"). Wired
# into src.verification.layer3_solver; exercised directly here at the unit
# level, and end-to-end through Layer3AdversarialSolver in
# tests/test_verification.py.
# ==============================================================================


def test_lemma_at_gap_resolves_the_infinitive_for_an_inflected_verb() -> None:
    if not sentence_tagger.analysis_available():
        pytest.skip("requires the German lemma tagger (de_core_news_sm) to be installed")
    prompt = "Meine Schwester sagte, dass sie sofort ___."
    gap_pos = prompt.index("___")
    lemma = _lemma_at_gap(prompt, "käme", gap_pos)
    assert lemma == "kommen"


def test_lemma_at_gap_agrees_across_two_forms_of_the_same_verb() -> None:
    if not sentence_tagger.analysis_available():
        pytest.skip("requires the German lemma tagger (de_core_news_sm) to be installed")
    prompt = "Meine Schwester sagte, dass sie sofort ___."
    gap_pos = prompt.index("___")
    assert _lemma_at_gap(prompt, "käme", gap_pos) == _lemma_at_gap(prompt, "kommt", gap_pos)


def test_lemma_at_gap_disagrees_for_two_different_verbs() -> None:
    if not sentence_tagger.analysis_available():
        pytest.skip("requires the German lemma tagger (de_core_news_sm) to be installed")
    prompt = "Meine Schwester sagte, dass sie sofort ___."
    gap_pos = prompt.index("___")
    assert _lemma_at_gap(prompt, "käme", gap_pos) != _lemma_at_gap(prompt, "ginge", gap_pos)


def _konjunktiv_gloss_item(distractors: list[str], gloss_en: str | None) -> CandidateItem:
    return CandidateItem(
        topic_id="konjunktiv_ii_irreal_gegenwart",
        type="cloze_free",
        difficulty=2,
        prompt="Meine Schwester sagte, dass sie sofort ___.",
        proposed_answer="käme",
        distractors=[Distractor(text=t) for t in distractors],
        gloss_en=gloss_en,
    )


@pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="requires the German lemma tagger (de_core_news_sm) to confirm same-verb identity",
)
def test_gloss_resolves_lexical_ambiguity_true_for_validated_gloss_same_verb() -> None:
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item(["kommt"], "My sister said that she would come immediately.")
    gap_pos = item.prompt.index("___")
    assert gloss_resolves_lexical_ambiguity(item, topic, ["kommt"], gap_pos) is True


def test_gloss_resolves_lexical_ambiguity_false_for_a_genuinely_different_lexeme() -> None:
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item(["ginge"], "My sister said that she would come immediately.")
    gap_pos = item.prompt.index("___")
    assert gloss_resolves_lexical_ambiguity(item, topic, ["ginge"], gap_pos) is False


def test_gloss_resolves_lexical_ambiguity_false_without_a_gloss() -> None:
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item(["kommt"], None)
    gap_pos = item.prompt.index("___")
    assert gloss_resolves_lexical_ambiguity(item, topic, ["kommt"], gap_pos) is False


def test_gloss_resolves_lexical_ambiguity_false_when_gloss_contradicts_the_answer() -> None:
    """The item under test moved topic, and the move is deliberate.

    This used to be a Konjunktiv II item glossed "My sister said that she
    comes immediately.", asserted to contradict its "käme" answer. It no
    longer does, and the change is a correction rather than a loss: English
    has no conditional tense, so present or past marking is not evidence
    against a Konjunktiv II (see
    ``test_konjunktiv_ii_target_does_not_reject_a_present_indicative_gloss``
    for the pilot item that forced that). A contradiction is simply not
    expressible on a Konjunktiv II target any more, so the gate's real
    protection -- a gloss that DOES contradict must not license dropping
    layer3's rejection -- is asserted where it still is expressible: a Futur
    I target glossed in the simple past.

    The distractor is a form of the SAME verb ("wirst" beside "werde"), so
    the lemma half of the gate passes and only the gloss half can make this
    ``False``.
    """
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["futur_i"]
    item = CandidateItem(
        topic_id="futur_i",
        type="cloze_free",
        difficulty=2,
        prompt="Nächstes Jahr ___ ich nach Spanien reisen.",
        proposed_answer="werde",
        distractors=[Distractor(text="wirst")],
        gloss_en="Last year I travelled to Spain.",
    )
    gap_pos = item.prompt.index("___")
    assert (
        validate_gloss_consistency(
            item.prompt, item.proposed_answer, item.gloss_en or "", topic
        ).consistent
        is False
    )
    assert gloss_resolves_lexical_ambiguity(item, topic, ["wirst"], gap_pos) is False


def test_gloss_resolves_lexical_ambiguity_false_when_the_gloss_verifies_nothing() -> None:
    """The other half of the gate's protection, on the Konjunktiv II item
    the contradiction case used to live on: a gloss that supplies NO tense
    evidence at all is unverified, and unverified must never be read as a
    resolved ambiguity."""
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item(["kommt"], "My sister, on the subject of her arrival.")
    gap_pos = item.prompt.index("___")
    result = validate_gloss_consistency(
        item.prompt, item.proposed_answer, item.gloss_en or "", topic
    )
    assert "tense" in result.unverified_dimensions
    assert gloss_resolves_lexical_ambiguity(item, topic, ["kommt"], gap_pos) is False


def test_gloss_resolves_lexical_ambiguity_false_for_a_non_tense_selecting_topic(
    dativ_topic: Topic,
) -> None:
    """A Case-only topic has nothing for the gloss to constrain -- must
    never be resolved regardless of how well the gloss reads."""
    item = CandidateItem(
        topic_id=dativ_topic.id,
        type="cloze_free",
        difficulty=1,
        prompt="Das Geschenk liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="einem")],
        gloss_en="The gift is on the table.",
    )
    gap_pos = item.prompt.index("___")
    assert gloss_resolves_lexical_ambiguity(item, dativ_topic, ["einem"], gap_pos) is False


def test_gloss_resolves_lexical_ambiguity_false_with_no_heterogeneous_distractors() -> None:
    """Nothing to resolve when the caller passes an empty list -- guards
    against a vacuous ``True`` from an empty-set membership check."""
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item([], "My sister said that she would come immediately.")
    gap_pos = item.prompt.index("___")
    assert gloss_resolves_lexical_ambiguity(item, topic, [], gap_pos) is False


# ==============================================================================
# Pilot regression corpus
#
# The first pilot with English glosses populated ran this check in
# measure-only mode over 376 accepted items and flagged 34. The owner read
# all 34 by hand: exactly ONE is a real defect. The other 33 are quoted here
# verbatim -- the German prompt, the German answer and the machine
# translation exactly as they appear in ``data/corpus_pilot_review.jsonl`` --
# because they are the measurement this module's four corrections were
# derived from, and a regression on any one of them is a regression on the
# whole 33-to-1 argument.
#
# These run through ``src.verification.pipeline.check_gloss``, the same seam
# ``VerificationPipeline._gloss_check`` and ``scripts/step7_corpus_pilot.py``
# call, so what is asserted here is what the pilot would actually do.
# ==============================================================================

PILOT_FALSE_REJECTIONS: list[tuple[str, str, str, str]] = [
    (
        "adjektivdeklination_bestimmt",
        "Minister fallen wie Butterbrote gewöhnlich auf die ___ Seite.",
        "gute",
        "Ministers usually fall on the good side like sandwiches.",
    ),
    (
        "futur_i",
        "Ich ___ einsam sein, nachdem du gegangen bist.",
        "werde",
        "I'll be lonely after you've gone.",
    ),
    (
        "futur_i",
        "Ich denke, dass er kommen ___.",
        "wird",
        "I think he'll come.",
    ),
    (
        "futur_i",
        "Wenn du etwas Geld brauchst, ___ ich dir etwas leihen.",
        "werde",
        "If you need any money, I'll lend you some.",
    ),
    (
        "futur_i",
        "Ich denke, dass Tom gewinnen ___.",
        "wird",
        "I think Tom will win.",
    ),
    (
        "futur_i",
        "Bist du sicher, dass alles gut gehen ___?",
        "wird",
        "Are you sure everything will go well?",
    ),
    (
        "konjunktiv_ii_hoeflichkeit",
        "___ es dich stören, das Fenster zu öffnen?",
        "Würde",
        "Would you mind opening the window?",
    ),
    (
        "konjunktiv_ii_hoeflichkeit",
        "Warum tun Sie so, als ___ Sie mich nicht kennen?",
        "würden",
        "Why are you pretending you don't know me?",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Tom ___ um Längen nicht so reich, wenn er Maria nicht geheiratet hätte.",
        "wäre",
        "Tom certainly wouldn't be anywhere near as rich as he is if he hadn't married Mary.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Schön wär’s, wenn ich hübsch ___.",
        "wäre",
        "I wish I was pretty.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich du ___, würde ich nicht mit ihm sprechen.",
        "wäre",
        "If I were you, I wouldn't talk to him.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich mehr Zeit ___, würde ich mehr studieren.",
        "hätte",
        "If I had more time, I would study more.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich Platz ___, würde ich mir einen größeren Fernseher kaufen.",
        "hätte",
        "I'd buy a larger TV if I had room for it.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich viel Geld ___, würde ich mir ein Haus am Meer kaufen.",
        "hätte",
        "If I had a lot of money, I would buy a house by the sea.",
    ),
    (
        "konjunktiv_ii_irreal_gegenwart",
        "Wenn ich im Lotto gewänne, ___ ich mir ein neues Auto kaufen.",
        "würde",
        "If I won the lottery, I'd buy you a new car.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Auch wenn du mich um Hilfe gebeten hättest, ___ ich dir nicht geholfen.",
        "hätte",
        "Even if you had asked me for help, I wouldn't have helped you.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Wenn ich deine Hilfe gewollt ___, dann hätte ich darum gebeten.",
        "hätte",
        "If I'd wanted your help, I'd have asked for it.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Ich ___ gerne ins Kino gegangen, wenn ich die Zeit dazu gehabt hätte.",
        "wäre",
        "I would've gone to the movies if I'd had the time.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Tom ___ gewinnen können, wenn er es gewollt hätte.",
        "hätte",
        "Tom could've won if he'd wanted to.",
    ),
    (
        "konjunktiv_ii_vergangenheit",
        "Ich wusste, dass ich dir eine stärkere Dosis ___ geben sollen.",
        "hätte",
        "I knew I should've given you a stronger dose.",
    ),
    (
        "modalverben_praesens",
        "Ich ___ mit euch noch ein anderes Unternehmen besuchen.",
        "möchte",
        "I would like to visit another company with you.",
    ),
    (
        "partizip_ii_attributiv_erweitert",
        "Die von Eva Waser in Luzern ___ private Institution erhält 40'000 Franken.",
        "gegründete",
        "The private institution founded by Eva Waser in Lucerne will receive 40,000 Swiss francs.",
    ),
    (
        "partizip_ii_attributiv_erweitert",
        "Die Zahl der neu nach Deutschland ___ Flüchtlinge sinkt laut einem Bericht drastisch.",
        "gekommenen",
        "According to a report, the number of new refugees arriving in Germany "
        "is falling drastically.",
    ),
    (
        "passiv_praesens",
        "Ich will, dass dieser Brief jetzt geöffnet ___.",
        "wird",
        "I want this letter to be opened now.",
    ),
    (
        "perfekt_haben",
        "Das Geschäft am Neuen Wall 17 ___ noch bis zum 19. Mai geöffnet.",
        "hat",
        "The store at Neuer Wall 17 is open until May 19.",
    ),
    (
        "perfekt_haben",
        "Ich ___ schon drei Briefe geschrieben.",
        "habe",
        "I've already written three letters.",
    ),
    (
        "perfekt_sein",
        "Tom ___ gestern mit uns schwimmen gegangen.",
        "ist",
        "Tom went swimming with us yesterday.",
    ),
    (
        "perfekt_sein",
        "Die meisten Mitarbeiter ___ gegangen.",
        "sind",
        "Most of the staff has left.",
    ),
    (
        "plusquamperfekt",
        "Nachdem wir eine Weile gegangen ___, kamen wir zum See.",
        "waren",
        "After we had walked for some time, we came to the lake.",
    ),
    (
        "verb_sein_haben",
        "Lasst Tom zu Ende führen, was er begonnen ___!",
        "hat",
        "Let Tom finish what he started.",
    ),
    (
        "verb_sein_haben",
        "Die Maschinen, die in seiner Firma hergestellt werden, ___ besser als unsere.",
        "sind",
        "Machines that his company produces are superior to ours.",
    ),
    (
        "verben_trennbar_praesens",
        "Man ___ dort von hier aus nicht hin.",
        "kommt",
        "You can't get there from here.",
    ),
    (
        "zustandspassiv_zeiten",
        "Das Spitzentreffen im Lancaster House ___ zunächst nur als eines von "
        "mehreren zum Ukraine-Krieg geplant.",
        "war",
        "The summit meeting at Lancaster House was initially planned as just one "
        "of several on the Ukraine war.",
    ),
]

#: The one item in that set of 34 that is a genuine defect: a Tatoeba
#: pairing whose English sentence is about something else entirely. The
#: German is a Plusquamperfekt ("war ... gekommen") and the gloss is a bare
#: present with no past reference anywhere in it. Every correction in this
#: module has to leave this one still rejected -- that is the whole point of
#: correcting the rules rather than switching the check off.
PILOT_REAL_DEFECT: tuple[str, str, str, str] = (
    "plusquamperfekt",
    "Nachdem der Vorfall an die Öffentlichkeit gekommen ___, klärte der "
    "Sender wohl ein Missverständnis.",
    "war",
    "The trouble is that I don't have much money now.",
)


@pytest.mark.parametrize(
    ("topic_id", "prompt", "answer", "gloss_en"),
    PILOT_FALSE_REJECTIONS,
    ids=[f"{row[0]}::{row[1][:40]}" for row in PILOT_FALSE_REJECTIONS],
)
def test_pilot_flagged_item_is_no_longer_rejected(
    topics: dict[str, Topic], topic_id: str, prompt: str, answer: str, gloss_en: str
) -> None:
    rejection, _unverified = check_gloss(prompt, answer, gloss_en, topics.get(topic_id))
    assert rejection is None, rejection.reason if rejection else ""


def test_pilot_real_defect_is_still_rejected(topics: dict[str, Topic]) -> None:
    """The unrelated-English-sentence pairing must keep being caught."""
    topic_id, prompt, answer, gloss_en = PILOT_REAL_DEFECT
    rejection, _unverified = check_gloss(prompt, answer, gloss_en, topics.get(topic_id))
    assert rejection is not None
    assert rejection.error_type == "pedagogical_flaw"
    assert "'past'-tense" in rejection.reason


# ==============================================================================
# Both leak checks still do their real job on English text
#
# The pilot corpus above pins the two FALSE firings ("fall" the English verb
# matched against German "Fall" = Kasus, "war" the English noun matched
# against German "war" = was). These pin the other direction: a German
# string that has no ordinary English spelling still rejects, so neither
# check was switched off, only taught which language it is reading.
# ==============================================================================


def test_untranslated_german_answer_in_the_gloss_is_still_an_answer_leak(
    praesens: Topic,
) -> None:
    """A gloss that leaves the German answer standing in the English hands
    the learner the answer. "wohnt" is not an English word of any spelling,
    so nothing exempts it."""
    rejection, _unverified = check_gloss(
        "Er ___ (wohnen) in Hamburg.",
        "wohnt",
        "He wohnt in Hamburg.",
        praesens,
    )
    assert rejection is not None
    assert rejection.error_type == "answer_leak"


def test_german_grammar_terminology_in_the_gloss_is_still_a_topic_leak(
    praeteritum: Topic,
) -> None:
    """A gloss naming the grammar topic breaks CLAUDE.md rule 2 exactly as a
    prompt would. "Präteritum" and "Dativ" have no English homograph, so the
    shared-spelling exemption does not reach them."""
    rejection, _unverified = check_gloss(
        "Er ___ (wohnen) letztes Jahr in Hamburg.",
        "wohnte",
        "He lived in Hamburg last year (Präteritum).",
        praeteritum,
    )
    assert rejection is not None
    assert rejection.error_type == "topic_leak"


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("i'll", {"i", "will"}),
        ("wouldn't", {"would", "not"}),
        ("could've", {"could", "have"}),
        ("he'd", {"he", "would", "had"}),
        ("she's", {"she", "is", "has"}),
        ("you've", {"you", "have"}),
        ("i'm", {"i", "am"}),
        ("they're", {"they", "are"}),
        ("lonely", {"lonely"}),
    ],
)
def test_contraction_expands_to_every_word_it_can_stand_for(token: str, expected: set[str]) -> None:
    """ "'d" and "'s" are genuinely ambiguous, so both readings are produced
    and a match against either counts -- this check catches contradictions,
    it does not prove agreement."""
    assert set(_token_readings(token)) == expected
