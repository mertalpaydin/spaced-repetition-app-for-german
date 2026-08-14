"""180-day Monte Carlo learner simulation harness testing FSRS pacing and DAG velocity."""

import random
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MAX_NEW_TOPICS_PER_DAY, Topic
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy
from src.engine.scheduler import LearningScheduler
from src.engine.topic_state import TopicStateManager


class SimulationSummary(BaseModel):
    """Aggregated outcome of 180-day learner progression simulation."""

    model_config = ConfigDict(frozen=True)
    days_simulated: int
    total_reviews_completed: int
    topics_acquired: int
    avg_daily_reviews: float
    max_forecast_load_encountered: int
    retention_rate: float
    daily_review_counts: list[int] = Field(default_factory=list)


class LearnerSimulationHarness:
    """Simulates realistic learner behavior across months of daily study."""

    def __init__(
        self,
        topics: list[Topic],
        base_learner_accuracy: float = 0.85,
        random_seed: int = 42,
    ) -> None:
        self.topics = topics
        self.base_accuracy = base_learner_accuracy
        self.rng = random.Random(random_seed)
        self.fsrs_engine = FSRSEngine()
        self.scheduler = LearningScheduler()

    def run_simulation(self, days: int = 180) -> SimulationSummary:
        """Run step-by-step daily simulation."""
        start_time = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        topic_manager = TopicStateManager(self.topics, now=start_time)
        fsrs_records: dict[str, FSRSRecord] = {}
        total_reviews = 0
        total_successes = 0
        daily_counts: list[int] = []
        max_load = 0

        # Synthetic item pool per topic
        topic_items: dict[str, list[str]] = {
            t.id: [f"{t.id}_item_{i}" for i in range(12)] for t in self.topics
        }

        for day in range(days):
            current_time = start_time + timedelta(days=day)

            # Check 7-day forecast load
            forecast = self.scheduler.forecast_7day_load(
                list(fsrs_records.values()), now=current_time
            )
            max_load = max(max_load, forecast)
            overload_blocked = forecast > self.scheduler.forecast_threshold

            # Determine due reviews
            due_cards = [r for r in fsrs_records.values() if r.due <= current_time]

            # Introduce new topics if not overload blocked (up to MAX_NEW_TOPICS_PER_DAY)
            new_topics_today = 0
            if not overload_blocked and new_topics_today < MAX_NEW_TOPICS_PER_DAY:
                ready_topics = [
                    t_id for t_id, s in topic_manager.states.items() if s.state == "ready"
                ]
                for t_id in ready_topics[:MAX_NEW_TOPICS_PER_DAY]:
                    for item_id in topic_items[t_id][:3]:
                        if item_id not in fsrs_records:
                            fsrs_records[item_id] = FSRSRecord(card_id=item_id, due=current_time)
                    new_topics_today += 1

            # Execute reviews for today
            day_reviews = 0
            for card in due_cards:
                is_correct = self.rng.random() < self.base_accuracy
                rating = HintPolicy.evaluate_attempt(hint_level=0, is_correct=is_correct)

                updated = self.fsrs_engine.schedule_review(card, rating=rating, now=current_time)
                fsrs_records[card.card_id] = updated

                # Extract topic_id from item_id
                t_id = "_".join(card.card_id.split("_")[:-2])
                if t_id in topic_manager.states:
                    topic_manager.record_attempt(
                        t_id, is_unhinted_pass=is_correct, now=current_time
                    )

                day_reviews += 1
                total_reviews += 1
                if is_correct:
                    total_successes += 1

            daily_counts.append(day_reviews)

        acquired_count = sum(1 for s in topic_manager.states.values() if s.state == "acquired")
        avg_reviews = round(total_reviews / days, 1) if days > 0 else 0.0
        retention = round(total_successes / total_reviews, 3) if total_reviews > 0 else 0.0

        return SimulationSummary(
            days_simulated=days,
            total_reviews_completed=total_reviews,
            topics_acquired=acquired_count,
            avg_daily_reviews=avg_reviews,
            max_forecast_load_encountered=max_load,
            retention_rate=retention,
            daily_review_counts=daily_counts,
        )
