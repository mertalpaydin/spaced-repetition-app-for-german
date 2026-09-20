"""Fixed expressions, mined from surface n-grams.

"Soweit ich weiß", "auf jeden Fall", "mit anderen Worten": word sequences
that are learned whole and that none of the other detectors can see, because
they are held together by usage rather than by a dependency relation.

The measure is the minimum pointwise mutual information over every binary
split of the n-gram. A real fixed expression is surprising at every seam;
"in der Stadt" is not, because "in der" is merely a common article frame and
"der Stadt" a common noun phrase, so one of its splits scores low. Taking
the minimum is what separates the two, and a mean or a single split does not.

The counts come from the parse stage, over the glossed sentences only, since
those are the only sentences an expression card could use.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

#: An expression made only of function words teaches nothing: it is a frame
#: ("in der", "es ist ein"), not vocabulary. At least one token must be a
#: content word.
FUNCTION_WORDS: frozenset[str] = frozenset(
    {
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
        "kein",
        "keine",
        "keinen",
        "keinem",
        "keiner",
        "ich",
        "du",
        "er",
        "sie",
        "es",
        "wir",
        "ihr",
        "man",
        "mich",
        "dich",
        "sich",
        "mir",
        "dir",
        "ihm",
        "ihn",
        "ihnen",
        "uns",
        "euch",
        "wer",
        "wen",
        "wem",
        "und",
        "oder",
        "aber",
        "denn",
        "dass",
        "ob",
        "wenn",
        "weil",
        "als",
        "wie",
        "in",
        "an",
        "auf",
        "aus",
        "bei",
        "mit",
        "nach",
        "von",
        "vor",
        "zu",
        "zum",
        "zur",
        "im",
        "am",
        "ins",
        "ans",
        "beim",
        "vom",
        "um",
        "für",
        "über",
        "unter",
        "durch",
        "gegen",
        "ohne",
        "bis",
        "seit",
        "hinter",
        "neben",
        "ist",
        "sind",
        "war",
        "waren",
        "bin",
        "bist",
        "hat",
        "habe",
        "hast",
        "haben",
        "hatte",
        "hatten",
        "wird",
        "werden",
        "wurde",
        "wurden",
        "sein",
        "nicht",
        "noch",
        "nur",
        "auch",
        "schon",
        "so",
        "da",
        "dann",
        "hier",
        "mehr",
        "sehr",
        "immer",
        "ja",
        "nein",
        "doch",
        "mal",
        "etwas",
        "alle",
        "was",
        "wo",
        "wann",
        "warum",
        "wieder",
        "jetzt",
        "dies",
        "diese",
        "dieser",
    }
)


@dataclass(frozen=True)
class Expression:
    """One candidate, with what earned it a place."""

    text: str
    count: int
    #: The weakest seam: the minimum PMI over the binary splits.
    score: float

    @property
    def tokens(self) -> list[str]:
        return self.text.split()


def _pmi(pair: int, left: int, right: int, total: int) -> float:
    """Pointwise mutual information of the two halves, in bits."""
    if pair <= 0 or left <= 0 or right <= 0 or total <= 0:
        return 0.0
    expected = (left / total) * (right / total)
    if expected <= 0.0:
        return 0.0
    return math.log2((pair / total) / expected)


def min_split_pmi(
    text: str, count: int, counts: Counter[str], surfaces: Counter[str], total: int
) -> float:
    """The lowest PMI over every way of cutting the n-gram in two.

    A four-word expression is cut 1+3, 2+2 and 3+1; each half's count comes
    from the unigram table or the n-gram table. ``0.0`` when a half was
    pruned out of the counts, which only happens for a rare n-gram.
    """
    tokens = text.split()
    if len(tokens) < 2:
        return 0.0

    def frequency(part: list[str]) -> int:
        if len(part) == 1:
            return surfaces.get(part[0], 0)
        return counts.get(" ".join(part), 0)

    scores = []
    for cut in range(1, len(tokens)):
        left, right = frequency(tokens[:cut]), frequency(tokens[cut:])
        if not left or not right:
            return 0.0
        scores.append(_pmi(count, left, right, total))
    return min(scores)


def select_expressions(
    ngrams: Counter[str],
    surfaces: Counter[str],
    *,
    sentences: int,
    min_count: int = 30,
    min_score: float = 6.0,
    known_keys: Iterable[str] = (),
    limit: int | None = None,
) -> list[Expression]:
    """The n-grams worth teaching whole, best first.

    ``known_keys`` are the lemma keys the other detectors already produce, so
    an expression never duplicates a unit that exists.
    """
    known = {key.lower() for key in known_keys}
    out: list[Expression] = []
    for text, count in ngrams.items():
        if count < min_count:
            continue
        tokens = text.split()
        if len(tokens) < 2 or all(token in FUNCTION_WORDS for token in tokens):
            continue
        if text in known:
            continue
        score = min_split_pmi(text, count, ngrams, surfaces, sentences)
        if score < min_score:
            continue
        out.append(Expression(text=text, count=count, score=score))
    out.sort(key=lambda e: (-e.score, -e.count, e.text))
    return out[:limit] if limit is not None else out


def drop_contained(expressions: list[Expression]) -> list[Expression]:
    """Drop a candidate that is a part of a better one with a similar count.

    "jeden Fall" and "auf jeden Fall" both score; only the longer is the
    expression, and only when the shorter adds nothing.
    """
    kept: list[Expression] = []
    for candidate in expressions:
        contained = False
        for other in kept:
            if candidate.text in other.text and candidate.count <= other.count * 1.25:
                contained = True
                break
        if not contained:
            kept.append(candidate)
    return kept
