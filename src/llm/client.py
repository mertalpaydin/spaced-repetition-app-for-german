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
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import google.genai as genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.contracts import (
    MODEL_GENERATE,
    MODEL_LIVE,
    MODEL_VERIFY,
    THINKING_FLASH_LITE,
    THINKING_VERIFY,
)
from src.llm.batch_jobs import (
    DEFAULT_BATCH_JOB_STORE,
    BatchJobStore,
    record_submission,
)
from src.llm.cache import LlmCache, cache_key_kwargs
from src.llm.config import DEFAULT_CONFIG_PATH, load_restrict_user_content_to_paid_lane

#: ``"local"`` is a fourth lane, additive on the same terms as every other
#: field on ``CostLogRow``: an on-device model reached over localhost. It bills
#: nothing and has no quota, so it never touches ``spend_ceiling_usd``, but it
#: is logged anyway because CLAUDE.md rule 4 is about VISIBILITY rather than
#: money -- a run whose verification pass happened entirely on a local model
#: must not read as a run that did no verification at all. Historical rows are
#: unaffected: no row has ever carried this value, so nothing reparses
#: differently. Flagged per CLAUDE.md rule 8 as a change to a persisted format.
Lane = Literal["free", "paid", "cache", "local"]
QuotaType = Literal["rpm", "rpd"]

#: How a call actually reached Google. This is NOT derivable from the lane:
#: the paid lane runs batch for nightly/initial generation and synchronous
#: on-demand for a ``forbid_batch=True`` pilot, and Google prices the two
#: differently (batch is half price, on-demand is not).
TransportMode = Literal["sync", "batch"]

#: What a cost-log row records for its mode. Adds ``"cache"`` to
#: ``TransportMode`` because a cache hit reached no transport at all, so
#: calling it "sync" would put a fiction in an audit record.
LoggedMode = Literal["sync", "batch", "cache", "local"]

