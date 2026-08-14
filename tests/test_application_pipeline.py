"""Unit and system tests for Stage 10 automation deficits, caps, and Stage 11 live LLM features."""

import json
from pathlib import Path

from src.contracts import GenerationRequest
from src.generation.batch_client import CostTracker, MockBatchClient, run_ingest, run_submit
from src.generation.deficits import (
    MIN_BATCH_THRESHOLD,
    NIGHTLY_ITEM_CAP,
    SAFETY_FACTOR,
    compute_deficit,
    compute_topic_deficits,
)
from src.llm.client import CostLogRow, GeminiLlmClient
from src.llm.minimal_pairs import MinimalPairGenerator
from src.llm.production_grader import ProductionGrader
from src.llm.provider import MockLlmClient


def test_deficit_zero_when_stock_exceeds_projected_demand() -> None:
    """When bank stock exceeds ceil(projected_demand * SAFETY_FACTOR), deficit is 0."""
    stock = 50
    projected_demand = 30
    assert compute_deficit(projected_demand, stock) == 0


def test_deficit_accounts_for_difficulty_tier_separately() -> None:
    """Deficits are computed across distinct difficulty tiers (1, 2, 3), independently."""
    demand_and_stock = {
        ("dativ_nach_praeposition", 1): (10, 20),
        ("dativ_nach_praeposition", 2): (10, 5),
        ("dativ_nach_praeposition", 3): (10, 0),
    }
    deficits = {
        d.difficulty: d.deficit for d in compute_topic_deficits(demand_and_stock, safety_factor=1.0)
    }

    assert deficits.get(1, 0) == 0
    assert deficits[2] == 5
    assert deficits[3] == 10


def test_monthly_spend_ceiling_blocks_generation(tmp_path: Path) -> None:
    """A cost log already at the ceiling blocks nightly submission: zero batches
    submitted, graceful log, exit 0. A budget stop is normal operation, not an
    error (docs/04-application.md)."""
    log_file = tmp_path / "cost_log.jsonl"
    llm_client = GeminiLlmClient(
        spend_ceiling_usd=0.50,
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
    )
    llm_client._log_cost(
        CostLogRow(
            model="gemini-3.7-flash",
            lane="paid",
            prompt_tokens=5_000_000,
            completion_tokens=2_000_000,
            cost_usd=0.55,
        )
    )

    db_path = tmp_path / "bank.db"  # deliberately does not exist
    state_path = tmp_path / "generation_state.json"

    exit_code = run_submit(db_path=db_path, state_path=state_path, llm_client=llm_client)

    assert exit_code == 0
    assert not state_path.exists(), "spend ceiling must block submission before any batch is built"


def test_nightly_item_cap_enforced() -> None:
    """A deficit calculation returning far more than the cap generates at most CAP items."""
    demand_and_stock = {(f"topic_{i}", 1): (10_000, 0) for i in range(50)}
    deficits = compute_topic_deficits(demand_and_stock, safety_factor=SAFETY_FACTOR)
    from src.generation.deficits import build_generation_requests

    requests = build_generation_requests(
        deficits,
        eligible_types_by_topic={f"topic_{i}": ["cloze_free"] for i in range(50)},
    )

    total_requested = sum(r.count for r in requests)
    assert total_requested <= NIGHTLY_ITEM_CAP
    assert total_requested > MIN_BATCH_THRESHOLD


def test_ingest_on_pending_batch_exits_zero(tmp_path: Path) -> None:
    """Ingest on a batch that has not completed yet exits 0 (retry tomorrow), not a failure."""
    state_path = tmp_path / "generation_state.json"
    request = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=6, difficulty=1, item_types=["cloze_free"]
    )
    batch_client = MockBatchClient()
    batch_id = batch_client.submit([request])
    batch_client.rate_limit_simulations.add(batch_id)  # simulate: still processing
    state_path.write_text(
        json.dumps({"batch_id": batch_id, "requests": [request.model_dump()]}), encoding="utf-8"
    )

    exit_code = run_ingest(state_path=state_path, batch_client=batch_client)
    assert exit_code == 0


def test_ingest_with_no_submitted_batch_exits_zero(tmp_path: Path) -> None:
    """Ingest before anything has ever been submitted is harmless, not a KeyError crash."""
    state_path = tmp_path / "generation_state.json"  # never written
    exit_code = run_ingest(state_path=state_path)
    assert exit_code == 0


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


def test_cost_tracker_total_reflects_recorded_batches() -> None:
    """CostTracker sums recorded usage across batches (sanity check for the ceiling test above)."""
    tracker = CostTracker()
    tracker.record_usage("batch_a", "gemini-3.7-flash", 1_000_000, 500_000)
    tracker.record_usage("batch_b", "gemini-3.5-flash-lite", 1_000_000, 500_000)
    assert tracker.total_cost_usd == sum(r.estimated_cost_usd for r in tracker.records)
    assert len(tracker.records) == 2
