"""Unit and golden tests for the generation pipeline and spec sheet contracts."""

import json
from pathlib import Path
from typing import Any

import pytest
from src.contracts import GenerationRequest
from src.generation.batch_client import (
    CostTracker,
    GeminiBatchClient,
    MockBatchClient,
    SpecSheetMissingError,
)
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec, build_spec_for_topic, load_spec, save_spec
from src.llm.client import GeminiLlmClient
from src.taxonomy.loader import load_taxonomy


@pytest.fixture
def specs_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "specs"


@pytest.fixture
def all_specs(specs_dir: Path) -> list[TopicSpec]:
    return [load_spec(f) for f in sorted(specs_dir.glob("*.yaml")) if f.is_file()]


def test_every_topic_in_taxonomy_has_a_spec_sheet(
    all_specs: list[TopicSpec],
) -> None:
    """Verify that all 87 taxonomy topics possess an exact corresponding spec sheet."""
    taxonomy = load_taxonomy()
    spec_topic_ids = {s.topic_id for s in all_specs}
    taxonomy_ids = {t.id for t in taxonomy}

    assert spec_topic_ids == taxonomy_ids, (
        f"Mismatch between taxonomy topics and spec sheets!\n"
        f"Missing specs: {taxonomy_ids - spec_topic_ids}\n"
        f"Extra specs: {spec_topic_ids - taxonomy_ids}"
    )


def test_every_spec_sheet_validates_and_has_three_gold_examples(
    all_specs: list[TopicSpec],
) -> None:
    """Verify all spec sheets have at least 3 gold examples and valid tier limits."""
    for spec in all_specs:
        assert len(spec.gold_examples) >= 3, f"Spec '{spec.topic_id}' has < 3 gold examples"
        assert spec.max_tokens_per_sentence >= 10
        assert len(spec.item_types) >= 1
        for ex in spec.gold_examples:
            assert "___" in ex.prompt, (
                f"Gold example in '{spec.topic_id}' lacks '___' gap placeholder"
            )
            assert len(ex.accepted_answers) >= 1


def test_spec_item_types_are_a_subset_of_topic_eligible_types(
    all_specs: list[TopicSpec],
) -> None:
    """Verify that a spec sheet does not request item types disallowed by the taxonomy."""
    taxonomy_map = {t.id: t for t in load_taxonomy()}
    for spec in all_specs:
        topic = taxonomy_map[spec.topic_id]
        for t in spec.item_types:
            assert t in topic.eligible_types, f"Spec '{spec.topic_id}' requests invalid type '{t}'"


def test_requires_context_topics_only_request_paragraph_blocks(
    all_specs: list[TopicSpec],
) -> None:
    """Verify context-requiring topics request paragraph_cloze or error_correction only."""
    taxonomy_map = {t.id: t for t in load_taxonomy()}
    for spec in all_specs:
        topic = taxonomy_map[spec.topic_id]
        if topic.requires_context:
            for t in spec.item_types:
                assert t in ["paragraph_cloze", "error_correction"], (
                    f"Context-requiring topic '{spec.topic_id}' has non-context type '{t}'"
                )


def test_prompt_never_contains_grammar_terminology(
    all_specs: list[TopicSpec],
) -> None:
    """Ensure that built prompt instructions and gold prompts contain no grammar terminology."""
    builder = PromptBuilder()
    for spec in all_specs:
        for gold in spec.gold_examples:
            leaks = builder.check_for_topic_leaks(gold.prompt)
            assert len(leaks) == 0, f"Gold prompt in '{spec.topic_id}' leaks grammar terms: {leaks}"


