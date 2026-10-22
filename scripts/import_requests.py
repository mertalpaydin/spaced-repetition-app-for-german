"""Bring the words added from the page into ``requested.yaml``.

    uv run python scripts/import_requests.py path/to/review_log.jsonl          # dry run
    uv run python scripts/import_requests.py path/to/review_log.jsonl --write

A word added with the page's button rides the review log as a ``request``
entry and is taught from the reserve shard, which no reviewer has read. The
owner's rule for the reserve is that its words "should be reviewed if only
added" (2026-10-22), and the review only ever looks at the deck. So this
copies each added word into ``requested.yaml``: the next build makes it a
requested unit, ``audit_top_units.band_units`` and ``review_deck.in_band``
count it wherever it ranks, and the next review round reads its cards.

The page-added unit keeps its id, so the learner's history carries over.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.engine.typo_grader import ScopedTypoGrader
from src.phrases.curated import DEFAULT_PHRASES_DIR, load_curated
from src.phrases.export import DEFAULT_DECK_DIR

from scripts.read_issues import _objects


def known_units(deck_dir: Path) -> dict[str, tuple[str, str]]:
    """unit_id -> (lemma_key, kind) over the deck and its reserve."""
    out: dict[str, tuple[str, str]] = {}
    sources = [deck_dir / "units.json", *sorted((deck_dir / "reserve").glob("?.json"))]
    for path in sources:
        if not path.exists():
            continue
        for unit in json.loads(path.read_text(encoding="utf-8")).get("units", []):
            out[str(unit["unit_id"])] = (str(unit["lemma_key"]), str(unit["kind"]))
    return out


def requested_words(log_text: str, units: dict[str, tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(key, kind, unit_id) for every word added from the page, oldest first.

    A word written by a model carries its unit in the entry; a reserve word
    carries only its id and is looked up. One found in neither is reported
    and skipped: without its lemma there is nothing to request.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    rows = [r for r in _objects(log_text) if r.get("type") == "request"]
    for row in sorted(rows, key=lambda r: str(r.get("ts", ""))):
        unit_id = str(row.get("unit_id", ""))
        if not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        payload = row.get("unit")
        if isinstance(payload, dict) and payload.get("lemma_key") and payload.get("kind"):
            out.append((str(payload["lemma_key"]), str(payload["kind"]), unit_id))
        elif unit_id in units:
            key, kind = units[unit_id]
            out.append((key, kind, unit_id))
        else:
            print(f"  cannot resolve {unit_id}: not in the deck or the reserve, skipped")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="an exported review_log.jsonl")
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--phrases-dir", type=Path, default=DEFAULT_PHRASES_DIR)
    parser.add_argument("--write", action="store_true", help="append to requested.yaml")
    args = parser.parse_args(argv)

    fold = ScopedTypoGrader.apply_transliteration
    already = {(fold(r.key), r.kind) for r in load_curated(args.phrases_dir).requested}
    found = requested_words(args.log.read_text(encoding="utf-8"), known_units(args.deck))
    new = [(k, kind, uid) for k, kind, uid in found if (fold(k), kind) not in already]
    print(f"{len(found)} word(s) added from the page, {len(new)} not yet in requested.yaml")
    for key, kind, unit_id in new:
        print(f"  {unit_id:30} {kind:16} {key}")
    if not new or not args.write:
        return 0
    stamp = datetime.now(UTC).date().isoformat()
    with (args.phrases_dir / "requested.yaml").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            f"\n# Added from the page's button, imported {stamp} so a review reads them.\n"
        )
        for key, kind, _ in new:
            handle.write(f'- {{key: "{key}", kind: {kind}, note: "added from the page"}}\n')
    print(f"appended {len(new)} to requested.yaml; rebuild and run a review round")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
