"""Derive facet spaces and concrete item facets from a topic's ``morph_spec``.

``01-foundation.md:168`` specifies that ``morph_spec`` does double duty: beyond
gating the stage 4 morphology check, the features it *leaves unspecified* are
exactly the dimensions a topic varies over ("facets"). A topic fixing
``{"Case": "Dat"}`` varies over Gender, Number and so on, so those form its
facet space. Facets are therefore always derived from the topic, never
hand-written.

This module has two responsibilities:

1. ``facet_space(topic)`` -- which UD feature dimensions a topic's items vary
   over, derived from ``morph_spec`` (what it fixes) and ``syntax_tags``
   (which part of speech the topic targets, restricting which dimensions are
   even meaningful for that word class).
2. ``derive_facet(item, topic)`` -- the concrete facet string an ingested
   item exhibits, decoded deterministically from
   ``item.accepted_answers[0]`` using small, explicit lookup tables of closed
   German word classes (articles, personal/reflexive pronouns, common finite
   verb endings). No spaCy, no guessing: a dimension we cannot pin down from
   the closed-class table is encoded literally as ``"Unk"``.

### The German-relevant UD feature universe

``GERMAN_UD_FEATURE_UNIVERSE`` is the full set of Universal Dependencies FEATS
keys this taxonomy considers meaningful for German: ``Case, Gender, Number,
Person, Tense, Mood, Voice, Aspect, Degree, Definite, PronType, Reflex,
VerbForm, Polarity``. This is also the allowed key set for ``morph_spec``
(enforced by ``test_morph_spec_keys_are_valid_universal_dependencies_features``
in ``tests/test_taxonomy.py``).

### Per-part-of-speech scoping

Returning all 14 dimensions minus whatever ``morph_spec`` fixes would be
meaningless: a preposition topic does not "vary over Mood", and a verb topic
does not "vary over Definite". ``_POS_CATEGORY_UNIVERSE`` restricts the
universe to the dimensions that can actually vary for a topic's word class,
determined primarily from the migrated ``syntax_tags["Pos"]`` value (and, for
topics that predate a ``Pos`` tag, from other ``syntax_tags`` markers or from
which UD keys already appear in ``morph_spec``). The mapping, documented
alongside the constant below, is:

| category | trigger                                    | facet dimensions        |
|----------|---------------------------------------------|--------------------------|
| Prep     | ``Pos == "Prep"``                            | Definite, Gender, Number |
| Pron     | ``Pos == "Pron"``                            | Gender, Number, Person   |
| Adj      | ``Pos == "Adj"`` or ``Degree`` fixed         | Case, Gender, Number     |
| Adv      | ``Pos == "Adv"``                             | (none, adverbs don't decline) |
| Part     | ``Pos == "Part"``                            | (none, particles don't decline) |
| AdjDecl  | ``Declension`` tag present                   | Case, Gender, Number     |
| Det      | ``ArtType`` tag present                      | Gender, Number           |
| ReflVerb | ``Reflex`` fixed in morph_spec               | Person, Number           |
| Verb     | Tense/Mood/Voice/Aspect fixed, or VerbType   | Person, Number           |
| Noun     | ``Number`` fixed, ``Case`` unfixed           | Case, Gender             |
| Nominal  | ``Case`` fixed, none of the above            | Gender, Number           |
| Default  | none of the above (unused today)             | Case, Gender, Number, Person |

A topic with an empty ``morph_spec`` returns an empty facet space
unconditionally and short-circuits classification entirely: it has no
facets, explicitly, per ``01-foundation.md:170``.

### Facet encoding

``derive_facet`` encodes the concrete values as
``"Dim1=Value1|Dim2=Value2"``, dimensions sorted alphabetically so the string
is independent of iteration order. It returns ``None`` if and only if
``facet_space(topic)`` is empty.

### Honesty limits

The decoding tables below are deliberately small and closed-class. Where a
dimension genuinely cannot be pinned down from the answer surface form alone
(a classic case: German "die" is simultaneously Nom/Akk feminine singular
*and* Nom/Akk plural for every gender), the table records every candidate and
the resolver emits ``"Unk"`` for any dimension the candidates disagree on,
rather than guessing. Adjective attributive endings (comparative/participial)
are not decoded at all -- German adjective endings are so densely
syncretic (``-en`` alone spans most case/gender/number cells) that a
"small, honest" table cannot do better than ``Unk`` for every cell, so that
is exactly what it returns. Noun gender is lexical, not inflectional, so it
is only resolved for a small fixed list of common nouns; everything else is
``Unk``.
"""

