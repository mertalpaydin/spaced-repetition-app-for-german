"""Unit tests for ScopedTypoGrader, golden grading table, and LearnerSimulationHarness."""

import csv
import string
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from src.contracts import Topic
from src.engine.simulation import LearnerSimulationHarness
from src.engine.typo_grader import ScopedTypoGrader
from src.taxonomy.loader import load_taxonomy

# Loaded once at import time so the hypothesis strategy below doesn't touch the
# filesystem inside the property. `is_faceted` mirrors ScopedTypoGrader's own
# notion of "faceted": a topic whose morph_spec is non-empty.
_TAXONOMY_TOPICS = load_taxonomy()
_MORPH_SPEC_MAP = {t.id: bool(t.morph_spec) for t in _TAXONOMY_TOPICS}
FACETED_TOPIC_IDS = sorted(tid for tid, faceted in _MORPH_SPEC_MAP.items() if faceted)

# Consonants only: the property mutates a single trailing character, and using
# an alphabet with no vowels or 's' guarantees the mutated word can never
# collide with a transliteration pair (ae/oe/ue/ss) or a real closed-class
# grammatical morpheme, so the only mechanism under test is topic scoping.
_CONSONANTS = "".join(c for c in string.ascii_lowercase if c not in "aeiousy")


def test_typo_grader_exact_and_capitalization() -> None:
    """Verify exact match and strict case-sensitivity."""
    res_exact = ScopedTypoGrader.grade("dem", ["dem"])
    assert res_exact.is_correct is True
    assert res_exact.is_exact is True
    assert res_exact.is_scoped_typo is False

    # In German, capitalization error strictly FAILS
    res_cap = ScopedTypoGrader.grade("Dem", ["dem"])
    assert res_cap.is_correct is False
    assert res_cap.is_capitalization_error is True


def test_typo_grader_transliteration_and_whitespace() -> None:
    """Verify German umlaut transliteration (oe, ae, ue, ss) and whitespace normalization."""
    res_oe = ScopedTypoGrader.grade("groesser", ["größer"])
    assert res_oe.is_correct is True
    assert res_oe.is_transliteration is True

    res_ws = ScopedTypoGrader.grade("dem   Mann", ["dem Mann"])
    assert res_ws.is_correct is True
    assert res_ws.is_exact is True


def test_typo_grader_scoped_typo_peripheral_vs_grammatical_morpheme() -> None:
    """Verify peripheral typos are tolerated, while grammatical morphemes are strictly rejected."""
    # Peripheral typo outside tested slot -> Tolerated
    res_typo = ScopedTypoGrader.grade("dem Mnn", ["dem Mann"])
    assert res_typo.is_correct is True
    assert res_typo.is_scoped_typo is True

    # Critical grammatical minimal pairs (dem/den, grosser/groesser, hatte/haette) -> REJECTED
    res_case_error = ScopedTypoGrader.grade("den", ["dem"])
    assert res_case_error.is_correct is False

    res_comp_error = ScopedTypoGrader.grade("großer", ["größer"])
    assert res_comp_error.is_correct is False

    res_subj_error = ScopedTypoGrader.grade("hatte", ["hätte"])
    assert res_subj_error.is_correct is False


