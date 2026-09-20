"""Every detector over one parsed sentence, plus the lemma counts the
association measure needs."""

from collections import Counter
from dataclasses import dataclass, field

from src.contracts import WORD_KINDS
from src.phrases.curated import CuratedLists
from src.phrases.mining.collocations import detect_adj_noun, detect_adj_verb, detect_noun_verb
from src.phrases.mining.curated_match import detect_connectors, detect_idioms
from src.phrases.mining.verbs import (
    detect_reflexive,
    detect_separable,
    detect_verb_prep,
    verb_prep_candidates,
)
from src.phrases.mining.words import WordGate, detect_words, word_lemmas
from src.phrases.occurrences import Occurrence
from src.phrases.parse import ParsedSentence, has_konjunktiv_i, verb_lemma_key

#: n-grams are counted only over tokens common enough to build a fixed
#: expression ("soweit ich weiss", "auf jeden Fall"). Without this bound the
#: counter grows into the tens of millions of entries and the parse stage
#: runs out of memory.
NGRAM_VOCAB_SIZE = 20_000
NGRAM_SIZES = (2, 3, 4)
#: Entries seen once are dropped this often, so the counter stays bounded.
NGRAM_PRUNE_EVERY = 50_000


@dataclass
class LemmaCounts:
    """Sentence counts per lemma, by role, for lift and log-likelihood.

    ``word_by_source`` is the per-corpus count for the single-word kinds.
    Their occurrences are capped at parse time (see ``mining.words``), so the
    occurrence tally would understate them; the ranking reads these instead.
    """

    sentences: int = 0
    sentences_by_source: Counter[str] = field(default_factory=Counter)
    verbs: Counter[str] = field(default_factory=Counter)
    nouns: Counter[str] = field(default_factory=Counter)
    adjectives: Counter[str] = field(default_factory=Counter)
    adverbs: Counter[str] = field(default_factory=Counter)
    prepositions: Counter[str] = field(default_factory=Counter)
    #: "{kind}:{lemma}" -> {corpus source: sentences}
    word_by_source: dict[str, Counter[str]] = field(default_factory=dict)
    #: Surface unigrams and 2-to-4-grams over the common vocabulary, for the
    #: expression kind. Empty unless ``count_ngrams`` was asked for.
    surfaces: Counter[str] = field(default_factory=Counter)
    ngrams: Counter[str] = field(default_factory=Counter)

    #: Lemmas worth a single-word unit; everything else is not counted per
    #: source. Set by the parse stage, not persisted.
    gate: WordGate | None = None
    count_ngrams: bool = False
    ngram_vocab: frozenset[str] = frozenset()
    ngram_sentences: int = 0

    def add(self, sentence: ParsedSentence, source: str = "") -> None:
        self.sentences += 1
        if source:
            self.sentences_by_source[source] += 1
        verbs: set[str] = set()
        nouns: set[str] = set()
        adjs: set[str] = set()
        advs: set[str] = set()
        preps: set[str] = set()
        for t in sentence.tokens:
            if t.pos == "VERB":
                key = verb_lemma_key(sentence, t)
                if key:
                    verbs.add(key)
            elif t.pos == "AUX" and t.lemma.lower() == "haben":
                verbs.add("haben")
            elif t.pos == "NOUN":
                nouns.add(t.lemma.lower())
            elif t.pos == "ADJ":
                adjs.add(t.lemma.lower())
            elif t.pos == "ADV":
                advs.add(t.lemma.lower())
            elif t.pos == "ADP":
                preps.add(t.lower)
        self.verbs.update(verbs)
        self.nouns.update(nouns)
        self.adjectives.update(adjs)
        self.adverbs.update(advs)
        self.prepositions.update(preps)
        if self.gate is not None and source:
            self._count_words(sentence, source)
        # Only the glossed sentences: they are the only ones an expression
        # card could ever use, and counting over all four million would hold
        # several million singleton n-grams in memory at once.
        if self.count_ngrams and self.gate is not None and sentence.text in self.gate.glossed:
            self._count_ngrams(sentence)

    def _count_words(self, sentence: ParsedSentence, source: str) -> None:
        for kind, lemma in word_lemmas(sentence, self.gate):
            bucket = self.word_by_source.get(f"{kind}:{lemma}")
            if bucket is None:
                bucket = Counter()
                self.word_by_source[f"{kind}:{lemma}"] = bucket
            bucket[source] += 1

    def _count_ngrams(self, sentence: ParsedSentence) -> None:
        run: list[str] = []
        for token in [*sentence.tokens, None]:
            word = token.lower if token is not None else ""
            if token is not None and word in self.ngram_vocab:
                run.append(word)
                continue
            for size in NGRAM_SIZES:
                for start in range(0, max(len(run) - size + 1, 0)):
                    self.ngrams[" ".join(run[start : start + size])] += 1
            for word_in_run in run:
                self.surfaces[word_in_run] += 1
            run = []
        self.ngram_sentences += 1
        if self.ngram_sentences % NGRAM_PRUNE_EVERY == 0:
            self.prune_ngrams()

    def prune_ngrams(self, floor: int = 2) -> None:
        """Drop the n-grams seen fewer than ``floor`` times. Lossy by design:
        a fixed expression survives any window, a one-off sequence does not."""
        for key in [k for k, n in self.ngrams.items() if n < floor]:
            del self.ngrams[key]

    def to_dict(self) -> dict[str, object]:
        return {
            "sentences": self.sentences,
            "sentences_by_source": dict(self.sentences_by_source),
            "verbs": dict(self.verbs),
            "nouns": dict(self.nouns),
            "adjectives": dict(self.adjectives),
            "adverbs": dict(self.adverbs),
            "prepositions": dict(self.prepositions),
            "word_by_source": {k: dict(v) for k, v in self.word_by_source.items()},
            "surfaces": dict(self.surfaces),
            "ngrams": dict(self.ngrams),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "LemmaCounts":
        def counter(key: str) -> Counter[str]:
            value = raw.get(key, {})
            assert isinstance(value, dict)
            return Counter({str(k): int(v) for k, v in value.items()})

        def nested(key: str) -> dict[str, Counter[str]]:
            value = raw.get(key, {})
            assert isinstance(value, dict)
            out: dict[str, Counter[str]] = {}
            for k, v in value.items():
                assert isinstance(v, dict)
                out[str(k)] = Counter({str(kk): int(vv) for kk, vv in v.items()})
            return out

        sentences = raw.get("sentences", 0)
        assert isinstance(sentences, int)
        return cls(
            sentences=sentences,
            sentences_by_source=counter("sentences_by_source"),
            verbs=counter("verbs"),
            nouns=counter("nouns"),
            adjectives=counter("adjectives"),
            adverbs=counter("adverbs"),
            prepositions=counter("prepositions"),
            word_by_source=nested("word_by_source"),
            surfaces=counter("surfaces"),
            ngrams=counter("ngrams"),
        )

    def role_counts(self, kind: str) -> Counter[str]:
        """The sentence counts for one single-word kind."""
        return {
            "noun": self.nouns,
            "verb": self.verbs,
            "adjective": self.adjectives,
            "adverb": self.adverbs,
        }[kind]


def detect_all(
    sentence: ParsedSentence,
    curated: CuratedLists,
    *,
    source: str,
    line_id: str,
    dictionary: frozenset[str] | None,
    words: WordGate | None = None,
) -> list[Occurrence]:
    candidates = verb_prep_candidates(sentence, source=source, line_id=line_id)
    found = [occ for occ in candidates if occ.evidence.get("reflexive") != "true"]
    found += detect_reflexive(sentence, candidates, source=source, line_id=line_id)
    found += detect_separable(sentence, source=source, line_id=line_id, dictionary=dictionary)
    found += detect_noun_verb(sentence, source=source, line_id=line_id)
    found += detect_adj_verb(sentence, source=source, line_id=line_id)
    found += detect_adj_noun(sentence, source=source, line_id=line_id)
    governed = frozenset(seed.key for seed in curated.verb_prep_seeds)
    found += detect_connectors(
        sentence, curated.connectors, source=source, line_id=line_id, governed=governed
    )
    found += detect_idioms(sentence, curated.idioms, source=source, line_id=line_id)
    if words is not None:
        found += detect_words(sentence, words, source=source, line_id=line_id)
    if has_konjunktiv_i(sentence):
        found = [
            occ.model_copy(update={"evidence": {**occ.evidence, "k1_sentence": "true"}})
            for occ in found
        ]
    return found


__all__ = [
    "NGRAM_PRUNE_EVERY",
    "NGRAM_SIZES",
    "NGRAM_VOCAB_SIZE",
    "WORD_KINDS",
    "LemmaCounts",
    "WordGate",
    "detect_adj_noun",
    "detect_adj_verb",
    "detect_all",
    "detect_connectors",
    "detect_idioms",
    "detect_noun_verb",
    "detect_reflexive",
    "detect_separable",
    "detect_verb_prep",
    "detect_words",
]
