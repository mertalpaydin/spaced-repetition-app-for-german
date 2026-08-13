"""Unit and golden tests for the generation pipeline and spec sheet contracts."""

import json
from pathlib import Path

import pytest
from src.contracts import GenerationRequest
from src.generation.batch_client import CostTracker, MockBatchClient
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec, load_spec
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
                    f"Context topic '{spec.topic_id}' requests single-sentence type '{t}'"
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


@pytest.mark.golden
def test_prompt_construction_is_stable(all_specs: list[TopicSpec]) -> None:
    """Fixed spec -> byte-identical prompt output across runs."""
    builder = PromptBuilder()
    spec = all_specs[0]
    prompt1 = builder.build_generation_prompt(spec, count=5, difficulty=1)
    prompt2 = builder.build_generation_prompt(spec, count=5, difficulty=1)

    assert prompt1 == prompt2
    parsed = json.loads(prompt1)
    assert parsed["topic_id"] == spec.topic_id
    assert parsed["count"] == 5


def test_batch_submit_is_idempotent() -> None:
    """Resubmitting the same request set returns the existing batch ID without duplicate billing."""
    client = MockBatchClient()
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=10,
        difficulty=1,
        item_types=["cloze_free"],
    )

    batch_id_1 = client.submit([req])
    batch_id_2 = client.submit([req])
    assert batch_id_1 == batch_id_2
    assert len(client.submitted_batches) == 1


def test_batch_poll_retries_on_429_with_backoff() -> None:
    """Poll handles 429 rate limit simulations and completes on subsequent check."""
    client = MockBatchClient()
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=5,
        difficulty=1,
        item_types=["cloze_free"],
    )
    batch_id = client.submit([req])
    client.rate_limit_simulations.add(batch_id)

    status_1 = client.poll(batch_id)
    assert status_1 == "pending"

    status_2 = client.poll(batch_id)
    assert status_2 == "completed"


def test_batch_retrieve_on_incomplete_batch_raises() -> None:
    """Attempting to retrieve an incomplete batch raises RuntimeError."""
    client = MockBatchClient()
    req = GenerationRequest(
        topic_id="dativ_nach_praeposition",
        count=5,
        difficulty=1,
        item_types=["cloze_free"],
    )
    batch_id = client.submit([req])
    with pytest.raises(RuntimeError, match="not completed"):
        client.retrieve(batch_id)


def test_every_candidate_carries_exactly_three_distractors() -> None:
    """Verify retrieved CandidateItems carry exactly 3 distractors with valid taxonomy topics."""
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
    from src.generation.spec import build_spec_for_topic, save_spec

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
