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
from functools import lru_cache
from pathlib import Path
from typing import Literal

from src.generation.blanking import paradigms
from src.generation.blanking.paradigms import Cell
from src.generation.blanking.sentence_tagger import TaggedSentence, Token
from src.lexicon.frequency import FrequencyBander
from src.lexicon.lemmatizer import _MIN_COMPOUND_PART_LEN, compound_split_candidates, normalise
from src.taxonomy.facets import UNK as _FACETS_UNK

ArtFamily = Literal["Def", "Ind", "Neg", "Poss"]
CandidateKind = Literal[
    "determiner",
    "adjective",
    "degree",
    "personal_pronoun",
    "reflexive_pronoun",
    "relative_pronoun",
    "verb_form",
    "irregular_aux",
    "fixed_particle",
    "plural_noun",
]

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

# The formal/academic-register Genitive prepositions named in
# praepositionen_genitiv_gehoben's own rule_hint. "mangels" is tagged
# inconsistently by the small model in testing (sometimes PROPN, not ADP),
# so it is kept in the set for the sentences where the tagger does get it
# right rather than dropped outright -- a mistagged instance simply yields
# no candidate for that one sentence, same "reject rather than guess"
# posture as everywhere else in this module.
_GEHOBENE_GENITIV_PRAEPOSITIONEN: frozenset[str] = frozenset(
    {
        "anhand",
        "anlässlich",
        "anlaesslich",
        "infolge",
        "zugunsten",
        "bezüglich",
        "bezueglich",
        "gemäß",
        "gemaess",
        "mangels",
        "mittels",
    }
)


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
    # Cycle 3 additions (pronouns, verb conjugation): decided here, in the
    # selector, never re-derived in ``blanker.py`` from the raw token --
    # same separation of concerns as ``art_type``/``cell`` above, so a
    # selector's own classification decision (e.g. "this lemma belongs to
    # the vokalwechsel family, not the regular one") is never silently
    # re-guessed downstream.
    lemma: str | None = None
    person: str | None = None
    number: str | None = None
    gender: str | None = None
    verb_family: str | None = None
    tense_mood: str | None = None
    # docs/audits/cycle-04-report.md recommendation 5, and the same report's
    # first finding more generally: a bracketed lemma cue (the citation
    # form -- nominative singular for a noun, infinitive for a verb) that
    # rescues a closed-class slot the uniqueness gate (``uniqueness.py`)
    # would otherwise always skip as interchangeable, e.g. "meine ___
    # (Zahn)" for "Zähne", "___ (müssen) er noch arbeiten" for "muss".
    # docs/audits/cycle-05-report.md extends this to every OPEN-class
    # lexical-verb topic (``verb_praesens_regelm``, ``verb_praesens_
    # vokalwechsel``, ``verben_trennbar_praesens``, ``praeteritum_
    # vollverben``): a full verb is a strictly worse case of the same
    # defect a plural noun has, since essentially any semantically
    # plausible verb fits an ordinary sentence's finite-verb slot. Set only
    # by the selectors for the topics the two reports name; ``None``
    # everywhere else, including every candidate a topic's own selector
    # could not derive a *reliable* cue for -- see ``_citation_cue``,
    # ``_plural_noun_cue`` and ``_lexical_verb_lemma_trustworthy``'s own
    # docstrings for exactly what "reliable" means per part of speech.
    # Additive: nothing downstream is required to read this field, and
    # every existing candidate kind keeps emitting ``cue=None`` exactly as
    # before.
    cue: str | None = None
    # docs/audits/cycle-07-report.md section A: ``True`` only for a
    # ``determiner`` candidate whose selector already established a genuine
    # forcing anchor that rules out every RIVAL DETERMINER FAMILY, not
    # merely the blanked token's own case/gender cell -- currently
    # ``_select_artikel_unbestimmt_kein_nom``'s causal ``weil``-clause
    # negation (naming the referent's absence as the CAUSE of a stated
    # consequence rules out every other family: "der"/"ein"/"mein" would
    # all read backwards there) and ``_select_artikel_possessiv_nom``'s
    # possessive-person relative-clause anchor. ``uniqueness.py`` trusts
    # this instead of requiring a cue for exactly those two selectors' own
    # candidates; every other ``determiner`` candidate (including
    # ``artikel_bestimmt_nom``, whose own anchor only rules out the
    # indefinite family, not a possessive or demonstrative one -- see that
    # selector's own module-level comment) leaves this ``False`` and goes
    # through the ordinary cue-or-skip determiner policy instead. Additive,
    # same posture as ``cue`` itself: every other candidate kind keeps
    # emitting the dataclass default (``False``).
    lexeme_anchored: bool = False


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


# Categories transparent to a determiner's own agreement with its head noun
# -- an attributive adjective ("die schöne Tasse") or a bare degree/
# intensifier adverb modifying that adjective ("eine sehr schöne Tasse") --
# walked past by ``_determiner_head_noun`` below on the way to the noun that
# actually decides the determiner's own gender/number for cue purposes. Not
# the SAME transparent set as ``_DECLENSION_TRANSPARENT_SPAN_POS`` (that one
# is keyed by ``pos``, this one mixes a ``tag`` and a ``pos`` check because
# an attributive adjective is only unambiguously identified by its own tag,
# ``ADJA``, not by ``pos=="ADJ"`` alone, which also matches a predicative
# adjective this walk should not treat as transparent in the same way).
def _determiner_head_noun(sentence: TaggedSentence, det_index: int) -> Token | None:
    """The noun this determiner at ``det_index`` governs -- walked forward
    past zero or more attributive adjectives and degree adverbs, stopping at
    the first ``NOUN``/``PROPN`` token. ``None`` if the walk runs off the
    sentence or lands on anything else (a verb, a pronoun, a second
    determiner...) -- "reject rather than guess" applied to the source of
    the determiner cue's own gender/number, docs/audits/cycle-07-report.md
    section A's own instruction to derive the cue from the HEAD NOUN's own
    tagged gender/number, never from the blanked token's own morphology
    (which is exactly the fact the learner is meant to work out)."""
    tokens = sentence.tokens
    j = det_index + 1
    n = len(tokens)
    while j < n and (tokens[j].tag == "ADJA" or tokens[j].pos == "ADV"):
        j += 1
    if j >= n:
        return None
    head = tokens[j]
    if head.pos not in ("NOUN", "PROPN"):
        return None
    return head


def _determiner_cue(token: Token, art_type: ArtFamily) -> str | None:
    """The invariant citation form of the determiner's own FAMILY -- owner's
    decision (TODO.md section 2), reversing the previous cycle's design.
    Previously this was built from the determiner's own Nominative form
    AGREED TO THE HEAD NOUN's gender/number (``_determiner_head_noun``,
    still used elsewhere in this package -- see ``uniqueness.
    _possessive_governs_a_subject`` -- but no longer for this), which handed
    the learner the noun's gender for free and left only the case-form
    inflection to work out. It was also, independently, unreliable: the
    head noun's own tagged ``Gender``/``Number`` is read off spaCy's
    per-token morphology, which can disagree with the determiner's own
    (agreement-consistent) tag on the exact same noun phrase -- confirmed
    empirically (docs/audits/cycle-09-report.md's ``Orangensaft`` and
    ``Akku`` cues, both masculine, both cued as feminine "eine"/"die"):
    "einen ... Orangensaft" tags the determiner itself ``Case=Acc,
    Gender=Masc`` correctly throughout, while the same sentence's
    ``Orangensaft`` token is independently mistagged ``Gender=Fem``.

    The new rule needs no head noun at all, so that whole class of defect
    cannot recur here: always "der" for the definite article, always "ein"
    for the indefinite, "kein" for the negative, and -- for a possessive --
    the token's own uninflected stem ("mein"/"dein"/"sein"/"ihr"/"unser"/
    "euer"/"Ihr"), read directly off the blanked token's own surface text
    via ``paradigms.match_ein_word`` (the same stem-matching this module's
    paradigm lookups already use elsewhere), never guessed or agreed to
    anything else. "eur" (``match_ein_word``'s own contracted stem for an
    inflected "euer"-family form) is rendered back to the citation spelling
    "euer". Capitalisation (deciding "ihr" from formal "Ihr", which share
    every stem and ending) is resolved the same way every other cue in this
    module resolves a capitalisation question: matched to the actual
    blanked token's own case via ``_cue_case_matched_to_answer`` -- formal
    "Ihr"/"Ihre"/... is always capitalised in real German regardless of
    sentence position, so trusting the observed token's own case is not a
    guess for that family, and for a genuine sentence-initial "ihr"
    (her/their) it is the same accepted narrowing
    ``_cue_case_matched_to_answer``'s own docstring already documents for
    every other cue.

    Per TODO.md 2.2 (the owner's own words, recorded there verbatim), the
    cue happening to equal the answer -- true for roughly a fifth of
    ``artikel_bestimmt_nom`` items, where the blanked cell already IS the
    Nominative masculine cell the citation form names -- is no longer
    treated as a leak for a determiner slot; see
    ``blanker._determiner_outcome`` for where that exemption is applied.
    ``None`` only for a ``Poss`` candidate whose own token somehow does not
    match the ein-word paradigm at all -- not an expected runtime state
    (``_art_type``/``_determiner_selector`` only ever classify a token
    ``"Poss"`` once it already matched this exact family), kept as a
    reject-rather-than-guess backstop rather than an ``assert``."""
    if art_type == "Def":
        return _cue_case_matched_to_answer("der", token.text)
    if art_type == "Ind":
        return _cue_case_matched_to_answer("ein", token.text)
    if art_type == "Neg":
        return _cue_case_matched_to_answer("kein", token.text)
    match = paradigms.match_ein_word(token.text)
    if match is None:
        return None
    stem, _ending = match
    if stem == "eur":
        stem = "euer"
    return _cue_case_matched_to_answer(stem, token.text)


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
                Candidate(
                    token_index=token.i,
                    kind="determiner",
                    art_type=art_type,
                    cell=cell,
                    cue=_determiner_cue(token, art_type),
                )
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


# A demonstrative pronoun (PDS) is spelled identically to the definite
# article for every one of these six surface forms -- "der"/"die"/"das"/
# "den"/"dem"/"des" -- and docs/audits/cycle-06-report.md class E's third
# item is exactly this ambiguity, confirmed empirically, not assumed: the
# SAME "das von einem Maler gestaltete Arbeitszimmer" fragment tags "das"
# ART when it is the sentence's own grammatical subject/object position
# reached directly ("Wir hatten das ... besichtigt."), but PDS once any
# fronted clause or adverbial precedes the finite verb ("Weil ..., hatten
# wir das ... besichtigt." / "Gestern hatten wir das ... besichtigt.") --
# a POS-disambiguation flip driven entirely by clause position, not by this
# "das" itself changing grammatical role. Trusted as a weak-declension
# trigger here regardless of which of the two tags the parser settled on,
# because the two readings are not actually in tension for this module's
# one question: a demonstrative DETERMINER ("dieses Auto") triggers weak
# adjective declension in German exactly like the definite article does, so
# "weak" is the correct consequence of a PDS in this governing position
# under EITHER reading, not a guess between them.
_DEFINITE_ARTICLE_SURFACE_FORMS: frozenset[str] = frozenset(
    {"der", "die", "das", "den", "dem", "des"}
)


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
    if prev.tag == "PDS" and prev.text.lower() in _DEFINITE_ARTICLE_SURFACE_FORMS:
        return "weak"
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


# TODO.md 1.5: POS/tag values that end the current CLAUSE when walking
# backward from a candidate zero-article adjective -- a finite verb, an
# auxiliary, a subordinating or coordinating conjunction, or punctuation.
# Deliberately POS-based (not the single-tag ``_CLAUSE_BOUNDARY_TAG`` used
# elsewhere in this module, which only recognises a comma): the walk this
# boundary set gates needs to stop at a VERB too ("Der Mann kauft frisches
# Brot" must never let "Der" leak across "kauft" to license "frisches" as
# weak), which a comma-only boundary does not do.
_NOUN_PHRASE_BOUNDARY_POS: frozenset[str] = frozenset({"VERB", "AUX", "SCONJ", "CCONJ", "PUNCT"})


def _noun_phrase_governing_token(tok: Token) -> bool:
    """Whether ``tok`` is a determiner-shaped token that governs weak/mixed
    declension on whatever it precedes -- ``_art_type(tok) is not None``
    (the definite article, a "kein"-stemmed ``PIAT``, or a possessive)
    covers three of the four families this module already classifies; a
    demonstrative (``PDAT``, "diese"/"jener"/...) is added here because it
    is not one of ``_art_type``'s four families at all but still triggers
    weak declension exactly like the definite article does (the gap
    TODO.md 1.5 closes -- see ``_noun_phrase_has_governing_determiner``'s
    own docstring); the fused preposition+article tag is a determiner in
    disguise, same as everywhere else in this module.

    Deliberately NOT every ``PIAT``-tagged token -- an unrecognised
    quantifier ("jeden"/"manche"/"viele") is excluded by ``_art_type``
    itself, on purpose, matching ``_preceding_declension_trigger``'s own
    established precedent for the immediately-adjacent case: this
    module's declension rules only cover four determiner families, and a
    quantifier governing an unrelated noun phrase elsewhere in the clause
    ("jeden Abend") must never be mistaken for the determiner of a
    candidate's OWN noun phrase just because it happens to sit somewhere
    in between."""
    return (
        _art_type(tok) is not None
        or tok.tag == "PDAT"
        or tok.tag == _FUSED_DEFINITE_PREPOSITION_TAG
    )


def _noun_phrase_has_governing_determiner(sentence: TaggedSentence, adjective_index: int) -> bool:
    """Whether ANY determiner-shaped token governs the noun phrase
    ``adjective_index`` sits inside, found by scanning every token back to
    the start of the CURRENT CLAUSE -- not merely the tokens immediately
    adjacent to the candidate, and not gated on a "trustworthy span"
    allowlist between the candidate and a found determiner the way
    ``_find_governing_declension_trigger``/``_span_trusts_far_declension_
    trigger`` are.

    TODO.md 1.5, fourth cycle for this exact class: cycle 5 fixed an
    intervening prepositional phrase, cycle 7 fixed an intervening
    adverbial, and each fix extended the SAME backward scan by teaching it
    to tolerate one more specific shape of "what sits between the
    determiner and the candidate". "Wir möchten gerne wissen, ob Ihnen
    diese innovative ___ Lösung gefällt." defeated that scan a fourth time
    -- not because an intervening adjective ("innovative") is untolerated
    (``_DECLENSION_TRANSPARENT_SPAN_POS`` already allows ``ADJ``), but
    because "diese" is tagged ``PDAT`` (a demonstrative DETERMINER), which
    ``_preceding_declension_trigger``'s own PDS check does not cover: PDS
    is the demonstrative PRONOUN tag, only assigned when "der"/"die"/"das"
    stands alone; PDAT is what "diese"/"jener"/... always get, and nothing
    in the existing scan recognises it as a determiner at all, at any
    distance.

    Rather than extend that scan a fifth time, this asks the only question
    that actually matters for the zero-article/strong-declension case:
    does this noun phrase have a determiner ANYWHERE in it. Walked
    backward from immediately before ``adjective_index`` to the nearest
    clause boundary (``_NOUN_PHRASE_BOUNDARY_POS`` -- a verb, a
    conjunction, or punctuation), with no allowlist on what else sits in
    between, since a genuine intervening noun (an inserted PP's own
    object, e.g. "mit [dem vor wenigen Wochen] ___ Ball") is exactly the
    "anything in between" this walk is meant to see past, not stop at."""
    k = adjective_index - 1
    while k >= 0:
        tok = sentence.tokens[k]
        if tok.pos in _NOUN_PHRASE_BOUNDARY_POS:
            return False
        if _noun_phrase_governing_token(tok):
            return True
        if tok.tag == "PDS" and tok.text.lower() in _DEFINITE_ARTICLE_SURFACE_FORMS:
            return True
        k -= 1
    return False


# A span between a far-found governing determiner and the candidate
# adjective/participle is trustworthy when every token in it is either part
# of a genuine inserted PP (``ADP`` present, the ``von einem Maler`` shape
# ``_find_governing_declension_trigger`` was originally built for) or is
# ITSELF transparent to declension -- another attributive adjective in a
# chain ("die erste ___ Besprechung": pos ``ADJ``), or a bare degree/
# intensifier adverb modifying the candidate directly ("eine viel ___
# Zukunft": pos ``ADV``, and this is also where an ADJD used adverbially,
# e.g. "wirklich", lands, since German's own POS scheme gives predicative/
# adverbial adjectives pos ``ADV`` not ``ADJ``). docs/audits/
# cycle-06-report.md class E's first two items are exactly these two
# shapes, confirmed live: the far determiner was already being FOUND by
# ``_find_governing_declension_trigger`` (that walk does not stop at an
# adjective or an adverb), it was only ever being DISCARDED afterwards by
# the old "span must contain an ADP" gate, which the PP case alone needs
# and the adjective-chain/quantifier case never had.
#
# An allowlist, not "anything that is not a boundary", on purpose: a verb,
# a noun, or a pronoun in the span means the walk crossed into unrelated
# material (a different clause's own words, or a genuinely separate NP),
# and the found determiner must not be trusted then -- "reject rather than
# guess" applied to a token category this module has not considered yet,
# same as everywhere else here, by defaulting new/unknown span content to
# rejection instead of silent acceptance.
_DECLENSION_TRANSPARENT_SPAN_POS: frozenset[str] = frozenset({"ADJ", "ADV"})


def _span_trusts_far_declension_trigger(span: tuple[Token, ...]) -> bool:
    if any(t.pos == "ADP" for t in span):
        return True
    return all(t.pos in _DECLENSION_TRANSPARENT_SPAN_POS for t in span)


