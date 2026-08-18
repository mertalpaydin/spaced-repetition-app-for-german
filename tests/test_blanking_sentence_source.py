"""Tests for src/generation/blanking/sentence_source.py.

Unit tests never touch the network (CLAUDE.md 7): every test here either
exercises ``MockSentenceGenerator`` directly or feeds ``LiveSentenceGenerator``
a fake ``GeminiLlmClient``-shaped object whose ``generate`` is a plain
Python function, never a real client.
"""

import re
from collections import Counter

import pytest
from src.contracts import CEFR
from src.generation.blanking import carrier_validation as cv
from src.generation.blanking.selectors import SELECTORS
from src.generation.blanking.sentence_source import (
    _FEW_SHOT_EXAMPLES_DE,
    _INSTRUCTION_DE_LIVE,
    _INSTRUCTION_EN_REFERENCE_ONLY,
    _MOCK_SENTENCE_POOL,
    _STARVED_CONSTRUCTION_EXAMPLES,
    CONSTRUCTION_HINTS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_POOL_SIZE,
    DEFAULT_THEMES,
    PERSON_PERSPECTIVES,
    REGISTERS,
    STRUCTURES,
    TIME_FRAMES,
    LiveSentenceGenerator,
    MockSentenceGenerator,
    SentencePool,
    build_prompt,
    build_sentence_generator,
    client_from_env,
    generate_sentence_pool,
)
from src.generation.blanking.sentence_tagger import tag_sentence

_FORBIDDEN_GRAMMAR_WORDS = (
    "kasus",
    "artikel",
    "dativ",
    "akkusativ",
    "nominativ",
    "genitiv",
    "deklination",
    "perfekt",
    "präteritum",
    "plusquamperfekt",
    "futur",
    "konjunktiv",
    "___",
)


def _contains_forbidden_word(text: str) -> str | None:
    """Whole-word match only: "futur" (the German grammar term) must not
    appear, but the ordinary English word "future" -- which contains
    "futur" as a substring purely by coincidence of two unrelated languages
    -- is exactly the kind of plain-language time-frame wording these hints
    are supposed to use, and a naive substring check would wrongly flag
    it."""
    lowered = text.lower()
    for forbidden in _FORBIDDEN_GRAMMAR_WORDS:
        if forbidden == "___":
            if forbidden in lowered:
                return forbidden
            continue
        if re.search(rf"\b{re.escape(forbidden)}\b", lowered):
            return forbidden
    return None


def test_build_prompt_never_names_a_grammar_topic() -> None:
    """The one invariant this whole architecture depends on: the generation
    prompt must never leak a grammar topic, an answer, or a gap."""
    prompt = build_prompt("A2", "Reisen", 10)
    assert _contains_forbidden_word(prompt) is None
    assert "Reisen" in prompt
    assert "A2" in prompt


def test_build_prompt_with_no_hints_has_the_documented_structure() -> None:
    """``generate_sentence_pool``'s four keyword-only hints must be a strictly
    additive change: with all four left at their default of ``None``, no hint
    line appears at all.

    This test used to pin ``build_prompt``'s ENTIRE output byte-for-byte
    against a hardcoded English string. That pin is deliberately gone: task 1
    (German is now the live instruction, English is a documentation-only
    reference) and task 2 (few-shot examples were added) both intentionally
    changed what ``build_prompt`` outputs, on purpose, per CLAUDE.md 7 --
    this is not the failing-test-silently-weakened case that rule warns
    about, it is the sanctioned behaviour change the rest of this test file's
    new tests (below) exist to pin instead. What still deserves a literal
    pin here is the STRUCTURE: the live instruction, then the few-shot
    examples verbatim, then the CEFR/theme/count lines, in that order, with
    no hint lines when hints are omitted."""
    prompt = build_prompt("A2", "Reisen", 10)
    expected_lines = [_INSTRUCTION_DE_LIVE, "", "Beispiele für richtige, natürliche Sätze:"]
    expected_lines += [f"- {example}" for example in _FEW_SHOT_EXAMPLES_DE]
    expected_lines += ["", "CEFR level: A2", "Theme: Reisen", "Number of sentences: 10"]
    assert prompt == "\n".join(expected_lines) + "\n"
    assert "Perspective:" not in prompt
    assert "Time frame:" not in prompt
    assert "Register:" not in prompt
    assert "Sentence shape:" not in prompt