from __future__ import annotations

from src.contracts import BankItem, Topic

# ==============================================================================
# The German-relevant UD feature universe
# ==============================================================================

GERMAN_UD_FEATURE_UNIVERSE: tuple[str, ...] = (
    "Case",
    "Gender",
    "Number",
    "Person",
    "Tense",
    "Mood",
    "Voice",
    "Aspect",
    "Degree",
    "Definite",
    "PronType",
    "Reflex",
    "VerbForm",
    "Polarity",
)

# Approximate size of each dimension's value inventory in German, used only to
# demonstrate that a non-empty facet space yields at least two distinct
# possible facet values (the promotion rule's requirement). Not a claim of
# UD-wide completeness, just "how many German values does this dimension
# realistically take".
VALUE_CARDINALITY: dict[str, int] = {
    "Case": 4,  # Nom, Acc, Dat, Gen
    "Gender": 3,  # Masc, Fem, Neut
    "Number": 2,  # Sing, Plur
    "Person": 3,  # 1, 2, 3
    "Tense": 4,  # Pres, Past, FutI, FutII (PastPerf folded in)
    "Mood": 3,  # Ind, Imp, Sub
    "Voice": 2,  # Act, Pass
    "Aspect": 2,  # Perf, Prog
    "Degree": 3,  # Pos, Cmp, Sup
    "Definite": 2,  # Def, Ind
    "PronType": 2,  # Prs, Rel (this taxonomy only ever needs these two)
    "Reflex": 2,  # Yes, No
    "VerbForm": 2,  # Fin, Part
    "Polarity": 2,  # Pos, Neg
}

# Unknown / undecidable placeholder. Never a guess.
UNK = "Unk"

# ==============================================================================
# Part-of-speech scoped facet universes
# ==============================================================================

_POS_CATEGORY_UNIVERSE: dict[str, tuple[str, ...]] = {
    "Prep": ("Definite", "Gender", "Number"),
    "Pron": ("Gender", "Number", "Person"),
    "Adj": ("Case", "Gender", "Number"),
    "AdjDecl": ("Case", "Gender", "Number"),
    "Adv": (),
    "Part": (),
    "Det": ("Gender", "Number"),
    "ReflVerb": ("Person", "Number"),
    "Verb": ("Person", "Number"),
    "Noun": ("Case", "Gender"),
    "Nominal": ("Gender", "Number"),
    "Default": ("Case", "Gender", "Number", "Person"),
}

_VERB_MORPH_KEYS = ("Tense", "Mood", "Voice", "Aspect")


def _pos_category(topic: Topic) -> str:
    """Classify a topic into one of ``_POS_CATEGORY_UNIVERSE``'s buckets.

    See the module docstring's table for the full mapping and rationale.
    """
    tags = topic.syntax_tags
    morph = topic.morph_spec or {}
    pos = tags.get("Pos")

    if pos in _POS_CATEGORY_UNIVERSE:
        return pos

    if tags.get("Declension"):
        return "AdjDecl"
    if tags.get("ArtType"):
        return "Det"
    if "Reflex" in morph:
        return "ReflVerb"
    if any(k in morph for k in _VERB_MORPH_KEYS) or tags.get("VerbType"):
        return "Verb"
    if "Number" in morph and "Case" not in morph:
        return "Noun"
    if "Degree" in morph:
        return "Adj"
    if "Case" in morph:
        return "Nominal"
    return "Default"


