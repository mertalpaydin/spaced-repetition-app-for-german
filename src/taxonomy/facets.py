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
| RelPron  | ``PronType == "Rel"`` fixed in morph_spec    | Case, Gender, Number     |
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

``RelPron`` exists because a relative pronoun genuinely varies over Case (its
case comes from its role inside its own clause, independent of its
antecedent), which none of ``Prep``/``Det``/``Nominal`` expose as a facet --
those all assume Case is fixed by the topic. ``PronType: Rel`` is a real,
non-disjunctive Universal Dependencies feature value (unlike the invented
``Case: NomOrAcc`` this replaced in ``relativsatz_nom_akk``); a topic that
also fixes ``Case`` (``relativsatz_genitiv`` fixes ``Case: Gen``) simply has
Case filtered out of its facet space by the same "dim not in fixed" rule as
every other category.

A topic with an empty ``morph_spec`` returns an empty facet space
unconditionally and short-circuits classification entirely: it has no
facets, explicitly, per ``01-foundation.md:170``. A topic may also be marked
``facet_exempt: true`` (with a ``facet_exempt_reason``) to the same effect
while keeping a genuinely non-empty ``morph_spec`` for the stage 4
morphology check -- see ``facet_space``'s docstring below for when that
applies instead of emptying ``morph_spec``.

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
rather than guessing. Noun gender is lexical, not inflectional, so it is only
resolved for a small fixed list of common nouns; everything else is ``Unk``.

### Adjective and participle endings: context, not guessing

A bare attributive ending (``guten``) is genuinely ambiguous in isolation --
it is simultaneously masculine accusative singular, dative plural, and more.
But German attributive adjectives are almost always preceded by a determiner
in the gap's local context, and that determiner disambiguates most of those
cells. ``derive_facet`` therefore looks at the token immediately preceding
the ``___`` gap in ``item.prompt``:

* If it is a definite article or ein-word already in the tables above, the
  ending is decoded against the matching declension paradigm (*weak* after
  a definite article, *mixed* after an ein-word) and intersected with the
  determiner's own candidate set. A dimension is only committed when both
  sides agree; where they disagree (or the intersection is empty, i.e. the
  prompt is contradictory), the dimension is ``"Unk"``.
* If no such determiner precedes the gap (including the gap opening the
  sentence), the ending is decoded against the *strong* declension paradigm
  alone -- with zero article the adjective carries the same signal a
  definite article would otherwise carry, so the ending is informative on
  its own (subject to the same genuine syncretism as any other closed-class
  table, e.g. strong ``-er`` alone still spans Nom Masc Sg, Dat Fem Sg, Gen
  Fem Sg and Gen Plur).

This is still a small, closed-class, deterministic lookup -- no spaCy, no
guessing -- it is just scoped to the gap's immediate left context instead of
the answer alone. Where the determiner and ending genuinely leave a cell
ambiguous (most famously the ``-en`` ending, which is syncretic across nearly
every case/gender/number combination in all three declensions), the result
is honestly ``"Unk"`` for that dimension rather than a guess.
"""

from __future__ import annotations

import re

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
    "RelPron": ("Case", "Gender", "Number"),
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

    # A relative pronoun is genuinely PronType=Rel (a real, valid UD feature
    # value -- unlike the disjunctive "NomOrAcc" this replaced). Case is
    # deliberately left out of morph_spec for relativsatz_nom_akk because a
    # relative pronoun's case is exactly the thing that varies with the
    # pronoun's syntactic role in its own clause; relativsatz_genitiv fixes
    # Case: Gen in addition, which the "dim not in fixed" filter below
    # already handles correctly either way.
    if morph.get("PronType") == "Rel":
        return "RelPron"
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

    A topic may also be explicitly marked ``facet_exempt: true`` in
    ``data/taxonomy.yaml`` (with a required ``facet_exempt_reason``). This is
    for topics whose ``morph_spec`` is genuinely non-empty and still useful
    for the stage 4 morphology check, but whose word class has no second
    grammatical cell to vary into (impersonal passive's invariant 3sg
    agreement; genitive syncretism with no Gender/Number combination left
    undecided). Emptying ``morph_spec`` for those topics would silently
    discard a real stage 4 constraint just to dodge stage 6, so the
    exemption is instead a separate, visible, reasoned flag -- checked
    first and unconditionally, exactly like the empty-``morph_spec`` case.
    """
    if getattr(topic, "facet_exempt", False):
        return ()
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

