"""Closed-class German paradigm tables.

Lifted verbatim from the deleted ``src/taxonomy/facets.py`` (the grammar
trainer's facet decoder) because ``src.phrases.paradigms`` builds its
reverse lookups from them. Data only; no behaviour.
"""

UNK = "Unk"

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