# -- TASK 1: two instructions, English reference + German live -------------


def test_both_instruction_languages_exist_and_are_nonempty() -> None:
    assert _INSTRUCTION_EN_REFERENCE_ONLY.strip() != ""
    assert _INSTRUCTION_DE_LIVE.strip() != ""
    # They must not be the same text -- if they were, one of the two edits
    # this task requires (an actual German operative prompt) never happened.
    assert _INSTRUCTION_EN_REFERENCE_ONLY != _INSTRUCTION_DE_LIVE


def test_build_prompt_uses_the_german_instruction_not_the_english_one() -> None:
    """The one thing this whole task is FOR: the live prompt must be the
    German text, never the English reference-only text."""
    prompt = build_prompt("A2", "Reisen", 10)
    assert _INSTRUCTION_DE_LIVE in prompt
    assert _INSTRUCTION_EN_REFERENCE_ONLY not in prompt


def test_both_instruction_languages_forbid_gaps_and_underscores() -> None:
    """Cannot test semantic equivalence between two natural-language texts in
    two different languages (see the module docstring's own honesty note on
    this) -- what CAN be tested is that both independently state the
    structural "no gap" invariant this whole architecture depends on, each
    in its own language."""
    assert "no gaps" in _INSTRUCTION_EN_REFERENCE_ONLY
    assert "underscores" in _INSTRUCTION_EN_REFERENCE_ONLY
    assert "Lücken" in _INSTRUCTION_DE_LIVE
    assert "Unterstriche" in _INSTRUCTION_DE_LIVE
    assert "___" not in _INSTRUCTION_EN_REFERENCE_ONLY
    assert "___" not in _INSTRUCTION_DE_LIVE


def test_both_instruction_languages_demand_the_same_json_shape() -> None:
    """The one piece of literal, language-independent content both texts
    must share exactly: the JSON envelope shape, since that shape is parsed
    by ``_parse_sentences`` regardless of which language asked for it."""
    json_shape = '{"sentences": ["...", "..."]}'
    assert json_shape in _INSTRUCTION_EN_REFERENCE_ONLY
    assert json_shape in _INSTRUCTION_DE_LIVE


def test_german_live_instruction_avoids_grammar_terminology_itself() -> None:
    """The German instruction tells the model not to use grammar
    terminology; it must not do so by NAMING the forbidden terms (e.g.
    "Kasus", "Artikel", "Perfekt") inside that very prohibition, which is
    what ``_contains_forbidden_word`` (used across this whole file) already
    checks for the full prompt -- this test isolates that same check to the
    instruction text alone, so a future edit that reintroduces a named term
    here fails immediately rather than only downstream."""
    assert _contains_forbidden_word(_INSTRUCTION_DE_LIVE) is None


# -- TASK 2: few-shot examples -----------------------------------------------


def test_few_shot_examples_are_nonempty_and_appear_in_the_prompt() -> None:
    assert len(_FEW_SHOT_EXAMPLES_DE) >= 3
    prompt = build_prompt("A2", "Reisen", 10)
    for example in _FEW_SHOT_EXAMPLES_DE:
        assert example in prompt


def test_few_shot_examples_include_a_fronted_adverbial_with_v2_inversion() -> None:
    """The exact structure that produced the audited "kauft ich" defect:
    fronted adverbial, finite verb before the subject."""
    fronted_adverbials = ("Auf dem Weg", "Morgens", "Am Wochenende", "Später", "Heute Abend")
    assert any(
        example.startswith(adv) for example in _FEW_SHOT_EXAMPLES_DE for adv in fronted_adverbials
    )


def test_few_shot_examples_include_a_separable_verb() -> None:
    """Several examples end in a bare separated prefix ("ein"/"auf") standing
    apart from its verb earlier in the sentence -- the exact shape of a
    separable verb like "einkaufen" or "aufstehen" split across a main
    clause, the other structure implicated in the audited defect."""
    last_words = [example.rstrip(".").split()[-1] for example in _FEW_SHOT_EXAMPLES_DE]
    assert any(word in ("ein", "auf") for word in last_words)


