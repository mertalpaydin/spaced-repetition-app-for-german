"""Remove the glosses the cross-corpus id collision poisoned.

## What went wrong

``build_translations._fill_from_tatoeba`` used to consult ``shortest_by_id``
-- a dictionary keyed on TATOEBA SENTENCE IDS -- for every carrier that had
any id at all, including carriers read from Leipzig. Leipzig line ids and
Tatoeba sentence ids are both bare integers in the same numeric range and
mean nothing to each other, so a Leipzig line numbered 541845 silently
received Tatoeba sentence 541845's English. Two of the results reached the
last pilot's 430 accepted items:

    DE: Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung
        schlimmer ist als gedacht.
    EN: "She crossed the street."

    DE: Jetzt gibt sie ein Update zu ihrem Alltag während der Chemotherapie.
    EN: "Bye!"

Measured against the owner's real store and corpora: 137,816 Leipzig
carriers read, 7,499 of them present in the translation store, and 7,365 of
those (98.2%) labelled ``source="tatoeba"``. A Leipzig sentence's text
cannot legitimately come FROM Tatoeba unless that exact sentence also exists
in Tatoeba, which for 25-160 character news prose is close to never. The
join is fixed in ``build_translations.py``; this script cleans up what the
broken version already wrote.

## Why removal, not repair

The correct English for those 7,365 sentences is not known. Nothing in the
store records which Tatoeba id was misread, and even if it did, the right
answer was never a Tatoeba sentence in the first place. Rewriting a record
would mean inventing an English gloss, which is precisely the class of bug
being cleaned up here. Removing the record instead leaves the sentence
absent from the store, which is a state the pipeline already handles: the
next ``build_translations.py`` run sees a carrier with no entry and machine
translates it properly.

## The selection rule

A record is contradictory when BOTH hold:

* its ``german`` text is a length-plausible carrier in the Leipzig corpus,
* and its ``source`` is ``"tatoeba"``.

Nothing else is touched -- not Leipzig records labelled ``azure``/``gemini``
(those are correct machine translations), not Tatoeba records (those are
what the id join legitimately produced), and not records whose German text
appears in neither corpus (``--carriers-from`` runs put those there).

``--tatoeba PATH`` narrows it by one honest exception: a Leipzig sentence
whose exact text ALSO appears in the Tatoeba corpus really can carry a
Tatoeba gloss, because the fixed join still matches those by text. Without
the flag such a record is removed and then re-added, identical, by the next
backfill -- harmless but perpetual. With it, the record is spared and
counted separately. The flag is opt-in rather than the default because the
selection rule the owner specified is the conservative one, and sparing a
record requires being sure the Tatoeba file given is the same corpus the
gloss came from.

## Writing

Same atomic write as ``build_translations.py``: a temp file in the store's
own directory, ``fsync``, then one ``os.replace``. The store is either the
complete old content or the complete new content at every instant, and a
killed run leaves the original file untouched. ``--dry-run`` writes nothing
at all -- not the store, not a backup, not a temp file -- and prints exactly
what a real run would have removed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.build_translations import (
    DEFAULT_LEIPZIG_PATH,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_SEED,
    DEFAULT_STORE_PATH,
    DEFAULT_TATOEBA_PATH,
    TranslationRecord,
    _load_store,
    _write_store_atomic,
)
from scripts.corpus_reading import SOURCE_LEIPZIG, SOURCE_TATOEBA, read_corpus_lines

#: How many removed records to print as examples. Enough to see the shape of
#: the damage without paging through thousands.
DEFAULT_EXAMPLES = 10


@dataclass
class PurgeResult:
    """What one purge found, whether or not it wrote anything."""

    store_size_before: int = 0
    store_size_after: int = 0
    leipzig_carriers_read: int = 0
    leipzig_carriers_in_store: int = 0
    removed: int = 0
    spared_text_matches: int = 0
    examples: list[tuple[str, str]] = field(default_factory=list)


def find_contradictory(
    store: dict[str, TranslationRecord],
    leipzig_texts: frozenset[str],
    tatoeba_texts: frozenset[str],
) -> list[str]:
    """Every German key in ``store`` whose record is a contradiction in
    terms: a Leipzig carrier labelled as having come from Tatoeba.

    ``tatoeba_texts`` is the exception set (module docstring, "The selection
    rule"); pass an empty set for the plain rule.
    """
    return [
        german
        for german, record in store.items()
        if record.source == SOURCE_TATOEBA
        and german in leipzig_texts
        and german not in tatoeba_texts
    ]


def purge(
    *,
    store_path: Path,
    leipzig_texts: frozenset[str],
    tatoeba_texts: frozenset[str] = frozenset(),
    dry_run: bool,
    example_limit: int = DEFAULT_EXAMPLES,
) -> PurgeResult:
    """Load the store, drop every contradictory record, and write the result
    back atomically unless ``dry_run``.

    Separate from ``main()`` and taking the corpora as plain text sets so a
    test drives it with a ``tmp_path`` store and no corpus files at all
    (CLAUDE.md section 8: filesystem access is an injected dependency where
    it can be). The store itself stays real I/O, because rewriting the store
    correctly is the thing under test.
    """
    store = _load_store(store_path)
    result = PurgeResult(store_size_before=len(store))
    result.leipzig_carriers_read = len(leipzig_texts)
    result.leipzig_carriers_in_store = sum(1 for text in store if text in leipzig_texts)

    contradictory = find_contradictory(store, leipzig_texts, tatoeba_texts)
    result.spared_text_matches = sum(
        1
        for german, record in store.items()
        if record.source == SOURCE_TATOEBA and german in leipzig_texts and german in tatoeba_texts
    )

    for german in contradictory[:example_limit]:
        result.examples.append((german, store[german].english))
    result.removed = len(contradictory)

    if dry_run:
        result.store_size_after = result.store_size_before
        return result

    for german in contradictory:
        del store[german]
    if contradictory:
        _write_store_atomic(store_path, store)
    result.store_size_after = len(store)
    return result


def _read_texts(path: Path, fmt: str, label: str, limit: int, seed: int, source: str) -> set[str]:
    """One corpus's length-plausible carrier texts, or an empty set with a
    warning if the file is missing.

    The SAME reader and the SAME length filter ``build_translations.py`` uses
    to decide what a carrier is, so "this store record is a Leipzig carrier"
    means here exactly what it meant when the record was written. A
    reimplemented filter here would purge a different set than the one the
    bug produced.
    """
    if not path.exists():
        print(f"  WARNING: {label} corpus not found at {path}.")
        return set()
    lines = read_corpus_lines(path, fmt, limit, seed, source=source)
    print(f"  {label}: {len(lines):,} length-plausible carriers read from {path}")
    return {line.text for line in lines}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove translation-store records whose German is a Leipzig carrier but "
            "whose source is 'tatoeba' -- the cross-corpus id collision fixed in "
            "build_translations.py."
        )
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument(
        "--tatoeba",
        type=Path,
        default=None,
        help=(
            "Optional. Spare records whose German text genuinely appears in Tatoeba "
            "too: a text match is legitimate even for a Leipzig carrier. Pass "
            f"{DEFAULT_TATOEBA_PATH} to enable it."
        ),
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT_PER_SOURCE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--examples", type=int, default=DEFAULT_EXAMPLES)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed and write nothing at all.",
    )
    args = parser.parse_args()

    if not args.store.exists():
        print(f"Translation store not found at {args.store}; nothing to purge.")
        return 1

    leipzig_texts = _read_texts(
        args.leipzig, "lines", "Leipzig", args.limit, args.seed, SOURCE_LEIPZIG
    )
    if not leipzig_texts:
        # Without the Leipzig corpus there is no way to tell a poisoned
        # record from a legitimate one, and "removed 0" would read as "the
        # store is clean". Refuse instead.
        print(
            "\nFAILING: no Leipzig carriers could be read, so no record can be "
            "identified as contradictory. This is not the same thing as a clean "
            "store. Point --leipzig at the corpus and re-run."
        )
        return 1

    tatoeba_texts: set[str] = set()
    if args.tatoeba is not None:
        tatoeba_texts = _read_texts(
            args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed, SOURCE_TATOEBA
        )

    result = purge(
        store_path=args.store,
        leipzig_texts=frozenset(leipzig_texts),
        tatoeba_texts=frozenset(tatoeba_texts),
        dry_run=args.dry_run,
        example_limit=args.examples,
    )

    contradictory_total = result.removed + result.spared_text_matches
    print(f"\n  {'Store:':<32}{args.store}")
    print(f"  {'Records before:':<32}{result.store_size_before:,}")
    print(f"  {'Leipzig carriers in store:':<32}{result.leipzig_carriers_in_store:,}")
    print(f"  {'...of those, source=tatoeba:':<32}{contradictory_total:,}")
    if args.tatoeba is not None:
        print(f"  {'...spared (genuine text match):':<32}{result.spared_text_matches:,}")
    label = "Would remove:" if args.dry_run else "Removed:"
    print(f"  {label:<32}{result.removed:,}")
    print(f"  {'Records remaining:':<32}{result.store_size_after:,}")

    if result.examples:
        print("\n  Examples of what this removes (German -> the English it was given):")
        for german, english in result.examples:
            print(f"    DE: {german}")
            print(f"    EN: {english!r}")
            print()

    if args.dry_run:
        print("  DRY RUN: nothing was written. Re-run without --dry-run to apply.")
    elif result.removed:
        print(
            "\n  Those sentences now have no gloss at all, which is the correct "
            "state: the next build_translations.py run machine translates them."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
