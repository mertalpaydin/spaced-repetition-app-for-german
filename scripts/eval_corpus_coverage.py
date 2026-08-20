"""Measure how many exercise candidates a text corpus yields per topic (TODO 4.3).

The question this answers, with a number rather than an opinion: **does taking
sentences from a corpus revive the topics that model generation cannot
reach?** Ten of the 49 topics ended cycle 9 with zero items, every one of them
because no generated carrier contained the construction. A construction that
occurs in 0.01 percent of natural sentences still yields dozens of hits in
several hundred thousand, which is the whole argument for retrieval over
generation on rare grammar.

It reads a plain corpus file, applies the same carrier validation and the same
49 selectors the generation pipeline uses, and reports candidates per topic and
per CEFR band. Nothing here is corpus specific beyond the reader: the point is
to run the EXISTING pipeline over corpus text so the numbers are comparable to
a pilot run's, not to build a second pipeline.

Supported input formats, chosen by ``--format``:

- ``lines``   one sentence per line. Leipzig's ``*-sentences.txt`` is
              ``<id>\\t<sentence>``, which this handles by taking everything
              after the first tab when a tab is present.
- ``tatoeba`` Tatoeba's per-language export, ``<id>\\t<lang>\\t<sentence>``.

Neither corpus is vendored: Tatoeba is roughly 700k sentences (CC BY 2.0 FR)
and Leipzig's 1M-sentence German news corpus is 226 MB compressed. CLAUDE.md
section 7 keeps ``data/fixtures/`` for small golden sets, so both stay outside
the repo and this script takes a path.

Usage::

    uv run python -m scripts.eval_corpus_coverage tatoeba_deu.tsv \\
        --format tatoeba --limit 150000

Sentence-length filtering is applied before tagging, for the same reason the
tagger evaluation applies it: a 40-token news sentence full of proper nouns is
not a carrier this product would ever use, and counting candidates in one
would overstate coverage.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

from src.generation.blanking import carrier_validation
from src.generation.blanking.pipeline import blank_sentences
from src.generation.blanking.selectors import SELECTORS
from src.lexicon.vocabulary import VocabularyStore

DEFAULT_VOCAB_PATH = Path("data/fixtures/corpus/vocab_levels.json")

# Carrier-plausible bounds, matching scripts/eval_tagger_vs_gold.py so the two
# measurements are talking about the same kind of sentence.
MIN_CHARS = 25
MAX_CHARS = 160
MIN_WORDS = 5
MAX_WORDS = 18


def read_sentences(path: Path, fmt: str, limit: int, seed: int) -> list[str]:
    """Read up to ``limit`` length-plausible sentences from ``path``.

    Reads the whole file, filters, then samples, rather than taking the first
    ``limit`` lines: both corpora are ordered (Tatoeba by contribution id,
    Leipzig by source document), so a prefix is not a sample of the corpus.
    """
    raw: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.rstrip("\n")
            if not text:
                continue
            if fmt == "tatoeba":
                parts = text.split("\t")
                if len(parts) < 3:
                    continue
                text = parts[2]
            elif "\t" in text:
                text = text.split("\t", 1)[1]
            if not _plausible(text):
                continue
            raw.append(text)
    random.Random(seed).shuffle(raw)
    return raw[:limit]


def _plausible(text: str) -> bool:
    """Cheap pre-tagging filter: length, terminal punctuation, no markup.

    Deliberately crude. Real judgement is carrier validation's job; this only
    exists so the expensive tagging pass is not spent on obvious junk such as
    Leipzig's price fragments and table rows.
    """
    if not (MIN_CHARS <= len(text) <= MAX_CHARS):
        return False
    if not text.endswith((".", "!", "?")):
        return False
    if not (MIN_WORDS <= len(text.split()) <= MAX_WORDS):
        return False
    # A sentence that opens mid-thought, or carries markup or tabular debris,
    # cannot stand alone as a carrier no matter how well it tags.
    return not any(ch in text for ch in "|<>[]{}\t")


def main() -> int:
    parser = argparse.ArgumentParser(description="Corpus coverage per grammar topic.")
    parser.add_argument("corpus", type=Path, help="Path to the corpus file.")
    parser.add_argument("--format", choices=("lines", "tatoeba"), default="lines")
    parser.add_argument("--limit", type=int, default=100000, help="Sentences to tag.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--max-items-per-topic",
        type=int,
        default=10**9,
        help="Default is effectively uncapped: this measures what the corpus CONTAINS, "
        "so a balance cap would hide exactly the number being measured.",
    )
    parser.add_argument("--max-items-per-sentence", type=int, default=10**9)
    parser.add_argument(
        "--cefr-ceiling",
        choices=("A1", "A2", "B1", "B2"),
        default=None,
        help="Drop sentences whose vocabulary is above this level, using the same "
        "budgeted rule the generation pipeline applies (VocabularyStore."
        "check_ceiling_budget). Omit to measure the corpus without a level filter.",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Write counts here.")
    args = parser.parse_args()

    sentences = read_sentences(args.corpus, args.format, args.limit, args.seed)
    print(f"sentences read and length-filtered: {len(sentences)}")

    level_rejected = 0
    if args.cefr_ceiling:
        # Applied BEFORE tagging, deliberately: it is far cheaper than spaCy
        # and it is the filter that decides whether a corpus sentence could
        # ever be used at all, so measuring coverage after it is the number
        # that actually matters for the product.
        store = VocabularyStore.load(DEFAULT_VOCAB_PATH)
        kept = [s for s in sentences if not store.validate_sentence(s, args.cefr_ceiling)]
        level_rejected = len(sentences) - len(kept)
        share = 100 * level_rejected / len(sentences) if sentences else 0.0
        print(
            f"above the {args.cefr_ceiling} vocabulary ceiling: {level_rejected} "
            f"({share:.1f}%), leaving {len(kept)}"
        )
        sentences = kept

    validation = carrier_validation.validate_carriers(sentences)
    print(f"carrier-valid: {len(validation.accepted)}")
    print("rejected by reason:")
    for reason, count in validation.rejected_by_reason.most_common():
        print(f"  {reason:38s} {count}")

    accepted = [s if isinstance(s, str) else s.text for s in validation.accepted]
    report = blank_sentences(
        accepted,
        max_items_per_topic=args.max_items_per_topic,
        max_items_per_sentence=args.max_items_per_sentence,
    )

    by_topic = collections.Counter(item.topic_id for item in report.items)
    print(f"\nitems: {len(report.items)}   topics with items: {len(by_topic)} of {len(SELECTORS)}")
    print("\nper topic:")
    for topic in sorted(SELECTORS):
        print(f"  {topic:38s} {by_topic.get(topic, 0):6d}")

    empty = sorted(t for t in SELECTORS if not by_topic.get(t))
    print(f"\ntopics with ZERO candidates: {len(empty)}")
    for topic in empty:
        print(f"  - {topic}")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {
                    "corpus": str(args.corpus),
                    "cefr_ceiling": args.cefr_ceiling,
                    "above_ceiling": level_rejected,
                    "sentences_tagged": len(sentences),
                    "carrier_valid": len(validation.accepted),
                    "items": len(report.items),
                    "by_topic": dict(by_topic),
                    "empty_topics": empty,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
