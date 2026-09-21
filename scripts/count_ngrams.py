"""Count surface n-grams over the whole corpus, for the expression kind.

Fixed expressions are held together by usage, not by grammar, so they need
nothing from the dependency parse: only how often a word sequence occurs.
That makes this a separate light pass rather than part of the three-hour
parse stage, and it is why it can run over all four million sentences when
the parse stage's n-gram counting was confined to the glossed ones.

    uv run python scripts/count_ngrams.py

Writes ``data/phrases/build/ngram_counts.json``. Two minutes over the four
million sentences, measured 2026-09-21: it does no parsing at all.

The counter is bounded two ways, or it grows into the tens of millions of
entries: only tokens inside the commonest ``NGRAM_VOCAB_SIZE`` surface forms
are counted, since a fixed expression is built of common words, and entries
seen once are dropped at intervals.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from src.atomic_write import write_text_atomic
from src.lexicon.lemmatizer import normalise
from src.lexicon.vocabulary import VocabularyStore, _load_frequency_ranks
from src.phrases.mining import NGRAM_SIZES, NGRAM_VOCAB_SIZE
from src.phrases.units import EVERYDAY_SOURCES

from scripts.build_phrase_deck import (
    DEFAULT_BUILD_DIR,
    DEFAULT_EXTRA_CORPORA,
    DEFAULT_LEIPZIG_PATH,
    DEFAULT_LIMIT,
    DEFAULT_SEED,
    DEFAULT_TATOEBA_PATH,
    read_corpora,
)
from scripts.corpus_reading import default_corpus_path

#: A run of word characters; anything else ends the run, so an n-gram never
#: crosses a comma or a full stop.
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
PRUNE_EVERY = 250_000
FINAL_FLOOR = 30


@dataclass(frozen=True)
class NgramCounts:
    """What one pass over the corpus yields."""

    ngrams: Counter[str]
    surfaces: Counter[str]
    #: The same n-grams counted over the everyday corpora alone, so a
    #: candidate's register can be read off as a share. Counting the two
    #: everyday sources separately rather than all six keeps the memory cost
    #: to the fraction of the corpus they make up.
    everyday: Counter[str]


def count_ngrams(
    texts: Iterable[str] | Iterable[tuple[str, str]],
    vocabulary: frozenset[str],
    *,
    log: Callable[[str], None] | None = print,
) -> NgramCounts:
    """Count over the given sentences, each a ``text`` or a ``(source, text)``.

    A bare string counts towards the totals only; the source is what lets
    ``everyday_share`` tell "auf jeden Fall" from "Angaben zufolge".
    """
    ngrams: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    everyday: Counter[str] = Counter()
    started = time.monotonic()
    seen = 0
    for item in texts:
        source, text = ("", item) if isinstance(item, str) else item
        into = everyday if source in EVERYDAY_SOURCES else None
        seen += 1
        run: list[str] = []
        # The vocabulary is eszett-folded (``normalise``), so the text has to
        # be too: without this every word with an "ss" spelling breaks the
        # run and "soweit ich weiss" is never counted at all.
        lowered = text.lower()
        previous_end: int | None = None
        for match in _TOKEN.finditer(lowered):
            # Anything but a single space between two words ends the run, so
            # an n-gram never spans a comma, a full stop or a number.
            if previous_end is not None and lowered[previous_end : match.start()] != " ":
                _flush(run, ngrams, surfaces, into)
                run = []
            previous_end = match.end()
            word = normalise(match.group(0))
            if word in vocabulary:
                run.append(word)
                continue
            _flush(run, ngrams, surfaces, into)
            run = []
        _flush(run, ngrams, surfaces, into)
        if seen % PRUNE_EVERY == 0:
            before = len(ngrams)
            for key in [k for k, n in ngrams.items() if n < 2]:
                del ngrams[key]
            # The everyday table follows the main one, or the pruning that
            # bounds this pass would leave it growing unbounded.
            for key in [k for k in everyday if k not in ngrams]:
                del everyday[key]
            if log is not None:
                elapsed = (time.monotonic() - started) / 60
                kept = len(ngrams)
                log(f"  {seen:,} sentences, {before:,} -> {kept:,} n-grams, {elapsed:.1f} min")
    return NgramCounts(ngrams=ngrams, surfaces=surfaces, everyday=everyday)


def _flush(
    run: list[str],
    ngrams: Counter[str],
    surfaces: Counter[str],
    everyday: Counter[str] | None,
) -> None:
    for word in run:
        surfaces[word] += 1
    for size in NGRAM_SIZES:
        for start in range(max(len(run) - size + 1, 0)):
            key = " ".join(run[start : start + size])
            ngrams[key] += 1
            if everyday is not None:
                everyday[key] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--floor", type=int, default=FINAL_FLOOR)
    args = parser.parse_args(argv)

    ranks = _load_frequency_ranks(str(VocabularyStore.DEFAULT_FREQUENCY_RANKS_PATH))
    vocabulary = frozenset(word for word, rank in ranks.items() if rank <= NGRAM_VOCAB_SIZE)
    extra = [(name, default_corpus_path(file)) for name, file in DEFAULT_EXTRA_CORPORA]
    lines = read_corpora(args.tatoeba, args.leipzig, limit=args.limit, seed=args.seed, extra=extra)
    ordered = sorted(lines.values(), key=lambda line: (line.source, line.line_id))
    print(f"ngrams: {len(ordered):,} sentences, {len(vocabulary):,} words in the vocabulary")

    counts = count_ngrams(((line.source, line.text) for line in ordered), vocabulary)
    ngrams, surfaces = counts.ngrams, counts.surfaces
    for key in [k for k, n in ngrams.items() if n < args.floor]:
        del ngrams[key]
    everyday_sentences = sum(1 for line in ordered if line.source in EVERYDAY_SOURCES)
    payload = {
        "sentences": len(ordered),
        "everyday_sentences": everyday_sentences,
        "ngrams": dict(ngrams),
        "surfaces": dict(surfaces),
        # Only for the kept keys: the everyday table is a register signal,
        # not a second corpus, and writing all of it would double the file.
        "everyday": {key: counts.everyday[key] for key in ngrams if counts.everyday[key]},
    }
    out = args.build_dir / "ngram_counts.json"
    write_text_atomic(out, json.dumps(payload, ensure_ascii=False))
    print(f"ngrams: {len(ngrams):,} kept at count >= {args.floor} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
