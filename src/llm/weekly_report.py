"""Weekly progress report generator synthesizing learning metrics into motivational narrative."""

from pydantic import BaseModel, ConfigDict, Field

from src.llm.provider import LlmProvider, MockLlmClient


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
        self.provider = provider or MockLlmClient()

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
        )

        return WeeklyProgressReport(
            total_reviews=total_reviews,
            accuracy=accuracy,
            newly_acquired_topics=newly_acquired_topics,
            current_streak_days=current_streak_days,
            forecast_7day_load=forecast_7day_load,
            narrative_summary=narrative,
        )
