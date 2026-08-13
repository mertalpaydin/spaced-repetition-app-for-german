"""Batch client for asynchronous candidate item generation with cost and token accounting."""

import hashlib
import json
import time
from dataclasses import dataclass, field

from src.contracts import (
    BatchId,
    BatchStatus,
    CandidateItem,
    Distractor,
    GenerationRequest,
)


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
    """Mock implementation of the BatchClient protocol for unit and integration tests."""

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
                model="gemini-3.5-flash-lite",
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
