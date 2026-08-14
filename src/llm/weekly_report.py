"""Weekly progress report generator synthesizing learning metrics into motivational narrative.

Also implements the report's trigger: activity-triggered auto-fire, a
manually-gated button, and a rate limit, since the narrative line is an LLM
call. The clock is always an injected dependency (never ``datetime.now()``
captured implicitly), per ``CLAUDE.md``, so the trigger stays deterministic
and testable.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import WEEKLY_REPORT_MANUAL_MIN_ITEMS, WEEKLY_REPORT_TRIGGER_ITEMS
from src.llm.provider import LlmProvider, default_llm_provider

# A report costs an LLM call; do not allow rapid re-fires even if the item
# threshold is crossed again immediately (e.g. a burst of duel practice).
DEFAULT_RATE_LIMIT_COOLDOWN = timedelta(hours=20)


class WeeklyReportTriggerDecision(BaseModel):
    """Outcome of evaluating whether a weekly report should fire right now."""

    model_config = ConfigDict(frozen=True)
    should_auto_fire: bool
    manual_button_enabled: bool
    is_rate_limited: bool
    items_since_last_report: int
    reason: str


class WeeklyReportTrigger:
    """Decides when a weekly report fires: activity-triggered, gated, rate-limited."""

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        cooldown: timedelta = DEFAULT_RATE_LIMIT_COOLDOWN,
        trigger_items: int = WEEKLY_REPORT_TRIGGER_ITEMS,
        manual_min_items: int = WEEKLY_REPORT_MANUAL_MIN_ITEMS,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.cooldown = cooldown
        self.trigger_items = trigger_items
        self.manual_min_items = manual_min_items

    def now(self) -> datetime:
        """Current time, from the injected clock."""
        return self._clock()

    def evaluate(
        self,
        items_since_last_report: int,
        last_report_generated_at: datetime | None,
    ) -> WeeklyReportTriggerDecision:
        """Decide auto-fire, manual-button availability, and rate-limit status.

        The window runs from ``last_report_generated_at`` (rolling), not a
        calendar week: ``items_since_last_report`` is the caller's count of
        completed items since that timestamp.
        """
        current_time = self.now()
        is_rate_limited = (
            last_report_generated_at is not None
            and current_time - last_report_generated_at < self.cooldown
        )

        manual_button_enabled = (
            items_since_last_report >= self.manual_min_items and not is_rate_limited
        )
        should_auto_fire = items_since_last_report >= self.trigger_items and not is_rate_limited

        if is_rate_limited:
            reason = "rate_limited"
        elif should_auto_fire:
            reason = "auto_trigger_threshold_reached"
        elif manual_button_enabled:
            reason = "manual_only"
        else:
            reason = "insufficient_activity"

        return WeeklyReportTriggerDecision(
            should_auto_fire=should_auto_fire,
            manual_button_enabled=manual_button_enabled,
            is_rate_limited=is_rate_limited,
            items_since_last_report=items_since_last_report,
            reason=reason,
        )


class WeeklyProgressReport(BaseModel):
    """Weekly synthesis report on learning activity, retention, and progress."""

    model_config = ConfigDict(frozen=True)
    total_reviews: int
    accuracy: float
    newly_acquired_topics: list[str] = Field(default_factory=list)
    current_streak_days: int
    forecast_7day_load: int
    narrative_summary: str


class WeeklyReportGenerator:
    """Produces weekly progress reports from learning history metrics."""

    SYSTEM_PROMPT = (
        "Du bist ein motivierender Lerncoach für Deutsch als Fremdsprache. Fasse die "
        "wöchentlichen Lernerfolge prägnant und motivierend in 2-3 Absätzen zusammen. "
        "Hebe Stärken hervor und gib einen kurzen Ausblick auf die nächste Woche."
    )

    def __init__(self, provider: LlmProvider | None = None) -> None:
        self.provider = provider or default_llm_provider()

    def generate_report(
        self,
        total_reviews: int,
        accuracy: float,
        newly_acquired_topics: list[str],
        current_streak_days: int,
        forecast_7day_load: int,
    ) -> WeeklyProgressReport:
        """Generate weekly summary narrative using LLM."""
        topics_str = ", ".join(newly_acquired_topics) if newly_acquired_topics else "Keine"
        prompt = (
            f"Wochen-Statistik:\n"
            f"- Wiederholungen diese Woche: {total_reviews}\n"
            f"- Genauigkeit: {accuracy:.1%}\n"
            f"- Neu erworbene Themen: {topics_str}\n"
            f"- Serie (Tage): {current_streak_days}\n"
            f"- 7-Tage FSRS Arbeitslast: {forecast_7day_load} anstehende Karten\n\n"
            "Verfasse den wöchentlichen Fortschrittsbericht."
        )

        narrative = self.provider.generate_text(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            purpose="weekly_report",
            is_user_content=True,  # carries the learner's mistake history
        )

        return WeeklyProgressReport(
            total_reviews=total_reviews,
            accuracy=accuracy,
            newly_acquired_topics=newly_acquired_topics,
            current_streak_days=current_streak_days,
            forecast_7day_load=forecast_7day_load,
            narrative_summary=narrative,
        )

    def maybe_generate_report(
        self,
        trigger: WeeklyReportTrigger,
        items_since_last_report: int,
        last_report_generated_at: datetime | None,
        total_reviews: int,
        accuracy: float,
        newly_acquired_topics: list[str],
        current_streak_days: int,
        forecast_7day_load: int,
        manual_request: bool = False,
    ) -> tuple[WeeklyProgressReport | None, WeeklyReportTriggerDecision]:
        """Generate a report only if the trigger allows it right now.

        Fires automatically once ``items_since_last_report`` crosses the
        auto-fire threshold, or on an explicit ``manual_request`` provided the
        manual-activity minimum is met -- in both cases only if not currently
        rate-limited. Returns ``(None, decision)`` when nothing should fire,
        so the caller can inspect ``decision.reason`` without ever blocking
        the round.
        """
        decision = trigger.evaluate(items_since_last_report, last_report_generated_at)
        should_fire = decision.should_auto_fire or (
            manual_request and decision.manual_button_enabled
        )
        if not should_fire:
            return None, decision

        report = self.generate_report(
            total_reviews=total_reviews,
            accuracy=accuracy,
            newly_acquired_topics=newly_acquired_topics,
            current_streak_days=current_streak_days,
            forecast_7day_load=forecast_7day_load,
        )
        return report, decision
