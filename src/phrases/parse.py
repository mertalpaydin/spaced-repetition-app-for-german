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

from src.lexicon.lemmatizer import normalise
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
    return lemma


def separable_particle(sentence: ParsedSentence, verb: ParsedToken) -> ParsedToken | None:
    return sentence.child_with_dep(verb.i, "svp")


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
    else:
        key = vf
    return f"{key}|{suffix}" if suffix else key
