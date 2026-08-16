"""Stage 4 kill-gate pilot generation (docs/00-index.md:32, docs/02-content-pipeline.md).

The kill gate requires auditing ~100 accepted items and computing the
post-verifier error rate *before* any full top-up runs. This module is the
bounded, spread run that produces that audit sample: it never generates the
full ~1000-item shortfall, and it refuses outright if asked to exceed the
nightly item cap or the monthly spend ceiling that already govern the nightly
automation (``src.generation.deficits``, ``src.llm.client``).

Entry point for humans: ``scripts/step5_pilot_generation.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import Difficulty, GenerationRequest, Topic, VerificationClass
from src.generation.batch_client import (
    DEFAULT_DB_PATH,
    GeminiBatchClient,
    MockBatchClient,
    _build_llm_client_if_configured,
    _verify_and_insert_candidates,
)
from src.generation.deficits import NIGHTLY_ITEM_CAP
from src.llm.client import BudgetExceeded, GeminiLlmClient

DEFAULT_REVIEW_PATH = Path("data/pilot_review.jsonl")
DEFAULT_REJECTED_PATH = Path("data/pilot_rejected.jsonl")
DEFAULT_PILOT_ITEM_COUNT = 100
DEFAULT_TOPICS_PER_CEFR = 3
DEFAULT_DIFFICULTIES: tuple[Difficulty, ...] = (1, 2, 3)

# docs/audits/generation-track-plan.md "Topic triage": every verification
# class the taxonomy assigns, and the subset a pilot run draws from by
# default. "semantic" topics have no mechanically checkable answer, so they
# are excluded unless a caller opts in explicitly via --classes.
ALL_VERIFICATION_CLASSES: tuple[VerificationClass, ...] = (
    "computable",
    "lexical_table",
    "structural",
    "semantic",
)
DEFAULT_PILOT_CLASSES: tuple[VerificationClass, ...] = (
    "computable",
    "lexical_table",
    "structural",
)


class PilotRunReport(BaseModel):
    """Everything the person running the pilot from a terminal needs to know
    whether it worked and whether the kill-gate audit can start."""

    model_config = ConfigDict(frozen=True)
    requested_item_count: int
    topics_used: list[str] = Field(default_factory=list)
    batch_id: str
    retrieved: int
    accepted: int
    rejected: int
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)
    inserted: int
    duplicates: int
    bank_rejected: int
    cost_usd_incurred: float
    review_file: str
    rejected_review_file: str
    ran_live: bool


def select_pilot_topics(
    topics: list[Topic],
    topics_per_cefr: int = DEFAULT_TOPICS_PER_CEFR,
    cefr: str | None = None,
    classes: Iterable[str] | None = DEFAULT_PILOT_CLASSES,
) -> list[Topic]:
    """Select the topics a pilot run will generate for.

    Two different jobs, distinguished by ``cefr``:

    * ``cefr is None`` (the audit sample): take up to ``topics_per_cefr``
      topics from every band, sorted by id for determinism, so the run
      spreads across levels and measures the chain's behaviour over the
      whole taxonomy.
    * ``cefr`` set (stocking a level): take EVERY topic in that one band.

    docs/audits/stage-04-recovery-plan.md fix E. The spread was the only
    available behaviour, and its output was being used as the item bank.
    Those are different jobs. Spreading 100 items over 12 topics across four
    bands leaves one to three items per topic after rejection, which is a
    sample, not a stock, and it cannot support a scheduler test.

    It is also the wrong shape pedagogically: interleaving is a within-level
    technique. The effect comes from interleaving dative against accusative
    against genitive, so the learner must first work out which rule applies.
    Interleaving A1 against B2 is a difficulty cliff, not interleaving.

    ``classes`` restricts the pool to topics whose ``verification_class`` is
    one of the given values (docs/audits/generation-track-plan.md "Topic
    triage"), applied before the CEFR selection above so the two filters
    compose: a ``--cefr B1 --classes computable`` run only ever sees B1
    topics that are also computable. ``None`` disables the filter entirely
    (every verification class, including ``semantic``); the default is
    ``DEFAULT_PILOT_CLASSES``, which excludes ``semantic``.
    """
    if classes is not None:
        allowed_classes = set(classes)
        topics = [t for t in topics if t.verification_class in allowed_classes]

    by_cefr: dict[str, list[Topic]] = {}
    for topic in topics:
        by_cefr.setdefault(topic.cefr, []).append(topic)

    if cefr is not None:
        if cefr not in by_cefr:
            raise ValueError(
                f"No topics at CEFR level {cefr!r} with verification_class in "
                f"{sorted(classes) if classes is not None else 'any'!r}. "
                f"Available CEFR levels after class filtering: {sorted(by_cefr)}."
            )
        return sorted(by_cefr[cefr], key=lambda t: t.id)

    selected: list[Topic] = []
    for band_level in sorted(by_cefr):
        band = sorted(by_cefr[band_level], key=lambda t: t.id)
        selected.extend(band[:topics_per_cefr])
    return selected


def build_pilot_requests(
    topics: list[Topic],
    item_count: int,
    difficulties: tuple[Difficulty, ...] = DEFAULT_DIFFICULTIES,
) -> list[GenerationRequest]:
    """Spread ``item_count`` items round-robin across every (topic,
    difficulty) cell in ``topics`` x ``difficulties``, so the pilot audit
    sample covers a genuine spread of topics and levels rather than
    exhausting one topic before touching the next.

    Topics marked ``requires_context`` in the taxonomy are limited to their
    context-capable item types, exactly as
    ``src.generation.deficits.build_generation_requests`` does for the
    nightly top-up, so a pilot request never asks a topic for a type its
    taxonomy entry says cannot honestly test it.
    """
    if item_count <= 0 or not topics:
        return []

    cells: list[tuple[Topic, Difficulty]] = [
        (topic, difficulty) for topic in topics for difficulty in difficulties
    ]
    if not cells:
        return []

    counts = [0] * len(cells)
    for i in range(item_count):
        counts[i % len(cells)] += 1

    requests: list[GenerationRequest] = []
    for (topic, difficulty), count in zip(cells, counts, strict=True):
        if count == 0:
            continue
        item_types = list(topic.eligible_types) or ["cloze_free"]
        if topic.requires_context:
            item_types = [t for t in item_types if t in ("paragraph_cloze", "error_correction")]
            if not item_types:
                item_types = ["paragraph_cloze"]
        requests.append(
            GenerationRequest(
                topic_id=topic.id,
                count=count,
                difficulty=difficulty,
                item_types=item_types,
            )
        )
    return requests


def _write_review_file(path: Path, items: list[Any], batch_id: str) -> None:
    """Write accepted items to a JSONL file for the stage-4 hand audit
    (docs/02-content-pipeline.md: "Sample 100 accepted items at random.
    Audit each")."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            row = item.model_dump(mode="json")
            row["_pilot_batch_id"] = batch_id
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_rejected_file(path: Path, items: list[Any], batch_id: str) -> None:
    """Write every REJECTED candidate, with its layer/reason, to a JSONL file.

    docs/audits/stage-04-pilot-2026-08-14.md: "The pilot does not persist
    rejected items, so the cause cannot be diagnosed from this run." This is
    the fix -- run one pilot and the rejection breakdown (31 of 54
    ``structural_malformation`` in the 2026-08-14 run) is diagnosable
    afterwards instead of only countable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            row = item.model_dump(mode="json")
            row["_pilot_batch_id"] = batch_id
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_pilot(
    item_count: int = DEFAULT_PILOT_ITEM_COUNT,
    topics_per_cefr: int = DEFAULT_TOPICS_PER_CEFR,
    difficulties: tuple[Difficulty, ...] = DEFAULT_DIFFICULTIES,
    db_path: Path | str = DEFAULT_DB_PATH,
    taxonomy_path: Path | str | None = None,
    review_path: Path | str = DEFAULT_REVIEW_PATH,
    rejected_path: Path | str = DEFAULT_REJECTED_PATH,
    item_cap: int = NIGHTLY_ITEM_CAP,
    llm_client: GeminiLlmClient | None = None,
    batch_client: GeminiBatchClient | MockBatchClient | None = None,
    cefr: str | None = None,
    classes: Iterable[str] | None = DEFAULT_PILOT_CLASSES,
) -> PilotRunReport:
    """Generate a bounded, spread sample, verify it, insert what passes, and
    write the accepted set to ``review_path`` (and every rejected candidate,
    with its reason, to ``rejected_path``) for the kill-gate audit.

    Refuses outright, before any model call, if ``item_count`` exceeds
    ``item_cap`` (the nightly item cap by default) or if the monthly spend
    ceiling is already reached -- the same two guardrails the nightly
    automation already respects (``src.generation.deficits.NIGHTLY_ITEM_CAP``,
    ``GeminiLlmClient.spend_ceiling_usd``).
    """
    from src.taxonomy.loader import load_taxonomy

    if item_count > item_cap:
        raise ValueError(
            f"Pilot item_count ({item_count}) exceeds the item cap ({item_cap}); "
            "refusing to run. This is exactly the kind of accidental full top-up "
            "the stage-4 kill gate exists to prevent -- lower --pilot, or raise "
            "the cap deliberately if you mean it."
        )

    db_path = Path(db_path)
    review_path = Path(review_path)
    rejected_path = Path(rejected_path)

    topics = load_taxonomy(taxonomy_path)
    pilot_topics = select_pilot_topics(
        topics, topics_per_cefr=topics_per_cefr, cefr=cefr, classes=classes
    )
    requests = build_pilot_requests(pilot_topics, item_count, difficulties=difficulties)
    if not requests:
        raise ValueError("No pilot requests could be built (empty taxonomy or item_count <= 0).")

    ran_live = False
    if batch_client is None:
        if llm_client is None:
            llm_client = _build_llm_client_if_configured()
        if llm_client is not None:
            spend = llm_client.get_month_to_date_spend()
            if spend >= llm_client.spend_ceiling_usd:
                raise BudgetExceeded(
                    f"Monthly spend ceiling of ${llm_client.spend_ceiling_usd:.2f} is already "
                    f"reached (${spend:.4f}); refusing to run the pilot. This is normal "
                    "operation, not a bug."
                )
            batch_client = GeminiBatchClient(llm_client)
            ran_live = True
        else:
            batch_client = MockBatchClient()

    cost_before = llm_client.get_month_to_date_spend() if llm_client is not None else 0.0

    batch_id = batch_client.submit(requests)
    batch_client.poll(batch_id)
    candidates = batch_client.retrieve(batch_id)

    result = _verify_and_insert_candidates(
        candidates,
        db_path=db_path,
        taxonomy_path=taxonomy_path,
        source_batch_id=batch_id,
        llm_client=llm_client,
    )

    cost_after = llm_client.get_month_to_date_spend() if llm_client is not None else 0.0
    cost_incurred = round(cost_after - cost_before, 6)

    _write_review_file(review_path, result.accepted_items, batch_id)
    _write_rejected_file(rejected_path, result.rejected_items, batch_id)

    return PilotRunReport(
        requested_item_count=item_count,
        topics_used=[t.id for t in pilot_topics],
        batch_id=batch_id,
        retrieved=result.retrieved,
        accepted=result.accepted,
        rejected=result.rejected,
        rejected_by_reason=result.rejected_by_reason,
        inserted=result.insert_report.inserted,
        duplicates=result.insert_report.duplicates,
        bank_rejected=result.insert_report.rejected,
        cost_usd_incurred=cost_incurred,
        review_file=str(review_path),
        rejected_review_file=str(rejected_path),
        ran_live=ran_live,
    )