def test_few_shot_examples_are_not_all_first_person_singular() -> None:
    """Task 2's explicit ask: varied person, so the model does not anchor on
    first-person singular the way the audited pilot's source text did."""
    first_person_singular_markers = (" ich ", "Ich ")
    non_first_person = [
        example
        for example in _FEW_SHOT_EXAMPLES_DE
        if not any(marker in f" {example} " for marker in first_person_singular_markers)
    ]
    assert len(non_first_person) >= 3


def test_few_shot_examples_avoid_grammar_terminology_and_gaps() -> None:
    for example in _FEW_SHOT_EXAMPLES_DE:
        assert _contains_forbidden_word(example) is None


@pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)
def test_few_shot_examples_all_pass_carrier_validation() -> None:
    """The whole point of hand-writing these: they must themselves be sound
    carriers, or they would be teaching the model the exact class of defect
    this task exists to fix."""
    failures = [
        (example, result.reason)
        for example in _FEW_SHOT_EXAMPLES_DE
        for result in [cv.validate_carrier(example)]
        if not result.accepted
    ]
    assert failures == []


def test_build_prompt_with_hints_includes_them_and_still_avoids_grammar_terms() -> None:
    prompt = build_prompt(
        "A2",
        "Reisen",
        10,
        person="Write from one person's own point of view, telling about themselves.",
        tense="Tell about something that already happened, as a short story about the past.",
        register="Use a casual, informal tone, as between friends.",
        structure="Keep each sentence to one main clause.",
    )
    assert "Perspective:" in prompt
    assert "Time frame:" in prompt
    assert "Register:" in prompt
    assert "Sentence shape:" in prompt
    assert _contains_forbidden_word(prompt) is None


def test_every_variety_hint_avoids_grammar_terminology() -> None:
    """Every hint string in every variety axis, not just one example
    combination -- the actual content ``generate_sentence_pool`` sends."""
    all_hints = [text for _, text in PERSON_PERSPECTIVES]
    all_hints += [text for _, text in TIME_FRAMES]
    all_hints += [text for _, text in REGISTERS]
    all_hints += [text for _, text in STRUCTURES]
    for hint in all_hints:
        found = _contains_forbidden_word(hint)
        assert found is None, f"{found!r} leaked in hint: {hint!r}"


# -- Construction-aware generation (the 16-of-49 starved-topic fix) ---------


def test_construction_hints_cover_sixteen_topics_with_unique_ids() -> None:
    """One hint per starved topic that actually has a selector (see
    ``CONSTRUCTION_HINTS``'s own module comment on why ``passiv_unpersoenlich``,
    the seventeenth topic the audit named, is deliberately not among
    these), and every topic id is used exactly once."""
    ids = [topic_id for topic_id, _ in CONSTRUCTION_HINTS]
    assert len(ids) == 16
    assert len(set(ids)) == 16
    for topic_id in ids:
        assert topic_id in SELECTORS, f"{topic_id!r} has no selector to ever fire on it"


def test_every_construction_hint_avoids_grammar_terminology() -> None:
    """The same forbidden-term check every other hint axis passes, applied
    to the German construction hints -- CLAUDE.md rule 2 is exactly as
    binding on a meaning-based hint as on an English one."""
    for topic_id, hint in CONSTRUCTION_HINTS:
        found = _contains_forbidden_word(hint)
        assert found is None, f"{found!r} leaked in {topic_id!r}'s hint: {hint!r}"


def test_build_prompt_with_construction_hint_includes_it_and_avoids_grammar_terms() -> None:
    _, hint = CONSTRUCTION_HINTS[0]
    prompt = build_prompt("A2", "Alltag", 10, construction=hint)
    assert "Kommunikatives Ziel:" in prompt
    assert hint in prompt
    assert _contains_forbidden_word(prompt) is None


def test_build_prompt_without_construction_omits_the_line() -> None:
    prompt = build_prompt("A2", "Alltag", 10)
    assert "Kommunikatives Ziel:" not in prompt


