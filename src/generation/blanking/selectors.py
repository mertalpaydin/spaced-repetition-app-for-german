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

from src.generation.blanking import paradigms
from src.generation.blanking.paradigms import Cell
from src.generation.blanking.sentence_tagger import TaggedSentence, Token
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
    # (Zahn)" for "Zähne", "___ (müssen) er noch arbeiten" for "muss". Set
    # only by the selectors for the topics the report names (``nomen_plural``,
    # ``modalverben_praesens``, ``passiv_modalverben``); ``None`` everywhere
    # else, including every candidate a topic's own selector could not
    # derive a *reliable* cue for -- see ``_citation_cue`` and
    # ``_plural_noun_cue``'s own docstrings for exactly what "reliable"
    # means per part of speech. Additive: nothing downstream is required to
    # read this field, and every existing candidate kind keeps emitting
    # ``cue=None`` exactly as before.
    cue: str | None = None


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
# Dative reading.
_TEMPORAL_ACCUSATIVE_LEMMAS: frozenset[str] = frozenset(
    {"morgen", "tag", "woche", "monat", "jahr", "abend", "nacht", "stunde", "minute", "sekunde"}
)

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
    for other in sentence.tokens[clause_start:clause_end]:
        if other.i == exclude_index:
            continue
        if other.pos not in ("NOUN", "PROPN", "PRON"):
            continue
        if other.morph.get("Case") != "Acc":
            continue
        if other.lemma.lower() in _TEMPORAL_ACCUSATIVE_LEMMAS:
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