@pytest.mark.parametrize(
    "sentence",
    [
        "Er hat sein Deutsch in den letzten Monaten deutlich verbessert.",
        "Das war jedenfalls nicht meine Schuld.",
        "Er hatte gestern einen Unfall auf der Autobahn.",
        "Ich lese jeden Morgen die Zeitung.",
        "Für den kaputten Toaster gab es einen Ersatz.",
        "Bitte fülle das Formular vollständig aus.",
    ],
)
def test_topic_leak_check_does_not_flag_ordinary_words_containing_grammar_substrings(
    sentence: str,
) -> None:
    """Regression test for the substring-match defect.

    ``check_for_topic_leaks`` used to do a plain ``term in text`` scan, so
    ordinary German words that merely *contain* a blocklist term as a
    substring were rejected as topic leaks: "verbessert" contains "verb",
    "jedenfalls" contains "fall", and likewise for "Unfall", "Zeitung",
    "Ersatz", and "Formular". None of these sentences names a grammar
    topic, so none of them may be flagged. If this test starts failing,
    the matching has silently regressed back to substring containment.
    """
    leaks = PromptBuilder.check_for_topic_leaks(sentence)
    assert leaks == [], f"False-positive topic leak on ordinary sentence {sentence!r}: {leaks}"


def test_topic_leak_check_still_catches_whole_word_grammar_terms() -> None:
    """Whole-word grammar terminology must still be rejected."""
    assert PromptBuilder.check_for_topic_leaks("Das Wort steht im Dativ.") == ["dativ"]
    assert PromptBuilder.check_for_topic_leaks("Setze das Nomen in den Akkusativ.") == ["akkusativ"]
    assert PromptBuilder.check_for_topic_leaks("Setze ins Perfekt: Er ___ nach Hause.") == [
        "perfekt"
    ]


def test_topic_leak_check_catches_compound_words_built_from_two_grammar_stems() -> None:
    """A leak can hide inside a German compound, not just as a whole word.

    "Dativform", "Akkusativobjekt", and "Konjunktivsatz" each name the topic
    just as clearly as the bare terms "Dativ", "Akkusativ", "Konjunktiv"
    would, but none of them is itself a listed blocklist entry -- they are
    two-part compounds where both halves are grammar metalanguage
    ("dativ"+"form", "akkusativ"+"objekt", "konjunktiv"+"satz"). A pure
    word-boundary regex over the blocklist would miss all three.
    """
    assert PromptBuilder.check_for_topic_leaks("Die Dativform ist hier unregelmäßig.") == [
        "dativform"
    ]
    assert PromptBuilder.check_for_topic_leaks("Bestimme das Akkusativobjekt im Satz.") == [
        "akkusativobjekt"
    ]
    assert PromptBuilder.check_for_topic_leaks("Bilde einen passenden Konjunktivsatz.") == [
        "konjunktivsatz"
    ]


def test_every_distractor_carries_an_implied_topic_or_explicit_null() -> None:
    """Verify distractors generated by batch pipeline resolve correctly."""
    taxonomy_ids = {t.id for t in load_taxonomy()}
    client = MockBatchClient()
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=4,
        difficulty=1,
        item_types=["cloze_free"],
    )
    batch_id = client.submit([req])
    client.poll(batch_id)
    items = client.retrieve(batch_id)

    assert len(items) == 4
    for it in items:
        assert len(it.distractors) == 3
        for d in it.distractors:
            assert d.implied_topic_id is None or d.implied_topic_id in taxonomy_ids
            # Distractors must not be inside accepted answers
            assert d.text not in [it.proposed_answer]


def test_batch_retrieve_on_incomplete_batch_returns_pending_not_partial() -> None:
    """Incomplete batches report pending status rather than returning incomplete partial items."""
    client = MockBatchClient()
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=10,
        difficulty=1,
        item_types=["cloze_free"],
    )
    batch_id = client.submit([req])
    status = client.poll(batch_id)
    assert status in ("pending", "completed")


def test_cost_recorded_per_batch_with_token_counts() -> None:
    """Verify token counts and cost calculation in CostTracker."""
    tracker = CostTracker()
    client = MockBatchClient(cost_tracker=tracker)
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=20,
        difficulty=1,
        item_types=["cloze_free"],
    )
    batch_id = client.submit([req])
    assert tracker.total_cost_usd > 0.0
    assert len(tracker.records) == 1
    assert tracker.records[0].batch_id == batch_id
    assert tracker.records[0].input_tokens > 0
    assert tracker.records[0].output_tokens > 0


