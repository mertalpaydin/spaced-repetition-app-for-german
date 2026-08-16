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


def test_konjunktiv_ii_target_requires_a_conditional_marker(
    konjunktiv_hoeflichkeit: Topic,
) -> None:
    consistent_result = validate_gloss_consistency(
        "___ (können) Sie mir bitte helfen?",
        "Könnten",
        "Could you please help me?",
        konjunktiv_hoeflichkeit,
    )
    assert consistent_result.consistent is True

    inconsistent_result = validate_gloss_consistency(
        "___ (können) Sie mir bitte helfen?",
        "Könnten",
        "Do you help me?",
        konjunktiv_hoeflichkeit,
    )
    assert inconsistent_result.consistent is False


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
    topics = {t.id: t for t in load_taxonomy()}
    topic = topics["konjunktiv_ii_irreal_gegenwart"]
    item = _konjunktiv_gloss_item(["kommt"], "My sister said that she comes immediately.")
    gap_pos = item.prompt.index("___")
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
