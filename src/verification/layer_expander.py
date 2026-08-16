"""Answer-set expander: cheap contraction expansion plus the model-backed
semantic/collocation check docs/audits/stage-00-quota.md's routing table
calls "Answer-Set Expansion (Stage 4)".

Two independent responsibilities live here, both about the same question --
"is the accepted-answers set for this item right?" -- from opposite ends:

- ``expand_answers`` is free and deterministic: known contractions and
  sentence-initial capitalisation, so an equally-correct variant of the
  proposed answer is not rejected as wrong at review or grading time.
- ``verify_semantic_validity`` is the one model-backed layer in the chain
  (``gemini-3.7-flash`` / ``MODEL_VERIFY``, per the routing table), reserved
  for exactly the defect class no regex or closed-class table can catch:
  wrong connectors, broken collocations, hallucinated tokens, and carrier
  sentences that are structurally fine but semantically incoherent. See
  docs/audits/stage-04-pilot-2026-08-14.md, whose items 34, 35, 39, 40, 41,
  43 and 44 are all in this class -- the audit's finding was that this class
  accounted for the largest share of defects reaching acceptance specifically
  because this layer existed but was never wired into
  ``src.verification.pipeline.VerificationPipeline``.
"""

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MODEL_VERIFY, BankItem, CandidateItem, Topic

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient

# Surface forms whose Konjunktiv II reading is unambiguous -- no legitimate
# Indikativ/Präteritum reading exists for the same spelling (unlike
# "wollte"/"sollte", which are genuinely ambiguous between preterite
# indicative and Konjunktiv II for weak-conjugation modals, and are
# deliberately left out rather than guessed). Used to give a Konjunktiv II
# form a distinct identity from its own lemma's indicative forms: "würden"
# (unambiguously subjunctive/conditional) must not be treated as the same
# "form" as "werden" (indicative) for a topic testing Indikativ Futur I,
# even though both share the lemma "werden" in
# ``layer2_morphology.IRREGULAR_VERB_LEMMA``.
_KONJUNKTIV_II_UNAMBIGUOUS_FORMS: frozenset[str] = frozenset(
    {
        "wäre",
        "wärst",
        "wären",
        "wäret",
        "hätte",
        "hättest",
        "hätten",
        "hättet",
        "würde",
        "würdest",
        "würden",
        "würdet",
        "könnte",
        "könntest",
        "könnten",
        "könntet",
        "müsste",
        "müsstest",
        "müssten",
        "müsstet",
        "dürfte",
        "dürftest",
        "dürften",
        "dürftet",
    }
)


# Universal Dependencies feature keys that mark a topic as testing a verb's
# inflectional category. Shared by ``check_ambiguity`` and
# ``filter_alternatives_by_target_form`` -- factored out once rather than
# repeated as an inline tuple literal in both places.
_VERB_MORPH_KEYS: tuple[str, ...] = ("Tense", "Mood", "Voice", "Aspect")

# ``IRREGULAR_VERB_LEMMA`` (src/verification/layer2_morphology.py) omits
# "hättest" -- every other Konjunktiv II form in
# _KONJUNKTIV_II_UNAMBIGUOUS_FORMS above resolves through it, this one form
# does not (docs/audits/stage-04-a2-pilot-audit.md item 41: "hättest" was
# one of three accepted answers for "Wenn du Zeit ___, helfen wir dir.",
# alongside the indicative "hast" and "findest" -- a genuine mood mismatch
# the chain could not see because the lemma lookup itself failed first).
# Not layer2_morphology.py's file to fix here (outside this fix's
# ownership); patched locally instead.
_SUPPLEMENTARY_VERB_LEMMA: dict[str, str] = {"hättest": "haben"}

# Minimum acceptable UNK marker string, matching facets.py's own "Unk" so a
# reason string built from either module reads consistently.
_UNK = "Unk"

# Possessive determiner stems (see ``src.taxonomy.facets._EIN_WORD_STEMS``,
# the same closed word class) and the (Person, Number) pair(s) of possessor
# each one asserts. "ihr" is genuinely ambiguous on its own (both "her" --
# a single 3rd-person possessor -- and "their" -- a plural 3rd-person
# possessor -- share the surface form), so it carries both candidate pairs;
# every other stem is unambiguous. Longest-stem-first order matters: "euer"
# must be tried before its own contracted stem "eur" (both map to the same
# pair here, so the order is for correctness of the general pattern, not
# because the two answers differ).
_POSSESSIVE_STEM_PERSON_NUMBER: dict[str, frozenset[tuple[str, str]]] = {
    "unser": frozenset({("1", "Plur")}),
    "euer": frozenset({("2", "Plur")}),
    "mein": frozenset({("1", "Sing")}),
    "dein": frozenset({("2", "Sing")}),
    "sein": frozenset({("3", "Sing")}),
    "ihr": frozenset({("3", "Sing"), ("3", "Plur")}),
    "eur": frozenset({("2", "Plur")}),
}
_POSSESSIVE_STEMS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(_POSSESSIVE_STEM_PERSON_NUMBER, key=len, reverse=True)
)


# Pronoun-class closed tables for ``check_pronoun_class_consistency`` (Task
# 5, cycle-2 report). Deliberately LOCAL to this module rather than reused
# from ``src.taxonomy.facets.determiner_art_type``: that function resolves
# "der"/"die"/"das" as DEFINITE ARTICLES (correct for a determiner-choice
# topic, where they precede a noun), while this table resolves the SAME
# surface forms as DEMONSTRATIVE PRONOUNS (correct for a pronoun topic,
# where they stand in for a noun) -- two different grammatical facts about
# one spelling, and conflating them would misfire on every article-choice
# topic. Personal-pronoun forms include the oblique (Akk/Dat) cases too,
# since a pronoun topic's accepted set is answered in whichever case that
# topic tests, not only Nominative.
_INTERROGATIVE_PRONOUN_FORMS: frozenset[str] = frozenset({"wer", "wen", "wem", "wessen", "was"})

_PERSONAL_PRONOUN_FORMS: frozenset[str] = frozenset(
    {
        "ich", "mich", "mir",
        "du", "dich", "dir",
        "er", "ihn", "ihm",
        "sie", "ihr", "ihnen",
        "es",
        "wir", "uns",
        "euch",
    }
)  # fmt: skip