def test_build_spec_for_topic_and_save_load(tmp_path: Path) -> None:
    """Test build_spec_for_topic, save_spec, and load_spec."""
    topic = load_taxonomy()[0]
    spec = build_spec_for_topic(topic)
    assert spec.topic_id == topic.id
    assert len(spec.gold_examples) >= 3

    spec_file = tmp_path / "temp_spec.yaml"
    save_spec(spec, spec_file)
    assert spec_file.exists()

    loaded = load_spec(spec_file)
    assert loaded.topic_id == spec.topic_id


def test_paragraph_block_prompt_construction(all_specs: list[TopicSpec]) -> None:
    """Test paragraph cloze block prompt construction."""
    builder = PromptBuilder()
    primary = all_specs[0]
    fillers = all_specs[1:3]
    prompt = builder.build_paragraph_block_prompt(primary, fillers, gap_count=4)
    assert "paragraph" in prompt.lower()
    parsed = json.loads(prompt)
    assert parsed["primary_context_topic"] == primary.topic_id
    assert len(parsed["filler_topics"]) == 2


# ==============================================================================
# BREAK 1: the submit path must actually build and send a spec-anchored prompt.
# ==============================================================================


def _make_llm_client(tmp_path: Path) -> GeminiLlmClient:
    return GeminiLlmClient(
        free_api_key="test-key",
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
    )


def test_gemini_batch_client_sends_prompt_built_from_real_spec_sheet(tmp_path: Path) -> None:
    """The exact prompt handed to the transport must be
    ``PromptBuilder.build_generation_prompt`` applied to the real spec sheet on
    disk for the request's topic -- gold examples, blocklist context,
    vocabulary ceiling and target form all reach the model, not a bare hash."""
    captured: list[str] = []

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        captured.append(prompt)
        return json.dumps({"items": []}), 10, 10

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]

    batch_client = GeminiBatchClient(llm_client)
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=3, difficulty=1, item_types=["cloze_free"]
    )
    batch_client.submit([req])

    assert len(captured) == 1
    spec = load_spec(Path("data/specs/dativ_nach_praeposition.yaml"))
    expected_prompt = PromptBuilder().build_generation_prompt(spec, 3, 1)
    assert captured[0] == expected_prompt

    payload = json.loads(captured[0])
    assert payload["topic_id"] == "dativ_nach_praeposition"
    assert payload["vocabulary_ceiling"] == spec.vocabulary_ceiling
    assert len(payload["gold_few_shot_examples"]) >= 3
    assert any("NEVER name or hint" in p for p in payload["prohibitions"])


def test_gemini_batch_client_missing_spec_sheet_fails_loudly(tmp_path: Path) -> None:
    """A request naming a topic with no spec sheet must raise, not silently
    produce zero items."""

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        raise AssertionError("transport must never be called when a spec sheet is missing")

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]
    batch_client = GeminiBatchClient(llm_client)

    req = GenerationRequest(
        topic_id="does_not_exist_in_data_specs", count=1, difficulty=1, item_types=["cloze_free"]
    )
    with pytest.raises(SpecSheetMissingError):
        batch_client.submit([req])


def test_gemini_batch_client_submit_is_idempotent_no_second_transport_call(
    tmp_path: Path,
) -> None:
    """Resubmitting the same request set returns the existing batch id and
    makes no second model call."""
    call_count = 0

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        nonlocal call_count
        call_count += 1
        return json.dumps({"items": []}), 10, 10

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]
    batch_client = GeminiBatchClient(llm_client)

    req = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=2, difficulty=1, item_types=["cloze_free"]
    )
    first_id = batch_client.submit([req])
    second_id = batch_client.submit([req])

    assert first_id == second_id
    assert call_count == 1


