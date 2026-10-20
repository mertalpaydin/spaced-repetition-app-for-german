"""What the learner reported from the app, against the cards he reported.

    uv run python scripts/read_issues.py path/to/review_log.jsonl

The "Problem melden" button writes an ``issue`` line into the review log,
which syncs through the gist like everything else. This prints each one with
the card it was about, so the report arrives with its evidence instead of as
a screenshot and a memory (owner, 2026-10-20: "so that I do not have to take
screenshots or notes to explain them to you").

Exit code 1 when there are unread reports, so it can gate a session.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.contracts import IssueEntry, PhraseCard, PhraseUnit
from src.phrases.export import DEFAULT_DECK_DIR, load_deck


def read_issues(log_path: Path) -> list[IssueEntry]:
    """Every issue line in the log, oldest first.

    The log is append-only and may hold entries this build does not know, so
    a line that is not an issue is skipped rather than parsed.
    """
    out: list[IssueEntry] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and raw.get("type") == "issue":
            out.append(IssueEntry.model_validate(raw))
    return sorted(out, key=lambda e: (e.ts, e.seq))


def describe(
    issue: IssueEntry,
    units: dict[str, PhraseUnit],
    cards: dict[str, PhraseCard],
) -> str:
    """One report, with the card as the learner met it."""
    unit = units.get(issue.unit_id)
    card = cards.get(issue.card_id)
    head = f"{issue.ts:%Y-%m-%d %H:%M}  {issue.unit_id}"
    if unit is not None:
        head += f"  {unit.display_de}  (rank {unit.rank}, {unit.kind})"
        head += f"\n    Gesucht: {unit.gloss_en}"
    else:
        head += "  [unit is no longer in the deck]"
    if card is None:
        head += f"\n    card {issue.card_id} is no longer in the deck"
    else:
        text = card.sentence_de
        for gap in sorted(card.gaps, key=lambda g: -g.start):
            text = f"{text[: gap.start]}[{text[gap.start : gap.end]}]{text[gap.end :]}"
        head += f"\n    {text}\n    EN: {card.gloss_en}"
    if issue.note:
        head += f'\n    note: "{issue.note}"'
    return head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="an exported review_log.jsonl")
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--since", default="", help="only reports at or after this ISO date")
    args = parser.parse_args(argv)

    issues = read_issues(args.log)
    if args.since:
        issues = [i for i in issues if i.ts.isoformat() >= args.since]
    _, units, cards = load_deck(args.deck)
    by_unit = {u.unit_id: u for u in units}
    by_card = {c.card_id: c for c in cards}

    if not issues:
        print("no reports in this log")
        return 0
    print(f"{len(issues)} report(s)\n")
    for issue in issues:
        print(describe(issue, by_unit, by_card))
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
