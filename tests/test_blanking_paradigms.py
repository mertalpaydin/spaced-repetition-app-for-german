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


# ==============================================================================
# Cycle 3. Personal, reflexive, and relative pronoun paradigms.
# ==============================================================================


def test_personal_pronoun_form_resolves_all_three_cases() -> None:
    assert paradigms.personal_pronoun_form("Nom", "3", "Sing", "Masc") == "er"
    assert paradigms.personal_pronoun_form("Acc", "3", "Sing", "Masc") == "ihn"
    assert paradigms.personal_pronoun_form("Dat", "1", "Plur", "UNK") == "uns"


def test_personal_pronoun_family_forms_stays_within_the_same_person_number_gender() -> None:
    family = paradigms.personal_pronoun_family_forms("3", "Sing", "Masc")
    assert family == {"Nom": "er", "Acc": "ihn", "Dat": "ihm"}


def test_reflexive_form_is_invariant_across_persons_for_sich_only() -> None:
    assert paradigms.reflexive_form("Acc", "1", "Sing") == "mich"
    assert paradigms.reflexive_form("Dat", "1", "Sing") == "mir"
    assert paradigms.reflexive_form("Acc", "3", "Plur") == "sich"
    assert paradigms.reflexive_form("Dat", "3", "Plur") == "sich"


def test_reflexive_family_forms_covers_both_cases() -> None:
    assert paradigms.reflexive_family_forms("3", "Sing") == {"Acc": "sich", "Dat": "sich"}
    assert paradigms.reflexive_family_forms("1", "Sing") == {"Acc": "mich", "Dat": "mir"}


def test_relative_pronoun_form_covers_all_four_cases() -> None:
    assert paradigms.relative_pronoun_form("Nom", "Masc", "Sing") == "der"
    assert paradigms.relative_pronoun_form("Acc", "Masc", "Sing") == "den"
    assert paradigms.relative_pronoun_form("Dat", "Masc", "Sing") == "dem"
    assert paradigms.relative_pronoun_form("Gen", "Fem", "Sing") == "deren"


def test_relative_pronoun_family_forms_stays_within_the_same_gender_and_number() -> None:
    family = paradigms.relative_pronoun_family_forms("Masc", "Sing")
    assert family == {"Nom": "der", "Acc": "den", "Dat": "dem", "Gen": "dessen"}


# ==============================================================================
# Cycle 3. Verb conjugation: regular, mixed, strong, vowel-changing present.
# ==============================================================================


def test_regular_praesens_form_conjugates_a_weak_verb() -> None:
    assert paradigms.regular_praesens_form("machen", "1", "Sing") == "mache"
    assert paradigms.regular_praesens_form("machen", "2", "Sing") == "machst"
    assert paradigms.regular_praesens_form("machen", "3", "Plur") == "machen"


def test_regular_praesens_form_handles_epenthesis_for_d_and_t_stems() -> None:
    """ "arbeiten" (stem ends "t") needs an epenthetic "e" before consonantal
    endings ("arbeitest", not "arbeitst")."""
    assert paradigms.regular_praesens_form("arbeiten", "2", "Sing") == "arbeitest"
    assert paradigms.regular_praesens_form("arbeiten", "3", "Sing") == "arbeitet"


def test_regular_praesens_form_handles_the_sibilant_stem_2sg_exception() -> None:
    """ "heißen"'s stem already ends in a sibilant, so the 2sg ending drops
    its own "-s" ("heißt", not "heißst")."""
    assert paradigms.regular_praesens_form("reisen", "2", "Sing") == "reist"


def test_regular_praeteritum_form_conjugates_a_weak_verb() -> None:
    assert paradigms.regular_praeteritum_form("machen", "1", "Sing") == "machte"
    assert paradigms.regular_praeteritum_form("machen", "3", "Plur") == "machten"


def test_strong_praeteritum_form_uses_the_hand_verified_stem_table() -> None:
    assert paradigms.strong_praeteritum_form("sprechen", "3", "Sing") == "sprach"
    assert paradigms.strong_praeteritum_form("gehen", "1", "Sing") == "ging"


def test_mixed_praeteritum_form_uses_weak_endings_with_an_irregular_stem() -> None:
    assert paradigms.mixed_praeteritum_form("bringen", "1", "Sing") == "brachte"
    assert paradigms.mixed_praeteritum_form("wissen", "3", "Sing") == "wusste"


def test_vokalwechsel_praesens_form_only_changes_the_du_and_er_forms() -> None:
    assert paradigms.vokalwechsel_praesens_form("fahren", "3", "Sing") == "fährt"
    assert paradigms.vokalwechsel_praesens_form("fahren", "2", "Sing") == "fährst"
    # 1st person singular and every plural form are unaffected by the
    # vowel change -- this is exactly the ambiguity that makes the vowel
    # change worth testing at all.
    assert paradigms.vokalwechsel_praesens_form("fahren", "1", "Sing") == "fahre"
    assert paradigms.vokalwechsel_praesens_form("fahren", "1", "Plur") == "fahren"


def test_verb_family_form_dispatches_by_family_name() -> None:
    assert paradigms.verb_family_form("regular_praesens", "machen", "1", "Sing") == "mache"
    assert paradigms.verb_family_form("strong_praeteritum", "gehen", "1", "Sing") == "ging"
    assert paradigms.verb_family_form("vokalwechsel_praesens", "fahren", "3", "Sing") == "fährt"