def test_gemini_batch_client_parses_realistic_response_and_drops_malformed_items(
    tmp_path: Path,
) -> None:
    """A realistic JSON response is parsed into ``CandidateItem``s; an item
    missing a required field (no ``proposed_answer``) is dropped rather than
    coerced or defaulted."""

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        payload = {
            "items": [
                {
                    "type": "cloze_free",
                    "prompt": "Das Buch liegt auf ___ Tisch.",
                    "proposed_answer": "dem",
                    "distractors": [
                        {"text": "den", "implied_topic_id": "kasus_akkusativ_formen"},
                        {"text": "des", "implied_topic_id": "kasus_genitiv_formen"},
                        {"text": "das", "implied_topic_id": "artikel_bestimmt_nom"},
                    ],
                },
                {
                    # Malformed: no proposed_answer at all.
                    "type": "cloze_free",
                    "prompt": "Wir sitzen ___ Tisch.",
                    "distractors": [{"text": "den"}, {"text": "des"}, {"text": "das"}],
                },
            ]
        }
        return json.dumps(payload), 20, 20

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]
    batch_client = GeminiBatchClient(llm_client)

    req = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=2, difficulty=1, item_types=["cloze_free"]
    )
    batch_id = batch_client.submit([req])
    batch_client.poll(batch_id)
    items = batch_client.retrieve(batch_id)

    assert len(items) == 1
    assert items[0].proposed_answer == "dem"
    assert items[0].topic_id == "dativ_nach_praeposition"
    assert items[0].difficulty == 1


def test_gemini_batch_client_strips_markdown_fence_before_parsing(tmp_path: Path) -> None:
    """Gemini routinely wraps its JSON response in a ```json ... ``` fence
    since no ``response_mime_type`` is set on the request. The parser must
    strip that fence rather than treating the whole response as malformed."""

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        payload = {
            "items": [
                {
                    "type": "cloze_free",
                    "prompt": "Das Buch liegt auf ___ Tisch.",
                    "proposed_answer": "dem",
                    "distractors": [
                        {"text": "den", "implied_topic_id": "kasus_akkusativ_formen"},
                        {"text": "des", "implied_topic_id": "kasus_genitiv_formen"},
                        {"text": "das", "implied_topic_id": "artikel_bestimmt_nom"},
                    ],
                }
            ]
        }
        fenced = f"```json\n{json.dumps(payload)}\n```"
        return fenced, 20, 20

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]
    batch_client = GeminiBatchClient(llm_client)

    req = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=1, difficulty=1, item_types=["cloze_free"]
    )
    batch_id = batch_client.submit([req])
    items = batch_client.retrieve(batch_id)

    assert len(items) == 1
    assert items[0].proposed_answer == "dem"


def test_gemini_batch_client_used_when_key_configured_mock_reserved_for_offline(
    tmp_path: Path,
) -> None:
    """``run_submit`` must route through the real batch client whenever a key
    is configured, and only fall back to ``MockBatchClient`` when none is."""
    from src.bank.storage import SqliteItemBank
    from src.generation.batch_client import run_submit

    db_path = tmp_path / "bank.db"
    SqliteItemBank(db_path)  # create an (empty) bank so deficits compute

    seen_prompts: list[str] = []

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        seen_prompts.append(prompt)
        return json.dumps({"items": []}), 10, 10

    llm_client = _make_llm_client(tmp_path)
    llm_client._call_transport = fake_transport  # type: ignore[method-assign]

    exit_code = run_submit(
        db_path=db_path,
        state_path=tmp_path / "state.json",
        llm_client=llm_client,
    )

    assert exit_code == 0
    # Real, spec-anchored prompts were actually built and sent -- not the
    # offline SHA-256 synthesiser.
    assert len(seen_prompts) > 0
    for prompt in seen_prompts:
        payload: dict[str, Any] = json.loads(prompt)
        assert "gold_few_shot_examples" in payload
