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
        # Possessives and the finite forms of the auxiliaries. Without these
        # the head of the list at a low score floor is conjugation frames,
        # "habt ihr", "koennt ihr", "mein Vater", which are common because
        # German is, not because they are expressions.
        "mein",
        "meine",
        "meinen",
        "meinem",
        "meiner",
        "dein",
        "deine",
        "deinen",
        "deinem",
        "deiner",
        "seine",
        "seinen",
        "seinem",
        "seiner",
        "ihre",
        "ihren",
        "ihrem",
        "ihrer",
        "unser",
        "unsere",
        "euer",
        "eure",
        "hab",
        "habt",
        "seid",
        "wart",
        "werde",
        "wirst",
        "werdet",
        "würde",
        "würden",
        "kann",
        "kannst",
        "könnt",
        "können",
        "muss",
        "musst",
        "müsst",
        "müssen",
        "will",
        "willst",
        "wollt",
        "wollen",
        "soll",
        "sollst",
        "sollt",
        "sollen",
        "gibt",
        "gib",
        "lass",
        "lasst",
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


def _carries_vocabulary(tokens: list[str]) -> bool:
    """Whether the sequence teaches anything beyond grammar.

    A pair must be two content words: at a low score floor the two-word
    candidates are otherwise dominated by frames ("jeden Tag" earns its
    place, "habt ihr" does not). From three words up one content word is
    enough, because the frame is then part of what is learned: "zum ersten
    Mal", "soweit ich weiss".
    """
    if len(tokens) < 2:
        return False
    if len(tokens) == 2:
        return all(token not in FUNCTION_WORDS for token in tokens)
    return any(token not in FUNCTION_WORDS for token in tokens)


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
        if not _carries_vocabulary(tokens):
            continue
        if text in known:
            continue
        score = min_split_pmi(text, count, ngrams, surfaces, sentences)
        if score < min_score:
            continue
        out.append(Expression(text=text, count=count, score=score))
    out.sort(key=lambda e: (-e.score, -e.count, e.text))
    return out[:limit] if limit is not None else out


def extend_to_longest(
    expressions: list[Expression],
    ngrams: Counter[str],
    *,
    ratio: float = 0.9,
) -> list[Expression]:
    """Grow each candidate to the longest span it almost always occurs in.

    The min-PMI measure is biased towards the short span: "in erster Linie"
    scores below the floor only because "in" is so common that its seam is
    weak, while the bare "erster Linie" sails through. The corpus settles it
    without any measure at all. "erster Linie" occurs 766 times and "in
    erster Linie" 763, so the preposition is not optional and the expression
    is the longer one. "Krankenhaus gebracht" (1,088) against "ins
    Krankenhaus gebracht" (503) is the opposite case, and is left alone.

    ``ratio`` is that "almost always": an extension is taken only when it
    accounts for at least this share of the shorter span's count.
    """
    index = _boundary_index(ngrams)
    grown: dict[str, Expression] = {}
    for candidate in expressions:
        text, count = candidate.text, candidate.count
        while True:
            longer = _best_extension(text, count, index, ngrams, ratio)
            if longer is None:
                break
            text, count = longer, ngrams[longer]
        # Two candidates can grow into the same span ("erster Linie" and
        # "in erster"); keep the one that scored best, which is first here.
        grown.setdefault(text, Expression(text=text, count=count, score=candidate.score))
    return sorted(grown.values(), key=lambda e: (-e.score, -e.count, e.text))


def _boundary_index(ngrams: Counter[str]) -> dict[str, list[str]]:
    """Every n-gram filed under the two spans it extends by one word, so an
    extension is a lookup rather than a scan of the whole table."""
    index: dict[str, list[str]] = {}
    for text in ngrams:
        tokens = text.split()
        if len(tokens) < 2:
            continue
        for shorter in (" ".join(tokens[:-1]), " ".join(tokens[1:])):
            index.setdefault(shorter, []).append(text)
    return index


def _best_extension(
    text: str, count: int, index: dict[str, list[str]], ngrams: Counter[str], ratio: float
) -> str | None:
    """The commonest n-gram that adds one word to either end of ``text`` and
    still accounts for ``ratio`` of its occurrences."""
    floor = count * ratio
    best: str | None = None
    for longer in index.get(text, ()):
        longer_count = ngrams[longer]
        if longer_count < floor:
            continue
        if best is None or longer_count > ngrams[best]:
            best = longer
    return best


def everyday_share(text: str, ngrams: Counter[str], everyday: Counter[str]) -> float:
    """The share of an n-gram's occurrences that come from everyday speech.

    Without this the head of the list is Leipzig news boilerplate, "Angaben
    zufolge" and "unbekannte Taeter", which is real German and not what a
    learner needs first. Every other mined kind already has this gate.
    """
    total = ngrams.get(text, 0)
    return everyday.get(text, 0) / total if total else 0.0


def choose_expressions(
    ngrams: Counter[str],
    surfaces: Counter[str],
    everyday: Counter[str],
    *,
    sentences: int,
    dictionary: frozenset[str],
    known_keys: Iterable[str] = (),
    min_count: int = 30,
    min_score: float = 6.0,
    min_everyday_share: float = 0.05,
    deny: Iterable[str] = (),
) -> list[Expression]:
    """Every gate, in the order that makes each one cheap.

    Score, then grow to the longest span the corpus insists on, then drop
    what is contained in a better candidate, then the two gates that no
    measure can stand in for: every token a German dictionary word, which is
    what kills "buenos aires" and "wall street" at the head of the PMI
    ranking, and a minimum share of everyday register.
    """
    denied = {text.lower() for text in deny}
    chosen = drop_contained(
        extend_to_longest(
            select_expressions(
                ngrams,
                surfaces,
                sentences=sentences,
                min_count=min_count,
                min_score=min_score,
                known_keys=known_keys,
            ),
            ngrams,
        )
    )
    return [
        candidate
        for candidate in chosen
        if candidate.text not in denied
        and all(token in dictionary for token in candidate.tokens)
        and everyday_share(candidate.text, ngrams, everyday) >= min_everyday_share
    ]


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
