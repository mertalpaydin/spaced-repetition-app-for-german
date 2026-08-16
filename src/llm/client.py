"""Unified Gemini LLM client with cost accounting, budget ceilings, and two-lane execution."""

import os
import threading
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import google.genai as genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.contracts import MODEL_GENERATE, MODEL_LIVE, MODEL_VERIFY, THINKING_VERIFY
from src.llm.cache import LlmCache
from src.llm.config import DEFAULT_CONFIG_PATH, load_restrict_user_content_to_paid_lane

Lane = Literal["free", "paid", "cache"]
QuotaType = Literal["rpm", "rpd"]

PACIFIC_TZ_KEY = "America/Los_Angeles"


@lru_cache(maxsize=1)
def pacific_tz() -> ZoneInfo:
    """Return the Pacific timezone, which is when Gemini's daily quota resets.

    Resolved lazily rather than at import time. Windows ships no system tz
    database, so ``ZoneInfo`` there depends on the ``tzdata`` package; binding
    this at module scope meant a missing tzdata broke every import of this
    module, including code paths that never touch a quota reset. The error is
    now raised only if the RPD reset is actually computed, and it names the fix.
    """
    try:
        return ZoneInfo(PACIFIC_TZ_KEY)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - platform dependent
        raise ZoneInfoNotFoundError(
            f"No timezone data for {PACIFIC_TZ_KEY!r}. On Windows the standard "
            "library has no system tz database; install the 'tzdata' package "
            "(it is a declared dependency, so `uv sync` should provide it)."
        ) from exc


