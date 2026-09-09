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

from src.lexicon.lemmatizer import SEPARABLE_PREFIXES as _SEPARABLE_PREFIXES
from src.phrases.morph_tables import (
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
from src.phrases.morph_tables import UNK as _FACETS_UNK

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


def candidate_weak_praesens_infinitives(surface: str) -> frozenset[str]:
    """TODO.md 1.6: candidate infinitives ``surface`` -- a 3rd-singular-
    present verb form ending in "-t" -- could mechanically come from,
    reached by reversing ``regular_praesens_form``'s own (Person=3,
    Number=Sing) rule rather than by a second, independent un-conjugation
    written from scratch. Up to two raw candidates are tried (the plain
    strip, "passt" -> "pass" + "en" = "passen"; and, when ``surface`` ends
    "-et", the epenthesis-stripped one, "arbeitet" -> "arbeit" + "en" =
    "arbeiten"), and each is forward-checked against
    ``regular_praesens_form`` itself before being offered -- so a caller
    never receives a candidate this same forward rule would not itself
    reproduce as ``surface``. This forward check alone is not sufficient to
    pick a single right answer: both raw candidates for an "-et" surface
    form forward-validate (the epenthesis rule and the plain rule can
    coincide), and a genuinely irregular (strong/vokalwechsel) verb's
    unreduced "-t" form can forward-validate on a WRONG candidate purely by
    the accident that ``regular_praesens_form`` reapplies the regular
    ending mechanically regardless of whether the verb is actually regular
    ("trägt" -> candidate "trägen", which reconjugates back to "trägt"
    despite not being a real word). A real-word check on top of this (this
    module has no dictionary to consult) is therefore the caller's job, not
    this function's -- see ``selectors._reduce_unreduced_weak_finite_lemma``,
    the one caller, for how it is used and gated."""
    surface = surface.strip().lower()
    if not surface.endswith("t") or len(surface) < 3:
        return frozenset()
    raw = {surface[:-1] + "en"}
    if surface.endswith("et") and len(surface) > 3:
        raw.add(surface[:-2] + "en")
    return frozenset(
        candidate for candidate in raw if regular_praesens_form(candidate, "3", "Sing") == surface
    )


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
    # TODO.md 8.8: this key used to be spelled "schliessen" (ASCII, the
    # Swiss/keyboard-limited substitute for "ß"). The Präteritum/Partizip-II
    # forms ("schloss"/"geschlossen") were always correct standard German
    # (short-vowel "o", genuinely ``ss`` there) -- only the INFINITIVE
    # itself needed the fix, since "ie" is a long-vowel diphthong and
    # standard German never spells one of those followed by "ss" (see
    # ``carrier_validation.py``'s own ``_SWISS_DIPHTHONG_SS_PATTERN``
    # comment for the same rule stated the other way, as a corpus-carrier
    # rejection). This key is looked up by two different callers with two
    # different sources for their query key -- ``strong_praeteritum_form``/
    # ``PARTICIPLE_II_TO_INFINITIVE`` against a real ``token.lemma`` (spaCy's
    # own lemmatiser, confirmed directly to already return "schließen" with
    # the correct ß, never "schliessen") -- so the ASCII spelling was a
    # dead key for THAT caller (docs/audits/cycle-10-corpus-report.md 8.8;
    # ``sentence_source.py``'s own comment already named this exact gap:
    # "geschlossen" lemmatises to "schließen", which does not match the
    # ASCII "schliessen" key), and a second caller,
    # ``selectors._select_partizip_ii_attributiv_erweitert``, which inverts
    # ``PARTICIPLE_II_TO_INFINITIVE`` to build the learner-facing CUE for an
    # attributive participle -- for that caller the wrong key was not a
    # dead lookup, it was a live one that handed a Swiss-spelled cue
    # ("(schliessen)") straight to the learner. Fixing the key closes both:
    # the lemma lookup and ``TRANSITIVE_LEMMAS`` (below) now agree with what
    # the tagger actually produces, and the derived cue is correctly
    # "schließen".
    "schließen": _StrongVerb("schloss", "geschlossen"),
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
    # Added for ``is_participle_shape``/``participle_shape_infinitive``
    # (below): both are common, everyday strong verbs whose separable-
    # prefixed participles ("angebraten", "aufgeladen") are the two exact
    # sentences docs/audits/cycle-09-report.md and TODO.md 1.2 reported
    # de_core_news_sm mistagging ``VVIZU``. Hand-verified standard forms,
    # same standard as every other entry in this table.
    "braten": _StrongVerb("briet", "gebraten"),
    "laden": _StrongVerb("lud", "geladen"),
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

# Every surface form ``IRREGULAR_FINITE`` produces (across sein/haben/werden/
# every modal, every tense_mood, every cell) that does NOT itself equal one
# of that table's own lemma keys -- i.e. every INFLECTED form of a closed,
# irregular verb, with the handful of cells that happen to be spelled
# identically to their own infinitive ("wir/sie können" == "können") removed
# again by the set difference so those are not wrongly flagged.
#
# docs/audits/cycle-06-modal-leak.md's finding: a lexical-verb selector's own
# lemma-trustworthiness guard (``selectors._lexical_verb_lemma_trustworthy``)
# needs to distinguish an infinitive from an inflected form, and a suffix
# shape check alone cannot do it -- "sollten" (spaCy's own, wrong, lemma for
# "solltet") ends in "-en" exactly like a genuine infinitive does. What
# actually tells the two apart is not spelling, it is membership: a German
# infinitive can never legitimately coincide with an INFLECTED form of one of
# these closed, already-fully-tabulated irregular verbs (no distinct lexeme
# is spelled "sollten", "hatten", or "wärst"), so any candidate lemma found
# here is proof the tagger mislemmatised an inflected form as if it were a
# citation form -- the same class of self-consistent tagger bug
# ``_MISLEMMATIZED_VERB_LEMMAS`` names one confirmed instance of at a time,
# generalised here from the closed table itself so it can never drift out of
# sync with it and does not need a new entry hand-added per confirmed case.
IRREGULAR_FINITE_INFLECTED_FORMS: frozenset[str] = frozenset(
    form
    for tables in IRREGULAR_FINITE.values()
    for table in tables.values()
    for form in table.values()
) - frozenset(IRREGULAR_FINITE.keys())


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
        # TODO.md 8.8: was "schliessen" (ASCII) -- see ``STRONG_VERBS``'s own
        # comment on that same key for why this needed to match spaCy's
        # actual lemma ("schließen") to ever be reached at lookup time.
        "schließen",
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
        # Added alongside ``is_participle_shape``/``participle_shape_
        # infinitive`` below: "braten"/"laden" are the bare verbs behind the
        # two exact TODO.md 1.2 sentences ("angebraten"/"aufgeladen"), and
        # "packen" behind the third named example ("eingepackt"). The
        # SEPARABLE-prefixed lemma is also listed by name for each, matching
        # ``AUX_SEIN_LEMMAS``'s own precedent above of listing a verified
        # prefixed form alongside its base rather than only the base --
        # ``participle_shape_infinitive`` reconstructs exactly this
        # prefixed spelling for a separable-prefix participle, never the
        # bare base alone, so the prefixed form is the one this set actually
        # needs to contain for that lookup to succeed.
        "braten",
        "anbraten",
        "laden",
        "aufladen",
        "packen",
        "einpacken",
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

# The infinitive each ``KNOWN_PARTICIPLE_FORMS`` surface form belongs to --
# docs/audits/cycle-06-modal-leak.md's cue extension for
# ``partizip_ii_attributiv_erweitert``: the participle's own citation cue is
# its infinitive, never itself (an ADJA-tagged participle's own ``lemma`` is
# already the bare participle form, see ``KNOWN_PARTICIPLE_FORMS``'s own
# comment, so there is nothing to reduce it to without this second table).
# The strong/mixed half is derived by inverting ``STRONG_VERBS``/
# ``MIXED_VERBS`` (no new fact, same "do not duplicate paradigm data"
# posture as the rest of this module); the weak half is hand-verified by the
# same standard as every other literal table here. Keys are exactly
# ``KNOWN_PARTICIPLE_FORMS`` -- confirmed by
# ``tests/test_blanking_paradigms.py`` -- so a selector that already checked
# ``lemma in KNOWN_PARTICIPLE_FORMS`` can look its infinitive up here
# unconditionally, no separate reliability check needed.
_WEAK_PARTICIPLE_TO_INFINITIVE: dict[str, str] = {
    "repariert": "reparieren",
    "gebaut": "bauen",
    "gekauft": "kaufen",
    "gekocht": "kochen",
    "geplant": "planen",
    "geöffnet": "öffnen",
    "organisiert": "organisieren",
    "produziert": "produzieren",
    "renoviert": "renovieren",
    "entwickelt": "entwickeln",
    "gemacht": "machen",
    "gefragt": "fragen",
    "gesucht": "suchen",
    "gegründet": "gründen",
    "fotografiert": "fotografieren",
    "informiert": "informieren",
    "verkauft": "verkaufen",
    "bestellt": "bestellen",
    "gemalt": "malen",
    "beendet": "beenden",
}

PARTICIPLE_II_TO_INFINITIVE: dict[str, str] = {
    **{verb.partizip_ii: lemma for lemma, verb in STRONG_VERBS.items()},
    **{verb.partizip_ii: lemma for lemma, verb in MIXED_VERBS.items()},
    **_WEAK_PARTICIPLE_TO_INFINITIVE,
}

# ==============================================================================
# Participle by SHAPE, not by tag: docs/audits/cycle-09-report.md /
# TODO.md 1.2's own caveat, confirmed directly against this exact tagger
# (``uv run`` against the two reported sentences): a separable-prefixed past
# participle ("angebraten", "aufgeladen", "eingepackt") is sometimes tagged
# ``VVIZU`` (the zu-infinitive tag) instead of ``VVPP`` in exactly this
# construction ("wird ... angebraten", clause-final passive word order).
# ``selectors.py``'s participle search (``_participle_after``/``_before``
# and their ``_in_clause`` variants) gates on the TAG alone today, so every
# selector reading through them silently drops a passive whose participle
# the tagger mislabels this way -- confirmed at scale in
# ``docs/audits/corpus-coverage.md``: ``passiv_praesens``/``passiv_
# praeteritum`` are two of the LOWEST-yield topics out of 49 on both
# corpora, on a construction (the present passive) that is one of the
# commonest in German journalism.
#
# The fix is not to trust the tagger's alternative reading, it is to trust
# neither reading and check the word's own SPELLING instead: a genuine
# German past participle has one of exactly three shapes --
#
#   1. separable prefix + "ge" + stem + participle ending ("an" + "ge" +
#      "braten" -> "angebraten", "auf" + "ge" + "laden" -> "aufgeladen")
#   2. "ge" + stem + participle ending, no separable prefix ("gekocht",
#      "gesehen")
#   3. an inseparable prefix (``_INSEPARABLE_PREFIXES``) + stem + participle
#      ending, no "ge" at all ("verkauft", "besucht", "erklärt") -- German
#      never prefixes an ALREADY-prefixed verb's participle with a second
#      "ge-"
#
# -- and a genuine zu-infinitive has a related but different shape: the
# INFIX is "zu", not "ge" ("an" + "zu" + "braten" -> "anzubraten"). That
# infix is the one thing this check must get right, since ``VVIZU`` is also
# the CORRECT tag for a genuine fused zu-infinitive, and this module's own
# job is to add a second, independent way to be right about a participle,
# not merely a way to blanket-accept anything wearing that tag.
#
# Reuses ``src.lexicon.lemmatizer.SEPARABLE_PREFIXES`` -- this module's own
# "no new linguistic facts" posture (see the module docstring) extended to
# prefix data, exactly the precedent ``carrier_validation.py``'s own
# ``_NON_FINITE_VERB_PREFIXES`` already set for the identical "strip a
# separable prefix off a fused non-finite verb form" problem. Not a table
# this module already held under a different name -- there is no separable-
# prefix list in ``paradigms.py`` itself, only ``_INSEPARABLE_PREFIXES``
# above, which names a disjoint, unrelated closed class (be-/ver-/ent-/...,
# a prefix that never detaches) -- so this reuses the *lexicon* package's
# table rather than inventing a second, parallel one here.
# ==============================================================================

_SEPARABLE_PREFIXES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(_SEPARABLE_PREFIXES, key=len, reverse=True)
)


