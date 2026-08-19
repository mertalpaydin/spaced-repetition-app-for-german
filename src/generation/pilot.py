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
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

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

# The project owner's own words: batch is too slow to develop against, so a
# pilot run's DEFAULT path is the free lane, synchronous and paced by
# ``GeminiLlmClient``'s own rate limiter, spilling over to the paid lane
# ON DEMAND (also synchronous, never the real Batch API) once the free
# lane's daily quota is spent -- "no batch api ... for pilot go to paid on
# demand api, if free lane is already expired." The real Batch API is
# reserved for a deliberate ``--batch`` real stock run (CLAUDE.md 9, "two
# lanes, two projects": this supersedes that section's earlier "the paid
# lane is always batch" wording for pilots specifically, at the owner's
# explicit instruction). ``requests`` here means
# ``GenerationRequest`` objects, i.e. one (topic, difficulty) cell -- each
# one prompt asking the model for that cell's whole ``count`` of items in a
# single call, NOT one prompt per item. A 300-item pilot with the default
# ``--topics-per-cefr 3`` spreads over at most 12 topics x 3 difficulties =
# 36 requests, so pacing 36 calls against the rate limiter, not 300.
#
# ``DEFAULT_SYNC_CHUNK_SIZE`` splits even that modest request list into
# groups so a long run reports progress and lands partial, inspectable
# results on disk as it goes rather than staying opaque until the very last
# request completes. Every existing test's request count is well under this,
# so chunking is invisible to them (one chunk, identical behaviour to the
# unchunked call this replaces).
DEFAULT_SYNC_CHUNK_SIZE = 20

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
    used_batch: bool = False
    chunks_run: int = 1
    elapsed_seconds: float = 0.0