def _adjective_selector(declension: Literal["weak", "mixed", "strong"]) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.tag != "ADJA":
                continue
            # docs/audits/cycle-07-report.md defect 2: "euer" (a possessive
            # DETERMINER, "your") is mistagged ``ADJA`` by this exact model
            # in some contexts ("Gegen euer Unwohlsein ..."), which the bare
            # tag check above cannot catch -- it already IS "ADJA" by the
            # tagger's own (wrong) call. A possessive/indefinite/negative
            # determiner is a closed, fully enumerable family
            # (``paradigms.match_ein_word``'s own stem list); no genuine
            # German ADJECTIVE is spelled as one of those stems plus a valid
            # ending (they are function words, not descriptive adjectives),
            # so any token that parses as a member of that family is
            # rejected here regardless of what tag the tagger assigned it --
            # the actual attributive-adjective requirement the topic needs,
            # not merely the tag that usually, but not always, signals it.
            if paradigms.match_ein_word(token.text) is not None:
                continue
            if not _followed_by_nominal(sentence, token.i):
                continue
            # docs/audits/cycle-07-report.md section B: a comparative or
            # superlative attributive adjective ("bester", "bessere") shares
            # its lemma with the positive form ("gut"), so a "(gut)" cue
            # would not distinguish "guter" from "bester" -- the item stays
            # ambiguous even with a cue. That ambiguity belongs to
            # ``adjektiv_komparativ_superlativ`` instead, so a non-Pos degree
            # is excluded here rather than cued. A missing ``Degree`` (never
            # observed for an ``ADJA`` token in testing, but not assumed
            # impossible) is treated as positive rather than rejected --
            # this filter only ever excludes a CONFIRMED comparative/
            # superlative, never an unresolved one.
            degree = token.morph.get("Degree")
            if degree and degree != "Pos":
                continue
            cell = _cell(token)
            if cell is None:
                continue
            prev = sentence.token_before(token.i)
            trigger = _preceding_declension_trigger(prev)
            if trigger is None:
                # No determiner immediately adjacent -- but one may still
                # govern this adjective through an intervening phrase or
                # word ("das [von einem Maler] ___ Arbeitszimmer", "die
                # [erste] ___ Besprechung", "eine [viel] ___ Zukunft").
                # Reuses the same bounded backward scan built for
                # ``partizip_ii_attributiv_erweitert`` after the first of
                # those traps, gated by ``_span_trusts_far_declension_
                # trigger`` (see its own docstring) so a plain,
                # non-extended zero-article reading ("frische Milch") is
                # never second-guessed by an unrelated determiner several
                # tokens further back across unrelated material.
                found = _find_governing_declension_trigger(sentence, token.i)
                if found is not None:
                    determiner_index, far_trigger = found
                    span = sentence.tokens[determiner_index + 1 : token.i]
                    if _span_trusts_far_declension_trigger(span):
                        trigger = far_trigger
            if declension in ("weak", "mixed"):
                if trigger != declension:
                    continue
            else:  # strong / zero-article
                if trigger is not None or not _is_safe_zero_article_context(prev):
                    continue
                # TODO.md 1.5: a thorough, distance-independent check,
                # additional to (not a replacement for) the two checks
                # above -- see ``_noun_phrase_has_governing_determiner``'s
                # own docstring for why extending the backward-scan
                # machinery those two checks share a fifth time was the
                # wrong fix.
                if _noun_phrase_has_governing_determiner(sentence, token.i):
                    continue
            # docs/audits/cycle-07-report.md section B: the adjective's own
            # uninflected positive base form, the same fact
            # ``partizip_i_attributiv``/``partizip_ii_attributiv_erweitert``
            # already cue with (their own infinitive) -- here the lemma
            # itself is already that base form (an ADJA's lemma is never the
            # inflected surface form the way a verb's finite-form lemma can
            # be), so no extra table lookup is needed. Delegated to
            # ``_citation_cue`` for the same equals-the-answer guard and
            # dictionary reality check every other cue in this module goes
            # through -- never equal in practice, since the base form always
            # lacks the declension ending the inflected surface form carries.
            cue = _citation_cue(token.lemma, token.text) if token.lemma else None
            out.append(
                Candidate(
                    token_index=token.i,
                    kind="adjective",
                    declension=declension,
                    cell=cell,
                    cue=cue,
                )
            )
        return out

    return select


# ==============================================================================
# Comparative / superlative.
# ==============================================================================

# Adjective lemmas confirmed, by direct testing against this exact model,
# to mislemmatise a comparative/superlative ADJD back to something other
# than the true positive form -- the same "self-consistent tagger bug" class
# as ``_MISLEMMATIZED_VERB_LEMMAS``, found the same way (each entry
# individually confirmed, never guessed at in bulk; a live, LLM-generated
# corpus is unbounded and a future audit may find more). Two distinct
# failure shapes, both real:
#
# * "lieb" (from "lieber"/"liebsten") -- not a lemmatiser artefact at all,
#   but a genuinely WRONG citation form: "gern - lieber - am liebsten" is a
#   suppletive comparison (the positive form is a different word, "gern",
#   not a comparative/superlative of "lieb"), and de_core_news_sm resolves
#   the comparative/superlative surface to the unrelated, real adjective
#   "lieb" ("dear") instead. Cueing "(lieb)" would point the learner at the
#   wrong word entirely, not a spelling variant of the right one.
# * "kält"/"kälte"/"wärm"/"jüng" (from "kälter"/"kälteste"/"wärmste"/
#   "jüngste") -- unreduced or wrongly-reduced UMLAUT stems the lemmatiser
#   fails to map back to their true positive ("kalt"/"warm"/"jung"); none of
#   these are real German words, and none equal the surface form either, so
#   ``_citation_cue``'s own equals-the-answer guard does not catch them.
_MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS: frozenset[str] = frozenset(
    {"lieb", "kält", "kälte", "wärm", "jüng"}
)


def _degree_cue(token: Token) -> str | None:
    """The base (positive) citation form for a blanked comparative/
    superlative -- docs/audits/cycle-06-modal-leak.md's cue extension:
    without one, any comparative/superlative fits the slot equally well
    ("er läuft ___ als sein Bruder" admits schneller/langsamer/besser
    alike). Trusted only when it clears the same two-part reliability
    standard as ``_lexical_verb_lemma_trustworthy``/``_plural_noun_cue``:
    differs from the surface form (delegated to ``_citation_cue``, and this
    doubles as the "genuinely invariant" guard) and is not one of the
    lemmas confirmed mislemmatised for this exact model above."""
    lemma = token.lemma
    if not lemma or lemma.lower() in _MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS:
        return None
    return _citation_cue(lemma, token.text)


def _select_komparativ_superlativ(sentence: TaggedSentence) -> list[Candidate]:
    """Predicative/adverbial comparative or superlative (``schneller``, ``am
    schnellsten``) -- deliberately NOT the attributive, declined form
    (``der schnellste Läufer``), which would conflate this topic's scope with
    the adjective-declension topics above. Each degree needs its own forcing
    element, matching the topic's own worked examples:

    * Comparative: an explicit comparative ``als`` somewhere later in the
      sentence. A bare comparative with no ``als`` could just as well be an
      intensified positive in casual speech, which this topic does not
      test.

      docs/audits/cycle-09-report.md 1.7's own found item: "Später trinken
      wir dann gemeinsam eine Tasse Kaffee als kleines Dankeschön." was
      wrongly filed here because it literally contains the WORD "als" --
      but "als kleines Dankeschön" means "as a small thank-you", not a
      comparison, and "Später" here means "afterwards", not "more late
      than". German's comparative "als" ("than") and its unrelated
      "as"/"in the role of" homograph are not the same token to the
      tagger, though: confirmed empirically, spaCy's own fine-grained tag
      already tells the two apart -- comparative "als" ("schneller ALS
      sein Bruder") tags ``KOKOM``, the "as"-sense ("ALS kleines
      Dankeschön", "ALS Lehrer verdient er ...", "kenne ihn ALS
      freundlichen Mann") tags ``APPR`` -- so requiring the specific tag
      closes this without inventing a new heuristic.
    * Superlative: immediately preceded by the fused particle "am" (the only
      periphrastic superlative construction; "der/die/das ...ste" is
      attributive and out of scope here for the same reason as above).

    ``cue`` is the derived positive-form citation cue (see ``_degree_cue``),
    ``None`` wherever that cannot be trusted -- the candidate is still
    returned either way, matching every other cued kind in this module.
    """
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "ADJD":
            continue
        degree = token.morph.get("Degree")
        if degree not in ("Cmp", "Sup"):
            continue
        if degree == "Cmp":
            has_comparative_als = any(
                t.text.lower() == "als" and t.tag == "KOKOM" for t in sentence.tokens[token.i + 1 :]
            )
            if not has_comparative_als:
                continue
        else:
            prev = sentence.token_before(token.i)
            if prev is None or prev.text.lower() != "am":
                continue
        out.append(Candidate(token_index=token.i, kind="degree", cue=_degree_cue(token)))
    return out


# ==============================================================================
# Cycle 3: pronouns (personal, reflexive, relative).
# ==============================================================================

# The 1st/2nd person Accusative/Dative pronoun forms are genuinely
# ambiguous with the reflexive paradigm on the surface ("mich" is both "me"
# and reflexive-"myself") -- 3rd person never is, since German's only 3rd
# person reflexive form is "sich", distinct from "ihn"/"sie"/"es"/"ihnen".
_AMBIGUOUS_REFLEXIVE_FORMS: frozenset[str] = frozenset(
    {"mich", "dich", "uns", "euch", "mir", "dir"}
)


def _finite_verb_person_number(sentence: TaggedSentence) -> tuple[str, str] | None:
    """The sentence's finite verb's own ``(Person, Number)``, if every
    finite verb in the sentence agrees on it, else ``None``. Used to tell a
    genuinely non-reflexive personal pronoun apart from an ambiguous form
    that happens to share the subject's person/number (reflexive pronouns
    always agree with their own clause's subject; a personal pronoun that
    does NOT agree with the subject cannot be reflexive) -- a real mismatch
    is proof of non-reflexivity; agreement is treated as "could be either",
    which the personal-pronoun selectors below reject rather than guess."""
    found: set[tuple[str, str]] = set()
    for token in sentence.tokens:
        if token.pos not in ("VERB", "AUX"):
            continue
        if token.morph.get("VerbForm") != "Fin":
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if person and number:
            found.add((person, number))
    if len(found) == 1:
        return next(iter(found))
    return None


def _personal_pronoun_selector(fixed_case: str) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.pos != "PRON":
                continue
            if token.morph.get("Case") != fixed_case:
                continue
            person, number = token.morph.get("Person"), token.morph.get("Number")
            if not person or not number:
                continue
            lower = token.text.lower()
            if lower == "sich":  # never a personal-pronoun form
                continue
            if token.morph.get("Reflex") == "Yes":
                continue
            if lower in _AMBIGUOUS_REFLEXIVE_FORMS:
                subject = _finite_verb_person_number(sentence)
                if subject is None or subject == (person, number):
                    continue  # could be reflexive here -- reject, don't guess
            out.append(
                Candidate(
                    token_index=token.i,
                    kind="personal_pronoun",
                    person=person,
                    number=number,
                    gender=token.morph.get("Gender"),
                )
            )
        return out

    return select


# The reflexive selectors below deliberately do NOT trust spaCy's own
# ``Case``/``Reflex`` morphology for the reflexive pronoun itself --
# confirmed unreliable in both directions during testing: "Er kauft sich
# ein neues Auto" tags "sich" ``Case=Acc`` (it is Dative: the direct object
# is "ein neues Auto"), while "Er schämt sich jeden Morgen" tags it
# ``Case=Dat`` (it is Accusative: "schämen" always takes an Accusative
# reflexive, and "jeden Morgen" is a time adverbial, not an object). Both
# would silently misattribute the item to the wrong one of these two
# topics if trusted.
#
# Case is instead derived from the GOVERNING VERB's own argument structure
# (``_governing_verb_lemma`` plus the closed ``paradigms.DATIVE_REFLEXIVE_
# VERBS_*`` lists) -- docs/audits/cycle-04-report.md's second finding: an
# earlier version of this module decided Dative-vs-Accusative purely from
# whether SOME accusative object sat anywhere in the sentence, which wrongly
# promoted three genuinely Accusative-reflexive verbs ("sich freuen", "sich
# treffen", "sich ändern") to Dative whenever an unrelated accusative
# happened to appear elsewhere (even in a different clause). The governing
# verb's own lemma is the actual fact that decides it; the accusative-object
# signal is now only consulted, and only within the SAME CLAUSE
# (``_clause_span``), for the specific verbs from
# ``paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` that are genuinely
# polysemous between an Accusative-reflexive reading and a "Dative reflexive
# plus its own Accusative object" reading depending on whether that object
# is present ("sich vorstellen" alone = introduce oneself, Accusative;
# "sich (Dat) etwas vorstellen" = imagine something, Dative).
#
# The closed, unambiguous-by-spelling forms ("mich"/"dich" are only ever
# Accusative, "mir"/"dir" only ever Dative) are cross-checked against the
# governing-verb-derived case as a second, independent safety net: a
# mismatch means either the clause-boundary heuristic or the governing-verb
# lookup got it wrong, and "reject rather than guess" applies there exactly
# as everywhere else in this module. Agreement with the sentence's own
# finite verb (person/number must MATCH, not merely "not conflict" -- a
# reflexive pronoun always agrees with its subject by definition) is a
# further, separate safety net against the same kind of tagger error, kept
# from the previous version of this module unchanged.
_REFLEXIVE_CASE_BY_FORM: dict[str, str] = {"mich": "Acc", "dich": "Acc", "mir": "Dat", "dir": "Dat"}
_REFLEXIVE_PERSON_NUMBER_BY_FORM: dict[str, tuple[str, str]] = {
    "mich": ("1", "Sing"),
    "mir": ("1", "Sing"),
    "dich": ("2", "Sing"),
    "dir": ("2", "Sing"),
    "uns": ("1", "Plur"),
    "euch": ("2", "Plur"),
}
_REFLEXIVE_CAPABLE_FORMS: frozenset[str] = frozenset(
    {"mich", "dich", "sich", "uns", "euch", "mir", "dir"}
)

# Temporal accusatives ("jeden Morgen", "letzte Woche") are extremely common
# and are NOT direct objects -- exempted from the "bare accusative object"
# check below so they never masquerade as the object that would force a
# Dative reading. The seven weekday names ("jeden Samstag") are the same
# shape and were a confirmed gap (docs/audits/cycle-07-report.md defect 8's
# own fix, generalising the accusative-object check to every reflexive verb,
# surfaced it: "Wir treffen uns jeden Samstag im Park." was wrongly promoted
# to Dative because "Samstag" is not in this set).
#
# TODO.md 1.1 widens this further: reused directly from
# ``paradigms.TEMPORAL_ANCHOR_LEMMAS`` (the same closed list an unrelated
# topic-anchoring check already carries, per the module's own "no new
# linguistic facts" standard) rather than kept as an independently
# maintained literal set, and topped up with the handful of common
# clock/calendar nouns confirmed missing from that list by direct testing
# against this exact bug class: "Uhr" ("Um zwei Uhr treffen wir uns..." --
# "zwei Uhr" is a clock-time accusative, not an object, and was wrongly
# promoting "treffen" to Dative) and "Nachmittag" ("... treffen sich heute
# Nachmittag..." -- the SAME defect, a different compound-with-"Mittag"
# time noun ``TEMPORAL_ANCHOR_LEMMAS`` does not carry either, since that
# list's own words are all invariant adverbs/anchors, not every declinable
# time noun a bare accusative NP can be built from).
_TEMPORAL_ACCUSATIVE_LEMMAS: frozenset[str] = paradigms.TEMPORAL_ANCHOR_LEMMAS | frozenset(
    {
        "morgen",
        "tag",
        "woche",
        "monat",
        "jahr",
        "abend",
        "nacht",
        "stunde",
        "minute",
        "sekunde",
        "uhr",
        "nachmittag",
        "mittag",
        "vormittag",
    }
    | {"montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"}
)

# TODO.md 1.1: a quantifier pronoun in APPOSITION to the clause's own
# subject ("wir ... uns ALLE vor dem Haupteingang", "sie kommen BEIDE") is
# not a direct object of the verb at all, even when spaCy tags it
# Accusative (confirmed: "alle" in exactly that sentence tags
# ``Case=Acc``, agreeing with the reflexive rather than the Nominative
# subject it actually restates) -- excluded from the accusative-object scan
# by lemma, the same "reject rather than guess" posture as the temporal
# exclusion above, not by trying to detect apposition structurally without
# a dependency parse.
_APPOSITIONAL_QUANTIFIER_LEMMAS: frozenset[str] = frozenset({"alle", "beide"})

# Determiner/adjective tags that sit INSIDE a noun phrase, between a
# governing preposition and the noun itself ("für sein Verhalten" -- "sein"
# sits between "für" and "Verhalten") -- walked past by
# ``_governed_by_adposition`` below so a preposition two tokens back is
# still found, unlike the single-token ``_preceded_by_adposition`` used for
# the determiner-shaped topics above (which only ever look at a bare
# determiner immediately after its preposition, so one token back is
# always enough there).
_NP_INTERNAL_TAGS: frozenset[str] = frozenset({"ART", "PIAT", "PPOSAT", "ADJA", "PDAT"})


def _governed_by_adposition(sentence: TaggedSentence, index: int) -> bool:
    j = index - 1
    while j >= 0 and sentence.tokens[j].tag in _NP_INTERNAL_TAGS:
        j -= 1
    return j >= 0 and sentence.tokens[j].pos == "ADP"


def _has_bare_accusative_object(
    sentence: TaggedSentence, exclude_index: int, clause_start: int, clause_end: int
) -> bool:
    """Whether a genuine direct object -- not a time adverbial, not an
    apposition to the subject, not anything the tagger itself heads as
    Nominative -- sits in ``[clause_start, clause_end)`` besides
    ``exclude_index`` (the reflexive pronoun itself). ``other.morph.get
    ("Case") != "Acc"`` already excludes anything the tagger heads
    Nominative by construction (only an ``Acc``-tagged token reaches the
    checks below at all); TODO.md 1.1 adds the other two directions
    docs/audits/cycle-09-report.md found this scan wrongly treating as an
    object: a bare accusative time expression (``_TEMPORAL_ACCUSATIVE_
    LEMMAS``) and a quantifier pronoun in apposition to the subject
    (``_APPOSITIONAL_QUANTIFIER_LEMMAS``, "alle"/"beide")."""
    for other in sentence.tokens[clause_start:clause_end]:
        if other.i == exclude_index:
            continue
        if other.pos not in ("NOUN", "PROPN", "PRON"):
            continue
        if other.morph.get("Case") != "Acc":
            continue
        if other.lemma.lower() in _TEMPORAL_ACCUSATIVE_LEMMAS:
            continue
        if other.lemma.lower() in _APPOSITIONAL_QUANTIFIER_LEMMAS:
            continue
        if _governed_by_adposition(sentence, other.i):
            continue
        return True
    return False