def _split_separable_prefix(lower: str) -> tuple[str, str] | None:
    """``(prefix, remainder)`` for the longest ``_SEPARABLE_PREFIXES`` member
    ``lower`` starts with, else ``None``. Longest-first, the same precedent
    ``_vokalwechsel_base_and_prefix`` and ``carrier_validation.py``'s own
    ``_NON_FINITE_VERB_PREFIXES`` both already set, so a short prefix that is
    itself a substring of a longer one is never preferred."""
    for prefix in _SEPARABLE_PREFIXES_BY_LENGTH:
        if lower.startswith(prefix) and len(lower) > len(prefix):
            return prefix, lower[len(prefix) :]
    return None


def _has_participle_ending(text: str) -> bool:
    return text.endswith("t") or (text.endswith("en") and len(text) > 2)


def _is_inseparable_prefixed(lower: str) -> bool:
    return any(lower.startswith(p) and len(lower) > len(p) for p in _INSEPARABLE_PREFIXES)


# The one tag this exact tagger (de_core_news_sm) is confirmed, empirically,
# to confuse with a genuine participle on a separable-prefix verb. Not a
# broader set: ``VVIZU`` is ALSO the correct tag for a genuine fused
# zu-infinitive ("aufzuladen"), so ``is_participle_shape``'s own ge/zu
# discriminator, not this set alone, is what keeps one from being mistaken
# for the other. Every other non-finite verb tag this tagger emits (VVINF,
# VAINF, VMINF, VVFIN...) was checked directly against both reported
# sentences and against the isolated forms this module's own tests exercise
# and found NOT to carry a real participle mislabelled this way -- confirmed
# a plain "zu besuchen"/"zu verkaufen" (two separate tokens, not fused) tags
# its infinitive ``VVINF``, never ``VVIZU``, so restricting to the one
# confirmed tag does not, on current evidence, leave a second confusable tag
# unhandled.
PARTICIPLE_CONFUSABLE_TAGS: frozenset[str] = frozenset({"VVIZU"})