def facet_space(topic: Topic) -> tuple[str, ...]:
    """The UD feature dimensions ``topic``'s items vary over.

    Universe minus whatever ``topic.morph_spec`` fixes, restricted to the
    dimensions meaningful for the topic's part of speech (see module
    docstring). A topic with an empty ``morph_spec`` has no facets and
    returns ``()`` explicitly -- this is the stage 6 promotion-rule
    degradation called out in ``01-foundation.md:170``, made unconditional
    and unambiguous here rather than left to a downstream truthiness check.
    """
    if not topic.morph_spec:
        return ()

    category = _pos_category(topic)
    universe = _POS_CATEGORY_UNIVERSE.get(category, _POS_CATEGORY_UNIVERSE["Default"])
    fixed = set(topic.morph_spec.keys())
    return tuple(dim for dim in GERMAN_UD_FEATURE_UNIVERSE if dim in universe and dim not in fixed)


# ==============================================================================
# Closed-class decoding tables
#
# Each table lists every surface form this taxonomy's item bank is expected
# to produce for that closed class, together with every (dimension, value)
# combination the form is grammatically ambiguous between. The resolver
# below only commits to a value when every candidate agrees; otherwise it
# reports "Unk" for that dimension rather than guessing.
# ==============================================================================

# Definite article: full paradigm, Case x Gender x Number -> form.
# Deliberately includes every genuine German ambiguity (e.g. "die" is Nom/Akk
# Fem Sg *and* Nom/Akk Plur of any gender; "der" is Nom Masc Sg, Dat Fem Sg,
# and Gen Fem Sg/Gen Plur of any gender).
_DEFINITE_ARTICLE_PARADIGM: tuple[tuple[str, str, str, str], ...] = (
    # (form, Case, Gender, Number)
    ("der", "Nom", "Masc", "Sing"),
    ("der", "Dat", "Fem", "Sing"),
    ("der", "Gen", "Fem", "Sing"),
    ("der", "Gen", "Masc", "Plur"),
    ("der", "Gen", "Fem", "Plur"),
    ("der", "Gen", "Neut", "Plur"),
    ("die", "Nom", "Fem", "Sing"),
    ("die", "Acc", "Fem", "Sing"),
    ("die", "Nom", "Masc", "Plur"),
    ("die", "Nom", "Fem", "Plur"),
    ("die", "Nom", "Neut", "Plur"),
    ("die", "Acc", "Masc", "Plur"),
    ("die", "Acc", "Fem", "Plur"),
    ("die", "Acc", "Neut", "Plur"),
    ("das", "Nom", "Neut", "Sing"),
    ("das", "Acc", "Neut", "Sing"),
    ("den", "Acc", "Masc", "Sing"),
    ("den", "Dat", "Masc", "Plur"),
    ("den", "Dat", "Fem", "Plur"),
    ("den", "Dat", "Neut", "Plur"),
    ("dem", "Dat", "Masc", "Sing"),
    ("dem", "Dat", "Neut", "Sing"),
    ("des", "Gen", "Masc", "Sing"),
    ("des", "Gen", "Neut", "Sing"),
)

# ein-words (ein/kein/mein/dein/sein/ihr/unser): the "mixed declension"
# ending pattern, keyed by ending only -- the stem carries no case/gender
# information the ending doesn't already carry. "ein" itself has no plural.
_EIN_WORD_STEMS: tuple[str, ...] = ("kein", "mein", "dein", "sein", "ihr", "unser", "ein")
_EIN_ENDING_PARADIGM: tuple[tuple[str, str, str, str], ...] = (
    # (ending, Case, Gender, Number)
    ("", "Nom", "Masc", "Sing"),
    ("", "Nom", "Neut", "Sing"),
    ("", "Acc", "Neut", "Sing"),
    ("e", "Nom", "Fem", "Sing"),
    ("e", "Acc", "Fem", "Sing"),
    ("e", "Nom", "Masc", "Plur"),
    ("e", "Nom", "Fem", "Plur"),
    ("e", "Nom", "Neut", "Plur"),
    ("e", "Acc", "Masc", "Plur"),
    ("e", "Acc", "Fem", "Plur"),
    ("e", "Acc", "Neut", "Plur"),
    ("en", "Acc", "Masc", "Sing"),
    ("en", "Dat", "Masc", "Plur"),
    ("en", "Dat", "Fem", "Plur"),
    ("en", "Dat", "Neut", "Plur"),
    ("em", "Dat", "Masc", "Sing"),
    ("em", "Dat", "Neut", "Sing"),
    ("er", "Dat", "Fem", "Sing"),
    ("er", "Gen", "Fem", "Sing"),
    ("er", "Gen", "Masc", "Plur"),
    ("er", "Gen", "Fem", "Plur"),
    ("er", "Gen", "Neut", "Plur"),
    ("es", "Gen", "Masc", "Sing"),
    ("es", "Gen", "Neut", "Sing"),
)

