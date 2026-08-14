"""Unified Gemini LLM client with cost accounting, budget ceilings, and two-lane execution."""

import os
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import google.genai as genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.contracts import MODEL_GENERATE, MODEL_LIVE, MODEL_VERIFY, THINKING_VERIFY
from src.llm.cache import LlmCache
from src.llm.config import DEFAULT_CONFIG_PATH, load_restrict_user_content_to_paid_lane

Lane = Literal["free", "paid", "cache"]
QuotaType = Literal["rpm", "rpd"]

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


class BudgetExceeded(Exception):
    """Raised when month-to-date LLM spend exceeds the configured spend ceiling."""


class QuotaExceededError(Exception):
    """Raised by the transport when Gemini returns a 429.

    ``quota_type`` distinguishes the two kinds of 429 Google returns, which must be
    handled differently: RPM (requests-per-minute) exhaustion is transient and the
    same lane should be retried after a short backoff; RPD (requests-per-day)
    exhaustion means the free lane is closed for the rest of the Pacific day.
    """

    def __init__(self, quota_type: QuotaType, message: str = "") -> None:
        self.quota_type = quota_type
        super().__init__(message or f"Gemini quota exceeded: {quota_type}")


class MissingApiKeyError(RuntimeError):
    """Raised when a lane is used but its API key was never configured.

    Deliberately its own type (rather than a bare ``RuntimeError``) so callers
    and tests can distinguish "no key configured" from any other transport
    failure, and deliberately named after the lane so the message is
    actionable rather than a generic auth failure.
    """


class ModelRejectedError(RuntimeError):
    """Raised when the Gemini API rejects a request because of its model id.

    Wraps the SDK's error but always names the model explicitly: a silently
    wrong or stale model id would otherwise fail in a way that is easy to
    miss, or -- worse -- be billed against the wrong tier and produce garbage
    without anyone noticing.
    """