def _has_accusative_object(
    sentence: TaggedSentence, exclude_index: int, clause_start: int, clause_end: int
) -> bool:
    """Combines the Case-based and word-order-based bare-object signals --
    see ``_has_bare_accusative_object`` and ``_immediately_followed_by_object_np``
    docstrings for why neither alone is sufficient. Both are scoped to the
    reflexive's OWN clause (``clause_start``/``clause_end`` from
    ``_clause_span``) -- see the module-level comment above
    ``_REFLEXIVE_CASE_BY_FORM`` for why an unscoped, whole-sentence scan was
    a confirmed defect, not merely a theoretical one."""
    return _has_bare_accusative_object(
        sentence, exclude_index, clause_start, clause_end
    ) or _immediately_followed_by_object_np(sentence, exclude_index, clause_end)


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
    (``_reflexive_case``) does exactly that on a ``None`` return."""
    finite = [
        t
        for t in sentence.tokens[clause_start:clause_end]
        if t.pos in ("VERB", "AUX") and t.morph.get("VerbForm") == "Fin"
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
    elif verb_lemma in paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT:
        has_object = _has_accusative_object(sentence, token.i, clause_start, clause_end)
        verb_case = "Dat" if has_object else "Acc"
    else:
        verb_case = "Acc"
    unambiguous = _REFLEXIVE_CASE_BY_FORM.get(lower)
    if unambiguous is not None and unambiguous != verb_case:
        return None
    return verb_case


def _reflexive_selector(fixed_case: str) -> Selector:
    def select(sentence: TaggedSentence) -> list[Candidate]:
        out: list[Candidate] = []
        for token in sentence.tokens:
            if token.pos != "PRON":
                continue
            lower = token.text.lower()
            if lower not in _REFLEXIVE_CAPABLE_FORMS:
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
# eligible).
_IRREGULAR_PRAESENS_LEMMAS: frozenset[str] = (
    frozenset({"sein", "haben", "werden"}) | paradigms.MODAL_LEMMAS | frozenset({"möchten"})
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
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family="regular_praesens",
            )
        )
    return out


def _select_verb_praesens_vokalwechsel(sentence: TaggedSentence) -> list[Candidate]:
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.tag != "VVFIN":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        lemma = token.lemma.lower()
        if not paradigms.is_vokalwechsel_praesens_lemma(lemma):
            continue
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family="vokalwechsel_praesens",
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
        if _own_clause_particle(sentence, token.i) is None:
            continue
        lemma = token.lemma.lower()
        if not lemma:
            continue
        family = (
            "vokalwechsel_praesens"
            if paradigms.is_vokalwechsel_praesens_lemma(lemma)
            else "regular_praesens"
        )
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family=family,
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
# tagger already committed to. Every OTHER finite form of "schalten" tested
# ("schaltest", "schaltet", "schalten", "schaltete") tags correctly, so this
# is a targeted exclusion of the one broken lemma, not a guess about the
# whole verb family.
_MISLEMMATIZED_PRAETERITUM_LEMMAS: frozenset[str] = frozenset({"schalen"})


def _select_praeteritum_vollverben(sentence: TaggedSentence) -> list[Candidate]:
    """Simple past of a full lexical verb -- weak (by rule), strong or mixed
    (from the closed tables in ``paradigms.py``). Excludes sein/haben/modals
    (``praeteritum_sein_haben_modal``'s own scope), ``werden`` (the
    passive/Futur auxiliary topics' scope), and the confirmed-mislemmatised
    lemmas above."""
    excluded = (
        frozenset({"sein", "haben", "werden"})
        | paradigms.MODAL_LEMMAS
        | {"möchten"}
        | _MISLEMMATIZED_PRAETERITUM_LEMMAS
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
            family = "regular_praeteritum"
        person, number = token.morph.get("Person"), token.morph.get("Number")
        if not person or not number:
            continue
        out.append(
            Candidate(
                token_index=token.i,
                kind="verb_form",
                lemma=lemma,
                person=person,
                number=number,
                verb_family=family,
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
# only the two functions that compute one.
# ------------------------------------------------------------------------


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
    this cue mechanism existed."""
    return None if lemma.lower() == surface.lower() else lemma


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
       "no cue is the safe outcome" posture."""
    lemma = token.lemma
    if not lemma or not lemma[:1].isupper():
        return None
    if token.morph.get("Case") == "Dat":
        return None
    return _citation_cue(lemma, token.text)


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


# The five modals plus the frozen sixth ("möchten") -- exactly the set
# ``uniqueness.py`` treats as always-interchangeable in a modal slot (its
# own module docstring's policy table). A modal lemma's own infinitive IS
# its citation form, and selection into this set already means the lemma
# matched one of exactly six known, closed values (below), so unlike
# ``_plural_noun_cue`` there is no separate reliability check to make: a
# mislemmatised token would simply not be in ``lemmas`` in the first place
# and never reach here at all.
_MODAL_LEMMAS_FOR_CUE: frozenset[str] = paradigms.MODAL_LEMMAS | frozenset({"möchten"})


def _irregular_finite_selector(
    lemmas: frozenset[str], tense_mood: str, morph_tense: str, morph_mood: str
) -> Selector:
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
            cue = _citation_cue(lemma, token.text) if lemma in _MODAL_LEMMAS_FOR_CUE else None
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


def _participle_before(sentence: TaggedSentence, index: int) -> Token | None:
    for token in reversed(sentence.tokens[:index]):
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
        if _participle_after(sentence, token.i) is not None:
            continue  # vergangenheit's own shape, not this one
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


def _select_konjunktiv_ii_irreal_gegenwart(sentence: TaggedSentence) -> list[Candidate]:
    if sentence.text.rstrip().endswith("?"):
        return []
    if not any(t.text.lower() == "wenn" and t.pos == "SCONJ" for t in sentence.tokens):
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
        participle = _participle_after(sentence, token.i)
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
        participle = _participle_after(sentence, token.i)
        if participle is None or participle.lemma.lower() not in paradigms.TRANSITIVE_LEMMAS:
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
    out: list[Candidate] = []
    for token in sentence.tokens:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if token.morph.get("Tense") != "Pres" or token.morph.get("Mood") != "Ind":
            continue
        if token.lemma.lower() != "werden":
            continue
        if _participle_after(sentence, token.i) is not None:
            continue  # Futur II's own shape
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
            Candidate(token_index=token.i, kind="adjective", declension=declension, cell=cell)
        )
    return out


_DECLENSION_SEARCH_WINDOW = 6


def _find_governing_declension_trigger(
    sentence: TaggedSentence, participle_index: int
) -> tuple[int, Literal["weak", "mixed"]] | None:
    """Walk backward from an extended attributive participle looking for
    the token that actually governs its declension, tolerating an inserted
    phrase in between ("das [von Experten] entwickelte Programm" -- the
    immediately preceding token is "Experten", not the real governing
    article "das", unlike a plain, non-extended attributive adjective/
    participle where the immediately preceding token IS the trigger).
    Stops at a coordination boundary (comma/"und") without finding one,
    same posture as ``_AMBIGUOUS_ZERO_CONTEXT_TAGS`` elsewhere in this
    module: real but out of this cycle's scope, not a guess."""
    start = max(-1, participle_index - 1 - _DECLENSION_SEARCH_WINDOW)
    for j in range(participle_index - 1, start, -1):
        candidate = sentence.tokens[j]
        trigger = _preceding_declension_trigger(candidate)
        if trigger is not None:
            return j, trigger
        if candidate.tag in ("KON", "$,"):
            return None
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
    guessed at, per the module's own "reject rather than guess" standard."""
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
        out.append(
            Candidate(token_index=token.i, kind="adjective", declension=declension, cell=cell)
        )
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
