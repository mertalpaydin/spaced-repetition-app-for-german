"""Batch client for asynchronous candidate item generation with cost and token accounting.

Also the nightly automation entry point (``python -m src.generation.batch_client``),
invoked by ``.github/workflows/generate-submit.yml`` and ``generate-ingest.yml``.
"""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.contracts import (
    MODEL_GENERATE,
    BankItem,
    BatchId,
    BatchStatus,
    CandidateItem,
    Difficulty,
    Distractor,
    GenerationRequest,
    InsertReport,
)
from src.generation.deficits import (
    MIN_STOCK_PER_TIER_DEMAND_STANDIN,
    build_generation_requests,
    compute_topic_deficits,
)
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec, load_spec
from src.llm.client import BudgetExceeded, GeminiLlmClient

DEFAULT_STATE_PATH = Path("data/generation_state.json")
DEFAULT_DB_PATH = Path("data/bank.db")
DEFAULT_SPECS_DIR = Path("data/specs")
# Stage 2's lexicon output (01-foundation.md): 11,633 lemmas banded by CEFR
# level. Despite the path, this is the real vocabulary data, not a test
# double -- confirmed live: with no VocabularyStore ever constructed here,
# layer1_syntax's vocabulary-ceiling check AND fix 4's real-word validation
# of expander alternatives (docs/audits/stage-04-pilot-2026-08-15.md) were
# both silently inert in every actual pilot/nightly run to date, since both
# gate on ``self.vocab_store is not None``.
DEFAULT_VOCAB_LEVELS_PATH = Path("data/fixtures/corpus/vocab_levels.json")


class SpecSheetMissingError(RuntimeError):
    """Raised when a ``GenerationRequest`` names a topic with no spec sheet in
    ``data/specs/``. Per docs/02-content-pipeline.md stage 3, a request whose
    topic has no spec sheet must fail loudly, never be silently skipped: the
    spec sheet carries the gold examples, the grammar-terminology blocklist
    context, the vocabulary ceiling and the target form, none of which have
    any safe default."""


_UNDERSCORE_RUN_RE = re.compile(r"_{3,}")


def _normalize_underscore_runs(text: str) -> str:
    """Collapse any run of three or more underscores to exactly three.

    A pilot candidate whose prompt used four underscores (``"____"``) passed
    the gap-count check outright, because Python's ``str.count`` matches
    non-overlapping occurrences left to right: ``"____".count("___")`` is 1,
    with the fourth underscore simply left over and uncounted, so the check
    reads it as one well-formed gap. That is structurally wrong the moment
    anything downstream assumes the gap is exactly three characters wide
    (e.g. replacing only the first three and leaving a stray underscore
    stuck to the filled answer). Normalising every run of 3+ underscores
    down to exactly ``"___"`` here -- at ``_parse_response``, the point a
    model's raw text first becomes a ``CandidateItem``, and again at
    ``_verify_and_insert_candidates``'s entry, the point ANY candidate
    (model-generated, hand-fed, or from another pipeline) reaches
    verification -- means every later check and every consumer sees an
    exact, canonical three-underscore gap regardless of what actually
    produced the prompt.
    """
    return _UNDERSCORE_RUN_RE.sub("___", text)


def _normalize_candidate_prompt(item: CandidateItem) -> CandidateItem:
    """Return ``item`` with its prompt's underscore runs normalised, or
    ``item`` itself unchanged if there was nothing to normalise (``CandidateItem``
    is frozen, so a copy is only made when the prompt actually changes)."""
    normalized_prompt = _normalize_underscore_runs(item.prompt)
    if normalized_prompt == item.prompt:
        return item
    return item.model_copy(update={"prompt": normalized_prompt})


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


