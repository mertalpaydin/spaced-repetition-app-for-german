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

from src.taxonomy.facets import (
    _ADJ_ENDING_MIXED,
    _ADJ_ENDING_STRONG,
    _ADJ_ENDING_WEAK,
    _DEFINITE_ARTICLE_PARADIGM,
    _EIN_ENDING_PARADIGM,
    _EIN_WORD_STEMS,
)

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
