"""Unit and golden tests for the Grammar Topic Taxonomy and Prerequisite DAG."""

import json
from pathlib import Path

import pytest
from src.contracts import CEFR, Topic
from src.taxonomy.loader import load_taxonomy
from src.taxonomy.validator import TaxonomyValidator


@pytest.fixture
def taxonomy_topics() -> list[Topic]:
    """Load the default taxonomy topics from data/taxonomy.yaml."""
    return load_taxonomy()


@pytest.fixture
def validator(taxonomy_topics: list[Topic]) -> TaxonomyValidator:
    """Create a TaxonomyValidator instance for the loaded topics."""
    return TaxonomyValidator(taxonomy_topics)


def test_taxonomy_loads_and_has_expected_volume(taxonomy_topics: list[Topic]) -> None:
    """Verify that taxonomy loads properly and contains 75-90 topics across A1-B2."""
    assert 75 <= len(taxonomy_topics) <= 90, f"Expected 75-90 topics, got {len(taxonomy_topics)}"


def test_taxonomy_cefr_distribution(taxonomy_topics: list[Topic]) -> None:
    """Verify that every CEFR level has substantial topic representation."""
    cefr_counts: dict[CEFR, int] = {"A1": 0, "A2": 0, "B1": 0, "B2": 0}
    for t in taxonomy_topics:
        cefr_counts[t.cefr] += 1

    for level, count in cefr_counts.items():
        assert count >= 18, f"CEFR level {level} has only {count} topics (expected >= 18)"


def test_taxonomy_dag_is_acyclic_and_valid(validator: TaxonomyValidator) -> None:
    """Verify that the prerequisite graph is a valid acyclic DAG with no broken edges."""
    report = validator.validate()
    assert report.is_valid, f"Taxonomy validation failed with errors: {report.errors}"
    assert len(report.cycles) == 0, f"Cycles detected in DAG: {report.cycles}"
    assert len(report.errors) == 0


def test_requires_context_topics_include_paragraph_cloze(
    taxonomy_topics: list[Topic],
) -> None:
    """Verify that topics marked requires_context strictly support paragraph_cloze."""
    context_topics = [t for t in taxonomy_topics if t.requires_context]
    assert len(context_topics) >= 5, "Expected at least 5 context-requiring topics across taxonomy"

    for t in context_topics:
        assert "paragraph_cloze" in t.eligible_types, (
            f"Topic '{t.id}' requires context but lacks 'paragraph_cloze' in eligible_types"
        )


def test_intro_cards_are_complete(taxonomy_topics: list[Topic]) -> None:
    """Verify that every topic has a complete static intro_card."""
    for t in taxonomy_topics:
        assert t.intro_card is not None, f"Topic '{t.id}' is missing intro_card"
        assert len(t.intro_card.summary.strip()) > 0
        assert len(t.intro_card.rule_de.strip()) > 0
        assert len(t.intro_card.worked_examples) >= 1


def test_transitive_prerequisites_computation(validator: TaxonomyValidator) -> None:
    """Verify transitive prerequisites downward propagation."""
    # Plusquamperfekt depends on praeteritum_vollverben and perfekt_haben/perfekt_sein
    # which in turn depend on verb_sein_haben, pronomen_personal_nom, etc.
    prereqs = validator.get_transitive_prereqs("plusquamperfekt")
    assert "praeteritum_vollverben" in prereqs
    assert "verb_sein_haben" in prereqs
    assert "pronomen_personal_nom" in prereqs


def test_descendants_computation(validator: TaxonomyValidator) -> None:
    """Verify downstream dependent computation."""
    descendants = validator.get_descendants("pronomen_personal_nom")
    assert "kasus_akkusativ_formen" in descendants or "verb_praesens_regelm" in descendants
    assert len(descendants) > 10


@pytest.mark.golden
def test_expected_topic_ids_golden(taxonomy_topics: list[Topic], data_fixtures_dir: Path) -> None:
    """Assert topic IDs match golden fixture to guard against silent mutation."""
    golden_file = data_fixtures_dir / "taxonomy" / "expected_ids.json"
    current_ids = [t.id for t in taxonomy_topics]

    if not golden_file.exists():
        # First-time initialization
        with golden_file.open("w", encoding="utf-8") as f:
            json.dump(current_ids, f, indent=2, ensure_ascii=False)

    with golden_file.open("r", encoding="utf-8") as f:
        expected_ids = json.load(f)

    assert current_ids == expected_ids, (
        f"Taxonomy topic IDs have drifted from golden fixture {golden_file}!\n"
        f"Diff: added={set(current_ids) - set(expected_ids)}, "
        f"removed={set(expected_ids) - set(current_ids)}"
    )


def test_loader_file_not_found(tmp_path: Path) -> None:
    """Test loader raises FileNotFoundError for missing path."""
    with pytest.raises(FileNotFoundError):
        load_taxonomy(tmp_path / "non_existent.yaml")


def test_loader_invalid_format(tmp_path: Path) -> None:
    """Test loader raises ValueError when YAML is not a list."""
    invalid_file = tmp_path / "invalid.yaml"
    invalid_file.write_text("key: value\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a top-level list"):
        load_taxonomy(invalid_file)


def test_validator_detects_duplicate_ids() -> None:
    """Test validator reports duplicate topic IDs."""
    t1 = Topic(id="t1", name_de="T1", cefr="A1", description="D", eligible_types=["cloze_free"])
    t2 = Topic(
        id="t1", name_de="T1 duplicate", cefr="A1", description="D", eligible_types=["cloze_free"]
    )
    validator = TaxonomyValidator([t1, t2])
    report = validator.validate()
    assert not report.is_valid
    assert any("Duplicate topic ID: 't1'" in e for e in report.errors)


def test_validator_detects_broken_prereq_reference() -> None:
    """Test validator reports missing prerequisite reference."""
    t1 = Topic(
        id="t1",
        name_de="T1",
        cefr="A1",
        prereqs=["missing_p"],
        description="D",
        eligible_types=["cloze_free"],
    )
    validator = TaxonomyValidator([t1])
    report = validator.validate()
    assert not report.is_valid
    assert any("references non-existent prerequisite 'missing_p'" in e for e in report.errors)


def test_validator_detects_cycles() -> None:
    """Test validator catches cyclic dependencies."""
    t1 = Topic(
        id="t1",
        name_de="T1",
        cefr="A1",
        prereqs=["t2"],
        description="D",
        eligible_types=["cloze_free"],
    )
    t2 = Topic(
        id="t2",
        name_de="T2",
        cefr="A1",
        prereqs=["t1"],
        description="D",
        eligible_types=["cloze_free"],
    )
    validator = TaxonomyValidator([t1, t2])
    report = validator.validate()
    assert not report.is_valid
    assert len(report.cycles) > 0
    assert any("Cycle detected" in e for e in report.errors)


def test_validator_get_transitive_prereqs_missing_key() -> None:
    """Test get_transitive_prereqs raises KeyError for invalid topic ID."""
    t1 = Topic(id="t1", name_de="T1", cefr="A1", description="D", eligible_types=["cloze_free"])
    validator = TaxonomyValidator([t1])
    with pytest.raises(KeyError):
        validator.get_transitive_prereqs("unknown_id")