class CostLogRow(BaseModel):
    """Immutable audit record of a single LLM invocation or cache hit."""

    model_config = ConfigDict(frozen=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str
    lane: Lane
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    purpose: str = "generation"


class GeminiLlmClient:
    """The single entry point for all LLM calls in the application.

    Note on transport: ``_call_transport`` makes the real ``google.genai`` call
    (sync ``models.generate_content`` on the free lane, batch ``batches.create``
    on the paid lane). The ``genai.Client`` for each lane is built lazily, on
    first use, from ``free_api_key``/``paid_api_key`` -- constructing this class
    or importing this module never requires a key or touches the network. Cost
    accounting, caching, the spend ceiling, and the two-lane routing logic sit
    entirely outside ``_call_transport`` and are unaffected by the transport
    used, so tests exercise them by substituting a fake transport at that seam
    instead of hitting the network.
    """

    # Pricing per million tokens (USD)
    PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
        "gemini-3.5-flash-lite": (0.075, 0.30),
        "gemini-3.7-flash": (0.15, 0.60),
    }

    # RPM 429s are transient; retry the same (free) lane after a short backoff.
    RPM_BACKOFF_SECONDS: float = 1.0
    RPM_MAX_RETRIES: int = 1

    def __init__(
        self,
        free_api_key: str | None = None,
        paid_api_key: str | None = None,
        spend_ceiling_usd: float = 5.00,
        cost_log_path: Path | str = ".cache/cost_log.jsonl",
        cache_dir: Path | str = ".cache/llm",
        restrict_user_content_to_paid_lane: bool | None = None,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.free_api_key = (
            free_api_key or os.getenv("GEMINI_FREE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
        self.paid_api_key = paid_api_key or os.getenv("GEMINI_PAID_API_KEY")
        self.spend_ceiling_usd = spend_ceiling_usd
        self.cost_log_path = Path(cost_log_path)
        self.cache = LlmCache(cache_dir=cache_dir)
        # The privacy flag is read from config.yaml by default, not hardcoded. An
        # explicit constructor argument still overrides it, which tests rely on.
        self.restrict_user_content_to_paid_lane = (
            restrict_user_content_to_paid_lane
            if restrict_user_content_to_paid_lane is not None
            else load_restrict_user_content_to_paid_lane(config_path)
        )
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep

        self.free_lane_open = True
        self.free_lane_closed_until: datetime | None = None

        self.cost_records: list[CostLogRow] = []
        self._load_cost_log()

        # Built lazily, per lane, on first real transport call -- see
        # ``_get_sdk_client``. Never constructed here, so importing or
        # instantiating this class never requires an API key.
        self._free_client: genai.Client | None = None
        self._paid_client: genai.Client | None = None

    def _load_cost_log(self) -> None:
        if not self.cost_log_path.exists():
            return
        for line in self.cost_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                self.cost_records.append(CostLogRow.model_validate_json(line))
            except ValidationError as exc:
                # A corrupt or malformed row must not crash startup, but it also
                # must not vanish silently: surface it so under-counted spend is
                # noticed instead of hidden.
                warnings.warn(
                    f"Skipping corrupt cost_log row in {self.cost_log_path}: {exc}",
                    stacklevel=2,
                )

    def get_month_to_date_spend(self, now: datetime | None = None) -> float:
        """Calculate total USD spend for the current calendar month."""
        ref_time = now or self._clock()
        total = 0.0
        for row in self.cost_records:
            if row.timestamp.year == ref_time.year and row.timestamp.month == ref_time.month:
                total += row.cost_usd
        return round(total, 6)

    def _log_cost(self, row: CostLogRow) -> None:
        self.cost_records.append(row)
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cost_log_path.open("a", encoding="utf-8") as f:
            f.write(row.model_dump_json() + "\n")

    def _estimate_cost(
        self, model: str, prompt_tokens: int, completion_tokens: int, lane: Lane
    ) -> float:
        if lane == "cache" or lane == "free":
            # The free lane is an unbilled Google Cloud project: it genuinely
            # costs nothing. Cost only accrues once work moves to the paid lane.
            return 0.0
        in_p, out_p = self.PRICING_PER_MILLION.get(model, (0.075, 0.30))
        # Batch 50% discount applies to paid lane batch requests
        cost = ((prompt_tokens / 1_000_000) * in_p + (completion_tokens / 1_000_000) * out_p) * 0.5
        return round(cost, 6)

    # ------------------------------------------------------------------
    # Two-lane bookkeeping
    # ------------------------------------------------------------------

    def _next_pacific_midnight(self, ref_time: datetime) -> datetime:
        """Return the next Pacific-time midnight strictly after ``ref_time``, in UTC."""
        local = ref_time.astimezone(PACIFIC_TZ)
        next_day = local.date() + timedelta(days=1)
        next_midnight_local = datetime.combine(next_day, datetime.min.time(), tzinfo=PACIFIC_TZ)
        return next_midnight_local.astimezone(UTC)

    def _refresh_free_lane_state(self, ref_time: datetime) -> None:
        """Reopen the free lane once the Pacific-midnight reset has passed."""
        if (
            not self.free_lane_open
            and self.free_lane_closed_until is not None
            and ref_time >= self.free_lane_closed_until
        ):
            self.free_lane_open = True
            self.free_lane_closed_until = None

    def _close_free_lane_until_pacific_midnight(self, ref_time: datetime) -> None:
        self.free_lane_open = False
        self.free_lane_closed_until = self._next_pacific_midnight(ref_time)

    def _determine_lane(self, is_user_content: bool, ref_time: datetime) -> Lane:
        self._refresh_free_lane_state(ref_time)
        if self.restrict_user_content_to_paid_lane and is_user_content:
            return "paid"
        if not self.free_lane_open:
            return "paid"
        return "free"

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    # A batch job is not instantaneous; poll ``batches.get`` at this interval,
    # bounded by this many attempts, before giving up. This keeps
    # ``_call_transport``'s signature synchronous (matching the free lane and
    # every existing caller) without blocking forever on a stuck job. Real
    # Gemini batch jobs commonly finish in minutes; Google's SLA allows up to
    # 24h for very large jobs, which this bound deliberately does not cover --
    # a job that takes that long needs its own out-of-band tracking, which is
    # outside this wrapper's scope.
    BATCH_POLL_INTERVAL_SECONDS: float = 10.0
    BATCH_POLL_MAX_ATTEMPTS: int = 360  # ~1 hour at the default interval

    def _get_sdk_client(self, lane: Lane) -> genai.Client:
        """Build (or reuse) the ``genai.Client`` for ``lane``, lazily.

        Never called from ``__init__``: constructing this class or importing
        this module must not require an API key. The free lane and paid lane
        are separate Google Cloud projects (CLAUDE.md 9), so each gets its own
        client built from its own key.
        """
        if lane == "free":
            if self._free_client is None:
                if not self.free_api_key:
                    raise MissingApiKeyError(
                        "The free lane needs an API key from the unbilled Google "
                        "Cloud project, but none is configured. Set "
                        "GEMINI_FREE_API_KEY (or GEMINI_API_KEY) in the environment, "
                        "or pass free_api_key= to GeminiLlmClient."
                    )
                self._free_client = genai.Client(api_key=self.free_api_key)
            return self._free_client
        if lane == "paid":
            if self._paid_client is None:
                if not self.paid_api_key:
                    raise MissingApiKeyError(
                        "The paid lane needs an API key from the billed Google "
                        "Cloud project, but none is configured. Set "
                        "GEMINI_PAID_API_KEY in the environment, or pass "
                        "paid_api_key= to GeminiLlmClient."
                    )
                self._paid_client = genai.Client(api_key=self.paid_api_key)
            return self._paid_client
        raise ValueError(f"_get_sdk_client has no client for lane={lane!r}")

    def _thinking_config_for(self, model: str) -> genai_types.ThinkingConfig:
        """Thinking is off everywhere except the ``gemini-3.7-flash`` verify workloads.

        CLAUDE.md 213: thinking tokens bill as output and can multiply the
        largest cost line severalfold, so generation runs with thinking
        disabled (``thinking_budget=0``). Only ``MODEL_VERIFY`` uses it, at the
        level configured in ``THINKING_VERIFY``.
        """
        if model == MODEL_VERIFY:
            return genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel(THINKING_VERIFY)
            )
        return genai_types.ThinkingConfig(thinking_budget=0)

    def _classify_quota_error(self, exc: genai_errors.ClientError) -> QuotaType:
        """Distinguish RPM (per-minute) from RPD (per-day) 429s.

        The SDK does not expose a typed distinction: both surface as
        ``ClientError`` with ``code == 429`` and ``status ==
        "RESOURCE_EXHAUSTED"``. The real signal is Google's structured
        ``google.rpc.QuotaFailure`` error detail, whose violations carry a
        ``quotaId``/``quotaMetric`` string containing "PerMinute" or "PerDay"
        (see https://ai.google.dev/gemini-api/docs/rate-limits). We inspect
        that structured field first, rather than pattern-matching the
        free-text message, and only fall back to the message text if the
        structured detail is missing.
        """
        details = exc.details if isinstance(exc.details, dict) else {}
        error_body = details.get("error", details) if isinstance(details, dict) else {}
        quota_ids: list[str] = []
        if isinstance(error_body, dict):
            for detail in error_body.get("details") or []:
                if not isinstance(detail, dict):
                    continue
                for violation in detail.get("violations") or []:
                    if not isinstance(violation, dict):
                        continue
                    quota_id = violation.get("quotaId") or violation.get("quotaMetric")
                    if quota_id:
                        quota_ids.append(str(quota_id))

        joined = " ".join(quota_ids).lower()
        if "perday" in joined or "per_day" in joined:
            return "rpd"
        if "perminute" in joined or "per_minute" in joined:
            return "rpm"

        # No structured quota metric found -- fall back to the free-text
        # message, still preferring explicit words over a guess.
        message = (exc.message or str(exc)).lower()
        if "per day" in message or "daily" in message:
            return "rpd"
        if "per minute" in message or "rate" in message:
            return "rpm"

        # Genuinely unclassifiable. Default to RPM: a wrong RPM guess costs one
        # bounded retry (RPM_MAX_RETRIES) before the error propagates, while a
        # wrong RPD guess closes the free lane for the rest of the Pacific day
        # and forces every subsequent call onto the paid lane -- the more
        # expensive mistake to make on a guess.
        warnings.warn(
            "Could not classify a Gemini 429 as RPM or RPD from structured or "
            f"free-text error content; defaulting to RPM. Raw error: {exc}",
            stacklevel=2,
        )
        return "rpm"

    def _extract_text_and_tokens(
        self, response: genai_types.GenerateContentResponse, *, prompt: str
    ) -> tuple[str, int, int]:
        """Pull the response text and real token counts off ``usage_metadata``.

        ``completion_tokens`` includes ``thoughts_token_count``: thinking
        tokens are billed as output (CLAUDE.md 213), so leaving them out of
        the cost log would under-report spend on any call where thinking is
        enabled.
        """
        text = response.text or ""
        usage = response.usage_metadata
        if (
            usage is not None
            and usage.prompt_token_count is not None
            and usage.candidates_token_count is not None
        ):
            prompt_tokens = usage.prompt_token_count
            completion_tokens = usage.candidates_token_count + (usage.thoughts_token_count or 0)
            return text, prompt_tokens, completion_tokens

        # A real Gemini response always carries usage_metadata. If it is
        # genuinely absent -- an unexpected response shape, not something
        # observed against the live API -- we cannot know the true token
        # counts. Fall back to a crude whitespace-based estimate rather than
        # silently reporting a cost of zero, and say so loudly since the cost
        # log's whole purpose is to be trustworthy.
        warnings.warn(
            "Gemini response had no usable usage_metadata; falling back to a "
            "word-count token estimate for cost logging. This will under- or "
            "over-count against the real bill.",
            stacklevel=2,
        )
        prompt_tokens = len(prompt.split()) * 2
        completion_tokens = len(text.split()) * 2
        return text, prompt_tokens, completion_tokens

    def _call_sync(
        self,
        client: genai.Client,
        *,
        model: str,
        prompt: str,
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> tuple[str, int, int]:
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise QuotaExceededError(self._classify_quota_error(exc), str(exc)) from exc
            raise ModelRejectedError(
                f"Gemini API rejected the request for model {model!r} (purpose={purpose!r}): {exc}"
            ) from exc
        return self._extract_text_and_tokens(response, prompt=prompt)

    def _poll_batch_job(
        self, client: genai.Client, job: genai_types.BatchJob
    ) -> genai_types.BatchJob:
        """Poll ``batches.get`` until the job leaves the pending/running states."""
        terminal_states = {
            genai_types.JobState.JOB_STATE_SUCCEEDED,
            genai_types.JobState.JOB_STATE_FAILED,
            genai_types.JobState.JOB_STATE_CANCELLED,
            genai_types.JobState.JOB_STATE_EXPIRED,
            genai_types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
        }
        attempts = 0
        while job.state not in terminal_states:
            if attempts >= self.BATCH_POLL_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Gemini batch job {job.name!r} did not finish within "
                    f"{self.BATCH_POLL_MAX_ATTEMPTS} polls at "
                    f"{self.BATCH_POLL_INTERVAL_SECONDS}s intervals (state={job.state})."
                )
            self._sleep(self.BATCH_POLL_INTERVAL_SECONDS)
            attempts += 1
            if job.name is None:
                raise RuntimeError("Gemini batch job has no name to poll on.")
            job = client.batches.get(name=job.name)
        return job

    def _call_batch(
        self,
        client: genai.Client,
        *,
        model: str,
        prompt: str,
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> tuple[str, int, int]:
        inlined_request = genai_types.InlinedRequest(model=model, contents=prompt, config=config)
        try:
            job = client.batches.create(model=model, src=[inlined_request])
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise QuotaExceededError(self._classify_quota_error(exc), str(exc)) from exc
            raise ModelRejectedError(
                f"Gemini batch API rejected the request for model {model!r} "
                f"(purpose={purpose!r}): {exc}"
            ) from exc

        job = self._poll_batch_job(client, job)

        if job.state != genai_types.JobState.JOB_STATE_SUCCEEDED:
            error_detail = job.error.message if job.error is not None else "no error detail"
            raise RuntimeError(
                f"Gemini batch job {job.name!r} for model {model!r} "
                f"(purpose={purpose!r}) did not succeed "
                f"(state={job.state}): {error_detail}"
            )

        inlined_responses = job.dest.inlined_responses if job.dest is not None else None
        if not inlined_responses:
            raise RuntimeError(
                f"Gemini batch job {job.name!r} for model {model!r} succeeded "
                "but returned no inlined responses."
            )

        inlined = inlined_responses[0]
        if inlined.error is not None:
            raise RuntimeError(
                f"Gemini batch request failed for model {model!r} "
                f"(purpose={purpose!r}): {inlined.error.message}"
            )
        if inlined.response is None:
            raise RuntimeError(
                f"Gemini batch job {job.name!r} for model {model!r} returned no response body."
            )
        return self._extract_text_and_tokens(inlined.response, prompt=prompt)

    def _call_transport(
        self,
        *,
        model: str,
        prompt: str,
        lane: Lane,
        mode: Literal["sync", "batch"],
        purpose: str,
    ) -> tuple[str, int, int]:
        """Execute the generation call and return (response_text, prompt_tokens, completion_tokens).

        This is the single seam through which every real ``google.genai`` call
        is made: sync ``models.generate_content`` for the free lane, batch
        ``batches.create``/``batches.get`` for the paid lane. It is also the
        seam tests patch to simulate 429s and other transport behaviour
        without touching the network (see ``QuotaExceededError``).
        """
        # CLAUDE.md 9: the free lane is always synchronous, the paid lane is
        # always batch. A call that violates either is a defect in the caller,
        # not something to silently coerce.
        if lane == "paid" and mode != "batch":
            raise ValueError(
                f"Paid-lane call for purpose={purpose!r} requested mode={mode!r}; "
                "the paid lane is batch-only (a non-batch paid-lane call is a defect)."
            )
        if lane == "free" and mode != "sync":
            raise ValueError(
                f"Free-lane call for purpose={purpose!r} requested mode={mode!r}; "
                "the free lane is always synchronous."
            )

        client = self._get_sdk_client(lane)
        config = genai_types.GenerateContentConfig(thinking_config=self._thinking_config_for(model))

        if mode == "sync":
            return self._call_sync(
                client, model=model, prompt=prompt, config=config, purpose=purpose
            )
        return self._call_batch(client, model=model, prompt=prompt, config=config, purpose=purpose)

    def _call_transport_with_lane_handling(
        self,
        *,
        model: str,
        prompt: str,
        lane: Lane,
        purpose: str,
        ref_time: datetime,
    ) -> tuple[str, int, int, Lane]:
        """Call the transport, handling RPM/RPD 429s per the two-lane rules.

        RPM exhaustion: back off and retry on the same (free) lane.
        RPD exhaustion: close the free lane until the next Pacific midnight and
        move the work to the paid lane.
        """
        mode: Literal["sync", "batch"] = "batch" if lane == "paid" else "sync"
        attempts = 0
        while True:
            try:
                text, p_tok, c_tok = self._call_transport(
                    model=model, prompt=prompt, lane=lane, mode=mode, purpose=purpose
                )
                return text, p_tok, c_tok, lane
            except QuotaExceededError as exc:
                if exc.quota_type == "rpm":
                    if attempts >= self.RPM_MAX_RETRIES:
                        raise
                    attempts += 1
                    self._sleep(self.RPM_BACKOFF_SECONDS)
                    continue
                # RPD: close the free lane and move the work to paid.
                self._close_free_lane_until_pacific_midnight(ref_time)
                lane = "paid"
                mode = "batch"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        model: str = MODEL_GENERATE,
        purpose: str = "generation",
        use_cache: bool = True,
        is_user_content: bool = False,
        now: datetime | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute a text generation call through the cache and cost accounting gates."""
        ref_time = now or self._clock()

        # 1. Budget Gate Check
        current_spend = self.get_month_to_date_spend(ref_time)
        if current_spend >= self.spend_ceiling_usd:
            raise BudgetExceeded(
                f"Monthly spend ceiling of ${self.spend_ceiling_usd:.2f} reached "
                f"(current spend: ${current_spend:.4f})."
            )

        # 2. Local Disk Cache Check
        if use_cache:
            cached = self.cache.get(model=model, prompt=prompt, **kwargs)
            if cached is not None:
                self._log_cost(
                    CostLogRow(
                        timestamp=ref_time,
                        model=model,
                        lane="cache",
                        prompt_tokens=len(prompt.split()),
                        completion_tokens=len(cached.split()),
                        cost_usd=0.0,
                        purpose=purpose,
                    )
                )
                return cached

        # 3. Determine lane (free unless restricted-and-user-content, or free lane closed)
        lane = self._determine_lane(is_user_content, ref_time)

        # 4. Call transport, handling 429s per the two-lane rules
        response_text, prompt_tokens, completion_tokens, lane = (
            self._call_transport_with_lane_handling(
                model=model, prompt=prompt, lane=lane, purpose=purpose, ref_time=ref_time
            )
        )

        cost_usd = self._estimate_cost(model, prompt_tokens, completion_tokens, lane)

        # 5. Persist to Cost Log & Cache
        self._log_cost(
            CostLogRow(
                timestamp=ref_time,
                model=model,
                lane=lane,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                purpose=purpose,
            )
        )

        if use_cache:
            self.cache.set(model=model, prompt=prompt, response=response_text, **kwargs)

        return response_text

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        purpose: str = "generation",
        is_user_content: bool = False,
    ) -> str:
        """Satisfy ``src.llm.provider.LlmProvider`` so this client can be injected anywhere
        a provider is expected (LiveExplainer, ProductionGrader, MinimalPairGenerator,
        WeeklyReportGenerator)."""
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        return self.generate(
            full_prompt,
            model=MODEL_LIVE,
            purpose=purpose,
            is_user_content=is_user_content,
        )
