"""Measure how usable Tatoeba's English translations are (TODO 5.1 step 1).

The owner's decision is that every exercise shows its English translation,
and that the verifier may then judge answer uniqueness with that translation
counted as visible to the learner. Both of those rest on the translations
being right. This script produces the number that decides whether the rest
of TODO 5.1 is worth building.

## What this can measure, and what it cannot

**Coverage** and **ambiguity** are exact: how many of our own German carriers
have an English translation at all, and how many have more than one.

**Quality is not machine-measurable here, and this script does not pretend
otherwise.** Tatoeba's translations are contributed by users and vary. The
export also does not mark INDIRECT translations (German to a third language
to English), which are the ones that drift, so indirectness cannot be read
off the data either. An earlier version of this plan proposed counting
indirect links as a proxy for quality; that is not available, and it was the
wrong instrument anyway. Hand-checking a sample measures quality directly,
which is what the proxy was standing in for.

So this script does the two things it honestly can:

1. Reports coverage and ambiguity exactly.
2. Flags pairs whose length ratio is implausible, which is a cheap, purely
   mechanical red flag for a mismatched or truncated translation, and is
   NOT a quality judgment on the rest.
3. Writes a seeded random sample to a file for hand-checking, which is the
   actual measurement.

## Input formats

Tatoeba offers the pairs two ways and this script accepts either:

* The **custom export** from https://tatoeba.org/en/downloads ("Download all
  sentences in language A that are translated into language B"), which
  carries both texts. Columns vary by export options, so the columns are
  detected rather than assumed: the script looks for the first two
  text-bearing columns and treats numeric-only columns as ids.
* A **links file** (``deu-eng_links.tsv``) plus an English sentences file
  (``eng_sentences.tsv``), joined here by sentence id.

Usage::

    uv run python -m scripts.eval_tatoeba_translation_quality \\
      --pairs data/raw/_extract/deu-eng_pairs.tsv \\
      --sample-out data/tatoeba_translation_sample.tsv

    uv run python -m scripts.eval_tatoeba_translation_quality \\
      --links data/raw/_extract/deu-eng_links.tsv \\
      --english data/raw/_extract/eng_sentences.tsv \\
      --sample-out data/tatoeba_translation_sample.tsv
"""

from __future__ import annotations

import argparse
import collections
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.corpus_reading import read_corpus_lines

DEFAULT_GERMAN_PATH = Path("data/raw/_extract/tatoeba_deu.tsv")

#: How many pairs to write out for hand-checking. Large enough that a defect
#: rate of a few percent is visible, small enough to read in one sitting.
DEFAULT_SAMPLE_SIZE = 120

#: A German sentence and its English translation should be within these
#: character-length ratios of each other. German runs longer than English on
#: average (compounds, longer function words), so the window is deliberately
#: asymmetric and deliberately wide: this is a mechanical tripwire for a
#: truncated or plainly mismatched pair, not a quality score. Anything inside
#: the window is not thereby judged good.
MIN_LENGTH_RATIO = 0.4
MAX_LENGTH_RATIO = 2.5


@dataclass(frozen=True)
class Pair:
    """One German sentence and one English translation of it."""

    german_id: str
    german: str
    english: str


def _looks_numeric(value: str) -> bool:
    return value.strip().isdigit()


def read_pairs_file(path: Path) -> list[Pair]:
    """Read a Tatoeba custom export, detecting which columns hold text.

    Column layout differs by export option (with or without ids, with or
    without language codes), so the layout is read off the first data row
    rather than assumed. Numeric-only columns are ids; the first two
    remaining columns are German and English in that order, which is the
    order the export writes when German is the source language.
    """
    pairs: list[Pair] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            ids = [p for p in parts if _looks_numeric(p)]
            texts = [p for p in parts if p.strip() and not _looks_numeric(p) and len(p) > 3]
            if len(texts) < 2:
                continue
            pairs.append(Pair(german_id=ids[0] if ids else "", german=texts[0], english=texts[1]))
    return pairs