def _immediately_followed_by_object_np(
    sentence: TaggedSentence, index: int, clause_end: int
) -> bool:
    """Whether an explicit-determiner noun phrase ("ein neues Auto", "die
    Hände") sits directly after ``index``, still within the same clause
    (``clause_end``), with nothing between -- a second, Case-independent
    signal for the same "is there a bare accusative object" question
    ``_has_bare_accusative_object`` answers from ``token.morph``.

    Confirmed empirically that this is necessary, not merely
    belt-and-braces: in "Er kauft sich ein neues Auto.", spaCy tags the
    ENTIRE object NP "ein neues Auto" as ``Case=Nom`` (subject-shaped,
    wrongly), so ``_has_bare_accusative_object`` alone finds nothing and
    the reflexive would default to Accusative -- the wrong case, not merely
    a missed item. Word order is the fallback signal: a determiner/adjective
    span immediately after the reflexive pronoun, ending in a noun, that is
    not part of a prepositional phrase, is reliably an object NP in a plain
    declarative clause. Deliberately requires at least one determiner or
    adjective token (not a completely bare noun) before accepting, so as
    not to fire on rarer bare-predicate-noun constructions ("Er nennt sich
    Künstler.", where "Künstler" is a predicate, not an object, and "sich"
    really is Accusative) -- this narrows recall further but keeps the same
    "reject rather than guess" bias as the rest of this module.
    """
    start = index + 1
    if start >= clause_end:
        return False
    if sentence.tokens[start].pos == "ADP":
        return False
    j = start
    while j < clause_end and sentence.tokens[j].tag in _NP_INTERNAL_TAGS:
        j += 1
    if j == start or j >= clause_end:
        return False
    tok = sentence.tokens[j]
    if tok.pos not in ("NOUN", "PROPN"):
        return False
    return tok.lemma.lower() not in _TEMPORAL_ACCUSATIVE_LEMMAS


_CLAUSE_BOUNDARY_TAG = "$,"


def _clause_span(sentence: TaggedSentence, index: int) -> tuple[int, int]:
    """The token index range ``[start, end)`` of ``index``'s own clause,
    bounded by the nearest comma on either side (or the sentence's own
    edges). This package's tagger deliberately excludes the dependency
    parser (``sentence_tagger.py``'s own docstring), so there is no parse
    tree to read a real clause boundary from; a comma is the reliable proxy
    available instead -- German subordinate clauses are conventionally
    comma-set-off in standard written prose, and this cycle's sentences are
    exactly that (LLM-generated, then carrier-validated German), not
    colloquial text where a clause boundary might go unmarked. A clause
    joined to its neighbour by a coordinating conjunction with no comma
    ("und", "oder" in a short clause) is not specially detected here; it
    simply yields a span containing more than one finite verb, which
    ``_governing_verb_lemma`` already treats as undetermined rather than
    guessing which one governs."""
    start = index
    while start > 0 and sentence.tokens[start - 1].tag != _CLAUSE_BOUNDARY_TAG:
        start -= 1
    end = index
    n = len(sentence.tokens)
    while end < n and sentence.tokens[end].tag != _CLAUSE_BOUNDARY_TAG:
        end += 1
    return start, end


# German 2nd-person plural imperative is spelled identically to the present
# indicative ("helft"/"helft") -- spaCy tags it ``VVIMP``/``VAIMP``/``VMIMP``
# with no ``VerbForm`` at all rather than ``VerbForm=Fin``, so a competing
# predicate tagged this way must be counted alongside a genuine
# ``VerbForm=Fin`` finite verb wherever "exactly one finite verb in this
# span" is the signal being checked (see ``_governing_verb_lemma``'s own
# docstring for the confirmed defect this closes).
_IMPERATIVE_TAGS: frozenset[str] = frozenset({"VVIMP", "VAIMP", "VMIMP"})


def _governing_verb_lemma(
    sentence: TaggedSentence, clause_start: int, clause_end: int
) -> str | None:
    """The lexeme that actually governs a reflexive pronoun inside
    ``[clause_start, clause_end)`` (see ``_clause_span``): the clause's one
    finite verb, resolved through an auxiliary or modal to the participle or
    infinitive that carries the real lexical meaning ("hat ... gekauft" ->
    "kaufen", "will ... treffen" -> "treffen", "würde ... freuen" ->
    "freuen"), never the auxiliary/modal's own lemma -- an auxiliary or
    modal has no reflexive-object argument structure of its own to look up.
    A plain, non-auxiliary finite verb ("wir treffen uns") is returned
    directly.

    More or fewer than one finite verb in the span -- a mis-split clause, an
    ``und``-joined pair with no comma between them -- is treated as
    undetermined (``None``), and so is a finite auxiliary/modal with no
    participle or infinitive found in the same span (an incomplete or
    unparseable construction): "reject rather than guess" applied to
    governing-verb resolution itself, exactly like every other structural
    signal in this module. This is also this function's own answer to "where
    the governing verb cannot be determined, skip" -- the caller
    (``_reflexive_case``) does exactly that on a ``None`` return.

    A second, ``und``-joined clause's own predicate counts as a competing
    finite verb here even when spaCy tags it ``VVIMP``/``VAIMP``/``VMIMP``
    (imperative) with no ``VerbForm`` at all, not only ``VerbForm=Fin``.
    Confirmed necessary against this exact model: "Ihr gebt aber nicht auf
    und helft euch gegenseitig." tags "helft" as an imperative (its 2nd
    person plural form is spelled identically to the present indicative),
    which a ``VerbForm=Fin``-only filter simply does not see -- so the span
    (no comma before "und") looked like it had exactly one finite verb
    ("gebt"/"aufgeben") and this function wrongly resolved through it
    instead of returning ``None`` for the genuinely two-predicate clause it
    actually is."""
    finite = [
        t
        for t in sentence.tokens[clause_start:clause_end]
        if t.pos in ("VERB", "AUX")
        and (t.morph.get("VerbForm") == "Fin" or t.tag in _IMPERATIVE_TAGS)
    ]
    if len(finite) != 1:
        return None
    lemma = finite[0].lemma.lower()
    if not lemma:
        return None
    is_modal = lemma in paradigms.MODAL_LEMMAS or lemma == "möchten"
    if is_modal or lemma in ("haben", "sein", "werden"):
        non_finite = next(
            (
                t
                for t in sentence.tokens[clause_start:clause_end]
                if t.tag in _MODAL_INFINITIVE_TAGS or t.tag == "VVPP"
            ),
            None,
        )
        if non_finite is None or not non_finite.lemma:
            return None
        # An infinitive/participle is already written as one fused word
        # ("angesehen", "ansehen") -- confirmed empirically that spaCy
        # lemmatises these correctly, unlike the bare-finite branch below.
        return non_finite.lemma.lower()
    # A separable-prefixed verb in PLAIN present/preterite main-clause word
    # order splits ("Ich sehe den Film an."), and confirmed empirically that
    # spaCy's own lemma for the finite half alone drops the prefix entirely
    # ("sehe" -> "sehen", not "ansehen") -- unlike the infinitive/participle
    # case just above, there is no fused word to lemmatise correctly in the
    # first place. The separated prefix is still right there as its own
    # token (tag ``PTKVZ``, its own lemma the bare prefix text), so it is
    # reconstructed by simple concatenation rather than trusting the
    # (confirmed wrong) bare lemma alone -- this is exactly how the verb is
    # spelled in its own infinitive/citation form, not a guess.
    prefix = next((t for t in sentence.tokens[clause_start:clause_end] if t.tag == "PTKVZ"), None)
    if prefix is not None and prefix.lemma:
        return prefix.lemma.lower() + lemma
    return lemma


def _followed_by_dass_clause_object(sentence: TaggedSentence, clause_end: int) -> bool:
    """Whether a ``dass``-introduced subordinate clause immediately follows
    the reflexive verb's own clause -- TODO.md 1.1: the object of a verb
    like "sich (Dat) etwas wünschen" is not always a noun phrase in the
    SAME clause the reflexive pronoun sits in; "Wir wünschen uns, dass Sie
    uns Ihre ehrliche Meinung mitteilen." has its entire object as a
    subordinate clause, which neither ``_has_bare_accusative_object`` (a
    same-clause NP scan) nor ``_immediately_followed_by_object_np`` (an
    NP-shape check) can see by construction -- neither looks past the
    clause boundary at all. Three of docs/audits/cycle-09-report.md's six
    reflexive-routing defects were exactly this, the same clause-scoped
    object test simply never being asked the right question. ``clause_end``
    is expected to be the index of the boundary comma itself
    (``_clause_span``'s own return value), so the clause immediately
    following starts at ``clause_end + 1``.

    ``dass`` specifically, not any other subordinator: it is the one that
    marks a genuine NOUN-CLAUSE object standing in for an argument the verb
    selects for, unlike a ``weil`` reason clause or a ``wenn`` conditional,
    neither of which is itself the thing the verb takes as an object."""
    n = len(sentence.tokens)
    if clause_end >= n or sentence.tokens[clause_end].tag != _CLAUSE_BOUNDARY_TAG:
        return False
    after = clause_end + 1
    return after < n and sentence.tokens[after].text.lower() == "dass"


def _reflexive_case(sentence: TaggedSentence, token: Token) -> str | None:
    """The reflexive pronoun's own Case, decided by its governing verb's
    argument structure (see the module-level comment above
    ``_REFLEXIVE_CASE_BY_FORM``), never by scanning for an accusative object
    with no regard to which verb actually governs it. Returns ``None`` --
    reject, don't guess -- when the governing verb cannot be determined at
    all, or when an unambiguous-by-spelling form ("mich"/"dich"/"mir"/"dir")
    contradicts what the governing verb says, which means one of the two
    structural signals got this sentence wrong and neither should be
    trusted alone."""
    lower = token.text.lower()
    clause_start, clause_end = _clause_span(sentence, token.i)
    verb_lemma = _governing_verb_lemma(sentence, clause_start, clause_end)
    if verb_lemma is None:
        return None
    if verb_lemma in paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT:
        verb_case = "Dat"
    elif verb_lemma in paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS:
        # TODO.md 1.1: a verb known to be genuinely Accusative-reflexive-
        # only never takes a further object at all, so the object scan
        # below is not merely unnecessary for these, it is actively unsafe
        # -- see ``paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS``'s own
        # comment for the confirmed tagger-mistagging case ("viel Staub",
        # the genuine Nominative subject of "sich ansammeln", tagged
        # ``Case=Acc``) that a Case-based object scan cannot tell apart
        # from a real object for exactly this verb.
        verb_case = "Acc"
    else:
        # docs/audits/cycle-07-report.md defect 8: "sich (Dat) etwas tun" is
        # not limited to the closed ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT``
        # list -- it is the general benefactive-dative construction, and it
        # applies to essentially any transitive verb, not only the handful
        # the list happens to name. "den er sich frisch gekocht hatte" (a
        # relative clause: "er hatte sich einen Kaffee gekocht", the
        # accusative object fronted as the relative pronoun "den") is
        # exactly this shape with "kochen", a verb the cycle-5 list never
        # covered because it is not lexically dative-reflexive at all --
        # this is the STRUCTURAL case the list was never meant to handle
        # (see that list's own module comment in ``paradigms.py``: it names
        # verbs "genuinely POLYSEMOUS between an Accusative-reflexive
        # reading and a Dative one", which "kochen" is not -- "sich kochen"
        # alone is not idiomatic Accusative-reflexive German at all, so
        # restricting the object-presence check to that list only ever
        # under-covers this construction, never over-covers it). Checking
        # ``_has_bare_accusative_object`` for every verb not lexically
        # Dative-only closes that gap: an accusative-reflexive verb
        # genuinely never
        # co-occurs with a further accusative object in its own clause (the
        # cycle-4 report's own three counter-examples -- "sich freuen",
        # "sich treffen", "sich ändern" -- are all intransitive-reflexive
        # and simply never trip this check), so widening it costs nothing
        # for those and closes this defect for every OTHER transitive verb.
        #
        # Widened to the CASE-based signal only (``_has_bare_accusative_
        # object``), not the WORD-ORDER fallback (``_immediately_followed_
        # by_object_np``): confirmed necessary while building this fix --
        # "Er sagte, dass sich die Situation geändert hat." has "die
        # Situation" (correctly tagged ``Case=Nom``, the genuine SUBJECT of
        # this intransitive-reflexive verb) sitting immediately after
        # "sich" purely from ordinary subordinate-clause word order (the
        # reflexive object preceding a "heavy" subject), which the
        # word-order fallback cannot tell apart from a genuine object NP by
        # shape alone -- it was only ever vetted against verbs KNOWN to take
        # this construction (the closed list below), never against every
        # verb in German. The Case-based check alone already resolves
        # "kochen" correctly (its own accusative object, the fronted
        # relative pronoun "den", is directly Case-tagged and needs no
        # word-order guess), so nothing is lost by keeping the fallback
        # scoped to its original, narrower, already-vetted population.
        has_object = _has_bare_accusative_object(sentence, token.i, clause_start, clause_end)
        if not has_object and verb_lemma in paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT:
            has_object = _immediately_followed_by_object_np(sentence, token.i, clause_end)
            if not has_object:
                # TODO.md 1.1: "sich (Dat) etwas wünschen"'s object is not
                # always a noun phrase -- see
                # ``_followed_by_dass_clause_object``'s own docstring.
                has_object = _followed_by_dass_clause_object(sentence, clause_end)
        verb_case = "Dat" if has_object else "Acc"
    unambiguous = _REFLEXIVE_CASE_BY_FORM.get(lower)
    if unambiguous is not None and unambiguous != verb_case:
        return None
    return verb_case


def _reflexive_governed_by_attributive_phrase(sentence: TaggedSentence, index: int) -> bool:
    """Whether the reflexive-capable pronoun at ``index`` sits INSIDE a
    determiner-headed attributive noun phrase ("dem [mir] oft helfenden
    Trainer") rather than as a bare clause argument of the matrix verb.

    docs/audits/cycle-07-report.md defect 9: "Im Verein helfe ich dem ___
    oft helfenden Trainer beim Aufräumen." wrongly selects "mir" for
    ``verben_reflexiv_dat`` -- "mir" is the dative OBJECT OF THE PARTICIPLE
    "helfenden" ("the trainer who often helps me"), whose own subject is
    "der Trainer" (3rd person), never a reflexive argument of the main
    clause's "ich helfe" at all. The existing subject-agreement check does
    not catch this: "mir" always maps to (1, Sing) regardless of context,
    which happens to equal the main clause's own subject "ich" (also
    1, Sing) purely by coincidence of person, not because "mir" actually
    corefers with it.

    Detected structurally, since this package's own tagger carries no
    dependency parse to ask "what governs this pronoun" directly: a
    determiner-shaped token (``_DETERMINER_TAGS``, or a PDS mistagged
    demonstrative spelled like the definite article -- the exact ambiguity
    ``_preceding_declension_trigger`` already documents and handles for the
    identical model quirk) immediately precedes the pronoun, which is the
    "trapped inside this determiner's own noun phrase" signal, and a later
    token in the SAME clause is an attributive adjective/participle
    (``ADJA``) immediately followed by a nominal head -- the participle
    that is the pronoun's REAL governor. A pronoun that is a genuine clause
    argument is never preceded by a bare determiner this way (it follows
    the finite verb or the subject directly), so this never fires on any
    of the module's own passing reflexive examples."""
    prev = sentence.token_before(index)
    if prev is None:
        return False
    is_determiner_like = prev.tag in _DETERMINER_TAGS or (
        prev.tag == "PDS" and prev.text.lower() in _DEFINITE_ARTICLE_SURFACE_FORMS
    )
    if not is_determiner_like:
        return False
    _, clause_end = _clause_span(sentence, index)
    j = index + 1
    while j < clause_end:
        tok = sentence.tokens[j]
        if tok.pos in ("VERB", "AUX") and tok.morph.get("VerbForm") == "Fin":
            return False
        if tok.tag == "ADJA" and _followed_by_nominal(sentence, tok.i):
            return True
        j += 1
    return False


def _reflexive_selector(fixed_case: str) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.pos != "PRON":
                continue
            lower = token.text.lower()
            if lower not in _REFLEXIVE_CAPABLE_FORMS:
                continue
            if _reflexive_governed_by_attributive_phrase(sentence, token.i):
                continue
            if _governed_by_adposition(sentence, token.i):
                # A genuine reflexive is a bare clause argument of the verb,
                # never the object of a preposition. Confirmed on "Ich lade
                # meine besten Freunde zu mir nach Hause ein.": "zu mir" is
                # an ordinary prepositional phrase ("to my place"), not a
                # reflexive dative object of "einladen" -- it does not
                # corefer with the subject as an ARGUMENT, it is governed by
                # "zu". Without this check, the (unrelated) accusative
                # object "meine besten Freunde" elsewhere in the clause was
                # previously enough to satisfy the old object-presence
                # heuristic and wrongly select "mir" for verben_reflexiv_dat
                # anyway.
                continue
            subject = _finite_verb_person_number(sentence)
            if subject is None:
                continue
            if lower in _REFLEXIVE_PERSON_NUMBER_BY_FORM:
                person, number = _REFLEXIVE_PERSON_NUMBER_BY_FORM[lower]
            else:  # "sich" -- Person is always 3rd; Number is not marked on
                # the form itself, so it is taken from the sentence's own
                # (agreeing) finite verb.
                if subject[0] != "3":
                    continue
                person, number = subject
            if subject != (person, number):
                continue  # a reflexive pronoun always agrees with its subject
            if _reflexive_case(sentence, token) != fixed_case:
                continue
            out.append(
                Candidate(
                    token_index=token.i, kind="reflexive_pronoun", person=person, number=number
                )
            )
        return out

    return select


_RELATIVE_PRONOUN_TAGS: frozenset[str] = frozenset({"PRELS", "PRELAT"})


