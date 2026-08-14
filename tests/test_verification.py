"""Unit and golden tests for the 4-layer verification chain and kill gate."""

import json
from pathlib import Path

import pytest
from src.contracts import CandidateItem, Distractor, Topic
from src.generation.spec import TopicSpec, load_spec
from src.lexicon.vocabulary import VocabularyStore
from src.taxonomy.loader import load_taxonomy
from src.verification.pipeline import VerificationPipeline


@pytest.fixture
def adversarial_suite_path(data_fixtures_dir: Path) -> Path:
    return data_fixtures_dir / "verification" / "adversarial_suite.jsonl"


@pytest.fixture
def vocab_store(data_fixtures_dir: Path) -> VocabularyStore:
    vocab_path = data_fixtures_dir / "corpus" / "vocab_levels.json"
    if vocab_path.exists():
        return VocabularyStore.load(vocab_path)
    return VocabularyStore({"buch": "A1", "tisch": "A1", "liegen": "A1"})


@pytest.fixture
def pipeline(vocab_store: VocabularyStore) -> VerificationPipeline:
    return VerificationPipeline(vocab_store=vocab_store)


@pytest.fixture
def sample_spec(repo_root: Path) -> TopicSpec:
    return load_spec(repo_root / "data" / "specs" / "dativ_nach_praeposition.yaml")


@pytest.fixture
def sample_topic() -> Topic:
    taxonomy = load_taxonomy()
    return next(t for t in taxonomy if t.id == "dativ_nach_praeposition")


@pytest.mark.golden
def test_adversarial_suite_catches_all_known_defects(
    pipeline: VerificationPipeline,
    adversarial_suite_path: Path,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Verify that all 30 handcrafted adversarial candidate items are rejected."""
    assert adversarial_suite_path.exists()

    with adversarial_suite_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == 30, f"Expected 30 adversarial items, found {len(lines)}"

    rejected_count = 0
    for idx, line in enumerate(lines, start=1):
        data = json.loads(line)
        expected_layer = data.pop("expected_layer", None)
        data.pop("defect", None)
        item = CandidateItem.model_validate(data)

        res = pipeline.verify_item(item, spec=sample_spec)
        assert not res.passed, (
            f"Adversarial item #{idx} ('{item.prompt}') unexpectedly passed verification!"
        )
        assert res.layer_failed is not None
        assert res.error_type is not None
        if expected_layer:
            assert res.layer_failed == expected_layer, (
                f"Item #{idx} expected failure at layer {expected_layer}, "
                f"failed at {res.layer_failed} ({res.reason})"
            )
        rejected_count += 1

    assert rejected_count == 30


@pytest.mark.golden
def test_known_good_suite_passes_all_verification_layers(
    pipeline: VerificationPipeline,
    data_fixtures_dir: Path,
) -> None:
    """Verify that all items in known_good.jsonl pass the complete verification chain."""
    known_good_path = data_fixtures_dir / "verification" / "known_good.jsonl"
    assert known_good_path.exists()

    with known_good_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) >= 30, f"Expected at least 30 known good items, found {len(lines)}"

    for line in lines:
        data = json.loads(line)
        item = CandidateItem(
            topic_id=data["topic_id"],
            type=data["type"],
            difficulty=data["difficulty"],
            prompt=data["prompt"],
            proposed_answer=data["accepted_answers"][0],
            distractors=[Distractor(text=d["text"]) for d in data.get("distractors", [])],
            cue=data.get("cue"),
        )
        res = pipeline.verify_item(item)
        assert res.passed, f"Known good item failed: {res.reason} (prompt: '{item.prompt}')"


def test_clean_items_pass_all_four_layers(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Verify that a compliant candidate item successfully passes all verification layers."""
    clean_item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
        carrier_lemmas=["Buch", "liegen", "Tisch"],
    )

    res = pipeline.verify_item(clean_item, spec=sample_spec, topic=sample_topic)
    assert res.passed
    assert res.layer_failed is None
    assert res.error_type is None


def test_kill_gate_trips_when_error_rate_exceeds_15_percent(
    pipeline: VerificationPipeline,
    sample_spec: TopicSpec,
    sample_topic: Topic,
) -> None:
    """Assert kill gate trips when error rate exceeds the 15% threshold."""
    clean_item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
    )
    bad_item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf dem Tisch.",  # missing gap
        proposed_answer="dem",
        distractors=[
            Distractor(text="den"),
            Distractor(text="des"),
            Distractor(text="das"),
        ],
    )

    # 8 clean items, 2 bad items = 20% error rate > 15% threshold
    batch = [clean_item] * 8 + [bad_item] * 2
    report = pipeline.verify_batch(batch, spec=sample_spec, topic=sample_topic)

    assert report.total_candidates == 10
    assert report.passed_count == 8
    assert report.failed_count == 2
    assert report.error_rate == 0.20
    assert report.kill_gate_tripped is True


def test_layer1_rejects_missing_gap(pipeline: VerificationPipeline, sample_spec: TopicSpec) -> None:
    """Layer 1 rejects items without a gap placeholder."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf dem Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1


def test_layer1_rejects_wrong_distractor_count(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 1 rejects items with != 3 distractors."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1


def test_layer1_rejects_vocabulary_ceiling_violations(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 1 rejects items containing vocabulary beyond CEFR ceiling."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Die verfassungswidrige Demonstrationsverbotsverordnung liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 1
    assert res.error_type == "vocabulary_ceiling_violation"


def test_layer2_morphosyntactic_validation(
    pipeline: VerificationPipeline, sample_spec: TopicSpec
) -> None:
    """Layer 2 catches lowercase sentence starts and missing terminal punctuation."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="das Buch liegt auf ___ Tisch",  # lowercase start + no terminal punct
        proposed_answer="dem",
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    res = pipeline.verify_item(item, spec=sample_spec)
    assert not res.passed
    assert res.layer_failed == 2


def test_layer3_detects_ambiguous_completions(pipeline: VerificationPipeline) -> None:
    """Layer 3 catches under-constrained prompts admitting open alternative completions."""
    item = CandidateItem(
        topic_id="verb_praesens_regelm",
        type="cloze_free",
        difficulty=1,
        prompt="Ich trinke ___ Kaffee.",
        proposed_answer="oft",
        distractors=[Distractor(text="gern"), Distractor(text="heute"), Distractor(text="viel")],
    )
    res = pipeline.verify_item(item)
    assert not res.passed
    assert res.layer_failed == 3
    assert res.error_type == "ambiguity"
