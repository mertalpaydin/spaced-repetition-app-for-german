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

    Topics explicitly marked ``facet_exempt`` are skipped here on purpose: they
    keep a genuinely non-empty ``morph_spec`` for the stage 4 morphology check
    while being deliberately, visibly excused from the stage 6 facet-variety
    requirement (see ``test_facet_exempt_topics_declare_a_reason`` below and
    ``facet_space``'s docstring in ``src/taxonomy/facets.py``).
    """
    checked_any = False
    for t in taxonomy_topics:
        if not t.morph_spec:
            continue
        if getattr(t, "facet_exempt", False):
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


def test_facet_exempt_topics_declare_a_reason_and_actually_degrade(
    taxonomy_topics: list[Topic],
) -> None:
    """``facet_exempt`` must be a deliberate, visible, reasoned property, not a
    silent side effect: every topic that sets it must also carry a non-empty
    ``facet_exempt_reason``, must keep a genuinely non-empty ``morph_spec``
    (an empty one is the separate, pre-existing degradation path and doesn't
    need this flag at all), and must actually derive an empty facet space.
    """
    exempt_topics = [t for t in taxonomy_topics if getattr(t, "facet_exempt", False)]
    assert exempt_topics, "expected at least one facet_exempt topic"
    for t in exempt_topics:
        reason = getattr(t, "facet_exempt_reason", None)
        assert reason and reason.strip(), (
            f"Topic '{t.id}' sets facet_exempt but has no facet_exempt_reason"
        )
        assert t.morph_spec, (
            f"Topic '{t.id}' sets facet_exempt with an empty morph_spec; the empty-"
            "morph_spec degradation already covers that case without this flag"
        )
        assert facet_space(t) == (), (
            f"Topic '{t.id}' is facet_exempt but facet_space(t) did not degrade to ()"
        )


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


# ==============================================================================
# Adjective / participle attributive endings: context-aware derivation
#
# `guten` alone is genuinely ambiguous (masc acc sg, dat plur, ...), but the
# determiner immediately before the gap almost always disambiguates it. These
# topics used to collapse every item to a single all-Unk facet, which is a
# structural deadlock: PROMOTION_MIN_DISTINCT_FACETS can never be satisfied.
# ==============================================================================

_ADJECTIVE_AND_PARTICIPLE_GOLD_PAIRS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    # Each topic maps to two (prompt, answer) pairs a real gold set would
    # plausibly contain -- a definite-article-preceded example (weak
    # declension) and a second example that must land on a different facet.
    "adjektivdeklination_bestimmt": (
        ("Der ___ Mann liest die Zeitung.", "alte"),
        ("Das ___ Kind spielt im Garten.", "alte"),
    ),
    "adjektivdeklination_unbestimmt": (
        ("Ein ___ Baum steht im Garten.", "alter"),
        ("Sie kauft ein ___ Auto.", "altes"),
    ),
    "adjektiv_komparativ_superlativ": (
        ("Der ___ Sohn hilft im Haushalt.", "ältere"),
        ("Er hat eine ___ Idee als ich.", "bessere"),
    ),
    "partizip_i_attributiv": (
        ("Der ___ Mann ruft laut.", "rufende"),
        ("Das ___ Kind sucht seine Mutter.", "weinende"),
    ),
    "partizip_ii_attributiv_erweitert": (
        ("Der ___ Brief liegt auf dem Tisch.", "geschriebene"),
        ("Ich lese das ___ Buch.", "geschriebene"),
    ),
}


def _all_unk_facet(facet: str | None, dims: tuple[str, ...]) -> bool:
    if facet is None:
        return True
    return set(facet.split("|")) == {f"{dim}=Unk" for dim in dims}


@pytest.mark.parametrize("topic_id", sorted(_ADJECTIVE_AND_PARTICIPLE_GOLD_PAIRS))
def test_adjective_and_participle_topics_escape_the_all_unk_deadlock(
    taxonomy_topics: list[Topic], topic_id: str
) -> None:
    """A realistic gold set (determiner before the gap, adjective as the answer) must
    produce at least two distinct, non-all-Unk facets for each adjective/participle
    topic, or PROMOTION_MIN_DISTINCT_FACETS can never be satisfied -- the exact
    deadlock that made these topics permanently stuck in 'learning'.
    """
    topic = {t.id: t for t in taxonomy_topics}[topic_id]
    dims = facet_space(topic)
    assert dims, f"{topic_id} unexpectedly has no facet space"

    items = [
        BankItem(
            id=f"{topic_id}-{idx}",
            topic_id=topic_id,
            type="cloze_free",
            difficulty=1,
            cefr=topic.cefr,
            prompt=prompt,
            accepted_answers=[answer],
        )
        for idx, (prompt, answer) in enumerate(_ADJECTIVE_AND_PARTICIPLE_GOLD_PAIRS[topic_id])
    ]
    facets = [derive_facet(item, topic) for item in items]

    assert None not in facets
    assert len(set(facets)) >= 2, f"{topic_id}: gold set collapsed to a single facet {facets}"
    for facet in facets:
        assert not _all_unk_facet(facet, dims), (
            f"{topic_id}: realistic answer yielded an all-Unk facet {facet!r} over {dims}"
        )


def test_adjective_facet_derivation_reads_the_determiner_before_the_gap(
    taxonomy_topics: list[Topic],
) -> None:
    """`Der ___ Mann liest.` (alte) and `Ich sehe den ___ Mann.` (alten) must not
    collapse to the same facet -- the determiner immediately before the gap
    ('der' vs 'den') is exactly the context that disambiguates an otherwise
    identically-ambiguous weak adjective ending.
    """
    topic = {t.id: t for t in taxonomy_topics}["adjektivdeklination_bestimmt"]

    nominative_item = BankItem(
        id="nom",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="Der ___ Mann liest.",
        accepted_answers=["alte"],
    )
    accusative_item = BankItem(
        id="acc",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="Ich sehe den ___ Mann.",
        accepted_answers=["alten"],
    )

    nominative_facet = derive_facet(nominative_item, topic)
    accusative_facet = derive_facet(accusative_item, topic)

    assert nominative_facet != accusative_facet
    assert nominative_facet == "Case=Nom|Gender=Masc|Number=Sing"


def test_adjective_facet_derivation_resolves_mixed_declension_after_ein_word(
    taxonomy_topics: list[Topic],
) -> None:
    """An ein-word before the gap must select the mixed declension paradigm, not
    the weak one -- 'ein alter Baum' (nom masc sg) and 'ein altes Auto' (a
    genuinely ambiguous nom/acc neut sg) must resolve differently from their
    weak-declension counterparts.
    """
    topic = {t.id: t for t in taxonomy_topics}["adjektivdeklination_unbestimmt"]

    masc_item = BankItem(
        id="masc",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="Ein ___ Baum steht im Garten.",
        accepted_answers=["alter"],
    )
    neut_item = BankItem(
        id="neut",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="Sie kauft ein ___ Auto.",
        accepted_answers=["altes"],
    )

    assert derive_facet(masc_item, topic) == "Case=Nom|Gender=Masc|Number=Sing"
    neut_facet = derive_facet(neut_item, topic)
    assert neut_facet is not None
    assert "Gender=Neut" in neut_facet
    assert "Number=Sing" in neut_facet


def test_adjective_facet_derivation_handles_zero_article_with_strong_ending(
    taxonomy_topics: list[Topic],
) -> None:
    """With no determiner before the gap (including the gap opening the prompt),
    the strong declension ending must be read on its own -- 'Frisches Brot'
    (nom/acc neut sg, zero article) is informative without any preceding word.
    """
    topic = {t.id: t for t in taxonomy_topics}["adjektiv_komparativ_superlativ"]

    item = BankItem(
        id="zero-article",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="___ Brot schmeckt am besten.",
        accepted_answers=["Frischeres"],
    )
    facet = derive_facet(item, topic)
    assert facet is not None
    assert "Gender=Neut" in facet
    assert "Number=Sing" in facet


def test_adjective_facet_derivation_never_guesses_on_contradictory_context(
    taxonomy_topics: list[Topic],
) -> None:
    """A determiner/ending combination that is not jointly attested anywhere in the
    paradigm (a malformed or contradictory prompt) must degrade to all-Unk rather
    than committing to a value neither side actually supports.
    """
    topic = {t.id: t for t in taxonomy_topics}["adjektivdeklination_bestimmt"]
    dims = facet_space(topic)

    item = BankItem(
        id="contradictory",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        # "der" (weak) never combines with a bare "-er" ending anywhere in the
        # weak paradigm (that ending only exists in the mixed/strong tables).
        prompt="Der ___ Mann liest.",
        accepted_answers=["alter"],
    )
    facet = derive_facet(item, topic)
    assert _all_unk_facet(facet, dims)


def test_derive_facet_is_deterministic_for_context_aware_adjective_decoding(
    taxonomy_topics: list[Topic],
) -> None:
    """Same item plus same topic must always yield the same facet string, even once
    derivation depends on parsing ``item.prompt`` rather than the answer alone.
    """
    topic = {t.id: t for t in taxonomy_topics}["partizip_ii_attributiv_erweitert"]
    item = BankItem(
        id="det-1",
        topic_id=topic.id,
        type="cloze_free",
        difficulty=1,
        cefr=topic.cefr,
        prompt="Der ___ Brief liegt auf dem Tisch.",
        accepted_answers=["geschriebene"],
    )
    results = {derive_facet(item, topic) for _ in range(5)}
    assert len(results) == 1
    assert next(iter(results)) == "Case=Nom|Gender=Masc|Number=Sing"