def _relative_pronoun_selector(fixed_cases: frozenset[str]) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.tag not in _RELATIVE_PRONOUN_TAGS:
                continue
            case = token.morph.get("Case")
            number = token.morph.get("Number")
            if case is None or number is None or case not in fixed_cases:
                continue
            gender = token.morph.get("Gender") or _FACETS_UNK
            out.append(
                Candidate(
                    token_index=token.i,
                    kind="relative_pronoun",
                    cell=(case, gender, number),
                )
            )
        return out

    return select


# ==============================================================================
# Cycle 3: finite verb conjugation -- present tense (regular, vowel-change,
# separable), Präteritum (weak/strong/mixed and sein/haben/modals), and the
# small closed-class helpers shared by the compound-tense/passive/Konjunktiv
# selectors further down.
# ==============================================================================

# Present-tense lemmas that are NOT formed by the regular rule and are not
# already covered by ``is_vokalwechsel_praesens_lemma`` -- verb_praesens_regelm
# must exclude these too (a verb irregular only in the Präteritum, e.g.
# "gehen"/"kommen", is still perfectly regular in the present and stays
# eligible). ``paradigms.IRREGULAR_FINITE_INFLECTED_FORMS`` closes the gap
# docs/audits/cycle-06-modal-leak.md found: excluding the modals' own
# INFINITIVES here is not enough, because a mislemmatised token can carry an
# INFLECTED modal form as its "lemma" instead ("sollten" for "solltet", true
# infinitive "sollen") and evade a membership check keyed on the infinitives
# alone -- confirmed live, "Ihr solltet ... bleiben." was selected as a
# regular verb_praesens_regelm candidate with lemma "sollten" before this.
_IRREGULAR_PRAESENS_LEMMAS: frozenset[str] = (
    frozenset({"sein", "haben", "werden"})
    | paradigms.MODAL_LEMMAS
    | frozenset({"möchten"})
    | paradigms.IRREGULAR_FINITE_INFLECTED_FORMS
)


def _select_verb_praesens_regelm(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "VVFIN":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        if not lemma or lemma in _IRREGULAR_PRAESENS_LEMMAS:
            continue
        if paradigms.is_vokalwechsel_praesens_lemma(lemma):
            # Belongs to verb_praesens_vokalwechsel instead -- includes
            # inseparable-prefixed derivatives like "verlassen" ("du
            # verlässt"), not just the bare base verbs in
            # VOKALWECHSEL_PRAESENS itself. Confirmed on "Um acht Uhr
            # verlasse ich das Haus...": "verlassen" is strong (not a
            # direct table key), so the old bare-membership check let it
            # fall through to this topic by default.
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        if not _lexical_verb_answer_is_plausible(lemma):
            continue
        cue = _citation_cue(lemma, token.text) if _lexical_verb_lemma_trustworthy(lemma) else None
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family="regular_praesens",
                cue=cue,
            )
        )
    return out


def _select_verb_praesens_vokalwechsel(sentence: TaggedSentence) -> list[Candidate]:
    """docs/audits/cycle-06-report.md class D: German's present-tense stem-
    vowel change only ever surfaces in the 2nd/3rd person singular ("du
    isst", "er isst") -- 1st singular and every plural cell are spelled
    exactly like a regular verb ("ich esse", identical to what
    ``verb_praesens_regelm`` would produce), so blanking one of those cells
    exercises nothing this topic is about. Seven of eight items audited
    live were exactly this: a first-person form of a verb that merely
    HAPPENS to belong to the vowel-change table, not a cell that actually
    shows the change.

    The fix is mechanical, not a person/number allowlist: reconstruct both
    the actual vowel-change form and the form a REGULAR verb would take for
    the same ``(lemma, person, number)`` cell (``paradigms.
    vokalwechsel_praesens_form``/``regular_praesens_form``, the same two
    resolvers ``blanker.py`` already trusts for reconstruction) and require
    them to differ. By construction of ``vokalwechsel_praesens_form``
    itself, they differ only in the 2nd/3rd singular cells the table
    actually covers, and are identical everywhere else (1st singular, every
    plural) since that function falls back to the regular resolver there --
    so this single comparison IS the person/number restriction, derived
    from the paradigm data instead of hand-declared, and it also rejects a
    cell either resolver cannot cover at all (``None``) rather than
    guessing."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "VVFIN":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        # A mislemmatised modal-inflected "lemma" (see
        # ``_IRREGULAR_PRAESENS_LEMMAS``'s own comment) is not a
        # ``VOKALWECHSEL_PRAESENS`` table key or a recognised inseparable
        # derivative of one either, so ``is_vokalwechsel_praesens_lemma``
        # already excludes it structurally -- kept here anyway as an
        # explicit, documented guard rather than an implicit side effect of
        # an unrelated table's own closed membership.
        if lemma in paradigms.IRREGULAR_FINITE_INFLECTED_FORMS:
            continue
        if not paradigms.is_vokalwechsel_praesens_lemma(lemma):
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        actual_form = paradigms.vokalwechsel_praesens_form(lemma, person, number)
        regular_form = paradigms.regular_praesens_form(lemma, person, number)
        if actual_form is None or regular_form is None or actual_form == regular_form:
            continue  # this cell does not show the vowel change -- see docstring
        cue = _citation_cue(lemma, token.text) if _lexical_verb_lemma_trustworthy(lemma) else None
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family="vokalwechsel_praesens",
                cue=cue,
            )
        )
    return out


# Tags that end a clause for the purposes of ``_own_clause_particle`` below:
# any OTHER finite verb (a second, coordinated or subordinate clause has its
# own finite verb, and any ``PTKVZ`` beyond it belongs to that clause, not
# the candidate) or a coordinating conjunction (the clause boundary itself,
# reached before that clause's own verb).
_CLAUSE_BOUNDARY_TAGS: frozenset[str] = frozenset({"VVFIN", "VAFIN", "VMFIN", "KON"})


def _own_clause_particle(sentence: TaggedSentence, index: int) -> Token | None:
    """The ``PTKVZ`` token that actually belongs to the finite verb at
    ``index`` -- the first one found scanning forward, PROVIDED no clause
    boundary (another finite verb, or a coordinating conjunction) is crossed
    first. ``sentence_tagger`` excludes spaCy's dependency parser (see its
    own module docstring: neither of Cycle 2's selectors needed it, and it
    is the most expensive pipe to run), so there is no ``token.head``/
    ``token.dep_`` available here to check particle attachment directly;
    this is a structural proxy for it instead, using clause boundaries as
    the substitute signal. Confirmed necessary, not just theoretical: "Um
    zehn Uhr müde ___ ich ins Schlafzimmer und schlafe schnell ein." has a
    ``PTKVZ`` ("ein") that belongs to the SECOND clause's own verb
    ("schlafe"), reached only after crossing "und" -- the old unbounded
    "any PTKVZ later in the sentence" check mis-attributed it to the first
    clause's "gehe" instead."""
    for token in sentence.tokens[index + 1 :]:
        if token.tag == "PTKVZ":
            return token
        if token.tag in _CLAUSE_BOUNDARY_TAGS:
            return None
    return None


