"""The terminal client: triage, practice, stats.

    uv run python -m src.cli.train practice
    uv run python -m src.cli.train triage --batch 50
    uv run python -m src.cli.train stats

Everything the learner does lands in ``data/review_log.jsonl``; everything
shown is derived from it. Input and output are injected so the client can
be driven by a scripted stdin in tests.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.contracts import PhraseCard, PhraseUnit
from src.engine.fsrs import FSRSEngine
from src.engine.grading import grade_card, render_marked, render_with_gaps
from src.engine.review_log import (
    DEFAULT_LOG_PATH,
    LearnerState,
    ReviewLog,
    derive_state,
    merge_entries,
    read_entries,
)
from src.engine.session import Deck, Settings, next_unit, pick_card, untriaged_units
from src.engine.stats import compute_stats
from src.phrases.export import DEFAULT_DECK_DIR

Reader = Callable[[str], str | None]
Writer = Callable[[str], None]

REVEAL = "?"
KNOWN = "!"
QUIT = "q"


class Client:
    def __init__(
        self,
        deck: Deck,
        log: ReviewLog,
        *,
        read: Reader,
        write: Writer,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
        settings: Settings | None = None,
    ) -> None:
        self.deck = deck
        self.log = log
        self.read = read
        self.write = write
        self.now = now
        self.clock = clock
        self.settings = settings or Settings()
        self.engine = FSRSEngine(request_retention=self.settings.retention)

    def state(self) -> LearnerState:
        return derive_state(self.log.entries, self.engine)

    # -- triage ---------------------------------------------------------------

    def triage(self, batch: int) -> int:
        """Rank order, one unit per line: 1 = known, 2 = learn, u = undo, q = stop."""
        state = self.state()
        todo = untriaged_units(self.deck, state)[:batch]
        if not todo:
            self.write("Nichts mehr zu sortieren.\n")
            return 0
        self.write(f"{len(todo)} Einheiten: 1 = kenne ich, 2 = lernen, u = zurück, q = Ende\n")
        history: list[tuple[PhraseUnit, bool]] = []
        i = 0
        while i < len(todo):
            unit = todo[i]
            case = f" +{unit.case}" if unit.case else ""
            answer = self.read(f"[{unit.rank}] {unit.display_de}{case} ({unit.kind}) > ")
            if answer is None or answer.strip().lower() == QUIT:
                break
            answer = answer.strip().lower()
            if answer == "u" and history:
                unit_prev, _ = history.pop()
                self.log.record_mark(unit_id=unit_prev.unit_id, known=False, source="triage")
                i -= 1
                self.write(f"  zurück: {unit_prev.display_de} wird gelernt\n")
                continue
            if answer not in {"1", "2"}:
                continue
            known = answer == "1"
            self.log.record_mark(unit_id=unit.unit_id, known=known, source="triage")
            history.append((unit, known))
            i += 1
        self.write(f"{len(history)} sortiert.\n")
        return 0

    # -- practice -------------------------------------------------------------

    def practice(self, limit: int) -> int:
        shown = 0
        while shown < limit:
            state = self.state()
            unit = next_unit(self.deck, state, self.engine, self.settings, self.now())
            if unit is None:
                self.write("Nichts fällig, keine neuen Einheiten für heute.\n")
                break
            card = pick_card(self.deck, unit, state)
            if card is None:
                break
            if not self._practice_card(unit, card):
                break
            shown += 1
        self.write(f"{shown} Karten geübt.\n")
        return 0

    def _practice_card(self, unit: PhraseUnit, card: PhraseCard) -> bool:
        """One card. Returns False when the learner quits."""
        self.write("\n")
        if card.context_de:
            self.write(f"  {card.context_de}\n")
        self.write(f"  {render_with_gaps(card)}\n")
        if card.context_en:
            self.write(f"  ({card.context_en})\n")
        self.write(f"  {card.gloss_en}\n")
        started = self.clock()
        typed: list[str | None] = []
        for i in range(len(card.gaps)):
            hint = f"{REVEAL} = zeigen, {KNOWN} = kannte ich, q = Ende"
            prompt = f"  Lücke {i + 1}/{len(card.gaps)} ({hint}) > "
            answer = self.read(prompt)
            if answer is None or answer.strip() == QUIT:
                return False
            answer = answer.strip()
            if answer == KNOWN:
                self.log.record_mark(unit_id=unit.unit_id, known=True, source="practice")
                self.write(f"  Als bekannt markiert: {unit.display_de}\n")
                return True
            typed.append(None if answer == REVEAL else answer)
        elapsed_ms = int((self.clock() - started) * 1000)
        grade = grade_card(card, typed)
        self.log.record_review(
            unit_id=unit.unit_id,
            card_id=card.card_id,
            rating=grade.rating,
            outcome=grade.outcome,
            answers=[g.typed for g in grade.gaps],
            expected=[g.expected for g in grade.gaps],
            elapsed_ms=elapsed_ms,
            deck_version=self.deck.manifest.deck_version,
        )
        verdict = {"good": "Richtig", "hard": "Richtig, mit Tippfehler", "again": "Falsch"}[
            grade.rating
        ]
        self.write(f"  {verdict}.  {render_marked(card)}\n")
        for g in grade.gaps:
            if g.outcome not in {"exact", "translit"}:
                self.write(f"    {g.typed or '(gezeigt)'} -> {g.expected}\n")
        case = f" +{unit.case}" if unit.case else ""
        self.write(f"  {unit.display_de}{case}\n")
        return True

    # -- merge ----------------------------------------------------------------

    def merge(self, other: Path) -> int:
        """Fold another device's log into this one: entries this log does not
        have (by type, unit and time) are appended, renumbered after ours."""
        incoming = read_entries(other)
        merged = merge_entries(self.log.entries, incoming)
        have = {(e.type, e.unit_id, e.ts) for e in self.log.entries}
        added = 0
        for entry in merged:
            if (entry.type, entry.unit_id, entry.ts) in have:
                continue
            self.log.append(entry.model_copy(update={"seq": self.log.next_seq}))
            added += 1
        self.write(f"{added} Einträge aus {other} übernommen.\n")
        return 0

    # -- stats ----------------------------------------------------------------

    def stats(self) -> int:
        state = self.state()
        s = compute_stats(self.deck, state, self.log.entries, self.engine, self.now())
        lines = [
            f"bekannt {s.known}  lernend {s.learning}  jung {s.young}  reif {s.mature}",
            f"fällig jetzt {s.due_now}  neu verfügbar {s.new_remaining}",
            f"heute {s.reviews_today}  gesamt {s.reviews_total}  Serie {s.streak_days} Tag(e)",
            f"Behalten (30 Tage): {'-' if s.retention_30d is None else f'{s.retention_30d:.0%}'}",
        ]
        for band, (seen, total) in sorted(s.coverage.items()):
            lines.append(f"  Rang {band:5}-{band + 499:5}: {seen:4}/{total}")
        self.write("\n".join(lines) + "\n")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["triage", "practice", "stats", "merge"])
    parser.add_argument("other", nargs="?", type=Path, help="merge: the other device's log")
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--batch", type=int, default=50, help="triage: units per run")
    parser.add_argument("--limit", type=int, default=30, help="practice: cards per run")
    parser.add_argument("--cards-per-day", type=int, default=40)
    return parser


def _stdout_writer(text: str) -> None:
    sys.stdout.write(text)


def _stdin_reader(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    client = Client(
        Deck.load(args.deck),
        ReviewLog(args.log),
        read=_stdin_reader,
        write=_stdout_writer,
        settings=Settings(cards_per_day=args.cards_per_day),
    )
    if args.mode == "triage":
        return client.triage(args.batch)
    if args.mode == "practice":
        return client.practice(args.limit)
    if args.mode == "merge":
        if args.other is None:
            print("merge needs the other log's path")
            return 2
        return client.merge(args.other)
    return client.stats()


if __name__ == "__main__":
    sys.exit(main())