def is_participle_shape(text: str) -> bool:
    """Whether ``text``'s own SPELLING is a German past participle, by the
    three shapes this module's own comment above sets out, independent of
    whatever tag a tagger attached to it. ``False`` for a genuine
    zu-infinitive (the "zu" infix, checked first and unconditionally, wins
    over any participle reading for that same prefix split) and for any
    other shape this module cannot confirm one way or the other -- "reject
    rather than guess", the same posture as every other lookup here."""
    lower = text.lower()
    if not lower:
        return False
    split = _split_separable_prefix(lower)
    if split is not None:
        _, remainder = split
        if remainder.startswith("zu") and len(remainder) > 2:
            return False
        if remainder.startswith("ge") and _has_participle_ending(remainder):
            return True
    if lower.startswith("ge") and _has_participle_ending(lower):
        return True
    return bool(_is_inseparable_prefixed(lower) and lower.endswith("t"))


# ``_SEPARABLE_PREFIXES`` (``src.lexicon.lemmatizer.SEPARABLE_PREFIXES``,
# reused unchanged everywhere else this module needs a prefix split) does
# not include "wieder" -- confirmed directly: "wiederzubeleben" (the exact
# TODO.md 8.6 reported sentence's own infinitive) does not split at all
# against it. "wieder" is a genuine separable verb prefix ("wiederkommen",
# "wiederfinden", and -- confirmed by the reported sentence's own usage --
# "wiederbeleben"), so this is a real gap in that table for THIS narrower
# question, not a typo to route around. Extended LOCALLY, for this one
# function only, rather than added to the shared list: the shared list also
# feeds participle-shape reconstruction and vocabulary compound-splitting
# elsewhere, both out of this task's own scope to re-verify.
_ZU_INFINITIV_PREFIXES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted({*_SEPARABLE_PREFIXES, "wieder"}, key=len, reverse=True)
)


def is_fused_zu_infinitiv_shape(text: str) -> bool:
    """Whether ``text``'s own SPELLING is a fused zu-infinitive -- a
    separable prefix with "zu" infixed before the stem ("wieder" + "zu" +
    "beleben" -> "wiederzubeleben") -- independent of whatever tag a tagger
    attached to it. Reuses the exact same infix check ``is_participle_
    shape`` already performs (see its own docstring) to REJECT a
    participle reading for this shape, exposed here as the positive fact
    for a caller that needs it: TODO.md 8.6 confirmed this exact tagger
    sometimes mistags a fused zu-infinitive as a genuine FINITE verb
    (``VVFIN``, ``VerbForm=Fin``) -- not the already-known ``VVIZU``
    confusion ``PARTICIPLE_CONFUSABLE_TAGS`` exists for -- so a caller
    checking "does this clause have a genuine finite verb" by tag alone
    needs a second, independent way to rule this shape out, exactly as
    ``is_participle_shape`` already needed one for the participle
    question.

    Uses ``_ZU_INFINITIV_PREFIXES_BY_LENGTH`` (this function's own,
    locally-extended prefix set -- see its own comment), not the shared
    ``_split_separable_prefix``."""
    lower = text.lower()
    if not lower:
        return False
    for prefix in _ZU_INFINITIV_PREFIXES_BY_LENGTH:
        if not (lower.startswith(prefix) and len(lower) > len(prefix)):
            continue
        remainder = lower[len(prefix) :]
        if remainder.startswith("zu") and len(remainder) > 2:
            return True
    return False