class _SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter: blocks the calling thread in
    ``acquire()`` until issuing another call would keep the count at or below
    ``max_calls`` within the trailing ``period_seconds``.

    Exists because ``generate_many``'s free-lane path dispatches independent
    items from multiple threads concurrently. Concurrency alone does not
    raise the free tier's throughput ceiling (Google enforces 15 RPM
    regardless of how many requests arrive at once) -- it only changes
    whether those requests arrive evenly spaced or in a burst. A burst that
    exceeds the ceiling makes EVERY item in the burst 429 at roughly the same
    moment; if several of them then retry at roughly the same moment too
    (their backoffs overlapping), a single item can exhaust its bounded
    ``RPM_MAX_RETRIES`` and raise, which crashes the *entire* concurrent
    group via ``future.result()`` -- observed live. Pacing requests here,
    proactively, before they are ever sent, avoids the 429 in the first
    place instead of reacting to it after several threads have already
    collided on the same window.

    Uses ``time.monotonic()`` for the window itself (wall-clock pacing must
    be real regardless of any injected business clock used for cost-log
    timestamps or RPD determination), but sleeps via the client's own
    injectable ``sleep_fn`` so tests can still fake the wait.
    """

    def __init__(self, max_calls: int, period_seconds: float, sleep_fn: Callable[[float], None]):
        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._sleep = sleep_fn
        self._lock = threading.Lock()
        self._call_times: list[float] = []

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._call_times = [t for t in self._call_times if now - t < self._period_seconds]
                if len(self._call_times) < self._max_calls:
                    self._call_times.append(now)
                    return
                wait = self._period_seconds - (now - self._call_times[0])
            if wait > 0:
                self._sleep(wait)


class BudgetExceeded(Exception):
    """Raised when month-to-date LLM spend exceeds the configured spend ceiling."""


class QuotaExceededError(Exception):
    """Raised by the transport when Gemini returns a 429.

    ``quota_type`` distinguishes the two kinds of 429 Google returns, which must be
    handled differently: RPM (requests-per-minute) exhaustion is transient and the
    same lane should be retried after a short backoff; RPD (requests-per-day)
    exhaustion means the free lane is closed for the rest of the Pacific day.
    """

    def __init__(
        self,
        quota_type: QuotaType,
        message: str = "",
        retry_delay_seconds: float | None = None,
    ) -> None:
        self.quota_type = quota_type
        self.retry_delay_seconds = retry_delay_seconds
        super().__init__(message or f"Gemini quota exceeded: {quota_type}")


class ServerUnavailableError(Exception):
    """Raised by the transport when Gemini returns a 5xx (server overload,
    internal error -- confirmed live as ``503 UNAVAILABLE``, "This model is
    currently experiencing high demand").

    Deliberately distinct from ``QuotaExceededError``: this carries no quota
    signal at all, so it must never trigger RPD's lane-closing behaviour --
    it is transient trouble on Google's side, typically gone within seconds,
    and the correct response is the same short backoff-and-retry on the same
    lane that RPM already gets.
    """


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

    # Standard (non-batch) pricing per million tokens (USD, input, output).
    # ``_estimate_cost`` applies its own 0.5x batch-discount multiplier on top
    # of these, since Google's batch price for both models is confirmed
    # exactly half of standard -- these must stay the standard price, not the
    # already-discounted one.
    #
    # Verified 2026-08-14 against ai.google.dev/gemini-api/docs/pricing and
    # cross-checked against a second independent source; the previous values
    # here (0.075/0.30 and 0.15/0.60) were stale and under-estimated real
    # spend by roughly 4-8x, silently weakening the $5/month spend ceiling
    # this table feeds (docs/audits/stage-00-quota.md, addendum). gemini-3.7-flash
    # carries 2026 introductory pricing, 50% off standard through
    # 2026-12-31; standard pricing (1.50/7.50) applies from 2027-01-01.
    PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
        "gemini-3.5-flash-lite": (0.30, 2.50),
        "gemini-3.7-flash": (0.75, 3.75),
    }

    # RPM 429s are transient; retry the same (free) lane after a backoff. This
    # is only the fallback used when Google's response carries no structured
    # RetryInfo -- the real backoff duration is read off the error itself (see
    # ``_extract_retry_delay_seconds``), since the free tier's per-minute
    # window is tens of seconds, not one, and a fixed 1s backoff retries into
    # the same still-exhausted window and fails a pilot run outright.
    RPM_BACKOFF_SECONDS: float = 1.0
    RPM_MAX_RETRIES: int = 2

    # 5xx server overload is transient and carries no structured retry delay
    # (confirmed live: "503 UNAVAILABLE... currently experiencing high
    # demand" with a plain-text body, no RetryInfo). A few short retries on
    # the same lane clears it in practice; this is not a quota signal and
    # must never touch the RPD lane-closing path.
    SERVER_ERROR_BACKOFF_SECONDS: float = 5.0
    SERVER_ERROR_MAX_RETRIES: int = 3

    # ``generate_many``'s free-lane path fires independent items concurrently
    # instead of serially -- each is still just one HTTP round-trip, so wall
    # clock time is bound by the slowest concurrent item, not the sum of all
    # of them. Throughput is still capped by ``FREE_LANE_RATE_LIMIT_PER_MINUTE``
    # below regardless of this value; it mainly controls how many requests can
    # be in flight (and therefore latency-overlapping) at once.
    FREE_LANE_MAX_CONCURRENCY: int = 8

    # The free tier's real ceiling is 15 RPM. Pacing to slightly under it
    # (not to 15 itself) leaves headroom against Google's window boundary not
    # lining up exactly with ours -- see ``_SlidingWindowRateLimiter``.
    FREE_LANE_RATE_LIMIT_PER_MINUTE: int = 14

    # Google's inline (non-file) batch submission is documented as suitable
    # for "smaller batches that keep the total request size under 20MB";
    # above that (or for "a large number of requests" generally) Google's own
    # guidance is to switch to file-based batch input instead
    # (ai.google.dev/gemini-api/docs/batch-mode, verified 2026-08-15). This
    # client never had that ceiling in mind at all -- ``generate_many`` just
    # dumped the whole prompt list into one ``src=[...]`` call regardless of
    # size. 12MB leaves real headroom under the 20MB guidance for the
    # request's JSON envelope and per-item overhead beyond just prompt text;
    # 2000 is a defensive count cap for the (undocumented) possibility of a
    # separate per-request-count ceiling, sized well above any batch this
    # codebase's own caps (``NIGHTLY_ITEM_CAP``) will ever actually submit.
    BATCH_INLINE_MAX_BYTES: int = 12_000_000
    BATCH_INLINE_MAX_COUNT: int = 2000

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
        # Guards lazy client construction only: ``generate_many``'s free-lane
        # path can call ``_get_sdk_client`` from multiple threads on first
        # use. Cost-log writes and cache reads/writes never need this lock --
        # ``generate_many`` only ever performs those from the calling thread,
        # after collecting each worker's result, never from inside a worker.
        self._sdk_client_lock = threading.Lock()
        self._free_lane_limiter = _SlidingWindowRateLimiter(
            max_calls=self.FREE_LANE_RATE_LIMIT_PER_MINUTE,
            period_seconds=60.0,
            sleep_fn=self._sleep,
        )

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
        # Fallback for a model string absent from the table: the cheapest
        # known model's price, not the pre-2026-08-14-correction stale
        # default -- an under-estimate here would silently weaken the spend
        # ceiling exactly like the bug this table's own values just fixed.
        in_p, out_p = self.PRICING_PER_MILLION.get(model, (0.30, 2.50))
        # Batch 50% discount applies to paid lane batch requests
        cost = ((prompt_tokens / 1_000_000) * in_p + (completion_tokens / 1_000_000) * out_p) * 0.5
        return round(cost, 6)

    # ------------------------------------------------------------------
    # Two-lane bookkeeping
    # ------------------------------------------------------------------

    def _next_pacific_midnight(self, ref_time: datetime) -> datetime:
        """Return the next Pacific-time midnight strictly after ``ref_time``, in UTC."""
        local = ref_time.astimezone(pacific_tz())
        next_day = local.date() + timedelta(days=1)
        next_midnight_local = datetime.combine(next_day, datetime.min.time(), tzinfo=pacific_tz())
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
        with self._sdk_client_lock:
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

    def _thinking_config_for(self, model: str) -> genai_types.ThinkingConfig | None:
        """Thinking is off everywhere except the ``gemini-3.7-flash`` verify workloads.

        CLAUDE.md 213: thinking tokens bill as output and can multiply the
        largest cost line severalfold, so generation runs with thinking
        disabled. Only ``MODEL_VERIFY`` uses it, at the level configured in
        ``THINKING_VERIFY``.

        Every other model in the routing table (``gemini-3.5-flash-lite``) has
        no thinking capability at all, so there is nothing to disable: sending
        an explicit ``thinking_config`` (even ``thinking_budget=0``) is itself
        an INVALID_ARGUMENT rejection from the API, confirmed against the live
        endpoint, not just a no-op. Omitting the field entirely is the correct
        way to express "thinking off" on those models.
        """
        if model == MODEL_VERIFY:
            return genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel(THINKING_VERIFY)
            )
        return None

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

    def _extract_retry_delay_seconds(self, exc: genai_errors.ClientError) -> float | None:
        """Read Google's suggested backoff off the structured ``google.rpc.RetryInfo``
        error detail (e.g. ``retryDelay: "48s"``), the same ``details`` list
        ``_classify_quota_error`` reads ``QuotaFailure`` from.

        The free tier's per-minute window is tens of seconds, not one, so a
        fixed short backoff routinely retries into the same still-exhausted
        window (confirmed against the live API: quota resets in ~48s, not
        ~1s). Returns ``None`` if the detail is absent, so the caller can fall
        back to ``RPM_BACKOFF_SECONDS``.
        """
        details = exc.details if isinstance(exc.details, dict) else {}
        error_body = details.get("error", details) if isinstance(details, dict) else {}
        if not isinstance(error_body, dict):
            return None
        for detail in error_body.get("details") or []:
            if not isinstance(detail, dict):
                continue
            retry_delay = detail.get("retryDelay")
            if isinstance(retry_delay, str) and retry_delay.endswith("s"):
                try:
                    return float(retry_delay[:-1])
                except ValueError:
                    return None
        return None

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
        except genai_errors.ServerError as exc:
            raise ServerUnavailableError(str(exc)) from exc
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise QuotaExceededError(
                    self._classify_quota_error(exc),
                    str(exc),
                    retry_delay_seconds=self._extract_retry_delay_seconds(exc),
                ) from exc
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

    def _call_batch_many(
        self,
        client: genai.Client,
        *,
        model: str,
        prompts: list[str],
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> list[tuple[str, int, int]]:
        """Submit MANY prompts as ONE real Gemini batch job and poll it once.

        Submitting one item per job (the original implementation, now
        ``_call_batch``'s single-prompt case below) pays the batch API's own
        scheduling/queueing overhead once per item and gets none of the
        throughput benefit batching exists for -- confirmed live: a pilot run
        of ~50 paid-lane calls took the better part of an hour, each one
        individually queueing for 1-3 minutes, serially. Grouping N
        independent prompts into one job here pays that overhead once for all
        N, not N times, and is what ``generate_many`` uses for the paid lane.
        """
        inlined_requests = [
            genai_types.InlinedRequest(model=model, contents=p, config=config) for p in prompts
        ]
        try:
            job = client.batches.create(model=model, src=inlined_requests)
        except genai_errors.ServerError as exc:
            raise ServerUnavailableError(str(exc)) from exc
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise QuotaExceededError(
                    self._classify_quota_error(exc),
                    str(exc),
                    retry_delay_seconds=self._extract_retry_delay_seconds(exc),
                ) from exc
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
        if not inlined_responses or len(inlined_responses) != len(prompts):
            got = len(inlined_responses) if inlined_responses else 0
            raise RuntimeError(
                f"Gemini batch job {job.name!r} for model {model!r} returned "
                f"{got} responses for {len(prompts)} submitted prompts."
            )

        results: list[tuple[str, int, int]] = []
        for prompt, inlined in zip(prompts, inlined_responses, strict=True):
            if inlined.error is not None:
                raise RuntimeError(
                    f"Gemini batch request failed for model {model!r} "
                    f"(purpose={purpose!r}): {inlined.error.message}"
                )
            if inlined.response is None:
                raise RuntimeError(
                    f"Gemini batch job {job.name!r} for model {model!r} returned no response body."
                )
            results.append(self._extract_text_and_tokens(inlined.response, prompt=prompt))
        return results

    def _call_batch_many_with_retry(
        self,
        client: genai.Client,
        *,
        model: str,
        prompts: list[str],
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> list[tuple[str, int, int]]:
        """Transient-retry wrapper around ``_call_batch_many``, scoped to the
        paid lane's group submission: no RPD lane-switch (there is nowhere
        further to move from the paid lane), just the same bounded
        backoff-and-retry ``_call_transport_with_lane_handling`` gives a
        single free-lane call, applied to the whole group at once.
        """
        rpm_attempts = 0
        server_attempts = 0
        while True:
            try:
                return self._call_batch_many(
                    client, model=model, prompts=prompts, config=config, purpose=purpose
                )
            except QuotaExceededError as exc:
                if rpm_attempts >= self.RPM_MAX_RETRIES:
                    raise
                rpm_attempts += 1
                self._sleep(exc.retry_delay_seconds or self.RPM_BACKOFF_SECONDS)
                continue
            except ServerUnavailableError:
                if server_attempts >= self.SERVER_ERROR_MAX_RETRIES:
                    raise
                server_attempts += 1
                self._sleep(self.SERVER_ERROR_BACKOFF_SECONDS)
                continue

    def _chunk_indices_for_inline_batch(
        self, indices: list[int], prompts: list[str]
    ) -> list[list[int]]:
        """Split ``indices`` into groups that respect the inline batch API's
        real limits (``BATCH_INLINE_MAX_BYTES`` / ``BATCH_INLINE_MAX_COUNT``),
        so ``generate_many`` submits as FEW real batch jobs as possible (one,
        for every group small enough) while never risking an oversized
        request. A single prompt larger than the byte cap on its own still
        gets its own one-item chunk -- better to attempt it alone than
        silently drop it.
        """
        chunks: list[list[int]] = []
        current: list[int] = []
        current_bytes = 0
        for i in indices:
            size = len(prompts[i].encode("utf-8"))
            if current and (
                current_bytes + size > self.BATCH_INLINE_MAX_BYTES
                or len(current) >= self.BATCH_INLINE_MAX_COUNT
            ):
                chunks.append(current)
                current = []
                current_bytes = 0
            current.append(i)
            current_bytes += size
        if current:
            chunks.append(current)
        return chunks

    def _call_batch(
        self,
        client: genai.Client,
        *,
        model: str,
        prompt: str,
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> tuple[str, int, int]:
        """Single-prompt case of ``_call_batch_many``, kept for the free
        lane's per-item fallback path (e.g. an individual call that trips RPD
        mid-flight) where grouping isn't possible."""
        return self._call_batch_many(
            client, model=model, prompts=[prompt], config=config, purpose=purpose
        )[0]

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
            # Proactively pace free-lane requests to the real ceiling instead
            # of reacting to 429s after the fact -- see
            # ``_SlidingWindowRateLimiter``'s docstring for why this matters
            # specifically once ``generate_many`` can call this concurrently.
            self._free_lane_limiter.acquire()
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
        """Call the transport, handling RPM/RPD 429s and 5xx server overload
        per the two-lane rules.

        RPM exhaustion: back off and retry on the same (free) lane.
        RPD exhaustion: close the free lane until the next Pacific midnight and
        move the work to the paid lane.
        5xx server overload: not a quota signal at all -- back off and retry
        on the same lane, exactly like RPM, but with its own bounded retry
        count so it can never masquerade as quota exhaustion or trigger the
        RPD lane-closing path.
        """
        mode: Literal["sync", "batch"] = "batch" if lane == "paid" else "sync"
        rpm_attempts = 0
        server_attempts = 0
        while True:
            try:
                text, p_tok, c_tok = self._call_transport(
                    model=model, prompt=prompt, lane=lane, mode=mode, purpose=purpose
                )
                return text, p_tok, c_tok, lane
            except QuotaExceededError as exc:
                if exc.quota_type == "rpm":
                    if rpm_attempts >= self.RPM_MAX_RETRIES:
                        raise
                    rpm_attempts += 1
                    self._sleep(exc.retry_delay_seconds or self.RPM_BACKOFF_SECONDS)
                    continue
                # RPD: close the free lane and move the work to paid.
                self._close_free_lane_until_pacific_midnight(ref_time)
                lane = "paid"
                mode = "batch"
            except ServerUnavailableError:
                if server_attempts >= self.SERVER_ERROR_MAX_RETRIES:
                    raise
                server_attempts += 1
                self._sleep(self.SERVER_ERROR_BACKOFF_SECONDS)
                continue

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

    def generate_many(
        self,
        prompts: list[str],
        model: str = MODEL_GENERATE,
        purpose: str = "generation",
        use_cache: bool = True,
        is_user_content: bool = False,
        now: datetime | None = None,
        force_lane: Literal["free", "paid"] | None = None,
    ) -> list[str]:
        """Execute many independent prompts as one logical call, returning
        responses in the same order as ``prompts``.

        This is the fix for both the batch-API and the wall-clock problem a
        loop of ``generate()`` calls has:

        - **Free lane**: every cache-miss prompt is a separate HTTP round-trip
          regardless, so they are fired concurrently (bounded by
          ``FREE_LANE_MAX_CONCURRENCY``) instead of one after another. Each
          item still goes through the exact same per-item
          ``_call_transport_with_lane_handling`` RPM/RPD/503 handling
          ``generate()`` uses -- concurrency changes wall-clock time, not
          correctness. Cost-log writes and cache writes happen on the calling
          thread only, after collecting each worker's result, never inside a
          worker, so no locking is needed around them.
        - **Paid lane**: all cache-miss prompts are submitted as real
          multi-item Gemini batch jobs (``_call_batch_many``) instead of one
          job per prompt -- the previous per-``generate()``-call loop paid a
          full batch-job's queueing overhead (observed live: 1-3 minutes) for
          every single item, serially, with none of the throughput benefit
          batching exists for. Chunked by ``_chunk_indices_for_inline_batch``
          to respect Google's documented ~20MB inline-submission guidance
          (``BATCH_INLINE_MAX_BYTES``): one job for every group small enough
          to submit inline (every group this codebase's own item caps
          produce), more only if a caller ever hands this a genuinely
          oversized group.

        ``force_lane`` bypasses ``_determine_lane`` entirely and pins the
        call to the given lane instead. Deliberately narrow: the normal
        "free unless closed or restricted" policy is a global two-lane
        invariant (CLAUDE.md 9) and stays the default for every caller that
        does not pass this. It exists for one deliberate opt-in case --
        ``scripts/step5_pilot_generation.py --batch``, which asks for a real
        stock run through the paid lane's actual Batch API on purpose, not a
        fast, cheap dev-iteration pilot. A forced ``"paid"`` lane with no
        ``paid_api_key`` configured still raises ``MissingApiKeyError`` from
        ``_get_sdk_client`` exactly as an auto-routed paid call would; this
        parameter changes lane selection, not the paid lane's own key
        requirement.
        """
        if not prompts:
            return []
        ref_time = now or self._clock()

        current_spend = self.get_month_to_date_spend(ref_time)
        if current_spend >= self.spend_ceiling_usd:
            raise BudgetExceeded(
                f"Monthly spend ceiling of ${self.spend_ceiling_usd:.2f} reached "
                f"(current spend: ${current_spend:.4f})."
            )

        results: list[str | None] = [None] * len(prompts)
        pending_indices: list[int] = []
        for i, prompt in enumerate(prompts):
            if use_cache:
                cached = self.cache.get(model=model, prompt=prompt)
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
                    results[i] = cached
                    continue
            pending_indices.append(i)

        if not pending_indices:
            return [text for text in results if text is not None]

        lane: Lane = force_lane or self._determine_lane(is_user_content, ref_time)

        if lane == "free":
            with ThreadPoolExecutor(max_workers=self.FREE_LANE_MAX_CONCURRENCY) as pool:
                future_to_index = {
                    pool.submit(
                        self._call_transport_with_lane_handling,
                        model=model,
                        prompt=prompts[i],
                        lane=lane,
                        purpose=purpose,
                        ref_time=ref_time,
                    ): i
                    for i in pending_indices
                }
                for future in as_completed(future_to_index):
                    i = future_to_index[future]
                    text, prompt_tokens, completion_tokens, used_lane = future.result()
                    cost_usd = self._estimate_cost(
                        model, prompt_tokens, completion_tokens, used_lane
                    )
                    self._log_cost(
                        CostLogRow(
                            timestamp=ref_time,
                            model=model,
                            lane=used_lane,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cost_usd=cost_usd,
                            purpose=purpose,
                        )
                    )
                    if use_cache:
                        self.cache.set(model=model, prompt=prompts[i], response=text)
                    results[i] = text
        else:  # paid
            client = self._get_sdk_client("paid")
            config = genai_types.GenerateContentConfig(
                thinking_config=self._thinking_config_for(model)
            )
            # Chunked so each real batch job stays within Google's inline
            # submission limits (BATCH_INLINE_MAX_BYTES/_COUNT) -- for every
            # group this codebase actually submits (bounded by
            # NIGHTLY_ITEM_CAP) this is exactly one chunk, i.e. one job; it
            # only splits into more than one job if a caller ever hands
            # ``generate_many`` a genuinely oversized group.
            for chunk in self._chunk_indices_for_inline_batch(pending_indices, prompts):
                chunk_prompts = [prompts[i] for i in chunk]
                batch_results = self._call_batch_many_with_retry(
                    client, model=model, prompts=chunk_prompts, config=config, purpose=purpose
                )
                for i, (text, prompt_tokens, completion_tokens) in zip(
                    chunk, batch_results, strict=True
                ):
                    cost_usd = self._estimate_cost(model, prompt_tokens, completion_tokens, "paid")
                    self._log_cost(
                        CostLogRow(
                            timestamp=ref_time,
                            model=model,
                            lane="paid",
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cost_usd=cost_usd,
                            purpose=purpose,
                        )
                    )
                    if use_cache:
                        self.cache.set(model=model, prompt=prompts[i], response=text)
                    results[i] = text

        assert all(text is not None for text in results), (
            "generate_many must fill every index before returning"
        )
        return [text for text in results if text is not None]

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