# Personal pronoun, Case x Person x Number x Gender (3rd Sg only) -> form.
_PERSONAL_PRONOUN_PARADIGM: tuple[tuple[str, str, str, str, str], ...] = (
    # (form, Case, Person, Number, Gender)
    ("ich", "Nom", "1", "Sing", UNK),
    ("du", "Nom", "2", "Sing", UNK),
    ("er", "Nom", "3", "Sing", "Masc"),
    ("sie", "Nom", "3", "Sing", "Fem"),
    ("sie", "Nom", "3", "Plur", UNK),
    ("es", "Nom", "3", "Sing", "Neut"),
    ("wir", "Nom", "1", "Plur", UNK),
    ("ihr", "Nom", "2", "Plur", UNK),
    ("mich", "Acc", "1", "Sing", UNK),
    ("dich", "Acc", "2", "Sing", UNK),
    ("ihn", "Acc", "3", "Sing", "Masc"),
    ("sie", "Acc", "3", "Sing", "Fem"),
    ("sie", "Acc", "3", "Plur", UNK),
    ("es", "Acc", "3", "Sing", "Neut"),
    ("uns", "Acc", "1", "Plur", UNK),
    ("euch", "Acc", "2", "Plur", UNK),
    ("mir", "Dat", "1", "Sing", UNK),
    ("dir", "Dat", "2", "Sing", UNK),
    ("ihm", "Dat", "3", "Sing", "Masc"),
    ("ihm", "Dat", "3", "Sing", "Neut"),
    ("ihr", "Dat", "3", "Sing", "Fem"),
    ("uns", "Dat", "1", "Plur", UNK),
    ("euch", "Dat", "2", "Plur", UNK),
    ("ihnen", "Dat", "3", "Plur", UNK),
)

# Reflexive pronoun, Case x Person x Number -> form.
_REFLEXIVE_PARADIGM: tuple[tuple[str, str, str, str], ...] = (
    # (form, Case, Person, Number)
    ("mich", "Acc", "1", "Sing"),
    ("dich", "Acc", "2", "Sing"),
    ("sich", "Acc", "3", "Sing"),
    ("sich", "Acc", "3", "Plur"),
    ("uns", "Acc", "1", "Plur"),
    ("euch", "Acc", "2", "Plur"),
    ("mir", "Dat", "1", "Sing"),
    ("dir", "Dat", "2", "Sing"),
    ("sich", "Dat", "3", "Sing"),
    ("sich", "Dat", "3", "Plur"),
    ("uns", "Dat", "1", "Plur"),
    ("euch", "Dat", "2", "Plur"),
)

