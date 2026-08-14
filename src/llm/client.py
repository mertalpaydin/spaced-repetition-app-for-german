"""Unified Gemini LLM client with cost accounting, budget ceilings, and two-lane execution."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MODEL_GENERATE
from src.llm.cache import LlmCache

Lane = Literal["free", "paid", "cache"]


class BudgetExceeded(Exception):
    """Raised when month-to-date LLM spend exceeds the configured spend ceiling."""


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
    """The single entry point for all LLM calls in the application."""

    # Pricing per million tokens (USD)
    PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
        "gemini-3.5-flash-lite": (0.075, 0.30),
        "gemini-3.7-flash": (0.15, 0.60),
    }

    def __init__(
        self,
        free_api_key: str | None = None,
        paid_api_key: str | None = None,
        spend_ceiling_usd: float = 5.00,
        cost_log_path: Path | str = ".cache/cost_log.jsonl",
        cache_dir: Path | str = ".cache/llm",
        restrict_user_content_to_paid_lane: bool = False,
    ) -> None:
        self.free_api_key = (
            free_api_key or os.getenv("GEMINI_FREE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
        self.paid_api_key = paid_api_key or os.getenv("GEMINI_PAID_API_KEY")
        self.spend_ceiling_usd = spend_ceiling_usd
        self.cost_log_path = Path(cost_log_path)
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache = LlmCache(cache_dir=cache_dir)
        self.restrict_user_content_to_paid_lane = restrict_user_content_to_paid_lane
        self.free_lane_open = True
        self.cost_records: list[CostLogRow] = []
        self._load_cost_log()

    def _load_cost_log(self) -> None:
        if self.cost_log_path.exists():
            for line in self.cost_log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        self.cost_records.append(CostLogRow.model_validate_json(line))
                    except Exception:
                        pass

    def get_month_to_date_spend(self, now: datetime | None = None) -> float:
        """Calculate total USD spend for the current calendar month."""
        ref_time = now or datetime.now(UTC)
        total = 0.0
        for row in self.cost_records:
            if row.timestamp.year == ref_time.year and row.timestamp.month == ref_time.month:
                total += row.cost_usd
        return round(total, 6)

    def _log_cost(self, row: CostLogRow) -> None:
        self.cost_records.append(row)
        with self.cost_log_path.open("a", encoding="utf-8") as f:
            f.write(row.model_dump_json() + "\n")

    def _estimate_cost(
        self, model: str, prompt_tokens: int, completion_tokens: int, lane: Lane
    ) -> float:
        if lane == "cache" or lane == "free":
            return 0.0
        in_p, out_p = self.PRICING_PER_MILLION.get(model, (0.075, 0.30))
        # Batch 50% discount applies to paid lane batch requests
        cost = ((prompt_tokens / 1_000_000) * in_p + (completion_tokens / 1_000_000) * out_p) * 0.5
        return round(cost, 6)

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
        ref_time = now or datetime.now(UTC)

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

        # 3. Determine Lane
        lane: Lane = "free"
        if self.restrict_user_content_to_paid_lane and is_user_content:
            lane = "paid"
        elif not self.free_lane_open:
            lane = "paid"

        # 4. Generate Response (Mocked transport when no active network / keys)
        prompt_tokens = len(prompt.split()) * 2
        response_text = f"Mocked LLM generation response for {purpose}"
        completion_tokens = 50

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