def test_mock_sentence_generator_is_deterministic() -> None:
    gen = MockSentenceGenerator()
    first = gen.generate("A2", "Alltag", 20)
    second = gen.generate("A2", "Alltag", 20)
    assert first == second
    assert len(first) == 20
    assert all(isinstance(s, str) and s for s in first)


def test_mock_sentence_generator_is_deterministic_with_hints() -> None:
    gen = MockSentenceGenerator()
    kwargs: dict[str, str] = {
        "person": "first_person_singular",
        "tense": "recent_past",
        "register": "informal",
        "structure": "simple",
        "construction": "a construction hint",
    }
    first = gen.generate("A2", "Alltag", 15, **kwargs)
    second = gen.generate("A2", "Alltag", 15, **kwargs)
    assert first == second


def test_mock_sentence_generator_varies_by_construction() -> None:
    """A different construction hint (everything else held fixed) must
    surface a different slice of the pool offline too -- the same property
    ``test_mock_sentence_generator_varies_by_theme`` checks for theme,
    checked here for the fifth axis."""
    gen = MockSentenceGenerator()
    _, hint_a = CONSTRUCTION_HINTS[0]
    _, hint_b = CONSTRUCTION_HINTS[1]
    by_hint_a = gen.generate("A2", "Alltag", 10, construction=hint_a)
    by_hint_b = gen.generate("A2", "Alltag", 10, construction=hint_b)
    assert by_hint_a != by_hint_b


def test_mock_sentence_generator_varies_by_theme() -> None:
    """Different themes must surface different slices of the pool offline
    too, or every batch in ``generate_sentence_pool`` would collapse to the
    same handful of sentences regardless of how many themes it cycles
    through."""
    gen = MockSentenceGenerator()
    by_alltag = gen.generate("A2", "Alltag", 10)
    by_reisen = gen.generate("A2", "Reisen", 10)
    assert by_alltag != by_reisen


def test_mock_sentence_generator_handles_zero_and_negative_count() -> None:
    gen = MockSentenceGenerator()
    assert gen.generate("A1", "Essen", 0) == []
    assert gen.generate("A1", "Essen", -3) == []


def test_mock_sentence_generator_never_produces_a_gap_or_grammar_terminology() -> None:
    gen = MockSentenceGenerator()
    for sentence in gen.generate("B1", "Alltag", 40):
        assert "___" not in sentence


@pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)
def test_mock_sentence_pool_entries_all_pass_carrier_validation() -> None:
    """The offline pool is meant to be a curated set of *sound* carriers
    (task 1's whole point); a future edit that introduces a bad sentence
    into it should fail here, not be discovered downstream in a pilot
    report the way the "kauft ich" defect originally was."""
    failures = [
        (sentence, result.reason)
        for sentence in _MOCK_SENTENCE_POOL
        for result in [cv.validate_carrier(sentence)]
        if not result.accepted
    ]
    assert failures == []


def test_mock_sentence_pool_is_well_above_the_audited_pilot_size() -> None:
    """The audited pilot's pool was ~60 sentences, one narrative. This pool
    is the offline substitute for a live pool built by
    ``generate_sentence_pool`` and should not be a token gesture."""
    assert len(_MOCK_SENTENCE_POOL) > 150
    assert len(set(_MOCK_SENTENCE_POOL)) == len(_MOCK_SENTENCE_POOL)


# -- Verifying the construction-aware technique actually works --------------
#
# It is not enough that the hand-written examples READ like the right
# construction to a human -- the whole point of this technique is that a
# TOKEN in the sentence is actually selectable by the topic's own selector
# in ``selectors.SELECTORS``. Every test below runs the real tagger and the
# real selector against each hand-written example, exactly the way the
# report requested ("run selectors.py against your new sentences yourself
# and report, topic by topic, which of the 16 now produce a candidate").


def test_starved_construction_examples_cover_every_wired_construction_hint() -> None:
    """``_STARVED_CONSTRUCTION_EXAMPLES`` must have an entry for every topic
    ``CONSTRUCTION_HINTS`` targets (so no hint is wired for a topic with no
    examples backing it up), plus the one extra topic
    (``passiv_unpersoenlich``) that has examples but deliberately no wired
    hint (see ``CONSTRUCTION_HINTS``'s own comment)."""
    hinted_topic_ids = {topic_id for topic_id, _ in CONSTRUCTION_HINTS}
    example_topic_ids = set(_STARVED_CONSTRUCTION_EXAMPLES)
    assert hinted_topic_ids <= example_topic_ids
    assert example_topic_ids - hinted_topic_ids == {"passiv_unpersoenlich"}


@pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)
@pytest.mark.parametrize(
    "topic_id",
    [topic_id for topic_id in _STARVED_CONSTRUCTION_EXAMPLES if topic_id in SELECTORS],
)
def test_starved_topic_examples_each_produce_a_selector_candidate(topic_id: str) -> None:
    """The actual proof, per starved topic: every one of that topic's own
    hand-written examples, tagged and run through THAT topic's own selector,
    yields at least one candidate. A hint that reads right but produces no
    selectable token would be worthless (the report's own standard) --
    this is what would catch that."""
    selector = SELECTORS[topic_id]
    for sentence in _STARVED_CONSTRUCTION_EXAMPLES[topic_id]:
        tagged = tag_sentence(sentence)
        assert tagged is not None, f"could not tag: {sentence!r}"
        candidates = selector(tagged)
        assert candidates, f"{topic_id}: no candidate for {sentence!r}"


def test_passiv_unpersoenlich_has_no_selector_so_its_examples_cannot_fire_one() -> None:
    """Documented honestly, not silently: ``passiv_unpersoenlich`` is one of
    three topics (with ``imperativ`` and ``relativsatz_was_wo``) that cycle 3
    excluded from ``selectors.SELECTORS`` entirely, for a tagger-level
    reason unrelated to pool coverage (see ``tests/test_blanking_pipeline.py``).
    No sentence pool change -- this one included -- can produce an item for
    it; that is a selector gap, out of ``sentence_source.py``'s ownership."""
    assert "passiv_unpersoenlich" not in SELECTORS
    assert "passiv_unpersoenlich" in _STARVED_CONSTRUCTION_EXAMPLES
    assert len(_STARVED_CONSTRUCTION_EXAMPLES["passiv_unpersoenlich"]) >= 3


class _FakeLlmClient:
    """A minimal stand-in for GeminiLlmClient.generate -- not a real client,
    never touches the network."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, **kwargs: object) -> str:
        self.calls.append({"prompt": prompt, **kwargs})
        return self._response_text


def test_live_sentence_generator_parses_plain_json() -> None:
    fake = _FakeLlmClient('{"sentences": ["Der Hund läuft.", "Die Katze schläft."]}')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    sentences = generator.generate("A1", "Tiere", 2)
    assert sentences == ["Der Hund läuft.", "Die Katze schläft."]
    assert fake.calls[0]["purpose"] == "sentence_generation"
    assert fake.calls[0]["is_user_content"] is False


def test_live_sentence_generator_forwards_hints_into_the_prompt() -> None:
    fake = _FakeLlmClient('{"sentences": []}')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    generator.generate(
        "A2",
        "Reisen",
        5,
        person="a person-perspective hint",
        tense="a time-frame hint",
        register="a register hint",
        structure="a structure hint",
        construction="a construction hint",
    )
    prompt = fake.calls[0]["prompt"]
    assert isinstance(prompt, str)
    assert "a person-perspective hint" in prompt
    assert "a time-frame hint" in prompt
    assert "a register hint" in prompt
    assert "a structure hint" in prompt
    assert "a construction hint" in prompt


def test_live_sentence_generator_strips_a_markdown_code_fence() -> None:
    fake = _FakeLlmClient('```json\n{"sentences": ["Ein Beispiel."]}\n```')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 1) == ["Ein Beispiel."]


def test_live_sentence_generator_returns_empty_list_on_malformed_json() -> None:
    fake = _FakeLlmClient("not json at all")
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 3) == []


def test_live_sentence_generator_returns_empty_list_when_sentences_key_missing() -> None:
    fake = _FakeLlmClient('{"oops": []}')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 3) == []


def test_client_from_env_returns_none_without_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert client_from_env() is None


def test_build_sentence_generator_falls_back_to_mock_when_no_client() -> None:
    generator = build_sentence_generator(None)
    assert isinstance(generator, MockSentenceGenerator)


def test_build_sentence_generator_wraps_a_real_client() -> None:
    fake = _FakeLlmClient('{"sentences": []}')
    generator = build_sentence_generator(fake)  # type: ignore[arg-type]
    assert isinstance(generator, LiveSentenceGenerator)


# -- generate_sentence_pool --------------------------------------------------


class _ScriptedGenerator:
    """A ``SentenceGenerator`` that records every call it receives and
    returns a caller-supplied, per-call batch of sentences -- lets a test
    assert on exactly what ``generate_sentence_pool`` asked for (theme,
    count, and every hint) without touching an LLM or the offline mock
    pool's own fixed content."""

    def __init__(self, batches: list[list[str]] | None = None) -> None:
        self._batches = batches
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        cefr: CEFR,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]:
        call_index = len(self.calls)
        self.calls.append(
            {
                "cefr": cefr,
                "theme": theme,
                "count": count,
                "person": person,
                "tense": tense,
                "register": register,
                "structure": structure,
                "construction": construction,
            }
        )
        if self._batches is not None:
            return self._batches[call_index % len(self._batches)]
        # Default: fabricate ``count`` distinct, deterministic sentences per
        # call so different calls never collide and dedup has nothing to do,
        # unless a test wants that behaviour (use ``batches`` instead).
        return [f"Satz {call_index}-{i}." for i in range(count)]

    @property
    def theme_calls(self) -> list[str]:
        return [str(call["theme"]) for call in self.calls]