def test_grading_table_golden() -> None:
    """Verify all rows in the golden grading table fixture."""
    fixture_path = Path("data/fixtures/grading/grading_table.csv")
    assert fixture_path.exists()

    with open(fixture_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected = row["expected"]
            given = row["given"]
            topic_id = row["topic_id"] or None
            expected_verdict = row["verdict"] == "pass"
            res = ScopedTypoGrader.grade(given, [expected], topic_id)
            assert res.is_correct == expected_verdict, (
                f"Grading mismatch for '{given}' vs '{expected}' (Topic: {row.get('topic_id')}): "
                f"expected {expected_verdict}, got {res.is_correct}. Reason: {row.get('reason')}"
            )


def test_typo_tolerance_never_crosses_the_tested_morpheme() -> None:
    """Grammatical morpheme minimal pairs must never be tolerated as typos.

    Kept as a fixed regression list alongside the property test below: these are
    exactly the closed-class pairs the hardcoded backstop lists exist to catch
    even when no `topic_id` is supplied.
    """
    minimal_pairs = [
        ("dem", "den"),
        ("dem", "des"),
        ("hatte", "hätte"),
        ("war", "wäre"),
        ("großer", "größer"),
        ("sie", "Sie"),
    ]
    for target, wrong in minimal_pairs:
        res = ScopedTypoGrader.grade(wrong, [target])
        assert not res.is_correct, (
            f"Minimal pair error '{wrong}' for target '{target}' must strictly fail"
        )


@given(
    topic_id=st.sampled_from(FACETED_TOPIC_IDS),
    base=st.text(alphabet=_CONSONANTS, min_size=3, max_size=12),
    mutation=st.text(alphabet=_CONSONANTS, min_size=1, max_size=1),
)
@settings(max_examples=300, deadline=None)
def test_typo_tolerance_never_crosses_the_tested_morpheme_property(
    topic_id: str, base: str, mutation: str
) -> None:
    """For every faceted topic, mutating the final character of the accepted
    answer must FAIL: the tested morpheme is exact, with no edit-distance
    tolerance, per the scoping rule in docs/03-learning-engine.md.
    """
    if mutation == base[-1]:
        return  # not a mutation
    mutated = base[:-1] + mutation
    res = ScopedTypoGrader.grade(mutated, [base], topic_id)
    assert not res.is_correct, (
        f"Topic '{topic_id}' is faceted (has morph_spec): mutating the final "
        f"character of '{base}' to get '{mutated}' must fail, not be tolerated "
        f"as a typo."
    )


def test_grade_adjective_declension_ending_wrong_fails() -> None:
    """Regression: wrong adjective declension ending must fail, not pass as a typo.

    Previously `grade('großem', ['großen'])` -> is_correct=True (blanket
    edit-distance-1 tolerance). The wrong ending is the tested morpheme for a
    faceted adjective-declension topic, so it must fail.
    """
    res = ScopedTypoGrader.grade("großem", ["großen"], "adjektivdeklination_bestimmt")
    assert res.is_correct is False


def test_grade_verb_person_ending_wrong_fails() -> None:
    """Regression: wrong verb person ending must fail, not pass as a typo.

    Previously `grade('geht', ['gehst'])` -> is_correct=True. The person
    ending is the tested morpheme for a faceted present-tense topic.
    """
    res = ScopedTypoGrader.grade("geht", ["gehst"], "verb_praesens_regelm")
    assert res.is_correct is False


def test_grade_dativ_akkusativ_adjective_ending_confusion_fails() -> None:
    """Regression: dative/accusative adjective ending confusion must fail.

    Previously `grade('kleinen', ['kleinem'])` -> is_correct=True. The case
    ending is the tested morpheme for a faceted adjective-declension topic.
    """
    res = ScopedTypoGrader.grade("kleinen", ["kleinem"], "adjektivdeklination_unbestimmt")
    assert res.is_correct is False


def test_simulation_harness_180_days() -> None:
    """Test 180-day Monte Carlo learner simulation across FSRS pacing and overload limits."""
    mock_topics = [
        Topic(id="t_a1_01", name_de="Pronomen", cefr="A1", description="A1.1", prereqs=[]),
        Topic(id="t_a1_02", name_de="Verben", cefr="A1", description="A1.2", prereqs=["t_a1_01"]),
        Topic(id="t_a2_01", name_de="Dativ", cefr="A2", description="A2.1", prereqs=["t_a1_02"]),
    ]

    harness = LearnerSimulationHarness(
        topics=mock_topics, base_learner_accuracy=0.90, random_seed=42
    )
    summary = harness.run_simulation(days=30)

    assert summary.days_simulated == 30
    assert summary.total_reviews_completed > 0
    assert summary.retention_rate >= 0.80
    assert summary.max_forecast_load_encountered >= 0
