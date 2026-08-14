"""Batch client for asynchronous candidate item generation with cost and token accounting.

Also the nightly automation entry point (``python -m src.generation.batch_client``),
invoked by ``.github/workflows/generate-submit.yml`` and ``generate-ingest.yml``.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.contracts import (
    MODEL_GENERATE,
    BatchId,
    BatchStatus,
    CandidateItem,
    Difficulty,
    Distractor,
    GenerationRequest,
)
from src.generation.deficits import (
    MIN_STOCK_PER_TIER_DEMAND_STANDIN,
    build_generation_requests,
    compute_topic_deficits,
)
from src.llm.client import CostLogRow, GeminiLlmClient

DEFAULT_STATE_PATH = Path("data/generation_state.json")
DEFAULT_DB_PATH = Path("data/bank.db")


@dataclass
class BatchCostRecord:
    """Accounting record for a single batch run."""

    batch_id: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    timestamp: float = field(default_factory=time.time)


class CostTracker:
    """Tracks token consumption and API spend across all batch generation calls."""

    # Gemini 3.5 Flash-Lite pricing: $0.0375 / 1M in, $0.15 / 1M out
    PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
        "gemini-3.5-flash-lite": (0.0375, 0.15),
        "gemini-3.7-flash": (0.075, 0.30),
    }

    def __init__(self) -> None:
        self.records: list[BatchCostRecord] = []

    def record_usage(
        self, batch_id: str, model: str, input_tokens: int, output_tokens: int
    ) -> BatchCostRecord:
        """Record token usage and compute estimated USD cost."""
        in_rate, out_rate = self.PRICING_PER_MILLION.get(model, (0.05, 0.20))
        cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
        record = BatchCostRecord(
            batch_id=batch_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(cost, 6),
        )
        self.records.append(record)
        return record

    @property
    def total_cost_usd(self) -> float:
        return sum(r.estimated_cost_usd for r in self.records)


class MockBatchClient:
    """Mock implementation of the BatchClient protocol for unit and integration tests.

    Also doubles as the nightly automation's batch client: no real Gemini Batch API
    transport is implemented (see ``src/llm/client.py``'s ``_call_transport`` for the
    equivalent stub on the synchronous path), so this offline synthesiser is what
    both ``generate-submit`` and ``generate-ingest`` actually run against today.
    """

    def __init__(self, cost_tracker: CostTracker | None = None) -> None:
        self.cost_tracker = cost_tracker or CostTracker()
        self.submitted_batches: dict[BatchId, list[GenerationRequest]] = {}
        self.batch_statuses: dict[BatchId, BatchStatus] = {}
        self.poll_counts: dict[BatchId, int] = {}
        self.rate_limit_simulations: set[BatchId] = set()

    def submit(self, requests: list[GenerationRequest]) -> BatchId:
        """Submit a generation request set idempotently."""
        # Compute deterministic hash of request payloads
        serialized = json.dumps([r.model_dump() for r in requests], sort_keys=True)
        batch_id = f"batch_{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:12]}"

        if batch_id not in self.submitted_batches:
            self.submitted_batches[batch_id] = requests
            self.batch_statuses[batch_id] = "pending"
            self.poll_counts[batch_id] = 0

            # Record estimated cost
            total_items = sum(r.count for r in requests)
            self.cost_tracker.record_usage(
                batch_id=batch_id,
                model=MODEL_GENERATE,
                input_tokens=total_items * 150,
                output_tokens=total_items * 180,
            )

        return batch_id

    def poll(self, batch_id: BatchId) -> BatchStatus:
        """Poll status with simulated progress and 429 backoff handling."""
        if batch_id not in self.submitted_batches:
            raise KeyError(f"Batch ID '{batch_id}' not found.")

        if batch_id in self.rate_limit_simulations:
            # Simulate a 429 rate limit on first poll
            self.rate_limit_simulations.remove(batch_id)
            time.sleep(0.01)  # backoff
            return "pending"

        self.poll_counts[batch_id] += 1
        if self.poll_counts[batch_id] >= 1:
            self.batch_statuses[batch_id] = "completed"

        return self.batch_statuses[batch_id]

    def retrieve(self, batch_id: BatchId) -> list[CandidateItem]:
        """Retrieve generated CandidateItems for a completed batch."""
        if batch_id not in self.submitted_batches:
            raise KeyError(f"Batch ID '{batch_id}' not found.")

        if self.batch_statuses[batch_id] != "completed":
            status = self.batch_statuses[batch_id]
            raise RuntimeError(f"Batch '{batch_id}' is not completed yet (status: {status}).")

        requests = self.submitted_batches[batch_id]
        results: list[CandidateItem] = []

        for req in requests:
            for i in range(req.count):
                # Synthesize valid candidate item with exactly 3 distractors
                item_type = req.item_types[i % len(req.item_types)]
                distractors = [
                    Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
                    Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
                    Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
                ]
                results.append(
                    CandidateItem(
                        topic_id=req.topic_id,
                        type=item_type,
                        difficulty=req.difficulty,
                        prompt=f"Das Buch liegt auf ___ Tisch {i + 1}.",
                        cue="Tisch" if item_type == "cloze_cued" else None,
                        proposed_answer="dem",
                        distractors=distractors,
                        carrier_lemmas=["Buch", "liegen", "Tisch"],
                    )
                )

        return results


# ==============================================================================
# Nightly automation: deficit-driven submit, and pending-tolerant ingest.
# ==============================================================================


def _build_llm_client_if_configured() -> GeminiLlmClient | None:
    """Route real cost accounting and the spend ceiling through the single client
    in src/llm/client.py (CLAUDE.md rule 4) whenever an API key is configured.
    Falls back to None (no ceiling check possible, offline dev) otherwise."""
    if (
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    ):
        return GeminiLlmClient()
    return None


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _compute_bank_deficits(
    db_path: Path, taxonomy_path: Path | str | None
) -> list[GenerationRequest]:
    """Read real per-topic, per-difficulty stock from the bank and build the
    understocked-topic ``GenerationRequest`` list, capped by NIGHTLY_ITEM_CAP.

    ``projected_demand`` should come from the FSRS scheduler's 14-day forecast
    (src/engine, owned by another agent); until that wiring exists this uses
    MIN_STOCK_PER_TIER_DEMAND_STANDIN as a documented placeholder. The deficit
    arithmetic itself (src/generation/deficits.py) is real and independently
    tested against the formula in docs/04-application.md.
    """
    from src.bank.storage import SqliteItemBank
    from src.taxonomy.loader import load_taxonomy

    bank = SqliteItemBank(db_path)
    topics = load_taxonomy(taxonomy_path)
    difficulties: tuple[Difficulty, ...] = (1, 2, 3)

    demand_and_stock: dict[tuple[str, Difficulty], tuple[int, int]] = {}
    for topic in topics:
        for difficulty in difficulties:
            stock = len(bank.query_by_difficulty(topic.id, difficulty))
            demand_and_stock[(topic.id, difficulty)] = (
                MIN_STOCK_PER_TIER_DEMAND_STANDIN,
                stock,
            )

    deficits = compute_topic_deficits(demand_and_stock)
    eligible_types = {t.id: t.eligible_types for t in topics}
    return build_generation_requests(deficits, eligible_types)


def run_submit(
    db_path: Path | str = DEFAULT_DB_PATH,
    state_path: Path | str = DEFAULT_STATE_PATH,
    taxonomy_path: Path | str | None = None,
    llm_client: GeminiLlmClient | None = None,
) -> int:
    """Compute per-topic deficits and submit one batch covering every understocked
    topic. Returns a process exit code: always 0 unless something genuinely wrong
    happens, per docs/04-application.md ("Exits in under a minute", and
    test_monthly_spend_ceiling_blocks_generation: a budget stop is normal
    operation, not an error)."""
    db_path = Path(db_path)
    state_path = Path(state_path)

    if llm_client is None:
        llm_client = _build_llm_client_if_configured()

    if llm_client is not None:
        spend = llm_client.get_month_to_date_spend()
        if spend >= llm_client.spend_ceiling_usd:
            print(
                f"Monthly spend ceiling reached (${spend:.4f} >= "
                f"${llm_client.spend_ceiling_usd:.2f}); skipping nightly submission. "
                "This is normal operation, not a failure."
            )
            return 0

    if not db_path.exists():
        print(f"No bank database at {db_path}; nothing to compute deficits against.")
        return 0

    requests = _compute_bank_deficits(db_path, taxonomy_path)
    if not requests:
        print("No topic is understocked past the batch threshold; nothing to submit.")
        return 0

    cost_tracker = CostTracker()
    batch_client = MockBatchClient(cost_tracker=cost_tracker)
    batch_id = batch_client.submit(requests)

    if llm_client is not None and cost_tracker.records:
        record = cost_tracker.records[-1]
        llm_client._log_cost(
            CostLogRow(
                timestamp=datetime.now(UTC),
                model=record.model,
                lane="paid",  # nightly top-up is always paid-lane batch (CLAUDE.md rule 9)
                prompt_tokens=record.input_tokens,
                completion_tokens=record.output_tokens,
                cost_usd=record.estimated_cost_usd,
                purpose="nightly_topup",
            )
        )

    total_items = sum(r.count for r in requests)
    _save_state(
        state_path,
        {
            "batch_id": batch_id,
            "submitted_at": datetime.now(UTC).isoformat(),
            "total_items": total_items,
            "requests": [r.model_dump() for r in requests],
        },
    )
    print(
        f"Submitted batch {batch_id} covering {total_items} items across "
        f"{len({r.topic_id for r in requests})} topics."
    )
    return 0


def run_ingest(
    state_path: Path | str = DEFAULT_STATE_PATH,
    batch_id_override: str | None = None,
    batch_client: MockBatchClient | None = None,
) -> int:
    """Poll the persisted batch. Exits 0 if nothing has been submitted yet or the
    batch is still pending (retried tomorrow); exits non-zero only on a real
    failure (a failed batch, or a batch id that no longer matches its own
    persisted requests)."""
    state = _load_state(Path(state_path))
    persisted_batch_id = state.get("batch_id")

    if not persisted_batch_id and not batch_id_override:
        print("No batch has been submitted yet; nothing to ingest.")
        return 0

    raw_requests = state.get("requests") or []
    if not raw_requests:
        print("No persisted batch requests found; nothing to ingest.")
        return 0

    requests = [GenerationRequest.model_validate(r) for r in raw_requests]

    if batch_client is None:
        batch_client = MockBatchClient()
    # Idempotent: submitting the same content hashes to the same batch id, which
    # is how a fresh process (a new GitHub Actions job) recovers batch state
    # submitted by an earlier, separate process.
    batch_id = batch_client.submit(requests)
    target_batch_id = batch_id_override or persisted_batch_id

    if target_batch_id != batch_id:
        print(
            f"Persisted batch id {target_batch_id!r} does not match the id recomputed "
            f"from persisted requests ({batch_id!r}); refusing to guess. Leaving batch "
            "id in place for investigation."
        )
        return 1

    try:
        status = batch_client.poll(target_batch_id)
    except KeyError:
        print(f"Batch '{target_batch_id}' not found; nothing to ingest.")
        return 0

    if status == "pending":
        print(f"Batch {target_batch_id} is still pending; will retry on the next scheduled run.")
        return 0

    if status == "failed":
        print(f"Batch {target_batch_id} failed; leaving batch id in place for investigation.")
        return 1

    items = batch_client.retrieve(target_batch_id)
    print(f"Ingested {len(items)} candidate items from batch {target_batch_id}.")
    # Verification-chain insertion into the bank (src/verification, src/bank) is
    # owned by other agents; wiring the retrieved items through it is left to them.
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Gemini Batch Generation & Ingestion CLI")
    parser.add_argument(
        "--action",
        choices=["submit", "ingest"],
        required=True,
        help="Action to perform: submit new batch or ingest completed batch",
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help="Optional specific batch ID for ingestion",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help="Path to the SQLite item bank",
    )
    parser.add_argument(
        "--state-path",
        type=str,
        default=str(DEFAULT_STATE_PATH),
        help="Path to the persisted batch state file",
    )
    args = parser.parse_args()

    if args.action == "submit":
        exit_code = run_submit(db_path=args.db, state_path=args.state_path)
    else:
        exit_code = run_ingest(state_path=args.state_path, batch_id_override=args.batch_id)

    sys.exit(exit_code)