def _weak_participle_ending_to_infinitive(word: str) -> str | None:
    """``word`` (no "ge-", no separable prefix) with its participle ending
    reduced back to "-en", by the same dental-epenthesis rule
    ``regular_praeteritum_form`` already applies going the other way
    ("arbeiten" -> "gearbeitet", stem ends in a dental so the ending gets an
    epenthetic "-e-"). ``None`` if ``word`` does not end in a participle
    shape at all."""
    if word.endswith("et") and len(word) > 2 and word[:-2].endswith(("d", "t")):
        return word[:-2] + "en"
    if word.endswith("t") and len(word) > 1:
        return word[:-1] + "en"
    return None


def participle_shape_infinitive(text: str) -> str | None:
    """``text``'s own infinitive lemma, reconstructed from its SPELLING
    alone -- for a token ``is_participle_shape`` already accepted, used
    wherever a selector needs the participle's lemma (``TRANSITIVE_LEMMAS``/
    ``AUX_SEIN_LEMMAS`` membership) but the tagger's own ``.lemma`` is not
    trustworthy for a separable-prefix participle. ``AUX_SEIN_LEMMAS``'s own
    comment above already documents this exact unreliability ("aufgestanden"
    lemmatises to the nonsense "aufgestehen") for the tag-correct case; a
    participle only found via ``is_participle_shape`` (tag ``VVIZU``) is not
    reduced by the tagger's lemmatiser AT ALL in the two confirmed cases
    (``.lemma`` is the bare surface form, unchanged), so it needs this
    reconstruction even more, not less.

    Tries, in order: the whole word directly against
    ``PARTICIPLE_II_TO_INFINITIVE`` (covers an inseparable-prefixed or
    unprefixed strong/mixed/known-weak participle, e.g. "verloren" ->
    "verlieren"); a separable prefix split, then the same table against the
    "ge"-remainder (e.g. "aufgeladen" -> "auf" + "geladen", "geladen" ->
    "laden" -> "aufladen"); the general weak-participle reduction rule on
    that same remainder; and finally the general weak-participle reduction
    rule on the whole word for an inseparable-prefixed weak participle with
    no "ge-" at all ("besucht" -> "besuchen"). Returns ``None``, never a
    guess, for a strong/irregular participle none of this module's own
    tables already cover -- the same "reject rather than guess" posture
    ``AUX_SEIN_LEMMAS``'s own comment states for the tag-correct case."""
    lower = text.lower()
    if not lower:
        return None
    known = PARTICIPLE_II_TO_INFINITIVE.get(lower)
    if known is not None:
        return known
    split = _split_separable_prefix(lower)
    if split is not None:
        prefix, remainder = split
        if remainder.startswith("ge") and _has_participle_ending(remainder):
            known_remainder = PARTICIPLE_II_TO_INFINITIVE.get(remainder)
            if known_remainder is not None:
                return prefix + known_remainder
            base = _weak_participle_ending_to_infinitive(remainder[2:])
            return None if base is None else prefix + base
    if lower.startswith("ge") and _has_participle_ending(lower):
        return _weak_participle_ending_to_infinitive(lower[2:])
    if _is_inseparable_prefixed(lower) and lower.endswith("t"):
        return _weak_participle_ending_to_infinitive(lower)
    return None


# ==============================================================================
# Dative-reflexive verb argument structure: another lexical fact, not a
# rule, same posture as ``AUX_SEIN_LEMMAS``/``TRANSITIVE_LEMMAS`` above (this
# module's own docstring says "no new linguistic facts", and those two sets
# already established the precedent that a verb's own argument structure is
# exactly the kind of fact this cycle keeps as a closed list here rather than
# re-deriving it per sentence).
#
# TODO 8.1: kept, not deleted -- a closed list cannot keep up with German's
# hundreds of fixed-case-government verbs (three cycles of reflexive
# defects were each "fixed" by extending one of the two simple lists below,
# and each time the next corpus run found verbs neither one named), but
# Dreyer/Schmitt and Duden are a better authority than corpus frequency for
# the verbs they already cover. ``src.generation.blanking.verb_government``
# now consults ``DATIVE_REFLEXIVE_VERBS_NO_OBJECT``/``ACCUSATIVE_ONLY_
# REFLEXIVE_VERBS`` as a trusted seed that WINS on conflict, merged with a
# corpus-built lexicon (``data/fixtures/verb_government/lexicon.v1.jsonl``,
# ``scripts/build_verb_government.py``) that extends coverage far past
# these two lists. ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` is not folded
# into that lexicon at all -- its own polysemy (module comment below) is
# not a fact a single verdict can represent, so ``selectors._reflexive_
# case`` still decides those verbs the way it always has, per occurrence.
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

