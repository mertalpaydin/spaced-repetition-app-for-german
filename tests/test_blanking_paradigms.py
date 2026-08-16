"""Unit tests for src/generation/blanking/paradigms.py."""

from src.generation.blanking import paradigms


def test_definite_article_form_resolves_every_cell_the_paradigm_covers() -> None:
    assert paradigms.definite_article_form(("Nom", "Masc", "Sing")) == "der"
    assert paradigms.definite_article_form(("Dat", "Masc", "Sing")) == "dem"
    assert paradigms.definite_article_form(("Dat", "Neut", "Sing")) == "dem"
    assert paradigms.definite_article_form(("Gen", "Fem", "Plur")) == "der"
    assert paradigms.definite_article_form(("Nom", "Fem", "Plur")) == "die"
    assert paradigms.definite_article_form(("Dat", "Masc", "Plur")) == "den"


def test_match_ein_word_splits_stem_and_ending() -> None:
    assert paradigms.match_ein_word("einen") == ("ein", "en")
    assert paradigms.match_ein_word("keine") == ("kein", "e")
    assert paradigms.match_ein_word("meiner") == ("mein", "er")


def test_match_ein_word_disambiguates_euer_from_its_contracted_stem() -> None:
    """ "euer" (bare, Nom Masc/Neut Sg) must not be mis-split as stem "eur" +
    ending "er" -- it is checked before "eur" in the same order
    ``facets._decode_article`` uses, so it matches its own bare stem."""
    assert paradigms.match_ein_word("euer") == ("euer", "")
    assert paradigms.match_ein_word("eure") == ("eur", "e")


def test_match_ein_word_returns_none_for_an_unrelated_word() -> None:
    assert paradigms.match_ein_word("Hund") is None
    assert paradigms.match_ein_word("jeder") is None


def test_ein_word_form_reconstructs_singular_cells() -> None:
    assert paradigms.ein_word_form("mein", ("Nom", "Masc", "Sing")) == "mein"
    assert paradigms.ein_word_form("kein", ("Gen", "Masc", "Sing")) == "keines"
    assert paradigms.ein_word_form("ihr", ("Dat", "Fem", "Sing")) == "ihrer"


def test_ein_word_form_has_no_nominative_or_accusative_plural_row() -> None:
    """Documented gap (module docstring): the shared ein-word ending
    paradigm was built for "ein", which has no plural at all, so it has no
    Nom/Acc plural row even though "kein"/possessives genuinely do have
    plural forms ("keine Kinder" is valid German). Reusing the table as-is
    (never duplicating it) means this cell honestly returns None rather than
    guessing "keine" from the Fem-singular "-e" row."""
    assert paradigms.ein_word_form("kein", ("Nom", "Neut", "Plur")) is None
    assert paradigms.ein_word_form("kein", ("Acc", "Neut", "Plur")) is None


def test_ein_word_form_covers_dative_and_genitive_plural() -> None:
    """Unlike Nom/Acc, the paradigm DOES cover Dat/Gen plural (every gender
    collapses to one invariant ending there), so these reconstruct fine."""
    assert paradigms.ein_word_form("ihr", ("Dat", "Masc", "Plur")) == "ihren"
    assert paradigms.ein_word_form("mein", ("Gen", "Fem", "Plur")) == "meiner"


def test_adjective_ending_covers_all_three_declensions() -> None:
    assert paradigms.adjective_ending("weak", ("Nom", "Masc", "Sing")) == "e"
    assert paradigms.adjective_ending("weak", ("Dat", "Fem", "Sing")) == "en"
    assert paradigms.adjective_ending("mixed", ("Nom", "Masc", "Sing")) == "er"
    assert paradigms.adjective_ending("mixed", ("Nom", "Neut", "Sing")) == "es"
    assert paradigms.adjective_ending("strong", ("Dat", "Fem", "Sing")) == "er"
    assert paradigms.adjective_ending("strong", ("Gen", "Masc", "Sing")) == "en"


def test_family_forms_definite_is_lemma_invariant() -> None:
    family = paradigms.family_forms("Def", "der")
    assert family is not None
    assert family[("Nom", "Masc", "Sing")] == "der"
    assert family[("Dat", "Masc", "Sing")] == "dem"


def test_family_forms_possessive_stays_within_the_same_stem() -> None:
    family = paradigms.family_forms("Poss", "meine")
    assert family is not None
    # Every value in the family must start with the same stem ("mein"), never
    # cross over into a different possessor's forms.
    assert all(form.startswith("mein") for form in family.values())
    assert family[("Nom", "Fem", "Sing")] == "meine"


def test_family_forms_returns_none_for_an_unrecognised_word() -> None:
    assert paradigms.family_forms("Ind", "Hund") is None


def test_adjective_family_forms_stays_within_the_same_stem_and_declension() -> None:
    family = paradigms.adjective_family_forms("strong", "kalt")
    assert family[("Acc", "Masc", "Sing")] == "kalten"
    assert family[("Nom", "Fem", "Sing")] == "kalte"
    assert all(form.startswith("kalt") for form in family.values())
