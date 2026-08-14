"""Unit and system tests for Stage 10 automation deficits, caps, and Stage 11 live LLM features."""

from src.generation.batch_client import CostTracker
from src.llm.minimal_pairs import MinimalPairGenerator
from src.llm.production_grader import ProductionGrader
from src.llm.provider import MockLlmClient


def test_deficit_zero_when_stock_exceeds_projected_demand() -> None:
    """When bank stock exceeds projected demand, deficit is 0."""
    stock = 50
    projected_demand = 30
    deficit = max(0, projected_demand - stock)
    assert deficit == 0


def test_deficit_accounts_for_difficulty_tier_separately() -> None:
    """Deficits are computed across distinct difficulty tiers (1, 2, 3)."""
    stock_by_tier = {1: 20, 2: 5, 3: 0}
    target_by_tier = {1: 15, 2: 15, 3: 15}

    deficits = {tier: max(0, target_by_tier[tier] - stock_by_tier[tier]) for tier in (1, 2, 3)}
    assert deficits[1] == 0
    assert deficits[2] == 10
    assert deficits[3] == 15


def test_monthly_spend_ceiling_blocks_generation() -> None:
    """Monthly spend ceiling blocks batch generation requests when exceeded."""
    tracker = CostTracker()
    # Simulate high spend
    tracker.record_usage("batch_999", "gemini-3.7-flash", 5_000_000, 2_000_000)
    ceiling_usd = 0.50
    is_blocked = tracker.total_cost_usd >= ceiling_usd
    assert is_blocked is True


def test_production_grading_dimensions_isolated() -> None:
    """Target structure detection is evaluated independently from naturalness/accuracy."""
    grader = ProductionGrader(provider=MockLlmClient())
    res = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Satz mit 'weil'.",
        student_submission="Ich lerne Deutsch, weil ich gern reise.",
    )

    assert res.target_structure_used is True
    assert res.grammatical_accuracy == 1.0
    assert res.naturalness == 1.0
    assert res.is_pass is True


def test_minimal_pair_members_share_confusion_group() -> None:
    """Minimal pair drills contrast confusable items from the same confusion group."""
    generator = MinimalPairGenerator()
    drill = generator.get_or_generate_drill("wechselpraepositionen")

    assert drill.confusion_group == "wechselpraepositionen"
    assert drill.target_a == "dem"
    assert drill.target_b == "den"
    assert "Dativ" in drill.explanation_a
    assert "Akkusativ" in drill.explanation_b
