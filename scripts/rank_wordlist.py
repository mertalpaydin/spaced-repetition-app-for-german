"""Match a word list against the deck and write it back with each entry's rank.

The owner keeps lists from other tools (a Lingvist export, a course word
list). Matching one against the deck is the only outside check there is on
the mining and the ranking, which are otherwise judged only against the
corpus that produced them.

    uv run python scripts/rank_wordlist.py "Deutsch Words.csv"

Writes ``<name> ranked.csv`` beside the input and prints a coverage summary.
The input is one entry per line, the header row ignored; entries may be
single words or phrases, inflected or not.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from src.contracts import WORD_KINDS, PhraseUnit
from src.phrases.export import DEFAULT_DECK_DIR, load_deck
from src.phrases.parse import load_parser

REFLEXIVE = frozenset({"sich", "mich", "dich", "uns", "euch", "mir", "dir"})
#: Dropped when building a probe key: the deck keys on the lemmas of the
#: content words, so "es geht um" has to reach "gehen um" and "rechne ich
#: mit" has to reach "rechnen mit".
FILLER = REFLEXIVE | frozenset(
    {
        "es",
        "ich",
        "du",
        "er",
        "sie",
        "wir",
        "ihr",
        "man",
        "ihn",
        "ihm",
        "ihnen",
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "eines",
        "mein",
        "meine",
        "meinem",
        "meinen",
        "ihre",
        "ihren",
        "ihrem",
        "kein",
        "keine",
        "keinen",
        "keinem",
        "zu",
        "nicht",
        "viel",
    }
)


def clean(text: str) -> str:
    # The owner's exports carry non-breaking spaces; normalise them away or
    # "dieselbe\u00a0" never matches "dieselbe".
    text = unicodedata.normalize("NFC", text).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def stem_variants(tokens: list[str]) -> list[list[str]]:
    """The same tokens with one turned into a plausible infinitive.

    spaCy's small model leaves a first-person form as its own lemma, so
    "interessiere mich für" never reaches "sich interessieren für" without
    this."""
    out: list[list[str]] = []
    for i, token in enumerate(tokens):
        for guess in (token + "n", token + "en", re.sub(r"[ts]$", "en", token)):
            if guess != token:
                out.append([*tokens[:i], guess, *tokens[i + 1 :]])
    return out


@dataclass
class Index:
    """The deck, keyed every way an entry might be written."""

    by_key: dict[str, list[PhraseUnit]]
    by_sorted: dict[str, list[PhraseUnit]]
    by_token: dict[str, list[PhraseUnit]]

    @classmethod
    def of(cls, units: Iterable[PhraseUnit]) -> Index:
        by_key: dict[str, list[PhraseUnit]] = {}
        by_sorted: dict[str, list[PhraseUnit]] = {}
        by_token: dict[str, list[PhraseUnit]] = {}
        for unit in units:
            key = clean(unit.lemma_key).lower()
            content = [t for t in key.split() if t not in FILLER]
            for probe in {key, " ".join(content)} - {""}:
                by_key.setdefault(probe, []).append(unit)
                by_sorted.setdefault(" ".join(sorted(probe.split())), []).append(unit)
            for token in content:
                by_token.setdefault(token, []).append(unit)
        return cls(by_key, by_sorted, by_token)

    def find(self, lemmas: list[str], surfaces: list[str]) -> tuple[list[PhraseUnit], str]:
        content = [lem for lem, s in zip(lemmas, surfaces, strict=True) if s not in FILLER]
        for probe, label in (
            (" ".join(lemmas), "lemma key"),
            (" ".join(content), "content lemmas"),
            (" ".join(surfaces), "surface"),
        ):
            if probe and probe in self.by_key:
                return self.by_key[probe], label
        for probe in (" ".join(sorted(content)), " ".join(sorted(lemmas))):
            if probe and probe in self.by_sorted:
                return self.by_sorted[probe], "any order"
        for variant in stem_variants(content):
            if " ".join(variant) in self.by_key:
                return self.by_key[" ".join(variant)], "stem"
            if " ".join(sorted(variant)) in self.by_sorted:
                return self.by_sorted[" ".join(sorted(variant))], "stem, any order"
        if len(content) == 1 and content[0] in self.by_token:
            return self.by_token[content[0]], "inside a phrase"
        return [], ""


def classify(pos: list[str], surfaces: list[str], content: list[str]) -> str:
    has_verb = any(p in {"VERB", "AUX"} for p in pos)
    has_reflexive = any(s in REFLEXIVE for s in surfaces)
    if has_reflexive or len(content) > 1:
        if has_verb or has_reflexive or not any(p == "ADP" for p in pos):
            return "phrase"
        return "case drill"
    return "single verb" if has_verb else "single word"


def rank_entries(entries: list[str], index: Index) -> list[dict[str, object]]:
    nlp = load_parser()
    if nlp is None:
        raise RuntimeError("spaCy model de_core_news_sm is not installed; run `uv sync`")
    rows: list[dict[str, object]] = []
    for text, doc in zip(entries, nlp.pipe(entries), strict=True):
        tokens = [t for t in doc if not t.is_punct and not t.is_space]
        lemmas = [t.lemma_.lower() for t in tokens]
        surfaces = [t.text.lower() for t in tokens]
        content = [lem for lem, s in zip(lemmas, surfaces, strict=True) if s not in FILLER]
        candidates, how = index.find(lemmas, surfaces)
        unit = min(candidates, key=lambda u: u.rank) if candidates else None
        rows.append(
            {
                "word": text,
                "list_position": len(rows) + 1,
                "entry_kind": classify([t.pos_ for t in tokens], surfaces, content),
                "deck_rank": unit.rank if unit else "",
                "deck_unit": unit.display_de if unit else "",
                "deck_kind": unit.kind if unit else "",
                "match": how,
                "units_containing_it": len(candidates),
            }
        )
    return rows


def summarise(rows: list[dict[str, object]], write: object = print) -> None:
    out = write if callable(write) else print
    out(Counter(str(r["entry_kind"]) for r in rows).most_common())
    for name in ("phrase", "single verb", "single word", "case drill"):
        group = [r for r in rows if r["entry_kind"] == name]
        if not group:
            continue
        hit = [r for r in group if r["deck_rank"] != ""]
        own = [r for r in hit if r["match"] != "inside a phrase"]
        out(f"\n{name}: {len(group)}")
        out(f"  taught as a unit of its own  {len(own):5d}  ({len(own) / len(group):.0%})")
        out(f"  only inside another phrase   {len(hit) - len(own):5d}")
        out(f"  not in the deck              {len(group) - len(hit):5d}")
        if own:
            ranks = sorted(int(str(r["deck_rank"])) for r in own)
            out(f"  rank: median {ranks[len(ranks) // 2]}, 90th {ranks[int(len(ranks) * 0.9)]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wordlist", type=Path)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows_in = [
        clean(row[0])
        for row in csv.reader(args.wordlist.open(encoding="utf-8-sig"))
        if row and clean(row[0])
    ]
    entries = rows_in[1:]
    print(f"{len(entries)} entries (header {rows_in[0]!r})")

    _, units, _ = load_deck(args.deck)
    index = Index.of(units)
    words = sum(1 for u in units if u.kind in WORD_KINDS)
    print(f"deck: {len(units):,} units, {words:,} of them single words")

    rows = rank_entries(entries, index)
    out_path = args.out or args.wordlist.with_name(f"{args.wordlist.stem} ranked.csv")
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_path}\n")
    summarise(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
