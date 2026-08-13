"""Unit and golden fixture tests for the learner error mining module (Falko-MERLIN / ERRANT)."""

from pathlib import Path

import pytest
from src.corpus.learner_errors import (
    EmpiricalConfusionMatrix,
    LearnerErrorMapper,
)
from src.taxonomy.loader import load_taxonomy


@pytest.fixture
def mapped_sample_path(data_fixtures_dir: Path) -> Path:
    return data_fixtures_dir / "corpus" / "mapped_sample.jsonl"


def test_learner_error_mapper_heuristics() -> None:
    """Test heuristic mapping of ERRANT edit tags."""
    mapper = LearnerErrorMapper()
    assert mapper.map_error("R:DET:CASE:DAT") == "kasus_dativ_formen"
    assert mapper.map_error("R:PREP:WECHSEL") == "dativ_nach_praeposition"
    assert mapper.map_error("R:VERB:TENSE:PERF_AUX", "Er ist gegangen") == "perfekt_sein"
    assert mapper.map_error("R:PREP", "Wegen des Regens") == "praepositionen_genitiv"
    assert mapper.map_error("UNKNOWN_TAG") is None


@pytest.mark.golden
def test_mapped_sample_golden_fixture(
    mapped_sample_path: Path,
) -> None:
    """Assert that the 100-sample mapped learner error fixture maps to valid taxonomy IDs."""
    errors = LearnerErrorMapper.load_mapped_sample(mapped_sample_path)
    assert len(errors) == 100, f"Expected 100 mapped learner errors, got {len(errors)}"

    taxonomy_topics = {t.id for t in load_taxonomy()}

    for err in errors:
        assert err.id.startswith("merlin_")
        assert len(err.original_text) > 0
        assert len(err.corrected_text) > 0
        assert err.mapped_topic_id is not None
        assert err.mapped_topic_id in taxonomy_topics, (
            f"Mapped topic ID '{err.mapped_topic_id}' in error {err.id} does not exist in taxonomy!"
        )


def test_empirical_confusion_matrix() -> None:
    """Test empirical confusion matrix accumulation and rate calculations."""
    matrix = EmpiricalConfusionMatrix()

    # Record errors: learner on Dativ answering with Akkusativ
    for _ in range(8):
        matrix.record_error("dativ_nach_praeposition", "akkusativ_nach_praeposition")
    for _ in range(2):
        matrix.record_error("dativ_nach_praeposition", "kasus_nominativ")

    rate = matrix.get_confusion_rate("dativ_nach_praeposition", "akkusativ_nach_praeposition")
    assert rate == 0.80

    top = matrix.get_top_confusions_for_topic("dativ_nach_praeposition", top_n=2)
    assert len(top) == 2
    assert top[0] == ("akkusativ_nach_praeposition", 8)
    assert top[1] == ("kasus_nominativ", 2)

    # Empty query returns 0.0
    assert matrix.get_confusion_rate("unseen_topic", "other") == 0.0


def test_missing_mapped_sample_raises_error(tmp_path: Path) -> None:
    """Test loader raises FileNotFoundError for missing path."""
    with pytest.raises(FileNotFoundError):
        LearnerErrorMapper.load_mapped_sample(tmp_path / "missing.jsonl")