# Finite verb forms: irregular high-frequency forms enumerated directly
# (sein, haben, the modal verbs, and the two most common Konjunktiv II
# forms), everything else resolved from the regular weak-verb ending.
_IRREGULAR_VERB_FORMS: dict[str, tuple[tuple[str, str], ...]] = {
    "bin": (("1", "Sing"),),
    "bist": (("2", "Sing"),),
    "ist": (("3", "Sing"),),
    "sind": (("1", "Plur"), ("3", "Plur")),
    "seid": (("2", "Plur"),),
    "habe": (("1", "Sing"),),
    "hast": (("2", "Sing"),),
    "hat": (("3", "Sing"),),
    "haben": (("1", "Plur"), ("3", "Plur")),
    "habt": (("2", "Plur"),),
    "wäre": (("1", "Sing"), ("3", "Sing")),
    "wärst": (("2", "Sing"),),
    "wären": (("1", "Plur"), ("3", "Plur")),
    "wärt": (("2", "Plur"),),
    "hätte": (("1", "Sing"), ("3", "Sing")),
    "hättest": (("2", "Sing"),),
    "hätten": (("1", "Plur"), ("3", "Plur")),
    "hättet": (("2", "Plur"),),
    "kann": (("1", "Sing"), ("3", "Sing")),
    "kannst": (("2", "Sing"),),
    "können": (("1", "Plur"), ("3", "Plur")),
    "könnt": (("2", "Plur"),),
    "muss": (("1", "Sing"), ("3", "Sing")),
    "musst": (("2", "Sing"),),
    "müssen": (("1", "Plur"), ("3", "Plur")),
    "müsst": (("2", "Plur"),),
    "will": (("1", "Sing"), ("3", "Sing")),
    "willst": (("2", "Sing"),),
    "wollen": (("1", "Plur"), ("3", "Plur")),
    "wollt": (("2", "Plur"),),
    "soll": (("1", "Sing"), ("3", "Sing")),
    "sollst": (("2", "Sing"),),
    "sollen": (("1", "Plur"), ("3", "Plur")),
    "sollt": (("2", "Plur"),),
    "darf": (("1", "Sing"), ("3", "Sing")),
    "darfst": (("2", "Sing"),),
    "dürfen": (("1", "Plur"), ("3", "Plur")),
    "dürft": (("2", "Plur"),),
}

# Regular weak-verb endings (Präsens and Präteritum), longest suffix first so
# e.g. "test" is tried before "st".
_REGULAR_VERB_ENDINGS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("test", (("2", "Sing"),)),
    ("tet", (("2", "Plur"),)),
    ("ten", (("1", "Plur"), ("3", "Plur"))),
    ("te", (("1", "Sing"), ("3", "Sing"))),
    ("est", (("2", "Sing"),)),
    ("et", (("2", "Plur"),)),
    ("en", (("1", "Plur"), ("3", "Plur"))),
    ("st", (("2", "Sing"),)),
    ("e", (("1", "Sing"),)),
    ("t", (("3", "Sing"), ("2", "Plur"))),
)

# A handful of very common A1/A2 nouns whose gender is memorised as part of
# the closed-class table, keyed by their plural surface form (the only form
# `nomen_plural` items expose). Noun gender is lexical, not inflectional, so
# this is intentionally a short, honest list and nothing more.
_PLURAL_NOUN_GENDER: dict[str, str] = {
    "tische": "Masc",
    "hunde": "Masc",
    "männer": "Masc",
    "bücher": "Neut",
    "häuser": "Neut",
    "autos": "Neut",
    "kinder": "Neut",
    "frauen": "Fem",
    "katzen": "Fem",
    "blumen": "Fem",
    "zeitungen": "Fem",
}


def _resolve(
    candidates: list[dict[str, str]],
    dims: tuple[str, ...],
) -> dict[str, str]:
    """Commit to a value for each of ``dims`` only where every candidate agrees."""
    result: dict[str, str] = {}
    for dim in dims:
        values = {c[dim] for c in candidates if dim in c}
        values.discard(UNK)
        if len(values) == 1:
            result[dim] = next(iter(values))
        else:
            result[dim] = UNK
    return result