def read_links_and_english(links_path: Path, english_path: Path, german_path: Path) -> list[Pair]:
    """Join a numeric links file against both sides' sentence files."""
    english_by_id: dict[str, str] = {}
    with english_path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) >= 3:
                english_by_id[parts[0]] = parts[2]

    german_by_id: dict[str, str] = {}
    with german_path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) >= 3:
                german_by_id[parts[0]] = parts[2]

    pairs: list[Pair] = []
    with links_path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            german = german_by_id.get(parts[0])
            english = english_by_id.get(parts[1])
            if german and english:
                pairs.append(Pair(german_id=parts[0], german=german, english=english))
    return pairs


def _length_ratio_implausible(pair: Pair) -> bool:
    if not pair.english:
        return True
    ratio = len(pair.german) / len(pair.english)
    return ratio < MIN_LENGTH_RATIO or ratio > MAX_LENGTH_RATIO


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, help="Tatoeba custom export with both texts.")
    parser.add_argument("--links", type=Path, help="deu-eng links file (numeric id pairs).")
    parser.add_argument("--english", type=Path, help="eng_sentences.tsv, needed with --links.")
    parser.add_argument("--german", type=Path, default=DEFAULT_GERMAN_PATH)
    parser.add_argument("--carrier-limit", type=int, default=40_000)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--sample-out", type=Path, default=Path("data/tatoeba_translation_sample.tsv")
    )
    args = parser.parse_args()

    if args.pairs:
        pairs = read_pairs_file(args.pairs)
        source = str(args.pairs)
    elif args.links and args.english:
        pairs = read_links_and_english(args.links, args.english, args.german)
        source = f"{args.links} joined with {args.english}"
    else:
        parser.error("give either --pairs, or both --links and --english")

    print(f"  Pairs read from {source}: {len(pairs):,}")

    by_german_id: dict[str, list[Pair]] = collections.defaultdict(list)
    by_german_text: dict[str, list[Pair]] = collections.defaultdict(list)
    for pair in pairs:
        if pair.german_id:
            by_german_id[pair.german_id].append(pair)
        by_german_text[pair.german].append(pair)

    multiple = sum(1 for group in by_german_text.values() if len(group) > 1)
    print(f"  Distinct German sentences:            {len(by_german_text):,}")
    print(
        f"  ...with more than one translation:    {multiple:,} "
        f"({100 * multiple / max(len(by_german_text), 1):.1f}%)"
    )

    # Coverage against the carriers this project actually reads, which is the
    # number that matters: a translation for a sentence we never use is worth
    # nothing.
    carriers = read_corpus_lines(args.german, "tatoeba", args.carrier_limit, args.seed)
    covered = [c for c in carriers if c.line_id in by_german_id or c.text in by_german_text]
    print(f"  Carriers checked (length-plausible):  {len(carriers):,}")
    print(
        f"  ...that have an English translation:  {len(covered):,} "
        f"({100 * len(covered) / max(len(carriers), 1):.1f}%)"
    )

    covered_pairs = [
        (by_german_id.get(c.line_id) or by_german_text.get(c.text) or [None])[0] for c in covered
    ]
    usable = [p for p in covered_pairs if p is not None]
    implausible = [p for p in usable if _length_ratio_implausible(p)]
    print(
        f"  ...whose length ratio is implausible: {len(implausible):,} "
        f"({100 * len(implausible) / max(len(usable), 1):.1f}%)  "
        f"[mechanical tripwire only, not a quality judgment]"
    )

    sample = random.Random(args.seed).sample(usable, min(args.sample_size, len(usable)))
    args.sample_out.parent.mkdir(parents=True, exist_ok=True)
    with args.sample_out.open("w", encoding="utf-8") as handle:
        handle.write("verdict\tgerman\tenglish\n")
        for pair in sample:
            handle.write(f"\t{pair.german}\t{pair.english}\n")
    print(f"\n  Hand-check sample written to {args.sample_out} ({len(sample)} pairs).")
    print("  Quality is NOT measured above. Reading that file is the measurement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
