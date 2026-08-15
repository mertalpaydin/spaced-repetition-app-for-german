"""Layer 2: Morphosyntactic consistency and grammatical agreement validator.

Every check below is driven by the *contents* of ``topic.morph_spec`` /
``topic.syntax_tags`` (UD-style feature values), never by ``topic.id``. A new
topic that sets ``morph_spec={"Case": "Dat"}`` and ``syntax_tags={"Pos":
"Prep"}`` gets the Wechselpräposition-direction check for free, without this
module knowing its id.

This module has no dependency on spaCy (not installed in this environment);
every check is a small, explicit, documented heuristic over a bounded,
general-purpose German word list. It is intentionally conservative: when a
signal is inconclusive (unknown noun gender, unrecognised verb ending,
ambiguous "sie"), the check is skipped rather than guessed, because a
missed defect is cheaper than a false rejection of a correct item.
"""

import re

from src.contracts import CandidateItem, ErrorTaxonomy, Topic
from src.taxonomy.facets import attributive_adjective_gender_candidates

# ---------------------------------------------------------------------------
# Small, general-purpose morphological tables. These describe the German
# language, not any one topic or fixture item.
# ---------------------------------------------------------------------------

# A modest common-noun gender lexicon, used only to *sharpen* agreement
# checks when the noun following a gap happens to be recognised. Unknown
# nouns fall back to case-form validity checks that do not need gender.
NOUN_GENDER: dict[str, str] = {
    # masculine
    "tisch": "masc",
    "boden": "masc",
    "stuhl": "masc",
    "schreibtisch": "masc",
    "garten": "masc",
    "hund": "masc",
    "park": "masc",
    "teppich": "masc",
    "schrank": "masc",
    "bahnhof": "masc",
    "vater": "masc",
    "mitarbeiter": "masc",
    "mann": "masc",
    "zug": "masc",
    "fernseher": "masc",
    "computer": "masc",
    "kaffee": "masc",
    "tee": "masc",
    "wein": "masc",
    "kapitän": "masc",
    # feminine
    "wand": "fem",
    "sonne": "fem",
    "frau": "fem",
    "schule": "fem",
    "tür": "fem",
    "tuer": "fem",
    "straße": "fem",
    "strasse": "fem",
    "lampe": "fem",
    "küche": "fem",
    "kueche": "fem",
    "haltestelle": "fem",
    "schwester": "fem",
    "blume": "fem",
    "suppe": "fem",
    "milch": "fem",
    "erfahrung": "fem",
    # neuter
    "haus": "neut",
    "kind": "neut",
    "bett": "neut",
    "auto": "neut",
    "fenster": "neut",
    "buch": "neut",
    "wasser": "neut",
}  # noqa: RUF012

# UD Gender value for each of this module's lowercase gender codes, used to
# compare against `src.taxonomy.facets.attributive_adjective_gender_candidates`
# (which speaks the UD "Masc"/"Fem"/"Neut" vocabulary, not this module's own).
_UD_GENDER: dict[str, str] = {"masc": "Masc", "fem": "Fem", "neut": "Neut"}  # noqa: RUF012

# Definite-article form per (Case, gender). Dative and Genitive collapse
# masculine/neuter to the same surface form, which is simply German fact,
# not a simplification of this table.
DEFINITE_FORMS: dict[str, dict[str, str]] = {
    "Nom": {"masc": "der", "fem": "die", "neut": "das"},
    "Acc": {"masc": "den", "fem": "die", "neut": "das"},
    "Dat": {"masc": "dem", "fem": "der", "neut": "dem"},
    "Gen": {"masc": "des", "fem": "der", "neut": "des"},
}  # noqa: RUF012

# Broad (gender-agnostic) validity sets, used when the following noun's
# gender is not recognised: any of these is *a* legitimate case-marked form
# for that case somewhere in the article/pronoun paradigm (definite,
# indefinite, possessive, negative).
CASE_FORM_FALLBACK: dict[str, set[str]] = {
    "Nom": {"der", "die", "das", "ein", "eine", "kein", "keine", "mein", "meine"},
    "Acc": {
        "den",
        "die",
        "das",
        "einen",
        "eine",
        "ein",
        "keinen",
        "keine",
        "kein",
        "meinen",
        "meine",
        "sein",
    },
    "Dat": {
        "dem",
        "der",
        "den",
        "einem",
        "einer",
        "keinem",
        "keiner",
        "meinem",
        "meiner",
        "seinem",
        "seiner",
        "ihrem",
    },
    "Gen": {"des", "der", "eines", "einer", "keines", "keiner", "meines", "meiner"},
}  # noqa: RUF012