# TODO.md 1.1: the mirror image of ``DATIVE_REFLEXIVE_VERBS_NO_OBJECT`` --
# verbs whose reflexive object is genuinely ALWAYS Accusative, intransitive
# (no "sich (Dat) etwas VERB" reading exists for any of these the way it
# does for the ``_WITH_OBJECT`` set above), so ``selectors._reflexive_case``
# trusts this list unconditionally rather than running its own same-clause
# accusative-object scan for them at all.
#
# Added because that scan is itself a heuristic over spaCy's own ``Case``
# tags, and confirmed empirically (docs/audits/cycle-09-report.md) that the
# tagger can mistag a postposed bare-NP SUBJECT as Accusative rather than
# Nominative in exactly the word order these verbs' own reflexive
# construction produces: "..., weil sich darauf viel Staub angesammelt
# hat." tags "Staub" (the genuine Nominative subject of "sich ansammeln")
# ``Case=Acc`` regardless of the surrounding sentence, confirmed against
# several rewordings, not a one-off. The scan cannot tell that mistagged
# subject apart from a real object by Case alone; a verb known to never
# take a further object needs no such scan in the first place. "freuen",
# "treffen" and "ändern" are the cycle-4 report's own three counter-
# examples for exactly this reason (see that report's docstring above);
# "ansammeln" and "beeilen" are the same class, confirmed against this
# task's own reported items.
# TODO.md 8.1's own leftover: "verbeugen" ("sich verbeugen", to bow) is
# Accusative and was still landing in ``verben_reflexiv_dat``
# (docs/audits/cycle-10-corpus-report.md). TODO 8.1's own corpus-built
# lexicon (``verb_government.py``) could not reach it -- confirmed directly
# by grepping both staged corpora for every occurrence of "verbeugen":
# zero reach the harvester's own unambiguous-pronoun evidence at all, every
# one either under the 5-word carrier-length filter, governed by a
# preposition ("vor mir"), or sitting in an "und"-joined clause where
# ``_governing_verb_lemma`` cannot resolve a single governing verb (the
# reported item itself: "küsste" mistags ``ADJA`` in that exact position,
# an unrelated, pre-existing tagger defect this task does not touch -- see
# ``tests/test_blanking_selectors.py`` for the pinned regression). Added
# here, by name, per this task's own instruction: a single verb named by a
# hand audit, sourced the same way every other member of this list already
# is (Dreyer/Schmitt, Duden), not a guess and not the list treadmill TODO
# 8.1 itself retired -- the corpus lexicon still wins on conflict for every
# verb it DOES have evidence for; this is the documented fallback for the
# one verb it has none for at all.
ACCUSATIVE_ONLY_REFLEXIVE_VERBS: frozenset[str] = frozenset(
    {"freuen", "treffen", "ändern", "ansammeln", "beeilen", "verbeugen"}
)

# ==============================================================================
# TODO.md 8.5 (replaced by TODO 8.1's corpus lexicon -- see the module
# comment above ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` for the general
# reasoning, identical here): non-reflexive Dative-governing verbs, for
# ``kasus_dativ_formen``'s own forcing check
# (``selectors._select_kasus_dativ_formen``).
#
# ``DATIVE_ONLY_VERBS`` is kept as the trusted seed
# ``verb_government.object_verdict`` merges corpus evidence into, winning
# on conflict; ``DITRANSITIVE_DATIVE_VERBS`` is not folded into that
# lexicon's binary verdict at all and stays consulted directly, unchanged
# -- a ditransitive verb's own object-pronoun evidence is genuinely mixed
# by construction (the recipient is Dative, the theme is sometimes itself
# an Accusative personal pronoun -- "Ich gebe ihn dir."), so it correctly
# never clears the lexicon's own ratio bar, which is exactly why this list
# needs its accompanying-Accusative-object confirmation and always will.
#
# docs/audits/cycle-10-corpus-report.md found three items in this topic
# that are not Dative at all -- a Nominative apposition ("einer nach dem
# anderen"), an Accusative direct object ("den Murks gelesen") and a
# Genitive noun complement ("Schlagzeuger der Band") -- every one of them
# tagged ``Case=Dat`` by the tagger regardless. docs/audits/tagger-
# accuracy-vs-gold.md measured Case as 6.08 percent conflicting and a
# further 20.26 percent absent on this exact tagger, so the tag alone is
# not evidence; the Dative reading has to be FORCED by the clause's own
# structure, the same "never gate on a tag when the fact is derivable from
# structure" remedy TODO.md 8's own preamble already names for this cycle.
#
# Two closed lists, the same kind of lexical fact ``DATIVE_REFLEXIVE_
# VERBS_*``/``AUX_SEIN_LEMMAS``/``TRANSITIVE_LEMMAS`` already are, not
# re-derived per sentence:
#
# * ``DATIVE_ONLY_VERBS`` -- verbs that take a Dative object and NEVER
#   an Accusative one, so the mere fact that this verb governs the
#   clause's own noun phrase is already proof the reading is Dative,
#   with no further object check needed. Sourced from the standard German
#   pedagogical "Verben mit Dativ" class (Dreyer/Schmitt, "Praktische
#   Grammatik der deutschen Sprache"; Duden), the same source
#   ``DATIVE_REFLEXIVE_VERBS_WITH_OBJECT``'s own comment cites. "antworen"
#   is not a spelling variant of "antworten" -- it is this exact tagger's
#   own confirmed lemma for "antworte"/"antworten" (dropping the medial
#   "t"; "antwortet" lemmatises correctly to "antworten", the 1st-person/
#   plural forms do not), kept alongside the correct spelling rather than
#   left for a future reader to rediscover, the same "confirmed necessary,
#   not just theoretical" posture TODO 1.2's own precedent set for a
#   different tagger quirk.
#
#   TODO.md 1.6: "anpassen" ("Der Körper passt sich schnell
#   Temperaturänderungen an.") added by name, on the same basis as
#   "verbeugen" above (a single verb named by a hand audit, not the list
#   treadmill TODO 8.1 retired) -- fixing ``selectors._governing_verb_
#   lemma``'s own unreduced-lemma bug (this same TODO item; see that
#   function's docstring) makes this verb correctly REACHABLE by the
#   corpus lexicon for the first time, but its own corpus evidence stays
#   at 1 occurrence even after a full rebuild over both staged corpora --
#   confirmed directly, not assumed, by actually rebuilding
#   ``data/fixtures/verb_government/lexicon.v1.jsonl`` and diffing before
#   and after: nowhere near ``build_verb_government.DEFAULT_MIN_COUNT``
#   (5). "sich (Akk) etwas (Dat) anpassen" (adapt oneself to something) is
#   a standard, Duden-attested dative-object construction. Unlike
#   "verbeugen", "anpassen" is NOT single-sense -- it also has a plain
#   transitive Accusative reading with no reflexive pronoun at all ("Sie
#   passt Verträge an."), so this entry is not as clean a fit for "never
#   an Accusative object" as this list's other members, and is accepted
#   here on the strength of the case TAG rather than verb polysemy alone:
#   every constructed test of the transitive sense tags its own bare
#   plural object ``Case=Acc`` correctly (``Sie passt Verträge an.``, ``Die
#   Firma passt Preise an.``, ``Wir passen Löhne an.``, ...), so this
#   list's own membership is never actually reached for that sense unless
#   the tagger ALSO mistags the object's Case -- a real but unconfirmed
#   residual risk, flagged here rather than silently accepted, not
#   evidence the addition is wrong.
# * ``DITRANSITIVE_DATIVE_VERBS`` -- verbs that take BOTH a Dative
#   (indirect object/recipient) and an Accusative (direct object) at once
#   ("geben", "zeigen", "sagen"...). Governing one of these is only forced
#   evidence of the DATIVE reading specifically when a genuine Accusative
#   direct object is ALSO present in the same clause (checked via
#   ``_has_bare_accusative_object``, the same same-clause object scan the
#   reflexive-case-routing fix already uses) -- the clause's own dative
#   NP is otherwise indistinguishable, by this module's own closed-class
#   evidence, from any other noun phrase the verb happens to be near.
DATIVE_ONLY_VERBS: frozenset[str] = frozenset(
    {
        "antworten",
        "antworen",
        "anpassen",
        "ähneln",
        "auffallen",
        "begegnen",
        "beistehen",
        "danken",
        "dienen",
        "drohen",
        "einfallen",
        "entgegenkommen",
        "entkommen",
        "entsprechen",
        "fehlen",
        "folgen",
        "gebühren",
        "gefallen",
        "gehorchen",
        "gehören",
        "gelingen",
        "genügen",
        "gratulieren",
        "helfen",
        "imponieren",
        "misslingen",
        "missfallen",
        "nützen",
        "passen",
        "schaden",
        "schmecken",
        "unterliegen",
        "vertrauen",
        "widersprechen",
        "widerstehen",
        "zuhören",
        "zusehen",
        "zustimmen",
    }
)
DITRANSITIVE_DATIVE_VERBS: frozenset[str] = frozenset(
    {
        "anbieten",
        "beibringen",
        "bringen",
        "empfehlen",
        "erklären",
        "erlauben",
        "erzählen",
        "geben",
        "leihen",
        "mitteilen",
        "sagen",
        "schenken",
        "schicken",
        "schreiben",
        "senden",
        "überreichen",
        "verbieten",
        "verkaufen",
        "versprechen",
        "verzeihen",
        "wünschen",
        "zeigen",
    }
)