def _select_verben_trennbar_praesens(sentence: TaggedSentence) -> list[Candidate]:
    """Present-tense separable verb: the finite stem, with its own prefix
    detached and moved to the end of the clause. Gated on spaCy's own
    ``PTKVZ`` tag (separable verb particle) appearing LATER in the sentence
    than the finite verb -- confirmed empirically to be a reliable signal
    distinct from an ordinary preposition/adverb (``auf``/``ein``/``mit``/
    ``an``/``vor``/``zu`` all tag ``PTKVZ`` specifically when detached from a
    separable verb, never when used as an independent adposition) -- AND
    that the particle is not stranded across a clause boundary into a
    different clause's own verb (see ``_own_clause_particle``)."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "VVFIN":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        particle = _own_clause_particle(sentence, token.i)
        if particle is None:
            continue
        lemma = token.lemma.lower()
        if not lemma:
            continue
        # See ``_IRREGULAR_PRAESENS_LEMMAS``'s own comment: a mislemmatised
        # modal-inflected "lemma" must not be treated as this topic's
        # lexical-verb base either, defence-in-depth alongside the other
        # three lexical-verb selectors even though a modal never carries a
        # genuine separable ``PTKVZ`` particle in the first place.
        if lemma in paradigms.IRREGULAR_FINITE_INFLECTED_FORMS:
            continue
        family = (
            "vokalwechsel_praesens"
            if paradigms.is_vokalwechsel_praesens_lemma(lemma)
            else "regular_praesens"
        )
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        if not _lexical_verb_answer_is_plausible(lemma):
            continue
        # spaCy's own lemma for the finite half of a split separable verb
        # drops the prefix entirely ("steht" -> "stehen", not "aufstehen"),
        # confirmed the same way ``_governing_verb_lemma`` already confirms
        # it for the reflexive router -- reused here rather than re-derived.
        # The prefix is reconstructed from the particle's OWN lemma (its
        # bare text), never trusted unless both halves are individually
        # reliable: the base per ``_lexical_verb_lemma_trustworthy``, and
        # the particle by simply being non-empty (a ``PTKVZ`` token's lemma
        # is its own surface text, nothing to mislemmatise).
        cue = None
        if particle.lemma and _lexical_verb_lemma_trustworthy(lemma):
            full_lemma = particle.lemma.lower() + lemma
            cue = _citation_cue(full_lemma, token.text)
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family=family,
                cue=cue,
            )
        )
    return out


# Confirmed empirically, independent of sentence context (reproduced on the
# bare "Ich schalte den Computer ein."): de_core_news_sm mislemmatises the
# genuine PRESENT-tense 1st-singular "schalte" (of "schalten"/"einschalten")
# to the unrelated, much rarer verb "schalen", AND simultaneously mistags
# its own Tense as Past -- both wrong, and self-consistently so: rebuilding
# a weak Präteritum from lemma "schalen" (stem "schal" + "te") reconstructs
# "schalte" exactly, so the ``blanker.py`` reconstruction-vs-token check
# cannot catch this either, it only re-derives the same wrong answer the
# tagger already committed to.
#
# The remaining six entries were found the same way, by exhaustively tagging
# every sentence in ``sentence_source._MOCK_SENTENCE_POOL`` (the pipeline's
# own deterministic offline corpus -- see docs/audits/cycle-05-report.md's
# lexical-verb cue task) and checking every ``verb_form`` candidate's own
# derived cue by hand, not by guessing where else the bug might strike:
#
# * "frühstücksen"/"rufsen"/"streichsen" ("frühstückst"/"rufst"/"streichst",
#   whose true infinitives are "frühstücken"/"rufen"/"streichen") -- the
#   lemmatiser strips only the final "t" of the 2nd-singular "-st" form and
#   appends "en", instead of removing the whole personal ending, leaving a
#   phantom "s" INSIDE the stem. This is a distinct, non-overlapping failure
#   mode from "schalen"'s (that one drops a stem consonant; this one adds
#   one), so a round-trip is unable to catch it for the identical reason:
#   the resulting stem happens to end in a sibilant, and
#   ``regular_praesens_form``'s own dedicated sibilant rule for 2nd-singular
#   ("reist", not "reisst") absorbs the phantom "s" and reconstructs the
#   real surface form anyway.
# * "antworen" ("antworte", true infinitive "antworten") -- the 1st-singular
#   present ending "-e" on a stem that happens to end in "t" ("antwort" +
#   "e") is spelled identically to a WEAK PRÄTERITUM "-te" ending on a
#   shorter stem, and the lemmatiser un-inflects it as if it were one.
# * "issen"/"vergissen" ("isst"/"vergisst", true infinitives "essen"/
#   "vergessen") -- both wrongly bake the finite form's OWN stem-vowel
#   ("i", from the "e"->"i" ablaut these two share) into the "citation"
#   form, which additionally means ``is_vokalwechsel_praesens_lemma`` never
#   recognises either as the vokalwechsel verb it actually is (a table
#   lookup keyed on the correct lemma "essen"/"vergessen" cannot match a
#   wrong one) -- misrouted to this file's regular-verb topics as well as
#   mislemmatised.
# * "fällen" ("gefällt", true infinitive "gefallen") -- conflated with the
#   unrelated real verb "fällen" ("to fell [a tree]"), losing the "ge-"
#   prefix entirely; the most severe single case found, a different verb
#   altogether, not merely a malformed spelling of the right one.
# * "hattesen" ("hattest", true infinitive "haben") -- built from a
#   Präteritum form (haben's own) that the tagger ALSO mistags
#   ``Tense=Pres``, so it evades ``_IRREGULAR_PRAESENS_LEMMAS``'s "haben"
#   exclusion in ``_select_verb_praesens_regelm`` (keyed on the correct
#   lemma, which this token never carries) as well as being mislemmatised
#   in its own right.
#
# Named for the whole verb-lemma class of defect, not only the Präteritum
# topic that first found the archetype (docs/audits/cycle-05-report.md's
# lexical-verb cue task reuses it below as a cue-reliability guard for every
# lexical-verb topic, not only that one -- the underlying tagger bug is the
# same regardless of which tense selected the token). This list is exactly
# what direct testing against this one closed, deterministic corpus turned
# up, not a claim that these are the only lemmas this model ever
# mislemmatises this way -- a live, LLM-generated corpus is unbounded and a
# future audit may well find more; each addition here is meant to be
# individually confirmed the same way, never guessed at in bulk.
_MISLEMMATIZED_VERB_LEMMAS: frozenset[str] = frozenset(
    {
        "schalen",
        "frühstücksen",
        "rufsen",
        "streichsen",
        "antworen",
        "issen",
        "vergissen",
        "fällen",
        "hattesen",
    }
)

# The closed class of German infinitives spelled WITHOUT the "-en" ending:
# a verb whose stem itself already ends in "-el" or "-er" drops the "e" and
# takes bare "-n" ("lächeln", "wandern", "sammeln", "klingeln"), plus the one
# irregular "tun". Every other genuine German infinitive ends "-en" -- used
# below as a structural shape check, not an exhaustive lemma list, so it
# generalises to any verb of this closed morphological class without
# needing to be named individually.
_BARE_N_INFINITIVE_STEM_SUFFIXES: tuple[str, ...] = ("el", "er")


def _lexical_verb_lemma_trustworthy(lemma: str) -> bool:
    """Whether ``lemma`` -- BEFORE any separable-prefix reconstruction -- is
    reliable enough to show a learner as an infinitive cue for a blanked
    lexical-verb slot (docs/audits/cycle-05-report.md's lexical-verb cue
    task). Same posture as ``_plural_noun_cue``'s own structural checks for
    the equivalent open-class-noun problem, not a round-trip
    reconstruction: the module's own confirmed trap is that rebuilding a
    weak Präteritum from the wrong lemma "schalen" regenerates "schalte"
    exactly, so a round-trip check on the ANSWER alone can never catch a
    mislemmatised lexical verb, only an independent check on the lemma
    itself can.

    1. Genuinely infinitive-SHAPED: ends in "en" (the overwhelming majority
       of German infinitives), or is "tun", or ends bare "-n" on a stem
       that itself ends "-el"/"-er" (see ``_BARE_N_INFINITIVE_STEM_SUFFIXES``
       -- "lächeln", "wandern"). An empty lemma, one left completely
       unreduced by the tagger, or one truncated mid-ending ("kochn" for
       "kochen", "fährstn"/"informierstn" for "fährst"/"informierst" left
       with their own personal ending still attached) fails this and is the
       tagger failing outright, not a citation form to show a learner. This
       check is necessary but NOT sufficient: several of the confirmed-bad
       lemmas below ("frühstücksen", "antworen", ...) are still "en"-shaped
       and pass it, which is exactly why check 2 exists.
    2. Not one of the lemmas confirmed mislemmatised for this exact model
       (above) -- self-consistent tagger bugs a shape check cannot catch
       any other way, per this function's own docstring.
    3. Not itself an INFLECTED form of sein/haben/werden/a modal
       (``paradigms.IRREGULAR_FINITE_INFLECTED_FORMS``) -- docs/audits/
       cycle-06-modal-leak.md: "sollten" (spaCy's own wrong lemma for
       "solltet", the true infinitive is "sollen") passes check 1, it ends in
       "-en" exactly like a genuine infinitive, so shape alone cannot catch
       it. What distinguishes an infinitive from a preterite plural here is
       not spelling, it is membership: no genuine German verb's infinitive is
       spelled "sollten"/"hatten"/"wären" -- those spellings are already,
       fully, claimed by the closed irregular-verb table's own INFLECTED
       cells, so a lexical-verb lemma that lands on one of them is proof the
       tagger mislemmatised an inflected form as if it were a citation form,
       the same class of self-consistent bug check 2 already guards against
       one confirmed lemma at a time, generalised here to the whole closed
       table instead."""
    if not lemma:
        return False
    shaped = (
        lemma.endswith("en")
        or lemma == "tun"
        or (lemma.endswith("n") and lemma[:-1].endswith(_BARE_N_INFINITIVE_STEM_SUFFIXES))
    )
    return (
        shaped
        and lemma not in _MISLEMMATIZED_VERB_LEMMAS
        and lemma not in paradigms.IRREGULAR_FINITE_INFLECTED_FORMS
    )


# docs/audits/cycle-06-report.md task 2: a hallucinated non-word ("treue" for
# "treffe", the confirmed live example) can still TAG as a perfectly formed
# finite verb -- de_core_news_sm gives it ``VVFIN``, ``Person=1|Number=Sing``,
# agreeing with its own subject -- so every STRUCTURAL check in this module
# (and the carrier validator's subject-verb agreement check) passes it. The
# dictionary check ``_cue_is_real_word`` already uses cannot catch this class
# either: "treuen" (spaCy's own lemma for "treue") IS a real dictionary
# entry -- the dative-plural inflection of the adjective "treu" ("den treuen
# Freunden") -- just never as a VERB, and the dictionary has no part-of-speech
# information to rule that reading out.
#
# The lever is the vendored frequency list instead
# (``data/fixtures/corpus/frequency/de_opensubtitles2018_top50k.txt``, a
# ``word count`` list in rank order, already loaded by
# ``src.lexicon.frequency.FrequencyBander``): German's 2nd-person-plural
# present ending ("-t"/"-et") is the ONE present-tense cell that is
# ALWAYS formed by the plain regular rule, for every verb class without
# exception -- strong, weak, mixed, vokalwechsel alike (the stem-vowel
# change docs/audits/cycle-06-report.md class D documents only ever touches
# 2nd/3rd SINGULAR, never any plural cell). ``paradigms.regular_praesens_form``
# happens to construct exactly this cell for the ("3", "Sing") input (the
# same "-t" ending, since German's regular paradigm reuses one ending for
# both), so probing it against the frequency list is a check on a form that
# is genuinely, always attested for a real verb -- "esst"/"geht"/"habt" all
# rank inside the top 50k for their own genuine verbs -- while a hallucinated
# non-verb's regularly-constructed "-t" form is, in ordinary usage, not a
# word at all: "treuen" itself ranks inside the list (as the adjective
# inflection above), but "treut" does not appear anywhere in it.
#
# This is deliberately independent of ``_lexical_verb_lemma_trustworthy``
# above: that function only ever gates the CUE (a wrong cue is shown
# alongside an otherwise-correct answer); this one gates the CANDIDATE
# itself, because here the blanked ANSWER is the thing that is wrong, not
# merely its citation-form cue. Applied unconditionally to every lexical-verb
# selector below, regardless of that function's own verdict.
_FREQUENCY_LIST_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "fixtures"
    / "corpus"
    / "frequency"
    / "de_opensubtitles2018_top50k.txt"
)


@lru_cache(maxsize=1)
def _load_frequency_ranked_words() -> list[str] | None:
    """The vendored frequency list's own words, most-frequent first, loaded
    once per process -- mirrors every other loader in this package's
    fail-safe contract (``_load_cue_dictionary``'s own docstring): never
    raises, ``None`` if the fixture is missing. The single shared source for
    both ``_load_verb_frequency_words`` (task 2: membership, any rank) and
    ``_load_high_frequency_words`` (task 3: membership within a top band),
    so the file is only ever read once regardless of how many checks use it."""
    try:
        return FrequencyBander.load_ranked_words(_FREQUENCY_LIST_PATH)
    except OSError:
        return None


@lru_cache(maxsize=1)
def _load_verb_frequency_words() -> frozenset[str] | None:
    """Every word in the vendored frequency list, degrading
    ``_lexical_verb_answer_is_plausible`` to "always trust the answer"
    (rather than crashing selection) when the fixture is missing."""
    ranked = _load_frequency_ranked_words()
    return None if ranked is None else frozenset(ranked)


# docs/audits/cycle-06-report.md task 3: "Radweg" is correct German, but is
# suppressed as a cue because it is absent from the 37,567-entry dictionary
# and its own compound halves ("Rad", "weg") are both only 3 characters --
# one short of ``compound_split_candidates``'s own 4-character floor
# (``src.lexicon.vocabulary.VocabularyStore``'s deliberate choice, kept here
# too: see ``_cue_is_real_word``'s own docstring for why a blanket 3-
# character floor was tried and reverted, "mussen" -> "mus"+"sen" being the
# confirmed hole it reopened). The fix keeps the 4-character floor as the
# general rule and allows a SHORTER half only when that half is ALSO a
# high-frequency word: "rad" (rank ~3,800 of 50,000) and "weg" (rank ~130)
# both clear a top-5,000 band comfortably; "mus" (rank ~38,400) and "sen"
# (rank ~29,100) do not come close, so the same split that used to rescue
# "mussen" stays rejected under this rule -- see
# ``tests/test_blanking_selectors.py`` for the pinned regression proving
# "mussen"/"einpacksen"/"Plastiktüt" are all still rejected.
#
# 5,000 is a round, defensible cut -- comfortably inside "everyday core
# vocabulary" territory (``FrequencyBander.DEFAULT_B2_BAND_SIZE`` bands the
# NEXT 6,000 newly-surfaced words beyond A1/A2/B1 for comparison) and, on the
# two confirmed cases above, wide enough to clear "rad"/"weg" by roughly an
# order of magnitude while nowhere near reaching "mus"/"sen". Not tuned
# narrower to fit one word: the module docstring's own standard is to check
# a reusable FACT (frequency band membership), not hand-fit a single lemma.
_HIGH_FREQUENCY_BAND_SIZE = 5000


@lru_cache(maxsize=1)
def _load_high_frequency_words() -> frozenset[str] | None:
    """The top ``_HIGH_FREQUENCY_BAND_SIZE`` words of the vendored frequency
    list, degrading ``_cue_is_real_word``'s short-compound-half rescue to
    "never rescue" (not "always trust") when the fixture is missing -- the
    opposite fail-safe direction from ``_load_verb_frequency_words`` above,
    deliberately: this list only ever WIDENS what a cue check already
    accepts (module docstring: an absent cue degrades to no-cue, never to a
    rejected item), so a missing fixture here must fall back to the
    STRICTER pre-existing behaviour, not a permissive one."""
    ranked = _load_frequency_ranked_words()
    return None if ranked is None else frozenset(ranked[:_HIGH_FREQUENCY_BAND_SIZE])


def _lexical_verb_answer_is_plausible(lemma: str) -> bool:
    """Whether ``lemma`` -- the tagger's own citation form for a blanked
    lexical-verb ANSWER, before separable-prefix reconstruction -- clears the
    frequency-plausibility check this function's own module comment
    describes. ``True`` (never rejects) when the frequency list failed to
    load, or when ``lemma`` is not infinitive-shaped enough for
    ``paradigms.regular_praesens_form`` to construct a probe from at all
    (``_lexical_verb_lemma_trustworthy``'s own check 1 already covers this
    shape more thoroughly; this function does not duplicate it, it only
    treats "cannot even construct a probe" as unverifiable rather than as a
    rejection, matching the module's own "reject rather than guess" posture
    applied to the ABSENCE of a verdict, not to a verdict itself)."""
    frequency = _load_verb_frequency_words()
    if frequency is None:
        return True
    probe = paradigms.regular_praesens_form(lemma, "3", "Sing")
    if probe is None:
        return True
    return normalise(probe) in frequency


def _select_praeteritum_vollverben(sentence: TaggedSentence) -> list[Candidate]:
    """Simple past of a full lexical verb -- weak (by rule), strong or mixed
    (from the closed tables in ``paradigms.py``). Excludes sein/haben/modals
    (``praeteritum_sein_haben_modal``'s own scope), ``werden`` (the
    passive/Futur auxiliary topics' scope), the confirmed-mislemmatised
    lemmas above, and every INFLECTED form of any of those (see
    ``_IRREGULAR_PRAESENS_LEMMAS``'s own comment on the same gap) -- a modal
    lemma's own infinitive alone is not enough here either, since a
    mislemmatised token can carry an inflected modal form as its "lemma"
    instead."""
    excluded = (
        frozenset({"sein", "haben", "werden"})
        | paradigms.MODAL_LEMMAS
        | {"möchten"}
        | _MISLEMMATIZED_VERB_LEMMAS
        | paradigms.IRREGULAR_FINITE_INFLECTED_FORMS
    )
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "VVFIN":
            continue
        if token.morph.get("Tense") != "Past" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        if not lemma or lemma in excluded:
            continue
        if lemma in paradigms.STRONG_VERBS:
            family = "strong_praeteritum"
        elif lemma in paradigms.MIXED_VERBS:
            family = "mixed_praeteritum"
        else:
            # Unlike the two closed-table branches above (hand-verified real
            # verbs, never tagger-derived), a "regular_praeteritum" lemma is
            # exactly the OPEN-class, tagger-derived case
            # ``_lexical_verb_answer_is_plausible`` exists for -- see that
            # function's own module comment.
            if not _lexical_verb_answer_is_plausible(lemma):
                continue
            family = "regular_praeteritum"
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        cue = _citation_cue(lemma, token.text) if _lexical_verb_lemma_trustworthy(lemma) else None
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family=family,
                cue=cue,
            )
        )
    return out


def _governing_determiner_number(sentence: TaggedSentence, index: int) -> str | None:
    """The ``Number`` of the determiner governing the noun at ``index``,
    walking back over any attributive adjectives first, or ``None`` if no
    determiner immediately governs it (or that determiner's own ``Number``
    could not be resolved).

    A determiner tagged ``Number=Sing`` can never govern a genuinely plural
    noun -- German determiner/noun number agreement is categorical, and the
    ein-word paradigm does not even have a Nominative/Accusative plural row
    at all (see ``paradigms.py``'s module docstring) -- so this is a hard
    grammatical fact, not a guess, and it is checked on the DETERMINER, not
    the noun itself: confirmed empirically that ``de_core_news_sm``
    mistags the noun's own ``Number`` outright in context (in "... trinke
    ich meistens eine Tasse Kaffee ...", "Tasse" -- unambiguously singular,
    governed by "eine" -- is itself tagged ``Number=Plur``, wrongly, while
    "eine" right next to it is correctly tagged ``Number=Sing``), so the
    noun's own morphology cannot be trusted as the sole signal here."""
    j = index - 1
    while j >= 0 and sentence.tokens[j].tag == "ADJA":
        j -= 1
    if j < 0:
        return None
    prev = sentence.tokens[j]
    if prev.tag not in _DETERMINER_TAGS:
        return None
    return prev.morph.get("Number")


# ------------------------------------------------------------------------
# Cue generation (docs/audits/cycle-04-report.md recommendation 5, and the
# same report's headline finding): a bracketed CITATION-FORM cue -- never a
# grammar term (rule 2 in CLAUDE.md is unchanged, this names a lexeme, not a
# category), never the answer itself, always the constituent the gap tests.
# See ``Candidate.cue``'s own docstring for the contract; this section is
# only the functions that compute one.
# ------------------------------------------------------------------------

# docs/audits/cycle-06-report.md class A: a cue reaching the learner as
# nonsense ("mussen", "einpacksen", "Plastiktüt") is the worst defect class
# in the audit, because unlike a wrong ANSWER (caught by paradigm
# reconstruction before it can ship) a wrong CUE is never cross-checked
# against anything and goes straight to the learner. Previous cycles caught
# individual instances by adding the exact bad lemma to a hand-maintained
# exclusion list (``_MISLEMMATIZED_VERB_LEMMAS``,
# ``_MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS``); that does not converge, since
# every new sentence can produce a new lemmatiser artefact. This is the
# general replacement: a cue must resolve to a real German word, checked
# against the same 37,567-entry vendored dictionary
# ``carrier_validation.py`` already uses for its own, unrelated lexical-
# reality check (``data/fixtures/corpus/frequency/de_dictionary_filter.txt``,
# CC0). The hand-maintained lists stay in place as a second line of defence
# (a real dictionary word that is nonetheless the WRONG word -- "fällen" for
# "gefallen" is the confirmed example -- is a defect this dictionary check
# structurally cannot see, since the check only asks "is this a word", never
# "is this the right one"), not superseded by it.
_DICTIONARY_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "fixtures"
    / "corpus"
    / "frequency"
    / "de_dictionary_filter.txt"
)


@lru_cache(maxsize=1)
def _load_cue_dictionary() -> frozenset[str] | None:
    """The vendored real-word list, loaded once per process and normalised
    through ``normalise`` on load (``FrequencyBander.load_dictionary_filter``
    already does this). Mirrors every other loader in this package's own
    fail-safe contract (``sentence_tagger._load_model``,
    ``carrier_validation._load_dictionary``): never raises, ``None`` if the
    fixture is missing, which degrades ``_cue_is_real_word`` to "always
    trust the cue" rather than crashing cue generation over a missing file.

    **Important caveat, confirmed empirically (not merely documented) by
    ``carrier_validation.py``'s own module docstring section 7**: the file
    was itself written through ``normalise``, which maps ``ß`` to ``ss`` and
    casefolds -- so it contains zero ``ß`` characters and no uppercase
    letters at all. A lookup against it must normalise its own query key the
    same way (``_cue_is_real_word`` below does), never compare the raw cue
    text directly, or every ``ß``-spelled or capitalised entry would
    (wrongly) look absent."""
    try:
        return FrequencyBander.load_dictionary_filter(_DICTIONARY_PATH)
    except OSError:
        return None


def _cue_is_real_word(cue: str) -> bool:
    """Whether ``cue`` -- a bracketed citation-form cue about to be shown to
    a learner -- resolves to a real German word.

    A direct dictionary hit is not the only avenue: a legitimate compound
    ("Familienfoto") may not be a direct entry in a 37,567-word general list
    even though it is perfectly good German, so a two-part compound split
    (``lemmatizer.compound_split_candidates``, both halves required to
    resolve) is tried as a fallback before rejecting -- the same technique
    ``src.lexicon.vocabulary.VocabularyStore._compound_level`` already uses
    for the identical "is this real, even though it's a compound" question,
    reused rather than re-invented.

    **Absent-from-the-list is treated as "reject the cue", not "reject the
    item"** (see ``_citation_cue``, the one caller): a cue that is genuinely
    correct German but happens to fall outside a 37,567-entry list (real,
    but rarer than the list's own cutoff, or a compound this function's
    bounded two-way split still cannot resolve) is not proof the item is
    bad, only that this one check cannot vouch for the cue. Killing the
    whole item on an absent-word false negative would be the module's own
    "reject rather than guess" posture turned the wrong direction -- the
    guess being rejected here is the CUE's reliability, not the sentence's
    correctness, so the safe degrade is the cue-less outcome every other
    reliability check in this module already falls back to, not a skip.

    **Measured on the offline mock pool (242 sentences, ``blank_sentences``,
    both with and without this check), not assumed:**

    * Total item count is identical either way (380). Every ``verb_form``/
      ``plural_noun``/modal ``irregular_aux`` candidate this check ever
      suppresses a cue for already fails ``uniqueness.check_uniqueness`` on
      that same missing cue regardless of WHY it is missing (that gate's
      own policy table requires a cue for exactly those three open-class
      kinds), and the other cue-bearing kinds (``degree``, the two
      participle ``adjective`` kinds) belong to topics whose
      ``eligible_types`` is ``cloze_cued``-only -- so on the topic set that
      exists today, a suppressed cue and an outright-rejected candidate
      always converge to the same "no item" outcome by construction, not by
      coincidence of this one pool. The choice still matters for a future
      topic that accepts ``cloze_free`` for one of these kinds, or a future
      uniqueness policy change -- suppressing preserves that item instead of
      silently discarding a possibly-fine sentence, which rejecting would
      not.
    * Concretely two ``nomen_plural`` items change (the pool's own topic cap
      backfills two others, which is why the total above does not move):
      "Großelter" (spaCy's own singular-strip lemma for "Großeltern") is
      correctly caught -- it is not a real German word, "Großelternteil" is
      the actual singular. "Radweg" (bike path, a common, entirely correct
      word) used to be a genuine OVER-reject: it is not a direct entry in
      the 37,567-word list, and its own compound halves ("Rad", "weg") are
      both only 3 characters, one short of ``compound_split_candidates``'s
      own 4-character floor, so the compound fallback could not rescue it
      either. Lowering that floor to 3 across the board was tried and
      reverted: it resolves "Radweg" but ALSO resolves "mussen" (the
      confirmed-bad modal lemma this check exists to catch) via the
      spurious split "mus"+"sen" -- both entries the dictionary happens to
      contain for unrelated reasons -- which reopens exactly the defect
      class this task closes.

      docs/audits/cycle-06-report.md task 3's fix, now below: keep the
      4-character floor as the GENERAL rule (unchanged default on
      ``compound_split_candidates``, and still the only rule applied to any
      part 4 characters or longer), but allow a SHORTER half specifically
      when that half is ALSO a member of ``_load_high_frequency_words``
      (the top ``_HIGH_FREQUENCY_BAND_SIZE`` words of the same vendored
      frequency list task 2 uses) -- "rad"/"weg" both clear that band
      comfortably (ranks ~3,800/~130 of 50,000), "mus"/"sen" do not (ranks
      ~38,400/~29,100), so "Radweg" now resolves while "mussen" stays
      exactly as rejected as before. See
      ``tests/test_blanking_selectors.py`` for the pinned regression proving
      "mussen"/"einpacksen"/"Plastiktüt" are all still rejected under this
      rule. The 4-character floor itself is
      ``src.lexicon.vocabulary.VocabularyStore``'s own existing, deliberate
      choice for the identical false-positive reason, so this function
      still keeps it as the default rather than lowering it outright.

    Degrades to ``True`` (never rejects) when the dictionary itself failed
    to load."""
    dictionary = _load_cue_dictionary()
    if dictionary is None:
        return True
    key = normalise(cue)
    if key in dictionary:
        return True
    for head, tail in compound_split_candidates(cue):
        if head in dictionary and tail in dictionary:
            return True
    high_frequency = _load_high_frequency_words()
    if high_frequency is not None:
        for head, tail in compound_split_candidates(cue, min_part_len=3):
            if head not in dictionary or tail not in dictionary:
                continue
            short_parts = [part for part in (head, tail) if len(part) < _MIN_COMPOUND_PART_LEN]
            if not short_parts:
                continue  # already covered by the standard-floor loop above
            if all(part in high_frequency for part in short_parts):
                return True
    return False


def _cue_case_matched_to_answer(cue: str, surface: str) -> str:
    """Capitalise (or lower-case) ``cue``'s first letter to match ``surface``
    -- the answer the cue is standing in for -- 's own first-letter case.

    docs/audits/cycle-08-report.md's capitalisation trap: a learner who
    types exactly what the bracketed cue shows must never be marked wrong
    for a case mismatch the cue itself introduced. Two distinct sources
    confirmed live, both closed by the same one-line rule:

    * The formal ``Sie``-family possessive ("Ihr", "Ihre", "Ihrer", ...) is
      always capitalised, but ``paradigms.family_forms`` stores every
      ein-word paradigm in lowercase (``match_ein_word`` lower-cases the
      surface before matching a stem) -- so the derived citation form for
      an answer like "Ihrer" came back "ihre", a plain lowercase mismatch
      against a formal-register answer.
    * A gap at the very start of a sentence capitalises its answer for a
      reason that has nothing to do with the word itself (German
      sentence-initial capitalisation, not the word's own citation form) --
      "Alte" as the answer to a sentence-initial "___ (alt) Batterien ..."
      cues "alt", lowercase, while the only acceptable typed answer starts
      uppercase.

    Mirrors ``blanker._match_case`` exactly (that function does the same
    thing for a reconstructed distractor form against the token it
    replaces), duplicated rather than imported: ``blanker.py`` already
    imports from this module, so importing back would be circular, and the
    rule itself is one line, not worth restructuring the module boundary
    for."""
    if not cue:
        return cue
    if surface[:1].isupper():
        return cue[:1].upper() + cue[1:]
    return cue[:1].lower() + cue[1:]


def _citation_cue(lemma: str, surface: str) -> str | None:
    """A cue string from ``lemma``, unless it is letter-for-letter identical
    to ``surface`` (case-insensitively) -- "the cue must never equal the
    answer" applies just as much to a lemma that happens to coincide with
    the very form being blanked as it does to a lemma that is simply wrong.
    Two concrete cases this guards, both real rather than hypothetical: a
    modal's 1st/3rd-plural present tense is spelled identically to its own
    infinitive ("wir/sie müssen" == "müssen"), and a handful of German
    nouns have a plural spelled identically to their own singular ("der
    Lehrer" / "die Lehrer"). No cue is the safe outcome for either -- the
    uniqueness gate simply keeps treating that one cell as it did before
    this cue mechanism existed.

    docs/audits/cycle-06-report.md class A: also the one choke point every
    cue this module ever produces passes through (every ``cue=`` in this
    file is either a literal call to this function or delegates to one that
    is), so the dictionary reality check (``_cue_is_real_word``) belongs
    here, once, rather than repeated at each of the call sites -- and so
    does the capitalisation-matching fix above (docs/audits/
    cycle-08-report.md): every cue this module ever hands to a learner is
    matched to ``surface``'s own case here, once, rather than at each call
    site."""
    if lemma.lower() == surface.lower():
        return None
    if not _cue_is_real_word(lemma):
        return None
    return _cue_case_matched_to_answer(lemma, surface)


def _plural_noun_cue(token: Token) -> str | None:
    """The citation form (nominative singular) for a blanked plural noun,
    taken from the tagger's own lemma -- the only source this cycle has for
    it, since German plural formation has no single computable rule to
    reconstruct a singular from (``_select_nomen_plural``'s own docstring).
    Trusted only when it clears three structural reliability checks, each
    confirmed necessary by direct testing against ``de_core_news_sm``
    (never assumed from the "schalte" -> "schalen" precedent alone, though
    that is the same CLASS of failure -- a lemma the tagger is simply
    wrong about, self-consistently so, with nothing else in this cycle's
    data to cross-check it against):

    1. Non-empty and capitalised -- every genuine German noun citation form
       is; an empty or lower-cased "lemma" is the tagger failing outright,
       not a real singular.
    2. Differs from the plural surface form itself (delegated to
       ``_citation_cue``) -- catches BOTH a genuinely invariant plural
       ("Lehrer"/"Fenster", where cueing would leak the answer) and a
       confirmed lemmatiser failure to reduce at all ("Hunde" -> "Hunde",
       "Äpfel" -> "Äpfel", "Gläser" -> "Gläser", all reproduced directly
       against this model) -- both must be skipped for the same reason
       (rule 2 aside, an unreduced lemma is simply not a citation form),
       so one check does for both without needing to tell them apart.
    3. The token's own ``Case`` is not Dative -- confirmed empirically that
       ``de_core_news_sm``'s lemmatiser is measurably less reliable on a
       DATIVE plural specifically (the same noun lemmatises correctly in
       Nominative/Accusative/Genitive context): "Vätern" -> "Väter" (still
       plural, not "Vater"), "Brüdern" -> "Brüdern" and "Müttern" ->
       "Müttern" (entirely unreduced) all reproduced directly, while the
       identical lemmas resolve correctly as "Väter" -> "Vater" in a
       Genitive sentence. This is a distinct failure class from check 2 (the
       lemma differs from the plural surface, so check 2 alone would wrongly
       accept it) and is deliberately narrower than excluding Dative
       candidates outright -- ``_select_nomen_plural`` itself still selects
       and blanks a Dative plural noun exactly as before, this only
       withholds the CUE for that one case, matching the module's own
       "no cue is the safe outcome" posture.

    4. **Round-trips against the observed plural**
       (``paradigms.plausible_noun_plurals``) -- docs/audits/
       cycle-07-report.md defect 1: "Tablett" (tray, a real word) is not
       proof the cue is right, because "Tabletten" is the plural of the
       unrelated "Tablette" (pill), not of "Tablett" (which pluralises to
       "Tabletts"). The dictionary check (``_citation_cue``'s own
       ``_cue_is_real_word`` call) cannot see this -- "Tablett" IS a real
       word -- so nothing upstream of this catches it. Withheld whenever the
       observed plural is not itself one of the lemma's own plausible
       plural forms; see that function's own docstring for what "plausible"
       deliberately does and does not cover."""
    lemma = token.lemma
    if not lemma or not lemma[:1].isupper():
        return None
    if token.morph.get("Case") == "Dat":
        return None
    cue = _citation_cue(lemma, token.text)
    if cue is None:
        return None
    if token.text not in paradigms.plausible_noun_plurals(cue):
        return None
    return cue


def _select_nomen_plural(sentence: TaggedSentence) -> list[Candidate]:
    """A plural common noun, trusted as its own correct answer exactly like
    ``adjektiv_komparativ_superlativ`` trusts a comparative form -- German
    plural formation has no single rule to reconstruct or cross-check
    against (the module docstring's "do not invent paradigm data" standard),
    so distractors are left empty rather than guessed (see
    ``blanker.py``). The noun's own ``Number=Plur`` tag is additionally
    cross-checked against any governing determiner's ``Number`` -- see
    ``_governing_determiner_number`` for the confirmed tagger failure this
    guards against. ``cue`` is the derived singular citation form (see
    ``_plural_noun_cue``), ``None`` wherever that cannot be derived
    reliably -- the candidate is still returned either way, exactly as
    before this cycle's cue mechanism existed."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "NN":
            continue
        if token.morph.get("Number") != "Plur":
            continue
        if _governing_determiner_number(sentence, token.i) == "Sing":
            continue
        out.append(Candidate(token_index=token.i, kind="plural_noun", cue=_plural_noun_cue(token)))
    return out


# ==============================================================================
# Cycle 3: irregular finite forms shared across many topics -- sein/haben/
# werden/the modals, in whichever (Tense, Mood) combination a given topic
# needs (Präsens indicative, Präteritum indicative, Konjunktiv II).
# ==============================================================================


def _irregular_finite_selector(
    lemmas: frozenset[str], tense_mood: str, morph_tense: str, morph_mood: str
) -> Selector:
    """``lemmas`` is this selector's own closed target set (sein/haben for
    ``verb_sein_haben``, the modals for ``modalverben_praesens``, their union
    for ``praeteritum_sein_haben_modal``, ...): every candidate this closure
    ever emits has already matched ``lemma in lemmas`` above, so the lemma is
    by construction one of a small, known-closed set of infinitives -- the
    same "selection into a closed set already means the citation form is
    trustworthy" argument ``_select_passiv_modalverben`` makes for its own
    unconditional cue. Cueing every candidate this selector produces, not
    only the ones whose lemma happens to be a modal, is docs/audits/
    cycle-06-modal-leak.md's extension: ``verb_sein_haben`` and the sein/haben
    half of ``praeteritum_sein_haben_modal`` used to go without a cue purely
    because ``_MODAL_LEMMAS_FOR_CUE`` (now removed) only ever named the
    modals, not because sein/haben's own citation form is any less reliable
    than a modal's -- it is exactly as reliable, for exactly the same
    closed-membership reason.
    """

    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.pos not in ("VERB", "AUX"):
                continue
            if token.morph.get("VerbForm") != "Fin":
                continue
            if token.morph.get("Tense") != morph_tense or token.morph.get("Mood") != morph_mood:
                continue
            lemma = token.lemma.lower()
            if lemma not in lemmas:
                continue
            person, number = token.morph.get("Person"), token.morph.get("Number")
            if not person or not number:
                continue
            cue = _citation_cue(lemma, token.text)
            out.append(
                Candidate(
                    token_index=token.i,
                    kind="irregular_aux",
                    lemma=lemma,
                    person=person,
                    number=number,
                    tense_mood=tense_mood,
                    cue=cue,
                )
            )
        return out

    return select


def _participle_after(sentence: TaggedSentence, index: int) -> Token | None:
    for token in sentence.tokens[index + 1 :]:
        if token.tag == "VVPP":
            return token
    return None


def _participle_after_in_clause(sentence: TaggedSentence, index: int) -> Token | None:
    """The same search as ``_participle_after``, bounded to ``index``'s own
    clause (``_clause_span``) rather than the rest of the sentence.

    docs/audits/cycle-07-report.md defects 5 and 7: ``_select_zustandspassiv``
    used the unbounded ``_participle_after`` to decide whether a bare "ist"/
    "war" is followed by a transitive participle -- which means it happily
    reaches into a LATER, unrelated clause for one. "Es ist sehr wichtig,
    dass die Suppe langsam gekocht wird." has no participle anywhere near
    "ist" (a predicative adjective, "wichtig", follows it directly), but the
    unbounded search walked straight past the clause boundary and found
    "gekocht" in the "dass" clause's own (unrelated) Vorgangspassiv --
    "kochen" is transitive, so the candidate was wrongly built. Bounding the
    search to "ist"'s own clause closes this specific shape; see the module
    docstring's ``_clause_span`` precedent for why a comma is the available
    proxy here, not a real parse."""
    _, end = _clause_span(sentence, index)
    for token in sentence.tokens[index + 1 : end]:
        if token.tag == "VVPP":
            return token
    return None


def _clause_contains_worden(sentence: TaggedSentence, index: int) -> bool:
    """Whether ``worden`` (the Vorgangspassiv Perfekt/Plusquamperfekt
    marker -- "ist/war ... PARTIZIP worden") appears anywhere in ``index``'s
    own clause.

    docs/audits/cycle-07-report.md defect 7: clause-scoping alone
    (``_participle_after_in_clause``) does not close every case -- a MAIN
    clause with ordinary Vorgangspassiv-Perfekt word order ("Das Auto ist
    gestern gekauft worden.") has its own participle immediately AFTER
    "ist", in the SAME clause, with no comma anywhere to bound the search
    at all. "worden" is the unambiguous marker that this is Vorgangspassiv
    (a dynamic passive event), never Zustandspassiv (a state) -- the two
    can never co-occur -- so its presence anywhere in the clause rules out
    Zustandspassiv regardless of where the participle itself sits relative
    to the blanked "ist"/"war"."""
    start, end = _clause_span(sentence, index)
    return any(t.text.lower() == "worden" for t in sentence.tokens[start:end])


def _participle_before(sentence: TaggedSentence, index: int) -> Token | None:
    for token in reversed(sentence.tokens[:index]):
        if token.tag == "VVPP":
            return token
    return None


def _participle_before_in_clause(sentence: TaggedSentence, index: int) -> Token | None:
    """The same search as ``_participle_before``, bounded to ``index``'s own
    clause -- the mirror of ``_participle_after_in_clause``'s own reasoning,
    needed for the identical reason: an unbounded backward search reaches
    into an EARLIER, unrelated clause's own participle. docs/audits/
    cycle-09-report.md 1.7's own regression while fixing this: "Wenn ich
    ... geachtet hätte, wäre ich jetzt bestimmt fitter." has a real
    participle ("geachtet") in the FIRST clause -- an unbounded
    ``_participle_before`` reached across the comma and wrongly excluded
    "wäre" (the second clause's own bare Konjunktiv II Gegenwart, which has
    no participle of its own at all) from ``konjunktiv_ii_irreal_gegenwart``
    entirely."""
    start, _ = _clause_span(sentence, index)
    for token in reversed(sentence.tokens[start:index]):
        if token.tag == "VVPP":
            return token
    return None


def _token_after(
    sentence: TaggedSentence, index: int, *, lemma: str, tags: frozenset[str]
) -> Token | None:
    for token in sentence.tokens[index + 1 :]:
        if token.lemma.lower() == lemma and token.tag in tags:
            return token
    return None


def _contains_text(sentence: TaggedSentence, text: str) -> bool:
    return any(t.text.lower() == text for t in sentence.tokens)


_MODAL_INFINITIVE_TAGS: frozenset[str] = frozenset({"VVINF", "VAINF"})


# ------------------------------------------------------------------------
# Perfekt / Plusquamperfekt.
#
# Both share the exact same surface shape (aux + Partizip II at clause
# end) and differ ONLY in the aux's own Tense -- which, per the top-level
# report, is not by itself proof the alternate tense would be equally
# implausible in the same blanked sentence ("Gestern ___ wir gefahren"
# accepts "sind" and "waren" alike with nothing else to anchor it). The
# mitigation adopted here is structural, not a guess: Perfekt is only
# selected in the ABSENCE of an anteriority marker ("nachdem"/"bevor") that
# would make Plusquamperfekt the natural reading instead, and
# Plusquamperfekt is only selected in that marker's PRESENCE. Neither guard
# is a proof of non-ambiguity in the general case (a residual risk honestly
# reported at the top level), but it directly targets the one failure
# pattern the audit actually found.
# ------------------------------------------------------------------------

_ANTERIORITY_MARKERS: tuple[str, ...] = ("nachdem", "bevor")


def _has_anteriority_marker(sentence: TaggedSentence) -> bool:
    return any(_contains_text(sentence, marker) for marker in _ANTERIORITY_MARKERS)


def _select_perfekt(sentence: TaggedSentence, *, aux_lemma: str) -> list[Candidate]:
    if _has_anteriority_marker(sentence):
        return []
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != aux_lemma:
            continue
        participle = _participle_after(sentence, token.i)
        if participle is None:
            continue
        part_lemma = participle.lemma.lower()
        if not part_lemma:
            continue
        takes_sein = part_lemma in paradigms.AUX_SEIN_LEMMAS
        if aux_lemma == "sein" and not takes_sein:
            continue
        if aux_lemma == "haben" and takes_sein:
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma=aux_lemma,
                person=person,
                number=number,
                tense_mood="Pres",
            )
        )
    return out


def _select_perfekt_haben(sentence: TaggedSentence) -> list[Candidate]:
    return _select_perfekt(sentence, aux_lemma="haben")


def _select_perfekt_sein(sentence: TaggedSentence) -> list[Candidate]:
    return _select_perfekt(sentence, aux_lemma="sein")


def _select_plusquamperfekt(sentence: TaggedSentence) -> list[Candidate]:
    if not _has_anteriority_marker(sentence):
        return []
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Past" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        if lemma not in ("haben", "sein"):
            continue
        # Plusquamperfekt's own requirement (an anteriority marker) means
        # this is very often a subordinate ("nachdem"/"bevor") clause, which
        # is verb-final in German -- the Partizip II precedes the aux there
        # ("nachdem er gegessen HATTE"), the reverse of a main clause's
        # order. Both directions are checked; whichever is found wins.
        participle = _participle_after(sentence, token.i) or _participle_before(sentence, token.i)
        if participle is None:
            continue
        part_lemma = participle.lemma.lower()
        if not part_lemma:
            continue
        takes_sein = part_lemma in paradigms.AUX_SEIN_LEMMAS
        if (lemma == "sein") != takes_sein:
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma=lemma,
                person=person,
                number=number,
                tense_mood="Past",
            )
        )
    return out


# ------------------------------------------------------------------------
# Konjunktiv II: würde/könnte/hätte/wäre, the four lemmas named in
# konjunktiv_ii_hoeflichkeit's own rule_hint in data/taxonomy.yaml.
# spaCy reports ``Tense: Past`` for all three Konjunktiv II topics
# (morphologically these forms derive from the Präteritum stem regardless
# of the semantic time they express), so ``Mood: Sub`` is the gate common
# to all three; a following Partizip II is what distinguishes vergangenheit
# from the other two, and sentence shape (question vs "wenn"-clause) is
# what distinguishes hoeflichkeit from irreal_gegenwart.
# ------------------------------------------------------------------------

_KONJUNKTIV_II_LEMMAS: frozenset[str] = frozenset({"werden", "können", "haben", "sein"})


def _select_konjunktiv_ii_base(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Past" or token.morph.get("Mood") != "Sub":
            continue
        lemma = token.lemma.lower()
        if lemma not in _KONJUNKTIV_II_LEMMAS:
            continue
        # vergangenheit's own shape, not this one -- checked in BOTH
        # directions, not only after, the same fix
        # ``_select_plusquamperfekt`` already needed for the identical
        # word-order fact: a verb-final "wenn"-clause puts the participle
        # BEFORE the aux ("wenn ich ... geachtet HÄTTE"), the reverse of a
        # main clause's own order. docs/audits/cycle-09-report.md 1.7:
        # "Wenn ich nur etwas früher auf meine Ernährung geachtet ___, wäre
        # ich jetzt bestimmt fitter." was filed under
        # ``konjunktiv_ii_irreal_gegenwart`` because the unidirectional
        # (after-only) check never saw "geachtet" sitting BEFORE "hätte"
        # and let it through as if it were a bare, participle-less
        # Konjunktiv II -- its condition is in the past, so it belongs to
        # ``konjunktiv_ii_vergangenheit`` instead.
        if (
            _participle_after_in_clause(sentence, token.i) is not None
            or _participle_before_in_clause(sentence, token.i) is not None
        ):
            continue
        if lemma == "werden":
            # "würde" only counts here with an Infinitiv later -- a bare
            # "würde" with nothing after it is not a complete construction
            # this cycle can verify.
            has_infinitive = any(
                t.tag in _MODAL_INFINITIVE_TAGS and t.i > token.i for t in sentence.tokens
            )
            if not has_infinitive:
                continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma=lemma,
                person=person,
                number=number,
                tense_mood="SubjII",
            )
        )
    return out


def _select_konjunktiv_ii_hoeflichkeit(sentence: TaggedSentence) -> list[Candidate]:
    if not sentence.text.rstrip().endswith("?"):
        return []
    if _contains_text(sentence, "wenn"):
        return []
    return _select_konjunktiv_ii_base(sentence)


# docs/audits/cycle-06-report.md class F: a "wenn" clause is not, by
# itself, proof of a genuine hypothetical CONDITION -- "wenn Sie unsere
# Fragen beantworten" is a plain present-indicative clause used inside a
# "wir würden uns freuen, wenn ..." POLITE-REQUEST formula, not a
# counterfactual. Mood alone is not quite enough to tell the two apart
# either, confirmed against the audit's own second example: "wenn Sie mir
# dabei helfen könnten" IS grammatically Konjunktiv II throughout (spaCy
# tags "könnten" ``Mood=Sub`` exactly like a genuine "wenn ich Zeit hätte"
# would), yet it is the same politeness formula as the first example, not
# a hypothetical condition either -- so "the subordinate clause verb is
# Konjunktiv II" has to mean something narrower than "some Konjunktiv II
# verb sits somewhere in the wenn-clause", or it would not close this
# second, reported item at all.
#
# The narrower, still mechanical fact used here: "können" and "werden" are
# exactly the two lemmas ``konjunktiv_ii_hoeflichkeit``'s own rule_hint
# names as ITS politeness markers ("könnten Sie", "würden Sie"). A
# Konjunktiv II verb in the wenn-clause built from one of those two lemmas
# is indistinguishable, by this module's own closed-class evidence, from
# the identical politeness construction one clause up -- it does not
# license the irrealis topic. A genuine hypothetical-state verb in the
# wenn-clause ("hätte", "wäre", or any other lexical verb's own Konjunktiv
# II) still does, unchanged.
_HOEFLICHKEIT_ONLY_KONJUNKTIV_LEMMAS: frozenset[str] = frozenset({"können", "werden"})


def _wenn_clause_has_genuine_konjunktiv(sentence: TaggedSentence) -> bool:
    """Whether the sentence's own "wenn" clause -- bounded the same way
    ``_clause_span`` bounds any other clause, by the nearest comma on
    either side or a sentence edge -- has a finite verb that is Konjunktiv
    II (``Mood=Sub``) AND is not one of the two politeness-only lemmas
    above (see this function's own preceding module-level comment for
    why the second condition is needed, not only the first). ``False``
    both when the wenn-clause's own verb is plainly indicative (class F's
    first reported item) and when its only Konjunktiv II verb is
    "könnte(n)"/"würde(n)" (its second) -- ``False`` is also the answer
    when no "wenn" is present at all, which the caller has already
    checked, so this never needs to guess at a clause that is not there."""
    for token in sentence.tokens:
        if token.text.lower() == "wenn" and token.pos == "SCONJ":
            start, end = _clause_span(sentence, token.i)
            return any(
                t.morph.get("VerbForm") == "Fin"
                and t.morph.get("Mood") == "Sub"
                and t.lemma.lower() not in _HOEFLICHKEIT_ONLY_KONJUNKTIV_LEMMAS
                for t in sentence.tokens[start:end]
            )
    return False


def _select_konjunktiv_ii_irreal_gegenwart(sentence: TaggedSentence) -> list[Candidate]:
    if sentence.text.rstrip().endswith("?"):
        return []
    if not any(t.text.lower() == "wenn" and t.pos == "SCONJ" for t in sentence.tokens):
        return []
    if not _wenn_clause_has_genuine_konjunktiv(sentence):
        return []
    return _select_konjunktiv_ii_base(sentence)


def _select_konjunktiv_ii_vergangenheit(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Past" or token.morph.get("Mood") != "Sub":
            continue
        lemma = token.lemma.lower()
        if lemma not in ("haben", "sein"):
            continue
        # Both directions, not only after -- see ``_select_konjunktiv_ii_base``'s
        # own comment on this same fact (docs/audits/cycle-09-report.md 1.7):
        # a verb-final "wenn"-clause puts the participle BEFORE the aux.
        # Clause-bounded, not sentence-wide, for the identical reason
        # ``_participle_before_in_clause``'s own docstring gives.
        participle = _participle_after_in_clause(sentence, token.i) or _participle_before_in_clause(
            sentence, token.i
        )
        if participle is None:
            continue
        part_lemma = participle.lemma.lower()
        if part_lemma:
            takes_sein = part_lemma in paradigms.AUX_SEIN_LEMMAS
            if (lemma == "sein") != takes_sein:
                continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma=lemma,
                person=person,
                number=number,
                tense_mood="SubjII",
            )
        )
    return out


# ------------------------------------------------------------------------
# Passive (Vorgangspassiv, Zustandspassiv) and Futur.
# ------------------------------------------------------------------------


def _select_passiv(sentence: TaggedSentence, *, morph_tense: str) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != morph_tense or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != "werden":
            continue
        participle = _participle_after(sentence, token.i)
        if participle is None or participle.lemma.lower() not in paradigms.TRANSITIVE_LEMMAS:
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma="werden",
                person=person,
                number=number,
                tense_mood=morph_tense,
            )
        )
    return out


def _select_passiv_praesens(sentence: TaggedSentence) -> list[Candidate]:
    return _select_passiv(sentence, morph_tense="Pres")


def _select_passiv_praeteritum(sentence: TaggedSentence) -> list[Candidate]:
    return _select_passiv(sentence, morph_tense="Past")


def _select_passiv_modalverben(sentence: TaggedSentence) -> list[Candidate]:
    """Modal + Partizip II + ``werden`` (Infinitiv) at clause end. The modal
    itself is blanked; its own Tense (Präsens or Präteritum) is read off the
    token so the reconstruction uses the matching irregular-finite table."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        if lemma not in paradigms.MODAL_LEMMAS:
            continue
        participle = _participle_after(sentence, token.i)
        if participle is None:
            continue
        werden_inf = _token_after(
            sentence, participle.i, lemma="werden", tags=_MODAL_INFINITIVE_TAGS
        )
        if werden_inf is None:
            continue
        tense = token.morph.get("Tense")
        if tense not in ("Pres", "Past"):
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma=lemma,
                person=person,
                number=number,
                tense_mood=tense,
                # Unconditional, unlike ``_irregular_finite_selector``'s own
                # cue: every candidate this selector emits already matched
                # ``lemma in paradigms.MODAL_LEMMAS`` above, the same closed,
                # reliable set.
                cue=_citation_cue(lemma, token.text),
            )
        )
    return out