# Wechselpräposition (two-way preposition) direction cues. This vocabulary is
# inherent to German, not to any specific topic; it is gated on
# ``confusion_group == "kasus_wechselpraeposition"`` (an architectural
# property of the taxonomy), never on ``topic.id``.
DIRECTIONAL_VERBS: frozenset[str] = frozenset(
    {"stellt", "stellen", "legt", "legen", "setzt", "setzen", "steckt", "stecken"}
)
STATIC_VERBS: frozenset[str] = frozenset(
    {
        "liegt",
        "liegen",
        "steht",
        "stehen",
        "sitzt",
        "sitzen",
        "bleibt",
        "bleiben",
        "hängt",
        "hängen",
    }
)

# Subject pronouns that unambiguously select a present-tense verb ending.
# Lowercase "sie" (she / they) and therefore also sentence-initial "Sie" are
# excluded deliberately: without more context, capitalisation alone cannot
# distinguish 3sg "sie" from 3pl "sie" from formal "Sie", so guessing an
# expected ending for either would risk a false rejection.
SUBJECT_ENDING: dict[str, str] = {
    "ich": "e",
    "du": "st",
    "er": "t",
    "es": "t",
    "wir": "en",
    "ihr": "t",
}  # noqa: RUF012

# High-frequency irregular verbs (sein, haben, werden, lassen, the modals),
# mapped surface form -> lemma. These do not follow the regular -e/-st/-t/-en
# present-tense suffix pattern reliably (e.g. "ist"/"bist" superficially look
# like a "-st" ending but are not person-marked the regular way), so
# ``verb_ending`` deliberately treats every form here as unclassifiable
# rather than guessing from its tail letters. This is general, bounded German
# vocabulary, not a fixture-specific list; also used by ``layer3_solver`` to
# recognise when two different surface forms are the same lexeme.
IRREGULAR_VERB_LEMMA: dict[str, str] = {
    "bin": "sein",
    "bist": "sein",
    "ist": "sein",
    "sind": "sein",
    "seid": "sein",
    "war": "sein",
    "warst": "sein",
    "waren": "sein",
    "wart": "sein",
    "wäre": "sein",
    "wärst": "sein",
    "wären": "sein",
    "wäret": "sein",
    "sei": "sein",
    "seien": "sein",
    "gewesen": "sein",
    "sein": "sein",
    "habe": "haben",
    "hast": "haben",
    "hat": "haben",
    "haben": "haben",
    "habt": "haben",
    "hatte": "haben",
    "hattest": "haben",
    "hatten": "haben",
    "hättet": "haben",
    "hätte": "haben",
    "hätten": "haben",
    "gehabt": "haben",
    "werde": "werden",
    "wirst": "werden",
    "wird": "werden",
    "werden": "werden",
    "werdet": "werden",
    "wurde": "werden",
    "wurdest": "werden",
    "wurden": "werden",
    "würde": "werden",
    "würdest": "werden",
    "würden": "werden",
    "geworden": "werden",
    "worden": "werden",
    "lasse": "lassen",
    "lässt": "lassen",
    "lasst": "lassen",
    "lassen": "lassen",
    "ließ": "lassen",
    "ließe": "lassen",
    "gelassen": "lassen",
    "kann": "können",
    "kannst": "können",
    "können": "können",
    "könnt": "können",
    "konnte": "können",
    "könnte": "können",
    "gekonnt": "können",
    "muss": "müssen",
    "musst": "müssen",
    "müssen": "müssen",
    "müsst": "müssen",
    "musste": "müssen",
    "müsste": "müssen",
    "gemusst": "müssen",
    "will": "wollen",
    "willst": "wollen",
    "wollen": "wollen",
    "wollt": "wollen",
    "wollte": "wollen",
    "gewollt": "wollen",
    "darf": "dürfen",
    "darfst": "dürfen",
    "dürfen": "dürfen",
    "dürft": "dürfen",
    "durfte": "dürfen",
    "dürfte": "dürfen",
    "gedurft": "dürfen",
    "soll": "sollen",
    "sollst": "sollen",
    "sollen": "sollen",
    "sollt": "sollen",
    "sollte": "sollen",
    "gesollt": "sollen",
    "mag": "mögen",
    "magst": "mögen",
    "mögen": "mögen",
    "mögt": "mögen",
    "mochte": "mögen",
    "möchte": "mögen",
    "gemocht": "mögen",
}  # noqa: RUF012