def test_verb_family_forms_covers_all_six_finite_cells() -> None:
    family = paradigms.verb_family_forms("regular_praesens", "machen")
    assert family[("1", "Sing")] == "mache"
    assert family[("2", "Sing")] == "machst"
    assert family[("3", "Sing")] == "macht"
    assert family[("1", "Plur")] == "machen"
    assert family[("2", "Plur")] == "macht"
    assert family[("3", "Plur")] == "machen"


# ==============================================================================
# Cycle 3. Irregular finite paradigms (sein/haben/werden/modals).
# ==============================================================================


def test_irregular_finite_form_covers_sein_present_and_past() -> None:
    assert paradigms.irregular_finite_form("sein", "Pres", "1", "Sing") == "bin"
    assert paradigms.irregular_finite_form("sein", "Past", "1", "Sing") == "war"


def test_irregular_finite_form_covers_a_modal() -> None:
    assert paradigms.irregular_finite_form("können", "Pres", "1", "Sing") == "kann"
    assert paradigms.irregular_finite_form("müssen", "Past", "3", "Plur") == "mussten"


def test_irregular_finite_form_returns_none_for_an_uncovered_lemma_or_tense() -> None:
    assert paradigms.irregular_finite_form("machen", "Pres", "1", "Sing") is None
    # "sollte"/"wollte" are surface-identical to their own Präteritum
    # Indikativ, so SubjII was deliberately not added for these two modals
    # (see IRREGULAR_FINITE's module-level comment) -- reconstructing from a
    # SubjII table for them would be fabricating a distinction the language
    # itself does not mark.
    assert paradigms.irregular_finite_form("sollen", "SubjII", "1", "Sing") is None


def test_irregular_finite_family_forms_covers_all_six_cells() -> None:
    family = paradigms.irregular_finite_family_forms("sein", "Pres")
    assert family is not None
    assert family == {
        ("1", "Sing"): "bin",
        ("2", "Sing"): "bist",
        ("3", "Sing"): "ist",
        ("1", "Plur"): "sind",
        ("2", "Plur"): "seid",
        ("3", "Plur"): "sind",
    }


# ==============================================================================
# Cycle 3. Aux-selection and transitivity as lexical facts (AUX_SEIN_LEMMAS,
# TRANSITIVE_LEMMAS) -- deliberately disjoint sets used to disambiguate
# structurally identical "sein + past participle" constructions (Perfekt
# with sein vs Zustandspassiv).
# ==============================================================================


def test_aux_sein_lemmas_and_transitive_lemmas_are_disjoint() -> None:
    """A verb cannot both take "sein" as its Perfekt auxiliary (intransitive
    motion/change-of-state) and be passivizable with "werden"/"sein" (needs
    a direct object) -- if the two sets ever overlapped, the aux/participle
    consistency check the selectors rely on would stop being able to tell
    Perfekt-mit-sein and Zustandspassiv apart."""
    assert paradigms.AUX_SEIN_LEMMAS.isdisjoint(paradigms.TRANSITIVE_LEMMAS)


def test_aux_sein_lemmas_contains_verified_motion_and_change_of_state_verbs() -> None:
    assert "fahren" in paradigms.AUX_SEIN_LEMMAS
    assert "kommen" in paradigms.AUX_SEIN_LEMMAS
    assert "machen" not in paradigms.AUX_SEIN_LEMMAS


def test_aux_sein_lemmas_excludes_an_individually_unverified_separable_verb() -> None:
    """ "aufstehen"'s Partizip II lemma is unreliably lemmatised by
    de_core_news_sm ("aufgestanden" -> "aufgestehen", not "aufstehen") --
    deliberately excluded rather than guessed, unlike "aufwachen",
    "ankommen", "umziehen", and "weggehen", each of which was individually
    verified to lemmatise correctly."""
    assert "aufstehen" not in paradigms.AUX_SEIN_LEMMAS
    assert "aufwachen" in paradigms.AUX_SEIN_LEMMAS


def test_transitive_lemmas_contains_verified_passivizable_verbs() -> None:
    assert "reparieren" in paradigms.TRANSITIVE_LEMMAS
    assert "kaufen" in paradigms.TRANSITIVE_LEMMAS


# ==============================================================================
# Dative-reflexive verb argument structure (docs/audits/cycle-04-report.md's
# second finding): the closed list ``selectors._reflexive_case`` uses to
# decide verben_reflexiv_akk/_dat instead of an unscoped "is there an
# accusative object anywhere" guess.
# ==============================================================================


def test_dative_reflexive_verb_sets_are_disjoint() -> None:
    """ "helfen" needs no co-occurring object and every other verb in the
    combined list does -- a verb cannot honestly belong to both subsets at
    once, or ``selectors._reflexive_case`` would not know which rule to
    apply to it."""
    assert paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT.isdisjoint(
        paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT
    )


def test_dative_reflexive_verbs_combines_both_subsets() -> None:
    assert paradigms.DATIVE_REFLEXIVE_VERBS == (
        paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT | paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT
    )


def test_dative_reflexive_verbs_contains_the_reports_own_named_examples() -> None:
    """docs/audits/cycle-04-report.md names these explicitly as the fix for
    its second finding."""
    for lemma in ("vorstellen", "merken", "leisten", "ansehen"):
        assert lemma in paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT
    assert "helfen" in paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT


def test_dative_reflexive_verbs_excludes_the_reports_misfiled_accusative_verbs() -> None:
    """docs/audits/cycle-04-report.md's second finding: these three were
    the accusative-reflexive verbs a whole-sentence object scan wrongly
    promoted to Dative -- none belongs on either dative-reflexive list."""
    for lemma in ("freuen", "treffen", "ändern"):
        assert lemma not in paradigms.DATIVE_REFLEXIVE_VERBS
