"""Per-topic token selectors: declarative predicates over a tagged sentence's
tokens (POS, fine-grained tag, UD morphology, and immediate neighbours) that
decide "this token is an instance of this topic".

Cycle 2 scope (docs/audits/generation-track-plan.md): the 15 article and
adjective declension topics below, and no others. A selector never looks at
``topic.id`` inside its own logic -- each entry in ``SELECTORS`` is built by
handing topic-specific *parameters* (a fixed case, an allowed determiner
family, a preposition set, a declension) to one of three small factories,
matching the module's data-driven design brief rather than fifteen bespoke
functions:

* ``_determiner_selector`` -- 11 topics: three Nominative article topics
  (``artikel_*``), three bare-case topics (``kasus_*_formen``), two
  Wechselpräposition-direction topics (``*_nach_praeposition``), and three
  fixed-case-preposition topics (``praepositionen_*``).
* ``_adjective_selector`` -- the three declension topics
  (``adjektivdeklination_*``).
* ``_select_komparativ_superlativ`` -- its own predicate; comparison varies
  over ``Degree``, not (Case, Gender, Number), so it does not fit the
  determiner/adjective shape at all.

Every selector returns zero or more ``Candidate``s and never raises; a token
whose morphology the tagger could not resolve (an empty ``Case``/``Gender``/
``Number``), or whose neighbour context is genuinely ambiguous, is simply not
returned -- "reject rather than guess" applied at the selection stage, before
``blanker.py`` even runs its own paradigm-reconstruction check.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from src.generation.blanking.paradigms import Cell
from src.generation.blanking.sentence_tagger import TaggedSentence, Token

ArtFamily = Literal["Def", "Ind", "Neg", "Poss"]
CandidateKind = Literal["determiner", "adjective", "degree"]

# Tags spaCy's German pipeline uses for the three determiner-shaped word
# classes this cycle covers: ART (definite/indefinite article), PIAT
# (attributive indefinite pronoun -- "kein", but also "jeder"/"manche"/
# "viele", which _art_type below deliberately leaves unclassified), PPOSAT
# (attributive possessive).
_DETERMINER_TAGS: frozenset[str] = frozenset({"ART", "PIAT", "PPOSAT"})
_KEIN_STEM = "kein"

# The nine Wechselpräpositionen (docs: dativ_nach_praeposition's intro_card),
# governing Accusative for direction or Dative for static location -- which
# one is decided by the DET's own Case, already resolved by the parser from
# the whole sentence, not guessed here from a verb-cue heuristic.
_WECHSEL_PREPOSITIONEN: frozenset[str] = frozenset(
    {"an", "auf", "hinter", "in", "neben", "über", "ueber", "unter", "vor", "zwischen"}
)
_AKKUSATIV_PRAEPOSITIONEN: frozenset[str] = frozenset(
    {"bis", "durch", "für", "fuer", "gegen", "ohne", "um"}
)
_DATIV_PRAEPOSITIONEN: frozenset[str] = frozenset(
    {"aus", "bei", "mit", "nach", "seit", "von", "zu", "gegenüber", "gegenueber"}
)
_GENITIV_PRAEPOSITIONEN: frozenset[str] = frozenset(
    {
        "wegen",
        "während",
        "waehrend",
        "trotz",
        "statt",
        "anstatt",
        "innerhalb",
        "außerhalb",
        "ausserhalb",
    }
)

_ALL_ART_TYPES: frozenset[ArtFamily] = frozenset({"Def", "Ind", "Neg", "Poss"})


@dataclass(frozen=True)
class Candidate:
    """One selected blank-able token, plus everything ``blanker.py`` needs to
    compute the accepted-answer set and distractors without re-deriving
    anything from the topic id itself."""

    token_index: int
    kind: CandidateKind
    art_type: ArtFamily | None = None
    declension: Literal["weak", "mixed", "strong"] | None = None
    cell: Cell | None = None


Selector = Callable[[TaggedSentence], list[Candidate]]


def _art_type(token: Token) -> ArtFamily | None:
    """Classify a determiner-shaped token into the four families this
    taxonomy names, or ``None`` if it is a determiner tag spaCy's tagset
    cannot cleanly bucket without guessing.

    ``PIAT`` covers far more than "kein" -- "jeder", "manche", "viele",
    "alle" all get the same tag, and their own declension/agreement rules
    are out of this cycle's scope. Rather than mis-bucket them, only a
    "kein"-stemmed ``PIAT`` is recognised (checked by surface text, since
    ``facets.determiner_art_type`` classifies from a bare answer string, not
    a tagged token, and this cycle's own closed-class check needs to run
    against a token in sentence context); every other ``PIAT`` returns
    ``None`` and is silently excluded from every selector.
    """
    if token.tag == "PPOSAT" and token.morph.get("Poss") == "Yes":
        return "Poss"
    if token.tag == "ART":
        definite = token.morph.get("Definite")
        if definite == "Def":
            return "Def"
        if definite == "Ind":
            return "Ind"
        return None
    if token.tag == "PIAT" and token.text.lower().startswith(_KEIN_STEM):
        return "Neg"
    return None


def _cell(token: Token) -> Cell | None:
    """``(Case, Gender, Number)`` from ``token``'s own morphology, or
    ``None`` if any of the three is missing -- the tagger could not resolve
    the cell this topic needs, so there is nothing to select."""
    case = token.morph.get("Case")
    gender = token.morph.get("Gender")
    number = token.morph.get("Number")
    if not case or not gender or not number:
        return None
    return (case, gender, number)


def _preceded_by_adposition(sentence: TaggedSentence, index: int) -> bool:
    prev = sentence.token_before(index)
    return prev is not None and prev.pos == "ADP"


def _preceded_by_adposition_in(sentence: TaggedSentence, index: int, forms: frozenset[str]) -> bool:
    prev = sentence.token_before(index)
    return prev is not None and prev.pos == "ADP" and prev.text.lower() in forms


# ==============================================================================
# Determiner topics: artikel_*, kasus_*_formen, *_nach_praeposition,
# praepositionen_*.
# ==============================================================================

# ``preposition_gate="forbidden"`` means "must NOT be governed by any
# preposition" (the bare-case topics: a direct/indirect/possessive-genitive
# object, not a prepositional object -- kept disjoint from every
# preposition-governed topic by construction). A ``frozenset[str]`` means
# "must be immediately governed by a preposition whose lowercase text is in
# this set".
PrepositionGate = Literal["forbidden"] | frozenset[str]


def _determiner_selector(
    fixed_case: str,
    allowed_art_types: frozenset[ArtFamily],
    preposition_gate: PrepositionGate,
) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.tag not in _DETERMINER_TAGS:
                continue
            art_type = _art_type(token)
            if art_type is None or art_type not in allowed_art_types:
                continue
            cell = _cell(token)
            if cell is None or cell[0] != fixed_case:
                continue
            if isinstance(preposition_gate, str):  # the "forbidden" sentinel
                if _preceded_by_adposition(sentence, token.i):
                    continue
            elif not _preceded_by_adposition_in(sentence, token.i, preposition_gate):
                continue
            out.append(
                Candidate(token_index=token.i, kind="determiner", art_type=art_type, cell=cell)
            )
        return out

    return select


# ==============================================================================
# Adjective declension topics.
# ==============================================================================


# A fused preposition+definite-article token ("im"="in dem", "am"="an dem",
# "zur"="zu der", ...) is spaCy's APPRART tag: one token, no separate DET for
# any selector above to see, but grammatically always definite. An adjective
# right after one is therefore always weak declension ("im großen Garten"),
# never zero-article -- without this check it would fall straight through
# to "no determiner tag precedes" and be mis-selected as strong. Confirmed
# live: "Die Kinder spielen fröhlich im großen Garten." mis-selected
# "großen" as zero-article/strong before this check existed.
_FUSED_DEFINITE_PREPOSITION_TAG = "APPRART"


# Tokens after which an attributive adjective is unambiguously governed by a
# preceding determiner (weak: definite article, plain or fused with a
# preposition; mixed: ein-word). Any OTHER determiner-tagged token (an
# unrecognised PIAT like "jeder", or a demonstrative tagged PDAT/pos DET)
# leaves the declension genuinely undetermined by this cycle's closed-class
# rules, so it is excluded from both the weak/mixed triggers below AND the
# zero-article safe-context check -- "der alte, kranke Mann" and "jeder
# junge Student" both simply yield no candidate rather than a guessed
# declension.
def _preceding_declension_trigger(prev: Token | None) -> Literal["weak", "mixed"] | None:
    if prev is None:
        return None
    if prev.tag == _FUSED_DEFINITE_PREPOSITION_TAG:
        return "weak"
    art_type = _art_type(prev)
    if art_type == "Def":
        return "weak"
    if art_type in ("Ind", "Neg", "Poss"):
        return "mixed"
    return None


# A coordinating conjunction or comma leaves open the possibility that the
# REAL governing determiner sits earlier in a coordinated NP ("der alte und
# kranke Mann") -- not zero-article, just not visible one token back. Any
# other determiner-tagged token (unrecognised PIAT, PDAT demonstrative) is
# excluded the same way: the true declension may be governed by a rule this
# cycle does not model, not genuinely absent. The fused preposition+article
# tag is excluded too, for the same reason ``_preceding_declension_trigger``
# maps it to "weak" above: it is a determiner in disguise, not its absence.
_AMBIGUOUS_ZERO_CONTEXT_TAGS: frozenset[str] = frozenset(
    {"KON", "$,", _FUSED_DEFINITE_PREPOSITION_TAG}
)


def _is_safe_zero_article_context(prev: Token | None) -> bool:
    if prev is None:
        return True
    if prev.tag in _DETERMINER_TAGS or prev.pos == "DET":
        return False
    return prev.tag not in _AMBIGUOUS_ZERO_CONTEXT_TAGS


# An attributive adjective's own head noun may be one token away when
# another adjective intervenes ("der alte, kranke Mann"); ADJ keeps the
# check meaningful for chains while still catching a token spaCy mistagged
# as attributive with nothing nominal in sight at all.
_ADJECTIVE_HEAD_POS: frozenset[str] = frozenset({"NOUN", "PROPN", "ADJ"})


def _followed_by_nominal(sentence: TaggedSentence, index: int) -> bool:
    nxt = sentence.token_after(index)
    return nxt is not None and nxt.pos in _ADJECTIVE_HEAD_POS


def _adjective_selector(declension: Literal["weak", "mixed", "strong"]) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.tag != "ADJA":
                continue
            if not _followed_by_nominal(sentence, token.i):
                continue
            cell = _cell(token)
            if cell is None:
                continue
            prev = sentence.token_before(token.i)
            trigger = _preceding_declension_trigger(prev)
            if declension in ("weak", "mixed"):
                if trigger != declension:
                    continue
            else:  # strong / zero-article
                if trigger is not None or not _is_safe_zero_article_context(prev):
                    continue
            out.append(
                Candidate(token_index=token.i, kind="adjective", declension=declension, cell=cell)
            )
        return out

    return select


# ==============================================================================
# Comparative / superlative.
# ==============================================================================


def _select_komparativ_superlativ(sentence: TaggedSentence) -> list[Candidate]:
    """Predicative/adverbial comparative or superlative (``schneller``, ``am
    schnellsten``) -- deliberately NOT the attributive, declined form
    (``der schnellste Läufer``), which would conflate this topic's scope with
    the adjective-declension topics above. Each degree needs its own forcing
    element, matching the topic's own worked examples:

    * Comparative: an explicit ``als`` somewhere later in the sentence. A
      bare comparative with no ``als`` could just as well be an intensified
      positive in casual speech, which this topic does not test.
    * Superlative: immediately preceded by the fused particle "am" (the only
      periphrastic superlative construction; "der/die/das ...ste" is
      attributive and out of scope here for the same reason as above).
    """
    out: list[Candidate] = []
    lower_texts = [t.text.lower() for t in sentence.tokens]
    for token in sentence.tokens:
        if token.tag != "ADJD":
            continue
        degree = token.morph.get("Degree")
        if degree not in ("Cmp", "Sup"):
            continue
        if degree == "Cmp":
            if "als" not in lower_texts[token.i + 1 :]:
                continue
        else:
            prev = sentence.token_before(token.i)
            if prev is None or prev.text.lower() != "am":
                continue
        out.append(Candidate(token_index=token.i, kind="degree"))
    return out


SELECTORS: dict[str, Selector] = {
    "artikel_bestimmt_nom": _determiner_selector("Nom", frozenset({"Def"}), "forbidden"),
    "artikel_unbestimmt_kein_nom": _determiner_selector(
        "Nom", frozenset({"Ind", "Neg"}), "forbidden"
    ),
    "artikel_possessiv_nom": _determiner_selector("Nom", frozenset({"Poss"}), "forbidden"),
    "kasus_akkusativ_formen": _determiner_selector("Acc", _ALL_ART_TYPES, "forbidden"),
    "kasus_dativ_formen": _determiner_selector("Dat", _ALL_ART_TYPES, "forbidden"),
    "kasus_genitiv_formen": _determiner_selector("Gen", _ALL_ART_TYPES, "forbidden"),
    "akkusativ_nach_praeposition": _determiner_selector(
        "Acc", _ALL_ART_TYPES, _WECHSEL_PREPOSITIONEN
    ),
    "dativ_nach_praeposition": _determiner_selector("Dat", _ALL_ART_TYPES, _WECHSEL_PREPOSITIONEN),
    "praepositionen_akkusativ": _determiner_selector(
        "Acc", _ALL_ART_TYPES, _AKKUSATIV_PRAEPOSITIONEN
    ),
    "praepositionen_dativ": _determiner_selector("Dat", _ALL_ART_TYPES, _DATIV_PRAEPOSITIONEN),
    "praepositionen_genitiv": _determiner_selector("Gen", _ALL_ART_TYPES, _GENITIV_PRAEPOSITIONEN),
    "adjektivdeklination_bestimmt": _adjective_selector("weak"),
    "adjektivdeklination_unbestimmt": _adjective_selector("mixed"),
    "adjektivdeklination_nullartikel": _adjective_selector("strong"),
    "adjektiv_komparativ_superlativ": _select_komparativ_superlativ,
}
