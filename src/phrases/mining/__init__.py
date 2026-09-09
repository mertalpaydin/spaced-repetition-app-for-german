"""Every detector over one parsed sentence, plus the lemma counts the
association measure needs."""

from collections import Counter
from dataclasses import dataclass, field

from src.phrases.curated import CuratedLists
from src.phrases.mining.collocations import detect_adj_noun, detect_noun_verb
from src.phrases.mining.curated_match import detect_connectors, detect_idioms
from src.phrases.mining.verbs import (
    detect_reflexive,
    detect_separable,
    detect_verb_prep,
    verb_prep_candidates,
)
from src.phrases.occurrences import Occurrence
from src.phrases.parse import ParsedSentence, has_konjunktiv_i, verb_lemma_key


@dataclass
class LemmaCounts:
    """Sentence counts per lemma, by role, for lift and log-likelihood."""

    sentences: int = 0
    sentences_by_source: Counter[str] = field(default_factory=Counter)
    verbs: Counter[str] = field(default_factory=Counter)
    nouns: Counter[str] = field(default_factory=Counter)
    adjectives: Counter[str] = field(default_factory=Counter)
    prepositions: Counter[str] = field(default_factory=Counter)

    def add(self, sentence: ParsedSentence, source: str = "") -> None:
        self.sentences += 1
        if source:
            self.sentences_by_source[source] += 1
        verbs: set[str] = set()
        nouns: set[str] = set()
        adjs: set[str] = set()
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
            elif t.pos == "ADP":
                preps.add(t.lower)
        self.verbs.update(verbs)
        self.nouns.update(nouns)
        self.adjectives.update(adjs)
        self.prepositions.update(preps)

    def to_dict(self) -> dict[str, object]:
        return {
            "sentences": self.sentences,
            "sentences_by_source": dict(self.sentences_by_source),
            "verbs": dict(self.verbs),
            "nouns": dict(self.nouns),
            "adjectives": dict(self.adjectives),
            "prepositions": dict(self.prepositions),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "LemmaCounts":
        def counter(key: str) -> Counter[str]:
            value = raw.get(key, {})
            assert isinstance(value, dict)
            return Counter({str(k): int(v) for k, v in value.items()})

        sentences = raw.get("sentences", 0)
        assert isinstance(sentences, int)
        return cls(
            sentences=sentences,
            sentences_by_source=counter("sentences_by_source"),
            verbs=counter("verbs"),
            nouns=counter("nouns"),
            adjectives=counter("adjectives"),
            prepositions=counter("prepositions"),
        )


def detect_all(
    sentence: ParsedSentence,
    curated: CuratedLists,
    *,
    source: str,
    line_id: str,
    dictionary: frozenset[str] | None,
) -> list[Occurrence]:
    candidates = verb_prep_candidates(sentence, source=source, line_id=line_id)
    found = [occ for occ in candidates if occ.evidence.get("reflexive") != "true"]
    found += detect_reflexive(sentence, candidates, source=source, line_id=line_id)
    found += detect_separable(sentence, source=source, line_id=line_id, dictionary=dictionary)
    found += detect_noun_verb(sentence, source=source, line_id=line_id)
    found += detect_adj_noun(sentence, source=source, line_id=line_id)
    governed = frozenset(seed.key for seed in curated.verb_prep_seeds)
    found += detect_connectors(
        sentence, curated.connectors, source=source, line_id=line_id, governed=governed
    )
    found += detect_idioms(sentence, curated.idioms, source=source, line_id=line_id)
    if has_konjunktiv_i(sentence):
        found = [
            occ.model_copy(update={"evidence": {**occ.evidence, "k1_sentence": "true"}})
            for occ in found
        ]
    return found


__all__ = [
    "LemmaCounts",
    "detect_all",
    "detect_adj_noun",
    "detect_connectors",
    "detect_idioms",
    "detect_noun_verb",
    "detect_reflexive",
    "detect_separable",
    "detect_verb_prep",
]