# ==============================================================================
# Tense anchoring (docs/audits/cycle-06-report.md's next-cycle task): a bare
# ``sein``/``haben``/``werden`` finite form ("ist"/"war", "hat"/"hatte",
# "wird"/"wurde") is grammatical at EITHER tense in an ordinary sentence with
# nothing else to decide which one is meant -- unlike a modal or a plural
# noun (``uniqueness.py``'s existing policy table), the ambiguity here is not
# over which LEXEME fills the slot, it is over which CELL of the same
# lexeme's own paradigm does, so a citation-form cue (which only ever names a
# lexeme) cannot rescue it the way it rescues those two kinds -- the same
# argument ``uniqueness.py``'s own ``personal_pronoun`` paragraph already
# makes for the identical reason, one part of speech over.
#
# ``TEMPORAL_ANCHOR_LEMMAS`` is the closed list of unambiguous German time
# expressions ``scripts/check_gold_examples.py`` already built and verified
# for exactly this purpose (its own D6 forcing-element check for the
# ``temporal_anchor`` kind) -- moved here, the one place this cycle's
# linguistic data lives, so ``uniqueness.py`` can reuse the identical list
# rather than redeclaring it, and ``check_gold_examples.py`` now imports it
# instead of keeping its own copy. Deliberately limited to words that are
# ALWAYS temporal, never also some other part of speech, so a hit can never
# be a false positive from an unrelated word elsewhere in the sentence.
TEMPORAL_ANCHOR_LEMMAS: frozenset[str] = frozenset(
    {
        "gestern",
        "heute",
        "morgen",
        "übermorgen",
        "vorgestern",
        "jetzt",
        "bald",
        "später",
        "damals",
        "früher",
        "demnächst",
        "künftig",
        "montag",
        "dienstag",
        "mittwoch",
        "donnerstag",
        "freitag",
        "samstag",
        "sonntag",
        "woche",
        "monat",
        "jahr",
        "nächste",
        "nächsten",
        "nächstes",
        "letzte",
        "letzten",
        "letztes",
        "vorher",
        "nachher",
        "anschließend",
        "zuvor",
        "danach",
        "sofort",
        "gleich",
        "nachdem",
        "bevor",
        "während",
        "als",
        "wenn",
        "sobald",
    }
)

# Subordinating conjunctions whose clause conventionally shares the MATRIX
# clause's own tense when both describe a concurrent state or reason, rather
# than an independent time reference of their own -- the mechanism behind
# docs/audits/cycle-06-report.md's task 1 worked example: "Obwohl die neuen
# Vorschriften sehr streng sind, bitten wir um Ihr Verständnis." forces "sind"
# (not "waren") because the "obwohl" clause describes the SAME present state
# the present-tense matrix clause ("bitten wir") is talking about, not a
# separate past event.
#
# Deliberately narrow and NOT the same list as ``TEMPORAL_ANCHOR_LEMMAS``'s
# own "nachdem"/"bevor"/"als"/"wenn" entries, which are excluded here on
# purpose: "nachdem"/"bevor" introduce an explicit ANTERIORITY relation
# (Plusquamperfekt's own forcing element), a *different* tense from the
# matrix by definition, not the same one; "als" is reserved for a single
# past narrative event and does not license a present reading either way;
# and "wenn" is genuinely polysemous between a tense-concordant habitual/
# temporal reading and a conditional one this module cannot mechanically
# tell apart from the tokens alone. Every entry actually kept here was
# checked individually against the "does the subordinate clause describe
# the SAME state/time as the matrix, in ordinary usage" question, not
# assumed from the general "subordinating conjunction" category.
TENSE_CONCORDANT_SUBORDINATORS: frozenset[str] = frozenset({"obwohl", "weil", "während", "da"})