def select_pilot_topics(
    topics: list[Topic],
    topics_per_cefr: int = DEFAULT_TOPICS_PER_CEFR,
    cefr: str | None = None,
    classes: Iterable[str] | None = DEFAULT_PILOT_CLASSES,
    all_topics: bool = False,
) -> list[Topic]:
    """Select the topics a pilot run will generate for.

    Two different jobs, distinguished by ``cefr``:

    * ``cefr is None`` (the audit sample): take up to ``topics_per_cefr``
      topics from every band (or, with ``all_topics=True``, EVERY topic in
      every band), sorted by id for determinism, so the run spreads across
      levels and measures the chain's behaviour over the whole taxonomy.
    * ``cefr`` set (stocking a level): take EVERY topic in that one band --
      already what ``all_topics`` would ask for, so the two compose without
      conflict; ``all_topics`` simply has nothing further to add here.

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

    ``all_topics`` widens the audit sample (``cefr is None``) from
    ``topics_per_cefr`` topics per band to every topic per band: the default
    ``--topics-per-cefr`` of 3 means a ``--pilot 300`` run only ever
    exercises 12 topics (3 per band x 4 bands) out of the whole taxonomy, so
    a run meant to measure the chain's behaviour broadly was instead
    measuring 12 topics repeatedly. Composes with ``classes`` (already
    applied above, before this parameter is even read) exactly like
    ``topics_per_cefr`` does.
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
        selected.extend(band if all_topics else band[:topics_per_cefr])
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


def _write_jsonl_rows(path: Path, items: list[Any], batch_id: str, *, append: bool) -> None:
    """Shared JSONL writer for both the review and rejected files.

    ``append=False`` truncates first (the start of a fresh run); ``append=True``
    adds to what is already there (one chunk's worth of results, mid-run) so a
    run that dies partway through still leaves every completed chunk's output
    inspectable on disk -- CLAUDE.md 7's "resumable, or at least not opaque"
    bar for a long pilot, satisfied by never buffering the whole run in memory
    before the first byte hits disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for item in items:
            row = item.model_dump(mode="json")
            row["_pilot_batch_id"] = batch_id
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_review_file(
    path: Path, items: list[Any], batch_id: str, *, append: bool = False
) -> None:
    """Write accepted items to a JSONL file for the stage-4 hand audit
    (docs/02-content-pipeline.md: "Sample 100 accepted items at random.
    Audit each")."""
    _write_jsonl_rows(path, items, batch_id, append=append)


def _write_rejected_file(
    path: Path, items: list[Any], batch_id: str, *, append: bool = False
) -> None:
    """Write every REJECTED candidate, with its layer/reason, to a JSONL file.

    docs/audits/stage-04-pilot-2026-08-14.md: "The pilot does not persist
    rejected items, so the cause cannot be diagnosed from this run." This is
    the fix -- run one pilot and the rejection breakdown (31 of 54
    ``structural_malformation`` in the 2026-08-14 run) is diagnosable
    afterwards instead of only countable.
    """
    _write_jsonl_rows(path, items, batch_id, append=append)


def _chunk_requests(
    requests: list[GenerationRequest], chunk_size: int
) -> list[list[GenerationRequest]]:
    """Split ``requests`` into groups of at most ``chunk_size``, preserving
    order. ``chunk_size <= 0`` is treated as "one chunk" (no splitting) rather
    than raising, since it is only ever a tuning knob, never a correctness
    gate."""
    if chunk_size <= 0 or len(requests) <= chunk_size:
        return [requests]
    return [requests[i : i + chunk_size] for i in range(0, len(requests), chunk_size)]


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
    all_topics: bool = False,
    use_batch: bool = False,
    sync_chunk_size: int = DEFAULT_SYNC_CHUNK_SIZE,
    progress: bool = True,
) -> PilotRunReport:
    """Generate a bounded, spread sample, verify it, insert what passes, and
    write the accepted set to ``review_path`` (and every rejected candidate,
    with its reason, to ``rejected_path``) for the kill-gate audit.

    Refuses outright, before any model call, if ``item_count`` exceeds
    ``item_cap`` (the nightly item cap by default) or if the monthly spend
    ceiling is already reached -- the same two guardrails the nightly
    automation already respects (``src.generation.deficits.NIGHTLY_ITEM_CAP``,
    ``GeminiLlmClient.spend_ceiling_usd``).

    ``all_topics`` is threaded straight through to ``select_pilot_topics``:
    with the default ``topics_per_cefr`` sampling, a ``--pilot 300`` run
    only ever covers 12 topics (3 per CEFR band); this widens the topic
    pool to every topic passing the ``classes``/``cefr`` filters.

    ``use_batch`` (default ``False``): route every request through the free
    lane's synchronous path, paced by ``GeminiLlmClient``'s own rate limiter
    -- fast, cheap, dev-iteration generation, which is what a pilot is for.
    When ``False`` and an ``llm_client`` is auto-built here (i.e. the caller
    did not pass one in), that client is constructed with
    ``forbid_batch=True`` and ``forbid_paid_lane=False``: real batch
    submission is genuinely off, not merely deprioritised, but the paid lane
    itself stays available for on-demand (synchronous) calls once the free
    lane's daily quota is exhausted (or closes mid-run) -- the project
    owner's explicit instruction: "when I said no batch api I meant for
    pilot go to paid on demand api, if free lane is already expired." An
    earlier version of this flag used ``forbid_paid_lane=True`` instead,
    which forbade the paid lane outright; that meant a pilot whose free-lane
    quota ran out mid-run raised ``PaidLaneForbiddenError`` on every
    subsequent call, including the model verification backstop, which
    degraded its entire run to ``"not_run"`` -- a run that looked like it
    passed (exit code 0) while zero verification calls were actually made.
    ``forbid_batch=True`` fixes that: the run keeps generating and
    verifying, on the paid lane, synchronously, instead of silently doing
    nothing. See ``BatchForbiddenError`` in ``src.llm.client`` for the full
    history.
    Set ``use_batch=True`` only for a deliberate real stock run through the
    paid lane's actual Batch API (``scripts/step5_pilot_generation.py
    --batch``); it requires a live ``GeminiBatchClient`` (a configured
    ``GEMINI_PAID_API_KEY``), since there is nothing for an offline mock run
    to batch. The request set is still split into ``sync_chunk_size``-sized
    groups either way, each submitted, verified, and inserted before the
    next starts, so a long run reports progress chunk by chunk and a crash
    partway through still leaves every completed chunk's items on disk
    (``review_path``/``rejected_path``) and in the bank instead of losing the
    whole run.
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
        topics,
        topics_per_cefr=topics_per_cefr,
        cefr=cefr,
        classes=classes,
        all_topics=all_topics,
    )
    requests = build_pilot_requests(pilot_topics, item_count, difficulties=difficulties)
    if not requests:
        raise ValueError("No pilot requests could be built (empty taxonomy or item_count <= 0).")

    ran_live = False
    if batch_client is None:
        if llm_client is None:
            # Pilots are genuinely off the real Batch API by default (the
            # project owner's instruction): a client built here for a
            # non-``--batch`` run forbids batch submission, so it never
            # queues a real batch job, but the paid lane itself stays
            # available for on-demand (synchronous) calls once the free
            # lane's daily quota is exhausted. ``--batch`` (``use_batch=True``)
            # is the deliberate opt-in and gets a client with the ordinary
            # batch-by-default policy instead.
            llm_client = _build_llm_client_if_configured(
                forbid_paid_lane=False, forbid_batch=not use_batch
            )
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

    if use_batch and not isinstance(batch_client, GeminiBatchClient):
        raise ValueError(
            "--batch requires a live client: no GEMINI_PAID_API_KEY (or "
            "GEMINI_FREE_API_KEY/GEMINI_API_KEY) is configured, so there is no "
            "paid-lane Batch API for --batch to submit to. Omit --batch for "
            "offline development against the mock, or configure a key for a "
            "real stock run."
        )
    force_lane: Literal["free", "paid"] | None = "paid" if use_batch else None

    cost_before = llm_client.get_month_to_date_spend() if llm_client is not None else 0.0

    # A stable id for the WHOLE pilot run, independent of how many chunks it
    # is split into -- stamped onto every review/rejected row so the run is
    # one comparable audit unit, exactly as a single unchunked submission
    # already behaved (every existing test's request count fits in one
    # chunk, so this is byte-identical to the old single-batch_id behaviour
    # for them).
    pilot_run_id = GeminiBatchClient._batch_id_for(requests)

    chunks = _chunk_requests(requests, sync_chunk_size)
    total_chunks = len(chunks)

    all_accepted: list[Any] = []
    all_rejected: list[Any] = []
    total_retrieved = 0
    total_accepted = 0
    total_rejected = 0
    rejected_by_reason: dict[str, int] = {}
    total_inserted = 0
    total_duplicates = 0
    total_bank_rejected = 0

    start_time = time.monotonic()
    for chunk_index, chunk_requests in enumerate(chunks, start=1):
        if isinstance(batch_client, GeminiBatchClient):
            sub_batch_id = batch_client.submit(chunk_requests, force_lane=force_lane)
        else:
            sub_batch_id = batch_client.submit(chunk_requests)
        batch_client.poll(sub_batch_id)
        candidates = batch_client.retrieve(sub_batch_id)

        result = _verify_and_insert_candidates(
            candidates,
            db_path=db_path,
            taxonomy_path=taxonomy_path,
            source_batch_id=sub_batch_id,
            llm_client=llm_client,
        )

        all_accepted.extend(result.accepted_items)
        all_rejected.extend(result.rejected_items)
        total_retrieved += result.retrieved
        total_accepted += result.accepted
        total_rejected += result.rejected
        for reason, count in result.rejected_by_reason.items():
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + count
        total_inserted += result.insert_report.inserted
        total_duplicates += result.insert_report.duplicates
        total_bank_rejected += result.insert_report.rejected

        # Written after EVERY chunk, not once at the end: a run killed
        # partway through (Ctrl-C, a crashed shell, an exhausted RPD quota)
        # still leaves every completed chunk's accepted/rejected items on
        # disk, auditable, instead of losing the whole run's work.
        _write_review_file(review_path, result.accepted_items, pilot_run_id, append=chunk_index > 1)
        _write_rejected_file(
            rejected_path, result.rejected_items, pilot_run_id, append=chunk_index > 1
        )

        if progress:
            elapsed = time.monotonic() - start_time
            chunk_item_count = sum(r.count for r in chunk_requests)
            print(
                f"[pilot] chunk {chunk_index}/{total_chunks}: requested "
                f"{chunk_item_count} items over {len(chunk_requests)} requests -- "
                f"{result.retrieved} retrieved, {result.accepted} accepted, "
                f"{result.rejected} rejected (running total: {total_accepted} "
                f"accepted / {total_retrieved} retrieved, {elapsed:.0f}s elapsed)"
            )

    cost_after = llm_client.get_month_to_date_spend() if llm_client is not None else 0.0
    cost_incurred = round(cost_after - cost_before, 6)
    elapsed_seconds = round(time.monotonic() - start_time, 3)

    return PilotRunReport(
        requested_item_count=item_count,
        topics_used=[t.id for t in pilot_topics],
        batch_id=pilot_run_id,
        retrieved=total_retrieved,
        accepted=total_accepted,
        rejected=total_rejected,
        rejected_by_reason=rejected_by_reason,
        inserted=total_inserted,
        duplicates=total_duplicates,
        bank_rejected=total_bank_rejected,
        cost_usd_incurred=cost_incurred,
        review_file=str(review_path),
        rejected_review_file=str(rejected_path),
        ran_live=ran_live,
        used_batch=use_batch,
        chunks_run=total_chunks,
        elapsed_seconds=elapsed_seconds,
    )
