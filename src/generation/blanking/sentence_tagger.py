"""Full-sentence spaCy tagging for the generate-then-blank pipeline.

``src.taxonomy.tagger`` already tags a single answer token in the context of
a ``___``-gapped prompt (``tag_answer``/``tag_context``). This module needs a
different entry point: a PLAIN natural sentence with no gap at all, tagged
token by token, so a selector can scan every token for one that is an
instance of a topic. There is no public function in ``src.taxonomy.tagger``
for that shape, and this package may only create new files (an existing
file's private ``_load_model`` could be reached into, but a second, focused
loader here is clearer than depending on another module's private state).

The loader below mirrors ``src.taxonomy.tagger._load_model`` exactly (same
model name, same excluded pipeline components -- this module only ever reads
``token.pos_``/``token.tag_``/``token.morph``, never the dependency tree,
lemma, or named entities) and the same never-crash degrade contract: no
``spacy.load(...)`` at import time, ``None`` (or an empty result) whenever
the model is missing, never an exception. ``analysis_available`` delegates to
``src.taxonomy.tagger.analysis_available`` rather than re-implementing the
availability check, since the two loaders share the exact same failure
conditions (same model name, same install).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from src.taxonomy import tagger as answer_tagger

if TYPE_CHECKING:
    from spacy.language import Language

MODEL_NAME = answer_tagger.MODEL_NAME  # "de_core_news_sm", named in one place


@dataclass(frozen=True)
class Token:
    """One token's POS, fine-grained tag, and UD morphology, plus its index
    and trailing whitespace so a caller can both scan neighbours by index and
    reconstruct the original sentence with one token replaced by a gap."""

    i: int
    text: str
    pos: str
    tag: str
    morph: dict[str, str]
    whitespace: str


@dataclass(frozen=True)
class TaggedSentence:
    """A parsed sentence: the original text plus every token in order."""

    text: str
    tokens: tuple[Token, ...]

    def token_before(self, index: int) -> Token | None:
        """The token immediately preceding ``index``, or ``None`` at the
        sentence's start. Selectors use this for neighbour-context checks
        (e.g. "is this determiner preceded by a preposition")."""
        return self.tokens[index - 1] if index > 0 else None

    def token_after(self, index: int) -> Token | None:
        """The token immediately following ``index``, or ``None`` at the
        sentence's end."""
        return self.tokens[index + 1] if index + 1 < len(self.tokens) else None


@lru_cache(maxsize=1)
def _load_model() -> Language | None:
    """Load ``de_core_news_sm`` once per process, or return ``None``.

    Same exclusions as ``src.taxonomy.tagger._load_model``: this module never
    reads the dependency tree, named entities, or lemma, only
    ``token.pos_``/``token.tag_``/``token.morph``.
    """
    try:
        import spacy
    except ImportError:
        return None
    try:
        return spacy.load(MODEL_NAME, exclude=["parser", "ner", "lemmatizer", "senter"])
    except OSError:
        return None


def analysis_available() -> bool:
    """Whether sentence tagging can actually run.

    Delegates to ``src.taxonomy.tagger.analysis_available()`` -- both loaders
    load the identical model under the identical conditions, so there is
    nothing to re-derive here, only to reuse. This module's own ``_load_model``
    still does the real per-sentence parsing (it cannot share the other
    module's private cached instance across a module boundary), but whether
    parsing is *possible at all* is one fact, checked once.
    """
    return answer_tagger.analysis_available()


def tag_sentence(text: str) -> TaggedSentence | None:
    """Tag every token of ``text`` with its POS, fine-grained tag, and UD
    morphology. Returns ``None`` if spaCy is unavailable or ``text`` is
    blank after stripping -- never raises for either condition, matching
    ``src.taxonomy.tagger``'s degrade contract.
    """
    stripped = text.strip()
    if not stripped:
        return None
    nlp = _load_model()
    if nlp is None:
        return None
    doc = nlp(stripped)
    tokens = tuple(
        Token(
            i=tok.i,
            text=tok.text,
            pos=tok.pos_,
            tag=tok.tag_,
            morph=dict(tok.morph.to_dict()),
            whitespace=tok.whitespace_,
        )
        for tok in doc
    )
    return TaggedSentence(text=stripped, tokens=tokens)