# ==============================================================================
# docs/audits/cycle-07-report.md defect 1: cue round-trip for a plural-noun
# cue. "Tablett" (tray) pluralises to "Tabletts", never "Tabletten" --
# "Tabletten" is the plural of the unrelated lemma "Tablette" (pill). The
# tagger's own lemmatiser strips a final "-e" indiscriminately, so it
# produced "Tablett" from "Tabletten" -- a real German word, so the cue
# dictionary check (``selectors._cue_is_real_word``) cannot see anything
# wrong with it; only checking whether "Tablett" actually PLURALISES TO the
# observed surface form catches this.
#
# German plural formation genuinely has no single rule (this module's own
# "do not invent paradigm data" standard, and ``_select_nomen_plural``'s own
# docstring), so this is not a predictor of THE correct plural -- it is a
# permissive SET of every standard plural-suffix CLASS a German noun might
# belong to, and the caller (``selectors._plural_noun_cue``) only trusts an
# EXACT match against the token actually observed, never a guess at which
# class applies.
#
# The "-(e)n" weak-noun class ("Student" -> "Studenten", "Nachbar" ->
# "Nachbarn", and the whole productive "-heit/-keit/-ung/-schaft" feminine
# suffix class, e.g. "Kleinigkeit" -> "Kleinigkeiten", "Landschaft" ->
# "Landschaften") is common enough that withholding it for every
# consonant-final lemma was measured, not assumed, to be a materially worse
# trade than the one line above claims: run against
# ``sentence_source._MOCK_SENTENCE_POOL`` through the real pipeline, an
# earlier version of this function that omitted "-(e)n" entirely silently
# dropped "Bäume", "Kleinigkeiten", "Landschaften", "Nachbarn" (twice) and
# "Studenten" from ``nomen_plural`` -- not merely "loses a cue", since an
# uncued ``nomen_plural`` candidate is always flagged ambiguous by
# ``uniqueness.py`` and so the whole item is skipped, not merely uncued.
# That is a large, common, productive plural class to disable outright to
# close one lemmatiser bug on one specific loanword.
#
# The orthographic feature that actually distinguishes "Tablett" (a loanword
# that pluralises "-s", never "-en") from "Student"/"Nachbar"/"Kleinigkeit"/
# "Landschaft" is a doubled final consonant letter ("tt"): German nouns
# ending in a geminate consonant are overwhelmingly the "-s"-plural loanword
# class (also "Ticket" -> "Tickets", "Etikett" -> "Etiketts"), not the
# "-(e)n" weak/abstract-suffix class, so "-(e)n" is withheld only for a
# lemma ending in a doubled consonant letter. This is still a heuristic, not
# a rule -- a handful of genuine "-(e)n" nouns do end in a doubled
# consonant ("Bett" -> "Betten") and lose their cue under it -- but losing a
# cue is always the safe direction here (a lost cue is a skip, never a
# wrong accept), and it closes the confirmed "Tablett" + "en" ==
# "Tabletten" defect while no longer discarding the much larger regular
# class.
_NOUN_PLURAL_SUFFIXES: tuple[str, ...] = ("e", "er", "s")


def _ends_in_geminate_consonant(lemma: str) -> bool:
    """Whether ``lemma`` ends in the same consonant letter twice ("Tablett",
    "Ticket" is NOT geminate and is unaffected either way, "Etikett",
    "Ball") -- see ``_NOUN_PLURAL_SUFFIXES``'s own comment for why this is
    the signal used to withhold the "-(e)n" plural class."""
    if len(lemma) < 2:
        return False
    last, second_last = lemma[-1].lower(), lemma[-2].lower()
    return last == second_last and last not in "aeiouäöü"


#: Forward umlaut table -- the reverse of ``lemmatizer._UMLAUT_REVERSE``,
#: used only to generate one extra permissive candidate per lemma (never to
#: assert which vowel actually umlauts for a given noun, which is itself a
#: per-lemma fact this module does not have a table for).
_UMLAUT_FORWARD: dict[str, str] = {"a": "ä", "o": "ö", "u": "ü"}

#: "au" is a diphthong that umlauts as a UNIT ("Baum" -> "Bäum-", "Haus" ->
#: "Häus-"), not by replacing its final "u" alone ("Baum" -> "Baüm" is not a
#: real German spelling) -- checked before the single-vowel table below so
#: the diphthong match wins.
_UMLAUT_DIPHTHONGS: dict[str, str] = {"au": "äu"}


def _umlaut_last_vowel(stem: str) -> str | None:
    """``stem`` with its LAST umlaut-able vowel or diphthong replaced by its
    umlaut equivalent, or ``None`` if it contains none -- a best-effort
    proxy for where German umlaut plurals actually umlaut (the stem vowel
    nearest the ending), offered only as one extra candidate, never
    asserted."""
    for i in range(len(stem) - 1, -1, -1):
        if i >= 1:
            pair = stem[i - 1 : i + 1]
            if pair in _UMLAUT_DIPHTHONGS:
                return stem[: i - 1] + _UMLAUT_DIPHTHONGS[pair] + stem[i + 1 :]
        if stem[i] in _UMLAUT_FORWARD:
            return stem[:i] + _UMLAUT_FORWARD[stem[i]] + stem[i + 1 :]
    return None


