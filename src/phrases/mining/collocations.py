"""Noun-verb and adjective-noun collocations.

Detection is generous; the units stage applies the association measure
(``units.log_likelihood``) and abstains where the counts are sparse.
"""

from src.phrases.mining.common import (
    LIGHT_VERB_LEMMAS,
    STOP_ADVERBS,
    is_word,
    make_occurrence,
)
from src.phrases.occurrences import Occurrence
from src.phrases.parse import (
    ParsedSentence,
    ParsedToken,
    form_key,
    lexical_verb,
    separable_particle,
    verb_lemma_key,
)

#: Adjectives that combine with everything and teach nothing as a pair.
_STOP_ADJECTIVES: frozenset[str] = frozenset(
    {
        # "ein bisschen Glück": a quantifier the tagger lemmatises to "bissch".
        "bissch",
        "bisschen",
        "ander",
        "viel",
        "wenig",
        "einig",
        "mehrer",
        "solch",
        "letzt",
        "nächst",
        "ganz",
        "erst",
        "zweit",
        "dritt",
        "eigen",
        "gleich",
        "einzeln",
        "weiter",
        "verschieden",
        "bestimmt",
        "gesamt",
        "jeweilig",
        "vergangen",
        "kommend",
        "früher",
        "heutig",
        "weitere",
        "sogenannt",
        "einzig",
        "meist",
        "zahlreich",
        "übrig",
        "folgend",
        "letzter",
        "erster",
        "nächster",
        "zweiter",
        "dritter",
        "vorig",
        "voriger",
        "vergangener",
        "kommender",
        "heutiger",
        "jeweiliger",
        "sogenannter",
        "einziger",
        "übriger",
        "gesamter",
        "ganzer",
        "eigener",
        "gleicher",
        "weiterer",
        "verschiedener",
        "bestimmter",
        "mehrere",
        "einige",
        "mein",
        "dein",
        "sein",
        "ihr",
        "unser",
        "euer",
        "anderer",
        "andere",
        "anderes",
        "welcher",
        "dieser",
        "jener",
        "jeder",
        "manch",
        "mancher",
        "beide",
        "beider",
        "sämtlich",
        "übrige",
        "französische",
        "englische",
        "deutsche",
    }
)

#: Measures and calendar nouns: ``Euro kosten`` and ``Jahr dauern`` are
#: syntax, not vocabulary.
STOP_NOUNS: frozenset[str] = frozenset(
    {
        "euro",
        "dollar",
        "cent",
        "prozent",
        "meter",
        "kilometer",
        "zentimeter",
        "kilo",
        "kilogramm",
        "gramm",
        "liter",
        "grad",
        "million",
        "milliarde",
        "tausend",
        "hundert",
        "stück",
        "jahr",
        "monat",
        "woche",
        "tag",
        "stunde",
        "minute",
        "sekunde",
        "uhr",
        "mal",
        "zeit",
        "prozentpunkt",
        "quadratmeter",
        "hektar",
        "tonne",
    }
)

#: ``haben`` as the main verb of a bare noun (``Hunger haben``) is tagged AUX
#: with no ``oc`` child; it is a collocation head all the same.
_HAVE_LEMMAS: frozenset[str] = frozenset({"haben"})


def _governing_verb(sentence: ParsedSentence, noun: ParsedToken) -> ParsedToken | None:
    head = sentence.tokens[noun.head]
    if head.pos == "AUX" and head.lemma.lower() in _HAVE_LEMMAS:
        if not any(c.dep == "oc" for c in sentence.children(head.i)):
            return head
    verb = lexical_verb(sentence, noun.i)
    if verb is None or verb.pos != "VERB":
        return None
    if verb.lemma.lower() in LIGHT_VERB_LEMMAS:
        return None
    return verb


def _verb_key(sentence: ParsedSentence, verb: ParsedToken) -> str:
    if verb.pos == "AUX":
        return verb.lemma.lower()
    return verb_lemma_key(sentence, verb)


