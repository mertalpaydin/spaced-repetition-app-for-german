"""The review log and its replay: the single source of truth for progress.

CLAUDE.md rule 1: every progress number is computed in code from this log.
The log is JSONL, append-only, one ``ReviewEntry`` or ``MarkEntry`` per line
(``data/review_log.jsonl``, gitignored). ``derive_state`` replays it through
the FSRS engine deterministically (fuzzing off), so the laptop client and,
later, the PWA reach the same memory state from the same lines.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from src.contracts import LogEntry, MarkEntry, ReviewEntry
from src.engine.fsrs import FSRSEngine, FSRSRecord

DEFAULT_LOG_PATH = Path("data/review_log.jsonl")
_ENTRY: TypeAdapter[LogEntry] = TypeAdapter(LogEntry)


def parse_entry(line: str) -> LogEntry | None:
    """One log line, or ``None`` for a line that is not an entry (a partial
    write, a hand edit). Skipped, never fatal: the log is the learner's
    history and a corrupt line must not hide the rest."""
    line = line.strip()
    if not line:
        return None
    try:
        entry: LogEntry = _ENTRY.validate_json(line)
        return entry
    except ValidationError:
        return None


def read_entries(path: Path) -> list[LogEntry]:
    if not path.exists():
        return []
    entries = [parse_entry(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return sorted((e for e in entries if e is not None), key=lambda e: (e.ts, e.seq))


def merge_entries(*logs: Iterable[LogEntry]) -> list[LogEntry]:
    """Two devices' logs as one: the same review appearing in both (same
    ``unit_id`` and ``ts``) counts once; order is by time, then sequence."""
    seen: set[tuple[str, str, datetime]] = set()
    merged: list[LogEntry] = []
    for log in logs:
        for entry in log:
            key = (entry.type, entry.unit_id, entry.ts)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return sorted(merged, key=lambda e: (e.ts, e.seq))


class ReviewLog:
    """Append-only JSONL file. ``now`` is injected so tests control the clock."""

    def __init__(
        self, path: Path = DEFAULT_LOG_PATH, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.path = path
        self.now = now
        self.entries: list[LogEntry] = read_entries(path)

    @property
    def next_seq(self) -> int:
        return max((e.seq for e in self.entries), default=0) + 1

    def append(self, entry: LogEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        self.entries.append(entry)

    def record_review(
        self,
        *,
        unit_id: str,
        card_id: str,
        rating: str,
        outcome: str,
        answers: list[str],
        expected: list[str],
        elapsed_ms: int,
        deck_version: str,
    ) -> ReviewEntry:
        entry = ReviewEntry(
            seq=self.next_seq,
            ts=self.now(),
            unit_id=unit_id,
            card_id=card_id,
            rating=rating,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            answers=answers,
            expected=expected,
            elapsed_ms=elapsed_ms,
            deck_version=deck_version,
        )
        self.append(entry)
        return entry

    def record_mark(self, *, unit_id: str, known: bool, source: str) -> MarkEntry:
        entry = MarkEntry(
            seq=self.next_seq,
            ts=self.now(),
            unit_id=unit_id,
            known=known,
            source=source,  # type: ignore[arg-type]
        )
        self.append(entry)
        return entry


@dataclass
class LearnerState:
    """What the log says about every unit it mentions."""

    #: FSRS memory state per unit, for units with at least one review.
    records: dict[str, FSRSRecord] = field(default_factory=dict)
    #: Units the learner marked known (the latest mark wins).
    known: set[str] = field(default_factory=set)
    #: Units the learner has passed judgement on in triage, known or not.
    triaged: set[str] = field(default_factory=set)
    #: The card shown last per unit, so the next review rotates.
    last_card: dict[str, str] = field(default_factory=dict)
    #: Ratings per unit in log order, for the stats.
    ratings: dict[str, list[str]] = field(default_factory=dict)
    #: When each unit was first reviewed, so "new today" can be counted.
    first_review: dict[str, datetime] = field(default_factory=dict)
    #: Every review's time, for the day's exercise budget.
    review_times: list[datetime] = field(default_factory=list)
    #: The unit of the last review, so the scheduler does not repeat it.
    last_unit: str | None = None


def derive_state(entries: Iterable[LogEntry], engine: FSRSEngine) -> LearnerState:
    """Replay the log. Deterministic: same entries, same engine parameters,
    same state, on any machine."""
    state = LearnerState()
    for entry in sorted(entries, key=lambda e: (e.ts, e.seq)):
        if isinstance(entry, MarkEntry):
            state.triaged.add(entry.unit_id)
            if entry.known:
                state.known.add(entry.unit_id)
            else:
                state.known.discard(entry.unit_id)
            continue
        record = state.records.get(entry.unit_id) or FSRSRecord(card_id=entry.unit_id, due=entry.ts)
        state.records[entry.unit_id] = engine.schedule_review(record, entry.rating, now=entry.ts)
        state.last_card[entry.unit_id] = entry.card_id
        state.ratings.setdefault(entry.unit_id, []).append(entry.rating)
        state.first_review.setdefault(entry.unit_id, entry.ts)
        state.review_times.append(entry.ts)
        state.last_unit = entry.unit_id
    return state