# "der"/"die"/"das" and the demonstrative-declension forms, used
# PRONOMINALLY (standing in for a noun, e.g. "___ ist mein Freund.", not
# preceding one, e.g. "___ Mann ist mein Freund."). See this section's own
# comment above on why this duplicates rather than reuses
# ``facets._DEMONSTRATIVE_FORMS``/``determiner_art_type``'s article table.
_DEMONSTRATIVE_PRONOUN_FORMS: frozenset[str] = frozenset(
    {
        "der", "die", "das", "den", "dem", "dessen", "deren", "denen",
        "dieser", "diese", "dieses", "diesen", "diesem",
        "jener", "jene", "jenes", "jenen", "jenem",
    }
)  # fmt: skip


def _pronoun_class(answer: str) -> str | None:
    """Coarse Personal/Demonstrative/Interrogative pronoun class for
    ``answer``, or ``None`` if it matches none of these closed tables (e.g.
    an indefinite pronoun like "jemand", or a word that is not
    pronoun-shaped at all) -- "no opinion", the same "skip rather than
    guess" posture as every other closed-class lookup in this module.
    Checked in Interrogative, Personal, Demonstrative order; the three
    tables above are mutually disjoint by construction, so order does not
    change the result, only readability.
    """
    lower = answer.strip().lower()
    if lower in _INTERROGATIVE_PRONOUN_FORMS:
        return "Interrog"
    if lower in _PERSONAL_PRONOUN_FORMS:
        return "Prs"
    if lower in _DEMONSTRATIVE_PRONOUN_FORMS:
        return "Dem"
    return None


def _possessive_person_number(answer: str) -> frozenset[tuple[str, str]] | None:
    """The set of (Person, Number) pairs ``answer`` asserts about its
    possessor, if ``answer`` is a recognisable possessive-determiner form,
    else ``None`` (not a possessive at all, e.g. a demonstrative or definite
    article -- callers must treat this the same as "no opinion", not as a
    mismatch)."""
    lower = answer.strip().lower()
    for stem in _POSSESSIVE_STEMS_LONGEST_FIRST:
        if lower.startswith(stem):
            return _POSSESSIVE_STEM_PERSON_NUMBER[stem]
    return None


def _lemma_guess(word: str) -> str | None:
    """Best-effort canonical lemma for ``word``, using
    ``src.lexicon.lemmatizer.lemma_candidates`` -- the shortest candidate
    that looks like an infinitive (ends ``-en``, at least 4 characters).
    Used only to compare whether two REGULAR verb forms (outside the closed
    ``IRREGULAR_VERB_LEMMA`` table, which every function in this module
    checks first) share a lemma, so exact canonical correctness does not
    matter, only that the same lexeme produces the same guess and a
    different lexeme does not collide with it -- the shortest reconstructed
    infinitive is the one least likely to carry a stray suffix fragment that
    would make two forms of the SAME verb disagree with each other (see the
    fix's own notes: "wohnt"/"wohnte" must land on the same guess).
    Returns ``None`` if no such candidate exists at all.
    """
    from src.lexicon.lemmatizer import lemma_candidates

    infinitive_shaped = [c for c in lemma_candidates(word) if c.endswith("en") and len(c) >= 4]
    return min(infinitive_shaped, key=len) if infinitive_shaped else None


def _answer_first_token(answer: str) -> str:
    """The first whitespace-separated token of ``answer``, or ``answer``
    itself if it is already a single token (or empty). Multi-word answers
    ("beginnen wird", "mein Gesicht") carry their inflectional signal on
    their first word; ``tagger.tag_answer`` already aligns to the same first
    token internally (see its own docstring), so every helper in this module
    that needs a single word to look up mirrors that choice."""
    stripped = answer.strip()
    return stripped.split()[0] if stripped else stripped


def _answer_is_verb(answer: str, prompt: str) -> bool:
    """Whether ``answer`` is a finite verb or auxiliary in ``prompt``'s
    context, per the spaCy tagger. Used to widen the verb-identity check
    (below) to topics whose ``morph_spec`` does not declare
    Tense/Mood/Voice/Aspect at all -- structural topics like
    ``nebensatz_wenn``/``nebensatz_indirekte_frage`` deliberately leave
    ``morph_spec`` empty because they test subordinate-clause verb-FINAL
    word order, not tense (see ``data/taxonomy.yaml``) -- but an
    accepted-answer set that silently spans multiple tenses/moods for the
    SAME verb slot is still a defect regardless of what the topic declares
    itself to test (docs/audits/stage-04-a2-pilot-audit.md items 37, 38, 41).
    ``False`` (not just unresolved) whenever the tagger has nothing to say,
    so this never widens a check the tagger cannot support."""
    from src.taxonomy import tagger as _tagger

    tagged = _tagger.tag_answer(prompt, answer)
    return tagged is not None and tagged.pos in ("VERB", "AUX")


def _answer_pos(answer: str, prompt: str) -> str | None:
    """The spaCy coarse POS tag for ``answer`` in ``prompt``'s context, or
    ``None`` if the tagger has nothing to say (no model, no gap, no
    alignment) -- callers must treat ``None`` as "cannot compare", never as
    a mismatch."""
    from src.taxonomy import tagger as _tagger

    tagged = _tagger.tag_answer(prompt, answer)
    return tagged.pos if tagged is not None else None


# Auxiliary/copula lemmas (Task 3, cycle-2 report): the closed-table lemma
# lookup below collapses EVERY finite form of "sein"/"haben"/"werden" onto
# one bare lemma key regardless of Tense/Mood/Person/Number, which is right
# for a lexeme-identity check (is this the same VERB) but wrong for these
# three specifically, because they are exactly the verbs a topic is most
# likely to test tense/mood/person/number ON: "sei"/"seid" (2sg/2pl
# imperative), "sind"/"waren" (Perfekt- vs Plusquamperfekt-tense auxiliary)
# and "ist"/"war" (present- vs past-tense copula/passive auxiliary) all
# share the bare lemma "sein" and so, before this fix, all compared equal.
_AUX_COPULA_LEMMAS: frozenset[str] = frozenset({"sein", "haben", "werden"})


