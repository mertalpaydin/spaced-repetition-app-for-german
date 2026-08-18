"""Reverse (grammatical cell -> surface form) lookups for the
generate-then-blank pipeline, built entirely from the closed-class paradigm
tables that already exist in ``src.taxonomy.facets`` -- the definite article
paradigm, the ein-word ending paradigm, and the three attributive
adjective/participle declension paradigms (weak, mixed, strong). This module
adds no new linguistic facts: every table it exposes is a dict built by
inverting one of those existing tuples, so the paradigm data itself is
declared in exactly one place.

Why ``src.taxonomy.facets`` and not ``src.verification.layer2_morphology``:
the task that specified this module named ``layer2_morphology`` as the
paradigm source, but that module's own tables (``DEFINITE_FORMS``,
``CASE_FORM_FALLBACK``) only cover the singular definite article and a set
membership check -- no plural, no ein-word/possessive endings, no adjective
declension endings at all. ``layer2_morphology`` itself imports from
``src.taxonomy.facets`` for exactly this reason (see its
``attributive_adjective_gender_candidates`` import). The full paradigms this
cycle needs live in ``facets``, so that is what is reused here; duplicating
them into a second copy would violate the same "do not duplicate" rule this
module exists to satisfy.

The imported names are private to ``facets`` (a leading underscore). That
module cannot be edited to make them public without touching an existing
file, which is out of scope for this cycle, so they are imported directly.
Every accessor below is a pure, deterministic function of a paradigm table
already tested by ``tests/test_taxonomy.py`` and exercised throughout
``src.verification``; nothing here re-derives or overrides that data.

Every lookup here returns ``None``/``()`` rather than guessing when a cell,
stem, or ending is not covered by the underlying table -- most notably, the
ein-word ending paradigm has no Nominative/Accusative *plural* row at all
(bare "ein" genuinely has no plural in German), so a plural "kein"/possessive
Nominative or Accusative form (e.g. "keine Kinder") cannot be reconstructed
here and callers must treat that as a reject, not a guess. See
``tests/test_blanking_paradigms.py`` for a regression test pinning
this exact gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.taxonomy.facets import (
    _ADJ_ENDING_MIXED,
    _ADJ_ENDING_STRONG,
    _ADJ_ENDING_WEAK,
    _DEFINITE_ARTICLE_PARADIGM,
    _EIN_ENDING_PARADIGM,
    _EIN_WORD_STEMS,
    _PERSONAL_PRONOUN_PARADIGM,
    _REFLEXIVE_PARADIGM,
    _RELATIVE_PRONOUN_PARADIGM,
)
from src.taxonomy.facets import UNK as _FACETS_UNK

# A grammatical cell: (Case, Gender, Number), using the exact UD FEATS
# vocabulary spaCy's German pipeline emits and ``facets.py`` already keys its
# tables on -- no translation layer, same as ``src.taxonomy.tagger``'s own
# docstring notes for ``TaggedAnswer.feats``.
Cell = tuple[str, str, str]

Declension = str  # "weak" | "mixed" | "strong", checked by callers


def _reverse_definite_article() -> dict[Cell, str]:
    reverse: dict[Cell, str] = {}
    for form, case, gender, number in _DEFINITE_ARTICLE_PARADIGM:
        reverse[(case, gender, number)] = form
    return reverse


DEFINITE_ARTICLE_BY_CELL: dict[Cell, str] = _reverse_definite_article()


def _reverse_ein_ending() -> dict[Cell, str]:
    reverse: dict[Cell, str] = {}
    for ending, case, gender, number in _EIN_ENDING_PARADIGM:
        reverse[(case, gender, number)] = ending
    return reverse


EIN_ENDING_BY_CELL: dict[Cell, str] = _reverse_ein_ending()
_EIN_VALID_ENDINGS: frozenset[str] = frozenset(e for e, *_ in _EIN_ENDING_PARADIGM)


def _reverse_adjective_endings(
    paradigm: tuple[tuple[str, str, str, str], ...],
) -> dict[Cell, str]:
    reverse: dict[Cell, str] = {}
    for ending, case, gender, number in paradigm:
        reverse[(case, gender, number)] = ending
    return reverse


ADJ_ENDING_BY_CELL: dict[Declension, dict[Cell, str]] = {
    "weak": _reverse_adjective_endings(_ADJ_ENDING_WEAK),
    "mixed": _reverse_adjective_endings(_ADJ_ENDING_MIXED),
    "strong": _reverse_adjective_endings(_ADJ_ENDING_STRONG),
}


def definite_article_form(cell: Cell) -> str | None:
    """The definite article form for ``cell``, or ``None`` if the cell is
    outside the paradigm (it never is, for a valid (Case, Gender, Number)
    triple, but callers still treat ``None`` as a reject rather than assume
    that)."""
    return DEFINITE_ARTICLE_BY_CELL.get(cell)


def match_ein_word(text: str) -> tuple[str, str] | None:
    """Split a surface form into ``(stem, ending)`` against the ein-word
    family (ein/kein/mein/dein/sein/ihr/unser/euer), or ``None`` if it
    matches no stem in that family with a recognised ending.

    Mirrors ``facets._decode_article``'s own stem-matching loop exactly: same
    stem list, same iteration order (``_EIN_WORD_STEMS`` tries "euer" before
    its contracted form "eur", so "euer" itself is never mis-split as
    "eur"+"er"), same requirement that the remainder be a real ending in
    ``_EIN_ENDING_PARADIGM``. This one function serves indefinite ("ein"),
    negative ("kein") and every possessive stem alike -- they all decline on
    the identical ending paradigm in German.
    """
    lower = text.strip().lower()
    for stem in _EIN_WORD_STEMS:
        if lower.startswith(stem):
            ending = lower[len(stem) :]
            if ending in _EIN_VALID_ENDINGS:
                return stem, ending
    return None


def ein_word_form(stem: str, cell: Cell) -> str | None:
    """The ein-word-family form for ``stem`` at ``cell``, or ``None`` if the
    ending paradigm has no row for ``cell`` at all (most notably: no
    Nominative/Accusative plural row exists, see module docstring)."""
    ending = EIN_ENDING_BY_CELL.get(cell)
    return None if ending is None else stem + ending


def adjective_ending(declension: Declension, cell: Cell) -> str | None:
    """The attributive adjective/participle ending for ``declension`` at
    ``cell``, or ``None`` if the cell is outside the paradigm."""
    return ADJ_ENDING_BY_CELL[declension].get(cell)


def family_forms(art_type: str, token_text: str) -> dict[Cell, str] | None:
    """Every ``cell -> surface form`` pair for the closed-class family
    ``token_text`` belongs to, given its already-classified ``art_type``
    (``"Def"``, ``"Ind"``, ``"Neg"``, or ``"Poss"``).

    ``"Def"`` returns the whole (lemma-invariant) definite article paradigm.
    Every other ``art_type`` first extracts ``token_text``'s own stem via
    ``match_ein_word`` (so "meine" only ever yields other "mein"-family
    forms, never "deine" or "eine") and returns ``None`` if the word does not
    actually match that family at all -- the caller's signal to reject the
    candidate rather than guess a family for an unrecognised form.
    """
    if art_type == "Def":
        return dict(DEFINITE_ARTICLE_BY_CELL)
    match = match_ein_word(token_text)
    if match is None:
        return None
    stem, _ending = match
    return {cell: stem + ending for cell, ending in EIN_ENDING_BY_CELL.items()}


def adjective_family_forms(declension: Declension, stem: str) -> dict[Cell, str]:
    """Every ``cell -> surface form`` pair for ``stem`` under ``declension``
    (weak/mixed/strong) -- used to generate near-miss distractors (the same
    stem, a different, equally valid cell in the same declension paradigm)."""
    return {cell: stem + ending for cell, ending in ADJ_ENDING_BY_CELL[declension].items()}


# ==============================================================================
# Cycle 3: personal / reflexive / relative pronoun paradigms.
#
# Reused from ``src.taxonomy.facets`` exactly like the article/adjective
# tables above -- no new pronoun facts are declared here, only reversed
# lookups over the tables that module already carries and
# ``tests/test_taxonomy.py`` already exercises.
# ==============================================================================

PersonCell = tuple[str, str, str]  # (Case, Person, Number)


def _reverse_personal_pronoun() -> dict[tuple[str, str, str, str], str]:
    reverse: dict[tuple[str, str, str, str], str] = {}
    for form, case, person, number, gender in _PERSONAL_PRONOUN_PARADIGM:
        reverse[(case, person, number, gender)] = form
    return reverse


PERSONAL_PRONOUN_BY_CELL: dict[tuple[str, str, str, str], str] = _reverse_personal_pronoun()


def personal_pronoun_form(case: str, person: str, number: str, gender: str) -> str | None:
    """The personal-pronoun form for ``(Case, Person, Number)``, resolving
    Gender only where German itself marks it (3rd person singular: "er" vs
    "sie" vs "es"). Every other cell is keyed on ``facets.UNK`` for gender in
    the source table -- German genuinely does not mark gender there (1st/2nd
    person, or any person in the plural), so falling back to the ``UNK`` row
    is not a guess, it is the only candidate that table ever had for that
    cell. Returns ``None`` for a cell the paradigm has no row for at all."""
    exact = PERSONAL_PRONOUN_BY_CELL.get((case, person, number, gender))
    if exact is not None:
        return exact
    return PERSONAL_PRONOUN_BY_CELL.get((case, person, number, _FACETS_UNK))


def personal_pronoun_family_forms(person: str, number: str, gender: str) -> dict[str, str]:
    """Every ``Case -> form`` pair for one ``(Person, Number, Gender)`` --
    used for near-miss distractors within the same person/number (e.g. "ich"
    blanked as Nominative, distractors "mich"/"mir", never "du"/"er")."""
    out: dict[str, str] = {}
    for case in ("Nom", "Acc", "Dat"):
        form = personal_pronoun_form(case, person, number, gender)
        if form is not None:
            out[case] = form
    return out


def _reverse_reflexive() -> dict[tuple[str, str, str], str]:
    reverse: dict[tuple[str, str, str], str] = {}
    for form, case, person, number in _REFLEXIVE_PARADIGM:
        reverse[(case, person, number)] = form
    return reverse


REFLEXIVE_BY_CELL: dict[tuple[str, str, str], str] = _reverse_reflexive()


def reflexive_form(case: str, person: str, number: str) -> str | None:
    """The reflexive-pronoun form for ``(Case, Person, Number)``, or
    ``None`` if the paradigm has no row (it always does for a valid triple,
    but callers still treat ``None`` as a reject, never a guess)."""
    return REFLEXIVE_BY_CELL.get((case, person, number))


def reflexive_family_forms(person: str, number: str) -> dict[str, str]:
    """Every ``Case -> form`` pair for one ``(Person, Number)`` -- the
    Accusative/Dative pair for that person, used for near-miss distractors."""
    out: dict[str, str] = {}
    for case in ("Acc", "Dat"):
        form = reflexive_form(case, person, number)
        if form is not None:
            out[case] = form
    return out


RelCell = tuple[str, str, str]  # (Case, Gender, Number) -- Gender may be facets.UNK


def _reverse_relative_pronoun() -> dict[RelCell, str]:
    reverse: dict[RelCell, str] = {}
    for form, case, gender, number in _RELATIVE_PRONOUN_PARADIGM:
        reverse[(case, gender, number)] = form
    return reverse


RELATIVE_PRONOUN_BY_CELL: dict[RelCell, str] = _reverse_relative_pronoun()


def relative_pronoun_form(case: str, gender: str, number: str) -> str | None:
    """The relative-pronoun form for ``(Case, Gender, Number)``. Plural
    relative pronouns carry no gender in German at all (see
    ``facets._RELATIVE_PRONOUN_PARADIGM``'s own docstring), so a Plural cell
    falls back to the table's single ``UNK``-gender plural row -- not a
    guess, the only row that cell was ever going to have."""
    exact = RELATIVE_PRONOUN_BY_CELL.get((case, gender, number))
    if exact is not None:
        return exact
    if number == "Plur":
        return RELATIVE_PRONOUN_BY_CELL.get((case, _FACETS_UNK, "Plur"))
    return None


def relative_pronoun_family_forms(gender: str, number: str) -> dict[str, str]:
    """Every ``Case -> form`` pair for one ``(Gender, Number)`` -- used for
    near-miss distractors (same referent, a different case/role)."""
    out: dict[str, str] = {}
    for case in ("Nom", "Acc", "Dat", "Gen"):
        form = relative_pronoun_form(case, gender, number)
        if form is not None:
            out[case] = form
    return out


# ==============================================================================
# Cycle 3: verb conjugation.
#
# Two kinds of fact, kept structurally separate per the module's "do not
# invent paradigm data" standard:
#
# * REGULAR formation is a RULE (endings, epenthesis) applied to a lemma's
#   own stem -- computed, not tabulated, exactly like a weak verb actually
#   works in German.
# * IRREGULAR stems/forms (ablaut preterites, participles, sein/haben/werden/
#   the modals, Konjunktiv II) are lexical facts no rule derives. They are
#   hand-verified, standard textbook forms held in small, closed,
#   documented tables below -- the same posture ``facets._IRREGULAR_VERB_FORMS``
#   already takes for the handful of forms it covers, extended here to a
#   fuller (but still explicitly finite, still A1-B2-relevant) verb list.
#
# Every reconstruction, regular or irregular, is still cross-checked against
# the actual token text by ``blanker.py`` before an item is built (see its
# module docstring) -- a wrong table entry or a formula that does not apply
# to some lemma produces a skip, never a wrong accept.
# ==============================================================================

VerbCell = tuple[str, str]  # (Person, Number)

_FINITE_CELLS: tuple[VerbCell, ...] = (
    ("1", "Sing"),
    ("2", "Sing"),
    ("3", "Sing"),
    ("1", "Plur"),
    ("2", "Plur"),
    ("3", "Plur"),
)

_PRAESENS_ENDINGS: dict[VerbCell, str] = {
    ("1", "Sing"): "e",
    ("2", "Sing"): "st",
    ("3", "Sing"): "t",
    ("1", "Plur"): "en",
    ("2", "Plur"): "t",
    ("3", "Plur"): "en",
}
_PRAETERITUM_WEAK_ENDINGS: dict[VerbCell, str] = {
    ("1", "Sing"): "te",
    ("2", "Sing"): "test",
    ("3", "Sing"): "te",
    ("1", "Plur"): "ten",
    ("2", "Plur"): "tet",
    ("3", "Plur"): "ten",
}
_PRAETERITUM_STRONG_ENDINGS: dict[VerbCell, str] = {
    ("1", "Sing"): "",
    ("2", "Sing"): "st",
    ("3", "Sing"): "",
    ("1", "Plur"): "en",
    ("2", "Plur"): "t",
    ("3", "Plur"): "en",
}

# A stem ending in a dental (d/t) needs an epenthetic -e- before a
# consonant-initial ending ("arbeiten" -> "du arbeitest", not "arbeitst") --
# standard German epenthesis, not a per-verb fact.
_EPENTHESIS_STEMS: tuple[str, ...] = ("d", "t")
# A stem ending in a sibilant loses the -s- of a following -st (2sg only):
# "reisen" -> "du reist", not "du reisst".
_SIBILANT_STEMS: tuple[str, ...] = ("s", "ss", "z", "x")


def _weak_stem(lemma: str) -> str | None:
    """``lemma`` minus its infinitive ending, or ``None`` if ``lemma`` does
    not end like an infinitive at all (too short, or no -n)."""
    if lemma.endswith("en") and len(lemma) > 3:
        return lemma[:-2]
    if lemma.endswith("n") and len(lemma) > 2:
        return lemma[:-1]
    return None


def regular_praesens_form(lemma: str, person: str, number: str) -> str | None:
    """Present tense of a regular weak verb, by rule. Callers gate this to
    lemmas NOT in ``VOKALWECHSEL_PRAESENS``/``STRONG_VERBS``/``MIXED_VERBS``/
    the irregular-finite table (see ``selectors.py``); a mismatch against the
    real token is still the final safety net regardless."""
    stem = _weak_stem(lemma)
    if stem is None:
        return None
    cell = (person, number)
    if cell not in _PRAESENS_ENDINGS:
        return None
    if cell == ("2", "Sing") and stem.endswith(_SIBILANT_STEMS):
        return stem + "t"
    if cell in (("2", "Sing"), ("2", "Plur"), ("3", "Sing")) and stem.endswith(_EPENTHESIS_STEMS):
        return stem + ("est" if cell == ("2", "Sing") else "et")
    return stem + _PRAESENS_ENDINGS[cell]


def regular_praeteritum_form(lemma: str, person: str, number: str) -> str | None:
    """Präteritum of a regular weak verb, by rule (stem + -te/-test/...,
    with the same dental epenthesis as the present tense)."""
    stem = _weak_stem(lemma)
    if stem is None:
        return None
    cell = (person, number)
    ending = _PRAETERITUM_WEAK_ENDINGS.get(cell)
    if ending is None:
        return None
    if stem.endswith(_EPENTHESIS_STEMS):
        return stem + "e" + ending
    return stem + ending


@dataclass(frozen=True)
class _StrongVerb:
    """One irregular verb's principal parts beyond the infinitive: the
    Präteritum stem (before personal endings are applied) and the Partizip
    II. Both are hand-verified standard forms, not derived."""

    praeteritum_stem: str
    partizip_ii: str


# Strong (ablaut) verbs: Präteritum takes the STRONG endings above (zero
# ending in 1st/3rd singular), never the weak -te family.
STRONG_VERBS: dict[str, _StrongVerb] = {
    "sprechen": _StrongVerb("sprach", "gesprochen"),
    "geben": _StrongVerb("gab", "gegeben"),
    "nehmen": _StrongVerb("nahm", "genommen"),
    "lesen": _StrongVerb("las", "gelesen"),
    "sehen": _StrongVerb("sah", "gesehen"),
    "helfen": _StrongVerb("half", "geholfen"),
    "bleiben": _StrongVerb("blieb", "geblieben"),
    "fahren": _StrongVerb("fuhr", "gefahren"),
    "kommen": _StrongVerb("kam", "gekommen"),
    "schliessen": _StrongVerb("schloss", "geschlossen"),
    "brechen": _StrongVerb("brach", "gebrochen"),
    "essen": _StrongVerb("ass", "gegessen"),
    "trinken": _StrongVerb("trank", "getrunken"),
    "finden": _StrongVerb("fand", "gefunden"),
    "schreiben": _StrongVerb("schrieb", "geschrieben"),
    "stehen": _StrongVerb("stand", "gestanden"),
    "gehen": _StrongVerb("ging", "gegangen"),
    "liegen": _StrongVerb("lag", "gelegen"),
    "laufen": _StrongVerb("lief", "gelaufen"),
    "sitzen": _StrongVerb("sass", "gesessen"),
    "heissen": _StrongVerb("hiess", "geheissen"),
    "lassen": _StrongVerb("liess", "gelassen"),
    "fallen": _StrongVerb("fiel", "gefallen"),
    "halten": _StrongVerb("hielt", "gehalten"),
    "schlafen": _StrongVerb("schlief", "geschlafen"),
    "tragen": _StrongVerb("trug", "getragen"),
    "schlagen": _StrongVerb("schlug", "geschlagen"),
    "waschen": _StrongVerb("wusch", "gewaschen"),
    "treffen": _StrongVerb("traf", "getroffen"),
    "werfen": _StrongVerb("warf", "geworfen"),
    "ziehen": _StrongVerb("zog", "gezogen"),
    "fliegen": _StrongVerb("flog", "geflogen"),
    "verlieren": _StrongVerb("verlor", "verloren"),
    "gewinnen": _StrongVerb("gewann", "gewonnen"),
    "beginnen": _StrongVerb("begann", "begonnen"),
    "singen": _StrongVerb("sang", "gesungen"),
    "springen": _StrongVerb("sprang", "gesprungen"),
    "schwimmen": _StrongVerb("schwamm", "geschwommen"),
    "steigen": _StrongVerb("stieg", "gestiegen"),
    "scheinen": _StrongVerb("schien", "geschienen"),
    "bitten": _StrongVerb("bat", "gebeten"),
    "vergessen": _StrongVerb("vergass", "vergessen"),
    "rufen": _StrongVerb("rief", "gerufen"),
    "tun": _StrongVerb("tat", "getan"),
    "sterben": _StrongVerb("starb", "gestorben"),
    "wachsen": _StrongVerb("wuchs", "gewachsen"),
}

# Mixed verbs: consonant-changed stem, but the WEAK personal endings
# (-te/-test/...) -- "brachte", never "brach" with a zero ending.
MIXED_VERBS: dict[str, _StrongVerb] = {
    "bringen": _StrongVerb("brach", "gebracht"),
    "denken": _StrongVerb("dach", "gedacht"),
    "kennen": _StrongVerb("kann", "gekannt"),
    "nennen": _StrongVerb("nann", "genannt"),
    "wissen": _StrongVerb("wuss", "gewusst"),
}

# Present-tense stem-vowel change (e->i/ie, a->ä, au->äu), 2nd/3rd person
# singular only -- a SEPARATE fact from the Präteritum table above: several
# of these verbs (fahren, laufen, lesen, sehen...) also happen to be
# ablauting in the Präteritum, but several strong-Präteritum verbs
# (gehen, kommen, schreiben, bleiben, fliegen...) do NOT change vowel in the
# present at all, so the two tables are kept independent rather than
# derived from one another.
VOKALWECHSEL_PRAESENS: dict[str, tuple[str, str]] = {
    # lemma -> (du-form, er/sie/es-form)
    "geben": ("gibst", "gibt"),
    "essen": ("isst", "isst"),
    "nehmen": ("nimmst", "nimmt"),
    "sprechen": ("sprichst", "spricht"),
    "helfen": ("hilfst", "hilft"),
    "treffen": ("triffst", "trifft"),
    "werfen": ("wirfst", "wirft"),
    "sterben": ("stirbst", "stirbt"),
    "brechen": ("brichst", "bricht"),
    "vergessen": ("vergisst", "vergisst"),
    "lesen": ("liest", "liest"),
    "sehen": ("siehst", "sieht"),
    "fahren": ("fährst", "fährt"),
    "schlafen": ("schläfst", "schläft"),
    "tragen": ("trägst", "trägt"),
    "waschen": ("wäschst", "wäscht"),
    "halten": ("hältst", "hält"),
    "lassen": ("lässt", "lässt"),
    "fallen": ("fällst", "fällt"),
    "laufen": ("läufst", "läuft"),
    "wachsen": ("wächst", "wächst"),
    "schlagen": ("schlägst", "schlägt"),
    "raten": ("rätst", "rät"),
    "empfehlen": ("empfiehlst", "empfiehlt"),
}


def strong_praeteritum_form(lemma: str, person: str, number: str) -> str | None:
    entry = STRONG_VERBS.get(lemma)
    if entry is None:
        return None
    stem = entry.praeteritum_stem
    cell = (person, number)
    if cell == ("2", "Sing") and stem.endswith(_EPENTHESIS_STEMS + _SIBILANT_STEMS):
        return stem + "est"
    if cell == ("2", "Plur") and stem.endswith(_EPENTHESIS_STEMS):
        return stem + "et"
    ending = _PRAETERITUM_STRONG_ENDINGS.get(cell)
    return None if ending is None else stem + ending


def mixed_praeteritum_form(lemma: str, person: str, number: str) -> str | None:
    entry = MIXED_VERBS.get(lemma)
    if entry is None:
        return None
    ending = _PRAETERITUM_WEAK_ENDINGS.get((person, number))
    return None if ending is None else entry.praeteritum_stem + ending


# An inseparable-prefixed verb ALWAYS inherits its base verb's own present-
# tense conjugation class unchanged -- "verlassen" changes its stem vowel
# exactly like "lassen" ("du verlässt"/"du lässt"), "entsprechen" exactly
# like "sprechen" ("er entspricht"/"er spricht"), and so on. This is a real,
# categorical German derivational fact (unlike a SEPARABLE prefix -- "auf-",
# "an-", ... -- which detaches from the finite verb entirely and is handled
# by ``verben_trennbar_praesens`` separately), not a guess: composing it
# from the closed ``VOKALWECHSEL_PRAESENS`` table is the same "closed
# paradigm, not invented" posture as every other lookup in this module,
# applied compositionally instead of by direct entry. Confirmed the gap this
# closes: "verlasse" (present, 1st singular of "verlassen") was previously
# invisible to this table (only "lassen" is a direct key), so
# ``verb_praesens_regelm`` wrongly treated it as a regular verb -- see
# ``selectors.py``'s own exclusion list, which now defers to
# ``is_vokalwechsel_praesens_lemma`` instead of a bare membership check.
_INSEPARABLE_PREFIXES: tuple[str, ...] = ("be", "emp", "ent", "er", "ge", "miss", "ver", "zer")


def _vokalwechsel_base_and_prefix(lemma: str) -> tuple[str, str] | None:
    """``(prefix, base)`` if ``lemma`` is a direct ``VOKALWECHSEL_PRAESENS``
    entry (``prefix=""``) or an inseparable-prefixed derivative of one, else
    ``None``. Checked longest-prefix-first so a lemma is never split on a
    shorter prefix when a longer one in the tuple also matches."""
    if lemma in VOKALWECHSEL_PRAESENS:
        return "", lemma
    for prefix in sorted(_INSEPARABLE_PREFIXES, key=len, reverse=True):
        if lemma.startswith(prefix):
            base = lemma[len(prefix) :]
            if base in VOKALWECHSEL_PRAESENS:
                return prefix, base
    return None


def is_vokalwechsel_praesens_lemma(lemma: str) -> bool:
    """Whether ``lemma``, directly or as an inseparable-prefixed derivative,
    belongs to the present-tense stem-vowel-change family -- the single
    check both ``verb_praesens_regelm`` (excludes on this) and
    ``verb_praesens_vokalwechsel`` (includes on this) share, so the two
    topics stay disjoint by construction rather than by two independently
    maintained conditions that could drift apart."""
    return _vokalwechsel_base_and_prefix(lemma) is not None


def vokalwechsel_praesens_form(lemma: str, person: str, number: str) -> str | None:
    found = _vokalwechsel_base_and_prefix(lemma)
    if found is None:
        return None
    prefix, base = found
    du, er = VOKALWECHSEL_PRAESENS[base]
    if (person, number) == ("2", "Sing"):
        return prefix + du
    if (person, number) == ("3", "Sing"):
        return prefix + er
    return regular_praesens_form(lemma, person, number)


VerbResolver = Callable[[str, str, str], "str | None"]

VERB_FAMILY_RESOLVERS: dict[str, VerbResolver] = {
    "regular_praesens": regular_praesens_form,
    "regular_praeteritum": regular_praeteritum_form,
    "vokalwechsel_praesens": vokalwechsel_praesens_form,
    "strong_praeteritum": strong_praeteritum_form,
    "mixed_praeteritum": mixed_praeteritum_form,
}


def verb_family_form(family: str, lemma: str, person: str, number: str) -> str | None:
    """Reconstruct one finite form for ``family`` (one of
    ``VERB_FAMILY_RESOLVERS``' keys), or ``None`` if ``family`` is unknown or
    the resolver itself returns ``None`` (lemma not in that family's table,
    or the cell is uncovered)."""
    resolver = VERB_FAMILY_RESOLVERS.get(family)
    if resolver is None:
        return None
    return resolver(lemma, person, number)


def verb_family_forms(family: str, lemma: str) -> dict[VerbCell, str]:
    """Every ``(Person, Number) -> form`` pair ``family`` resolves for
    ``lemma`` -- used for near-miss distractors (same lemma, a different
    person/number in the same family)."""
    out: dict[VerbCell, str] = {}
    for cell in _FINITE_CELLS:
        form = verb_family_form(family, lemma, cell[0], cell[1])
        if form is not None:
            out[cell] = form
    return out


# ==============================================================================
# Irregular finite paradigms: sein, haben, werden, and the modals, across
# every tense/mood this cycle's topics need (Präsens, Präteritum, Konjunktiv
# II). Hand-verified standard forms -- the same four Konjunktiv II lemmas
# named in ``konjunktiv_ii_hoeflichkeit``'s own ``rule_hint`` in
# ``data/taxonomy.yaml`` (würde, könnte, hätte, wäre); no other modal's
# Konjunktiv II is tabulated because "sollte"/"wollte" are surface-identical
# to their OWN Präteritum Indikativ (a genuine, unresolvable ambiguity --
# see the top-level report), so they are deliberately left out rather than
# guessed at.
# ==============================================================================


def _finite_table(forms: tuple[str, str, str, str, str, str]) -> dict[VerbCell, str]:
    return dict(zip(_FINITE_CELLS, forms, strict=True))


IRREGULAR_FINITE: dict[str, dict[str, dict[VerbCell, str]]] = {
    "sein": {
        "Pres": _finite_table(("bin", "bist", "ist", "sind", "seid", "sind")),
        "Past": _finite_table(("war", "warst", "war", "waren", "wart", "waren")),
        "SubjII": _finite_table(("wäre", "wärst", "wäre", "wären", "wärt", "wären")),
    },
    "haben": {
        "Pres": _finite_table(("habe", "hast", "hat", "haben", "habt", "haben")),
        "Past": _finite_table(("hatte", "hattest", "hatte", "hatten", "hattet", "hatten")),
        "SubjII": _finite_table(("hätte", "hättest", "hätte", "hätten", "hättet", "hätten")),
    },
    "werden": {
        "Pres": _finite_table(("werde", "wirst", "wird", "werden", "werdet", "werden")),
        "Past": _finite_table(("wurde", "wurdest", "wurde", "wurden", "wurdet", "wurden")),
        "SubjII": _finite_table(("würde", "würdest", "würde", "würden", "würdet", "würden")),
    },
    "können": {
        "Pres": _finite_table(("kann", "kannst", "kann", "können", "könnt", "können")),
        "Past": _finite_table(("konnte", "konntest", "konnte", "konnten", "konntet", "konnten")),
        "SubjII": _finite_table(("könnte", "könntest", "könnte", "könnten", "könntet", "könnten")),
    },
    "müssen": {
        "Pres": _finite_table(("muss", "musst", "muss", "müssen", "müsst", "müssen")),
        "Past": _finite_table(("musste", "musstest", "musste", "mussten", "musstet", "mussten")),
    },
    "wollen": {
        "Pres": _finite_table(("will", "willst", "will", "wollen", "wollt", "wollen")),
        "Past": _finite_table(("wollte", "wolltest", "wollte", "wollten", "wolltet", "wollten")),
    },
    "dürfen": {
        "Pres": _finite_table(("darf", "darfst", "darf", "dürfen", "dürft", "dürfen")),
        "Past": _finite_table(("durfte", "durftest", "durfte", "durften", "durftet", "durften")),
    },
    "sollen": {
        "Pres": _finite_table(("soll", "sollst", "soll", "sollen", "sollt", "sollen")),
        "Past": _finite_table(("sollte", "solltest", "sollte", "sollten", "solltet", "sollten")),
    },
}

# The five modals named in modalverben_praesens's own rule_hint, "möchten"
# treated as its own frozen lemma (its Konjunktiv-II-derived form no longer
# alternates with a separate indicative "mögen" present in ordinary use, and
# spaCy's lemmatiser already returns "möchten" as its lemma, never "mögen").
MODAL_LEMMAS: frozenset[str] = frozenset({"können", "müssen", "wollen", "dürfen", "sollen"})
IRREGULAR_FINITE["möchten"] = {
    "Pres": _finite_table(("möchte", "möchtest", "möchte", "möchten", "möchtet", "möchten")),
}


def irregular_finite_form(lemma: str, tense_mood: str, person: str, number: str) -> str | None:
    table = IRREGULAR_FINITE.get(lemma, {}).get(tense_mood)
    return None if table is None else table.get((person, number))


def irregular_finite_family_forms(lemma: str, tense_mood: str) -> dict[VerbCell, str] | None:
    """All six cells for one ``(lemma, tense_mood)``, or ``None`` if this
    cycle's tables do not cover that combination at all (never a partial,
    silently-incomplete dict)."""
    table = IRREGULAR_FINITE.get(lemma, {}).get(tense_mood)
    return None if table is None else dict(table)


# ==============================================================================
# Aux-selection and transitivity: lexical facts, not rules. Perfekt/
# Plusquamperfekt choose "haben" or "sein" by VERB, not by any morphological
# marker; Zustandspassiv is only well-formed from a transitive verb's
# participle. The two sets below are disjoint BY CONSTRUCTION (a verb is
# either the "sein + motion/change-of-state" kind or the "can be passivised"
# kind, never modelled as both here) -- that disjointness is exactly what
# lets ``selectors.py`` tell a Perfekt-mit-sein "ist gegangen" apart from a
# Zustandspassiv "ist repariert" using the SAME surface shape (sein + VVPP),
# see the top-level report's cautions.
#
# Separable-prefixed verbs (aufstehen, ankommen...) are mostly excluded:
# spaCy's lemmatiser does not RELIABLY reduce a separable verb's participle
# to its own infinitive (confirmed empirically: "aufgestanden" lemmatises to
# the nonsense "aufgestehen", not "aufstehen"), so a lemma lookup against a
# separable verb's base form is not trustworthy enough to key a lexical fact
# on IN GENERAL. It is not uniformly wrong, though -- "angekommen" ->
# "ankommen", "umgezogen" -> "umziehen", "weggegangen" -> "weggehen" and
# "aufgewacht" -> "aufwachen" were all confirmed to lemmatise correctly in
# testing, so those four are included by name (each individually verified,
# not assumed from the pattern); "aufstehen" is the one common motion/
# change-of-state separable verb confirmed to lemmatise WRONG and is
# deliberately left out. Any other separable verb's Perfekt/Zustandspassiv
# candidate simply fails this lookup and is skipped -- lower recall, never a
# wrong classification.
# ==============================================================================

AUX_SEIN_LEMMAS: frozenset[str] = frozenset(
    {
        "gehen",
        "kommen",
        "fahren",
        "fliegen",
        "laufen",
        "steigen",
        "fallen",
        "sterben",
        "bleiben",
        "werden",
        "wachsen",
        "reisen",
        "passieren",
        "geschehen",
        "gelingen",
        "ankommen",
        "umziehen",
        "weggehen",
        "aufwachen",
    }
)

TRANSITIVE_LEMMAS: frozenset[str] = frozenset(
    {
        "schliessen",
        "öffnen",
        "kochen",
        "schreiben",
        "reparieren",
        "verkaufen",
        "kaufen",
        "bauen",
        "lesen",
        "essen",
        "trinken",
        "waschen",
        "machen",
        "geben",
        "nehmen",
        "sehen",
        "planen",
        "organisieren",
        "renovieren",
        "gründen",
        "bitten",
        "rufen",
        "finden",
        "vergessen",
        "tragen",
        "werfen",
        "ziehen",
        "singen",
        "malen",
        "bestellen",
        "beenden",
    }
)

# Closed list of attributive participle SURFACE forms recognised by
# partizip_ii_attributiv_erweitert -- see selectors.py for why this cannot
# be a lemma check (an ADJA-tagged participle's own lemma is already the
# bare participle form, not the infinitive, so there is nothing further to
# reduce it to) and why it is closed-list rather than a "starts with ge-" or
# "ends in -t" heuristic (both would false-positive on genuine adjectives:
# "gerade", "geheim", "gemein", "gewiss" all start with "ge-"; "gut", "alt",
# "bekannt" all end in "-t").
KNOWN_PARTICIPLE_FORMS: frozenset[str] = frozenset(
    {v.partizip_ii for v in STRONG_VERBS.values()}
    | {v.partizip_ii for v in MIXED_VERBS.values()}
    | {
        "repariert",
        "gebaut",
        "gekauft",
        "gekocht",
        "geplant",
        "geöffnet",
        "organisiert",
        "produziert",
        "renoviert",
        "entwickelt",
        "gemacht",
        "gefragt",
        "gesucht",
        "gegründet",
        "fotografiert",
        "informiert",
        "verkauft",
        "bestellt",
        "gemalt",
        "beendet",
    }
)

# ==============================================================================
# Dative-reflexive verb argument structure: another lexical fact, not a
# rule, same posture as ``AUX_SEIN_LEMMAS``/``TRANSITIVE_LEMMAS`` above (this
# module's own docstring says "no new linguistic facts", and those two sets
# already established the precedent that a verb's own argument structure is
# exactly the kind of fact this cycle keeps as a closed list here rather than
# re-deriving it per sentence).
#
# docs/audits/cycle-04-report.md's second finding: ``uns``/``sich`` are
# syncretic between Accusative and Dative, so the reflexive pronoun's own
# surface form cannot decide which topic (``verben_reflexiv_akk`` vs
# ``verben_reflexiv_dat``) it belongs to -- the GOVERNING VERB decides it,
# because German reflexive case is a property of the verb's own argument
# structure, not of the sentence it happens to appear in. Three of the
# report's four sampled ``verben_reflexiv_dat`` items were actually
# Accusative-reflexive verbs ("sich freuen", "sich treffen", "sich ändern")
# that a same-clause-scanning "is there an accusative object anywhere"
# heuristic wrongly promoted to Dative; only "sich helfen" was genuinely
# Dative. This is the fix: a closed list of verbs whose reflexive object is
# genuinely Dative, checked by the governing verb's own lemma
# (``selectors._governing_verb_lemma``), not by scanning for an unrelated
# accusative elsewhere in the sentence.
#
# Sourced from the standard German pedagogical grammar's own "Verben mit
# Reflexivpronomen im Dativ" class (Dreyer/Schmitt, "Praktische Grammatik der
# deutschen Sprache"; Duden's reflexive-verb entries) -- an established,
# closed, textbook-taught set, not invented for this cycle. Two subclasses:
#
# * ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` -- the "sich (Dat) etwas tun"
#   pattern the report itself names ("sich etwas vorstellen", "sich etwas
#   merken", "sich etwas leisten", "sich etwas ansehen"): the Dative
#   reflexive stands beside its own separate Accusative object. Several of
#   these verbs are genuinely POLYSEMOUS with an Accusative-reflexive
#   reading when no object is present ("sich vorstellen" alone = "introduce
#   oneself", Accusative; "sich (Dat) etwas vorstellen" = "imagine
#   something", Dative) -- ``selectors._reflexive_case`` only treats a verb
#   from this set as Dative when a genuine Accusative object also sits in
#   the SAME clause (see ``_has_accusative_object``'s clause-scoped call),
#   never from list membership alone. "kaufen" and "waschen" are included
#   here because the confirmed body-part/purchase-for-oneself constructions
#   ("Er kauft sich ein neues Auto.", "Sie wäscht sich die Hände.") were
#   already this module's own precedent for the object-based signal before
#   this fix (see ``selectors._immediately_followed_by_object_np``'s
#   docstring) -- folding them into the closed list rather than leaving them
#   to a bare object-presence guess is strictly more precise, not a new
#   claim.
# * ``DATIVE_REFLEXIVE_VERBS_NO_OBJECT`` -- "helfen", the report's own
#   sole genuinely-Dative example ("Wir helfen uns gegenseitig."):
#   "helfen" governs the Dative on ANY object, reflexive or not ("ich helfe
#   dir"), so no co-occurring Accusative is expected or required. Kept as
#   its own single-verb set rather than folded into the set above so a
#   future addition to either subclass states its own evidence, rather than
#   silently inheriting the "and requires an object" default of the other.
DATIVE_REFLEXIVE_VERBS_WITH_OBJECT: frozenset[str] = frozenset(
    {
        "aneignen",
        "anhören",
        "anschauen",
        "ansehen",
        "anziehen",
        "ausdenken",
        "ausziehen",
        "bestellen",
        "eingestehen",
        "einbilden",
        "erlauben",
        "ersparen",
        "holen",
        "kämmen",
        "kaufen",
        "leihen",
        "leisten",
        "merken",
        "nehmen",
        "notieren",
        "putzen",
        "überlegen",
        "verdienen",
        "vornehmen",
        "vorstellen",
        "vorwerfen",
        "waschen",
        "wünschen",
        "zutrauen",
    }
)
DATIVE_REFLEXIVE_VERBS_NO_OBJECT: frozenset[str] = frozenset({"helfen"})
DATIVE_REFLEXIVE_VERBS: frozenset[str] = (
    DATIVE_REFLEXIVE_VERBS_WITH_OBJECT | DATIVE_REFLEXIVE_VERBS_NO_OBJECT
)