def _select_zustandspassiv(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != "sein":
            continue
        participle = _participle_after_in_clause(sentence, token.i)
        if participle is None or participle.lemma.lower() not in paradigms.TRANSITIVE_LEMMAS:
            continue
        if _clause_contains_worden(sentence, token.i):
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma="sein",
                person=person,
                number=number,
                tense_mood="Pres",
            )
        )
    return out


def _select_zustandspassiv_zeiten(sentence: TaggedSentence) -> list[Candidate]:
    """Präteritum Zustandspassiv (``war`` + Partizip II, aux blanked) and
    Perfekt Zustandspassiv (``ist`` + Partizip II + ``gewesen``, the fixed
    closing word ``gewesen`` blanked instead -- it is the one token that
    uniquely marks this specific sub-pattern; blanking ``ist`` there would
    leave the same Präsens-Zustandspassiv-vs-something-else ambiguity this
    cycle avoids everywhere else)."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin" or token.lemma.lower() != "sein":
            continue
        if token.morph.get("Mood") != "Ind":
            continue
        participle = _participle_after_in_clause(sentence, token.i)
        if participle is None or participle.lemma.lower() not in paradigms.TRANSITIVE_LEMMAS:
            continue
        if _clause_contains_worden(sentence, token.i):
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        if token.morph.get("Tense") == "Past":
            out.append(
                Candidate(
                    token_index=token.i,
                    kind="irregular_aux",
                    lemma="sein",
                    person=person,
                    number=number,
                    tense_mood="Past",
                )
            )
        elif token.morph.get("Tense") == "Pres":
            gewesen = _token_after(sentence, participle.i, lemma="sein", tags=frozenset({"VAPP"}))
            if gewesen is not None:
                out.append(Candidate(token_index=gewesen.i, kind="fixed_particle"))
    return out


def _select_futur_i(sentence: TaggedSentence) -> list[Candidate]:
    """Präsens ``werden`` plus a bare infinitive at clause end -- the one
    shape genuinely distinct from the present passive (``werden`` plus a
    past PARTICIPLE) and from Futur II (``werden`` plus a participle plus a
    trailing ``haben``/``sein`` infinitive), both of which also put a
    ``werden`` finite form in this exact tense/mood.

    docs/audits/cycle-09-report.md (TODO.md 1.2): the previous version had
    two holes, not one. It excluded on ``_participle_after`` -- an
    UNBOUNDED, whole-rest-of-sentence forward-only search for a ``VVPP``
    tag -- which finds nothing for a subordinate-clause passive
    ("..., dass das Fleisch scharf angebraten wird, ..."), where the
    participle sits BEFORE the clause-final "wird", not after it. It then
    separately required an infinitive tag ANYWHERE later in the sentence,
    including past a clause boundary -- "Das Smartphone wird jetzt
    aufgeladen, damit Sie es am Abend sofort nutzen können." has no
    infinitive anywhere near "wird" at all, but "nutzen" (the unrelated
    "damit" clause's own infinitive) satisfied the old, sentence-wide
    check regardless.

    Both are closed the same way here: everything is scoped to ``token``'s
    own clause (``_clause_span``). A genuine Futur I clause has a bare
    infinitive in it and no participle; requiring the infinitive to be
    clause-local is what actually excludes both reported passive sentences
    above, since neither has ANY ``VVINF``/``VAINF`` token in "wird"'s own
    clause at all (confirmed directly: de_core_news_sm mistags some
    separable-prefix passive participles, "angebraten"/"aufgeladen", as
    ``VVIZU`` rather than ``VVPP``, so the participle-exclusion alone is
    not a complete fix -- REQUIRING an infinitive is). The participle
    exclusion is kept anyway, now also clause-scoped, as the check that
    distinguishes genuine Futur I from Futur II ("wird ... erklärt haben"),
    whose participle this exact tagger does tag correctly."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != "werden":
            continue
        clause_start, clause_end = _clause_span(sentence, token.i)
        clause_tokens = sentence.tokens[clause_start:clause_end]
        if any(t.tag == "VVPP" for t in clause_tokens):
            continue  # Futur II's own shape, or a passive the tagger got right
        has_infinitive = any(t.tag in _MODAL_INFINITIVE_TAGS for t in clause_tokens)
        if not has_infinitive:
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma="werden",
                person=person,
                number=number,
                tense_mood="Pres",
            )
        )
    return out