def detect_noun_verb(sentence: ParsedSentence, *, source: str, line_id: str) -> list[Occurrence]:
    found: list[Occurrence] = []
    for noun in sentence.tokens:
        if noun.pos != "NOUN" or not is_word(noun) or noun.dep not in {"oa", "da"}:
            continue
        if noun.lemma.lower() in STOP_NOUNS:
            continue
        verb = _governing_verb(sentence, noun)
        if verb is None:
            continue
        # "es gibt eine Möglichkeit" is existential, not "eine Möglichkeit geben".
        if verb.lemma.lower() == "geben" and any(
            c.lower == "es" and c.dep in {"sb", "ep"} for c in sentence.children(verb.i)
        ):
            continue
        vkey = _verb_key(sentence, verb)
        if not vkey:
            continue
        particle = separable_particle(sentence, verb) if verb.pos == "VERB" else None
        tokens = [noun, verb] + ([particle] if particle is not None else [])
        noun_lemma = noun.lemma
        found.append(
            make_occurrence(
                kind="noun_verb",
                unit_key=f"{noun_lemma.lower()} {vkey}",
                parts=[noun_lemma, vkey],
                tokens=tokens,
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb),
                evidence={"dep": noun.dep, "noun": noun_lemma.lower(), "verb": vkey},
            )
        )
    # Funktionsverbgefüge: ``zur Verfügung stehen``, ``in Frage kommen``.
    for prep in sentence.tokens:
        if prep.pos != "ADP" or prep.dep != "cvc":
            continue
        nouns = [c for c in sentence.children(prep.i) if c.dep == "nk" and c.pos == "NOUN"]
        if len(nouns) != 1:
            continue
        verb = lexical_verb(sentence, prep.i)
        if verb is None or verb.pos != "VERB":
            continue
        vkey = verb_lemma_key(sentence, verb)
        if not vkey:
            continue
        noun = nouns[0]
        particle = separable_particle(sentence, verb)
        tokens = [prep, noun, verb] + ([particle] if particle is not None else [])
        found.append(
            make_occurrence(
                kind="noun_verb",
                unit_key=f"{prep.lower} {noun.lemma.lower()} {vkey}",
                parts=[prep.lower, noun.lemma, vkey],
                tokens=tokens,
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb),
                evidence={"dep": "cvc", "noun": noun.lemma.lower(), "verb": vkey},
            )
        )
    return found


def detect_adj_noun(sentence: ParsedSentence, *, source: str, line_id: str) -> list[Occurrence]:
    found: list[Occurrence] = []
    for adj in sentence.tokens:
        if adj.pos != "ADJ" or adj.dep != "nk" or not is_word(adj):
            continue
        if adj.morph.get("Degree", "Pos") != "Pos":
            continue
        lemma = adj.lemma.lower()
        if lemma in _STOP_ADJECTIVES or len(lemma) < 3:
            continue
        # A capitalised adjective inside the sentence is part of a proper
        # name ("Vereinigten Staaten", "Deutsche Bahn"), not a collocation.
        if adj.text[:1].isupper() and adj.i > 0:
            continue
        noun = sentence.tokens[adj.head]
        if noun.pos != "NOUN" or not is_word(noun):
            continue
        if noun.lemma.lower() in STOP_NOUNS:
            continue
        found.append(
            make_occurrence(
                kind="adj_noun",
                unit_key=f"{lemma} {noun.lemma.lower()}",
                parts=[lemma, noun.lemma],
                tokens=[adj, noun],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=f"{adj.morph.get('Case', '?')}|{adj.morph.get('Number', '?')}",
                evidence={"adj": lemma, "noun": noun.lemma.lower()},
            )
        )
    return found


def stop_adjective(lemma: str) -> bool:
    """Adjectives that combine with everything. Public so the single-word
    detector applies the same list (``mining.words``)."""
    return lemma in _STOP_ADJECTIVES


#: The dependency labels a predicative or adverbial adjective takes under its
#: verb. ``nk`` is attributive and belongs to ``adj_noun``; ``svp`` is a
#: separable particle and belongs to ``separable_verb``.
_ADJ_VERB_DEPS: frozenset[str] = frozenset({"pd", "oc", "mo"})


def detect_adj_verb(sentence: ParsedSentence, *, source: str, line_id: str) -> list[Occurrence]:
    """Adjective + verb collocations: ``ernst nehmen``, ``bereit machen``,
    ``bekannt geben``. The gap the Lingvist list exposed on 2026-09-19: the
    miner had adjective + noun and noun + verb but nothing for an adjective
    that forms a set phrase with a verb."""
    found: list[Occurrence] = []
    for adj in sentence.tokens:
        # The tagger calls a predicative adjective an adverb, so both parts of
        # speech are candidates; ``units._decide_adj_verb`` requires the
        # modifier to be used as an adjective somewhere in the corpus.
        if adj.pos not in {"ADJ", "ADV"} or adj.dep not in _ADJ_VERB_DEPS or not is_word(adj):
            continue
        if adj.morph.get("Degree", "Pos") != "Pos":
            continue
        lemma = adj.lemma.lower()
        if lemma in _STOP_ADJECTIVES or lemma in STOP_ADVERBS or len(lemma) < 3:
            continue
        if adj.text[:1].isupper() and adj.i > 0:
            continue
        verb = lexical_verb(sentence, adj.i)
        # A copula reading ("ist schön") is every predicative adjective, not
        # a collocation, so the light verbs are excluded as for noun + verb.
        if verb is None or verb.pos != "VERB" or verb.lemma.lower() in LIGHT_VERB_LEMMAS:
            continue
        vkey = verb_lemma_key(sentence, verb)
        if not vkey:
            continue
        particle = separable_particle(sentence, verb)
        tokens = [adj, verb] + ([particle] if particle is not None else [])
        found.append(
            make_occurrence(
                kind="adj_verb",
                unit_key=f"{lemma} {vkey}",
                parts=[lemma, vkey],
                tokens=tokens,
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb),
                evidence={"dep": adj.dep, "adj": lemma, "verb": vkey},
            )
        )
    return found