def plausible_noun_plurals(lemma: str) -> frozenset[str]:
    """A permissive set of surface forms ``lemma`` might plausibly
    pluralise to, built from the standard German plural-suffix classes this
    module's own docstring says have no single deciding rule -- see this
    section's own comment for exactly which class is deliberately withheld,
    and why. The caller trusts only an EXACT match against the token
    actually observed in the carrier; this function never claims one
    candidate is THE correct plural."""
    out = {lemma, *(lemma + suffix for suffix in _NOUN_PLURAL_SUFFIXES)}
    if lemma.endswith("e"):
        out.add(lemma + "n")
    elif not _ends_in_geminate_consonant(lemma):
        # The "-(e)n" weak/abstract-suffix class for a lemma NOT already
        # ending in "-e" -- both possible endings ("Student" -> "-en",
        # "Nachbar" -> "-n") are offered; see this section's own comment
        # for why the geminate-consonant exclusion, not a blanket ban, is
        # what keeps "Tablett" + "en" out of this set.
        out.add(lemma + "en")
        out.add(lemma + "n")
    umlauted = _umlaut_last_vowel(lemma)
    if umlauted is not None:
        out.add(umlauted)
        out.update(umlauted + suffix for suffix in _NOUN_PLURAL_SUFFIXES)
        if umlauted.endswith("e"):
            out.add(umlauted + "n")
        elif not _ends_in_geminate_consonant(umlauted):
            out.add(umlauted + "en")
            out.add(umlauted + "n")
    return frozenset(out)


#: Adverbs and adjectives that fuse with a verb into a separable verb the
#: dictionaries list ("offen|stehen", "zugute|kommen", "kennen|lernen").
#: A tagger ``svp`` particle outside this set and outside
#: ``SEPARABLE_PREFIXES`` is a parser artefact ("wie|wissen",
#: "stark|variieren") unless the fused form is itself a dictionary word.
KNOWN_PARTICLES: frozenset[str] = frozenset(
    {
        "abwärts",
        "auseinander",
        "aufwärts",
        "bekannt",
        "bereit",
        "dar",
        "davon",
        "dazu",
        "durch",
        "empor",
        "entgegen",
        "entlang",
        "fehl",
        "fern",
        "fort",
        "frei",
        "gegenüber",
        "gleich",
        "gut",
        "heim",
        "herab",
        "heran",
        "herauf",
        "heraus",
        "herbei",
        "herein",
        "herüber",
        "herum",
        "herunter",
        "hervor",
        "hierher",
        "hinab",
        "hinauf",
        "hinaus",
        "hindurch",
        "hinein",
        "hinterher",
        "hinüber",
        "hinunter",
        "hinweg",
        "hinzu",
        "hoch",
        "inne",
        "instand",
        "kaputt",
        "kennen",
        "klar",
        "leer",
        "nahe",
        "nieder",
        "offen",
        "preis",
        "ran",
        "raus",
        "rein",
        "rüber",
        "rum",
        "runter",
        "schwer",
        "sicher",
        "statt",
        "still",
        "teil",
        "tot",
        "über",
        "übrig",
        "um",
        "umher",
        "unter",
        "verloren",
        "voran",
        "voraus",
        "vorbei",
        "vorüber",
        "vorwärts",
        "wahr",
        "wider",
        "wieder",
        "zugrunde",
        "zugute",
        "zurecht",
        "zustande",
        "zuteil",
        "zuvor",
        "zuwider",
    }
)

#: Preterite stems of strong verbs the tagger leaves as their own lemma
#: ("ankamen", "aufwuchsen", "rochen"), keyed without "ge", so a prefixed
#: form resolves through one lookup like the participle table.
PRAETERITUM_STEMS: dict[str, str] = {
    "band": "binden",
    "bat": "bitten",
    "begann": "beginnen",
    "bewog": "bewegen",
    "bog": "biegen",
    "bot": "bieten",
    "brach": "brechen",
    "fand": "finden",
    "fiel": "fallen",
    "fing": "fangen",
    "flog": "fliegen",
    "floss": "fließen",
    "fuhr": "fahren",
    "gab": "geben",
    "galt": "gelten",
    "gewann": "gewinnen",
    "ging": "gehen",
    "glitt": "gleiten",
    "goss": "gießen",
    "griff": "greifen",
    "half": "helfen",
    "hielt": "halten",
    "hieß": "heißen",
    "hing": "hängen",
    "hob": "heben",
    "kam": "kommen",
    "lag": "liegen",
    "las": "lesen",
    "lief": "laufen",
    "ließ": "lassen",
    "litt": "leiden",
    "log": "lügen",
    "lud": "laden",
    "mied": "meiden",
    "nahm": "nehmen",
    "pfiff": "pfeifen",
    "rann": "rinnen",
    "rieb": "reiben",
    "rief": "rufen",
    "riss": "reißen",
    "ritt": "reiten",
    "roch": "riechen",
    "sah": "sehen",
    "sang": "singen",
    "sank": "sinken",
    "saß": "sitzen",
    "schied": "scheiden",
    "schien": "scheinen",
    "schlief": "schlafen",
    "schlug": "schlagen",
    "schloss": "schließen",
    "schnitt": "schneiden",
    "schob": "schieben",
    "schoss": "schießen",
    "schrieb": "schreiben",
    "schrie": "schreien",
    "schwamm": "schwimmen",
    "schwieg": "schweigen",
    "sprach": "sprechen",
    "sprang": "springen",
    "stach": "stechen",
    "stahl": "stehlen",
    "stand": "stehen",
    "starb": "sterben",
    "stieg": "steigen",
    "stieß": "stoßen",
    "strich": "streichen",
    "stritt": "streiten",
    "traf": "treffen",
    "trank": "trinken",
    "trat": "treten",
    "trieb": "treiben",
    "trug": "tragen",
    "tat": "tun",
    "verlor": "verlieren",
    "warf": "werfen",
    "wich": "weichen",
    "wies": "weisen",
    "wog": "wiegen",
    "wuchs": "wachsen",
    "wusch": "waschen",
    "zog": "ziehen",
    "zwang": "zwingen",
}
