"""Measure ``de_core_news_sm`` against gold morphology (TODO 4.2).

Compares spaCy's tags, lemmas and morphological features against
UD_German-HDT's hand annotation, on exactly the features the blanking
selectors read, so that "spaCy mistagged it" is a measured number rather than
an anecdote. The findings from the first run are written up in
``docs/audits/tagger-accuracy-vs-gold.md``.

The treebank is NOT vendored: it is roughly 260 MB across six files, and
CLAUDE.md section 7 keeps ``data/fixtures/`` for small golden sets. Fetch one
file first (CC BY-SA 4.0):

    curl -sSL -o hdt.conllu \\
      https://raw.githubusercontent.com/UniversalDependencies/UD_German-HDT/master/de_hdt-ud-train-a-1.conllu

then::

    uv run python -m scripts.eval_tagger_vs_gold hdt.conllu

A **conflicting** value (spaCy asserts something different from gold) and a
**missing** one (spaCy asserts nothing where gold has a value) are reported
separately, deliberately: the selectors already skip a candidate whose
paradigm cell cannot be determined, so a missing feature costs coverage,
while a conflicting one is what actually ships a defect.
"""

from __future__ import annotations

import argparse
import collections
import random
from pathlib import Path

import spacy

# The morphological features the blanking selectors actually read. Anything
# outside this list is not compared, because a disagreement there cannot
# reach an item.
COMPARED_FEATURES = ("Case", "Gender", "Number", "Tense", "Mood", "Person")

# Carrier-comparable lengths. HDT is technology journalism and its long
# nominal sentences are not what this pipeline generates, so comparing on
# everything would measure a text type we never see.
MIN_TOKENS = 5
MAX_TOKENS = 18

GoldToken = dict[str, object]
GoldSentence = tuple[str, list[GoldToken]]


def read_conllu(path: Path, limit: int) -> list[GoldSentence]:
    """Parse ``path`` into (surface text, gold tokens), stopping at ``limit``.

    Multi-word-token ranges (``1-2``) and empty nodes (``1.1``) are skipped:
    they carry no morphology of their own and would not align with spaCy's
    tokens.
    """
    sentences: list[GoldSentence] = []
    tokens: list[GoldToken] = []
    text: str | None = None
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.startswith("# text = "):
                text = line[len("# text = ") :]
            elif not line.strip():
                if tokens and text:
                    sentences.append((text, tokens))
                tokens, text = [], None
                if len(sentences) >= limit:
                    break
            elif not line.startswith("#"):
                parts = line.split("\t")
                if "-" in parts[0] or "." in parts[0]:
                    continue
                feats: dict[str, str] = {}
                if parts[5] != "_":
                    for pair in parts[5].split("|"):
                        key, _, value = pair.partition("=")
                        feats[key] = value
                tokens.append(
                    {"form": parts[1], "lemma": parts[2], "xpos": parts[4], "feats": feats}
                )
    return sentences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("conllu", type=Path, help="Path to a UD_German-HDT .conllu file.")
    parser.add_argument("--sentences", type=int, default=3000, help="How many to compare.")
    parser.add_argument("--seed", type=int, default=7, help="Sampling seed.")
    args = parser.parse_args()

    pool = read_conllu(args.conllu, limit=40000)
    random.Random(args.seed).shuffle(pool)
    sentences = [s for s in pool if MIN_TOKENS <= len(s[1]) <= MAX_TOKENS][: args.sentences]
    print(f"sentences compared: {len(sentences)}")

    nlp = spacy.load("de_core_news_sm")
    total: collections.Counter[str] = collections.Counter()
    conflict: collections.Counter[str] = collections.Counter()
    missing: collections.Counter[str] = collections.Counter()
    dirty_sentences = 0
    punctuation_lemma_diffs = 0

    for text, gold in sentences:
        spacy_tokens = [t for t in nlp(text) if not t.is_space]
        # Misaligned tokenisation is skipped rather than guessed at: pairing
        # up tokens that do not correspond would invent disagreements.
        if len(spacy_tokens) != len(gold):
            continue
        dirty = False
        for gold_token, token in zip(gold, spacy_tokens, strict=True):
            gold_xpos = str(gold_token["xpos"])
            gold_lemma = str(gold_token["lemma"])
            gold_feats = gold_token["feats"]
            assert isinstance(gold_feats, dict)

            total["xpos"] += 1
            if gold_xpos != token.tag_:
                conflict["xpos"] += 1
                dirty = True

            total["lemma"] += 1
            if gold_lemma.lower() != token.lemma_.lower():
                conflict["lemma"] += 1
                dirty = True
                # spaCy lemmatises every punctuation mark to "--". That is a
                # pure annotation convention, not an error, and it is large
                # enough to distort the headline lemma number.
                if gold_xpos.startswith("$"):
                    punctuation_lemma_diffs += 1

            morph = token.morph.to_dict()
            for feature in COMPARED_FEATURES:
                if feature not in gold_feats:
                    continue
                total[feature] += 1
                value = morph.get(feature)
                if value is None:
                    missing[feature] += 1
                    dirty = True
                elif value != gold_feats[feature]:
                    conflict[feature] += 1
                    dirty = True
        if dirty:
            dirty_sentences += 1

    share = 100 * dirty_sentences / len(sentences) if sentences else 0.0
    print(f"sentences with at least one spaCy disagreement: {dirty_sentences} ({share:.1f}%)")

    lemma_total = total["lemma"]
    lemma_bad = conflict["lemma"]
    lemma_bad_no_punct = lemma_bad - punctuation_lemma_diffs
    lemma_total_no_punct = lemma_total - punctuation_lemma_diffs
    print(f"  xpos     {lemma_pct(conflict['xpos'], total['xpos'])} conflicting")
    print(
        f"  lemma    {lemma_pct(lemma_bad, lemma_total)} conflicting, "
        f"{lemma_pct(lemma_bad_no_punct, lemma_total_no_punct)} excluding punctuation"
    )
    for feature in COMPARED_FEATURES:
        if not total[feature]:
            continue
        print(
            f"  {feature:8s} {lemma_pct(conflict[feature], total[feature])} conflicting, "
            f"{lemma_pct(missing[feature], total[feature])} missing"
        )
    return 0


def lemma_pct(count: int, total_count: int) -> str:
    """``count/total`` as a fixed-width "N (X.XX%)" string, or "n/a" at zero."""
    if not total_count:
        return "n/a"
    return f"{count:6d}/{total_count:6d} ({100 * count / total_count:5.2f}%)"


if __name__ == "__main__":
    raise SystemExit(main())
