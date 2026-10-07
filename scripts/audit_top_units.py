"""Is the top of the deck actually finished?

    uv run python scripts/audit_top_units.py --top 500

A unit is finished when a learner meeting it gets a complete card: a corpus
sentence with an English translation, the phrase's own English above it, and
a reviewer's eyes on both. Four separate build stages produce those, and
until 2026-10-07 nothing checked that they had all run for the same unit, so
a requested word could be taught first and still arrive with no gloss and no
review (owner: "make sure everything in top 500 are reviewed, translated,
phrase translated etc.").

Exit code 1 when anything in the band is unfinished, so it can gate a push.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from src.contracts import PhraseCard, PhraseUnit
from src.phrases.export import DEFAULT_DECK_DIR, load_deck

AUDIT_DIR = Path("docs/audits/phase-1-review")


def reviewed_ids(prefix: str, audit_dir: Path = AUDIT_DIR) -> set[str]:
    """Ids a reviewer has actually read, from the round ledgers."""
    out: set[str] = set()
    if not audit_dir.exists():
        return out
    for path in audit_dir.glob(f"reviewed-{prefix}-ids-round-*.txt"):
        out.update(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    out.discard("")
    return out


def unfinished(
    units: list[PhraseUnit],
    cards_by_unit: dict[str, list[PhraseCard]],
    reviewed_cards: set[str],
    reviewed_units: set[str],
) -> dict[str, list[PhraseUnit]]:
    """Which units are missing which finishing, keyed by what is missing."""
    out: dict[str, list[PhraseUnit]] = {
        "no_card": [],
        "no_sentence_translation": [],
        "no_phrase_translation": [],
        "unreviewed_cards": [],
        "unreviewed_unit": [],
    }
    for unit in units:
        if unit.trivial:
            continue
        cards = cards_by_unit.get(unit.unit_id, [])
        if not cards:
            out["no_card"].append(unit)
            continue
        glossed = [c for c in cards if c.gloss_en is not None]
        if not glossed:
            out["no_sentence_translation"].append(unit)
        if unit.gloss_en is None:
            out["no_phrase_translation"].append(unit)
        if any(c.card_id not in reviewed_cards for c in glossed):
            out["unreviewed_cards"].append(unit)
        if unit.unit_id not in reviewed_units:
            out["unreviewed_unit"].append(unit)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=500)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--audit-dir", type=Path, default=AUDIT_DIR)
    parser.add_argument("--show", type=int, default=12, help="examples to print per problem")
    parser.add_argument("--json", type=Path, default=None, help="also write the findings here")
    args = parser.parse_args(argv)

    _, units, cards = load_deck(args.deck)
    by_unit: dict[str, list[PhraseCard]] = {}
    for card in cards:
        by_unit.setdefault(card.unit_id, []).append(card)
    band = sorted(units, key=lambda u: u.rank)[: args.top]
    problems = unfinished(
        band,
        by_unit,
        reviewed_ids("card", args.audit_dir),
        reviewed_ids("unit", args.audit_dir),
    )

    teachable = [u for u in band if not u.trivial]
    print(f"top {args.top}: {len(teachable)} non-trivial units")
    labels = {
        "no_card": "no card at all",
        "no_sentence_translation": "cards but no English for the sentence",
        "no_phrase_translation": "no English for the phrase itself (Gesucht:)",
        "unreviewed_cards": "cards no reviewer has read",
        "unreviewed_unit": "unit no reviewer has read",
    }
    for key, label in labels.items():
        found = problems[key]
        share = 100 * len(found) / max(len(teachable), 1)
        print(f"  {label:<46} {len(found):5} ({share:4.1f}%)")
        for unit in found[: args.show]:
            print(f"      rank {unit.rank:5}  {unit.kind:16} {unit.display_de}")
        if len(found) > args.show:
            print(f"      ... and {len(found) - args.show} more")

    kinds = Counter(u.kind for u in problems["no_card"])
    if kinds:
        print(f"  card-less by kind: {dict(kinds)}")
    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {k: [u.unit_id for u in v] for k, v in problems.items()},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"  written to {args.json}")
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
