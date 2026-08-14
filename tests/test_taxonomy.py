"""Unit and golden tests for the Grammar Topic Taxonomy and Prerequisite DAG."""

import json
from collections import defaultdict
from pathlib import Path

import pytest
from src.contracts import CEFR, BankItem, Topic
from src.taxonomy.facets import (
    GERMAN_UD_FEATURE_UNIVERSE,
    VALUE_CARDINALITY,
    derive_facet,
    facet_space,
)
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

    assert golden_file.exists(), f"Taxonomy expected IDs fixture missing: {golden_file}"
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


def test_every_confusion_group_has_at_least_two_members(
    taxonomy_topics: list[Topic],
) -> None:
    """01-foundation.md:193: a group of one cannot produce interleaved contrast.

    This must be a hard requirement, not a warning: CLAUDE.md rule 7 forbids
    downgrading a failing check to keep report.is_valid green.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for t in taxonomy_topics:
        if t.confusion_group:
            groups[t.confusion_group].append(t.id)

    singletons = {name: members for name, members in groups.items() if len(members) < 2}
    assert not singletons, f"Confusion groups with fewer than 2 members: {singletons}"


def test_validator_flags_singleton_confusion_group_as_error() -> None:
    """A confusion group of one member must fail validation, not merely warn."""
    t1 = Topic(
        id="t1",
        name_de="T1",
        cefr="A1",
        description="D",
        eligible_types=["cloze_free"],
        confusion_group="lonely_group",
    )
    validator = TaxonomyValidator([t1])
    report = validator.validate()

    assert not report.is_valid
    assert any("fewer than 2 members" in e for e in report.errors)
    assert not any("fewer than 2 members" in w for w in report.warnings)


# ==============================================================================
# morph_spec / facets (01-foundation.md:168-170, 259-269)
# ==============================================================================


def test_morph_spec_keys_are_valid_universal_dependencies_features(
    taxonomy_topics: list[Topic],
) -> None:
    """Reject typos or invented keys that would silently disable the stage 4 morphology check.

    Only genuine Universal Dependencies FEATS keys may appear in morph_spec.
    Anything else (part-of-speech tags, syntactic markers like "Subordinate"
    or "Separable") belongs on Topic.syntax_tags instead.
    """
    allowed = set(GERMAN_UD_FEATURE_UNIVERSE)
    for t in taxonomy_topics:
        for key in t.morph_spec or {}:
            assert key in allowed, (
                f"Topic '{t.id}' morph_spec key '{key}' is not a valid Universal "
                f"Dependencies feature. Allowed keys: {sorted(allowed)}"
            )


def test_facet_space_derivable_from_morph_spec(taxonomy_topics: list[Topic]) -> None:
    """For every topic with non-empty morph_spec, the unspecified features must yield
    at least 2 possible facet values, or the topic can never satisfy the promotion rule.
    """
    checked_any = False
    for t in taxonomy_topics:
        if not t.morph_spec:
            continue
        checked_any = True
        dims = facet_space(t)
        assert dims, f"Topic '{t.id}' has a non-empty morph_spec but derives no facet dimensions"
        cardinality = 1
        for dim in dims:
            cardinality *= VALUE_CARDINALITY[dim]
        assert cardinality >= 2, (
            f"Topic '{t.id}' facet space {dims} yields only {cardinality} possible facet "
            "value(s), which can never satisfy PROMOTION_MIN_DISTINCT_FACETS"
        )
    assert checked_any, "expected at least one topic with a non-empty morph_spec"


def test_empty_morph_spec_topics_declare_no_facets_explicitly(
    taxonomy_topics: list[Topic],
) -> None:
    """An empty morph_spec must yield an explicit empty facet-space tuple, not an absent field."""
    empty_topics = [t for t in taxonomy_topics if not t.morph_spec]
    assert empty_topics, "expected at least one topic with an empty morph_spec"
    for t in empty_topics:
        dims = facet_space(t)
        assert dims == ()
        assert dims is not None


def test_derive_facet_is_deterministic(taxonomy_topics: list[Topic]) -> None:
    """Same item plus same topic must always yield the same facet string."""
    topics_by_id = {t.id: t for t in taxonomy_topics}
    topic = topics_by_id["dativ_nach_praeposition"]
    item = BankItem(
        id="det-1",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Das Buch liegt auf ___ Tisch.",
        accepted_answers=["dem"],
    )
    results = {derive_facet(item, topic) for _ in range(5)}
    assert len(results) == 1
    assert next(iter(results)) is not None


def test_derive_facet_is_none_iff_facet_space_empty(taxonomy_topics: list[Topic]) -> None:
    """derive_facet returns None exactly when the topic's facet space is empty."""
    topics_by_id = {t.id: t for t in taxonomy_topics}

    faceted_topic = topics_by_id["dativ_nach_praeposition"]
    assert facet_space(faceted_topic) != ()
    faceted_item = BankItem(
        id="i1",
        topic_id=faceted_topic.id,
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Das Buch liegt auf ___ Tisch.",
        accepted_answers=["dem"],
    )
    assert derive_facet(faceted_item, faceted_topic) is not None

    unfaceted_topic = topics_by_id["nebensatz_weil_da"]
    assert facet_space(unfaceted_topic) == ()
    unfaceted_item = BankItem(
        id="i2",
        topic_id=unfaceted_topic.id,
        type="cloze_free",
        difficulty=2,
        cefr="B1",
        prompt="Er bleibt hier, weil er müde ___.",
        accepted_answers=["ist"],
    )
    assert derive_facet(unfaceted_item, unfaceted_topic) is None


def test_realistic_bank_yields_distinct_facets_for_a_faceted_topic(
    taxonomy_topics: list[Topic],
) -> None:
    """A small realistic item bank must produce at least two distinct facets for at
    least one faceted topic, or PROMOTION_MIN_DISTINCT_FACETS can never be satisfied.
    """
    topics_by_id = {t.id: t for t in taxonomy_topics}
    topic = topics_by_id["artikel_bestimmt_nom"]
    items = [
        BankItem(
            id="a1",
            topic_id=topic.id,
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="___ Tisch ist groß.",
            accepted_answers=["Der"],
        ),
        BankItem(
            id="a2",
            topic_id=topic.id,
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="___ Kind schläft.",
            accepted_answers=["Das"],
        ),
    ]
    facets = {derive_facet(item, topic) for item in items}
    assert len(facets) >= 2
    assert None not in facets