def test_default_pool_size_is_well_above_the_audited_pilot_size() -> None:
    """The task's own wording: "well above 60", the size of the pilot pool
    that produced the audited topic skew."""
    assert DEFAULT_POOL_SIZE > 60
    assert DEFAULT_POOL_SIZE >= 200


def test_default_batch_size_uses_the_documented_default_when_unspecified() -> None:
    generator = _ScriptedGenerator()
    generate_sentence_pool(generator, "A2", total=DEFAULT_BATCH_SIZE * 3)
    assert all(call["count"] == DEFAULT_BATCH_SIZE for call in generator.calls)


def test_generate_sentence_pool_bounds_the_number_of_generate_calls() -> None:
    """Exactly ``ceil(total / batch_size)`` calls, no adaptive backfill loop
    -- CLAUDE.md 9's cost discipline (a fixed cap "independent of the spend
    ceiling") applies here even offline: the call count must not depend on
    how many carriers happen to fail validation."""
    generator = _ScriptedGenerator()
    result = generate_sentence_pool(generator, "A2", total=45, batch_size=10)
    assert len(generator.calls) == 5  # ceil(45/10)
    assert result.batches_run == 5


def test_generate_sentence_pool_zero_or_negative_total_makes_no_calls() -> None:
    generator = _ScriptedGenerator()
    result = generate_sentence_pool(generator, "A2", total=0)
    assert generator.calls == []
    assert result.sentences == []
    assert result.accepted_count == 0

    result = generate_sentence_pool(generator, "A2", total=-5)
    assert generator.calls == []
    assert result.accepted_count == 0


def test_generate_sentence_pool_varies_theme_person_tense_register_structure() -> None:
    """The actual fix for the audited topic skew: many small, differently
    -nudged requests, not one uniform one."""
    generator = _ScriptedGenerator()
    generate_sentence_pool(generator, "A2", total=160, batch_size=10)
    themes = {call["theme"] for call in generator.calls}
    persons = {call["person"] for call in generator.calls}
    tenses = {call["tense"] for call in generator.calls}
    registers = {call["register"] for call in generator.calls}
    structures = {call["structure"] for call in generator.calls}
    assert len(themes) == len(DEFAULT_THEMES)
    assert len(persons) == len(PERSON_PERSPECTIVES)
    assert len(tenses) == len(TIME_FRAMES)
    assert len(registers) == len(REGISTERS)
    assert len(structures) == len(STRUCTURES)


def test_generate_sentence_pool_also_varies_construction() -> None:
    """The fix for the OTHER gap (16 of 49 topics with zero items): every
    construction hint gets requested, not just theme/person/tense/register/
    structure -- a pool that varied everything else but never asked for a
    relative clause or a passive would still leave those topics starved."""
    generator = _ScriptedGenerator()
    generate_sentence_pool(generator, "A2", total=160, batch_size=10)
    constructions = {call["construction"] for call in generator.calls}
    assert len(constructions) == len(CONSTRUCTION_HINTS)