# Relative pronoun: its own paradigm, deliberately separate from
# _DEFINITE_ARTICLE_PARADIGM above. Nominative and accusative relative
# pronouns happen to share the definite article's surface forms, but Dative
# plural ("denen", not "den") and the whole Genitive row ("dessen"/"deren",
# not "des"/"der") genuinely differ -- reusing the article table would both
# mis-decode those forms and, for the forms that do coincide, drag in
# genitive/dative article candidates a relative pronoun can never actually
# be. Plural is collapsed to a single Gender=UNK row per case, exactly like
# _PERSONAL_PRONOUN_PARADIGM already does for 3rd-person plural "sie": German
# does not mark gender in the plural at all (der/die/das -> die; dessen/deren
# -> deren), so a real, distinct plural candidate that carries no gender
# information is honest, not a guess -- and it is what lets e.g. "deren"
# resolve Gender=Fem from its singular reading instead of collapsing to
# all-Unk against three fabricated plural genders that were never really
# separate candidates to begin with.
_RELATIVE_PRONOUN_PARADIGM: tuple[tuple[str, str, str, str], ...] = (
    # (form, Case, Gender, Number)
    ("der", "Nom", "Masc", "Sing"),
    ("die", "Nom", "Fem", "Sing"),
    ("das", "Nom", "Neut", "Sing"),
    ("die", "Nom", UNK, "Plur"),
    ("den", "Acc", "Masc", "Sing"),
    ("die", "Acc", "Fem", "Sing"),
    ("das", "Acc", "Neut", "Sing"),
    ("die", "Acc", UNK, "Plur"),
    ("dem", "Dat", "Masc", "Sing"),
    ("der", "Dat", "Fem", "Sing"),
    ("dem", "Dat", "Neut", "Sing"),
    ("denen", "Dat", UNK, "Plur"),
    ("dessen", "Gen", "Masc", "Sing"),
    ("deren", "Gen", "Fem", "Sing"),
    ("dessen", "Gen", "Neut", "Sing"),
    ("deren", "Gen", UNK, "Plur"),
)

