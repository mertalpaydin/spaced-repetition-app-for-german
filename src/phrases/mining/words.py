"""Single words: nouns, verbs, adjectives and adverbs.

The deck taught only multi-word phrases until 2026-09-20, which left most of
a learner's vocabulary out of scope. These four kinds teach one word, blanked
in a real sentence like any other card.

Two bounds keep the occurrence file usable. A common word appears in millions
of sentences, so a detector that emitted them all would grow
``occurrences.jsonl`` by an order of magnitude for no gain:

* only sentences that already carry an Azure or Gemini gloss are emitted,
  because ``cards.select_cards`` can never use any other sentence anyway;
* at most ``WordGate.cap`` sentences per word per corpus, so a build is
  bounded and two builds of the same corpus agree.

The counts that drive the ranking are therefore NOT the occurrence tally.
``LemmaCounts.word_by_source`` counts over every sentence, and
``units.UnitBuilder`` reads the frequency from there.
"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field

from src.phrases.mining.collocations import STOP_NOUNS, stop_adjective
from src.phrases.mining.common import NON_UNIT_ADVERBS, is_word, make_occurrence
from src.phrases.mining.verbs import fused_separable_key
from src.phrases.occurrences import Occurrence
from src.phrases.parse import (
    ParsedSentence,
    ParsedToken,
    form_key,
    repair_verb_lemma,
    separable_particle,
)

#: Nouns and adjectives whose lemma is a bare number or a calendar word: the
#: owner's decision of 2026-09-20 is that those are learned from a table, not
#: by spaced repetition. ``STOP_NOUNS`` already holds the calendar nouns.
_NUMBER_POS: frozenset[str] = frozenset({"NUM"})


@dataclass(frozen=True)
class WordGate:
    """Which words may become units, and how many sentences to keep for each.

    ``lemmas`` is the real-word filter: the CEFR list union the dictionary
    filter, so names, typos and rare compounds never enter. ``glossed`` is
    the set of sentence texts that carry a trusted gloss. The frequency
    threshold is NOT applied here; it is applied in ``units.py`` against the
    fresh counts, so re-tuning it never costs another parse.
    """

    lemmas: frozenset[str]
    glossed: frozenset[str]
    dictionary: frozenset[str] | None = None
    #: Sentences kept per word per corpus. Capping per word outright would
    #: draw every carrier from whichever corpus sorts first, and the card
    #: scorer prefers Tatoeba by a wide margin (``cards.SOURCE_PENALTY``).
    #: The card stage wants six and rejects about half its candidates.
    cap: int = 12
    seed: int = 7
    #: Mutable tally, keyed "kind:lemma:source".
    taken: Counter[str] = field(default_factory=Counter, compare=False)

    def wants(self, kind: str, lemma: str, text: str, source: str) -> bool:
        if lemma not in self.lemmas or text not in self.glossed:
            return False
        key = f"{kind}:{lemma}:{source}"
        if self.taken[key] >= self.cap:
            return False
        self.taken[key] += 1
        return True


def _noun_form_key(token: ParsedToken) -> str:
    return f"{token.morph.get('Case', '?')}|{token.morph.get('Number', '?')}"


def _adjective_form_key(token: ParsedToken) -> str:
    return f"{token.morph.get('Case', '?')}|{token.morph.get('Number', '?')}"


def word_lemmas(sentence: ParsedSentence, gate: WordGate | None) -> Iterator[tuple[str, str]]:
    """``(kind, lemma)`` for every token of this sentence that could be a
    single-word unit. Used by ``LemmaCounts`` to count over the whole corpus
    and by the detector to emit; both must agree on what counts as a word."""
    allowed = gate.lemmas if gate is not None else None
    for token in sentence.tokens:
        kind_lemma = _word_of(sentence, token, gate.dictionary if gate else None)
        if kind_lemma is None:
            continue
        kind, lemma = kind_lemma
        if allowed is not None and lemma not in allowed:
            continue
        yield kind, lemma


def _word_of(
    sentence: ParsedSentence, token: ParsedToken, dictionary: frozenset[str] | None = None
) -> tuple[str, str] | None:
    """The kind and lemma this token would teach, or ``None``.

    ADJ and ADV share the provisional kind ``adjective``: the tagger calls a
    predicative adjective an adverb ("er nimmt das ernst", "das ist bekannt"),
    so keying on the token's part of speech would file one word under two
    kinds and teach it twice. ``units._decide_word`` settles the final kind
    per lemma from the corpus-wide counters.
    """
    if not is_word(token) or token.pos in _NUMBER_POS:
        return None
    if token.pos == "NOUN":
        lemma = token.lemma.lower()
        if lemma in STOP_NOUNS or len(lemma) < 3:
            return None
        return "noun", lemma
    if token.pos == "VERB":
        # A separable verb is its own kind, whether it is written apart
        # ("steht auf") or fused ("abgesagt"); so is a verb whose lemma the
        # tagger could not resolve to an infinitive.
        if separable_particle(sentence, token) is not None:
            return None
        if fused_separable_key(token, dictionary) is not None:
            return None
        lemma = repair_verb_lemma(token.lemma.lower())
        if not lemma:
            return None
        return "verb", lemma
    if token.pos in {"ADJ", "ADV"}:
        if token.morph.get("Degree", "Pos") != "Pos":
            return None
        lemma = token.lemma.lower()
        if len(lemma) < 3 or stop_adjective(lemma) or lemma in NON_UNIT_ADVERBS:
            return None
        return "adjective", lemma
    return None


def detect_words(
    sentence: ParsedSentence, gate: WordGate, *, source: str, line_id: str
) -> list[Occurrence]:
    found: list[Occurrence] = []
    for token in sentence.tokens:
        kind_lemma = _word_of(sentence, token, gate.dictionary)
        if kind_lemma is None:
            continue
        kind, lemma = kind_lemma
        if not gate.wants(kind, lemma, sentence.text, source):
            continue
        if kind == "noun":
            key = _noun_form_key(token)
            evidence = {"gender": token.morph.get("Gender", ""), "surface": token.text}
        elif kind == "verb":
            key = form_key(token)
            evidence = {"surface": token.text}
        else:
            key = _adjective_form_key(token) if token.pos == "ADJ" else "adv"
            evidence = {"surface": token.text, "pos": token.pos}
        found.append(
            make_occurrence(
                kind=kind,
                unit_key=lemma,
                parts=[lemma],
                tokens=[token],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=key,
                evidence=evidence,
            )
        )
    return found