def _select_futur_ii(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != "werden":
            continue
        participle = _participle_after(sentence, token.i)
        if participle is None:
            continue
        part_lemma = participle.lemma.lower()
        if not part_lemma:
            continue
        expected_aux = "sein" if part_lemma in paradigms.AUX_SEIN_LEMMAS else "haben"
        final_aux = _token_after(
            sentence, participle.i, lemma=expected_aux, tags=frozenset({"VAINF"})
        )
        if final_aux is None:
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="irregular_aux",
                lemma="werden",
                person=person,
                number=number,
                tense_mood="Pres",
            )
        )
    return out


# ==============================================================================
# Infinitiv mit/um zu, and attributive participles (partizip_i, partizip_ii
# extended).
# ==============================================================================


def _select_infinitiv_zu(*, require_um: bool) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        lower_texts = [t.text.lower() for t in sentence.tokens]
        for token in sentence.tokens:
            if token.tag != "PTKZU":
                continue
            nxt = sentence.token_after(token.i)
            if nxt is None or nxt.tag != "VVINF":
                continue
            seg_start = 0
            for j in range(token.i - 1, -1, -1):
                if sentence.tokens[j].tag == "$,":
                    seg_start = j + 1
                    break
            has_um = "um" in lower_texts[seg_start : token.i]
            if has_um != require_um:
                continue
            out.append(Candidate(token_index=token.i, kind="fixed_particle"))
        return out

    return select


def _resolve_any_declension(prev: Token | None) -> Literal["weak", "mixed", "strong"] | None:
    """Weak/mixed/strong declension trigger from the token preceding a
    candidate, covering all three (unlike the fixed-declension adjective
    selectors above) -- a participle used attributively can follow any
    determiner class, or none."""
    trigger = _preceding_declension_trigger(prev)
    if trigger is not None:
        return trigger
    if _is_safe_zero_article_context(prev):
        return "strong"
    return None


# Infinitives whose Partizip I ("spielend", "lachend", ...) is common enough
# to test -- a closed allowlist, not a "lemma ends in -d" heuristic, because
# several genuine adjectives end in "-d" too (rund, gesund, fremd, blind)
# and are not participles at all.
_PARTIZIP_I_VERBS: frozenset[str] = frozenset(
    {
        "spielen",
        "lachen",
        "schlafen",
        "lesen",
        "weinen",
        "singen",
        "tanzen",
        "arbeiten",
        "warten",
        "laufen",
        "schreien",
        "kochen",
        "schwimmen",
        "lächeln",
    }
)