def _aux_copula_form_key(answer: str, prompt: str, lemma: str) -> str | None:
    """Explicit Tense/Mood/Person/Number identity for an auxiliary or
    copula answer, read off ``src.taxonomy.tagger.tag_answer`` rather than
    the derived facet string (facets.py decodes ``accepted_answers[0]``
    alone and was never meant to compare two candidate answers against each
    other -- see its own module docstring). This is what actually
    distinguishes "Sei" (2sg imperative) from "Seid" (2pl imperative),
    "sind" (Perfekt, Tense=Pres) from "waren" (Plusquamperfekt, Tense=Past),
    and "ist" (Tense=Pres) from "war" (Tense=Past), none of which the bare
    lemma key below can tell apart.

    Returns ``None`` when the tagger has nothing to say (no model, no gap,
    no alignment): the caller falls back to the plain lemma key, the same
    fail-open "skip rather than guess" posture as every other tagger-backed
    check in this module.
    """
    from src.taxonomy import tagger as _tagger

    tagged = _tagger.tag_answer(prompt, answer)
    if tagged is None:
        return None
    tense = tagged.feats.get("Tense", _UNK)
    mood = tagged.feats.get("Mood", _UNK)
    person = tagged.feats.get("Person", _UNK)
    number = tagged.feats.get("Number", _UNK)
    return f"{lemma}:{tense}:{mood}:{person}:{number}"


def _verb_form_key(answer: str, prompt: str | None = None) -> str | None:
    """The verb "form" ``check_ambiguity``/``filter_alternatives_by_target_form``
    compare candidates on: the lemma from ``IRREGULAR_VERB_LEMMA`` (plus the
    small local ``_SUPPLEMENTARY_VERB_LEMMA`` patch), suffixed ``:Sub`` when
    the surface form is unambiguously Konjunktiv II. ``None`` if the lemma
    cannot be resolved at all through that closed table AND no ``prompt`` is
    given to fall back on -- the signal this codebase's "skip rather than
    guess" philosophy treats as "no opinion", not as a mismatch.

    Task 3 (cycle-2 report): when the resolved lemma is an auxiliary or
    copula (``sein``/``haben``/``werden``) AND ``prompt`` is available, the
    bare lemma is not the whole identity -- ``_aux_copula_form_key`` reads
    Tense/Mood/Person/Number directly off the tagger first, and only the
    plain lemma (below) is used when the tagger cannot resolve anything at
    all (no model, no gap) or ``prompt`` was not given, exactly the
    fail-open behaviour this module's Konjunktiv-unambiguous-forms special
    case already relied on for the no-tagger case.

    When ``prompt`` IS given and the closed table misses (a regular verb --
    "wohnen", "kochen", "finden" have no irregular stem and are never in
    that table), this falls back to a tagger-and-lemmatizer-derived key:
    ``{lemma_guess}:Sub`` if the tagger reports Konjunktiv II, else
    ``{lemma_guess}:{Tense}`` (``Tense`` itself ``"Unk"`` if the tagger
    cannot resolve it, or reports nothing for a non-finite form -- e.g. the
    infinitive half of a periphrastic future like "beginnen wird", which
    legitimately differs in Tense identity from a finite present-tense
    reference). The lemma guess does not require the tagger to have
    correctly recognised the word as a verb at all (docs/audits/
    stage-04-a2-pilot-audit.md item 41: the small spaCy model mis-tags
    "findest" as an adverb in isolation) -- once the caller has already
    established via ``_answer_is_verb`` that this IS a verb-form comparison
    (checked once, against the reference answer, not per-candidate), a
    candidate that fails to independently re-confirm as a verb should still
    be compared on its lemma, not silently exempted from the check.
    """
    from src.verification.layer2_morphology import IRREGULAR_VERB_LEMMA

    lower = answer.strip().lower()
    lemma = IRREGULAR_VERB_LEMMA.get(lower) or _SUPPLEMENTARY_VERB_LEMMA.get(lower)
    if lemma is not None:
        if lemma in _AUX_COPULA_LEMMAS and prompt is not None:
            tag_key = _aux_copula_form_key(answer, prompt, lemma)
            if tag_key is not None:
                return tag_key
        return f"{lemma}:Sub" if lower in _KONJUNKTIV_II_UNAMBIGUOUS_FORMS else lemma

    if prompt is None:
        return None

    lemma_guess = _lemma_guess(_answer_first_token(answer))
    if lemma_guess is None:
        return None

    from src.taxonomy import tagger as _tagger

    tagged = _tagger.tag_answer(prompt, answer)
    if tagged is not None and tagged.feats.get("Mood") == "Sub":
        return f"{lemma_guess}:Sub"
    tense = tagged.feats.get("Tense", _UNK) if tagged is not None else _UNK
    return f"{lemma_guess}:{tense}"


class SemanticVerificationResult(BaseModel):
    """Verdict from the model-backed semantic/collocation check.

    ``valid=True`` with no ``additional_accepted_answers`` is also what a
    caller gets when the check could not run at all (no client configured,
    budget ceiling reached, unparseable response): a missing verdict must
    never reject an item that already passed every cheaper layer, so absence
    of signal degrades to "accept", never to "reject" -- the same fail-open
    posture ``ProductionGrader`` uses for its own model call.
    """

    model_config = ConfigDict(frozen=True)
    valid: bool = True
    reason: str | None = None
    additional_accepted_answers: list[str] = Field(default_factory=list)


