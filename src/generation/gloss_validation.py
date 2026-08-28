"""English gloss requirement and mechanical consistency checks.

docs/audits/generation-track-plan.md, Cycle 3. The largest rejection bucket
in the last pilot (26 items) was under-constrained gaps: nothing in the
carrier sentence chose between two grammatically valid fillers ("Weisst du,
wo er ___ (wohnen)?" does not choose between "wohnt" and "wohnte"). The fix
already decided (see that document, and the plan owner's own message it
quotes) is an English gloss of the complete sentence, not a category label:
a label makes the learner's category decision for them and reinstates the
topic-by-topic textbook this project exists to escape; a gloss supplies only
the *meaning*, leaving the learner to map meaning to category to form, which
is the discrimination interleaving trains. CLAUDE.md rule 2 forbids naming
the grammar topic; translating a sentence's meaning names no grammar, so
this does not weaken that rule.

This module has three jobs, kept as separate, composable functions so a
caller (the verification chain, not owned by this module) can use exactly
the piece it needs:

1. :func:`gloss_required` -- is a gap under-constrained enough that a gloss
   (or, narrowly, a category tag) is REQUIRED, as opposed to merely
   harmless-if-present.
2. :func:`topic_allows_category_fallback` / :func:`is_valid_category_tag` --
   the narrow fallback for the short list of topics where English marks no
   distinction at all, so a gloss cannot do the disambiguating work.
3. :func:`validate_gloss_consistency` -- the mechanical check that a
   SUPPLIED gloss actually agrees with the German answer's own computed
   features, so a wrong gloss (worse than no gloss at all) is caught rather
   than trusted.

A fourth piece, :func:`gloss_resolves_lexical_ambiguity`, is not one of the
three original jobs above -- it is the specific, conservative bridge
``src.verification.layer3_solver`` consults on its "open lexical slot"
rejection (the "no governing preposition, verb, or determiner" message):
whether a validated gloss, plus the German lemma tagger confirming a
flagged distractor is the SAME verb as the answer under a different
tense/person, together license dropping that rejection. See its own
docstring for the two-part gate and why neither half alone is sufficient.

Wired into ``src/verification/pipeline.py`` (``VerificationPipeline.
_gloss_check``, which validates a present ``gloss_en`` and rejects on
contradiction or leak) and ``src/verification/layer3_solver.py``
(``Layer3AdversarialSolver._check_under_constrained``, via
:func:`gloss_resolves_lexical_ambiguity`).

### What "tense-selecting" and "person-selecting" mean here

A topic is **tense-selecting** if its own ``morph_spec`` fixes ``Tense``
(``data/taxonomy.yaml``'s verb-tense topics: ``verb_praesens_regelm``,
``praeteritum_vollverben``, ``perfekt_haben``, ``futur_i``, ...). Fixing a
tense in the topic definition is exactly what creates the "Weisst du, wo er
___ (wohnen)?" failure mode: the topic's identity, not anything in the
carrier sentence, decides whether the answer is "wohnt" or "wohnte", and the
topic's identity is precisely the thing rule 2 forbids the prompt from
disclosing. A topic is **person-selecting** on the same logic if its
``morph_spec`` fixes ``Person`` (no current topic does; this is
forward-compatible with one that would).

This is deliberately a topic-level property, not "does the answer's own
Person/Number vary" -- most verb-conjugation topics DO vary over Person
(it's in their facet space, see ``src/taxonomy/facets.py``) but are not
under-constrained by it, because an ordinary declarative sentence carries an
explicit subject ("Er ___ (kommen)") that already fixes Person from context
without needing a gloss at all. What actually leaves a gap under-constrained
is a topic whose FIXED target dimension the sentence has no other way to
signal -- which is exactly what a fixed ``morph_spec`` entry means.

### The mechanical consistency check: what is real, what is a proxy

The check does NOT align individual English words to the German answer (no
translation model is available or appropriate here). It instead computes,
for the German target, which of a small set of tense "buckets" it belongs
to (present / past / perfect / future / conditional -- see
``_german_tense_bucket``), and separately scans the WHOLE gloss for
evidence of each bucket appearing ANYWHERE in it. The gloss is judged
inconsistent only if it shows evidence for a tense bucket other than the
expected one and NONE at all for the expected one -- never merely because
some OTHER clause in the gloss happens to be a different tense. This
matters concretely: "Do you know where he lived?" is the correct gloss for
a Praeteritum-selecting "wohnen" item, and its matrix clause ("Do you
know...") is present tense. A stricter "every verb must match" rule would
reject the plan owner's own worked example; this design does not.

Presence of no evidence at all (neither the expected bucket nor any other)
is reported as UNVERIFIED, not as a pass and not as a failure -- absence of
a detectable marker is not evidence the gloss is wrong, especially on the
closed-list fallback path (see below), which under-detects on purpose
rather than over-claims.

Person/Number checks the same way: the German answer's own (Person, Number)
must show up SOMEWHERE in the gloss (a subject pronoun, a finite verb's own
agreement morphology, or -- spaCy path only -- a NOMINAL subject, which
supplies third person without any pronoun being present at all). Gender is
deliberately NOT checked (whether a German 3rd-singular target glosses as
"he", "she" or "it"): the task's own worked examples (1sg -> "I", 3pl ->
"they") never need it, and getting it right would require aligning the
gloss's subject specifically to the German sentence's subject, which this
module does not attempt.

### What the first real pilot measured, and what it changed here

The first pilot with English glosses populated ran this check in
measure-only mode over 376 accepted items. It flagged 34 of them. The owner
read all 34 by hand: exactly ONE was a real defect (a Tatoeba pairing whose
English sentence is about something else entirely), and 33 were this check
being wrong. Enforcing it as it stood would have thrown away 33 good items
to catch 1 bad one. Four distinct faults produced those 33, and each is
corrected below rather than suppressed:

1. **English contractions were invisible.** The gloss was tokenised with
   ``[A-Za-z']+``, which glues "wouldn't", "could've" and "I'll" into single
   tokens that match no marker list, so plainly future/perfect/conditional
   glosses were read as present. :func:`_english_word_readings` now splits
   the clitic off and expands it to the SET of words it can stand for
   ("'d" -> would OR had, "'s" -> is OR has). A set, not a single reading:
   this check's job is to catch contradiction, not to prove agreement, so
   matching any member is the correct disposition of a genuine ambiguity.
2. **English marks the subjunctive with its past forms.** German Konjunktiv
   II maps onto English past-form subjunctives ("If I were you", "I wish I
   was pretty") and onto modal pasts, and -- in "als ob" clauses, wishes and
   polite requests -- onto the plain present or future ("als würden Sie mich
   nicht kennen" -> "you don't know me"). English simply has no dedicated
   conditional tense. So a ``conditional`` target is CONFIRMED by
   conditional, past or perfect marking and CONTRADICTED by nothing; a gloss
   showing only present or future marking comes back UNVERIFIED, which is
   the honest verdict rather than a false rejection. See
   :data:`_BUCKET_CONTRADICTED_BY`.
3. **The person check demanded a pronoun that often should not be there,
   and was fooled by German syncretism.** Two independent faults, fixed
   independently: a NOMINAL subject in the gloss ("I think Tom will win",
   "everything will go well", "Most of the staff has left") now supplies
   third-person evidence on its own, and a German finite-verb cell that is
   syncretic between persons ("hätte" is 1st AND 3rd singular, "sind" is
   1st AND 3rd plural, "kommt" is 3rd singular AND 2nd plural) cannot
   constrain the gloss at all and is skipped entirely rather than checked
   against the tagger's arbitrary pick of one reading. That second half
   reuses ``src.generation.blanking.selectors.
   _finite_verb_cell_is_unambiguous`` -- the machinery this repository
   already built for exactly that question -- rather than growing a second
   copy of it.
4. **German match strings were being found inside English text.** The
   grammar-terminology blocklist and the bare-answer-token check are both
   lists of GERMAN strings, and the gloss is English; German and English
   share a lot of spellings. English "fall" was matched against German
   "Fall" (grammatical case) and English "war" against German "war" (was).
   Both checks keep their real job -- a gloss must not hand the learner the
   German answer, and must not name the grammar topic -- but a match on a
   spelling that is also an ordinary English word is no longer evidence of
   either. See :data:`_ENGLISH_SHARED_SPELLINGS`.

The honest cost of 2, 3 and 4 is stated with each one: every fix trades some
detection away, and the traded-away detection is named where it is traded.

### What the SECOND glossed pilot measured, and what it changed here

The owner reran the pilot with all four corrections above in place: 437
accepted items, 3 flags, and reading all 3 by hand found the same verdict
as before -- every one of them was this check being wrong, none was a real
defect. Three more distinct faults produced them, each corrected below on
the same terms (extend the existing seam, name the cost):

5. **Impersonal "man" has no English pronoun counterpart.** "In der Schule
   ___ man nicht rauchen." / "You can't smoke at school." was rejected for
   showing no "he", "she" or "it". "man" is grammatically 3rd singular and
   drives 3rd-singular agreement, but English renders it as "you", "one",
   "people", "we", or a passive with the German object promoted to subject
   ("Man hat mir gesagt ..." -> "I was told ..."). This is the same shape as
   fault 3's nominal-subject seam and as the impersonal-"es" promotion:
   the German morphology genuinely does not predict the English pronoun. A
   nominative "man" therefore widens the expected set (see
   :data:`_MAN_GLOSS_PRONOUNS` and :func:`_impersonal_promoted_pronouns`)
   rather than being checked against the 3rd-singular table. The cost: for
   a "man" item only a gloss whose ONLY person marking is first-person, with
   no oblique 1st-person pronoun in the German to promote, is still caught.
6. **English modals do not inflect for tense, so they cannot confirm or
   contradict a German modal's.** "Schüler ___ nicht arbeiten, wenn sie zur
   Schule gehen." (answer "sollten", a Präteritum form) / "Students should
   not work if they are going to school." was rejected as a past target
   glossed present. "should" IS the only English rendering of that
   "sollten", and spaCy assigns an English modal no ``Tense`` feature at
   all -- so every tense bucket detected in such a gloss comes from some
   OTHER clause ("are going to school"), which is not evidence about the
   target. This is exactly the Konjunktiv II problem of fault 2, and it gets
   the same treatment: where the German target is a modal verb AND the gloss
   renders it with an English modal, the tense dimension can be CONFIRMED
   but never CONTRADICTED, so a non-matching gloss comes back UNVERIFIED.
   See :func:`_modal_tense_is_indeterminable`. The cost, stated plainly: a
   genuinely wrong gloss of a modal item is no longer caught on the tense
   dimension whenever the gloss happens to contain any English modal. A
   modal item glossed WITHOUT an English modal ("Er konnte nicht kommen." ->
   "He is not able to come.") still is, because "is able to" does carry
   tense.
7. **The English noun "will" was read as the future auxiliary.** "Er ___
   nach Gottes Willen nicht getötet werden." / "According to God's will, he
   must not be killed." was rejected as a present target glossed future.
   "God's will" is a noun. This is the same family as fault 4 but a
   different mechanism: not a German string matched inside English, but
   English-internal homography, so :data:`_ENGLISH_SHARED_SPELLINGS` cannot
   reach it. The correction is to ask the English tagger this module ALREADY
   loads (``en_core_web_sm``, needed for the tense and person morphology
   anyway) what part of speech each "will" is, and to count "will" as a
   future marker unless EVERY token in the gloss that could read as "will"
   is a ``NOUN``. See :func:`_future_will_is_only_a_noun`. This runs on the
   spaCy path only; the closed-list fallback keeps reading every "will" as
   future, which is the same under-detection-versus-over-claiming trade that
   path already documents everywhere else.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from src.contracts import CandidateItem, Topic
from src.generation.prompt_builder import PromptBuilder
from src.taxonomy import tagger as de_tagger

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Doc

    from src.generation.blanking.sentence_tagger import TaggedSentence
    from src.generation.blanking.sentence_tagger import Token as GermanToken

logger = logging.getLogger(__name__)

EN_MODEL_NAME = "en_core_web_sm"

GlossDimension = Literal["tense", "person", "number"]
_DimensionVerdict = Literal["consistent", "inconsistent", "unverified"]
_TenseBucket = Literal["present", "past", "perfect", "future", "conditional"]
_AnalysisSource = Literal["spacy", "closed_list"]

# ==============================================================================
# 1. Requirement: is a gloss (or fallback tag) REQUIRED for this item?
# ==============================================================================


def gloss_required(topic: Topic | None, accepted_answers: Sequence[str]) -> bool:
    """True if the gap is under-constrained enough that a disambiguator
    (gloss, or the narrow category-tag fallback) is required rather than
    merely harmless-if-present.

    Two independent triggers, either one is sufficient (docs/audits/
    generation-track-plan.md Cycle 3):

    - The computed accepted set has more than one distinct member -- the
      general "more than one filler is grammatically valid" case.
    - The topic is tense-selecting or person-selecting (``morph_spec``
      fixes ``Tense`` or ``Person``) -- the case a single-member accepted
      set can still hide, because the set is computed FOR the topic's own
      fixed tense/person and never sees the sibling tense/person that would
      also have fit the same carrier. This is exactly how "wohnt"/"wohnte"
      survived before this fix: the accepted set had one member (whichever
      tense the topic asked for), so the multi-member trigger above never
      fired.
    """
    distinct = {a.strip().lower() for a in accepted_answers if a.strip()}
    if len(distinct) > 1:
        return True
    if topic is None:
        return False
    morph = topic.morph_spec or {}
    return "Tense" in morph or "Person" in morph


# ==============================================================================
# 2. The narrow category-tag fallback
#
# Justification is required per-entry (task instruction) and kept here next
# to the data so the two cannot drift apart.
#
# - "imperativ" (the only topic whose morph_spec fixes Mood: Imp): its items
#   vary over Person (du/ihr/Sie) with NO subject expressed at all -- German
#   imperative drops the pronoun entirely, and English imperative has no
#   subject either ("Come in!"), so there is no English sentence-meaning
#   gloss that could show the addressee's formality. English "you" collapses
#   du/ihr/Sie completely; this is the textbook case the plan document names
#   explicitly as the one distinction English does not mark at all.
#
# No other topic is on this list. Every other tense/person-selecting topic
# DOES have an English tense or pronoun that marks the distinction (that is
# what makes the gloss work as the default), so adding an entry here for
# convenience rather than genuine English gaplessness is exactly the "back
# door for labelling everything" the task warns against.
# ==============================================================================

_CATEGORY_FALLBACK_TOPICS: dict[str, str] = {
    "imperativ": (
        "German imperative drops the subject; English imperative has none "
        "either. 'you' does not distinguish du/ihr/Sie, and no gloss of "
        "sentence MEANING can supply a distinction the sentence itself "
        "does not express."
    ),
}

# Controlled vocabulary for the tag itself, per topic. Short labels only --
# this is a formality/register TAG, not a grammar-topic name (rule 2 bans
# naming the topic, e.g. "Imperativ"; "Sie-Form" names an addressee
# register, not the mood being tested).
_CATEGORY_TAGS: dict[str, frozenset[str]] = {
    "imperativ": frozenset({"du-form", "ihr-form", "sie-form"}),
}


def topic_allows_category_fallback(topic: Topic | None) -> bool:
    """True only for the short, justified list in ``_CATEGORY_FALLBACK_TOPICS``."""
    return topic is not None and topic.id in _CATEGORY_FALLBACK_TOPICS


def category_fallback_reason(topic: Topic | None) -> str | None:
    """The justification for this topic being on the fallback list, or
    ``None`` if it is not on it at all."""
    if topic is None:
        return None
    return _CATEGORY_FALLBACK_TOPICS.get(topic.id)


def is_valid_category_tag(topic: Topic | None, tag: str) -> bool:
    """Whether ``tag`` is a member of this topic's own short, controlled
    category-tag vocabulary. Always ``False`` for a topic not on the
    fallback list at all -- there is no vocabulary to check it against.
    """
    if topic is None or topic.id not in _CATEGORY_TAGS:
        return False
    return tag.strip().lower() in _CATEGORY_TAGS[topic.id]


def check_requirement(
    topic: Topic | None,
    accepted_answers: Sequence[str],
    gloss_en: str | None,
) -> tuple[bool, str | None]:
    """Is the disambiguation REQUIREMENT satisfied for this item?

    Purely structural: "is something present, and if the topic is on the
    narrow fallback list, is it a valid tag from that topic's own
    vocabulary". Does NOT check whether a supplied gloss is mechanically
    CONSISTENT with the answer -- see :func:`validate_gloss_consistency`
    for that, run separately once this passes.

    Returns ``(satisfied, reason)``; ``reason`` is ``None`` iff
    ``satisfied`` is ``True``.
    """
    if not gloss_required(topic, accepted_answers):
        return True, None

    if gloss_en is None or not gloss_en.strip():
        return False, (
            "Gap is under-constrained (multiple valid fillers, or the "
            "topic is tense/person-selecting) and no gloss_en is present."
        )

    if topic_allows_category_fallback(topic) and is_valid_category_tag(topic, gloss_en):
        return True, None

    # Not a valid fallback tag for this topic (either the topic is not on
    # the fallback list at all, or it is and the string given is not one of
    # its controlled labels) -- fall through and treat gloss_en as an
    # ordinary English gloss, non-empty is enough to satisfy the structural
    # requirement here; its correctness is validate_gloss_consistency's job.
    return True, None


# ==============================================================================
# 3. Mechanical consistency check
# ==============================================================================


@dataclass(frozen=True)
class GlossCheckResult:
    """Outcome of :func:`validate_gloss_consistency`.

    ``checked_dimensions`` lists every dimension that actually had
    something to check (the German target had a determinate value for it);
    ``unverified_dimensions`` (a subset of ``checked_dimensions``) lists the
    ones where neither confirming nor contradicting evidence was found in
    the gloss -- per the task's own instruction, these must be surfaced,
    never silently treated as passed.
    """

    consistent: bool
    reason: str | None
    checked_dimensions: tuple[GlossDimension, ...]
    unverified_dimensions: tuple[GlossDimension, ...]
    analysis_source: Literal["spacy", "closed_list", "unavailable"]


@lru_cache(maxsize=1)
def _load_en_model() -> Language | None:
    """Load ``en_core_web_sm`` once per process, or return ``None``.

    Mirrors ``src/taxonomy/tagger.py::_load_model`` exactly: lazy (never at
    import time), caches the one outcome for the process, and degrades to
    ``None`` on either an unimported spaCy or a missing model rather than
    raising. Every caller in this module treats ``None`` as "use the
    closed-list fallback", never as an error.

    ``en_core_web_sm`` is now pinned in ``pyproject.toml`` the same way
    ``de-core-news-sm`` is (a direct-URL wheel from the spacy-models GitHub
    releases, ``[tool.hatch.metadata] allow-direct-references``), so a
    fresh ``uv sync`` fetches it unconditionally and this fallback should
    not normally engage in a checkout that ran ``uv sync``. It still exists
    for any environment that installs dependencies another way (a bare
    ``pip install`` from a lockfile export, a container image that skips
    ``uv sync``, ...): the warning below is what makes that silent
    degradation loud instead.
    """
    try:
        import spacy
    except ImportError:
        logger.warning(
            "spaCy is not installed; English gloss analysis is unavailable "
            "and gloss consistency checks fall back to a closed-list "
            "heuristic only."
        )
        return None

    try:
        # Parser and tagger stay in the pipeline (morph features and verb
        # agreement genuinely need them); NER is excluded because nothing
        # here reads entity spans.
        return spacy.load(EN_MODEL_NAME, exclude=["ner"])
    except OSError:
        logger.warning(
            "spaCy model %r is not installed (`python -m spacy download %s` "
            "installs it). English gloss analysis is unavailable and "
            "gloss consistency checks fall back to a closed-list "
            "heuristic only.",
            EN_MODEL_NAME,
            EN_MODEL_NAME,
        )
        return None


def english_analysis_available() -> bool:
    """Whether ``en_core_web_sm`` loaded successfully -- for tests and
    reporting that want to state which code path ran, not for gating
    behaviour (``validate_gloss_consistency`` already degrades on its own).
    """
    return _load_en_model() is not None


# ---- German target -> tense bucket -----------------------------------------

_BUCKET_SATISFIED_BY: dict[_TenseBucket, frozenset[_TenseBucket]] = {
    # German simple past AND Perfekt (a compound "past" in this taxonomy's
    # own morph_spec convention, Tense: Past, Aspect: Perf) both gloss
    # naturally into either an English simple past OR present-perfect
    # sentence ("he lived" / "he has lived" both correctly translate
    # Perfekt in casual register) -- so either English bucket satisfies
    # either German one. This is the one place "past" and "perfect" are
    # treated as interchangeable; present/future are not collapsed into
    # anything else.
    "present": frozenset({"present"}),
    "past": frozenset({"past", "perfect"}),
    "perfect": frozenset({"past", "perfect"}),
    "future": frozenset({"future"}),
    # English marks irrealis with its PAST forms, not with a tense of its
    # own: "If I were you", "I wish I was pretty", "if he'd wanted to",
    # "Even if you had asked me". A German Konjunktiv II target is therefore
    # confirmed by past and perfect marking exactly as much as by an
    # explicit modal.
    "conditional": frozenset({"conditional", "past", "perfect"}),
}

#: Which English marking positively CONTRADICTS each German bucket. Not the
#: complement of :data:`_BUCKET_SATISFIED_BY`: marking that is neither
#: confirming nor contradicting comes back UNVERIFIED, which is the module's
#: standing posture (see the docstring) and the only honest verdict where
#: the two languages genuinely do not line up.
#:
#: Two entries are deliberately narrower than the complement:
#:
#: - ``conditional`` contradicts NOTHING. Beyond the past forms above,
#:   German Konjunktiv II also renders as a plain English present in "als
#:   ob" clauses ("Warum tun Sie so, als würden Sie mich nicht kennen?" ->
#:   "Why are you pretending you don't know me?") and as a plain future in
#:   polite requests ("Würden Sie ...?" -> "Will you ...?"). There is no
#:   English tense marking that is evidence AGAINST a Konjunktiv II, so this
#:   dimension reports confirmation when it finds it and unverified
#:   otherwise. The cost, stated plainly: a genuinely wrong gloss of a
#:   Konjunktiv II item is no longer caught on the tense dimension.
#: - ``perfect`` is contradicted only by ``future``. The German Perfekt's
#:   own finite verb is PRESENT-tense ("hat", "ist") and the construction,
#:   not the finite form, carries the past reference; resultative uses gloss
#:   straight into an English present ("Das Geschäft hat noch bis zum 19.
#:   Mai geöffnet." -> "The store is open until May 19."). A genuinely
#:   past-marked German finite verb (Präteritum, Plusquamperfekt) is a
#:   different matter and still contradicts a present-only gloss -- that is
#:   the ``past`` row, and it is what catches the one real defect the pilot
#:   found.
_BUCKET_CONTRADICTED_BY: dict[_TenseBucket, frozenset[_TenseBucket]] = {
    "present": frozenset({"past", "perfect", "future", "conditional"}),
    "past": frozenset({"present", "future", "conditional"}),
    "perfect": frozenset({"future"}),
    "future": frozenset({"present", "past", "perfect", "conditional"}),
    "conditional": frozenset(),
}

#: Surface forms of "mögen"'s lexicalised Konjunktiv II. Morphologically
#: these are what spaCy calls Mood=Ind|Tense=Pres (and every modal-verb
#: topic's ``morph_spec`` says Tense: Pres), but their MEANING is polite
#: conditional and their correct English gloss is "would like to". Reading
#: them as a present target made "Ich möchte ... besuchen." / "I would like
#: to visit ..." a contradiction, which it plainly is not.
_MOECHTE_FORMS = frozenset({"möchte", "möchtest", "möchten", "möchtet"})

#: English modal auxiliaries, used ONLY as the closed-list fallback for
#: :func:`_gloss_has_english_modal` where the English tagger is unavailable.
#: "ca", "wo" and "sha" are not typos: the gloss tokeniser peels the "n't"
#: enclitic off the RIGHT (:func:`_token_readings`), so "can't", "won't" and
#: "shan't" leave those stems behind rather than their full forms.
_ENGLISH_MODALS = frozenset(
    {
        "can",
        "ca",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "sha",
        "should",
        "will",
        "wo",
        "would",
        "ought",
    }
)


def _german_modal_lemmas() -> frozenset[str]:
    """The German modal verbs, from ``blanking.paradigms``' own closed set
    rather than a second copy of it, plus "mögen"/"möchten" which that set
    deliberately omits (see ``blanking.uniqueness._MODAL_LEMMAS``, which
    extends it the same way). Imported lazily for the same reason every
    other ``blanking`` import in this module is: to keep this module's import
    cost to what its own callers need."""
    from src.generation.blanking import paradigms

    return paradigms.MODAL_LEMMAS | frozenset({"mögen", "möchten"})


def _gloss_has_english_modal(gloss: str) -> bool:
    """Whether ``gloss`` renders anything with an English modal auxiliary.
    Uses the English tagger's ``MD`` tag where it is available (which also
    catches the "ca" of "can't" without the closed list having to know about
    enclitic stems) and falls back to :data:`_ENGLISH_MODALS` otherwise."""
    nlp = _load_en_model()
    if nlp is not None:
        return any(tok.tag_ == "MD" for tok in nlp(gloss))
    return bool(_english_word_readings(gloss) & _ENGLISH_MODALS)


def _modal_tense_is_indeterminable(answer_token: GermanToken | None, gloss: str) -> bool:
    """Whether the German target's tense is simply not readable off this
    gloss, because the target is a MODAL verb and the gloss renders it with
    an English modal.

    English modals do not inflect for tense the way German ones do: "should"
    is the correct and only rendering of the Präteritum "sollten" in
    "Schüler sollten nicht arbeiten, wenn sie zur Schule gehen.", and spaCy
    assigns an English ``MD`` token no ``Tense`` feature at all. Every tense
    bucket :func:`_gloss_tense_buckets` finds in such a gloss therefore comes
    from some OTHER clause -- "are going to school", in that example -- and
    says nothing whatsoever about the modal the item is actually testing.
    The second glossed pilot's rejection of exactly that item is what this
    corrects.

    This is the same shape of problem as Konjunktiv II (see
    :data:`_BUCKET_CONTRADICTED_BY`, whose ``conditional`` row contradicts
    nothing for the same reason) and gets the same treatment: the caller
    keeps CONFIRMATION -- a gloss that does show the expected marking still
    passes on the evidence -- and drops CONTRADICTION, so a non-matching
    gloss comes back UNVERIFIED instead of rejected.

    Gated on an English modal actually being present, deliberately, so the
    narrower catch survives: "Er konnte nicht kommen." glossed "He is not
    able to come." has no modal in it, the periphrastic "is able to" does
    carry tense honestly, and that contradiction still fires.
    """
    if answer_token is None:
        return False
    if (answer_token.lemma or "").lower() not in _german_modal_lemmas():
        return False
    return _gloss_has_english_modal(gloss)


def _german_tense_bucket(
    feats: dict[str, str] | None,
    topic: Topic | None,
    answer_token: GermanToken | None = None,
    sentence: TaggedSentence | None = None,
) -> _TenseBucket | None:
    """Which tense bucket the German target belongs to, or ``None`` if
    undeterminable (no tense-like dimension applies to this topic/answer at
    all -- most Case/Gender-only topics).

    Prefers the topic's own ``morph_spec`` (its declared, authoritative
    target) over the tagger's per-token reading of the answer WHEREVER the
    topic actually fixes that dimension; falls back to the tagger only for
    a dimension the topic leaves open. This priority is deliberate, not
    incidental: a single inflected token in isolation cannot see a
    periphrastic construction at all (Futur I's finite verb is
    "werde"/"wirst"/... with no Tense morph of its own -- the "werden +
    Infinitiv" construction is what carries the future, not the token), and
    a Konjunktiv II modal ("koennten") can be morphologically close enough
    to its Indikativ Praeteritum counterpart ("konnten") that a per-token
    tagger reading is not reliable evidence against the topic's own
    declared Mood. The topic's fixed value is the ground truth it was
    generated against; the tagger is only ever consulted for a dimension
    the topic itself leaves unfixed.

    ``answer_token`` and ``sentence`` (the lemma-keeping
    ``blanking.sentence_tagger`` analysis of the gap-filled prompt, or
    ``None`` where it is unavailable) supply the two facts neither the
    ``morph_spec`` nor the answer's own FEATS can express, both of which the
    first glossed pilot got wrong:

    - **An attributive Partizip II carries no sentence tense.** "Die von Eva
      Waser in Luzern gegründete private Institution erhält ..." has an
      Aspect=Perf target ("gegründete") inside a sentence whose finite verb
      is something else entirely, and its gloss's tense ("... will receive
      ...") is the MAIN clause's, not the participle's. spaCy tags such a
      declined participle ``ADJ``; for an adjective the tense dimension is
      simply not determinable and this returns ``None``.
    - **"haben" + Partizip II is a Perfekt whatever the topic says about the
      auxiliary's own form.** ``verb_sein_haben`` fixes Tense: Pres because
      it teaches the PRESENT forms of "haben"; in "was er begonnen hat" that
      present-tense "hat" is the auxiliary of a Perfekt, and the English
      gloss is a simple past ("what he started"). The declared Pres
      describes the form, the construction describes the time reference, and
      it is the time reference this check compares against. Restricted to
      "haben" deliberately: "sein" + Partizip II is ambiguous between a
      Perfekt ("ist gegangen") and a Zustandspassiv ("ist geöffnet"), and
      "werden" + Partizip II is a present passive, so neither may be
      rewritten this way.
    """
    feats = feats or {}
    morph_spec = (topic.morph_spec or {}) if topic is not None else {}

    def _pick(key: str) -> str | None:
        spec_value = morph_spec.get(key)
        if isinstance(spec_value, str):
            return spec_value
        value = feats.get(key)
        return value if value and value != de_tagger_unk() else None

    mood = _pick("Mood")
    tense = _pick("Tense")
    aspect = _pick("Aspect")

    if answer_token is not None:
        if answer_token.pos == "ADJ":
            return None
        if answer_token.text.lower() in _MOECHTE_FORMS:
            return "conditional"
    if (
        answer_token is not None
        and sentence is not None
        and answer_token.lemma == "haben"
        and answer_token.morph.get("VerbForm") == "Fin"
        and answer_token.morph.get("Tense") == "Pres"
        and _sentence_has_past_participle(sentence)
    ):
        return "perfect"

    if mood == "SubjII":
        return "conditional"
    if tense in ("FutI", "FutII"):
        return "future"
    if aspect == "Perf":
        return "perfect"
    if tense in ("Past", "PastPerf"):
        return "past"
    if tense == "Pres":
        return "present"
    return None


def de_tagger_unk() -> str:
    """``src.taxonomy.tagger`` has no own "Unk" sentinel (it simply omits a
    key spaCy did not resolve) -- ``facets.UNK`` is the taxonomy's
    convention, imported lazily here purely to avoid this module importing
    the much heavier ``facets`` module just for one string constant.
    """
    from src.taxonomy.facets import UNK

    return UNK


def _tag_gap_filled_sentence(
    prompt: str, answer: str
) -> tuple[TaggedSentence | None, GermanToken | None]:
    """The gap-filled prompt tagged by ``blanking.sentence_tagger``, plus the
    one token that IS the answer, or ``(None, None)`` wherever that is not
    possible (no gap, empty answer, tagger unavailable, no aligning token).

    ``src.taxonomy.tagger.tag_answer`` -- which this module already uses for
    the answer's FEATS -- deliberately excludes spaCy's lemmatizer, so its
    ``TaggedAnswer`` has no lemma at all, and both new German-side rules
    here need one (``_german_tense_bucket``'s "haben + Partizip II" test,
    and ``_person_cell_is_syncretic``'s paradigm reconstruction). The
    blanking package's tagger keeps the lemmatizer for exactly that reason,
    so it is the tagger to ask.

    ``de_tagger._fill_gap`` is reused rather than reimplemented: a
    ``cloze_cued`` prompt keeps its bracketed cue after the gap ("... das
    ___ (groß) Fenster."), that cue is not part of the sentence's grammar,
    and leaving it in visibly derails the parse. Duplicating that stripping
    rule here is how the two copies would drift.
    """
    from src.generation.blanking import sentence_tagger

    answer = answer.strip()
    if not answer:
        return None, None
    filled = de_tagger._fill_gap(prompt, answer)
    if filled is None:
        return None, None
    text, start, end = filled
    sentence = sentence_tagger.tag_sentence(text)
    if sentence is None:
        return None, None

    offset = 0
    for token in sentence.tokens:
        token_start = offset
        token_end = token_start + len(token.text)
        if token_start < end and token_end > start:
            return sentence, token
        offset = token_end + len(token.whitespace)
    return sentence, None


def _sentence_has_past_participle(sentence: TaggedSentence) -> bool:
    """Whether ``sentence`` contains a verbal Partizip II. ``pos == "VERB"``
    is required alongside ``VerbForm=Part``: a participle spaCy has tagged
    ``ADJ`` is an attributive adjective ("ein gekochtes Ei"), not the second
    half of a periphrastic verb form."""
    return any(
        token.pos == "VERB" and token.morph.get("VerbForm") == "Part" for token in sentence.tokens
    )


# ---- Gloss -> detected tense buckets ----------------------------------------

_FUTURE_MARKERS = frozenset({"will", "shall", "gonna"})
_CONDITIONAL_MARKERS = frozenset({"would", "could", "might"})
_PRESENT_AUX_CLOSED_LIST = frozenset({"is", "am", "are", "do", "does"})
_PAST_AUX_CLOSED_LIST = frozenset({"was", "were", "did"})
_PERFECT_AUX_CLOSED_LIST = frozenset({"has", "have", "had"})
# Deliberately excludes tokens under 4 letters (avoids "red", "bed", "led",
# ...) and a short list of common non-verb -ed words this taxonomy's
# gold/pilot vocabulary is likely to produce. Still a heuristic, not a
# lexicon lookup -- see the module docstring's honesty note.
_PAST_ED_FALSE_POSITIVES = frozenset({"bored", "tired", "interested", "excited"})
_PAST_ED_PATTERN = re.compile(r"^[a-z]{4,}ed$")

#: Every English enclitic this module expands, mapped to the full words it
#: can stand for. ``'d`` and ``'s`` are genuinely ambiguous ("I'd" is "I
#: would" or "I had", "he's" is "he is" or "he has"), so each expands to a
#: SET and a match against ANY member counts. That is the right disposition
#: of the ambiguity for THIS check, whose job is catching a contradiction
#: rather than proving agreement: forcing a single reading would invent a
#: contradiction out of a coin flip. It is also safe in the classification
#: below, which tests the confirming set BEFORE the contradicting one, so an
#: over-generated reading can only ever turn "unverified" into "consistent",
#: never a real confirmation into a rejection.
#:
#: Without this the gloss tokeniser (``[A-Za-z']+``, which keeps the
#: apostrophe) glued "wouldn't", "could've" and "I'll" into single tokens
#: that matched no marker list at all, which is why the first glossed pilot
#: read "I'll be lonely after you've gone." as present tense.
_CONTRACTION_READINGS: dict[str, frozenset[str]] = {
    "n't": frozenset({"not"}),
    "'ll": frozenset({"will"}),
    "'ve": frozenset({"have"}),
    "'re": frozenset({"are"}),
    "'d": frozenset({"would", "had"}),
    "'s": frozenset({"is", "has"}),
    "'m": frozenset({"am"}),
}


def _token_readings(token: str) -> frozenset[str]:
    """Every full English word ``token`` (already lowercased) can stand for,
    peeling off enclitics from the right: "wouldn't" -> would, not;
    "could've" -> could, have; "he'd" -> he, would, had. A token with no
    enclitic is its own single reading."""
    readings: set[str] = set()
    rest = token
    while True:
        for clitic, expansions in _CONTRACTION_READINGS.items():
            if len(rest) > len(clitic) and rest.endswith(clitic):
                readings |= expansions
                rest = rest[: -len(clitic)]
                break
        else:
            break
    if rest and rest != "'":
        readings.add(rest)
    return frozenset(readings)


def _english_word_readings(gloss: str) -> frozenset[str]:
    """Every full English word any token of ``gloss`` can stand for, with
    enclitics expanded (:func:`_token_readings`)."""
    readings: set[str] = set()
    for token in re.findall(r"[A-Za-z']+", gloss.lower()):
        readings |= _token_readings(token)
    return frozenset(readings)


def _future_will_is_only_a_noun(doc: Doc) -> bool:
    """Whether every token of ``doc`` that could read as the word "will" is
    tagged a ``NOUN``, so that the lexical future-marker scan below must not
    count it.

    The second glossed pilot rejected "Er ___ nach Gottes Willen nicht
    getötet werden." / "According to God's will, he must not be killed." as
    a present target glossed future, because "will" is on
    :data:`_FUTURE_MARKERS` and the scan is purely lexical. "God's will" is
    an ordinary English noun and marks no tense whatsoever.

    This is English-internal homography, not the German-string-inside-English
    fault :data:`_ENGLISH_SHARED_SPELLINGS` handles, so that list cannot fix
    it: "will" genuinely IS an English future auxiliary as well, and
    exempting the spelling outright would blind the check to every real
    future gloss ("Tom will win."). The distinction is a part-of-speech one,
    and this module already loads an English tagger for the tense and person
    morphology it cannot do without, so asking that tagger is not a new
    dependency -- it is the one source of the answer already present.

    Requires ALL such tokens to be nominal, not merely one: "It is the will
    of God that he will not be killed." contains both, and the auxiliary is
    real future marking that must survive. Enclitics count as candidates too
    (``_token_readings("I'll")`` includes "will"), and spaCy tags "'ll"
    ``AUX``, so a contracted future is never mistaken for a noun.
    """
    candidates = [tok for tok in doc if "will" in _token_readings(tok.lower_)]
    return bool(candidates) and all(tok.pos_ == "NOUN" for tok in candidates)


def _gloss_tense_buckets(gloss: str) -> tuple[frozenset[_TenseBucket], _AnalysisSource]:
    """Every tense bucket detectable ANYWHERE in ``gloss``, plus which
    analysis path produced it. Never raises; an unparseable or empty gloss
    just yields an empty set (surfaced by the caller as "unverified").
    """
    tokens = re.findall(r"[A-Za-z']+", gloss.lower())
    readings = _english_word_readings(gloss)

    nlp = _load_en_model()
    doc = nlp(gloss) if nlp is not None else None
    if doc is not None and _future_will_is_only_a_noun(doc):
        readings -= {"will"}

    buckets: set[_TenseBucket] = set()
    if readings & _FUTURE_MARKERS:
        buckets.add("future")
    if readings & _CONDITIONAL_MARKERS:
        buckets.add("conditional")

    if doc is not None:
        has_perfect_aux = False
        has_participle = False
        for tok in doc:
            feats = tok.morph.to_dict()
            if feats.get("VerbForm") == "Fin":
                if feats.get("Tense") == "Pres":
                    buckets.add("present")
                elif feats.get("Tense") == "Past":
                    buckets.add("past")
            # Not gated on VerbForm=Fin: in "Tom could've won" spaCy reads
            # the "'ve" as an infinitive under the modal, and it is still the
            # perfect auxiliary. Gated on POS instead, which is what keeps a
            # possessive "'s" ("Tom's book", tagged PART) from being read as
            # "has".
            if tok.pos_ in ("AUX", "VERB") and _token_readings(tok.lower_) & (
                _PERFECT_AUX_CLOSED_LIST
            ):
                has_perfect_aux = True
            if tok.tag_ == "VBN":
                has_participle = True
        if has_perfect_aux and has_participle:
            buckets.add("perfect")
        return frozenset(buckets), "spacy"

    # Closed-list fallback: lexical only, no morphology at all. This is
    # deliberately conservative -- it will under-detect "present" for a
    # main verb with no auxiliary ("he lives" with no accompanying
    # is/does), returning no evidence (-> UNVERIFIED downstream) rather
    # than guess from a suffix, since a bare "-s" ending is indistinguishable
    # from an ordinary plural noun without a real tagger.
    if readings & _PRESENT_AUX_CLOSED_LIST:
        buckets.add("present")
    if readings & _PAST_AUX_CLOSED_LIST:
        buckets.add("past")
    has_perfect_aux_cl = bool(readings & _PERFECT_AUX_CLOSED_LIST)
    has_past_participle_guess = any(
        _PAST_ED_PATTERN.match(t) and t not in _PAST_ED_FALSE_POSITIVES for t in tokens
    )
    if has_perfect_aux_cl:
        buckets.add("perfect")
    elif has_past_participle_guess:
        buckets.add("past")
    return frozenset(buckets), "closed_list"


# ---- Gloss -> detected person/number ----------------------------------------

_EXPECTED_PRONOUNS: dict[tuple[str, str], frozenset[str]] = {
    ("1", "Sing"): frozenset({"i"}),
    ("1", "Plur"): frozenset({"we"}),
    ("2", "Sing"): frozenset({"you"}),
    ("2", "Plur"): frozenset({"you"}),
    ("3", "Sing"): frozenset({"he", "she", "it"}),
    ("3", "Plur"): frozenset({"they"}),
}

#: spaCy dependency labels for a clause's subject. ``expl`` ("there is ...")
#: is deliberately excluded: an expletive is not a referential subject and
#: says nothing about the German target's person.
_SUBJECT_DEPS = frozenset({"nsubj", "nsubjpass", "csubj", "csubjpass"})

_SIE_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")


def _third_plural_expected_pronouns(prompt: str) -> frozenset[str]:
    """German formal address ("Sie", capitalized, always) and 3rd-person
    plural ("sie", lowercase) share the identical Person=3|Number=Plur verb
    agreement -- the answer's own morphology cannot distinguish "Koennten
    Sie mir helfen?" (formal "you") from "Koennten sie mir helfen?" ("could
    they help me?"). The static ``_EXPECTED_PRONOUNS`` table alone would
    wrongly demand "they" for a formal-address item whose only correct
    English gloss is "you".

    Refines the expectation by scanning ``prompt`` (case matters: this must
    run on the ORIGINAL prompt, not a lowercased copy) for a capitalized
    "Sie" or lowercase "sie" token. A capitalized "Sie" NOT at the very
    start of the sentence is unambiguous formal address (German capitalizes
    both an ordinary sentence-initial word and "Sie" the same way, so only
    a non-initial position is decisive); a lowercase "sie" is unambiguous
    3rd plural (formal "Sie" is never written lowercase). Sentence-initial
    "Sie"/no matching token at all leaves both readings open, which is the
    honest, non-committal default -- either English word satisfies it.
    """
    tokens = _SIE_TOKEN.findall(prompt)
    for i, tok in enumerate(tokens):
        if tok == "Sie" and i != 0:
            return frozenset({"you"})
        if tok == "sie":
            return frozenset({"they"})
    return frozenset({"they", "you"})


def _gloss_person_number_pairs(
    gloss: str,
) -> tuple[frozenset[tuple[str, str]], _AnalysisSource]:
    """Every (Person, Number) pair detectable anywhere in ``gloss``.

    spaCy path: reads the morph ``Person``/``Number`` feature off EVERY
    token, not only pronouns -- a finite verb's own agreement morphology
    ("lives" -> Person=3, Number=Sing) catches a proper-noun subject with no
    literal pronoun at all ("Anna lives in Berlin"), which the closed-list
    path below genuinely cannot. English "you" is genuinely unmarked for
    Number (spaCy correctly reports Person=2 with no Number feature at
    all), so a bare Person=2 with no Number yields BOTH ("2", "Sing") and
    ("2", "Plur") rather than being dropped for lacking a Number reading
    that does not exist in English to begin with.

    spaCy path, second source: a NOMINAL subject. "I think Tom will win.",
    "Are you sure everything will go well?", "I want this letter to be
    opened now." and "Tom went swimming with us yesterday." are all correct
    glosses of a German 3rd-singular target, and the first pilot rejected
    every one of them, because the only pronoun anywhere in each gloss
    belongs to a DIFFERENT clause ("I", "you", "us") and the German target's
    own person was nowhere to be seen. It was there: the subject is a noun
    ("Tom", "everything", "this letter"), and an English subject that is a
    noun rather than a pronoun is third person by definition. Every
    ``nsubj``/``nsubjpass``/``csubj`` token that carries no ``Person``
    feature of its own therefore contributes third person, in BOTH numbers.

    Third person with no number claim, deliberately. A nominal subject's
    English number is not evidence about the German subject's: spaCy marks
    the head of a coordination ("Howes' kite and board were found" -> "kite",
    Number=Sing) and of a partitive ("Most of the staff has left" -> "Most",
    no Number at all) in ways that say nothing about the German plural on
    the other side. Reading a number off one of those turns a correct gloss
    into a rejection, which is what the pilot measured. A PRONOUN subject is
    a different matter: English personal pronouns mark number honestly, and
    those keep their exact (Person, Number).
    """
    tokens = re.findall(r"[A-Za-z']+", gloss.lower())
    nlp = _load_en_model()
    if nlp is not None:
        doc = nlp(gloss)
        pairs: set[tuple[str, str]] = set()
        for tok in doc:
            feats = tok.morph.to_dict()
            person = feats.get("Person")
            number = feats.get("Number")
            if person and number:
                pairs.add((person, number))
            elif person == "2":
                pairs.add((person, "Sing"))
                pairs.add((person, "Plur"))
            elif not person and tok.dep_ in _SUBJECT_DEPS:
                pairs.add(("3", "Sing"))
                pairs.add(("3", "Plur"))
        return frozenset(pairs), "spacy"

    pairs = set()
    token_set = set(tokens)
    for (person, number), words in _EXPECTED_PRONOUNS.items():
        if token_set & words:
            pairs.add((person, number))
    return frozenset(pairs), "closed_list"


def _german_person_cells(
    answer_token: GermanToken | None, person: str, number: str
) -> list[tuple[str, str]] | None:
    """Every (Person, Number) cell the German answer could occupy, or
    ``None`` where that is undecidable and the person dimension therefore
    cannot be checked at all.

    This is the second half of the pilot's person-check failure, and it is
    the larger half. "Wenn ich mehr Zeit ___, würde ich mehr studieren."
    has "hätte" as its answer; German's 1st and 3rd singular Konjunktiv II
    are spelled identically, the tagger picked 3rd, and the gloss ("If I had
    more time, I would study more.") correctly says "I". The same syncretism
    rejected "sind"/"waren" (1st and 3rd plural) and "kommt" (3rd singular
    and 2nd plural) items.

    ``selectors._finite_verb_cells`` already answers exactly this question,
    for exactly this reason (docs/audits/cycle-11-corpus-report.md defect
    class 2, where a syncretic verb made a blanked subject pronoun
    unrecoverable), by reconstructing the whole paradigm and looking the
    surface form up in it. It is reused here rather than reimplemented;
    ``uniqueness.py`` already imports its sibling the same way.

    Enumerating the cells rather than merely skipping every ambiguous one
    is what keeps the check's real job. "Wohnen sie in Hamburg?" glossed
    "Does she live in Hamburg?" is still rejected: "wohnen" is 1st OR 3rd
    plural, and "she" is neither. Only a gloss that matches ONE of the
    readings the German form genuinely has is let through.

    ``None`` (undecidable) is returned where there is no answer token at all
    (tagger unavailable, no aligning token) or the verb's paradigm is not in
    any table -- the skip-rather-than-guess direction. A NON-finite answer
    (a pronoun, an article, an adjective ending) is not a verb cell at all,
    and keeps the tagger's own single reading exactly as before.
    """
    if answer_token is None:
        return None
    if answer_token.morph.get("VerbForm") != "Fin":
        return [(person, number)]

    from src.generation.blanking.selectors import _finite_verb_cells

    cells = _finite_verb_cells(answer_token, person, number)
    return cells or None


#: The English subject pronouns an impersonal "man" clause can legitimately
#: be glossed with, beyond the "he"/"she"/"it" its 3rd-singular agreement
#: predicts. "man" is grammatically third person singular and is rendered in
#: English as "you" ("You can't smoke at school."), "one" ("One cannot smoke
#: at school."), "people" or "we" -- and as a passive with no personal
#: subject at all ("Smoking is not allowed at school."), whose nominal
#: subject :func:`_gloss_person_number_pairs` already reads as third person.
#: "one" needs no entry here for the same reason: spaCy gives it no
#: ``Person`` feature, so it too arrives as a nominal third-person subject.
_MAN_GLOSS_PRONOUNS = frozenset({"you", "we", "they"})


def _has_impersonal_man(sentence: TaggedSentence | None) -> bool:
    """Whether ``sentence`` has an impersonal nominative "man" in it.

    Matched as a PRONOUN specifically, so the noun "Mann" (a different
    spelling anyway) and any other reading cannot trigger it, and on the
    same "anywhere in the sentence" basis as the impersonal-"es" test below,
    for the same reason: this module has no dependency parse of the German.
    """
    if sentence is None:
        return False
    return any(
        token.pos == "PRON"
        and token.text.lower() == "man"
        and token.morph.get("Case") in (None, "Nom")
        for token in sentence.tokens
    )


def _impersonal_promoted_pronouns(sentence: TaggedSentence | None) -> frozenset[str]:
    """The English subject pronouns a German impersonal clause may
    legitimately be glossed with, beyond the ones its own verb agreement
    predicts.

    German builds a whole family of constructions on a dummy nominative
    subject whose English translation has a completely different subject:
    the experiencer, which German leaves in the accusative or dative,
    becomes the English subject. With "es": "Würde es dich stören, das
    Fenster zu öffnen?" is "Would you mind opening the window?"; "Es gefällt
    mir." is "I like it.". With "man" the same promotion happens through the
    English passive: "Man hat mir gesagt, dass ..." is "I was told that
    ...". The German verb agrees with the dummy subject (3rd singular), so
    demanding "he", "she" or "it" in the gloss rejects the only natural
    English rendering there is -- which is what the first glossed pilot did
    for "es" and the second did for "man".

    Gated on a nominative "es" or "man" actually being present, so an
    ordinary transitive sentence with an oblique pronoun ("Er gibt mir das
    Buch.") gains nothing and keeps being checked normally. Deliberately
    coarse in one respect: this module has no dependency parse of the German
    (the blanking tagger excludes the parser), so the dummy subject is looked
    for anywhere in the sentence rather than specifically as the answer's own
    subject. That errs towards accepting, which is the direction a check with
    a measured 33-to-1 false-positive rate should err in.
    """
    if sentence is None:
        return frozenset()
    has_impersonal_es = any(
        token.text.lower() == "es" and token.morph.get("Case") in (None, "Nom")
        for token in sentence.tokens
    )
    if not has_impersonal_es and not _has_impersonal_man(sentence):
        return frozenset()

    promoted: set[str] = set()
    for token in sentence.tokens:
        if token.pos != "PRON" or token.morph.get("Case") not in ("Acc", "Dat"):
            continue
        person = token.morph.get("Person")
        number = token.morph.get("Number")
        if person and number:
            promoted |= _EXPECTED_PRONOUNS.get((person, number), frozenset())
    return frozenset(promoted)


def _expected_gloss_pronouns(
    prompt: str,
    sentence: TaggedSentence | None,
    answer_token: GermanToken | None,
    person: str,
    number: str,
) -> frozenset[str] | None:
    """Every English subject pronoun that would be consistent with the
    German answer, or ``None`` if the person dimension is not checkable for
    this answer at all (see :func:`_german_person_cells`)."""
    cells = _german_person_cells(answer_token, person, number)
    if cells is None:
        return None

    expected: set[str] = set()
    for cell in cells:
        if cell == ("3", "Plur"):
            expected |= _third_plural_expected_pronouns(prompt)
        else:
            expected |= _EXPECTED_PRONOUNS.get(cell, frozenset())
    if ("3", "Sing") in cells:
        expected |= _impersonal_promoted_pronouns(sentence)
        if _has_impersonal_man(sentence):
            expected |= _MAN_GLOSS_PRONOUNS
    return frozenset(expected) or None


#: German strings that are also ordinary English words, so that finding one
#: of them in an ENGLISH gloss is no evidence of a German leak. Used by both
#: string-matching checks in this module -- the bare-answer-token check and
#: the grammar-terminology check -- because both suffer the identical fault:
#: they match German strings against English text.
#:
#: The list is scoped, not general. It covers only what can actually be
#: matched here: the grammar terms on ``PromptBuilder``'s own blocklist and
#: metalanguage stems ("fall" = Kasus, "modus", "form"), and the shapes this
#: taxonomy's ANSWER slots can take -- articles, pronouns, prepositions,
#: adjective forms and finite verb forms. It contains no nouns, because no
#: topic in this taxonomy blanks a noun.
#:
#: The cost, stated plainly: an answer that really was left untranslated in
#: the gloss is no longer caught if its spelling happens to be on this list
#: ("war", "will", "man", "hat", ...). The pilot measured that trade at 2
#: false rejections against 0 observed true ones, and a shared-spelling hit
#: is exactly the case where the string in the gloss teaches the learner
#: nothing about the German answer. Add to the list when a new false
#: rejection is measured, not pre-emptively.
_ENGLISH_SHARED_SPELLINGS = frozenset(
    {
        # grammar terminology (PromptBuilder blocklist / metalanguage stems)
        "fall",
        "form",
        "modus",
        # function words
        "am",
        "an",
        "die",
        "den",
        "in",
        "man",
        "so",
        "was",
        # finite verb forms
        "band",
        "bin",
        "half",
        "hat",
        "rang",
        "rate",
        "sang",
        "sank",
        "sprang",
        "stand",
        "war",
        "will",
        # adjective forms
        "arm",
        "warm",
        "wild",
    }
)


def _is_german_only(term: str) -> bool:
    """Whether ``term`` is a German string with no ordinary English word of
    the same spelling, so that finding it in an English gloss really is
    evidence of German text (see :data:`_ENGLISH_SHARED_SPELLINGS`)."""
    return term.strip().lower() not in _ENGLISH_SHARED_SPELLINGS


def _contains_answer_leak(gloss: str, answer: str) -> bool:
    """True if the bare German ``answer`` token itself appears in ``gloss``
    as a whole word AND that spelling is not also an ordinary English word.

    The original rule ("the answer is only ever a German word, so its
    literal appearance in an English translation is a leak") rested on a
    premise that is simply false: German and English share a great many
    spellings, and the first glossed pilot rejected "The summit meeting at
    Lancaster House was initially planned as just one of several on the
    Ukraine war." because the German answer was "war" (= "was"). The English
    noun "war" hands the learner nothing.

    The check keeps its real job. A genuine leak is an untranslated German
    word sitting in English text where no English word of that spelling
    fits: "He wohnt in Berlin." still fires, because "wohnt" is not English.
    What no longer fires is a match on a spelling both languages have --
    see :data:`_ENGLISH_SHARED_SPELLINGS` for the list and its cost.
    """
    answer = answer.strip()
    if not answer:
        return False
    if not _is_german_only(answer):
        return False
    pattern = r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])" + re.escape(answer) + r"(?![A-Za-zÀ-ÖØ-öø-ÿ])"
    return re.search(pattern, gloss, flags=re.IGNORECASE) is not None


def _gloss_topic_leaks(gloss: str) -> list[str]:
    """Grammar-terminology leaks in an ENGLISH gloss.

    ``PromptBuilder.check_for_topic_leaks`` is a list of GERMAN metalanguage
    matched as whole words, and it must stay that way: on a German prompt
    "Fall" really does name the grammatical case and really is a leak. Run
    unchanged against an English gloss it misfires on shared spellings, and
    the first glossed pilot duly rejected "Ministers usually fall on the
    good side like sandwiches." for the English verb "fall".

    The correction is not to weaken the blocklist but to read it in the
    right language: in English text, a hit on a spelling that is an ordinary
    English word is not evidence that the gloss names a grammar topic. The
    English reader of "fall" gets "fall", not "Kasus". Terms with no English
    homograph ("dativ", "perfekt", "konjunktiv", ...) are unaffected and
    still reject.
    """
    return [term for term in PromptBuilder.check_for_topic_leaks(gloss) if _is_german_only(term)]


# ---- number: a plural German answer against a singular English gloss -------
#
# docs/known-defects.md 2.15, TODO.md item 1. This is the one wrong-gloss kind
# the model verification pass caught NOTHING of: 0 of 6, where tense scored 6
# of 6 and polarity 5 of 6. It is also the kind that matters most, because it
# is the kind that changes the answer. A tense slip in the English still leaves
# "Haeuser" the only thing that fits the gap; a number slip actively points the
# learner at "Haus".
#
# **Only the plural direction is checked.** German plural answer against an
# English gloss with no plural anywhere is a real signal. The reverse (singular
# answer, plural somewhere in the gloss) is not: English sentences carry plural
# nouns for all sorts of reasons that have nothing to do with the blanked word,
# so it would fire constantly on correct items.
#
# **No alignment is attempted, deliberately.** There is no bilingual dictionary
# here, so nothing can say that "Haeuser" corresponds to "houses" rather than to
# some other noun in the sentence. An earlier draft tried to name the offending
# singular noun and fired on "her son" in a gloss whose actual defect was
# "a different experience" -- the right verdict for the wrong reason, and a
# false positive waiting to happen on any gloss containing an incidental
# singular. The claim made here is weaker and survives that: a plural content
# word in the German should leave SOME plural marker somewhere in its English.
#
# **Measured**, against `data/fixtures/adversarial/wrong_glosses.jsonl` and the
# 430 accepted items of the last corpus pilot:
#
#     6 of 6 wrong_number rows caught  (the model pass caught 0)
#     0 false positives on the fixture's 36 correct glosses
#     1 false positive in 31 applicable items of 430 real accepted items
#
# That one is `schwarze Zahlen schreiben` glossed "the company was in the
# black" -- a correct idiomatic translation whose English simply has no plural.
# It is irreducible without a dictionary of idioms, and it is the reason this
# dimension reports rather than rejects on its own (see `--enforce-gloss-check`,
# TODO.md item 11).

#: English plural pronouns and quantifiers. Presence of any of these is plural
#: evidence even when no NNS noun is present ("I saw several").
_PLURAL_PRONOUNS = frozenset({"they", "them", "these", "those", "we", "us", "both"})
_PLURAL_QUANTIFIERS = frozenset({"many", "several", "few", "various", "numerous", "multiple"})
#: A VBP with one of these as its subject is first/second person singular
#: ("I complain"), not a plural agreement.
_SINGULAR_SUBJECT_PRONOUNS = frozenset({"i", "he", "she", "it"})

#: English nouns that are singular in form and cannot be counted, so a German
#: plural maps onto them legitimately: "die Haare" is "hair", "die Moebel" is
#: "furniture". Consulted ONLY when the noun carries no indefinite article,
#: because a mass noun that takes "a" is being used countably ("a different
#: experience") and is then ordinary evidence of singularity.
#:
#: A closed list is the wrong tool for German verb government (hundreds of
#: verbs, always growing -- see scripts/build_verb_government.py). English
#: uncountables are a genuinely closed and small class, so it is the right tool
#: here. "hair" was added after it produced a real false positive on
#: "die Haare schneiden" -> "cuts his own hair".
_UNCOUNTABLE_EN = frozenset(
    {
        "advice",
        "baggage",
        "bread",
        "butter",
        "cash",
        "cheese",
        "clothing",
        "content",
        "damage",
        "electricity",
        "equipment",
        "evidence",
        "experience",
        "feedback",
        "food",
        "furniture",
        "garbage",
        "hair",
        "hardware",
        "help",
        "homework",
        "information",
        "jewellery",
        "jewelry",
        "knowledge",
        "luggage",
        "machinery",
        "mail",
        "meat",
        "milk",
        "money",
        "music",
        "news",
        "police",
        "produce",
        "progress",
        "rain",
        "research",
        "rice",
        "rubbish",
        "salt",
        "sand",
        "snow",
        "software",
        "staff",
        "stuff",
        "sugar",
        "traffic",
        "trouble",
        "water",
        "weather",
        "work",
    }
)


def _has_indefinite_article(token: Any) -> bool:
    return any(c.dep_ == "det" and c.lower_ in ("a", "an") for c in token.children)


def _subject_of(token: Any) -> Any | None:
    for child in token.children:
        if child.dep_ in ("nsubj", "nsubjpass"):
            return child
    return None


def _gloss_has_plural_marker(doc: Doc) -> str | None:
    """A description of the first plural marker in ``doc``, or ``None``."""
    for token in doc:
        if token.tag_ in ("NNS", "NNPS"):
            return f"plural noun {token.text!r}"
        if token.lower_ in _PLURAL_PRONOUNS:
            return f"plural pronoun {token.text!r}"
        if token.lower_ in _PLURAL_QUANTIFIERS:
            return f"plural quantifier {token.text!r}"
        if token.tag_ == "VBP" and token.lemma_.lower() != "be":
            subject = _subject_of(token)
            if subject is None or subject.lower_ not in _SINGULAR_SUBJECT_PRONOUNS:
                return f"plural-agreeing verb {token.text!r}"
    return None


def _gloss_uncountable_noun(doc: Doc) -> str | None:
    """An uncountable noun a German plural could legitimately map onto."""
    for token in doc:
        if (
            token.tag_ == "NN"
            and token.lemma_.lower() in _UNCOUNTABLE_EN
            and not _has_indefinite_article(token)
        ):
            return token.text
    return None


def _gloss_main_verb_is_singular(doc: Doc) -> str | None:
    """A singular-agreeing MAIN verb with a singular subject, or ``None``.

    Only the matrix clause counts. A relative clause is free to be singular
    while the main clause is plural -- "the machines that his company produces
    are superior" -- so scanning every VBZ would fire on the wrong verb and
    call a correct gloss inconsistent.
    """
    for sent in doc.sents:
        root = sent.root
        for candidate in [root, *[c for c in root.children if c.dep_ == "cop"]]:
            if candidate.tag_ != "VBZ":
                continue
            subject = _subject_of(root) or _subject_of(candidate)
            if subject is None:
                continue
            if subject.tag_ in ("NNS", "NNPS") or subject.lower_ in _PLURAL_PRONOUNS:
                continue
            return f"main verb {candidate.text!r} with singular subject {subject.text!r}"
    return None


def _classify_number(answer_token: GermanToken | None, gloss: str) -> tuple[_DimensionVerdict, str]:
    """Compare a plural German answer against the gloss's own number marking.

    Returns ``("unverified", reason)`` for everything this cannot speak to,
    which is most items: a singular answer, a non-content answer, no English
    model, or a gloss whose only candidate noun is uncountable.
    """
    if answer_token is None:
        return "unverified", "no answer token"
    if (answer_token.morph or {}).get("Number") != "Plur":
        return "unverified", "answer is not plural"
    if answer_token.pos not in ("NOUN", "PROPN", "VERB", "AUX"):
        return "unverified", f"answer pos={answer_token.pos} carries no number to check"

    nlp = _load_en_model()
    if nlp is None:
        return "unverified", "no English model"
    doc = nlp(gloss)

    plural_marker = _gloss_has_plural_marker(doc)
    if plural_marker is not None:
        return "consistent", plural_marker

    if answer_token.pos in ("NOUN", "PROPN"):
        uncountable = _gloss_uncountable_noun(doc)
        if uncountable is not None:
            return "unverified", f"gloss noun {uncountable!r} is uncountable"
        return (
            "inconsistent",
            "the German answer is plural but the gloss carries no plural marker at all",
        )

    singular_verb = _gloss_main_verb_is_singular(doc)
    if singular_verb is not None:
        return "inconsistent", f"the German answer is plural but the gloss has a {singular_verb}"
    return "unverified", "gloss shows no number marking"


def validate_gloss_consistency(
    prompt: str,
    answer: str,
    gloss_en: str,
    topic: Topic | None = None,
) -> GlossCheckResult:
    """Mechanically check that ``gloss_en`` agrees with ``answer``'s own
    computed German features, and leaks neither grammar terminology nor the
    bare answer token. See the module docstring for exactly what "agrees"
    means (presence-based bucket matching, not full alignment) and what is
    a real check versus a documented proxy.
    """
    stripped = gloss_en.strip()
    if not stripped:
        return GlossCheckResult(
            consistent=False,
            reason="gloss_en is empty.",
            checked_dimensions=(),
            unverified_dimensions=(),
            analysis_source="unavailable",
        )

    leaked_terms = _gloss_topic_leaks(stripped)
    if leaked_terms:
        return GlossCheckResult(
            consistent=False,
            reason=f"Gloss leaks grammar terminology: {leaked_terms}.",
            checked_dimensions=(),
            unverified_dimensions=(),
            analysis_source="unavailable",
        )
    if _contains_answer_leak(stripped, answer):
        return GlossCheckResult(
            consistent=False,
            reason=f"Gloss contains the bare answer token {answer!r}.",
            checked_dimensions=(),
            unverified_dimensions=(),
            analysis_source="unavailable",
        )

    tagged = de_tagger.tag_answer(prompt, answer)
    de_feats = tagged.feats if tagged is not None else {}
    sentence, answer_token = _tag_gap_filled_sentence(prompt, answer)

    checked: list[GlossDimension] = []
    unverified: list[GlossDimension] = []
    reasons: list[str] = []
    source: Literal["spacy", "closed_list", "unavailable"] = "unavailable"

    target_bucket = _german_tense_bucket(de_feats, topic, answer_token, sentence)
    if target_bucket is not None:
        checked.append("tense")
        detected, tense_source = _gloss_tense_buckets(stripped)
        source = tense_source
        verdict = _classify_tense(
            target_bucket,
            detected,
            contradiction_possible=not _modal_tense_is_indeterminable(answer_token, stripped),
        )
        if verdict == "unverified":
            unverified.append("tense")
        elif verdict == "inconsistent":
            reasons.append(
                f"German target is {target_bucket!r}-tense but the gloss shows "
                f"only {sorted(detected)} tense marking."
            )

    unk = de_tagger_unk()
    de_person = de_feats.get("Person")
    de_number = de_feats.get("Number")
    if de_person and de_person != unk and de_number and de_number != unk:
        expected = _expected_gloss_pronouns(prompt, sentence, answer_token, de_person, de_number)
        if expected is not None:
            checked.append("person")
            detected_pairs, person_source = _gloss_person_number_pairs(stripped)
            if source == "unavailable":
                source = person_source
            detected_flat: set[str] = set()
            for pair in detected_pairs:
                detected_flat |= _EXPECTED_PRONOUNS.get(pair, frozenset())
            verdict = _classify(expected, frozenset(detected_flat))
            if verdict == "unverified":
                unverified.append("person")
            elif verdict == "inconsistent":
                reasons.append(
                    f"German target is Person={de_person}|Number={de_number} "
                    f"(expected one of {sorted(expected)} in the gloss) but "
                    f"none of that appears."
                )

    number_verdict, number_detail = _classify_number(answer_token, stripped)
    if number_verdict != "unverified" or de_number == "Plur":
        checked.append("number")
        if source == "unavailable" and _load_en_model() is not None:
            source = "spacy"
        if number_verdict == "unverified":
            unverified.append("number")
        elif number_verdict == "inconsistent":
            reasons.append(f"Number mismatch: {number_detail}.")

    return GlossCheckResult(
        consistent=not reasons,
        reason="; ".join(reasons) if reasons else None,
        checked_dimensions=tuple(checked),
        unverified_dimensions=tuple(unverified),
        analysis_source=source,
    )


def _classify(expected: frozenset[str], detected: frozenset[str]) -> _DimensionVerdict:
    """Shared presence-based classification (see module docstring): no
    evidence at all is UNVERIFIED, evidence for the expected value (however
    much else is also present) is CONSISTENT, evidence only for something
    else is INCONSISTENT.
    """
    if not detected:
        return "unverified"
    if detected & expected:
        return "consistent"
    return "inconsistent"


def _classify_tense(
    target: _TenseBucket,
    detected: frozenset[_TenseBucket],
    *,
    contradiction_possible: bool = True,
) -> _DimensionVerdict:
    """Presence-based classification for the tense dimension, where -- unlike
    person -- "not confirming" and "contradicting" are two different things
    (see :data:`_BUCKET_CONTRADICTED_BY`).

    Confirmation is tested FIRST, so a gloss that shows both the expected
    marking and something else is consistent, exactly as before. Only a
    gloss that shows positively contradicting marking and no confirming
    marking at all is inconsistent; anything else is unverified.

    ``contradiction_possible=False`` is the per-ITEM counterpart of
    :data:`_BUCKET_CONTRADICTED_BY`'s empty ``conditional`` row: where the
    German target's tense is not readable off this particular gloss at all
    (:func:`_modal_tense_is_indeterminable`), confirmation still counts and
    contradiction is downgraded to UNVERIFIED rather than invented.
    """
    if detected & _BUCKET_SATISFIED_BY[target]:
        return "consistent"
    if contradiction_possible and detected & _BUCKET_CONTRADICTED_BY[target]:
        return "inconsistent"
    return "unverified"


# ==============================================================================
# 4. Gloss-aware relief for layer3_solver's "open lexical slot" rejection.
#
# docs/audits/generation-track-plan.md Cycle 3: "Prompt provides no
# governing preposition, verb, or determiner that narrows the gap to one
# lexeme" was the largest single rejection bucket in the prior pilot. Most
# of that bucket is a tense/person topic (``morph_spec`` fixes ``Tense`` or
# ``Person``) whose distractors are OTHER tense/person forms of the SAME
# verb -- which is the expected, correct shape of a wrong answer for a
# conjugation-discrimination exercise, not a genuine "which word" ambiguity.
# ``layer3_solver._same_lexeme`` under-recognises this for a full (non-
# auxiliary, non-modal) verb whose principal parts do not share a long
# prefix ("kaeme" vs "kommt" -- no table anywhere in this codebase records
# strong-verb ablaut; only IRREGULAR_VERB_LEMMA's closed set of auxiliaries
# and modals is exempt from the prefix heuristic).
#
# The fix is NOT "trust the gloss to know which verb was meant" -- this
# module explicitly does not align gloss words to the German answer (see
# the module docstring). It is: confirm, from the German side alone via the
# lemma tagger, that a flagged distractor is the SAME VERB as the answer
# (so the true ambiguity is tense/person, not lexeme), and separately
# confirm, from the gloss, that the item's tense/person is not actually
# ambiguous at all -- both conditions together are what license dropping
# the rejection; neither alone does.
# ==============================================================================


def _lemma_at_gap(prompt: str, filler: str, gap_pos: int) -> str | None:
    """The German lemma of ``filler`` when it fills the ``___`` gap at
    ``gap_pos`` in ``prompt``, via ``src.generation.blanking.sentence_tagger``
    -- the only tagger in this codebase that keeps spaCy's lemmatizer pipe
    (``src.taxonomy.tagger`` deliberately excludes it; see that module's own
    docstring). ``None`` whenever the tagger is unavailable or no token
    aligns with the filled span; never raises. Only ever called from a
    caller that has already confirmed ``prompt`` has exactly one gap and
    ``item.cue is None`` (``layer3_solver._check_under_constrained``'s own
    precondition), so no bracketed cue needs stripping here the way
    ``src.taxonomy.tagger._fill_gap`` strips one.
    """
    from src.generation.blanking import sentence_tagger

    filler = filler.strip()
    if not filler:
        return None
    filled = prompt[:gap_pos] + filler + prompt[gap_pos + 3 :]
    tagged = sentence_tagger.tag_sentence(filled)
    if tagged is None:
        return None

    gap_end = gap_pos + len(filler)
    offset = 0
    for tok in tagged.tokens:
        tok_start = offset
        tok_end = tok_start + len(tok.text)
        if tok_start < gap_end and tok_end > gap_pos:
            return tok.lemma or None
        offset = tok_end + len(tok.whitespace)
    return None


def gloss_resolves_lexical_ambiguity(
    item: CandidateItem,
    topic: Topic | None,
    heterogeneous_distractors: Sequence[str],
    gap_pos: int,
) -> bool:
    """Whether ``item.gloss_en`` licenses dropping layer3_solver's "open
    lexical slot" rejection for THIS item's ``heterogeneous_distractors``.

    Conservative by construction, per the task's own instruction: this
    returns ``True`` only when BOTH halves hold, never on either alone.

    1. The topic's own ``morph_spec`` fixes ``Tense`` and/or ``Person`` --
       i.e. this topic is tense- or person-selecting (:func:`gloss_required`'s
       own topic-level test) -- and the gloss's mechanical check
       (:func:`validate_gloss_consistency`) GENUINELY CONFIRMS every such
       fixed dimension: it must appear in ``checked_dimensions`` and NOT in
       ``unverified_dimensions``, and the overall result must be
       ``consistent``. An unverified dimension supplies no evidence at all
       (see the module docstring) and must never be treated as a resolved
       ambiguity -- that is exactly the "unverified read as passed"
       dishonesty this project keeps having to correct.
    2. EVERY distractor in ``heterogeneous_distractors`` is, via the German
       lemma tagger (:func:`_lemma_at_gap`), the SAME VERB LEXEME as
       ``item.proposed_answer`` -- i.e. the ambiguity ``_same_lexeme``'s
       prefix heuristic missed really is a tense/person form of the one
       verb, not a different word. If the lemma cannot be determined for
       either side (tagger unavailable, or no aligning token) that
       distractor counts as UNRESOLVED, never as a free pass -- this
       mirrors the tense/person check's own "absence of evidence is not
       evidence of resolution" rule, applied to the lexeme dimension gloss
       validation itself never touches.

    A distractor that is a genuinely different verb (e.g. "kommt" proposed
    as a distractor for a "kaeme" answer) fails step 2 and the rejection
    still fires -- the gloss's confirmed tense/person says nothing about
    which VERB was meant (this module never aligns gloss words to the
    German answer), so it must never be trusted to rule out a free lexical
    choice.
    """
    if not heterogeneous_distractors:
        return False
    if not item.gloss_en or not item.gloss_en.strip():
        return False
    if topic is None:
        return False

    morph = topic.morph_spec or {}
    required_dims: set[GlossDimension] = set()
    if "Tense" in morph:
        required_dims.add("tense")
    if "Person" in morph:
        required_dims.add("person")
    if not required_dims:
        return False

    result = validate_gloss_consistency(item.prompt, item.proposed_answer, item.gloss_en, topic)
    if not result.consistent:
        return False
    if not required_dims.issubset(result.checked_dimensions):
        return False
    if required_dims & set(result.unverified_dimensions):
        return False

    answer_lemma = _lemma_at_gap(item.prompt, item.proposed_answer, gap_pos)
    if answer_lemma is None:
        return False
    for distractor in heterogeneous_distractors:
        distractor_lemma = _lemma_at_gap(item.prompt, distractor, gap_pos)
        if distractor_lemma is None or distractor_lemma != answer_lemma:
            return False
    return True
