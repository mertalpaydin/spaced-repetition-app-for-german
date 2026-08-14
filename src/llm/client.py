"""Unified Gemini LLM client with cost accounting, budget ceilings, and two-lane execution."""

import os
import time
import warnings
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.contracts import MODEL_GENERATE, MODEL_LIVE
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

    Note on transport: this client currently stubs the actual network call (see
    ``_call_transport``). No real Gemini request is made yet; the provider SDK is
    declared as a dependency but not wired to a live transport. Cost accounting,
    caching, the spend ceiling, and the two-lane routing logic are all real and
    exercised by every call, so wiring in a real transport later is a matter of
    replacing ``_call_transport``'s body without touching any caller.
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

        This is a stub: no real network transport is implemented. It exists as the
        single seam a real Gemini SDK call would be wired into, and as the seam
        tests patch to simulate 429s (see ``QuotaExceededError``).
        """
        prompt_tokens = len(prompt.split()) * 2
        response_text = f"Mocked LLM generation response for {purpose} (lane={lane}, mode={mode})"
        completion_tokens = 50
        return response_text, prompt_tokens, completion_tokens

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
