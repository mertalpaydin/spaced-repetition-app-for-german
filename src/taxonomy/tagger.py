"""spaCy-backed morphological tagging, used to sharpen ``facets.derive_facet``.

``src/taxonomy/facets.py`` derives a topic's facets from small, closed-class
paradigm tables (articles, pronouns, regular verb endings, a short noun-gender
lexicon). Those tables are honest but shallow: they decode ``item.
accepted_answers[0]`` in isolation, so any genuine German syncretism (``dem``
is simultaneously Dat Masc Sg and Dat Neut Sg; a bare ``-en`` adjective ending
spans nearly every non-Nom-Sg cell) comes back ``Unk`` even when the sentence
around the gap resolves it unambiguously. A real dependency parser sees the
sentence, not just the answer, and can settle most of that.

This module is the only thing in the taxonomy package that touches spaCy, and
it is designed to fail safely everywhere the model is missing:

* The model is loaded lazily, once per process, via :func:`_load_model`
  (``functools.lru_cache``). It is **never** loaded at import time -- a
  module-level ``spacy.load(...)`` call would mean every import of this
  module (and transitively, every import of ``facets``) pays the load cost
  and can raise on a machine without the model, exactly the ``ZoneInfo``
  mistake ``src/llm/client.py:pacific_tz`` was written to avoid.
* If ``spacy`` itself is not installed, or ``de_core_news_sm`` is not
  downloaded, :func:`_load_model` catches that and returns ``None``. It never
  raises. The failure is logged once, the first time a caller actually needs
  tagging (not at import), and every function in this module then returns
  ``None`` instead of an analysis for the rest of the process.
* Every caller -- currently only ``facets.derive_facet`` -- must treat
  ``None`` as "no analysis available" and fall back to its own closed-class
  derivation. This module never being present must never change what a
  pilot run produces, only how much of it stays ``Unk``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spacy.language import Language

logger = logging.getLogger(__name__)

MODEL_NAME = "de_core_news_sm"
_GAP_MARKER = "___"


@dataclass(frozen=True)
class TaggedAnswer:
    """One token's UD morphological analysis, read in its sentence's context.

    ``feats`` uses the same UD FEATS vocabulary as
    ``facets.GERMAN_UD_FEATURE_UNIVERSE`` (``Case``, ``Gender``, ``Number``,
    ...) -- spaCy's German pipeline already emits Universal Dependencies
    feature names and values, so no translation layer is needed between the
    two. No ``lemma`` field: ``_load_model`` excludes the lemmatizer
    component for speed (facet derivation never needs it), which would make
    ``token.lemma_`` a silently empty string rather than a real value.
    """

    text: str
    pos: str
    feats: dict[str, str]


@lru_cache(maxsize=1)
def _load_model() -> Language | None:
    """Load ``de_core_news_sm`` once per process, or return ``None``.

    Runs exactly once regardless of outcome (that is what ``lru_cache`` on a
    no-argument function buys us here), so the warning below is emitted at
    most once per process even though every subsequent call to
    :func:`tag_answer` re-enters this function.
    """
    try:
        import spacy
    except ImportError:
        logger.warning(
            "spaCy is not installed; morphological tagging is unavailable "
            "and facet derivation falls back to closed-class tables only."
        )
        return None

    try:
        # facets.py only ever reads token.morph and token.pos_/tag_ -- never
        # the dependency tree, named entities, or lemma. Excluding those
        # pipeline components measured ~2.4x faster parsing with byte-for-
        # byte identical morph output (verified against the full pipeline
        # across every category this module feeds facet derivation for), and
        # keeps the test suite's added runtime from ballooning across the
        # hundreds of derive_facet calls a generation/verification test run
        # makes.
        return spacy.load(MODEL_NAME, exclude=["parser", "ner", "lemmatizer", "senter"])
    except OSError:
        logger.warning(
            "spaCy model %r is not installed (`uv sync` should fetch it as a "
            "declared dependency; if it did not, run `python -m spacy "
            "download %s`). Morphological tagging is unavailable and facet "
            "derivation falls back to closed-class tables only.",
            MODEL_NAME,
            MODEL_NAME,
        )
        return None


def analysis_available() -> bool:
    """Whether the spaCy model loaded successfully.

    For tests and reporting scripts that want to state which code path ran,
    not for gating behaviour -- ``tag_answer`` already degrades to ``None``
    on its own.
    """
    return _load_model() is not None


_TRAILING_CUE = re.compile(r"^\s*\([^)]*\)\s*")


def _fill_gap(prompt: str, answer: str) -> tuple[str, int, int] | None:
    """Substitute ``answer`` for the ``___`` gap in ``prompt``.

    ``cloze_cued`` items keep their bracketed cue in the prompt after the gap
    (``"Das Auto hat das ___ (groß) Fenster."``) for the learner to see; it is
    not part of the sentence's grammar. Left in place, it visibly confuses
    the tagger -- verified directly: "Das Auto hat das große (groß)
    Fenster." gets the accusative "das große" mistagged as nominative and
    "Fenster" mistagged as plural, while the same sentence with the cue
    dropped ("Das Auto hat das große Fenster.") tags correctly. So a ``(...)``
    immediately after the gap is stripped before parsing.

    Returns ``(filled_sentence, answer_start, answer_end)`` (character
    offsets of ``answer`` within the filled, cue-stripped sentence), or
    ``None`` if ``prompt`` has no gap to fill.
    """
    gap = prompt.find(_GAP_MARKER)
    if gap == -1:
        return None
    rest = prompt[gap + len(_GAP_MARKER) :]
    rest = _TRAILING_CUE.sub(" ", rest, count=1)
    filled = prompt[:gap] + answer + rest
    return filled, gap, gap + len(answer)


def tag_answer(prompt: str, answer: str) -> TaggedAnswer | None:
    """Morphologically tag ``answer`` in the context of ``prompt``'s gap.

    Fills the ``___`` gap with ``answer`` and parses the *whole* resulting
    sentence -- resolving genuine syncretism (which gender ``dem`` is, here)
    is exactly the thing that needs the rest of the sentence, not the answer
    in isolation. Returns the tagged token whose span overlaps the answer's
    position in the filled sentence.

    Returns ``None`` whenever no analysis is possible: the model did not
    load, ``prompt`` has no gap, ``answer`` is empty, or no token in the
    parse aligns with the answer's character span. Every caller must treat
    ``None`` as "this dimension is undecidable from tagging" and fall back
    to its own derivation, never as an error.
    """
    answer = answer.strip()
    if not answer:
        return None
    nlp = _load_model()
    if nlp is None:
        return None
    filled = _fill_gap(prompt, answer)
    if filled is None:
        return None
    text, start, end = filled

    doc = nlp(text)
    overlapping = [tok for tok in doc if tok.idx < end and (tok.idx + len(tok.text)) > start]
    if not overlapping:
        return None
    # This taxonomy's closed-class categories (articles, pronouns, finite
    # verbs, attributive adjective endings) are all single-word answers in
    # practice, so the overlap is almost always exactly one token; on the
    # rare multi-token overlap (a multi-word answer) the first token is the
    # one carrying the inflection facets.derive_facet actually asks about
    # (the determiner/pronoun/verb/adjective itself, not a following noun).
    token = overlapping[0]
    return TaggedAnswer(
        text=token.text,
        pos=token.pos_,
        feats=dict(token.morph.to_dict()),
    )