def _load_spec_or_raise(topic_id: str, specs_dir: Path | str) -> TopicSpec:
    """Load ``data/specs/<topic_id>.yaml``, raising ``SpecSheetMissingError``
    rather than skipping the request when it does not exist."""
    spec_path = Path(specs_dir) / f"{topic_id}.yaml"
    if not spec_path.exists():
        raise SpecSheetMissingError(
            f"No spec sheet found for topic '{topic_id}' at {spec_path}; refusing to "
            "silently skip generation for this topic. Add the spec sheet or remove the "
            "request."
        )
    return load_spec(spec_path)


class GeminiBatchClient:
    """Real batch client: for every ``GenerationRequest`` it loads that topic's
    spec sheet and builds the prompt via ``PromptBuilder.build_generation_prompt``
    (the gold examples, blocklist, vocabulary ceiling and target form the spec
    carries), then sends that prompt through the single LLM entry point
    (``src/llm/client.py``, CLAUDE.md rule 4) so cost accounting, caching, the
    spend ceiling and the two-lane routing all apply exactly as they do to
    every other call in the app.

    Used whenever an API key is configured (see ``_build_llm_client_if_configured``);
    ``MockBatchClient`` is reserved for tests and offline development. Every
    request's prompt is sent through one ``GeminiLlmClient.generate_many``
    call (not a loop of individual ``generate()`` calls): on the free lane
    that dispatches requests concurrently, and on the paid lane it submits
    every request as ONE real multi-item Gemini batch job.
    """

    def __init__(
        self,
        llm_client: GeminiLlmClient,
        specs_dir: Path | str = DEFAULT_SPECS_DIR,
    ) -> None:
        self.llm_client = llm_client
        self.specs_dir = Path(specs_dir)
        self._prompt_builder = PromptBuilder()
        self.submitted_batches: dict[BatchId, list[GenerationRequest]] = {}
        self.batch_statuses: dict[BatchId, BatchStatus] = {}
        self._results: dict[BatchId, list[CandidateItem]] = {}

    @staticmethod
    def _batch_id_for(requests: list[GenerationRequest]) -> BatchId:
        serialized = json.dumps([r.model_dump() for r in requests], sort_keys=True)
        return f"batch_{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:12]}"

    def submit(
        self,
        requests: list[GenerationRequest],
        force_lane: Literal["free", "paid"] | None = None,
    ) -> BatchId:
        """Build a spec-anchored prompt per request and send it. Idempotent:
        resubmitting the same request set returns the existing batch id and
        makes no second model call.

        ``force_lane`` is threaded straight through to
        ``GeminiLlmClient.generate_many``: ``None`` (the default) leaves the
        normal free-unless-closed-or-restricted policy in place, which is
        what every pilot run uses unless it deliberately opts into a real
        stock run on the paid lane's actual Batch API
        (``scripts/step5_pilot_generation.py --batch``).
        """
        batch_id = self._batch_id_for(requests)
        if batch_id in self.submitted_batches:
            return batch_id

        # Load every spec (and therefore fail loudly on any missing spec sheet)
        # before spending a single token, so a bad request in position 5 of 10
        # cannot leave 4 partially-billed calls behind it.
        specs = [_load_spec_or_raise(req.topic_id, self.specs_dir) for req in requests]

        # One ``generate_many`` call for every request, not one ``generate()``
        # call per request in a loop: on the free lane this dispatches them
        # concurrently instead of serially, and on the paid lane it submits
        # ONE real multi-item batch job instead of one job per request (see
        # ``GeminiLlmClient.generate_many``'s docstring for why the latter
        # matters -- the previous one-job-per-item loop was the dominant cost
        # of a slow pilot run).
        prompts = [
            self._prompt_builder.build_generation_prompt(spec, req.count, req.difficulty)
            for req, spec in zip(requests, specs, strict=True)
        ]
        response_texts = self.llm_client.generate_many(
            prompts,
            model=MODEL_GENERATE,
            purpose="generation",
            is_user_content=False,
            force_lane=force_lane,
        )

        candidates: list[CandidateItem] = []
        for req, response_text in zip(requests, response_texts, strict=True):
            candidates.extend(self._parse_response(response_text, req))

        self.submitted_batches[batch_id] = requests
        self.batch_statuses[batch_id] = "completed"
        self._results[batch_id] = candidates
        return batch_id

    def poll(self, batch_id: BatchId) -> BatchStatus:
        if batch_id not in self.submitted_batches:
            raise KeyError(f"Batch ID '{batch_id}' not found.")
        return self.batch_statuses[batch_id]

    def retrieve(self, batch_id: BatchId) -> list[CandidateItem]:
        if batch_id not in self.submitted_batches:
            raise KeyError(f"Batch ID '{batch_id}' not found.")
        if self.batch_statuses[batch_id] != "completed":
            status = self.batch_statuses[batch_id]
            raise RuntimeError(f"Batch '{batch_id}' is not completed yet (status: {status}).")
        return self._results[batch_id]

    @staticmethod
    def _parse_response(response_text: str, req: GenerationRequest) -> list[CandidateItem]:
        """Parse the model's JSON response (matching the ``required_output_schema``
        ``PromptBuilder`` embeds) into ``CandidateItem``s.

        Malformed output -- not JSON, no ``items`` list, or an item that fails
        ``CandidateItem`` validation -- is dropped, never coerced or defaulted
        (docs/02-content-pipeline.md: ``test_malformed_model_output_is_rejected_not_coerced``).
        ``topic_id`` and ``difficulty`` are always taken from the request, never
        trusted from the model's own claim about them.

        The prompt requests raw JSON but the transport sends no
        ``response_mime_type``, so Gemini routinely wraps the payload in a
        ```` ```json ... ``` ```` fence (confirmed against the live free-lane
        endpoint). Strip that fence before parsing; a response that still
        isn't valid JSON after stripping is genuinely malformed and is
        dropped as before.
        """
        text = response_text.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.removesuffix("```").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            return []

        items: list[CandidateItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            normalized = dict(raw)
            if isinstance(normalized.get("prompt"), str):
                normalized["prompt"] = _normalize_underscore_runs(normalized["prompt"])
            try:
                items.append(
                    CandidateItem.model_validate(
                        {**normalized, "topic_id": req.topic_id, "difficulty": req.difficulty}
                    )
                )
            except ValidationError:
                continue
        return items


# ==============================================================================
# Nightly automation: deficit-driven submit, and pending-tolerant ingest.
# ==============================================================================


def _build_llm_client_if_configured(
    *, forbid_paid_lane: bool = False, forbid_batch: bool = False
) -> GeminiLlmClient | None:
    """Route real cost accounting and the spend ceiling through the single client
    in src/llm/client.py (CLAUDE.md rule 4) whenever an API key is configured.
    Falls back to None (no ceiling check possible, offline dev) otherwise.

    ``forbid_paid_lane`` and ``forbid_batch`` both default to ``False`` here,
    which preserves the existing nightly-automation behaviour
    (``run_submit``/``run_ingest``): overflow is meant to accumulate and ship
    as one real batch job (CLAUDE.md 9, "Overflow accumulates, it does not
    fail over per request"). Pilot generation
    (``src.generation.pilot.run_pilot``) passes ``forbid_batch=True``
    explicitly instead (and ``forbid_paid_lane=False``): the project owner's
    instruction is "no batch for the pilot", not "no paid lane for the
    pilot" -- a pilot must still be able to spend on the paid lane
    synchronously once the free lane's daily quota is spent, it must just
    never queue a real batch job. See ``BatchForbiddenError`` in
    ``src.llm.client`` for the full history of that distinction."""
    if (
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    ):
        return GeminiLlmClient(forbid_paid_lane=forbid_paid_lane, forbid_batch=forbid_batch)
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

    batch_client: MockBatchClient | GeminiBatchClient
    if llm_client is not None:
        # A key is configured: build real spec-anchored prompts (gold examples,
        # blocklist, vocabulary ceiling, target form) and send them through the
        # single LLM entry point (CLAUDE.md rule 4). Cost accounting, caching
        # and the spend ceiling all run for real via GeminiLlmClient.generate();
        # no separate cost-log bookkeeping is needed here.
        batch_client = GeminiBatchClient(llm_client)
        try:
            batch_id = batch_client.submit(requests)
        except BudgetExceeded as exc:
            print(
                f"Monthly spend ceiling reached mid-batch ({exc}); stopping before the "
                "batch state is persisted. This is normal operation, not a failure."
            )
            return 0
    else:
        # No key configured (offline dev, CI): the deterministic offline
        # synthesiser. Reserved for tests and local iteration, never for a
        # real nightly run.
        batch_client = MockBatchClient()
        batch_id = batch_client.submit(requests)

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


class RejectedCandidateRecord(BaseModel):
    """One candidate the verification chain rejected, with enough of the
    original candidate and the rejection itself to diagnose it later without
    re-running generation.

    docs/audits/stage-04-pilot-2026-08-14.md: "31 of 54 rejections were
    structural_malformation... The pilot does not persist rejected items, so
    the cause cannot be diagnosed from this run." This is that persistence:
    every rejected candidate, not just the count.
    """

    model_config = ConfigDict(frozen=True)
    topic_id: str
    type: str
    difficulty: int
    prompt: str
    proposed_answer: str
    layer_failed: int | None
    error_type: str | None
    reason: str | None


class VerifiedIngestResult(BaseModel):
    """Outcome of running retrieved candidates through the verification chain
    and the bank insert, kept as one object so callers (``run_ingest``, the
    stage-4 pilot) print an honest, consistent summary instead of each
    re-deriving their own counts."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    retrieved: int
    accepted: int
    rejected: int
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)
    insert_report: InsertReport
    accepted_items: list[BankItem] = Field(default_factory=list)
    rejected_items: list[RejectedCandidateRecord] = Field(default_factory=list)


def _candidate_item_id(item: CandidateItem) -> str:
    """Content-addressed id: the same candidate (topic, type, difficulty,
    prompt, proposed answer) always hashes to the same id, so re-ingesting the
    same batch after a crash lands on ``bank.insert``'s idempotency-on-id path
    (a duplicate, not a second row) rather than depending on batch/position
    bookkeeping that would break under retries."""
    payload = json.dumps(
        {
            "topic_id": item.topic_id,
            "type": item.type,
            "difficulty": item.difficulty,
            "prompt": item.prompt,
            "proposed_answer": item.proposed_answer,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"gen_{digest}"


def _verify_and_insert_candidates(
    candidates: list[CandidateItem],
    db_path: Path | str,
    taxonomy_path: Path | str | None,
    source_batch_id: str,
    specs_dir: Path | str = DEFAULT_SPECS_DIR,
    llm_client: GeminiLlmClient | None = None,
    vocab_levels_path: Path | str = DEFAULT_VOCAB_LEVELS_PATH,
) -> VerifiedIngestResult:
    """Run retrieved candidates through the full verification chain
    (``src.verification.pipeline.VerificationPipeline``) and insert the
    accepted ones into the bank, stamping ``facet`` and ``confusion_group``
    exactly as ``scripts/step2_build_item_bank.py`` already does for the
    bootstrap ingest -- ``ingest_items`` is imported and reused here, not
    duplicated, so the two ingest paths cannot drift apart.

    Each candidate is verified against its OWN topic's spec sheet (mirroring
    ``tests/test_verification.py``'s known-good measurement): without it, the
    vocabulary-ceiling check and the per-topic token-length limit silently
    never run. A topic with no spec sheet on disk is verified with ``spec=None``
    instead of crashing the whole ingest -- generation already refuses to
    submit for such a topic (``SpecSheetMissingError``), so this only matters
    for candidates that arrive by some other path (e.g. a hand-fed test).

    ``vocab_levels_path`` is loaded into a real ``VocabularyStore`` and
    passed to ``VerificationPipeline`` -- previously never wired here at
    all, which meant layer 1's vocabulary-ceiling check AND fix 4's
    real-word validation of expander alternatives were both silently inert
    in every real pilot/nightly run (confirmed live: a hallucinated
    non-word survived to the accepted set because nothing was ever checking
    it against real vocabulary). A missing file degrades to ``None``
    (both checks skip, as they already did) rather than crashing the ingest.

    ``llm_client``, when supplied, is passed through to
    ``VerificationPipeline`` to enable its model-backed layer 5 (semantic /
    answer-set expansion, see ``src.verification.layer_expander``). ``None``
    (the default) disables that layer only, exactly as
    ``VerificationPipeline.__init__`` documents -- every other layer still
    runs.
    """
    # Local imports: this keeps src/generation/batch_client.py importable
    # without pulling in spaCy (src/verification) and the bank/taxonomy
    # loaders at module import time, matching the existing local-import
    # convention in this module (see ``_compute_bank_deficits``).
    from scripts.step2_build_item_bank import ingest_items

    from src.bank.storage import SqliteItemBank
    from src.lexicon.vocabulary import VocabularyStore
    from src.taxonomy.loader import load_taxonomy
    from src.verification.layer_expander import AnswerSetExpander
    from src.verification.pipeline import VerificationPipeline

    # Every candidate, regardless of where it came from (a model's raw JSON
    # via _parse_response, a hand-fed test double, or -- once cycle 3 wires
    # it up -- the generate-then-blank pipeline), is normalised here before
    # ANY verification-chain check runs. _parse_response already normalises
    # the LLM-direct pipeline's prompts at parse time; this is the second,
    # unconditional gate that catches everything else too.
    candidates = [_normalize_candidate_prompt(c) for c in candidates]

    db_path = Path(db_path)
    specs_dir = Path(specs_dir)
    vocab_levels_path = Path(vocab_levels_path)
    topics = load_taxonomy(taxonomy_path)
    topics_by_id = {t.id: t for t in topics}

    vocab_store = VocabularyStore.load(vocab_levels_path) if vocab_levels_path.exists() else None

    specs_by_topic: dict[str, TopicSpec] = {}
    for topic_id in {c.topic_id for c in candidates}:
        spec_path = specs_dir / f"{topic_id}.yaml"
        if spec_path.exists():
            specs_by_topic[topic_id] = load_spec(spec_path)

    bank = SqliteItemBank(db_path)
    existing_items = bank.get_all_items()
    pipeline = VerificationPipeline(vocab_store=vocab_store, topics=topics, llm_client=llm_client)

    verification = pipeline.verify_batch(
        candidates, specs=specs_by_topic, existing_bank_items=existing_items
    )

    rejected_by_reason: dict[str, int] = {}
    rejected_items: list[RejectedCandidateRecord] = []
    accepted_dicts: list[dict[str, Any]] = []
    for result in verification.results:
        if not result.accepted or result.item is None:
            reason = result.error_type or result.reason or "unknown"
            rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            if result.item is not None:
                rejected_items.append(
                    RejectedCandidateRecord(
                        topic_id=result.item.topic_id,
                        type=result.item.type,
                        difficulty=result.item.difficulty,
                        prompt=result.item.prompt,
                        proposed_answer=result.item.proposed_answer,
                        layer_failed=result.layer_failed,
                        error_type=result.error_type,
                        reason=result.reason,
                    )
                )
            continue

        item = result.item
        topic = topics_by_id.get(item.topic_id)
        if topic is None:
            # A request should never name a topic outside the taxonomy (the
            # spec-sheet load already fails loudly at submit time), but an
            # item claiming a topic we cannot resolve here has no honest CEFR
            # to stamp, so it is counted and dropped rather than guessed.
            rejected_by_reason["unknown_topic"] = rejected_by_reason.get("unknown_topic", 0) + 1
            rejected_items.append(
                RejectedCandidateRecord(
                    topic_id=item.topic_id,
                    type=item.type,
                    difficulty=item.difficulty,
                    prompt=item.prompt,
                    proposed_answer=item.proposed_answer,
                    layer_failed=None,
                    error_type="unknown_topic",
                    reason="Item claims a topic_id outside the loaded taxonomy.",
                )
            )
            continue

        accepted_answers = result.accepted_answers or AnswerSetExpander.expand_answers(item)
        accepted_dicts.append(
            {
                "id": _candidate_item_id(item),
                "topic_id": item.topic_id,
                "type": item.type,
                "difficulty": item.difficulty,
                "cefr": topic.cefr,
                "prompt": item.prompt,
                "cue": item.cue,
                "accepted_answers": accepted_answers,
                "distractors": [d.model_dump() for d in item.distractors],
                "domain": item.domain,
                "carrier_lemmas": item.carrier_lemmas,
                "source_sentence_id": item.source_sentence_id,
            }
        )

    insert_report = ingest_items(
        bank, accepted_dicts, topics_by_id, source_batch_id=source_batch_id
    )

    accepted_items: list[BankItem] = []
    for raw in accepted_dicts:
        stored = bank.get_item(str(raw["id"]))
        if stored is not None:
            accepted_items.append(stored)

    return VerifiedIngestResult(
        retrieved=len(candidates),
        accepted=len(accepted_dicts),
        rejected=len(candidates) - len(accepted_dicts),
        rejected_by_reason=rejected_by_reason,
        insert_report=insert_report,
        accepted_items=accepted_items,
        rejected_items=rejected_items,
    )


def _print_ingest_summary(result: VerifiedIngestResult, batch_id: str) -> None:
    print(f"Ingested {result.retrieved} candidate items from batch {batch_id}.")
    print(f"  Accepted by verification chain: {result.accepted}")
    print(f"  Rejected by verification chain: {result.rejected}")
    for reason, count in sorted(result.rejected_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")
    print(f"  Inserted into bank: {result.insert_report.inserted}")
    print(f"  Duplicates (already in bank): {result.insert_report.duplicates}")
    if result.insert_report.rejected:
        print(f"  Rejected at bank insert: {result.insert_report.rejected}")
        for reason in result.insert_report.rejection_reasons:
            print(f"    - {reason}")


def run_ingest(
    state_path: Path | str = DEFAULT_STATE_PATH,
    batch_id_override: str | None = None,
    batch_client: MockBatchClient | GeminiBatchClient | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    taxonomy_path: Path | str | None = None,
    llm_client: GeminiLlmClient | None = None,
) -> int:
    """Poll the persisted batch. Exits 0 if nothing has been submitted yet or the
    batch is still pending (retried tomorrow); exits non-zero only on a real
    failure (a failed batch, or a batch id that no longer matches its own
    persisted requests). Once a batch is retrieved, candidates are run through
    the full verification chain and accepted items are inserted into the bank
    (see ``_verify_and_insert_candidates``); nothing is discarded silently."""
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
        if llm_client is None:
            llm_client = _build_llm_client_if_configured()
        # Same rule as run_submit: a real key uses the real batch client
        # (spec-anchored prompts through the single LLM entry point);
        # MockBatchClient is reserved for tests and offline development.
        batch_client = (
            GeminiBatchClient(llm_client) if llm_client is not None else MockBatchClient()
        )
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
    result = _verify_and_insert_candidates(
        items,
        db_path=db_path,
        taxonomy_path=taxonomy_path,
        source_batch_id=target_batch_id,
        llm_client=llm_client,
    )
    _print_ingest_summary(result, target_batch_id)
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    from src.llm.env import load_env_file

    # Lane keys live in a gitignored .env (docs/01-foundation.md); nothing else
    # in the process reads it. Shell-exported values still win.
    load_env_file()

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
        exit_code = run_ingest(
            state_path=args.state_path, batch_id_override=args.batch_id, db_path=args.db
        )

    sys.exit(exit_code)