#: How one logged attempt ended.
#:
#: ``"ok"`` is the default so every row written before this field existed
#: parses as what it was: the successful attempt, the only kind that used to
#: be logged at all.
#:
#: ``"server_error"`` and ``"quota"`` are the two ways an attempt reaches
#: Google and comes back with nothing this client can account for. They are
#: deliberately not merged, because they mean different things about money: a
#: 429 is a refusal Google does not bill for, while a 5xx can arrive after the
#: provider has already done and billed work (a batch job that processed part
#: of its group before failing is the clearest case). Only the second kind
#: hides spend; both are worth a row, because the retry count is what makes
#: the first kind visible as noise and the second as a possible gap.
#:
#: ``"adjustment"`` is not an attempt at all. It marks a row appended by
#: ``scripts/repair_cost_log.py`` to carry the difference between a day's
#: logged spend and Google's actual charge for that day, so a repaired log
#: can never be mistaken for a log in which every call was recorded.
RowOutcome = Literal["ok", "server_error", "quota", "adjustment"]

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
    raise the free tier's throughput ceiling (the owner has observed 5 RPM
    for flash models, regardless of how many requests arrive at once) -- it
    only changes whether those requests arrive evenly spaced or in a burst.
    A burst that exceeds the ceiling makes EVERY item in the burst 429 at
    roughly the same moment; if several of them then retry at roughly the
    same moment too (their backoffs overlapping), a single item can exhaust
    its bounded ``RPM_MAX_RETRIES`` and raise, which crashes the *entire* concurrent
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

    The string form leads with that distinction rather than with Google's raw
    429 text, because a caller that lets this escape prints it straight to the
    operator's terminal (``scripts/eval_verifier.py`` prints
    ``f"{type(exc).__name__}: {exc}"``) and "429 RESOURCE_EXHAUSTED" on its own
    does not say whether re-running now is worth anything. Google's own text
    is kept verbatim after it, never replaced.
    """

    #: What each kind means for the person reading the terminal, which is the
    #: only reason the distinction is worth printing: one says try again, the
    #: other says wait for tomorrow.
    _OPERATOR_GUIDANCE: dict[str, str] = {
        "rpm": (
            "per-minute request limit (RPM) refused this attempt. This is "
            "transient: re-running rides through it, and cached results are "
            "replayed for free"
        ),
        "rpd": (
            "free-tier DAILY allowance (RPD) is exhausted. Re-running does "
            "not help until the quota resets at the next Pacific midnight"
        ),
    }

    def __init__(
        self,
        quota_type: QuotaType,
        message: str = "",
        retry_delay_seconds: float | None = None,
    ) -> None:
        self.quota_type = quota_type
        self.retry_delay_seconds = retry_delay_seconds
        summary = f"Gemini {self._OPERATOR_GUIDANCE[quota_type]}."
        super().__init__(f"{summary} Underlying error: {message}" if message else summary)


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


class PaidLaneForbiddenError(RuntimeError):
    """Raised by ``_determine_lane`` when routing would pick the paid lane
    but ``forbid_paid_lane`` is set on the client.

    Historical note: this used to be described as "the real 'batch is off'
    switch (CLAUDE.md 9)". That conflated two different ideas that the
    project owner has since split apart explicitly: "no batch" (see
    ``BatchForbiddenError`` below) and "no paid lane at all" are not the
    same instruction. This error is the latter -- it forbids the paid lane
    outright, sync or batch, and remains a real, still-used switch (see
    ``src.llm.provider.default_llm_provider``, ``LiveExplainer``,
    ``ProductionGrader``, ``MinimalPairGenerator``, all of which have a
    learner waiting on the reply and must never fall through to a paid
    lane at all, batch or not). It is simply no longer what pilots use to
    keep themselves off batch -- see ``BatchForbiddenError`` for that.

    Before this existed, ``--batch`` only ever controlled *forcing* the
    paid lane; nothing ever *forbade* it, so once the free lane's daily
    quota tripped (``_close_free_lane_until_pacific_midnight``), every
    subsequent call in the same process silently fell through to the paid
    batch lane -- for a whole pilot run, unattended, with no operator ever
    having asked for batch. ``forbid_paid_lane=True`` makes that
    fall-through fail loudly here instead, with a message that says why,
    when the free lane reopens, and that ``--batch`` remains available as
    an explicit opt-in.
    """


class PaidBatchDeferred(Exception):
    """Internal signal: this prompt's free lane closed and its paid fallback
    would be a batch submission, so the caller should gather it rather than let
    it submit alone.

    Never escapes ``generate_many``, which is the only thing that raises the
    flag enabling it. It exists because the free lane dispatches one request
    per prompt, so its built-in free-to-paid fallback also fires once per
    prompt -- and when the paid lane is in batch mode, that means every
    overflowing prompt submitting its own single-prompt batch job.

    Measured on the first real scheduled tick: 13 outstanding jobs carrying one
    prompt each. CLAUDE.md section 9 says the opposite ("remaining work queues
    and ships as one batch"), and the reason it says so is in
    ``_call_batch_many``'s own docstring: one job per item pays the batch API's
    scheduling overhead once per item. A 1,225-item build would have left about
    245 jobs to poll instead of one.
    """

    def __init__(self, prompt: str) -> None:
        super().__init__("free lane closed; deferring this prompt to a shared batch job")
        self.prompt = prompt


class BatchQueuedError(RuntimeError):
    """Raised when a client built with ``detach_batch=True`` has submitted work
    to the real Batch API and deliberately did not wait for it.

    Not a failure. It is how a run says "this is queued, come back for it",
    which is the whole point of a detached submission: the caller exits, the
    scheduled collector picks the job up later, and the next run of the same
    work finds every response already in the local cache.

    Carries the job names so a caller can report exactly what is outstanding.
    ``verify_items`` treats it like the other degrade cases and records
    ``not_run`` for the affected items, which is the honest verdict: nothing
    judged them THIS run. The pilot then refuses the bank write, as it already
    does for any unjudged item.
    """

    def __init__(self, *, job_names: list[str], prompts_queued: int, message: str) -> None:
        super().__init__(message)
        self.job_names = job_names
        self.prompts_queued = prompts_queued


class BatchForbiddenError(RuntimeError):
    """Raised when a call would use the paid lane's batch mode but the
    client was built with ``forbid_batch=True``.

    This is the real "no batch" switch the project owner actually asked
    for: "when I said no batch api I meant for pilot go to paid on demand
    api, if free lane is already expired." A pilot must never queue work
    into the real Batch API, but IS allowed to spend on the paid lane
    synchronously once the free lane's daily quota is spent -- unlike
    ``forbid_paid_lane``, which forbids the paid lane entirely, sync or
    batch.

    Before this existed, ``forbid_paid_lane=True`` was pilots' only lever,
    and it was wired to mean "no batch" -- so once the free lane's quota
    was spent mid-run, ``_determine_lane`` raised ``PaidLaneForbiddenError``
    instead of continuing on the paid lane synchronously, and the model
    verification pass degraded its entire run to ``"not_run"`` rather than
    actually verifying anything (``cost_log.jsonl`` from that run has zero
    rows with ``purpose="item_verification"``). ``forbid_batch=True`` fixes
    that: the paid lane stays open for on-demand (synchronous) calls, and
    only an actual batch submission is refused, with a message saying the
    run is on-demand only and that batch remains available for
    nightly/initial generation, which does not set this flag.
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


class TokenUsage(BaseModel):
    """Every billed token field one Gemini response reports, kept apart.

    The transport used to hand back only ``(prompt_tokens,
    completion_tokens)``, which threw away three things Google bills for and
    one it uses to check them:

    - ``thoughts_tokens`` was read but immediately merged into
      ``completion_tokens``, so thinking spend was invisible in the log even
      though thinking is on for every routed model (CLAUDE.md 213).
    - ``cached_content_tokens`` is billed at a reduced rate and was not read.
    - ``tool_use_prompt_tokens`` is billed and was not read at all.
    - ``total_tokens`` is the provider's own total, which is what makes it
      possible to notice that the parts do not add up (see
      ``GeminiLlmClient._extract_response``).

    ``completion_tokens`` deliberately still means what it has always meant,
    candidates plus thoughts, so nothing downstream shifts;
    ``thoughts_tokens`` is recorded alongside it, not instead of it.
    """

    # ``protected_namespaces=()`` because ``model_version`` is the SDK's own
    # field name and Pydantic otherwise warns about the ``model_`` prefix.
    model_config = ConfigDict(frozen=True, protected_namespaces=())
    prompt_tokens: int
    completion_tokens: int
    thoughts_tokens: int = 0
    cached_content_tokens: int = 0
    tool_use_prompt_tokens: int = 0
    total_tokens: int | None = None
    model_version: str | None = None


class CostLogRow(BaseModel):
    """Immutable audit record of a single LLM *attempt* or cache hit.

    An attempt, not a call: a call that 503s three times and succeeds on the
    fourth writes four rows, not one. See ``GeminiLlmClient._log_failed_attempt``
    for why that distinction is the entire point of this change.

    Every field after ``purpose`` is additive with a default, so rows written
    before those fields existed still parse (``GeminiLlmClient._load_cost_log``
    reads this file at construction and must not start warning about every
    historical row).
    """

    # See ``TokenUsage`` for why the protected namespace is opened up.
    model_config = ConfigDict(frozen=True, protected_namespaces=())
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str
    lane: Lane
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    purpose: str = "generation"
    #: ``None`` means "written before this field existed", which is the honest
    #: value for a historical row: the log had no sync/batch distinction, and
    #: guessing one now would invent evidence. Google's bill does make the
    #: distinction (separate ``gemini 3.7 flash text`` and ``... text batch``
    #: SKUs at different rates), which is why reconciling the two needed a
    #: billing export; with this field they line up field for field.
    mode: LoggedMode | None = None
    #: Recorded alongside ``completion_tokens``, which still includes them.
    thoughts_tokens: int = 0
    cached_content_tokens: int = 0
    tool_use_prompt_tokens: int = 0
    #: The provider's own ``total_token_count``, kept verbatim so a later
    #: reconciliation can see billed tokens this code did not account for.
    total_tokens: int | None = None
    #: The model Google says it actually served, which can differ from the
    #: model requested. Google bills per model, so a silent reroute would
    #: otherwise be invisible.
    model_version: str | None = None
    #: How this attempt ended. Defaults to ``"ok"`` so every historical row,
    #: all of which recorded a success because a success was the only thing
    #: that got logged, parses as exactly what it was.
    outcome: RowOutcome = "ok"
    #: 1-based index of this attempt within its call. ``1`` for a call that
    #: succeeded first time, which is what every historical row was.
    attempt: int = 1
    #: Shared by every row belonging to one logical call, so N attempts read
    #: as one retried call rather than N unrelated calls. ``None`` on a
    #: historical row (no such grouping existed), on a cache hit (which
    #: reached no transport and can never be retried), and on a
    #: reconciliation adjustment (which is a whole day, not a call).
    #:
    #: This matters more than it looks: ``generate_many`` dispatches the free
    #: and paid-sync lanes concurrently, so attempt rows from different calls
    #: interleave in the file and an attempt index on its own would not say
    #: which failure belongs to which success. On a batch job the id is
    #: shared by the whole submitted group, because the job, not the
    #: individual prompt, is the thing that retries.
    call_id: str | None = None
    #: Which kind of 429 this attempt hit, on a row with ``outcome="quota"``.
    #: ``None`` everywhere else, and on every historical row: the log recorded
    #: only that a 429 happened, and inventing a kind for such a row now would
    #: be manufacturing evidence that was never captured.
    #:
    #: The distinction is the entire reason to record it, because it decides
    #: what the operator does next. ``"rpm"`` is per-minute noise: the pacing
    #: was briefly too fast and a re-run rides straight through it. ``"rpd"``
    #: is the free tier's daily allowance spent, and no amount of re-running
    #: helps until the Pacific-midnight reset. The client has always known
    #: which (``_classify_quota_error``, ``QuotaExceededError.quota_type``) and
    #: threw it away at logging time, leaving the owner looking at a bare
    #: ``outcome="quota"`` row unable to tell a run worth retrying now from one
    #: that has to wait for tomorrow -- which is also what decides whether a
    #: 490-call bank build is feasible on the free lane at all.
    quota_type: QuotaType | None = None


#: Where ``GeminiLlmClient`` writes its cost log unless told otherwise, named
#: here so a non-Gemini provider can append to the SAME file without owning a
#: client. See ``append_cost_row``.
DEFAULT_COST_LOG_PATH = Path(".cache/cost_log.jsonl")


def append_cost_row(row: CostLogRow, path: Path | str = DEFAULT_COST_LOG_PATH) -> None:
    """Append one audit row to the cost log.

    Exists because CLAUDE.md rule 4 is about VISIBILITY, not about money, and
    the log had a hole in exactly that: ``AzureTranslator`` logs through a
    ``GeminiLlmClient`` it is handed, so a run configured with an Azure key
    and no Gemini key translated real sentences and wrote no row at all. Azure
    F0 costs nothing, which is why the hole was easy to miss and why it is
    still a hole: an audit that silently omits a whole provider is not an
    audit. This is the one write path, shared by ``GeminiLlmClient._log_cost``
    and by any provider that has no client to write through.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row.model_dump_json() + "\n")


class TransportResult(BaseModel):
    """One successful single-prompt transport call, with the retry history
    needed to log it truthfully.

    ``lane`` and ``mode`` are here because a call can move between them
    mid-flight (RPD closes the free lane, a 5xx exhausts its retries on free
    and falls through to paid), and the pair is what prices the row.
    ``attempt`` and ``call_id`` are here because the row that finally
    succeeds is no longer the only row: its failed predecessors were logged
    on the way, and these two fields are what tie them together.
    """

    model_config = ConfigDict(frozen=True)
    text: str
    usage: TokenUsage
    lane: Lane
    mode: TransportMode
    attempt: int
    call_id: str


class BatchTransportResult(BaseModel):
    """One successful batch job, with its retry history.

    The unit here is the job, not the prompt: ``_call_batch_many_with_retry``
    resubmits the whole group, so ``attempt`` and ``call_id`` describe the
    group. Every per-prompt row written from ``results`` carries the same
    pair.
    """

    model_config = ConfigDict(frozen=True)
    results: list[tuple[str, TokenUsage]]
    attempt: int
    call_id: str


class BatchCollectionReport(BaseModel):
    """What one sweep of the pending-batch store did.

    Every field is a count of something that actually happened, because this
    runs unattended and the log line it produces is the only evidence anybody
    will see.
    """

    model_config = ConfigDict(extra="forbid")

    #: Jobs in the store when the sweep started.
    examined: int = 0
    #: Jobs that had finished and whose responses are now cached.
    collected_jobs: int = 0
    #: Individual prompts written into the cache.
    cached_responses: int = 0
    #: Jobs still queued or running at Google. Left in the store.
    still_running: int = 0
    #: Jobs that reached a terminal non-success state. Dropped from the store.
    failed: list[str] = Field(default_factory=list)
    #: Anything that went wrong without being terminal. The job is kept.
    errors: list[str] = Field(default_factory=list)
    #: Jobs still in the store when the sweep ended.
    outstanding: int = 0

    def describe(self) -> str:
        parts = [
            f"examined {self.examined}",
            f"collected {self.collected_jobs}",
            f"cached {self.cached_responses} response(s)",
            f"still running {self.still_running}",
            f"outstanding {self.outstanding}",
        ]
        if self.failed:
            parts.append(f"failed {len(self.failed)}")
        if self.errors:
            parts.append(f"errors {len(self.errors)}")
        return ", ".join(parts)


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
    # ``_estimate_cost`` applies Google's 0.5x batch discount on top of these
    # for a paid-lane BATCH submission only, since Google's batch price for
    # both models is confirmed exactly half of standard -- these must stay the
    # standard price, not the already-discounted one.
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

    #: Nothing in this client ever sleeps longer than this in one go, however
    #: long Google says to wait. ``_extract_retry_delay_seconds`` returns the
    #: provider's own figure verbatim, and a 429 can carry a ``retryDelay``
    #: measured in hours -- the seconds remaining until a daily window reopens,
    #: for instance, or an RPD that ``_classify_quota_error`` read as an RPM
    #: because the message text did not name a window.
    #:
    #: Measured on 2026-08-29: a ``--free-lane-only`` verification run made one
    #: successful call, then sat for 23 minutes with no request, no output and
    #: no error before it was killed by hand. Ten minutes is far longer than any
    #: real per-minute window (the live free tier asks for about 48 seconds) and
    #: far shorter than a person's patience with a job that looks dead.
    MAX_BACKOFF_SECONDS: float = 600.0
    # Operator-tuned against observed live rate limiting, deliberately
    # conservative. Reverted to 2 across three separate cycles, each time
    # costing the owner a live pilot run; pinned by
    # test_operator_tuned_rate_limit_constants_are_pinned in
    # tests/test_llm_client.py. Do not change without asking the project
    # owner first.
    RPM_MAX_RETRIES: int = 5

    # 5xx server overload is transient and carries no structured retry delay
    # (confirmed live: "503 UNAVAILABLE... currently experiencing high
    # demand" with a plain-text body, no RetryInfo). It is not a quota signal
    # and must never touch the RPD lane-closing path.
    #
    # Few attempts, long waits, at the owner's instruction: "instead of making
    # 40 request make it like 4 but with much longer intervals". The tree this
    # replaces had 40 attempts at a flat 15 seconds, which is ten minutes of
    # hammering a service that is already down. A real outage lasts minutes,
    # so the schedule escalates instead: half a minute, two minutes, eight
    # minutes, fifteen minutes, covering roughly 25 minutes with four
    # attempts rather than ten minutes with forty.
    #
    # These are the PAID lane's budget. The free lane has its own, much
    # shorter one below, for the reason spelled out there.
    SERVER_ERROR_BACKOFF_SCHEDULE: tuple[float, ...] = (30.0, 120.0, 480.0, 900.0)
    # Scalar fallback for an attempt index past the end of a schedule, so a
    # raised ``*_MAX_RETRIES`` can never index off the end. It is the PAID
    # schedule's last entry; the free schedule no longer ends on it, and does
    # not need to, because both schedules hold exactly one entry per retry
    # (pinned by test_operator_tuned_server_error_constants_are_pinned) so
    # this fallback is unreachable unless a retry cap is raised without its
    # schedule. If the free cap is ever raised, extend its schedule too rather
    # than letting a fail-fast lane inherit a fifteen-minute sleep from here.
    SERVER_ERROR_BACKOFF_SECONDS: float = 900.0
    SERVER_ERROR_MAX_RETRIES: int = 4

    # The FREE lane's own 5xx budget: FAIL FAST, then let the operator re-run.
    #
    # The lane still differs from the paid one, and in the same direction: a
    # paid-lane 5xx can arrive after Google has already done and billed work,
    # so each paid retry can cost real money, while an unbilled free-lane
    # retry costs only time (``_estimate_cost`` prices every free-lane row at
    # exactly 0.0). That is why the two budgets stay separate. It is NOT a
    # reason to retry for hours, and an earlier version of this comment used
    # it as one: twelve retries plateauing at fifteen minutes, 7230 seconds
    # per call, roughly eight hours for a 15-call group at concurrency 4.
    # That shape was unusable in practice -- ``scripts/eval_verifier.py``, a
    # 15-call job that should take minutes, sat for over two and a half hours
    # with no output, indistinguishable from a hang.
    #
    # WHAT THAT REASONING MISSED: the local content-addressed cache
    # (``src/llm/cache.py``, CLAUDE.md 9 "Caching") makes a dead run nearly
    # free to repeat. Every verdict that already landed is replayed from disk
    # on the next run at zero cost, logged as ``lane="cache"``; the owner's
    # own second run opened with 5 such rows before it spent a single new
    # call. Progress is therefore already durable ACROSS runs. Sleeping for
    # hours inside one process to avoid losing a call protects against a loss
    # the cache has already prevented, while holding a concurrency slot doing
    # nothing. Short retries plus repeated runs strictly dominate long
    # in-process backoff: the operator gets a live process he can watch and a
    # clear exit, instead of silence he cannot tell from a hang.
    #
    # So: four retries, five attempts, on a doubling schedule that gives a
    # genuine transient blip a couple of chances and then gets out of the way.
    # Still escalating rather than a flat short delay, because hammering a
    # service that is down is what the cut from 40 retries was right to
    # remove; just capped where a human's patience actually is.
    #
    # WORST CASE, stated here so it can be agreed to rather than discovered:
    # 15 + 30 + 60 + 120 = 225 seconds, i.e. three minutes and forty-five
    # seconds of sleeping per call before it gives up, plus the attempts' own
    # round-trip time. The schedule holds exactly one entry per retry, so no
    # call can spin for an unbounded wall clock. For a whole ``generate_many``
    # group the bound multiplies by the number of concurrency waves rather
    # than by the number of calls: at ``FREE_LANE_MAX_CONCURRENCY`` = 4, a
    # 15-call group (``scripts/eval_verifier.py``) is 4 waves, so even if
    # EVERY call exhausts its budget the group raises after 4 * 225 = 900
    # seconds, fifteen minutes. Re-running it then costs nothing for the calls
    # that did land.
    FREE_LANE_SERVER_ERROR_MAX_RETRIES: int = 4
    FREE_LANE_SERVER_ERROR_BACKOFF_SCHEDULE: tuple[float, ...] = (
        15.0,
        30.0,
        60.0,
        120.0,
    )
    # The batch path gets its own, lower cap. This is NOT the owner's
    # instruction; it is this cycle's judgement, from the asymmetry in what a
    # retry costs. A sync 503 served nothing and billed nothing, so retrying
    # it is free. A batch job runs for minutes and processes its requests one
    # at a time; when it fails partway, Google has already done and billed
    # that work, and ``_call_batch_many_with_retry`` resubmits the WHOLE job.
    # The owner's Aug 14-17 bill shows batch usage on days where his cost log
    # has zero rows, which is what that leak looks like from the outside.
    SERVER_ERROR_BATCH_MAX_RETRIES: int = 2

    # ``generate_many``'s free-lane path fires independent items concurrently
    # instead of serially -- each is still just one HTTP round-trip, so wall
    # clock time is bound by the slowest concurrent item, not the sum of all
    # of them. Throughput is still capped by ``FREE_LANE_RATE_LIMIT_PER_MINUTE``
    # below regardless of this value; it mainly controls how many requests can
    # be in flight (and therefore latency-overlapping) at once.
    # Operator-tuned against observed live rate limiting, deliberately
    # conservative. Reverted to 8 across three separate cycles, each time
    # costing the owner a live pilot run; pinned by
    # test_operator_tuned_rate_limit_constants_are_pinned in
    # tests/test_llm_client.py. Do not change without asking the project
    # owner first.
    FREE_LANE_MAX_CONCURRENCY: int = 4

    # The owner has directly observed the free tier's real ceiling for flash
    # models to be 5 RPM, not the higher figures (up to 15 RPM) some
    # documentation and earlier comments here claimed. Pacing at the
    # observed ceiling, not a documented-but-unobserved one, is what
    # actually avoids 429s in practice.
    # Operator-tuned against observed live rate limiting, deliberately
    # conservative. Reverted to 14 across three separate cycles, each time
    # costing the owner a live pilot run; pinned by
    # test_operator_tuned_rate_limit_constants_are_pinned in
    # tests/test_llm_client.py. Do not change without asking the project
    # owner first.
    FREE_LANE_RATE_LIMIT_PER_MINUTE: int = 5

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
        # Raised from 5.00 at the owner's instruction. See CLAUDE.md 9 and
        # docs/audits/stage-00-quota.md, both corrected to match.
        spend_ceiling_usd: float = 7.50,
        cost_log_path: Path | str = ".cache/cost_log.jsonl",
        cache_dir: Path | str = ".cache/llm",
        restrict_user_content_to_paid_lane: bool | None = None,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        forbid_paid_lane: bool = False,
        forbid_batch: bool = False,
        detach_batch: bool = False,
        batch_job_store_path: Path | str | None = None,
    ) -> None:
        self.free_api_key = (
            free_api_key or os.getenv("GEMINI_FREE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
        self.paid_api_key = paid_api_key or os.getenv("GEMINI_PAID_API_KEY")
        # Submit a real batch job and do not wait for it. Off by default, so
        # every existing caller keeps blocking exactly as it did. The job name
        # is recorded on disk either way (see ``_call_batch_many``): that part
        # is not optional, because a job Google has accepted is billable
        # whether or not this process lives long enough to collect it.
        self.detach_batch = detach_batch
        # Resolved here rather than as a default argument, so the module
        # constant is read at construction time. A default argument binds once
        # at import, which makes the path impossible to redirect afterwards --
        # and the unit suite has to redirect it, or every test that submits a
        # fake batch job writes into the real store beside the cache. That is
        # not hypothetical: two `purpose="unit_test"` jobs were found in the
        # real store the first time this ran.
        self.batch_job_store_path = Path(batch_job_store_path or DEFAULT_BATCH_JOB_STORE)
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
        # Genuinely forbids ``_determine_lane`` from ever returning "paid":
        # instead of silently falling through to the paid batch lane once the
        # free lane closes (RPD exhaustion) or user-content restriction
        # applies, the call raises ``PaidLaneForbiddenError``. Default
        # ``False`` preserves the existing auto-routing behaviour for every
        # caller that does not opt in; pilots opt in by default
        # (``src.generation.pilot.run_pilot``), and ``force_lane`` still
        # bypasses this check entirely, which is how ``--batch`` remains an
        # explicit, deliberate opt-in into the paid lane even from a client
        # built with ``forbid_paid_lane=True``.
        self.forbid_paid_lane = forbid_paid_lane
        # Genuinely different from ``forbid_paid_lane`` above: this leaves
        # the paid lane available but forces every call onto it to be
        # synchronous (on-demand), never the real Batch API. ``_call_transport``
        # raises ``BatchForbiddenError`` instead of ``ValueError`` for a
        # paid-lane batch call when this is set, and the two seams that
        # otherwise default a paid-lane call to batch mode
        # (``_call_transport_with_lane_handling``'s up-front lane decision and
        # its mid-call RPD fallback, plus ``generate_many``'s own paid-lane
        # branch) all resolve to sync instead. Default ``False`` preserves
        # today's "paid lane is always batch" behaviour for every caller that
        # does not opt in; pilots opt in by default
        # (``src.generation.pilot.run_pilot``), with ``--batch`` (``use_batch``)
        # remaining the explicit, deliberate opt-in back into real batch mode.
        self.forbid_batch = forbid_batch
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep

        self.free_lane_open = True
        self.free_lane_closed_until: datetime | None = None

        self.cost_records: list[CostLogRow] = []
        # See ``_log_cost``: attempt rows are written from worker threads.
        self._cost_log_lock = threading.Lock()
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
        # Locked because failed-attempt rows are now written from inside
        # ``_call_transport_with_lane_handling``, which ``generate_many``
        # runs on worker threads. Success rows are still written from the
        # calling thread only, so this lock is uncontended on the common
        # path; it exists so a burst of concurrent 503s cannot interleave two
        # half-written JSON lines in the file or race on ``cost_records``.
        with self._cost_log_lock:
            self.cost_records.append(row)
            append_cost_row(row, self.cost_log_path)

    def _build_cost_row(
        self,
        *,
        model: str,
        lane: Lane,
        mode: TransportMode,
        usage: TokenUsage,
        purpose: str,
        attempt: int,
        call_id: str,
    ) -> CostLogRow:
        """Build the audit row for one real (non-cache) call that SUCCEEDED.

        One place, so the three call sites that log a real call cannot drift
        apart on which token fields they carry, and so pricing is always given
        the same ``(lane, mode)`` pair that the row records.

        ``attempt`` is the 1-based index of the attempt that succeeded, so an
        ``attempt`` above 1 says this call was retried and that
        ``_log_failed_attempt`` has already written ``attempt - 1`` rows under
        the same ``call_id``.

        The timestamp is read from the clock HERE, when the row is built, and
        is deliberately not passed in. See ``_log_failed_attempt`` for why.
        """
        return CostLogRow(
            timestamp=self._clock(),
            model=model,
            lane=lane,
            mode=mode,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            thoughts_tokens=usage.thoughts_tokens,
            cached_content_tokens=usage.cached_content_tokens,
            tool_use_prompt_tokens=usage.tool_use_prompt_tokens,
            total_tokens=usage.total_tokens,
            model_version=usage.model_version,
            cost_usd=self._estimate_cost(
                model, usage.prompt_tokens, usage.completion_tokens, lane, mode
            ),
            purpose=purpose,
            outcome="ok",
            attempt=attempt,
            call_id=call_id,
        )

    def _log_failed_attempt(
        self,
        *,
        model: str,
        lane: Lane,
        mode: TransportMode,
        purpose: str,
        outcome: RowOutcome,
        attempt: int,
        call_id: str,
        quota_type: QuotaType | None = None,
    ) -> None:
        """Write the audit row for an attempt that reached Google and failed.

        Until this existed, only the attempt that finally succeeded wrote a
        row, so a run that retried half its calls looked in the log exactly
        like a run that retried none of them. That is what made the owner's
        August gap invisible: his bill was $5.04 against a cost log claiming
        $1.99, and roughly $1.58 of the difference is attempts that billed and
        were never recorded. Three days in his billing export show real batch
        charges against days with literally zero rows in the log, because
        ``_call_batch_many_with_retry`` resubmits a whole job and Google has
        already billed whatever the failed job processed before it died.

        **The point of this row is not a corrected total. It is that the gap
        becomes visible.** A run that reports "24 calls, 11 of them retried"
        tells you at a glance that the log and the money have parted ways.
        The same run reported "24 calls" before, and looked clean.

        Deliberately zero tokens and zero cost:

        - **Zero tokens because the exception carries no usage metadata.** We
          do not know what Google billed for this attempt, and a guess in an
          audit log is how this whole problem started. An honest gap beats a
          fabricated number.
        - **Zero cost so ``get_month_to_date_spend`` and the spend ceiling are
          unaffected.** This change makes the gap visible; it does not close
          it. Pricing a failed attempt at an invented figure would move the
          ceiling on the strength of a number nobody measured.

        Note that the two outcomes differ in what they imply about money. A
        ``"quota"`` row is a 429, which Google refuses and does not bill; it
        is logged for retry legibility, not because it hides spend. A
        ``"server_error"`` row is the one that can hide spend.

        ``quota_type`` says WHICH 429, and callers must pass it on every
        ``outcome="quota"`` row. It is not about money either; it is about
        what the operator should do with the run. RPM means the pacing was
        briefly too fast and a re-run rides through it; RPD means the free
        tier's daily allowance is gone until the Pacific-midnight reset and
        re-running changes nothing. The client already classified the error
        to decide its own retry behaviour (``_classify_quota_error``), so
        recording it costs one field and closes a gap the log had no other
        way to answer. It stays ``None`` on every non-quota row rather than
        carrying a placeholder, for the same reason ``mode`` is ``None`` on a
        historical row: an empty cell is honest, a guess is not.

        **The timestamp is read from ``self._clock()`` here, at the moment the
        row is written, and is deliberately not a parameter.** It used to be
        passed in as the caller's ``ref_time``, which is computed once at the
        top of ``generate``/``generate_many`` and means "when this call
        started". Every attempt of a retried call therefore shared one
        timestamp, identical to the microsecond, even though
        ``SERVER_ERROR_BACKOFF_SCHEDULE`` puts at least ten minutes between
        the first attempt and the fourth. Two things broke:
        ``scripts/reconcile_cost_log.py`` groups by day, so a call that starts
        at 23:55 and retries past midnight filed those attempts on the wrong
        day and manufactured (or masked) a discrepancy against Google's
        per-day billing export; and the spacing between attempts, which is
        half of what attempt logging was added to make visible, was destroyed
        while the attempt COUNT survived. ``ref_time`` is still the right
        value for everything else it feeds (the month-to-date spend gate, the
        lane decision, the Pacific-midnight reset arithmetic) -- those all
        genuinely mean "at the start of the call". A log row does not.
        """
        self._log_cost(
            CostLogRow(
                timestamp=self._clock(),
                model=model,
                lane=lane,
                mode=mode,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                purpose=purpose,
                outcome=outcome,
                attempt=attempt,
                call_id=call_id,
                quota_type=quota_type,
            )
        )

    #: Google's batch discount: a Batch API submission is billed at half the
    #: standard rate for the same model. It applies to the transport MODE, not
    #: to the lane, which is why ``_estimate_cost`` needs ``mode``.
    BATCH_DISCOUNT_MULTIPLIER: float = 0.5

    # A classmethod, not an instance method: ``scripts/repair_cost_log.py``
    # has to reprice historical rows that were written with no ``mode`` at
    # all, and it must use THIS implementation rather than a second copy of
    # the table. Two pricing implementations is precisely how the 0.5 batch
    # discount came to be applied to calls that never went through batch.
    # Instance calls (``self._estimate_cost(...)``, and the existing tests'
    # ``client._estimate_cost(...)``) are unchanged.
    @classmethod
    def _estimate_cost(
        cls,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        lane: Lane,
        mode: LoggedMode,
    ) -> float:
        """Price one call at what Google actually charges for it.

        The discount depends on the mode, not the lane. This used to halve
        EVERY call: ``lane`` was taken as a parameter and never read, and the
        0.5 multiplier was applied unconditionally with a comment claiming it
        applied to "paid lane batch requests". It did not. Pilots run
        ``forbid_batch=True``, so their paid-lane calls are synchronous
        on-demand at FULL price, and every one of them was logged at half.
        Measured against the owner's August billing export, that alone
        accounts for roughly $1.90 of a $5.04 bill logged as $1.99.
        """
        if lane == "cache" or lane == "free":
            # The free lane is an unbilled Google Cloud project: it genuinely
            # costs nothing. Cost only accrues once work moves to the paid lane.
            return 0.0
        # Fallback for a model string absent from the table: the cheapest
        # known model's price, not the pre-2026-08-14-correction stale
        # default -- an under-estimate here would silently weaken the spend
        # ceiling exactly like the bug this table's own values just fixed.
        in_p, out_p = cls.PRICING_PER_MILLION.get(model, (0.30, 2.50))
        cost = (prompt_tokens / 1_000_000) * in_p + (completion_tokens / 1_000_000) * out_p
        if mode == "batch":
            # Half price, and only for a real Batch API submission on the paid
            # lane. A paid-lane synchronous call is on-demand pricing.
            cost *= cls.BATCH_DISCOUNT_MULTIPLIER
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

    def _paid_lane_forbidden_message(self, ref_time: datetime) -> str:
        """Build the actionable refusal message for ``PaidLaneForbiddenError``.

        Distinguishes the two reasons ``_determine_lane`` would otherwise
        pick the paid lane, since the fix differs: a closed free lane just
        needs to wait (or opt into ``--batch``); the user-content privacy
        restriction needs a config or call-site change, not a wait.

        The closed-lane branch is the message the operator actually sees when
        a ``--free-lane-only`` run ends on a quota refusal, because the free
        lane is closed by exactly one thing: an RPD 429
        (``_close_free_lane_until_pacific_midnight`` is called on no other
        path). It therefore names RPD outright, and states the reopen instant
        in the operator's OWN timezone first, with UTC after it. The reopen
        instant is stored in UTC and used to be printed that way, which is
        correct and unreadable: "reopens at 07:00Z" is not an answer to "when
        can I run this again", and answering that is the entire point of
        telling him which 429 he hit.
        """
        if not self.free_lane_open:
            reopen_at = self.free_lane_closed_until
            if reopen_at is None:
                reopen_str = "the next Pacific midnight"
            else:
                # ``astimezone()`` with no argument is the machine's local
                # zone, which for an operator-facing string is the right
                # default: he reads the terminal where the job runs.
                reopen_str = (
                    f"{reopen_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
                    f"local ({reopen_at.isoformat()})"
                )
            return (
                "The free lane is closed: the free-tier DAILY allowance (RPD) is "
                "exhausted, and the paid batch lane is forbidden in this run. "
                "Re-running does not help until the quota resets. The free lane "
                f"reopens at {reopen_str}, the next Pacific midnight. Pass --batch "
                "if you actually want this run to use the paid lane."
            )
        return (
            "This call carries user content and privacy.restrict_user_content_to_paid_lane "
            "routes user content to the paid lane, but the paid lane is forbidden in this "
            "run. Pass --batch if you actually want this run to use the paid lane."
        )

    def _determine_lane(self, is_user_content: bool, ref_time: datetime) -> Lane:
        self._refresh_free_lane_state(ref_time)
        if self.restrict_user_content_to_paid_lane and is_user_content:
            intended: Lane = "paid"
        elif not self.free_lane_open:
            intended = "paid"
        else:
            intended = "free"
        if intended == "paid" and self.forbid_paid_lane:
            raise PaidLaneForbiddenError(self._paid_lane_forbidden_message(ref_time))
        return intended

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

    def _thinking_config_for(self, model: str, purpose: str) -> genai_types.ThinkingConfig | None:
        """Thinking is keyed on ``model`` alone, not on ``purpose``: the
        ``gemini-3.7-flash`` verify workload runs at ``THINKING_VERIFY``
        ("medium"), and every call on ``gemini-3.5-flash-lite`` -- i.e. every
        call with ``model == MODEL_GENERATE`` or, equivalently,
        ``model == MODEL_LIVE``, since the two are the same string -- runs at
        ``THINKING_FLASH_LITE`` ("low").

        CLAUDE.md 213: thinking tokens bill as output and can multiply the
        largest cost line severalfold, which is why a thinking level is a
        named config value here rather than left to each caller. It is not,
        by itself, a reason to gate thinking off for any particular purpose;
        that judgement is the project owner's to make, and the owner's
        instruction ("set thinking level to low for all gemini 3.5
        flash-lite actions") is to apply it uniformly across the model.

        History: thinking was first turned on for this model line for one
        purpose only, sentence generation, after an accepted carrier ("Auf
        dem Weg kauft ich ... ein.") turned out to have an A1 subject-verb
        agreement error -- German V2 inversion after a fronted adverbial puts
        the finite verb before the subject, and an autoregressive model with
        no planning step can already have committed to the (statistically
        far more common) third-person verb form before it writes a
        first-person subject that no longer agrees with it. A thinking
        budget gives the model a chance to plan the sentence's subject before
        committing to the verb's agreement. That fix was deliberately gated
        on ``purpose == PURPOSE_SENTENCE_GENERATION`` rather than on model id
        alone, specifically to AVOID turning thinking on for explanations,
        production grading, minimal-pair generation, and the weekly report
        narrative -- all of which share this model id via ``MODEL_LIVE``.

        The project owner has since asked for the opposite: thinking on for
        every action on this model, not only sentence generation. The
        purpose gate is therefore removed here. This reverses CLAUDE.md
        section 9's model-routing table and ``docs/audits/stage-00-quota.md``'s
        routing matrix, both of which still list explanations, production
        grading, the weekly report, item generation, and the topic-leak
        check as thinking "off" for this model -- flagged here rather than
        silently left contradicting this code (CLAUDE.md rule 8); those
        documents need an explicit update pass to match.

        The level is ``THINKING_FLASH_LITE`` ("low"), not "minimal": the
        project owner confirmed "minimal" is this Flash-Lite line's own
        default thinking level, so setting it explicitly to "minimal" would
        be a no-op that buys no planning step over leaving thinking unset.
        "low" is the smallest level that is actually a step up from the
        model's default.

        ``purpose`` remains a parameter (rather than being dropped) because
        every call site already threads it through for cost-log attribution,
        and dropping it here only to have every caller keep passing it would
        be needless churn for no behavioural gain.

        Historical note, corrected: this docstring used to claim that
        ``gemini-3.5-flash-lite`` "has no thinking capability at all" and that
        sending any ``thinking_config`` to it was "an INVALID_ARGUMENT
        rejection from the API, confirmed against the live endpoint". That
        claim is now known to be stale: Google's current documentation states
        the Flash-Lite line supports thinking levels ``minimal``, ``low``,
        ``medium``, and ``high`` (``minimal`` is the documented default for
        this line). A single live probe call (gemini-3.5-flash-lite,
        thinking_config with thinking_level=LOW) was attempted to re-confirm
        this directly against the live endpoint, per the one-call budget for
        that earlier change, but the sandboxed environment's egress proxy
        refused the connection to generativelanguage.googleapis.com outright
        (403, an organisational policy denial reported by the proxy itself,
        not a response from Gemini) -- so the call never reached Google's API
        at all, and this specific claim could not be re-verified live in that
        environment. The change is made on the strength of the project
        owner's own reading of Google's current documentation, cited above,
        not on a live confirmation; whoever next has real network access to
        the Gemini API should make that one call and update this note with
        the actual result before this ships to production traffic.
        """
        if model == MODEL_VERIFY:
            return genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel(THINKING_VERIFY)
            )
        if model == MODEL_GENERATE:
            return genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel(THINKING_FLASH_LITE)
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

    def _bounded_backoff(self, seconds: float) -> float:
        """``seconds``, clamped to ``MAX_BACKOFF_SECONDS``.

        Every sleep whose duration comes from the provider rather than from
        this file goes through here. See ``MAX_BACKOFF_SECONDS`` for the run
        that made it necessary.
        """
        return min(seconds, self.MAX_BACKOFF_SECONDS)

    def _can_reach_paid(self, lane: Lane) -> bool:
        """Whether this call could still move to the paid lane if it gave up on
        the free one.

        False means waiting is the only option left, which is exactly when
        waiting a long time stops being a retry and becomes a hang: there is no
        second lane that a longer wait is buying access to.
        """
        return lane == "free" and not self.forbid_paid_lane and bool(self.paid_api_key)

    def _server_error_max_retries(self, lane: Lane) -> int:
        """How many 5xx retries ``lane`` gets.

        Lane-dependent because the cost of a retry is: a paid-lane 5xx can
        arrive after Google has already done and billed work, a free-lane one
        cannot bill anything at all. The free lane's budget is nonetheless the
        SHORTER of the two, because the local cache makes a dead free-lane run
        cheap to re-run and a long in-process sleep buys nothing. See
        ``FREE_LANE_SERVER_ERROR_MAX_RETRIES`` for the full argument and the
        worst-case wall clock it implies.

        ``"cache"`` never reaches the transport, so anything that is not the
        free lane takes the paid lane's conservative budget.
        """
        if lane == "free":
            return self.FREE_LANE_SERVER_ERROR_MAX_RETRIES
        return self.SERVER_ERROR_MAX_RETRIES

    def _server_error_backoff_seconds(self, attempt_index: int, *, lane: Lane) -> float:
        """Backoff for the ``attempt_index``-th (0-based) 5xx retry on ``lane``.

        Reads that lane's schedule in order and falls back to the scalar
        ``SERVER_ERROR_BACKOFF_SECONDS`` past the end of it, so raising a retry
        cap can never index off a schedule. ``lane`` is required rather than
        defaulted: the two lanes have different budgets now, and a caller that
        forgets which one it is on is a bug worth failing on.
        """
        schedule = (
            self.FREE_LANE_SERVER_ERROR_BACKOFF_SCHEDULE
            if lane == "free"
            else self.SERVER_ERROR_BACKOFF_SCHEDULE
        )
        if 0 <= attempt_index < len(schedule):
            return schedule[attempt_index]
        return self.SERVER_ERROR_BACKOFF_SECONDS

    def _warn_on_token_checksum_mismatch(
        self,
        *,
        usage: TokenUsage,
        accounted_tokens: int,
        purpose: str,
    ) -> None:
        """Compare the provider's own total against the parts we read.

        When they disagree, Gemini is reporting billed tokens this code does
        not know about, which is exactly the failure mode that hid roughly
        $1.58 of the owner's August bill: usage that was charged and never
        landed in the cost log.

        Deliberately a warning and never an exception. This runs inside a
        six-week unattended job; crashing it to report a bookkeeping
        discrepancy would cost far more than the discrepancy. The provider's
        own total is written to the cost-log row as well, so a later
        reconciliation can see the gap even if nobody read the warning.

        A response with no ``total_token_count`` is skipped rather than
        treated as zero: absent is not the same as zero, and assuming zero
        would make every such response look like a mismatch.
        """
        if usage.total_tokens is None:
            return
        if usage.total_tokens == accounted_tokens:
            return
        warnings.warn(
            "Gemini usage_metadata does not add up: the provider reports "
            f"total_token_count={usage.total_tokens} but the fields this client "
            f"reads sum to {accounted_tokens} "
            f"(prompt={usage.prompt_tokens}, candidates+thoughts={usage.completion_tokens}, "
            f"tool_use_prompt={usage.tool_use_prompt_tokens}; "
            f"difference={usage.total_tokens - accounted_tokens}). "
            f"Some billed tokens are unaccounted for on purpose={purpose!r}; the "
            "provider's own total is recorded in the cost log for reconciliation.",
            stacklevel=2,
        )

    def _extract_response(
        self, response: genai_types.GenerateContentResponse, *, prompt: str, purpose: str
    ) -> tuple[str, TokenUsage]:
        """Pull the response text and every billed token count off ``usage_metadata``.

        ``completion_tokens`` includes ``thoughts_token_count``: thinking
        tokens are billed as output (CLAUDE.md 213), so leaving them out of
        the cost log would under-report spend on any call where thinking is
        enabled. ``thoughts_tokens`` is recorded separately as well, because
        merged into the output total it is invisible, and thinking is the one
        output component this project can turn off.

        ``cached_content_token_count`` is a subset of ``prompt_token_count``
        (Google counts the cached prefix as part of the effective prompt), so
        it is recorded but deliberately NOT added again to the checksum below.
        ``tool_use_prompt_token_count`` is separate and is added.
        """
        text = response.text or ""
        usage_metadata = response.usage_metadata
        # Present on the SDK's response object (verified against the installed
        # google-genai ``GenerateContentResponse``), but read defensively: an
        # SDK that drops it must not take the whole call down.
        model_version = getattr(response, "model_version", None)
        if (
            usage_metadata is not None
            and usage_metadata.prompt_token_count is not None
            and usage_metadata.candidates_token_count is not None
        ):
            thoughts_tokens = usage_metadata.thoughts_token_count or 0
            tool_use_prompt_tokens = usage_metadata.tool_use_prompt_token_count or 0
            usage = TokenUsage(
                prompt_tokens=usage_metadata.prompt_token_count,
                completion_tokens=usage_metadata.candidates_token_count + thoughts_tokens,
                thoughts_tokens=thoughts_tokens,
                cached_content_tokens=usage_metadata.cached_content_token_count or 0,
                tool_use_prompt_tokens=tool_use_prompt_tokens,
                total_tokens=usage_metadata.total_token_count,
                model_version=model_version,
            )
            self._warn_on_token_checksum_mismatch(
                usage=usage,
                accounted_tokens=(
                    usage.prompt_tokens + usage.completion_tokens + usage.tool_use_prompt_tokens
                ),
                purpose=purpose,
            )
            return text, usage

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
        return text, TokenUsage(
            prompt_tokens=len(prompt.split()) * 2,
            completion_tokens=len(text.split()) * 2,
            model_version=model_version,
        )

    def _call_sync(
        self,
        client: genai.Client,
        *,
        model: str,
        prompt: str,
        config: genai_types.GenerateContentConfig,
        purpose: str,
    ) -> tuple[str, TokenUsage]:
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
        return self._extract_response(response, prompt=prompt, purpose=purpose)

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
        cache_namespace: str | None = None,
    ) -> list[tuple[str, TokenUsage]]:
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

        # Written down BEFORE anything else can fail. From the moment Google
        # accepts a job it is billable, so the window between "submitted" and
        # "recorded" is the window in which work can be paid for and lost --
        # which is exactly what used to happen to every job whose process was
        # killed mid-poll.
        if job.name is not None:
            record_submission(
                job_name=job.name,
                model=model,
                purpose=purpose,
                prompts=prompts,
                cache_namespace=cache_namespace,
                path=self.batch_job_store_path,
            )

        if self.detach_batch:
            # Submit and walk away. The scheduled collector picks the results
            # up later (``collect_pending_batches``) and writes them into the
            # cache, so the next run of this same work finds them for free.
            raise BatchQueuedError(
                job_names=[job.name] if job.name is not None else [],
                prompts_queued=len(prompts),
                message=(
                    f"Queued {len(prompts)} prompt(s) as batch job {job.name!r} "
                    f"(model={model!r}, purpose={purpose!r}) and did not wait. "
                    f"Collect with `uv run python -m scripts.collect_batch_jobs`."
                ),
            )

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

        results: list[tuple[str, TokenUsage]] = []
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
            results.append(self._extract_response(inlined.response, prompt=prompt, purpose=purpose))
        return results

    def _call_batch_many_with_retry(
        self,
        client: genai.Client,
        *,
        model: str,
        prompts: list[str],
        config: genai_types.GenerateContentConfig,
        purpose: str,
        cache_namespace: str | None = None,
    ) -> BatchTransportResult:
        """Transient-retry wrapper around ``_call_batch_many``, scoped to the
        paid lane's group submission: no RPD lane-switch (there is nowhere
        further to move from the paid lane), just the same bounded
        backoff-and-retry ``_call_transport_with_lane_handling`` gives a
        single free-lane call, applied to the whole group at once.

        Server errors get ``SERVER_ERROR_BATCH_MAX_RETRIES``, which is lower
        than the sync path's cap, because a batch retry is not free: the job
        that just failed had already run for minutes and Google had already
        billed the requests it processed before it failed, and this resubmits
        the whole group. See that constant for the full reasoning.

        That is also why every failed attempt here writes a row (see
        ``_log_failed_attempt``). **One row per failed JOB, not per prompt**:
        the job is what was submitted and what is resubmitted, and a job that
        died partway through does not tell us how many of its prompts it got
        to, so splitting the failure across the group would be inventing an
        attribution nobody measured. The owner's Aug 14-17 billing lines are
        exactly this case -- real batch charges on days whose cost log has
        zero rows -- and after this change those days would have had a row
        each saying "a batch job was attempted here and failed", which is the
        signal that was missing.
        """
        call_id = uuid4().hex
        attempt = 0
        rpm_attempts = 0
        server_attempts = 0
        while True:
            attempt += 1
            try:
                results = self._call_batch_many(
                    client,
                    model=model,
                    prompts=prompts,
                    config=config,
                    purpose=purpose,
                    cache_namespace=cache_namespace,
                )
                return BatchTransportResult(results=results, attempt=attempt, call_id=call_id)
            except QuotaExceededError as exc:
                self._log_failed_attempt(
                    model=model,
                    lane="paid",
                    mode="batch",
                    purpose=purpose,
                    outcome="quota",
                    attempt=attempt,
                    call_id=call_id,
                    quota_type=exc.quota_type,
                )
                if rpm_attempts >= self.RPM_MAX_RETRIES:
                    raise
                rpm_attempts += 1
                self._sleep(
                    self._bounded_backoff(exc.retry_delay_seconds or self.RPM_BACKOFF_SECONDS)
                )
                continue
            except ServerUnavailableError:
                self._log_failed_attempt(
                    model=model,
                    lane="paid",
                    mode="batch",
                    purpose=purpose,
                    outcome="server_error",
                    attempt=attempt,
                    call_id=call_id,
                )
                if server_attempts >= self.SERVER_ERROR_BATCH_MAX_RETRIES:
                    raise
                # Batch is paid-lane-only, so this is the paid schedule by
                # construction, not by default.
                self._sleep(self._server_error_backoff_seconds(server_attempts, lane="paid"))
                server_attempts += 1
                continue
            except ModelRejectedError:
                # A 4xx rejection: Google refused the submission outright, so
                # no job ran and nothing was billed. Deliberately no row --
                # this is the one failure here that is not billed usage, and
                # inventing an attempt record for it would be noise.
                raise
            except RuntimeError:
                # The job WAS accepted, ran at Google, and came back unusable:
                # JOB_STATE_FAILED, a per-item error, a short response set, or
                # a poll timeout (all of which ``_call_batch_many`` raises as
                # plain RuntimeError). Google has already billed whatever the
                # job processed before it died, and this path is not retried
                # at all -- it propagates straight out. Without this row it is
                # exactly the silent, billed, zero-row day the owner's
                # Aug 14-17 export shows and his cost log does not.
                self._log_failed_attempt(
                    model=model,
                    lane="paid",
                    mode="batch",
                    purpose=purpose,
                    outcome="server_error",
                    attempt=attempt,
                    call_id=call_id,
                )
                raise

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
    ) -> tuple[str, TokenUsage]:
        """Single-prompt case of ``_call_batch_many``, kept for the free
        lane's per-item fallback path (e.g. an individual call that trips RPD
        mid-flight) where grouping isn't possible."""
        return self._call_batch_many(
            client, model=model, prompts=[prompt], config=config, purpose=purpose
        )[0]

    def _mode_for_lane(self, lane: Lane) -> TransportMode:
        """The one place that decides which transport mode a lane resolves
        to, shared by ``_call_transport_with_lane_handling``'s up-front
        decision, its mid-call RPD fallback, and ``generate_many``'s own
        paid-lane branch, so the three can never quietly diverge.

        The free lane is always sync. The paid lane is batch by default,
        except when ``self.forbid_batch`` is set, in which case it is sync
        too -- the owner's "no batch api ... go to paid on demand api"
        instruction.
        """
        if lane == "paid" and not self.forbid_batch:
            return "batch"
        return "sync"

    def _call_transport(
        self,
        *,
        model: str,
        prompt: str,
        lane: Lane,
        mode: TransportMode,
        purpose: str,
    ) -> tuple[str, TokenUsage]:
        """Execute the generation call and return ``(response_text, usage)``.

        This is the single seam through which every real ``google.genai`` call
        is made: sync ``models.generate_content`` for the free lane, batch
        ``batches.create``/``batches.get`` for the paid lane. It is also the
        seam tests patch to simulate 429s and other transport behaviour
        without touching the network (see ``QuotaExceededError``).
        """
        # CLAUDE.md 9: the free lane is always synchronous, the paid lane is
        # batch by default. A call that violates either is a defect in the
        # caller, not something to silently coerce -- EXCEPT a paid-lane sync
        # call is legitimate, not a defect, when the client was built with
        # ``forbid_batch=True`` (the owner's "no batch api ... go to paid on
        # demand api" instruction): that is exactly the mode a pilot is meant
        # to use once the free lane's daily quota is spent.
        if lane == "paid" and mode == "batch" and self.forbid_batch:
            raise BatchForbiddenError(
                f"Paid-lane batch call for purpose={purpose!r} requested, but this "
                "client was built with forbid_batch=True: this run is on-demand "
                "(synchronous) only. Batch remains available for nightly/initial "
                "generation, which does not set forbid_batch."
            )
        if lane == "paid" and mode != "batch" and not self.forbid_batch:
            raise ValueError(
                f"Paid-lane call for purpose={purpose!r} requested mode={mode!r}; "
                "the paid lane is batch-only unless the client was built with "
                "forbid_batch=True (a non-batch paid-lane call is otherwise a defect)."
            )
        if lane == "free" and mode != "sync":
            raise ValueError(
                f"Free-lane call for purpose={purpose!r} requested mode={mode!r}; "
                "the free lane is always synchronous."
            )

        client = self._get_sdk_client(lane)
        config = genai_types.GenerateContentConfig(
            thinking_config=self._thinking_config_for(model, purpose)
        )

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
        defer_paid_batch: bool = False,
    ) -> TransportResult:
        """Call the transport, handling RPM/RPD 429s and 5xx server overload
        per the two-lane rules.

        ``defer_paid_batch`` changes ONE thing: instead of this prompt moving
        itself to the paid batch lane when the free lane closes under it, it
        raises ``PaidBatchDeferred`` so the caller can gather every such prompt
        and submit them as one job. See that exception for why per-prompt
        failover is the wrong shape for a batch submission.

        **Every attempt that reaches Google writes a cost-log row**, not only
        the one that succeeds: each ``except`` branch below calls
        ``_log_failed_attempt`` before it decides whether to retry, so a call
        retried three times leaves four rows sharing one ``call_id``. See
        ``_log_failed_attempt`` for why those rows carry zero tokens and zero
        cost, and why a visible gap is the whole deliverable here.

        Returns the mode the call actually used along with the lane, because
        the two together are what price it: the paid lane is batch for
        nightly generation and synchronous for a ``forbid_batch=True`` pilot,
        and Google charges half for the first and full price for the second.
        The mode was already computed here and thrown away, which is how
        every on-demand paid call came to be logged at the batch rate.

        RPM exhaustion: back off and retry on the same (free) lane.
        RPD exhaustion: close the free lane until the next Pacific midnight and
        move the work to the paid lane -- unless ``self.forbid_paid_lane`` is
        set, in which case this mid-call fallback is exactly the same kind of
        silent free-to-paid routing decision ``_determine_lane`` makes for
        every later call, so it must raise ``PaidLaneForbiddenError`` here
        too rather than let the very first call that trips RPD slip through
        onto paid before ``forbid_paid_lane`` ever gets a chance to apply.
        When the paid lane IS permitted (``forbid_paid_lane`` is not set),
        ``self.forbid_batch`` decides which mode that paid-lane fallback
        uses: batch by default, sync when the client was built for
        on-demand-only paid spend -- the same choice the up-front lane
        decision below makes, kept as one seam (``_mode_for_lane``) so the
        two can never diverge.
        5xx server overload: not a quota signal at all -- back off and retry
        on the same lane, exactly like RPM, but on its own escalating
        schedule and its own bounded retry count, so it can never masquerade
        as quota exhaustion or trigger the RPD lane-closing path. That
        schedule and count are per lane (``_server_error_max_retries``,
        ``_server_error_backoff_seconds``): the free lane fails fast, because
        the local cache makes a dead run cheap to repeat and a long sleep only
        holds a concurrency slot, while the paid lane's budget stays exactly
        where the owner set it. When those
        retries are exhausted on the free lane, the call moves to the paid
        lane rather than raising: that is the owner's own free-to-paid
        fallback, applied after a pilot died on a 503 that outlasted the
        retries. It fires only once (the lane is no longer "free" afterwards),
        only when the paid lane is permitted, and only when a paid key is
        actually configured, so a run with no paid project still surfaces the
        real ``ServerUnavailableError`` rather than a confusing
        ``MissingApiKeyError``.
        """
        mode: TransportMode = self._mode_for_lane(lane)
        call_id = uuid4().hex
        attempt = 0
        rpm_attempts = 0
        server_attempts = 0
        while True:
            attempt += 1
            try:
                text, usage = self._call_transport(
                    model=model, prompt=prompt, lane=lane, mode=mode, purpose=purpose
                )
                return TransportResult(
                    text=text,
                    usage=usage,
                    lane=lane,
                    mode=mode,
                    attempt=attempt,
                    call_id=call_id,
                )
            except QuotaExceededError as exc:
                self._log_failed_attempt(
                    model=model,
                    lane=lane,
                    mode=mode,
                    purpose=purpose,
                    outcome="quota",
                    attempt=attempt,
                    call_id=call_id,
                    quota_type=exc.quota_type,
                )
                if exc.quota_type == "rpm":
                    if rpm_attempts >= self.RPM_MAX_RETRIES:
                        raise
                    requested = exc.retry_delay_seconds or self.RPM_BACKOFF_SECONDS
                    if requested > self.MAX_BACKOFF_SECONDS and not self._can_reach_paid(lane):
                        # Google is asking us to wait longer than this client is
                        # willing to, and there is no paid lane to move the work
                        # to. Sleeping here is indistinguishable from hanging:
                        # measured 2026-08-29, a --free-lane-only run sat for 23
                        # minutes with no call and no output before it was
                        # killed. Stop instead, so the caller gets a report and
                        # an exit code and can come back when the window has
                        # reopened.
                        raise
                    rpm_attempts += 1
                    self._sleep(self._bounded_backoff(requested))
                    continue
                # RPD: close the free lane, then either move the work to paid
                # or, if paid is forbidden, fail loudly instead.
                self._close_free_lane_until_pacific_midnight(ref_time)
                if lane == "free" and self.forbid_paid_lane:
                    raise PaidLaneForbiddenError(
                        self._paid_lane_forbidden_message(ref_time)
                    ) from exc
                if defer_paid_batch and self._mode_for_lane("paid") == "batch":
                    raise PaidBatchDeferred(prompt) from exc
                lane = "paid"
                mode = self._mode_for_lane(lane)
            except ServerUnavailableError:
                self._log_failed_attempt(
                    model=model,
                    lane=lane,
                    mode=mode,
                    purpose=purpose,
                    outcome="server_error",
                    attempt=attempt,
                    call_id=call_id,
                )
                if server_attempts >= self._server_error_max_retries(lane):
                    if lane == "free" and not self.forbid_paid_lane and self.paid_api_key:
                        # The owner's free-to-paid fallback: Google's free
                        # project is down for this call, the paid project is a
                        # different project, so try it rather than losing the
                        # work. Retries restart on the new lane, and on the
                        # new lane's own budget -- the paid one, which is
                        # deliberately the longer of the two: a paid retry can
                        # cost money, so it is worth waiting out rather than
                        # repeating, whereas a free-lane run is cheap to
                        # re-run from the cache and so fails fast instead.
                        if defer_paid_batch and self._mode_for_lane("paid") == "batch":
                            raise PaidBatchDeferred(prompt) from None
                        lane = "paid"
                        mode = self._mode_for_lane(lane)
                        server_attempts = 0
                        continue
                    raise
                self._sleep(self._server_error_backoff_seconds(server_attempts, lane=lane))
                server_attempts += 1
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
                        # Read at write time, like every other row: a cache
                        # hit happens now, not at whatever ``ref_time`` the
                        # caller handed in.
                        timestamp=self._clock(),
                        model=model,
                        lane="cache",
                        mode="cache",
                        prompt_tokens=len(prompt.split()),
                        completion_tokens=len(cached.split()),
                        cost_usd=0.0,
                        purpose=purpose,
                    )
                )
                return cached

        # 3. Determine lane (free unless restricted-and-user-content, or free lane closed)
        lane = self._determine_lane(is_user_content, ref_time)

        # 4. Call transport, handling 429s per the two-lane rules. Any failed
        #    attempt on the way has already written its own row from inside
        #    there; this only logs the one that succeeded.
        result = self._call_transport_with_lane_handling(
            model=model, prompt=prompt, lane=lane, purpose=purpose, ref_time=ref_time
        )

        # 5. Persist to Cost Log & Cache
        self._log_cost(
            self._build_cost_row(
                model=model,
                lane=result.lane,
                mode=result.mode,
                usage=result.usage,
                purpose=purpose,
                attempt=result.attempt,
                call_id=result.call_id,
            )
        )

        if use_cache:
            self.cache.set(model=model, prompt=prompt, response=result.text, **kwargs)

        return result.text

    def collect_pending_batches(self, *, only_finished: bool = True) -> "BatchCollectionReport":
        """Poll every outstanding batch job once and cache what has landed.

        This is the other half of ``detach_batch``: a submission recorded the
        job and walked away, and something has to come back for it. Designed to
        be run repeatedly by a scheduled task, so it takes one look at each job
        and returns rather than waiting on anything.

        **Responses are written into the local cache**, under the same
        ``(model, prompt)`` keys a synchronous call would have used. That is
        what makes collection useful rather than merely tidy: the next run of
        the same work finds every prompt already answered and spends nothing.

        **A job is removed from the store only when it is terminal.** Still
        running means leave it and look again next time. Succeeded means cache
        the responses and drop it. Failed, cancelled or expired means drop it
        too, because re-polling something Google has finished with forever is
        just a slow way of never finishing.

        **Interrupting this is safe.** Responses are cached before the job is
        removed from the store, so a crash between the two leaves a job that
        gets collected again next time, finds every prompt already cached, and
        costs nothing. The reverse order would silently lose the results.
        """
        store = BatchJobStore.load(self.batch_job_store_path)
        report = BatchCollectionReport(examined=len(store.jobs))
        if not store.jobs:
            return report

        client = self._get_sdk_client("paid")
        terminal_failures = {
            genai_types.JobState.JOB_STATE_FAILED,
            genai_types.JobState.JOB_STATE_CANCELLED,
            genai_types.JobState.JOB_STATE_EXPIRED,
        }

        for pending in list(store.jobs):
            try:
                job = client.batches.get(name=pending.job_name)
            except Exception as exc:  # noqa: BLE001 - see below
                # Deliberately broad, and deliberately not fatal. This runs
                # unattended on a schedule over jobs that may be days old, and
                # one unreachable job must not stop the others from being
                # collected. The job stays in the store and is retried.
                report.errors.append(f"{pending.job_name}: {exc}")
                continue

            if job.state in terminal_failures:
                detail = job.error.message if job.error is not None else "no error detail"
                report.failed.append(f"{pending.job_name}: {job.state} ({detail})")
                store.remove(pending.job_name)
                store.save(self.batch_job_store_path)
                continue

            if job.state != genai_types.JobState.JOB_STATE_SUCCEEDED:
                if only_finished:
                    report.still_running += 1
                    continue
                job = self._poll_batch_job(client, job)
                if job.state != genai_types.JobState.JOB_STATE_SUCCEEDED:
                    report.still_running += 1
                    continue

            inlined = job.dest.inlined_responses if job.dest is not None else None
            if not inlined or len(inlined) != len(pending.prompts):
                got = len(inlined) if inlined else 0
                report.failed.append(
                    f"{pending.job_name}: returned {got} responses for "
                    f"{len(pending.prompts)} submitted prompts"
                )
                store.remove(pending.job_name)
                store.save(self.batch_job_store_path)
                continue

            cached = 0
            for prompt, response in zip(pending.prompts, inlined, strict=True):
                if response.error is not None or response.response is None:
                    report.errors.append(
                        f"{pending.job_name}: one request failed "
                        f"({response.error.message if response.error else 'no body'})"
                    )
                    continue
                text, usage = self._extract_response(
                    response.response, prompt=prompt, purpose=pending.purpose
                )
                self.cache.set(
                    model=pending.model,
                    prompt=prompt,
                    response=text,
                    **cache_key_kwargs(pending.cache_namespace),
                )
                self._log_cost(
                    self._build_cost_row(
                        model=pending.model,
                        lane="paid",
                        mode="batch",
                        usage=usage,
                        purpose=pending.purpose,
                        # The job is the unit that was submitted and the unit
                        # that would have retried, and a detached collection
                        # has no retry history to report: one attempt, and a
                        # call id shared by the whole collected job.
                        attempt=1,
                        call_id=pending.job_name,
                    )
                )
                cached += 1

            report.collected_jobs += 1
            report.cached_responses += cached
            # Cache first, then forget the job. A crash in between costs one
            # wasted poll next time; the other order costs the results.
            store.remove(pending.job_name)
            store.save(self.batch_job_store_path)

        report.outstanding = len(BatchJobStore.load(self.batch_job_store_path).jobs)
        return report

    def generate_many(
        self,
        prompts: list[str],
        model: str = MODEL_GENERATE,
        purpose: str = "generation",
        use_cache: bool = True,
        is_user_content: bool = False,
        now: datetime | None = None,
        force_lane: Literal["free", "paid"] | None = None,
        cache_namespace: str | None = None,
    ) -> list[str]:
        """Execute many independent prompts as one logical call, returning
        responses in the same order as ``prompts``.

        ``cache_namespace`` gives the call its own slot in the local cache:
        the same prompt under a different namespace is a different key, so a
        second verification pass over byte-identical prompts is a fresh model
        sample the first time and a replay every time after. Before this
        existed (2026-09-08) a second pass had to bypass the cache outright,
        which worked synchronously and silently broke detached: two batch
        jobs for the two passes were collected into ONE cache slot, the
        second overwrote the first, and a replay re-bought the second pass.
        The namespace rides with a submitted job (``PendingBatchJob``) so
        collection writes to the right slot.

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
        - **Paid lane, batch mode** (the default, ``self.forbid_batch`` unset):
          all cache-miss prompts are submitted as real multi-item Gemini
          batch jobs (``_call_batch_many``) instead of one job per prompt --
          the previous per-``generate()``-call loop paid a full batch-job's
          queueing overhead (observed live: 1-3 minutes) for every single
          item, serially, with none of the throughput benefit batching
          exists for. Chunked by ``_chunk_indices_for_inline_batch`` to
          respect Google's documented ~20MB inline-submission guidance
          (``BATCH_INLINE_MAX_BYTES``): one job for every group small enough
          to submit inline (every group this codebase's own item caps
          produce), more only if a caller ever hands this a genuinely
          oversized group.
        - **Paid lane, on-demand mode** (``self.forbid_batch`` set): dispatched
          exactly like the free lane above -- concurrently, per-item, through
          ``_call_transport_with_lane_handling`` -- just against the paid
          lane's own SDK client and key. This is the owner's "no batch api
          ... go to paid on demand api" policy: a pilot whose free-lane daily
          quota is spent keeps generating, on the paid lane, without ever
          queuing a real batch job.

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
        cache_kwargs = cache_key_kwargs(cache_namespace)
        for i, prompt in enumerate(prompts):
            if use_cache:
                cached = self.cache.get(model=model, prompt=prompt, **cache_kwargs)
                if cached is not None:
                    self._log_cost(
                        CostLogRow(
                            # Per hit, not per group: several hits in one
                            # ``generate_many`` are separate rows and each one
                            # records when it was actually served.
                            timestamp=self._clock(),
                            model=model,
                            lane="cache",
                            mode="cache",
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

        # Paid-lane on-demand mode (``forbid_batch``) dispatches exactly like
        # the free lane -- concurrently, per item, through
        # ``_call_transport_with_lane_handling`` -- so it shares that whole
        # branch; ``_call_transport_with_lane_handling`` (via
        # ``_mode_for_lane``) is what actually resolves the paid lane to sync
        # instead of batch, this condition only decides which of the two
        # dispatch strategies below is used.
        if lane == "free" or (lane == "paid" and self.forbid_batch):
            # Prompts whose free lane closed under them, when the paid fallback
            # would be a batch submission. Gathered here and shipped as ONE job
            # below, rather than each one submitting its own single-prompt job:
            # see ``PaidBatchDeferred``. Only ever non-empty when the caller
            # asked to defer, which today means a detached-batch client.
            deferred_indices: list[int] = []
            # Not gated on ``detach_batch``. A blocking client overflowing into
            # the batch lane wastes the same way -- N single-prompt jobs, each
            # paying the batch API's queueing overhead, polled one after
            # another -- so accumulating is right for both. ``forbid_batch``
            # clients are excluded because their paid fallback is synchronous,
            # and a synchronous fallback per prompt is exactly what that mode
            # is for.
            defer = not self.forbid_batch
            with ThreadPoolExecutor(max_workers=self.FREE_LANE_MAX_CONCURRENCY) as pool:
                future_to_index = {
                    pool.submit(
                        self._call_transport_with_lane_handling,
                        model=model,
                        prompt=prompts[i],
                        lane=lane,
                        purpose=purpose,
                        ref_time=ref_time,
                        defer_paid_batch=defer,
                    ): i
                    for i in pending_indices
                }
                for future in as_completed(future_to_index):
                    i = future_to_index[future]
                    try:
                        result = future.result()
                    except PaidBatchDeferred:
                        deferred_indices.append(i)
                        continue
                    self._log_cost(
                        self._build_cost_row(
                            model=model,
                            lane=result.lane,
                            mode=result.mode,
                            usage=result.usage,
                            purpose=purpose,
                            attempt=result.attempt,
                            call_id=result.call_id,
                        )
                    )
                    if use_cache:
                        self.cache.set(
                            model=model, prompt=prompts[i], response=result.text, **cache_kwargs
                        )
                    results[i] = result.text

            if deferred_indices:
                # Submitted in the prompts' own order rather than the order the
                # thread pool happened to finish in, so a job's prompt list is
                # reproducible and reads the same as the caller's input.
                self._dispatch_paid_batch(
                    sorted(deferred_indices),
                    prompts=prompts,
                    model=model,
                    purpose=purpose,
                    results=results,
                    use_cache=use_cache,
                    cache_namespace=cache_namespace,
                )
        else:  # paid, batch mode (self.forbid_batch is not set)
            self._dispatch_paid_batch(
                pending_indices,
                prompts=prompts,
                model=model,
                purpose=purpose,
                results=results,
                use_cache=use_cache,
                cache_namespace=cache_namespace,
            )

        assert all(text is not None for text in results), (
            "generate_many must fill every index before returning"
        )
        return [text for text in results if text is not None]

    def _dispatch_paid_batch(
        self,
        indices: list[int],
        *,
        prompts: list[str],
        model: str,
        purpose: str,
        results: list[str | None],
        use_cache: bool,
        cache_namespace: str | None = None,
    ) -> None:
        """Submit ``indices`` as real Batch API job(s) and fill ``results``.

        Shared by the two callers that reach the paid batch lane: a client
        routed there up front, and the free lane's overflow once its daily
        quota closes. Sharing it is the point -- the overflow path used to let
        each prompt fall over on its own, which produced one single-prompt job
        per prompt (see ``PaidBatchDeferred``).
        """
        if not indices:
            return
        client = self._get_sdk_client("paid")
        config = genai_types.GenerateContentConfig(
            thinking_config=self._thinking_config_for(model, purpose)
        )
        # Chunked so each real batch job stays within Google's inline
        # submission limits (BATCH_INLINE_MAX_BYTES/_COUNT) -- for every group
        # this codebase actually submits (bounded by NIGHTLY_ITEM_CAP) this is
        # exactly one chunk, i.e. one job; it only splits into more than one
        # job if a caller ever hands ``generate_many`` a genuinely oversized
        # group.
        for chunk in self._chunk_indices_for_inline_batch(indices, prompts):
            chunk_prompts = [prompts[i] for i in chunk]
            batch = self._call_batch_many_with_retry(
                client,
                model=model,
                prompts=chunk_prompts,
                config=config,
                purpose=purpose,
                cache_namespace=cache_namespace,
            )
            for i, (text, usage) in zip(chunk, batch.results, strict=True):
                # Only reachable for a real Batch API submission on the paid
                # lane, so the mode is known here directly rather than
                # derived: these rows, and only these, get Google's batch
                # discount.
                #
                # Every prompt in the job carries the job's own attempt index
                # and call id: the job is the unit that retried, so a group
                # that took three submissions to land shows as attempt=3 on all
                # of its rows, alongside the two ``server_error`` rows the
                # failed submissions wrote.
                self._log_cost(
                    self._build_cost_row(
                        model=model,
                        lane="paid",
                        mode="batch",
                        usage=usage,
                        purpose=purpose,
                        attempt=batch.attempt,
                        call_id=batch.call_id,
                    )
                )
                if use_cache:
                    self.cache.set(
                        model=model,
                        prompt=prompts[i],
                        response=text,
                        **cache_key_kwargs(cache_namespace),
                    )
                results[i] = text

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