def _decode_article(answer: str, fixed_case: str | None, dims: tuple[str, ...]) -> dict[str, str]:
    candidates: list[dict[str, str]] = [
        {"Case": c, "Gender": g, "Number": n}
        for (form, c, g, n) in _DEFINITE_ARTICLE_PARADIGM
        if form == answer and (fixed_case is None or c == fixed_case)
    ]
    if candidates:
        resolved = _resolve(candidates, dims)
        if "Definite" in dims:
            resolved["Definite"] = "Def"
        return resolved

    for stem in _EIN_WORD_STEMS:
        if answer.startswith(stem):
            ending = answer[len(stem) :]
            ein_candidates = [
                {"Case": c, "Gender": g, "Number": n}
                for (e, c, g, n) in _EIN_ENDING_PARADIGM
                if e == ending and (fixed_case is None or c == fixed_case)
            ]
            if ein_candidates:
                resolved = _resolve(ein_candidates, dims)
                if "Definite" in dims:
                    resolved["Definite"] = "Ind"
                return resolved

    return dict.fromkeys(dims, UNK)


def _decode_personal_pronoun(
    answer: str, fixed_case: str | None, dims: tuple[str, ...]
) -> dict[str, str]:
    candidates = [
        {"Case": c, "Person": p, "Number": n, "Gender": g}
        for (form, c, p, n, g) in _PERSONAL_PRONOUN_PARADIGM
        if form == answer and (fixed_case is None or c == fixed_case)
    ]
    if not candidates:
        return dict.fromkeys(dims, UNK)
    return _resolve(candidates, dims)


def _decode_reflexive_pronoun(
    answer: str, fixed_case: str | None, dims: tuple[str, ...]
) -> dict[str, str]:
    candidates = [
        {"Case": c, "Person": p, "Number": n}
        for (form, c, p, n) in _REFLEXIVE_PARADIGM
        if form == answer and (fixed_case is None or c == fixed_case)
    ]
    if not candidates:
        return dict.fromkeys(dims, UNK)
    return _resolve(candidates, dims)


def _decode_verb(answer: str, dims: tuple[str, ...]) -> dict[str, str]:
    pairs = _IRREGULAR_VERB_FORMS.get(answer)
    if pairs is None:
        for suffix, ending_pairs in _REGULAR_VERB_ENDINGS:
            if answer.endswith(suffix) and len(answer) > len(suffix):
                pairs = ending_pairs
                break
    if pairs is None:
        return dict.fromkeys(dims, UNK)
    candidates = [{"Person": p, "Number": n} for (p, n) in pairs]
    return _resolve(candidates, dims)


def _decode_noun(answer: str, dims: tuple[str, ...]) -> dict[str, str]:
    gender = _PLURAL_NOUN_GENDER.get(answer, UNK)
    return {dim: (gender if dim == "Gender" else UNK) for dim in dims}


def derive_facet(item: BankItem, topic: Topic) -> str | None:
    """Derive the stable, canonical facet an ingested item exhibits for ``topic``.

    Deterministic and pure: same ``item`` plus same ``topic`` always yields
    the same string. Returns ``None`` if and only if ``facet_space(topic)``
    is empty. Values are decoded from ``item.accepted_answers[0]`` alone,
    using the closed-class tables above; a dimension the tables cannot pin
    down is encoded as ``"Unk"``, never guessed or dropped.
    """
    dims = facet_space(topic)
    if not dims:
        return None

    answer = item.accepted_answers[0].strip().lower() if item.accepted_answers else ""
    fixed_case = (topic.morph_spec or {}).get("Case")
    fixed_case = fixed_case if isinstance(fixed_case, str) else None
    category = _pos_category(topic)

    if category in ("Prep", "Det", "Nominal"):
        values = _decode_article(answer, fixed_case, dims)
    elif category == "Pron":
        values = _decode_personal_pronoun(answer, fixed_case, dims)
    elif category == "ReflVerb":
        values = _decode_reflexive_pronoun(answer, fixed_case, dims)
    elif category == "Verb":
        values = _decode_verb(answer, dims)
    elif category == "Noun":
        values = _decode_noun(answer, dims)
    else:
        # Adj / AdjDecl attributive endings and any unclassified bucket:
        # German adjective endings are too syncretic for a small honest
        # table to resolve reliably (see module docstring). Explicit Unk.
        values = dict.fromkeys(dims, UNK)

    return "|".join(f"{dim}={values.get(dim, UNK)}" for dim in sorted(dims))