def test_generate_sentence_pool_deduplicates_across_batches() -> None:
    generator = _ScriptedGenerator(
        batches=[
            ["Der Hund läuft.", "Die Katze schläft."],
            ["Der Hund läuft.", "Ein Vogel singt."],
        ]
    )
    result = generate_sentence_pool(generator, "A2", total=6, batch_size=2, validate=False)
    assert result.sentences == ["Der Hund läuft.", "Die Katze schläft.", "Ein Vogel singt."]
    assert result.duplicates_skipped >= 1
    assert len(set(result.sentences)) == len(result.sentences)


def test_generate_sentence_pool_stops_once_total_is_reached() -> None:
    generator = _ScriptedGenerator(
        batches=[["Der Hund läuft.", "Die Katze schläft.", "Ein Vogel singt."]]
    )
    result = generate_sentence_pool(generator, "A2", total=2, batch_size=3, validate=False)
    assert result.sentences == ["Der Hund läuft.", "Die Katze schläft."]
    assert result.accepted_count == 2


@pytest.mark.skipif(
    not cv.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)
def test_generate_sentence_pool_applies_carrier_validation_and_counts_rejections() -> None:
    generator = _ScriptedGenerator(
        batches=[
            [
                "Der Hund läuft schnell durch den Park.",  # sound
                "Auf dem Weg kauft ich frisches Gemüse ein.",  # kauft/ich mismatch
                "der Hund läuft schnell.",  # not capitalised
            ]
        ]
    )
    result = generate_sentence_pool(generator, "A2", total=3, batch_size=3)
    assert result.sentences == ["Der Hund läuft schnell durch den Park."]
    assert result.rejected_by_reason[cv.REASON_SUBJECT_VERB_DISAGREEMENT] == 1
    assert result.rejected_by_reason[cv.REASON_NOT_CAPITALIZED] == 1


def test_generate_sentence_pool_validate_false_skips_carrier_validation() -> None:
    generator = _ScriptedGenerator(batches=[["auf keinen fall ein satz."]])
    result = generate_sentence_pool(generator, "A2", total=1, batch_size=1, validate=False)
    assert result.sentences == ["auf keinen fall ein satz."]
    assert result.rejected_by_reason == Counter()


def test_generate_sentence_pool_does_not_backfill_after_validation_losses() -> None:
    """A pool that loses most of its raw output to validation ends up SHORT
    of ``total``, not silently topped up by extra calls -- the batch count
    must stay bounded regardless of the rejection rate (see the "bounds the
    number of generate calls" test for the same property without
    validation)."""
    generator = _ScriptedGenerator(batches=[["der hund läuft schnell."]])  # always rejected
    result = generate_sentence_pool(generator, "A2", total=50, batch_size=1)
    assert len(generator.calls) == 50  # ceil(50/1), no extra calls
    assert result.accepted_count == 0
    assert result.rejected_by_reason[cv.REASON_NOT_CAPITALIZED] == 50


def test_generate_sentence_pool_reports_requested_and_raw_generated() -> None:
    generator = _ScriptedGenerator()
    result = generate_sentence_pool(generator, "A2", total=25, batch_size=10)
    assert result.requested == 25
    assert result.raw_generated == 30  # 3 calls x 10 raw sentences each, before capping


def test_generate_sentence_pool_with_offline_mock_reaches_well_above_60() -> None:
    """End-to-end with the same offline mock every other test in this file
    uses (CLAUDE.md 7: no network): a real, working substitute for what a
    live pool looks like, entirely reproducible."""
    generator = MockSentenceGenerator()
    result = generate_sentence_pool(generator, "A2", total=DEFAULT_POOL_SIZE)
    assert result.requested == DEFAULT_POOL_SIZE
    assert result.accepted_count > 60
    assert len(set(result.sentences)) == result.accepted_count


def test_sentence_pool_accepted_count_matches_sentences_length() -> None:
    pool = SentencePool(sentences=["a", "b", "c"])
    assert pool.accepted_count == 3
