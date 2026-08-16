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
must show up SOMEWHERE in the gloss (a subject pronoun, or -- spaCy path
only -- a finite verb's own agreement morphology, which also catches a
proper-noun subject like "Anna lives..." with no literal pronoun at all).
Gender is deliberately NOT checked (whether a German 3rd-singular target
glosses as "he", "she" or "it"): the task's own worked examples (1sg -> "I",
3pl -> "they") never need it, and getting it right would require aligning
the gloss's subject specifically to the German sentence's subject, which
this module does not attempt.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from src.contracts import CandidateItem, Topic
from src.generation.prompt_builder import PromptBuilder
from src.taxonomy import tagger as de_tagger

if TYPE_CHECKING:
    from spacy.language import Language

logger = logging.getLogger(__name__)

EN_MODEL_NAME = "en_core_web_sm"

GlossDimension = Literal["tense", "person"]
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
    # treated as interchangeable; present/future/conditional are not
    # collapsed into anything else.
    "present": frozenset({"present"}),
    "past": frozenset({"past", "perfect"}),
    "perfect": frozenset({"past", "perfect"}),
    "future": frozenset({"future"}),
    "conditional": frozenset({"conditional"}),
}


def _german_tense_bucket(feats: dict[str, str] | None, topic: Topic | None) -> _TenseBucket | None:
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


def _gloss_tense_buckets(gloss: str) -> tuple[frozenset[_TenseBucket], _AnalysisSource]:
    """Every tense bucket detectable ANYWHERE in ``gloss``, plus which
    analysis path produced it. Never raises; an unparseable or empty gloss
    just yields an empty set (surfaced by the caller as "unverified").
    """
    tokens = re.findall(r"[A-Za-z']+", gloss.lower())
    token_set = set(tokens)

    buckets: set[_TenseBucket] = set()
    if token_set & _FUTURE_MARKERS:
        buckets.add("future")
    if token_set & _CONDITIONAL_MARKERS:
        buckets.add("conditional")

    nlp = _load_en_model()
    if nlp is not None:
        doc = nlp(gloss)
        has_perfect_aux = False
        has_participle = False
        for tok in doc:
            feats = tok.morph.to_dict()
            if feats.get("VerbForm") == "Fin":
                if feats.get("Tense") == "Pres":
                    buckets.add("present")
                elif feats.get("Tense") == "Past":
                    buckets.add("past")
                if tok.lower_ in _PERFECT_AUX_CLOSED_LIST:
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
    if token_set & _PRESENT_AUX_CLOSED_LIST:
        buckets.add("present")
    if token_set & _PAST_AUX_CLOSED_LIST:
        buckets.add("past")
    has_perfect_aux_cl = bool(token_set & _PERFECT_AUX_CLOSED_LIST)
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
        return frozenset(pairs), "spacy"

    pairs = set()
    token_set = set(tokens)
    for (person, number), words in _EXPECTED_PRONOUNS.items():
        if token_set & words:
            pairs.add((person, number))
    return frozenset(pairs), "closed_list"


def _contains_answer_leak(gloss: str, answer: str) -> bool:
    """True if the bare German ``answer`` token itself appears in ``gloss``
    as a whole word -- the answer is only ever legitimately a German word,
    so its literal appearance in an English translation is a leak, not a
    coincidence (a German inflected verb form or article is not also an
    English word by chance)."""
    answer = answer.strip()
    if not answer:
        return False
    pattern = r"(?<![A-Za-zÀ-ÖØ-öø-ÿ])" + re.escape(answer) + r"(?![A-Za-zÀ-ÖØ-öø-ÿ])"
    return re.search(pattern, gloss, flags=re.IGNORECASE) is not None


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

    leaked_terms = PromptBuilder.check_for_topic_leaks(stripped)
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

    checked: list[GlossDimension] = []
    unverified: list[GlossDimension] = []
    reasons: list[str] = []
    source: Literal["spacy", "closed_list", "unavailable"] = "unavailable"

    target_bucket = _german_tense_bucket(de_feats, topic)
    if target_bucket is not None:
        checked.append("tense")
        detected, tense_source = _gloss_tense_buckets(stripped)
        source = tense_source
        verdict = _classify(_BUCKET_SATISFIED_BY[target_bucket], detected)
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
        checked.append("person")
        if de_person == "3" and de_number == "Plur":
            expected: frozenset[str] | None = _third_plural_expected_pronouns(prompt)
        else:
            expected = _EXPECTED_PRONOUNS.get((de_person, de_number))
        if expected is not None:
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
