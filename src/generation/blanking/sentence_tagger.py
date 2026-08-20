"""Full-sentence spaCy tagging for the generate-then-blank pipeline.

``src.taxonomy.tagger`` already tags a single answer token in the context of
a ``___``-gapped prompt (``tag_answer``/``tag_context``). This module needs a
different entry point: a PLAIN natural sentence with no gap at all, tagged
token by token, so a selector can scan every token for one that is an
instance of a topic. There is no public function in ``src.taxonomy.tagger``
for that shape, and this package may only create new files (an existing
file's private ``_load_model`` could be reached into, but a second, focused
loader here is clearer than depending on another module's private state).

The loader below mirrors ``src.taxonomy.tagger._load_model`` in every other
respect (same model name, same never-crash degrade contract: no
``spacy.load(...)`` at import time, ``None``/an empty result whenever the
model is missing, never an exception) but deliberately does NOT exclude the
``lemmatizer`` pipe. Cycle 2's selectors (articles, adjective declension)
never needed a lemma -- every closed-class form was looked up by its own
surface text. Cycle 3's verb-conjugation selectors cannot do that: a finite
verb's correct form is a function of its *lemma* plus (Person, Number,
Tense), and "gibst"/"gab"/"gegeben" share no recoverable surface stem without
one. Confirmed empirically before this change: with ``lemmatizer`` excluded,
``token.lemma_`` is the empty string for every token; with it included,
``de_core_news_sm``'s lemmatizer correctly reduces inflected and even
separable-prefixed forms to their infinitive ("spricht" -> "sprechen",
"gesprochen" -> "sprechen") at negligible added cost (~2.5ms/sentence
measured locally). This module still never reads the dependency tree or
named entities -- only ``token.pos_``/``token.tag_``/``token.morph``/
``token.lemma_``. ``analysis_available`` delegates to
``src.taxonomy.tagger.analysis_available`` rather than re-implementing the
availability check, since both loaders fail under the same condition (model
not installed) regardless of which pipes each one excludes.
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
    lemma: str


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

    Unlike ``src.taxonomy.tagger._load_model``, ``lemmatizer`` is kept (see
    module docstring): this module needs ``token.lemma_`` for verb-paradigm
    lookups. The dependency tree, named entities and sentence segmentation
    are still excluded -- nothing here reads them.
    """
    try:
        import spacy
    except ImportError:
        return None
    try:
        return spacy.load(MODEL_NAME, exclude=["parser", "ner", "senter"])
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


@lru_cache(maxsize=8192)
def tag_sentence(text: str) -> TaggedSentence | None:
    """Tag every token of ``text`` with its POS, fine-grained tag, and UD
    morphology. Returns ``None`` if spaCy is unavailable or ``text`` is
    blank after stripping -- never raises for either condition, matching
    ``src.taxonomy.tagger``'s degrade contract.

    Cached on ``text`` alone: this function is a pure mapping from a
    sentence string to its tagging (the loaded model is process-global and
    never changes mid-run), and ``src.generation.blanking.orchestrator``'s
    demand-recomputation loop (TODO 1.8) now re-runs ``pipeline.
    blank_sentences`` -- which calls this for every sentence -- over the
    SAME, growing pool of accumulated sentences many times in one run, to
    check whether a topic's demand has already been met by other topics'
    output before spending a call on it. Without this cache, that check
    would re-parse identical text with spaCy on every recheck; with it, only
    genuinely new sentences cost a fresh parse. 8192 is comfortably above
    any single pilot run's sentence count (49 topics x a handful of batches
    each), so eviction should not matter in practice; a run that did exceed
    it would simply re-parse the evicted sentences, never produce a wrong
    tagging -- this is a performance cache, not a correctness dependency.
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
            lemma=tok.lemma_,
        )
        for tok in doc
    )
    return TaggedSentence(text=stripped, tokens=tokens)