def verb_ending(word: str) -> str | None:
    """Classify a present-tense verb form's ending, or ``None`` if unrecognised.

    Irregular verbs are excluded up front (see ``IRREGULAR_VERB_LEMMA``):
    their forms do not reliably carry the regular suffix, and guessing from
    surface letters alone (e.g. "ist" superficially ends like a "-st" form)
    produces false agreement matches.
    """
    w = word.lower()
    if w in IRREGULAR_VERB_LEMMA:
        return None
    if w.endswith("en"):
        return "en"
    if w.endswith("st"):
        return "st"
    if w.endswith("t"):
        return "t"
    if w.endswith("e"):
        return "e"
    return None


class Layer2MorphologyValidator:
    """Validates that candidate completions satisfy German morphosyntactic rules."""

    def validate(
        self, item: CandidateItem, topic: Topic | None = None
    ) -> tuple[bool, str | None, ErrorTaxonomy | None]:
        """Validate morphosyntactic consistency of the completed sentence."""
        # 1. Check insertion sanity
        filled_sentence = item.prompt.replace("___", item.proposed_answer)
        if "___" in filled_sentence:
            return (
                False,
                "Unfilled gaps remaining after answer insertion.",
                "structural_malformation",
            )

        words = filled_sentence.split()
        if not words:
            return False, "Empty completed sentence.", "structural_malformation"

        # 2. Check capitalisation / casing consistency
        if not words[0][0].isupper():
            return (
                False,
                "Completed sentence does not begin with an uppercase letter.",
                "structural_malformation",
            )

        # 3. Check terminal punctuation (. ! ?)
        if filled_sentence.rstrip()[-1] not in ".!?\"'":
            return (
                False,
                "Completed sentence lacks terminal punctuation.",
                "structural_malformation",
            )

        if topic and topic.morph_spec:
            case_failure = self._check_case_agreement(item, topic)
            if case_failure:
                return False, case_failure[0], case_failure[1]

            failure = self._check_subject_verb_agreement(item, topic)
            if failure:
                return False, failure, "morphosyntactic_error"

        if topic and topic.syntax_tags.get("Separable"):
            ans_clean = item.proposed_answer.strip().lower()
            if not re.search(r"\b(an|auf|aus|ein|ab|mit|vor|zu|fern)\b", ans_clean):
                ans = item.proposed_answer
                return (
                    False,
                    f"Proposed answer '{ans}' lacks separable prefix.",
                    "morphosyntactic_error",
                )

        # Adjective declension (weak/mixed/strong) is gated on
        # syntax_tags["Declension"], never on morph_spec: adjektivdeklination_nullartikel
        # deliberately sets morph_spec={} (zero article has no UD FEATS
        # equivalent, see its taxonomy.yaml entry), so it would otherwise be
        # skipped entirely by the morph_spec gate above -- exactly the
        # coverage hole docs/audits/stage-04-pilot-2026-08-14.md identified.
        if topic:
            decl_failure = self._check_adjective_declension(item, topic)
            if decl_failure:
                return False, decl_failure[0], decl_failure[1]

        return True, None, None

    @staticmethod
    def _next_word_after_gap(prompt: str) -> str | None:
        match = re.search(r"___\s+([A-ZÄÖÜa-zäöüß]+)", prompt)
        return match.group(1).lower() if match else None

    def _check_case_agreement(
        self, item: CandidateItem, topic: Topic
    ) -> tuple[str, ErrorTaxonomy] | None:
        """General Case-driven agreement check, keyed off ``morph_spec['Case']``.

        Two independent sub-checks, both gated by generic taxonomy properties
        rather than a specific topic id:

        - Wechselpräposition direction: gated on
          ``confusion_group == "kasus_wechselpraeposition"``.
        - Definite-article gender/case agreement: gated on
          ``syntax_tags["ArtType"] == "Def"`` (so it never fires for personal
          pronoun, possessive, or indefinite-article topics, which use a
          disjoint form paradigm).

        The two possible failure categories are distinguished by whether a
        second sentence element is involved: a mismatch between the verb and
        the declared case, or between a determiner and its noun's gender, is
        an *agreement* failure between two elements (``morphosyntactic_error``);
        an answer that is simply not a recognised form of the required case at
        all, with no such second element in play, is a defect in the answer's
        own morphology (``morphological_defect``).
        """
        morph_spec = topic.morph_spec
        if not morph_spec:
            return None
        case_val = morph_spec.get("Case")
        if not isinstance(case_val, str) or case_val not in ("Nom", "Acc", "Dat", "Gen"):
            return None

        # This check's tables (DEFINITE_FORMS / CASE_FORM_FALLBACK) model the
        # article/determiner paradigm only. Personal pronouns
        # (syntax_tags Pos=="Pron") and reflexive pronouns (morph_spec
        # Reflex) inflect on a completely disjoint paradigm ("mich", "ihm",
        # "sich", ...) and are out of scope here, not merely absent from the
        # word lists -- misapplying an article-form check to them would
        # reject every correct pronoun answer.
        if (topic.syntax_tags.get("Pos") == "Pron") or morph_spec.get("Reflex"):
            return None

        ans_clean = item.proposed_answer.strip().lower()
        prompt_lower = item.prompt.lower()
        next_word = self._next_word_after_gap(item.prompt)
        gender = NOUN_GENDER.get(next_word) if next_word else None

        effective_case = case_val
        if topic.confusion_group == "kasus_wechselpraeposition" and case_val in ("Dat", "Acc"):
            tokens = set(re.findall(r"\b\w+\b", prompt_lower))
            has_directional = bool(tokens & DIRECTIONAL_VERBS)
            has_static = bool(tokens & STATIC_VERBS)
            if has_directional and not has_static:
                effective_case = "Acc"
            elif has_static and not has_directional:
                effective_case = "Dat"
            # both or neither present: no reliable verb cue, trust morph_spec

            if effective_case != case_val:
                verb_word = next(
                    w
                    for w in re.findall(r"\b\w+\b", item.prompt)
                    if w.lower() in (DIRECTIONAL_VERBS if has_directional else STATIC_VERBS)
                )
                kind = "Directional" if has_directional else "Static"
                needs = "Akkusativ" if effective_case == "Acc" else "Dativ"
                has = "Dativ" if case_val == "Dat" else "Akkusativ"
                return (
                    f"{kind} verb '{verb_word}' requires {needs}, not {has} "
                    f"(answer '{item.proposed_answer}').",
                    "morphosyntactic_error",
                )

        if gender and next_word:
            expected = DEFINITE_FORMS[effective_case][gender]
            # For Wechselpräposition / fixed-preposition topics the answer may
            # legitimately be an ein-word / possessive form, not just the bare
            # definite article; only enforce the exact definite form when the
            # topic is explicitly about the definite article paradigm.
            if topic.syntax_tags.get("ArtType") == "Def":
                if ans_clean != expected:
                    return (
                        f"'{next_word.capitalize()}' is {gender}; expected "
                        f"'{expected}' ({effective_case}), got '{item.proposed_answer}'.",
                        "morphosyntactic_error",
                    )
                return None
            if topic.confusion_group == "kasus_wechselpraeposition":
                fem_forms = {"der", "einer", "meiner", "seiner", "ihrer", "unserer"}
                masc_neut_forms = {
                    "dem",
                    "einem",
                    "meinem",
                    "seinem",
                    "ihrem",
                    "unserem",
                    "den",
                    "einen",
                    "meinen",
                    "seinen",
                    "ihren",
                    "unseren",
                    "das",
                }
                if effective_case == "Dat":
                    valid = (
                        fem_forms
                        if gender == "fem"
                        else masc_neut_forms
                        & {
                            "dem",
                            "einem",
                            "meinem",
                            "seinem",
                            "ihrem",
                            "unserem",
                        }
                    )
                else:  # Acc
                    valid = (
                        {"die", "eine", "keine", "meine", "seine"}
                        if gender == "fem"
                        else (
                            {"das", "ein", "kein", "mein", "sein"}
                            if gender == "neut"
                            else {"den", "einen", "meinen", "seinen", "ihren", "unseren"}
                        )
                    )
                if ans_clean not in valid:
                    return (
                        f"Proposed answer '{item.proposed_answer}' is not a valid "
                        f"{effective_case} form for {gender} noun '{next_word}'.",
                        "morphosyntactic_error",
                    )
                return None

        # No recognised gender: fall back to a broad case-validity check. With
        # no noun to agree with, this is a defect in the answer's own
        # morphology, not an agreement mismatch between two elements.
        fallback = CASE_FORM_FALLBACK.get(effective_case, set())
        if fallback and ans_clean not in fallback:
            return (
                f"Proposed answer '{item.proposed_answer}' is not a recognised "
                f"{effective_case} form.",
                "morphological_defect",
            )
        return None

    def _check_subject_verb_agreement(self, item: CandidateItem, topic: Topic) -> str | None:
        """General present-tense subject-verb agreement, keyed off ``morph_spec['Tense']``.

        Restricted to plain full verbs: auxiliaries, modals, and separable
        verbs are excluded via ``syntax_tags['VerbType']`` /
        ``syntax_tags['Separable']`` because their present-tense paradigms are
        irregular or the gap is a detached prefix rather than the finite verb,
        and a regular -e/-st/-t/-en ending check would misfire on them.
        """
        if not topic.morph_spec or topic.morph_spec.get("Tense") != "Pres":
            return None
        verb_type = topic.syntax_tags.get("VerbType") if topic.syntax_tags else None
        if verb_type in ("Modal", "Aux", "ModalEpistemic"):
            return None
        if topic.syntax_tags.get("Separable"):
            return None

        match = re.search(r"\b(ich|du|er|es|wir|ihr)\s+___", item.prompt, flags=re.IGNORECASE)
        if not match:
            return None
        subject = match.group(1).lower()
        expected = SUBJECT_ENDING[subject]
        actual = verb_ending(item.proposed_answer)
        if actual is None or actual == expected:
            return None
        return (
            f"Subject '{match.group(1)}' requires the '-{expected}' present-tense "
            f"ending, but '{item.proposed_answer}' has '-{actual}'."
        )

    def _check_adjective_declension(
        self, item: CandidateItem, topic: Topic
    ) -> tuple[str, ErrorTaxonomy] | None:
        """Attributive adjective/participle gender agreement for weak, mixed and
        strong declension topics (``syntax_tags["Declension"]``).

        Reuses ``facets.attributive_adjective_gender_candidates``, which
        already does the determiner-context paradigm selection for facet
        derivation -- the "paradigm tables exist; they are just not used for
        verification" gap in docs/audits/stage-04-pilot-2026-08-14.md. Only
        checks gender, not case: none of the three declension topics fix
        ``Case`` in ``morph_spec`` (case is itself a stage 6 facet dimension
        for all three), so there is no expected case to check against, but a
        wrong gender ending is unambiguous regardless of which case the
        sentence actually calls for -- exactly the class of defect the pilot
        audit's items 14 and 15 were (a masculine and a feminine noun each
        given a neuter- or masculine-only ending).

        Two independent sub-checks, in order of confidence:

        1. Is the answer's ending a recognised ending *at all* in the
           declension paradigm the gap's own determiner context selects
           (weak/mixed/strong)? This needs no noun-gender lookup -- German
           attributive endings are a closed 5-member set (-e, -en, -er, -es,
           -em) and each declension paradigm only admits a subset of them in
           any given cell, so an ending absent from the whole paradigm is a
           defect regardless of what noun follows (e.g. "-es" never occurs
           after a definite article under any circumstance; only the article
           itself carries that signal).
        2. If the ending *is* a recognised member of the paradigm, does its
           gender agree with the noun after the gap? This only fires when
           that noun is in ``NOUN_GENDER``: an unrecognised noun means no
           known gender to check against, so it is skipped rather than
           guessed, matching this module's stated philosophy throughout.
        """
        declension = topic.syntax_tags.get("Declension") if topic.syntax_tags else None
        if declension not in ("Weak", "Mixed", "Strong"):
            return None

        ans_clean = item.proposed_answer.strip().lower()
        candidate_genders = attributive_adjective_gender_candidates(item.prompt, ans_clean)
        if not candidate_genders:
            return (
                f"'{item.proposed_answer}' is not a valid {declension.lower()}-declension "
                "adjective ending for this context.",
                "morphosyntactic_error",
            )

        next_word = self._next_word_after_gap(item.prompt)
        if not next_word:
            return None
        gender = NOUN_GENDER.get(next_word)
        if not gender:
            return None

        ud_gender = _UD_GENDER[gender]
        if ud_gender not in candidate_genders:
            return (
                f"'{next_word.capitalize()}' is {gender}; '{item.proposed_answer}' is not "
                f"a valid {declension.lower()}-declension ending for that gender.",
                "morphosyntactic_error",
            )
        return None