class AnswerSetExpander:
    """Expands valid German grammatical variants and, when an LLM client is
    supplied, runs the model-backed semantic/collocation check."""

    CONTRACTION_MAP: dict[str, list[str]] = {
        "ans": ["an das"],
        "an das": ["ans"],
        "aufs": ["auf das"],
        "auf das": ["aufs"],
        "beim": ["bei dem"],
        "bei dem": ["beim"],
        "im": ["in dem"],
        "in dem": ["im"],
        "ins": ["in das"],
        "in das": ["ins"],
        "vom": ["von dem"],
        "von dem": ["vom"],
        "zum": ["zu dem"],
        "zu dem": ["zum"],
        "zur": ["zu der"],
        "zu der": ["zur"],
    }

    # Fallback for topics whose target feature has no morphological decoder
    # in this codebase (e.g. comparative/superlative degree) -- see
    # ``check_ambiguity``. Kept generous enough that it only fires on
    # genuinely wide answer sets, never on ordinary contraction-expansion
    # synonyms; topics with a real form decoder never reach this fallback.
    AMBIGUITY_COUNT_THRESHOLD: int = 5

    SYSTEM_PROMPT = (
        "Du bist ein erfahrener Deutschlehrer und prüfst Lückentext-Aufgaben "
        "gegen, nachdem sie bereits automatisch auf Struktur, Morphologie und "
        "Mehrdeutigkeit geprüft wurden. Deine einzige Aufgabe: beurteile, ob "
        "der VOLLSTÄNDIGE Satz (Lücke durch die vorgeschlagene Antwort "
        "ersetzt) für einen Muttersprachler semantisch kohärent, idiomatisch "
        "und inhaltlich sinnvoll ist -- und liste jede weitere Antwort, die "
        "ebenfalls grammatisch und inhaltlich korrekt wäre.\n\n"
        "Typische Fehler, auf die du achten musst: falsche Konnektoren "
        "(z.B. 'demnach' statt 'deshalb' oder 'folglich'), falsche "
        "Kollokationen (z.B. 'Kritik auf sich nehmen' statt 'auf sich "
        "ziehen'), erfundene oder unpassende Wörter, ungrammatische "
        "Konstruktionen, und inhaltlich widersprüchliche oder unlogische "
        "Sätze.\n\n"
        "Antworte ausschließlich im JSON-Format, ohne Markdown-Codeblock:\n"
        '{"valid": true, "reason": null, "additional_accepted_answers": []}\n'
        'Bei einem Fehler: {"valid": false, "reason": "kurze Begründung auf '
        'Deutsch", "additional_accepted_answers": []}'
    )

    @classmethod
    def expand_answers(cls, item: CandidateItem) -> list[str]:
        """Expand proposed answer into deduplicated accepted answers set."""
        accepted = [item.proposed_answer]
        raw_ans = item.proposed_answer.strip()

        # 1. Contraction expansion
        lower_ans = raw_ans.lower()
        if lower_ans in cls.CONTRACTION_MAP:
            for variant in cls.CONTRACTION_MAP[lower_ans]:
                if raw_ans[0].isupper():
                    expanded_v = variant.capitalize()
                else:
                    expanded_v = variant
                if expanded_v not in accepted:
                    accepted.append(expanded_v)

        # 2. Sentence initial position check
        if item.prompt.strip().startswith("___"):
            capitalized = raw_ans.capitalize()
            if capitalized not in accepted:
                accepted.append(capitalized)

        return accepted

    @classmethod
    def check_ambiguity(
        cls,
        accepted_answers: list[str],
        topic: Topic,
        item: CandidateItem | None = None,
    ) -> str | None:
        """Return an ``ambiguity`` rejection reason if ``accepted_answers``
        spans more than one distinct form of ``topic``'s target feature, or
        ``None`` if the set is internally consistent (or the form cannot be
        derived at all, in which case there is nothing to check against).

        docs/audits/stage-04-pilot-2026-08-15.md fix 1 /
        02-content-pipeline.md: "Threshold on distinct forms, not on answer
        count." Ten answers are fine when they are ten lexemes carrying one
        form (``kasus_genitiv_formen``: des, eines, meines... all genitive);
        two answers are fatal when they are two forms (``futur_i``: 'werden'
        vs 'können', a future auxiliary vs a modal).

        ``item`` is optional and used only to widen the verb-identity signal
        below to topics that do not declare Tense/Mood/Voice/Aspect at all
        (see ``_answer_is_verb``); every caller that omits it (every direct
        unit test of this method, predating that widening) gets exactly the
        original, narrower behaviour.

        Three identity signals, each gated on what the topic actually
        declares itself to be testing -- checking a signal the topic does
        not care about would reject good items. ``kasus_genitiv_formen``
        legitimately spans both definite-article and ein-word genitive forms
        (it has no ``ArtType`` tag: Case is its only target feature), so the
        determiner-Definite check below must not fire for it, only for
        topics that actually declare ``ArtType``:

        - **Verb-category topics** (``morph_spec`` fixes Tense/Mood/Voice/
          Aspect, ``syntax_tags['VerbType']`` is set, OR -- docs/audits/
          stage-04-a2-pilot-audit.md items 37, 38, 41 -- ``item`` is given
          and its own proposed answer is independently recognised as a verb
          by the tagger, which is how a structural topic like
          ``nebensatz_wenn`` that deliberately declares no verb morph_spec
          still gets its verb slot checked): the target feature is the
          LEMMA (the specific auxiliary/modal/verb) plus indicative-vs-
          unambiguously-Konjunktiv-II and, for the tagger fallback, Tense
          (``_verb_form_key``), so "würden" does not count as the same form
          as "werden" just because both are the lemma "werden", and
          "wohnt"/"wohnte" (same lemma, Präsens vs Präteritum) do not count
          as the same form either.
        - **Determiner-category topics that declare a real
          ``syntax_tags['ArtType']``** (definite, indefinite, possessive,
          negative article topics -- excluding the ``"Zero"`` sentinel,
          which marks a topic about the ADJECTIVE ending that appears with
          NO article, not about a determiner at all): the target feature is
          the specific ArtType (Def/Ind/Neg/Poss), decoded via
          ``facets.determiner_art_type`` -- WHICH ein-word stem matched, not
          just whether one did, so a possessive topic accepting a bare
          indefinite ("Eine" is not a possessive) is caught, not just a
          definite-vs-everything-else split.
        - **Degree-based topics** (``morph_spec['Degree']`` set, e.g.
          comparative/superlative): no morphological decoder for comparison
          endings exists in this codebase, so this falls back to a plain
          count threshold (``AMBIGUITY_COUNT_THRESHOLD``) -- the audit's own
          sanctioned fallback for when "the form cannot be derived".
        - Every other topic shape has no cheap, reliable form signal in this
          codebase's closed-class tables, and is left unchecked here rather
          than guessed -- exactly this module's and facets.py's shared
          philosophy of skipping over guessing.
        """
        if len(accepted_answers) < 2:
            return None

        morph_spec = topic.morph_spec or {}
        syntax_tags = topic.syntax_tags or {}
        prompt = item.prompt if item is not None else None

        verb_topic = bool(
            any(k in morph_spec for k in _VERB_MORPH_KEYS) or syntax_tags.get("VerbType")
        )
        if not verb_topic and item is not None:
            verb_topic = _answer_is_verb(item.proposed_answer, item.prompt)

        if verb_topic:
            forms = {
                key for a in accepted_answers if (key := _verb_form_key(a, prompt)) is not None
            }
            if len(forms) > 1:
                return (
                    "Accepted answers span more than one verb form "
                    f"({', '.join(sorted(forms))}), so the item does not "
                    "consistently test its target tense/mood."
                )
            return None

        art_type = syntax_tags.get("ArtType")
        if art_type and art_type != "Zero":
            from src.taxonomy.facets import determiner_art_type

            forms = {determiner_art_type(a) for a in accepted_answers}
            forms.discard("Unk")
            if len(forms) > 1:
                return (
                    f"Accepted answers span more than one determiner type "
                    f"({', '.join(sorted(forms))}), so the item does not "
                    "consistently test one article type."
                )
            return None

        # Task 2 (cycle-2 report): derive the determiner type from the
        # ANSWERS themselves rather than staying silent just because the
        # topic never declared ``ArtType`` at all -- "kaufen wir ___ Apfel
        # auf dem Markt" accepted einen/den/diesen/keinen (indefinite,
        # definite, demonstrative, negative) and the check above never
        # fired because its topic has no ``ArtType`` tag. But a topic whose
        # ``morph_spec`` fixes ``Case`` (the ``kasus_*_formen`` /
        # ``*_nach_praeposition`` family) is legitimately testing case
        # agreement, not article choice, and a set spanning several article
        # types at the SAME case/gender/number cell is correct there --
        # "Ich helfe ___ Kind" accepting dem/einem/keinem/meinem/deinem/
        # seinem/ihrem/unserem (all Dat Neut Sing) is eight equally correct
        # answers to a case question, not an ambiguous item. Only a topic
        # that neither declares ``ArtType`` NOR fixes ``Case`` reaches this
        # branch, and even then only rejects if the answers themselves
        # resolve to more than one determiner type -- if they resolve to
        # zero or one (e.g. a Degree topic's adjective endings, which are
        # never determiner-shaped), this falls through to the Degree
        # fallback below exactly as before.
        if not morph_spec.get("Case"):
            from src.taxonomy.facets import determiner_art_type

            forms = {determiner_art_type(a) for a in accepted_answers}
            forms.discard("Unk")
            if len(forms) > 1:
                return (
                    "Accepted answers span more than one determiner type "
                    f"({', '.join(sorted(forms))}), so the item does not "
                    "consistently test one article type."
                )

        if morph_spec.get("Degree") and len(accepted_answers) > cls.AMBIGUITY_COUNT_THRESHOLD:
            return (
                f"{len(accepted_answers)} accepted answers exceed the fallback "
                f"ambiguity threshold ({cls.AMBIGUITY_COUNT_THRESHOLD}) for a "
                "topic whose degree/comparison form cannot be verified directly."
            )

        return None

    @classmethod
    def check_pronoun_class_consistency(
        cls, accepted_answers: list[str], topic: Topic
    ) -> str | None:
        """Task 5 (cycle-2 report): reject when a pronoun topic's accepted
        set spans more than one PRONOUN CLASS (personal, demonstrative,
        interrogative). Real defect: "___ ist mein guter Freund." accepted
        "Er" (personal), "Das"/"Dieser"/"Der" (demonstrative) AND "Wer"
        (interrogative) -- "Wer" is not an alternative correct answer, it
        turns the declarative sentence into a question.

        Gated to topics that are actually about pronouns
        (``syntax_tags['Pos'] == 'Pron'``, e.g. ``pronomen_personal_nom``,
        or ``morph_spec['PronType']`` set, e.g. the ``relativsatz_*``
        family) -- this check's closed-class tables recognise "der"/"die"/
        "das" as demonstrative-PRONOUN forms, which are lexically identical
        to (but grammatically distinct from) their use as definite
        ARTICLES, so running this unconditionally on every topic would
        misclassify an ordinary article-choice item. Further gated OFF for
        relative-pronoun topics specifically (``morph_spec['PronType'] ==
        'Rel'``): "der"/"die"/"das"/"den"/"dem"/"deren"/"dessen" are the
        RELATIVE pronoun paradigm there, the topic's own single legitimate
        target form, not a mix of personal/demonstrative/interrogative
        pronouns.

        Returns ``None`` (no opinion) when the topic is not a pronoun
        topic, is a relative-pronoun topic, has fewer than two accepted
        answers, or none/only-one of the answers are classifiable into
        these three closed tables at all (e.g. an indefinite pronoun like
        "jemand", which this function does not attempt to classify --
        "skip rather than guess", the same posture every other closed-class
        check in this module takes).
        """
        if len(accepted_answers) < 2:
            return None

        morph_spec = topic.morph_spec or {}
        syntax_tags = topic.syntax_tags or {}

        if morph_spec.get("PronType") == "Rel":
            return None

        is_pronoun_topic = syntax_tags.get("Pos") == "Pron" or bool(morph_spec.get("PronType"))
        if not is_pronoun_topic:
            return None

        classes = {c for a in accepted_answers if (c := _pronoun_class(a)) is not None}
        if len(classes) > 1:
            return (
                "Accepted answers span more than one pronoun class "
                f"({', '.join(sorted(classes))}), so the item does not "
                "consistently test one kind of pronoun."
            )
        return None

    @classmethod
    def get_computed_accepted_answers(cls, item: CandidateItem) -> list[str] | None:
        """Task 1 (cycle-2 report): read a pre-verified, AUTHORITATIVE
        accepted-answer set off ``item``, if the candidate carries one.

        ``src/generation/blanking/`` (the generate-then-blank pipeline)
        removes a token from an already-tagged, already-parsed sentence, so
        its answer is OBSERVED (the literal text that was there) rather
        than proposed by a model, and is cross-checked against the closed
        paradigm table for the token's own (art_type/declension, cell)
        before the item is even built (see
        ``src.generation.blanking.blanker``'s own module docstring). That
        is categorically stronger evidence than ``proposed_answer`` from
        the LLM-direct pipeline, which the heuristic expansion/widening
        below (``expand_answers`` and every filter after it) exists to
        interrogate. If a blanked item's answer reaches that machinery
        anyway, its computed set is silently discarded and replaced by the
        SAME heuristic widening that produced the 34%/17% post-verifier
        defect rates this chain exists to keep under 15% -- exactly the
        regression this accessor exists to prevent.

        ``CandidateItem`` (src/contracts.py) is ``frozen`` with
        ``extra="allow"`` and does not declare a first-class field for
        this; contracts.py belongs to another agent this cycle, so no
        field was added there (flagged in this cycle's report as a
        possible follow-up for a future cycle, not done here). The
        convention adopted instead: a producer that has already computed
        and verified its own accepted set attaches it as the extra keyword
        ``computed_accepted_answers`` (a list of strings) when constructing
        the ``CandidateItem``, e.g. ``CandidateItem(...,
        computed_accepted_answers=["den"])``. Every consumer in this
        pipeline goes through this accessor rather than reading
        ``item.model_extra`` directly, so the convention is documented
        exactly once.

        Returns ``None`` when the item carries nothing usable here (no
        such extra field, not a list, or a list with no non-empty string
        entries) -- callers must then run the ordinary heuristic-expansion
        chain exactly as before, so a candidate that never opted into this
        mechanism is completely unaffected. Otherwise returns a
        deduplicated (first-seen order preserved) list of the entries as
        given: this is a TRUST boundary, not a re-verification -- the
        producer is asserting these answers are already correct, and this
        method's only job is to recognise that assertion, not to
        second-guess it.
        """
        extra = item.model_extra or {}
        raw = extra.get("computed_accepted_answers")
        if not isinstance(raw, list) or not raw:
            return None
        seen: set[str] = set()
        deduped: list[str] = []
        for candidate in raw:
            if isinstance(candidate, str) and candidate.strip() and candidate not in seen:
                seen.add(candidate)
                deduped.append(candidate)
        return deduped or None

    @classmethod
    def filter_alternatives_by_target_form(
        cls, alternatives: list[str], item: CandidateItem, topic: Topic
    ) -> list[str]:
        """Drop any accepted answer that does not carry ``item.
        proposed_answer``'s own target-feature form. An alternative that
        changes what is being tested is not an alternative correct answer,
        it is a different exercise (docs/audits/stage-04-pilot-2026-08-15.md
        fix 2 / 02-content-pipeline.md: "Expansion is constrained to the
        target form").

        Originally applied only to the semantic layer's proposed
        alternatives; docs/audits/stage-04-a2-pilot-audit.md's dominant
        finding was that the item's OWN generator-produced base answer set
        was never filtered at all, so 11 of 17 audited defects (mixed
        tense/mood, a cued verb ignoring its own cue, a reflexive slot
        accepting ordinary noun phrases, an adjective slot accepting
        adverbs and an article) reached acceptance unchecked. The fix is
        this method's OWN callers now passing the WHOLE accepted-answer set
        here, not a change to this method's per-candidate logic.

        Reuses exactly the identity signals ``check_ambiguity`` gates on,
        applied per-candidate against ``proposed_answer`` as the reference
        (it already passed layers 1-4, so it is a trustworthy anchor for
        "what form is this item actually testing") rather than mutual
        consistency across the whole set. Unresolvable candidates -- and an
        unresolvable reference -- are kept rather than dropped: this is a
        repair pass, not a gate, and ``check_ambiguity`` is the backstop
        that catches whatever slips through here.
        """
        if not alternatives:
            return []

        morph_spec = topic.morph_spec or {}
        syntax_tags = topic.syntax_tags or {}
        reference = item.proposed_answer

        verb_topic = bool(
            any(k in morph_spec for k in _VERB_MORPH_KEYS) or syntax_tags.get("VerbType")
        )
        if not verb_topic:
            verb_topic = _answer_is_verb(reference, item.prompt)

        if verb_topic:
            ref_key = _verb_form_key(reference, item.prompt)
            if ref_key is None:
                return alternatives
            return [a for a in alternatives if _verb_form_key(a, item.prompt) in (ref_key, None)]

        art_type = syntax_tags.get("ArtType")
        if art_type and art_type != "Zero":
            from src.taxonomy.facets import determiner_art_type

            ref_form = determiner_art_type(reference)
            if ref_form == "Unk":
                return alternatives
            return [a for a in alternatives if determiner_art_type(a) in (ref_form, "Unk")]

        # General part-of-speech consistency fallback (docs/audits/
        # stage-04-a2-pilot-audit.md items 9 and 50): an alternative that is
        # not even the same part of speech as the reference is never a
        # valid "alternative form" of it, regardless of whether the topic
        # declares a more specific identity signal above. "keine" (a
        # determiner) and "immer"/"gerne" (adverbs) are not adjective forms
        # of "frische"; "mein Gesicht" (a possessive-determiner-headed noun
        # phrase) is not a reflexive-pronoun form of "mich". Fails open --
        # keeps every alternative unfiltered -- whenever the tagger cannot
        # resolve the reference's own POS (no model, no gap, no alignment),
        # exactly like every other tagger-backed check in this module.
        ref_pos = _answer_pos(reference, item.prompt)
        if ref_pos is None:
            return alternatives
        return [a for a in alternatives if _answer_pos(a, item.prompt) in (ref_pos, None)]

    @classmethod
    def filter_by_cue_consistency(
        cls, accepted_answers: list[str], item: CandidateItem
    ) -> tuple[list[str], str | None]:
        """Cue consistency (docs/audits/stage-04-a2-pilot-audit.md, "Two
        rules that need to hold"): if ``item.cue`` is set, every accepted
        answer must be a form of that cue's own lemma, checked via
        ``src.lexicon.lemmatizer.lemma_candidates`` membership -- a cued
        item names the lemma the learner is meant to inflect, so an
        accepted answer of a DIFFERENT lemma is not a correct alternative,
        it silently drops the exercise's own instruction (item 42: cue
        "kochen" alongside accepted "macht", "bestellt", "holt", "kauft",
        "gönnt").

        Returns ``(filtered_answers, rejection_reason)``. When the PRIMARY
        answer (``item.proposed_answer``, always first in an
        already-expanded accepted-answer list) itself is not a form of the
        cue, the item is self-contradictory and ``rejection_reason`` is
        non-``None``; callers must reject the whole item, not merely drop
        the primary. When ``item.cue`` is absent this is a no-op --
        ``accepted_answers`` returned unchanged, ``None`` reason. "Absent"
        means ``None`` OR empty/whitespace-only: the model routinely emits
        ``cue: ""`` for an uncued item rather than omitting the field, and a
        truthiness/``is None`` check alone treats that empty string as a
        real cue whose own lemma is the empty string, which
        ``lemma_candidates("")`` never matches -- every item the model
        submits with an empty cue was rejected outright as
        "self-contradictory" (36 in one pilot) despite having no cue at all.
        """
        if item.cue is None or not item.cue.strip():
            return accepted_answers, None

        from src.lexicon.lemmatizer import lemma_candidates

        cue_lemmas = set(lemma_candidates(item.cue.strip()))

        def _matches_cue(answer: str) -> bool:
            return bool(set(lemma_candidates(_answer_first_token(answer))) & cue_lemmas)

        if not _matches_cue(item.proposed_answer):
            return [], (
                f"Proposed answer {item.proposed_answer!r} is not a form of "
                f"the item's own cue {item.cue!r}: the item is "
                "self-contradictory."
            )
        return [a for a in accepted_answers if _matches_cue(a)], None

    @classmethod
    def filter_by_cue_degree(
        cls, accepted_answers: list[str], item: CandidateItem
    ) -> tuple[list[str], str | None]:
        """Cue degree consistency: a cue names the adjective's citation
        (positive) form, e.g. "(groß)" for "das ___ (groß) Fenster" -- the
        learner is meant to inflect that word, not switch it for a
        different degree of comparison. "große" (Degree=Pos) is the correct
        inflection; "größte" (Degree=Sup) is a different word that happens
        to share a stem, exactly the same category of defect
        ``filter_by_cue_consistency`` catches for a different lexeme
        entirely -- both accepted for one cloze_cued item ("das ___ (groß)
        Fenster") is the concrete pilot defect this guards against.

        Uses ``src.taxonomy.tagger.tag_answer`` to read ``Degree`` off both
        the cue (filled into the gap in ``item.proposed_answer``'s place, so
        it parses in a complete sentence) and each candidate answer. Only
        acts when BOTH the cue's own Degree and a candidate's Degree can be
        resolved: no tagger, no gap, or a cue/answer the tagger does not
        mark for Degree at all (this check no-ops entirely for a verb or
        noun cue, which legitimately has no Degree) all fail open --
        "skip rather than guess", the same posture every other tagger-backed
        check in this module takes. Mirrors ``filter_by_cue_consistency``'s
        return shape: ``(filtered_answers, rejection_reason)``, the PRIMARY
        answer's own mismatch rejecting the whole item outright rather than
        merely dropping it.
        """
        if item.cue is None or not item.cue.strip():
            return accepted_answers, None

        from src.taxonomy import tagger as _tagger

        cue_tag = _tagger.tag_answer(item.prompt, item.cue.strip())
        if cue_tag is None:
            return accepted_answers, None
        required_degree = cue_tag.feats.get("Degree")
        if required_degree is None:
            return accepted_answers, None

        def _degree_ok(answer: str) -> bool:
            tag = _tagger.tag_answer(item.prompt, answer)
            if tag is None:
                return True
            degree = tag.feats.get("Degree")
            if degree is None:
                return True
            return degree == required_degree

        if not _degree_ok(item.proposed_answer):
            return [], (
                f"Proposed answer {item.proposed_answer!r} does not match "
                f"the item's own cue {item.cue!r}'s degree "
                f"({required_degree!r}): the item is self-contradictory."
            )
        return [a for a in accepted_answers if _degree_ok(a)], None

    @classmethod
    def check_possessive_person_agreement(
        cls, accepted_answers: list[str], item: CandidateItem, topic: Topic
    ) -> tuple[list[str], str | None]:
        """For a possessive-determiner topic (``syntax_tags['ArtType'] ==
        'Poss'``), every accepted answer must agree in person and number
        with the possessor established in the carrier sentence -- "Anna
        kocht gern, weil ___ Küche sehr groß ist." accepting 'ihre' (Anna,
        3rd singular -- correct) alongside 'unsere' (1st plural) and 'meine'
        (1st singular) does not consistently test one possessor; nothing in
        the sentence chooses between three different people.

        A possessive determiner's own surface form carries no Person
        feature in spaCy's German pipeline (``sein``/``ihre`` are tagged
        with the Case/Gender/Number of the noun they agree with, never the
        person of the possessor -- verified directly), so the possessor's
        person/number has to come from elsewhere in the sentence:
        ``src.taxonomy.tagger.tag_context`` tags every OTHER token, and a
        personal pronoun (``PronType=Prs``, giving Person and Number
        directly) or a proper noun (``PROPN``, giving Number; Person is
        always 3rd for a named referent) elsewhere in the carrier is taken
        as the possessor. Each recognised possessive stem's own set of
        legitimate (Person, Number) pairs lives in
        ``_POSSESSIVE_STEM_PERSON_NUMBER``.

        Exactly one distinct (Person, Number) candidate in the carrier is
        "established"; zero, or more than one DISTINCT pair (e.g. both a
        1st-person subject and a 3rd-person proper noun, with nothing to
        say which one the gap's possessive refers to), means no possessor
        is reliably established, so the item is rejected outright as
        under-constrained -- the same "skip rather than guess" posture
        every other check in this module takes, applied here to rejection
        rather than to silently accepting.

        No-ops (returns ``accepted_answers`` unchanged) when: the topic is
        not a possessive-article topic at all (``ArtType != "Poss"``), the
        accepted set (after fix B's target-form filtering already ran)
        contains no recognisable possessive-stem form at all -- nothing
        left to validate a possessor against, most often because it was
        already narrowed to a single non-possessive determinerlike answer
        by an earlier stage -- or the tagger is unavailable
        (``analysis_available() is False``), the same guard
        ``check_facet_derivability`` uses, since without a model this check
        has no basis to reject anything.
        """
        if (topic.syntax_tags or {}).get("ArtType") != "Poss":
            return accepted_answers, None

        if not any(_possessive_person_number(a) is not None for a in accepted_answers):
            return accepted_answers, None

        from src.taxonomy import tagger as _tagger

        if not _tagger.analysis_available():
            return accepted_answers, None

        context = _tagger.tag_context(item.prompt, item.proposed_answer)
        if context is None:
            return accepted_answers, None

        candidates: set[tuple[str, str]] = set()
        for tok in context:
            if tok.feats.get("PronType") == "Prs":
                person = tok.feats.get("Person")
                number = tok.feats.get("Number")
                if person is not None and number is not None:
                    candidates.add((person, number))
            elif tok.pos == "PROPN":
                candidates.add(("3", tok.feats.get("Number", "Sing")))

        if len(candidates) != 1:
            return [], (
                "No single possessor is established in the carrier for this "
                f"possessive-determiner item (found {len(candidates)} distinct "
                "person/number candidates among its pronouns and proper "
                "nouns): the item is under-constrained."
            )
        established = next(iter(candidates))

        def _agrees(answer: str) -> bool:
            pairs = _possessive_person_number(answer)
            return pairs is None or established in pairs

        if not _agrees(item.proposed_answer):
            return [], (
                f"Proposed answer {item.proposed_answer!r} does not agree in "
                f"person/number ({established[0]}, {established[1]}) with the "
                "possessor established in the carrier: the item is "
                "self-contradictory."
            )
        return [a for a in accepted_answers if _agrees(a)], None

    @classmethod
    def check_facet_derivability(
        cls, accepted_answers: list[str], item: CandidateItem, topic: Topic
    ) -> str | None:
        """Unk-facet honesty (docs/audits/stage-04-a2-pilot-audit.md, "Two
        rules that need to hold", rule 2): when ``topic``'s own target
        facet cannot be derived for this item's answer AT ALL, and the
        accepted-answer set still has more than one member, the chain
        cannot verify internal consistency -- "the filter cannot compare
        two things it cannot analyse" -- so the honest default is to reject
        as under-constrained, not to silently accept.

        Deliberately narrower than "some dimension is Unk": a topic with an
        empty ``facet_space`` (e.g. ``nebensatz_wenn``, tested via
        ``_answer_is_verb`` above instead) has NO facet to derive by
        design (01-foundation.md:170) and must not be penalised for that --
        ``facet_space(topic)`` empty short-circuits to no rejection here,
        exactly as ``facets.derive_facet`` itself returns ``None`` rather
        than a facet string in that case. Only a topic that DOES declare a
        facet space, whose derivation nonetheless comes back ``"Unk"`` on
        EVERY single dimension, counts.

        Guarded on ``tagger.analysis_available()``: several of
        ``facets.py``'s closed-class categories (documented in its own
        module docstring, e.g. noun Case) defer a dimension to the spaCy
        tagger with NO closed-class fallback of their own. Without that
        guard, an environment where the spaCy model failed to install would
        turn this rule into a mass-rejection engine on every such category
        instead of the rare backstop it is meant to be -- the tagger
        rewrite this cycle (commit 94b25e8) is what makes an all-Unk facet
        rare enough to BE a meaningful signal in the first place.
        """
        if len(accepted_answers) < 2:
            return None

        from src.taxonomy import tagger as _tagger

        if not _tagger.analysis_available():
            return None

        from src.taxonomy.facets import derive_facet, facet_space

        dims = facet_space(topic)
        if not dims:
            return None

        synthetic = BankItem(
            id="facet-derivability-check",
            topic_id=topic.id,
            type=item.type,
            difficulty=item.difficulty,
            cefr=topic.cefr,
            prompt=item.prompt,
            accepted_answers=[item.proposed_answer],
        )
        facet = derive_facet(synthetic, topic)
        if facet is None:
            return None

        values = [segment.split("=", 1)[1] for segment in facet.split("|")]
        if values and all(v == _UNK for v in values):
            return (
                "The topic's target facet could not be derived for "
                f"proposed answer {item.proposed_answer!r} ({facet}), and "
                "the accepted-answer set has more than one member: the "
                "item is under-constrained."
            )
        return None

    @classmethod
    def _build_user_prompt(cls, item: CandidateItem) -> str:
        filled = item.prompt.replace("___", item.proposed_answer)
        return (
            f"Aufgabe (mit Lücke): {item.prompt}\n"
            f"Vorgeschlagene Antwort: {item.proposed_answer}\n"
            f"Vollständiger Satz: {filled}\n\n"
            "Ist dieser Satz semantisch kohärent, idiomatisch und inhaltlich korrekt?"
        )

    @staticmethod
    def _parse_semantic_response(response_text: str) -> SemanticVerificationResult:
        """Parse the model's JSON verdict.

        Strips a markdown code fence first (the transport sends no
        ``response_mime_type``, so Gemini routinely wraps JSON in one -- see
        ``src.generation.batch_client._parse_response`` for the same fix
        against the same live behaviour). Any parse failure or unexpected
        shape degrades to ``valid=True`` rather than rejecting on a defect in
        this layer's own plumbing, not the candidate's German.
        """
        text = response_text.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.removesuffix("```").strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return SemanticVerificationResult()
        if not isinstance(payload, dict):
            return SemanticVerificationResult()

        valid = payload.get("valid", True)
        if not isinstance(valid, bool):
            valid = True

        reason = payload.get("reason")
        reason = reason if isinstance(reason, str) else None

        extra = payload.get("additional_accepted_answers")
        additional = [a for a in extra if isinstance(a, str)] if isinstance(extra, list) else []

        return SemanticVerificationResult(
            valid=valid, reason=reason, additional_accepted_answers=additional
        )

    @classmethod
    def verify_semantic_validity_many(
        cls, items: list[CandidateItem], llm_client: "GeminiLlmClient"
    ) -> list[SemanticVerificationResult]:
        """Run the model-backed semantic/collocation check for many candidates
        as one logical call, returning verdicts in the same order as ``items``.

        Every call goes through ``src.llm.client.GeminiLlmClient`` (CLAUDE.md
        rule 4) via ``generate_many``: cost accounting, the spend ceiling, and
        the two-lane routing all apply exactly as they do to generation, and
        on the free lane items are dispatched concurrently instead of one
        ``generate()`` call per item in a loop -- the previous per-item loop
        was, along with generation's own per-item loop, the dominant cost of
        a slow pilot run (each paid-lane item alone queued for 1-3 minutes).

        Two errors this layer explicitly degrades the WHOLE group on rather
        than propagates, both confirmed live against the real API, not
        hypothetical:

        - ``BudgetExceeded`` -- CLAUDE.md 9: "Callers handle it by degrading,
          never by retrying."
        - ``ServerUnavailableError`` -- transient 5xx overload that survives
          the transport's own bounded retries (``SERVER_ERROR_MAX_RETRIES``);
          observed live as a sustained-enough outage on ``gemini-3.7-flash``
          to exhaust them.

        Both are infrastructure trouble, not a verdict on any candidate's
        German, and this layer is a refinement on top of layers 1-4, not a
        required gate: either one skips the check for every item in the
        group rather than rejecting, or crashing the whole batch on,
        candidates that already passed every free/cheap check.
        """
        if not items:
            return []
        from src.llm.client import BudgetExceeded, ServerUnavailableError

        prompts = [f"{cls.SYSTEM_PROMPT}\n\n{cls._build_user_prompt(item)}" for item in items]
        try:
            response_texts = llm_client.generate_many(
                prompts, model=MODEL_VERIFY, purpose="answer_expansion"
            )
        except (BudgetExceeded, ServerUnavailableError):
            return [SemanticVerificationResult() for _ in items]
        return [cls._parse_semantic_response(text) for text in response_texts]

    @classmethod
    def verify_semantic_validity(
        cls, item: CandidateItem, llm_client: "GeminiLlmClient"
    ) -> SemanticVerificationResult:
        """Single-candidate case of ``verify_semantic_validity_many``, kept
        for callers verifying exactly one item on its own (e.g. ``verify_item``)."""
        return cls.verify_semantic_validity_many([item], llm_client)[0]
