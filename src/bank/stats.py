"""Bank statistics calculator and coverage summary generator."""

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from src.bank.storage import SqliteItemBank


class BankSummary(BaseModel):
    """Statistical summary of item bank volume and quality balance."""

    model_config = ConfigDict(frozen=True)
    total_items: int
    total_topics_covered: int
    items_per_cefr: dict[str, int] = Field(default_factory=dict)
    items_per_difficulty: dict[int, int] = Field(default_factory=dict)
    avg_items_per_topic: float


class BankStatsCalculator:
    """Computes distribution metrics across bank items."""

    @classmethod
    def compute_summary(cls, bank: SqliteItemBank) -> BankSummary:
        """Compute statistical coverage summary from SQLite database."""
        items = bank.get_all_items()
        total = len(items)

        topics: set[str] = set()
        cefr_counts: dict[str, int] = defaultdict(int)
        diff_counts: dict[int, int] = defaultdict(int)

        for it in items:
            topics.add(it.topic_id)
            cefr_counts[it.cefr] += 1
            diff_counts[it.difficulty] += 1

        topic_count = len(topics)
        avg_items = (total / topic_count) if topic_count > 0 else 0.0

        return BankSummary(
            total_items=total,
            total_topics_covered=topic_count,
            items_per_cefr=dict(cefr_counts),
            items_per_difficulty=dict(diff_counts),
            avg_items_per_topic=round(avg_items, 2),
        )
