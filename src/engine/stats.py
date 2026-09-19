"""Progress numbers, computed from the review log and nothing else (rule 1)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.contracts import LogEntry, ReviewEntry
from src.engine.fsrs import FSRSEngine
from src.engine.review_log import LearnerState, entries_since_reset
from src.engine.session import Deck, is_learnable

MATURE_DAYS = 21.0
BAND = 500


@dataclass
class Stats:
    known: int = 0
    learning: int = 0
    young: int = 0
    mature: int = 0
    due_now: int = 0
    new_remaining: int = 0
    reviews_today: int = 0
    reviews_total: int = 0
    streak_days: int = 0
    retention_30d: float | None = None
    #: rank band (1-based start) -> (units seen, units in band)
    coverage: dict[int, tuple[int, int]] = field(default_factory=dict)


def compute_stats(
    deck: Deck, state: LearnerState, entries: list[LogEntry], engine: FSRSEngine, now: datetime
) -> Stats:
    stats = Stats()
    by_id = deck.by_id
    stats.known = len(state.known)
    for unit_id, record in state.records.items():
        if unit_id in state.known or unit_id not in by_id:
            continue
        if record.state != "review" or record.stability is None:
            stats.learning += 1
        elif record.stability < MATURE_DAYS:
            stats.young += 1
        else:
            stats.mature += 1
        if record.due <= now:
            stats.due_now += 1
    stats.new_remaining = sum(
        1 for u in deck.units if u.unit_id not in state.records and is_learnable(deck, u, state)
    )
    reviews = [e for e in entries_since_reset(entries) if isinstance(e, ReviewEntry)]
    stats.reviews_total = len(reviews)
    today = now.astimezone(UTC).date()
    days = Counter(e.ts.astimezone(UTC).date() for e in reviews)
    stats.reviews_today = days.get(today, 0)
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        stats.streak_days += 1
        day -= timedelta(days=1)
    recent = [e for e in reviews if e.ts >= now - timedelta(days=30)]
    if recent:
        stats.retention_30d = round(sum(1 for e in recent if e.rating != "again") / len(recent), 3)
    for unit in deck.units:
        band = ((unit.rank - 1) // BAND) * BAND + 1
        seen, total = stats.coverage.get(band, (0, 0))
        seen += 1 if (unit.unit_id in state.records or unit.unit_id in state.known) else 0
        stats.coverage[band] = (seen, total + 1)
    return stats