def _select_partizip_i_attributiv(sentence: TaggedSentence) -> list[Candidate]:
    """``cue`` is the underlying verb's own infinitive (docs/audits/
    cycle-06-modal-leak.md's cue extension) -- unconditionally derivable and
    trustworthy here, unlike the general lexical-verb case: ``lemma[:-1]``
    (the participle's own lemma minus its trailing "d") is only ever reached
    once it has already matched a member of the closed ``_PARTIZIP_I_VERBS``
    allowlist above, so it is by construction a verified real infinitive,
    not a tagger guess."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "ADJA":
            continue
        lemma = token.lemma
        if not lemma.endswith("d") or lemma[:-1] not in _PARTIZIP_I_VERBS:
            continue
        if not _followed_by_nominal(sentence, token.i):
            continue
        cell = _cell(token)
        if cell is None:
            continue
        declension = _resolve_any_declension(sentence.token_before(token.i))
        if declension is None:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="adjective",
                declension=declension,
                cell=cell,
                cue=_citation_cue(lemma[:-1], token.text),
            )
        )
    return out


_DECLENSION_SEARCH_WINDOW = 6

# Tags/POS a prepositional phrase's own internal tokens (its determiner,
# any attributive adjectives, its head noun) can carry -- used by
# ``_skip_intervening_pp`` below to jump OVER a whole inserted PP in one
# step rather than evaluating its own determiner as a candidate trigger.
_PP_COMPLEMENT_POS: frozenset[str] = frozenset({"NOUN", "PROPN", "ADJ"})


def _skip_intervening_pp(sentence: TaggedSentence, j: int, floor: int) -> int:
    """If a prepositional phrase (``ADP`` + optional determiner/adjectives +
    noun) sits immediately at and before position ``j`` scanning backward,
    return the index of the token just before that PP's own ``ADP`` head --
    so the caller resumes looking for a governing trigger BEFORE the
    inserted phrase, not at a determiner that belongs to the PP's own
    object. Confirmed necessary, not hypothetical: "das [von einem Maler]
    gestaltete Arbeitszimmer" has its own determiner INSIDE the inserted
    phrase ("einem", governing "Maler") one token nearer than the real
    governing "das" -- a naive token-by-token backward scan hits "einem"
    first and wrongly reports MIXED declension instead of "das"'s WEAK.
    Returns ``j`` unchanged (no-op) whenever the run ending at ``j`` does not
    actually terminate in an ``ADP`` within ``floor``, so a bare determiner
    sitting directly at ``j`` (not part of any PP) is left for the caller's
    own per-token check exactly as before this helper existed."""
    k = j
    consumed = False
    while k > floor and (
        sentence.tokens[k].pos in _PP_COMPLEMENT_POS or sentence.tokens[k].tag in _DETERMINER_TAGS
    ):
        k -= 1
        consumed = True
    if consumed and k > floor and sentence.tokens[k].pos == "ADP":
        return k - 1
    return j


def _find_governing_declension_trigger(
    sentence: TaggedSentence, participle_index: int
) -> tuple[int, Literal["weak", "mixed"]] | None:
    """Walk backward from an extended attributive participle looking for
    the token that actually governs its declension, tolerating an inserted
    phrase in between ("das [von Experten] entwickelte Programm" -- the
    immediately preceding token is "Experten", not the real governing
    article "das", unlike a plain, non-extended attributive adjective/
    participle where the immediately preceding token IS the trigger). The
    inserted phrase is skipped as a whole via ``_skip_intervening_pp`` so an
    ITS OWN determiner ("von einem Maler"'s "einem") is never mistaken for
    the real governor. Stops at a coordination boundary (comma/"und")
    without finding one, same posture as ``_AMBIGUOUS_ZERO_CONTEXT_TAGS``
    elsewhere in this module: real but out of this cycle's scope, not a
    guess.

    docs/audits/cycle-07-report.md defect 3, third cycle for this exact
    trap ("mit dem vor wenigen Wochen ___ Ball"): the token at ``j`` is now
    tested as a trigger candidate FIRST, before ``_skip_intervening_pp`` is
    ever tried on it. The previous order tried the skip first, and
    ``_skip_intervening_pp``'s own inner loop treats ANY determiner-tagged
    token as more PP-complement material to walk past (it has to, for "von
    einem Maler"'s own "einem") -- so when ``j`` itself already IS the real
    governing determiner ("dem" in "mit **dem** [vor wenigen Wochen]
    gekauften Ball"), the old order let the skip swallow it whole, walking
    straight past "dem" AND its own outer preposition "mit" too, and the
    real trigger was never tested at all. Checking the candidate first means
    a genuine trigger is always caught at the position it actually sits at,
    and ``_skip_intervening_pp`` is only ever asked to jump an inserted
    phrase that does NOT itself start on the trigger -- exactly the "von
    einem Maler" shape it was built for, unchanged."""
    start = max(-1, participle_index - 1 - _DECLENSION_SEARCH_WINDOW)
    j = participle_index - 1
    while j > start:
        candidate = sentence.tokens[j]
        trigger = _preceding_declension_trigger(candidate)
        if trigger is not None:
            return j, trigger
        if candidate.tag in ("KON", "$,"):
            return None
        skipped = _skip_intervening_pp(sentence, j, start)
        if skipped != j:
            j = skipped
            continue
        j -= 1
    return None


def _select_partizip_ii_attributiv_erweitert(sentence: TaggedSentence) -> list[Candidate]:
    """Extended attributive Partizip II ("das [von Experten] entwickelte
    Programm"): unlike a bare attributive participle ("das reparierte
    Auto", out of this topic's scope -- no plain ``partizip_ii_attributiv``
    topic exists in the taxonomy), the topic specifically tests the
    EXTENDED form, so a genuine intervening phrase is required, not merely
    inferred. Detected structurally: the governing determiner is found by
    scanning backward past the inserted phrase (see
    ``_find_governing_declension_trigger``), and at least one ``ADP`` token
    must appear between it and the participle (the "von Experten" shape).
    Scoped to weak/mixed (a determiner IS found) rather than also
    reconstructing a zero-article/strong reading here -- conservative on
    purpose, and an adverbial-only extension with no preposition at all
    ("das schnell entwickelte Programm") is left uncovered rather than
    guessed at, per the module's own "reject rather than guess" standard.

    ``cue`` is the participle's own infinitive (docs/audits/
    cycle-06-modal-leak.md's cue extension), looked up in
    ``paradigms.PARTICIPLE_II_TO_INFINITIVE`` -- safe unconditionally, since
    ``token.lemma`` has already been checked against
    ``paradigms.KNOWN_PARTICIPLE_FORMS`` above and that dict's keys are
    exactly that same closed set (see its own module-level comment)."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "ADJA":
            continue
        if token.lemma not in paradigms.KNOWN_PARTICIPLE_FORMS:
            continue
        if not _followed_by_nominal(sentence, token.i):
            continue
        cell = _cell(token)
        if cell is None:
            continue
        found = _find_governing_declension_trigger(sentence, token.i)
        if found is None:
            continue
        determiner_index, declension = found
        span = sentence.tokens[determiner_index + 1 : token.i]
        if not any(t.pos == "ADP" for t in span):
            continue
        infinitive = paradigms.PARTICIPLE_II_TO_INFINITIVE.get(token.lemma)
        cue = _citation_cue(infinitive, token.text) if infinitive else None
        out.append(
            Candidate(
                token_index=token.i, kind="adjective", declension=declension, cell=cell, cue=cue
            )
        )
    return out


# ==============================================================================
# The three Nominative article topics, revisited: each needs a genuine
# forcing anchor, not merely a Nominative-case determiner of the right
# family. docs/audits/cycle-06-modal-leak.md's own fix for these three
# topics was to report zero unconditionally (blanker.py's own module
# docstring, final section) -- correct at the time, because the plain
# ``_determiner_selector`` fires on ANY Nominative determiner of the
# family, with nothing ruling out the other three families also fitting the
# same slot ("Der/Ein/Kein/Mein Hund schläft im Garten" are all
# grammatical). The fix here is not to relax that judgment, it is to add a
# real anchor per topic and require it: each of the three selectors below
# returns a candidate ONLY when its own structural anchor is present in the
# sentence, so a bare, unanchored Nominative sentence still yields nothing
# (confirmed by the negative-case tests in
# tests/test_blanking_selectors.py) and only a genuinely forced item is ever
# built. This is what makes ``cloze_free`` an honest type for these three
# again (data/taxonomy.yaml's own ``eligible_types`` now says so).
# ==============================================================================

# Ordinal-numeral lemmas German's own morphologizer assigns for an
# attributive ordinal ("die erste", "der zweite", ...): each declines
# exactly like a normal attributive adjective (``ADJA``, no distinct
# ``NumType`` feature to key off in this tagger), so the lemma itself -- the
# closed set of German ordinal citation forms up to twelfth, plus "letzter"
# ("last"), which behaves identically as a uniqueness anchor -- is the only
# reliable signal. Confirmed empirically: "erste"/"zweite"/"dritte" lemmatise
# to "erster"/"zweiter"/"dritter", not to a cardinal-numeral form.
_ORDINAL_ADJEKTIV_LEMMAS: frozenset[str] = frozenset(
    {
        "erster",
        "zweiter",
        "dritter",
        "vierter",
        "fünfter",
        "sechster",
        "siebter",
        "achter",
        "neunter",
        "zehnter",
        "elfter",
        "zwölfter",
        "letzter",
    }
)


def _definite_uniqueness_anchor(sentence: TaggedSentence, det_index: int) -> bool:
    """Whether the definite article at ``det_index`` sits in a construction
    that makes its own referent identifiable on the strength of the sentence
    alone -- a superlative or ordinal attributive adjective before the head
    noun, or a relative clause immediately after it -- which is what rules
    out ``ein`` (there is nothing left to introduce; the referent is already
    singled out) and, with it, ``kein``/a possessive (neither reading fits a
    referent already pinned down this way). Walks past zero or more
    attributive adjectives to find the head noun, exactly like
    ``_followed_by_nominal``'s own neighbour-scan elsewhere in this module,
    but needs the noun's own INDEX here (not just whether one exists) to
    look past it for a following relative clause."""
    tokens = sentence.tokens
    j = det_index + 1
    saw_forcing_adjective = False
    while j < len(tokens) and tokens[j].tag == "ADJA":
        adj = tokens[j]
        if adj.morph.get("Degree") == "Sup" or adj.lemma.lower() in _ORDINAL_ADJEKTIV_LEMMAS:
            saw_forcing_adjective = True
        j += 1
    if saw_forcing_adjective:
        return True
    if j >= len(tokens) or tokens[j].pos not in ("NOUN", "PROPN"):
        return False
    comma = sentence.token_after(j)
    if comma is None or comma.tag != "$,":
        return False
    rel = sentence.token_after(comma.i)
    return rel is not None and rel.tag in _RELATIVE_PRONOUN_TAGS


def _earlier_subject_shaped_np(sentence: TaggedSentence, det_index: int) -> bool:
    """Whether a NOUN/PROPN/PRON tagged ``Case=Nom`` -- excluding a relative
    pronoun, which refers back to an antecedent rather than being a subject
    of its own -- appears anywhere before ``det_index``.

    docs/audits/cycle-07-report.md defect 4: "hat ___ schwerste Kiste
    getragen" blanked "die", tagged ``Case=Nom`` by this exact model even
    though "die schwerste Kiste" is the ACCUSATIVE object of "getragen" --
    "die" is syncretic between the two cases, and the morphologizer's own
    Case call is simply wrong here (confirmed directly: the token's own
    dependency arc, when the parser is loaded at all -- which this module's
    tagger deliberately never does, see ``sentence_tagger``'s own module
    docstring -- resolves it correctly as an accusative object, "oa", while
    ``token.morph["Case"]`` still says "Nom" regardless). Trusting the
    tagger's Case call alone is exactly the bug.

    This package has no dependency parse to ask "is this noun phrase the
    clause's subject" directly (a literal ``sb``/``nsubj`` check needs the
    parser ``sentence_tagger.py`` excludes for every other selector too, and
    extending that loader is out of this cycle's scope -- flagged, not
    silently worked around). The structural proxy used instead: a clause
    has exactly one subject, so if a DIFFERENT, earlier Nominative-tagged
    noun phrase already exists in the sentence, THIS one cannot also be the
    subject, regardless of what its own (possibly wrong) Case tag claims.
    In the defect sentence, "Nachbar" (the true subject, "Der nette
    Nachbar von nebenan ...") is exactly that earlier NP. A relative
    pronoun ("der", introducing "der uns gestern geholfen hat") is excluded
    from this scan: it is Nominative because it is the RELATIVE CLAUSE's
    own subject, a fact that says nothing about the MAIN clause's subject
    one way or the other."""
    for token in sentence.tokens[:det_index]:
        if token.tag in _RELATIVE_PRONOUN_TAGS:
            continue
        if token.pos in ("NOUN", "PROPN", "PRON") and token.morph.get("Case") == "Nom":
            return True
    return False


def _select_artikel_bestimmt_nom(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "ART" or token.morph.get("Definite") != "Def":
            continue
        cell = _cell(token)
        if cell is None or cell[0] != "Nom":
            continue
        if _preceded_by_adposition(sentence, token.i):
            continue
        if not _definite_uniqueness_anchor(sentence, token.i):
            continue
        if _earlier_subject_shaped_np(sentence, token.i):
            continue
        # docs/audits/cycle-07-report.md section A: this anchor rules out
        # the INDEFINITE family (nothing is left to introduce), but not a
        # possessive or demonstrative one ("Der/Mein größter Baum..." are
        # both grammatical) -- so, unlike ``artikel_unbestimmt_kein_nom``
        # below, this candidate is NOT marked ``lexeme_anchored`` and goes
        # through the ordinary cue-or-skip policy. Under the owner's
        # invariant-citation-cue rule (TODO.md 2.1) the cue is always "der"
        # here, including for the roughly one-in-four candidates whose own
        # blanked cell already IS the Nominative masculine cell the cue
        # names -- no longer withheld, per TODO.md 2.2's own exemption for
        # determiner slots (``blanker._determiner_outcome``).
        out.append(
            Candidate(
                token_index=token.i,
                kind="determiner",
                art_type="Def",
                cell=cell,
                cue=_determiner_cue(token, "Def"),
            )
        )
    return out


def _kein_causal_anchor(sentence: TaggedSentence, det_index: int) -> bool:
    """Whether the ``kein``/``keine`` token at ``det_index`` sits inside its
    own clause, and that clause is introduced by ``weil`` -- naming the
    negated noun's absence as the CAUSE of a consequence stated in the
    sentence's other clause (either order: "..., weil kein Bus fährt." or
    "Weil kein Bus fährt, ..."), which is what forces a negation here rather
    than ``der``/``ein``/a possessive: the other clause only makes sense if
    the noun is genuinely absent. Reuses ``_clause_span``, already built for
    the reflexive-pronoun selectors above, to find the determiner's own
    clause boundaries (nearest comma either side, or a sentence edge)
    without a dependency parse -- this module never loads the parser (see
    ``sentence_tagger``'s own module docstring)."""
    start, _ = _clause_span(sentence, det_index)
    first = sentence.tokens[start]
    return first.tag == "KOUS" and first.text.lower() == "weil"


def _select_artikel_unbestimmt_kein_nom(sentence: TaggedSentence) -> list[Candidate]:
    """Scoped to ``kein``/``keine`` (the ``Neg`` family) only, not plain
    ``ein`` -- no single-sentence device this task identified forces a bare
    affirmative indefinite over ``der``/``kein``/a possessive the way a
    ``weil``-clause forces a negation (a first-mention "Das ist ein Hund"
    admits ``kein``/``mein``/``der`` just as grammatically -- see the module
    docstring above).

    TODO.md 2.3: previously this candidate was produced ONLY when
    ``_kein_causal_anchor`` held, because with no cue the family choice
    itself ("kein" vs. "der"/"ein"/a possessive) was a free lexical choice
    an ordinary sentence does not force. Under the owner's invariant-cue
    rule the cue always names the family ("kein") directly, so that
    forcing is no longer needed to make the item solvable -- the anchor
    check is kept, un-deleted, and still recorded on ``lexeme_anchored``
    (a strictly stronger, unconditional claim than a cue makes, kept for
    whatever downstream value it has), but no longer GATES candidacy."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "PIAT" or not token.text.lower().startswith(_KEIN_STEM):
            continue
        cell = _cell(token)
        if cell is None or cell[0] != "Nom":
            continue
        if _preceded_by_adposition(sentence, token.i):
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="determiner",
                art_type="Neg",
                cell=cell,
                cue=_determiner_cue(token, "Neg"),
                lexeme_anchored=_kein_causal_anchor(sentence, token.i),
            )
        )
    return out


# A closed set of common A1 kinship nouns, deliberately excluding body-part
# nouns entirely (never added to this set in the first place, not filtered
# out afterwards): German idiomatically prefers the definite article for a
# body part ("Ich wasche mir die Hände", never "meine Hände" there), so a
# body-part noun is the INVERSE of this topic's own forcing case and must
# never be treated as one of its anchors. "Mann"/"Frau" are deliberately
# left out too: both are also the plain, non-kinship words for "man"/"woman"
# and including them risks the anchor firing on a sentence that never meant
# "husband"/"wife" at all.
_KINSHIP_LEMMAS: frozenset[str] = frozenset(
    {
        "mutter",
        "vater",
        "bruder",
        "schwester",
        "großmutter",
        "grossmutter",
        "großvater",
        "grossvater",
        "tante",
        "onkel",
        "cousine",
        "cousin",
        "sohn",
        "tochter",
        "oma",
        "opa",
        "neffe",
        "nichte",
        "ehemann",
        "ehefrau",
    }
)


def _possessive_person_anchor(sentence: TaggedSentence, noun_index: int) -> bool:
    """Whether the kinship noun at ``noun_index`` is immediately followed by
    a relative clause whose own clause contains an explicit 1st- or
    2nd-person personal pronoun -- the same "made identifiable by a
    following clause" shape ``_definite_uniqueness_anchor`` uses above, here
    forcing WHICH person's kinship member is meant (the relative clause's
    own speaker or addressee: "meine Großmutter, die ICH jedes Wochenende
    besuche" reads as MY grandmother precisely because I am the one doing
    the visiting), not merely that a possessive of some kind is meant.
    German has no dependency parse available here to confirm that pronoun is
    specifically the relative clause's own subject (this module never loads
    the parser -- see ``sentence_tagger``'s own module docstring), so its
    mere presence inside the clause is the signal, the same "reject rather
    than guess"-scoped structural proxy ``_clause_span`` and friends already
    use elsewhere in this module for an unavailable dependency fact."""
    tokens = sentence.tokens
    comma = sentence.token_after(noun_index)
    if comma is None or comma.tag != "$,":
        return False
    rel = sentence.token_after(comma.i)
    if rel is None or rel.tag not in _RELATIVE_PRONOUN_TAGS:
        return False
    j = rel.i + 1
    while j < len(tokens) and tokens[j].tag != "$,":
        tok = tokens[j]
        if tok.pos == "PRON" and tok.morph.get("Person") in ("1", "2"):
            return True
        j += 1
    return False


def _select_artikel_possessiv_nom(sentence: TaggedSentence) -> list[Candidate]:
    """TODO.md 2.3: previously gated entirely on ``_possessive_person_anchor``
    (a kinship noun immediately followed by a relative clause naming the
    possessor's own person) -- a real anchor, but narrow enough that this
    topic produced nothing at all across three cycles. Under the owner's
    invariant-cue rule the cue always names WHICH possessive stem the
    blanked token itself is (read off the token, never guessed), so the
    same reasoning that revives ``artikel_bestimmt_nom``/``artikel_
    unbestimmt_kein_nom`` applies here too: the free-lexical-choice
    ambiguity a cue exists to close is closed for every Nominative
    possessive, not only the kinship-plus-relative-clause shape. That
    narrower shape is kept, un-deleted, and still recorded on
    ``lexeme_anchored`` when it holds, but no longer gates candidacy."""
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "PPOSAT" or token.morph.get("Poss") != "Yes":
            continue
        cell = _cell(token)
        if cell is None or cell[0] != "Nom":
            continue
        if _preceded_by_adposition(sentence, token.i):
            continue
        noun = sentence.token_after(token.i)
        anchored = (
            noun is not None
            and noun.pos in ("NOUN", "PROPN")
            and noun.lemma.lower() in _KINSHIP_LEMMAS
            and _possessive_person_anchor(sentence, noun.i)
        )
        out.append(
            Candidate(
                token_index=token.i,
                kind="determiner",
                art_type="Poss",
                cell=cell,
                cue=_determiner_cue(token, "Poss"),
                lexeme_anchored=anchored,
            )
        )
    return out


SELECTORS: dict[str, Selector] = {
    "artikel_bestimmt_nom": _select_artikel_bestimmt_nom,
    "artikel_unbestimmt_kein_nom": _select_artikel_unbestimmt_kein_nom,
    "artikel_possessiv_nom": _select_artikel_possessiv_nom,
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
    "praepositionen_genitiv_gehoben": _determiner_selector(
        "Gen", _ALL_ART_TYPES, _GEHOBENE_GENITIV_PRAEPOSITIONEN
    ),
    # -- Cycle 3: pronouns --------------------------------------------------
    "pronomen_personal_nom": _personal_pronoun_selector("Nom"),
    "pronomen_personal_akk": _personal_pronoun_selector("Acc"),
    "pronomen_personal_dat": _personal_pronoun_selector("Dat"),
    "verben_reflexiv_akk": _reflexive_selector("Acc"),
    "verben_reflexiv_dat": _reflexive_selector("Dat"),
    "relativsatz_nom_akk": _relative_pronoun_selector(frozenset({"Nom", "Acc"})),
    "relativsatz_dativ": _relative_pronoun_selector(frozenset({"Dat"})),
    "relativsatz_genitiv": _relative_pronoun_selector(frozenset({"Gen"})),
    # -- Cycle 3: verb conjugation --------------------------------------------
    "verb_sein_haben": _irregular_finite_selector(
        frozenset({"sein", "haben"}), "Pres", "Pres", "Ind"
    ),
    "verb_praesens_regelm": _select_verb_praesens_regelm,
    "verb_praesens_vokalwechsel": _select_verb_praesens_vokalwechsel,
    "modalverben_praesens": _irregular_finite_selector(
        paradigms.MODAL_LEMMAS | {"möchten"}, "Pres", "Pres", "Ind"
    ),
    "verben_trennbar_praesens": _select_verben_trennbar_praesens,
    "praeteritum_sein_haben_modal": _irregular_finite_selector(
        frozenset({"sein", "haben"}) | paradigms.MODAL_LEMMAS, "Past", "Past", "Ind"
    ),
    "praeteritum_vollverben": _select_praeteritum_vollverben,
    "nomen_plural": _select_nomen_plural,
    # -- Cycle 3: compound tenses, passive, Konjunktiv II ---------------------
    "perfekt_haben": _select_perfekt_haben,
    "perfekt_sein": _select_perfekt_sein,
    "plusquamperfekt": _select_plusquamperfekt,
    "konjunktiv_ii_hoeflichkeit": _select_konjunktiv_ii_hoeflichkeit,
    "konjunktiv_ii_irreal_gegenwart": _select_konjunktiv_ii_irreal_gegenwart,
    "konjunktiv_ii_vergangenheit": _select_konjunktiv_ii_vergangenheit,
    "passiv_praesens": _select_passiv_praesens,
    "passiv_praeteritum": _select_passiv_praeteritum,
    "passiv_modalverben": _select_passiv_modalverben,
    "zustandspassiv": _select_zustandspassiv,
    "zustandspassiv_zeiten": _select_zustandspassiv_zeiten,
    "futur_i": _select_futur_i,
    "futur_ii": _select_futur_ii,
    # -- Cycle 3: infinitive/participle constructions -------------------------
    "infinitiv_mit_zu": _select_infinitiv_zu(require_um=False),
    "infinitiv_um_zu": _select_infinitiv_zu(require_um=True),
    "partizip_i_attributiv": _select_partizip_i_attributiv,
    "partizip_ii_attributiv_erweitert": _select_partizip_ii_attributiv_erweitert,
}
