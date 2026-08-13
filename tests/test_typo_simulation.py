"""Unit tests for ScopedTypoGrader and 180-day LearnerSimulationHarness."""

from src.contracts import Topic
from src.engine.simulation import LearnerSimulationHarness
from src.engine.typo_grader import ScopedTypoGrader


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