# ein-words (ein/kein/mein/dein/sein/ihr/unser/euer): the "mixed declension"
# ending pattern, keyed by ending only -- the stem carries no case/gender
# information the ending doesn't already carry. "euer" additionally needs its
# own contracted stem "eur-" (euer -> eure/eurem/euren/eurer/eures drops the
# second "e" everywhere except the bare zero-ending form) alongside the full
# "euer" stem for that zero-ending form itself; the two never collide ("euer"
# does not start with "eur" + a valid ending prefix conflict, since "eur"'s
# third letter is "r" where "euer"'s third letter is "e").
_EIN_WORD_STEMS: tuple[str, ...] = (
    "kein",
    "mein",
    "dein",
    "sein",
    "ihr",
    "unser",
    "euer",
    "eur",
    "ein",
)
_EIN_ENDING_PARADIGM: tuple[tuple[str, str, str, str], ...] = (
    # (ending, Case, Gender, Number)
    ("", "Nom", "Masc", "Sing"),
    ("", "Nom", "Neut", "Sing"),
    ("", "Acc", "Neut", "Sing"),
    # "ein" itself has no plural at all, so for this closed word class the
    # bare -e ending is unambiguously feminine nominative/accusative
    # singular -- not also conflated with a fabricated Nom/Acc plural -e
    # cell the way the shared _DEFINITE_ARTICLE_PARADIGM's "die" is.
    ("e", "Nom", "Fem", "Sing"),
    ("e", "Acc", "Fem", "Sing"),
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


# ==============================================================================
# Attributive adjective/participle endings, scoped by declension class
#
# German attributive adjectives decline in one of three patterns depending on
# what (if anything) precedes them: *weak* after a definite article (or
# der-word), *mixed* after an ein-word, *strong* with no article at all. Each
# paradigm is honestly syncretic where German itself is syncretic (most
# famously the weak/mixed "-en" ending, which spans nearly every non-Nom-Sg
# cell) -- the resolver below only commits a dimension when every candidate
# agrees, exactly like the other closed-class tables in this module.
# ==============================================================================

_ADJ_ENDING_WEAK: tuple[tuple[str, str, str, str], ...] = (
    # (ending, Case, Gender, Number) -- after a definite article / der-word.
    ("e", "Nom", "Masc", "Sing"),
    ("e", "Nom", "Fem", "Sing"),
    ("e", "Nom", "Neut", "Sing"),
    ("e", "Acc", "Fem", "Sing"),
    ("e", "Acc", "Neut", "Sing"),
    ("en", "Acc", "Masc", "Sing"),
    ("en", "Dat", "Masc", "Sing"),
    ("en", "Dat", "Fem", "Sing"),
    ("en", "Dat", "Neut", "Sing"),
    ("en", "Gen", "Masc", "Sing"),
    ("en", "Gen", "Fem", "Sing"),
    ("en", "Gen", "Neut", "Sing"),
    ("en", "Nom", "Masc", "Plur"),
    ("en", "Nom", "Fem", "Plur"),
    ("en", "Nom", "Neut", "Plur"),
    ("en", "Acc", "Masc", "Plur"),
    ("en", "Acc", "Fem", "Plur"),
    ("en", "Acc", "Neut", "Plur"),
    ("en", "Dat", "Masc", "Plur"),
    ("en", "Dat", "Fem", "Plur"),
    ("en", "Dat", "Neut", "Plur"),
    ("en", "Gen", "Masc", "Plur"),
    ("en", "Gen", "Fem", "Plur"),
    ("en", "Gen", "Neut", "Plur"),
)

_ADJ_ENDING_MIXED: tuple[tuple[str, str, str, str], ...] = (
    # (ending, Case, Gender, Number) -- after an ein-word. Plural is
    # identical to the weak paradigm (mixed declension only differs from
    # weak in the singular).
    ("er", "Nom", "Masc", "Sing"),
    ("e", "Nom", "Fem", "Sing"),
    ("es", "Nom", "Neut", "Sing"),
    ("en", "Acc", "Masc", "Sing"),
    ("e", "Acc", "Fem", "Sing"),
    ("es", "Acc", "Neut", "Sing"),
    ("en", "Dat", "Masc", "Sing"),
    ("en", "Dat", "Fem", "Sing"),
    ("en", "Dat", "Neut", "Sing"),
    ("en", "Gen", "Masc", "Sing"),
    ("en", "Gen", "Fem", "Sing"),
    ("en", "Gen", "Neut", "Sing"),
    *(row for row in _ADJ_ENDING_WEAK if row[3] == "Plur"),
)

_ADJ_ENDING_STRONG: tuple[tuple[str, str, str, str], ...] = (
    # (ending, Case, Gender, Number) -- zero article: the adjective carries
    # the definite-article-like signal itself, except Gen Masc/Neut Sg
    # ("-en", not "-es") because the noun already carries the genitive -(e)s.
    ("er", "Nom", "Masc", "Sing"),
    ("e", "Nom", "Fem", "Sing"),
    ("es", "Nom", "Neut", "Sing"),
    ("en", "Acc", "Masc", "Sing"),
    ("e", "Acc", "Fem", "Sing"),
    ("es", "Acc", "Neut", "Sing"),
    ("em", "Dat", "Masc", "Sing"),
    ("er", "Dat", "Fem", "Sing"),
    ("em", "Dat", "Neut", "Sing"),
    ("en", "Gen", "Masc", "Sing"),
    ("er", "Gen", "Fem", "Sing"),
    ("en", "Gen", "Neut", "Sing"),
    ("e", "Nom", "Masc", "Plur"),
    ("e", "Nom", "Fem", "Plur"),
    ("e", "Nom", "Neut", "Plur"),
    ("e", "Acc", "Masc", "Plur"),
    ("e", "Acc", "Fem", "Plur"),
    ("e", "Acc", "Neut", "Plur"),
    ("en", "Dat", "Masc", "Plur"),
    ("en", "Dat", "Fem", "Plur"),
    ("en", "Dat", "Neut", "Plur"),
    ("er", "Gen", "Masc", "Plur"),
    ("er", "Gen", "Fem", "Plur"),
    ("er", "Gen", "Neut", "Plur"),
)

_GAP_MARKER = "___"


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


def determiner_definiteness(answer: str, fixed_case: str | None = None) -> str:
    """Best-effort Definite/Ind classification for a determiner-shaped
    ``answer`` (article or ein-word), reusing ``_decode_article``'s own
    paradigm tables. Returns ``"Def"``, ``"Ind"``, or ``UNK`` if the word
    matches neither paradigm (e.g. a demonstrative or interrogative
    determiner like "dieser"/"welcher", for which this taxonomy has no
    closed-class table).

    Public wrapper for stage 4's answer-set ambiguity check
    (docs/audits/stage-04-pilot-2026-08-15.md fix 1): a determiner topic
    that declares ``syntax_tags["ArtType"]`` is testing exactly this
    distinction, so an accepted-answer set spanning both Def and Ind is
    evidence the item no longer tests one article type.
    """
    resolved = _decode_article(answer.strip().lower(), fixed_case, ("Definite",))
    return resolved.get("Definite", UNK)


def determiner_art_type(answer: str) -> str:
    """Finer-grained determiner classification than
    ``determiner_definiteness``'s binary Def/Ind: distinguishes definite
    (``"Def"``), indefinite (``"Ind"``, bare "ein"), negative (``"Neg"``,
    "kein"), and possessive (``"Poss"``, mein/dein/sein/ihr/unser/euer) --
    the actual ``ArtType`` values this taxonomy's topics declare (see
    ``data/taxonomy.yaml``). ``UNK`` if the word matches neither the
    definite-article nor the ein-word paradigm (e.g. a demonstrative like
    "dieser"/"jener", for which this taxonomy has no closed-class table).

    ``determiner_definiteness`` alone is not enough to gate stage 4's
    answer-set ambiguity check (docs/audits/stage-04-pilot-2026-08-15.md fix
    1): "ein", "kein" and every possessive all resolve to the SAME coarse
    "Ind" bucket there (they share the ein-word ending paradigm), so an
    ``artikel_possessiv_nom`` item (``ArtType: Poss``) accepting "Eine" (a
    genuine indefinite article, not a possessive) alongside "Meine"/"Deine"
    would pass the Def/Ind check undetected -- confirmed live in a pilot
    re-run. This function inspects WHICH ein-word stem matched, not just
    whether one did, so ``Poss`` and ``Ind`` and ``Neg`` are distinguished.
    """
    lower = answer.strip().lower()
    if any(form == lower for (form, *_rest) in _DEFINITE_ARTICLE_PARADIGM):
        return "Def"
    for stem in _EIN_WORD_STEMS:
        if not lower.startswith(stem):
            continue
        ending = lower[len(stem) :]
        if not any(e == ending for (e, *_rest) in _EIN_ENDING_PARADIGM):
            continue
        if stem == "ein":
            return "Ind"
        if stem == "kein":
            return "Neg"
        return "Poss"
    return UNK


def _decode_relative_pronoun(
    answer: str, fixed_case: str | None, dims: tuple[str, ...]
) -> dict[str, str]:
    candidates: list[dict[str, str]] = [
        {"Case": c, "Gender": g, "Number": n}
        for (form, c, g, n) in _RELATIVE_PRONOUN_PARADIGM
        if form == answer and (fixed_case is None or c == fixed_case)
    ]
    if not candidates:
        return dict.fromkeys(dims, UNK)
    return _resolve(candidates, dims)


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


def _preceding_token(prompt: str) -> str | None:
    """The word immediately before the ``___`` gap, lowercased, or ``None``.

    ``None`` both when the gap is missing and when the gap opens the prompt
    (no preceding word at all) -- both cases the zero-article / strong
    declension caller treats identically: no determiner to consult.
    """
    gap_pos = prompt.find(_GAP_MARKER)
    if gap_pos == -1:
        return None
    before = prompt[:gap_pos]
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", before)
    return words[-1].lower() if words else None


def _determiner_candidates(token: str) -> tuple[str, list[tuple[str, str, str]]] | None:
    """Classify ``token`` as a weak- or mixed-declension trigger.

    Returns ``(declension, candidates)`` where ``declension`` is ``"weak"``
    for a definite article / der-word or ``"mixed"`` for an ein-word, and
    ``candidates`` is every (Case, Gender, Number) the token is grammatically
    ambiguous between. ``None`` if ``token`` matches neither closed class --
    the caller then treats the gap as zero-article (strong declension).
    """
    article_candidates = [
        (c, g, n) for (form, c, g, n) in _DEFINITE_ARTICLE_PARADIGM if form == token
    ]
    if article_candidates:
        return "weak", article_candidates

    for stem in _EIN_WORD_STEMS:
        if token.startswith(stem):
            ending = token[len(stem) :]
            ein_candidates = [(c, g, n) for (e, c, g, n) in _EIN_ENDING_PARADIGM if e == ending]
            if ein_candidates:
                return "mixed", ein_candidates

    return None


def _match_adjective_ending(
    answer: str, paradigm: tuple[tuple[str, str, str, str], ...]
) -> list[tuple[str, str, str]]:
    """Every (Case, Gender, Number) ``answer``'s ending is consistent with in ``paradigm``.

    German attributive endings end in a distinct final letter each (-e, -en,
    -er, -es, -em), so at most one ending in a paradigm ever matches.
    """
    for ending in sorted({row[0] for row in paradigm}, key=len, reverse=True):
        if answer.endswith(ending) and len(answer) > len(ending):
            return [(c, g, n) for (e, c, g, n) in paradigm if e == ending]
    return []


def _decode_attributive_adjective(
    prompt: str, answer: str, dims: tuple[str, ...]
) -> dict[str, str]:
    """Decode an attributive adjective/participle ending using its gap's left context.

    See the module docstring's "Adjective and participle endings" section:
    a determiner immediately before the gap selects the weak or mixed
    declension paradigm and constrains the ending's own candidates; no
    determiner falls back to the strong declension paradigm, which is
    informative on its own.
    """
    token = _preceding_token(prompt)
    determiner = _determiner_candidates(token) if token else None

    if determiner is not None:
        declension, det_candidates = determiner
        paradigm = _ADJ_ENDING_WEAK if declension == "weak" else _ADJ_ENDING_MIXED
        ending_candidates = set(_match_adjective_ending(answer, paradigm))
        combined = [c for c in det_candidates if c in ending_candidates]
    else:
        combined = _match_adjective_ending(answer, _ADJ_ENDING_STRONG)

    if not combined:
        return dict.fromkeys(dims, UNK)
    candidates = [{"Case": c, "Gender": g, "Number": n} for (c, g, n) in combined]
    return _resolve(candidates, dims)


def attributive_adjective_gender_candidates(
    prompt: str, answer: str, *, number: str = "Sing"
) -> set[str]:
    """Every ``Gender`` an attributive adjective/participle ``answer`` is
    consistent with, given the declension paradigm selected by the token
    preceding the ``___`` gap in ``prompt`` -- the exact determiner-context
    selection ``_decode_attributive_adjective`` uses for facet derivation
    (weak after a definite article/der-word, mixed after an ein-word, strong
    with none), reused here for stage 4 morphology verification instead of
    facet decoding.

    Restricted to ``number`` (default ``"Sing"``) because German's plural
    endings (-e Nom/Acc, -en Dat, -er Gen) are shared across all three
    genders and would otherwise swamp any genuine singular gender signal --
    e.g. the strong-declension "-en" ending is Masc-or-Neut-only in the
    singular (Acc Masc / Gen Neut) but appears in the Dat plural of every
    gender. Pass ``number=""`` (or any value matching no row) to consider
    every number instead.

    Returns an empty set if the ending is not recognised in the selected
    paradigm at all -- the caller's signal to skip the check rather than
    guess, exactly like every other closed-class table in this module.
    """
    token = _preceding_token(prompt)
    determiner = _determiner_candidates(token) if token else None

    if determiner is not None:
        declension, det_candidates = determiner
        paradigm = _ADJ_ENDING_WEAK if declension == "weak" else _ADJ_ENDING_MIXED
        ending_candidates = set(_match_adjective_ending(answer, paradigm))
        combined = [c for c in det_candidates if c in ending_candidates]
    else:
        combined = _match_adjective_ending(answer, _ADJ_ENDING_STRONG)

    restricted = [c for c in combined if c[2] == number]
    relevant = restricted or combined
    return {g for (_, g, _) in relevant}


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
    elif category == "RelPron":
        values = _decode_relative_pronoun(answer, fixed_case, dims)
    elif category == "Pron":
        values = _decode_personal_pronoun(answer, fixed_case, dims)
    elif category == "ReflVerb":
        values = _decode_reflexive_pronoun(answer, fixed_case, dims)
    elif category == "Verb":
        values = _decode_verb(answer, dims)
    elif category == "Noun":
        values = _decode_noun(answer, dims)
    elif category in ("Adj", "AdjDecl"):
        values = _decode_attributive_adjective(item.prompt, answer, dims)
    else:
        # Any unclassified bucket falls back to explicit Unk rather than
        # guessing.
        values = dict.fromkeys(dims, UNK)

    return "|".join(f"{dim}={values.get(dim, UNK)}" for dim in sorted(dims))
