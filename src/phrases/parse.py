"""One spaCy parse per sentence, shared by every miner.

``de_core_news_sm`` emits TIGER dependency labels, not Universal Dependencies:
``sb`` subject, ``oa`` accusative object, ``da`` dative object, ``op``
prepositional object, ``mo`` modifier, ``svp`` separable verb particle, ``nk``
noun kernel, ``oc`` clausal object (the lexical verb under an auxiliary or a
modal), ``cvc`` the noun phrase of a Funktionsverbgefüge (``zur Verfügung``),
``pd`` predicate, ``cd``/``cj`` coordination. The miners are written against
those names.

Never raises on a missing model: ``load_parser`` returns ``None`` and the
build script exits with a message, rather than silently mining nothing.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from src.lexicon.lemmatizer import SEPARABLE_PREFIXES, normalise
from src.phrases import paradigms

if TYPE_CHECKING:
    from spacy.language import Language

MODEL_NAME = "de_core_news_sm"

#: Finite forms that carry a lexical verb under them via ``oc``.
_AUXILIARY_LEMMAS: frozenset[str] = frozenset({"haben", "sein", "werden", "möchten"}) | (
    paradigms.MODAL_LEMMAS
)


@dataclass(frozen=True)
class ParsedToken:
    i: int
    text: str
    lemma: str
    pos: str
    tag: str
    dep: str
    head: int
    #: Character offset of the token in the sentence text.
    idx: int
    morph: dict[str, str] = field(default_factory=dict)
    whitespace: str = ""

    @property
    def lower(self) -> str:
        return self.text.lower()

    @property
    def end(self) -> int:
        return self.idx + len(self.text)


@dataclass(frozen=True)
class ParsedSentence:
    text: str
    tokens: list[ParsedToken]

    def children(self, i: int) -> list[ParsedToken]:
        return [t for t in self.tokens if t.head == i and t.i != i]

    def child_with_dep(self, i: int, dep: str) -> ParsedToken | None:
        for t in self.tokens:
            if t.head == i and t.i != i and t.dep == dep:
                return t
        return None


@lru_cache(maxsize=1)
def load_parser() -> "Language | None":
    """The one parser instance. ``None`` when spaCy or the model is missing."""
    try:
        import spacy
    except ImportError:
        return None
    try:
        return spacy.load(MODEL_NAME, exclude=["ner"])
    except OSError:
        return None


def parser_available() -> bool:
    return load_parser() is not None


def _convert(doc: object, text: str) -> ParsedSentence:
    tokens: list[ParsedToken] = []
    for t in doc:  # type: ignore[attr-defined]
        tokens.append(
            ParsedToken(
                i=t.i,
                text=t.text,
                lemma=t.lemma_,
                pos=t.pos_,
                tag=t.tag_,
                dep=t.dep_,
                head=t.head.i,
                idx=t.idx,
                morph=dict(kv.split("=", 1) for kv in str(t.morph).split("|") if "=" in kv),
                whitespace=t.whitespace_,
            )
        )
    return ParsedSentence(text=text, tokens=tokens)


def parse_one(text: str) -> ParsedSentence | None:
    nlp = load_parser()
    if nlp is None:
        return None
    return _convert(nlp(text), text)


def parse_many(texts: Iterable[str], batch_size: int = 256) -> Iterator[ParsedSentence]:
    """``nlp.pipe`` over ``texts``, in order. Raises if no parser is loaded:
    the build path must fail loudly, not yield nothing."""
    nlp = load_parser()
    if nlp is None:
        raise RuntimeError(f"spaCy model {MODEL_NAME} is not installed; run `uv sync`")
    texts = list(texts)
    for text, doc in zip(texts, nlp.pipe(texts, batch_size=batch_size), strict=True):
        yield _convert(doc, text)


def lexical_verb(sentence: ParsedSentence, i: int) -> ParsedToken | None:
    """The content verb a token's head chain leads to.

    ``i`` is the token whose governor is wanted (a preposition, an object, a
    reflexive pronoun). Starts at its head; while that is an auxiliary or a
    modal with exactly one ``oc`` child, descends into it. Returns ``None``
    when the chain ends on something that is not a VERB (``sein`` with a
    predicate, a noun) or when the ``oc`` child is not unique.
    """
    token = sentence.tokens[i]
    head = sentence.tokens[token.head]
    for _ in range(4):
        oc = [c for c in sentence.children(head.i) if c.dep == "oc" and c.pos == "VERB"]
        # A modal is sometimes tagged VERB with its surface form as lemma
        # ("muss"); a real infinitive always ends in -n.
        modal_like = head.lemma.lower() in _AUXILIARY_LEMMAS or not head.lemma.lower().endswith("n")
        if head.pos == "VERB" and not (modal_like and len(oc) == 1):
            return head
        if head.pos == "AUX" or modal_like:
            if len(oc) != 1:
                return None
            head = oc[0]
            continue
        return None
    return None


@lru_cache(maxsize=1)
def _dictionary() -> frozenset[str] | None:
    from src.phrases.carrier_validation import _load_dictionary

    return _load_dictionary()


_LEMMA_REPAIRS: tuple[tuple[str, str], ...] = (("eren", "ern"), ("elen", "eln"))


def repair_verb_lemma(lemma: str) -> str:
    """The tagger's lemma, or a dictionary-attested repair of one of its
    known slips (``erinneren`` for ``erinnern``). Empty when the lemma is
    not an infinitive at all (``muss``), which is how the small model says
    it does not know the verb."""
    lemma = lemma.lower()
    if not lemma.endswith("n") or len(lemma) < 4:
        return ""
    words = _dictionary()
    if words is None or normalise(lemma) in words:
        return lemma
    for wrong, right in _LEMMA_REPAIRS:
        if lemma.endswith(wrong):
            candidate = lemma[: -len(wrong)] + right
            if normalise(candidate) in words:
                return candidate
    # "benimmsen", "erinnerstn", "küssn": the tagger glued an ending onto an
    # inflected form. Not a word, so not a unit (review finding, 2026-09-08).
    return ""


#: Particles the tagger attaches as ``svp`` that are not verb prefixes in
#: any card worth having: the correlative "da" ("da, wo ..."), and prefixes
#: that sit inside a fixed adverbial ("ab und zu", "hin und her", "von ... aus").
_NEVER_A_PREFIX: frozenset[str] = frozenset({"da"})

#: Adverbs the tagger attaches as ``svp`` whose fusion with the verb is a
#: word only sometimes ("weitermachen" yes, "weiterheissen" no): gated on
#: the dictionary. Pronominal adverbs ("dabei", "davon") never fuse.
_ADVERB_PARTICLES: frozenset[str] = frozenset({"wieder", "weiter", "zurück"})
_PRONOMINAL_ADVERB_PREFIXES: tuple[str, ...] = ("da", "dar", "wo", "wor", "hier")
#: A token right after a real particle is punctuation, a conjunction or a
#: clause. A nominal there means the "particle" heads a prepositional
#: phrase ("finden Sie unter http://...", "unter 'Meine Bücher'").
_PP_OBJECT_POS: frozenset[str] = frozenset({"NOUN", "PROPN", "DET", "NUM", "X"})


def _fuses_with(particle: str, verb_lemma: str) -> bool:
    words = _dictionary()
    return words is None or normalise(particle + verb_lemma) in words


def separable_particle(sentence: ParsedSentence, verb: ParsedToken) -> ParsedToken | None:
    """The verb's separated prefix, or ``None`` when there is none or the
    attachment is not trustworthy (two candidates, a coordinated adverbial,
    a postposition)."""
    particles = [c for c in sentence.children(verb.i) if c.dep == "svp"]
    if len(particles) != 1:
        return None
    particle = particles[0]
    if particle.lower in _NEVER_A_PREFIX:
        return None
    lower = particle.lower
    if lower.startswith(_PRONOMINAL_ADVERB_PREFIXES) and lower not in {"dazu"}:
        if not _fuses_with(lower, verb.lemma.lower()):
            return None
    if lower in _ADVERB_PARTICLES and not _fuses_with(lower, verb.lemma.lower()):
        return None
    if (
        lower not in SEPARABLE_PREFIXES
        and lower not in paradigms.KNOWN_PARTICLES
        and not _fuses_with(lower, verb.lemma.lower())
    ):
        return None
    before = sentence.tokens[particle.i - 1] if particle.i > 0 else None
    after = sentence.tokens[particle.i + 1] if particle.i + 1 < len(sentence.tokens) else None
    if (before is not None and before.lower == "und") or (
        after is not None and after.lower == "und"
    ):
        return None
    if after is not None and after.pos == "PUNCT" and after.text == "," and particle.lower == "da":
        return None
    if after is not None and (
        after.pos in _PP_OBJECT_POS or after.text in {":", '"', "„", "'", "http"}
    ):
        return None
    # "von einem Telefon aus": the particle closes a "von" phrase.
    if particle.lower == "aus" and any(
        t.lower == "von" and t.pos == "ADP" and t.i < particle.i for t in sentence.tokens
    ):
        return None
    return particle


def verb_lemma_key(sentence: ParsedSentence, verb: ParsedToken) -> str:
    """Lowercased infinitive, with a separated particle re-attached
    (``steht … auf`` -> ``aufstehen``). Empty when the tagger left the
    surface form as its own lemma, which is its way of saying it does not
    know the verb."""
    lemma = repair_verb_lemma(verb.lemma)
    if not lemma:
        return ""
    particle = separable_particle(sentence, verb)
    if particle is not None:
        return particle.lower + lemma
    return lemma


def is_sentence_initial(sentence: ParsedSentence, i: int) -> bool:
    """Only punctuation or quotes precede token ``i``."""
    return all(
        t.pos == "PUNCT" or not any(ch.isalnum() for ch in t.text) for t in sentence.tokens[:i]
    )


def form_key(verb: ParsedToken, *, suffix: str = "") -> str:
    """``"Fin|Pres|3|Sing"``, ``"Part"``, ``"Inf"``: which surface form of the
    verb this occurrence shows. Cards for one unit cover distinct keys."""
    m = verb.morph
    vf = m.get("VerbForm", "Fin")
    if vf == "Fin":
        key = "|".join(["Fin", m.get("Tense", "?"), m.get("Person", "?"), m.get("Number", "?")])
        if is_konjunktiv_i(verb):
            key += "|K1"
    else:
        key = vf
    return f"{key}|{suffix}" if suffix else key


def is_konjunktiv_i(token: ParsedToken) -> bool:
    """Reported-speech Konjunktiv I ("er gebe", "es handle sich"): correct
    German that a learner typing the indicative would be marked wrong on."""
    return token.morph.get("Mood") == "Sub" and token.morph.get("Tense") == "Pres"


def has_konjunktiv_i(sentence: ParsedSentence) -> bool:
    return any(t.pos in {"VERB", "AUX"} and is_konjunktiv_i(t) for t in sentence.tokens)
