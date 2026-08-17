"""Carrier soundness validation: is the model-generated CARRIER sentence
itself correct German, before anything is blanked from it.

Every other check in this package (``selectors``, ``blanker``, ``paradigms``)
answers "is the removed token a computable instance of this topic", which
makes the ANSWER hallucination-proof -- it is derived from a closed paradigm,
never asserted by a model. Nothing upstream of this module checked whether
the SENTENCE THE ANSWER WAS TAKEN FROM is itself grammatical. A pilot run
generated "Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch
ein." -- "kauft ich" is wrong; "ich" takes "kaufe" -- and that one bad
carrier produced two accepted items, one of which blanked the very verb that
was wrong. This module is the fix: it runs on a plain generated sentence,
before ``sentence_tagger.tag_sentence`` and ``selectors`` ever see it, and
discards anything that is not sound German, counting why.

## What is checked, and how honestly it is checked

Three real, mechanically-checkable properties, each backed by the
dependency parse (not a proxy for anything semantic):

1. **Subject-verb agreement.** A nominative subject (``sb``) or expletive
   subject (``ep`` -- "es regnet") must agree with its finite verb in Person
   and Number. This is read off the dependency arc, not word order, so it is
   correct for both "Ich komme" (SVO) and "Weil ich komme, ..." (subject
   after the conjunction). This is the exact check that catches "kauft ich".

2. **Exactly one finite verb per clause, and at least one finite verb
   present.** A sentence with zero finite verbs is a fragment. A finite verb
   that is not the sentence's ROOT and whose head is *itself* a finite verb
   is only a legitimate second clause (subordinate: "..., dass er kommt") if
   it carries an explicit subordinating conjunction (a ``cp`` child) --
   coordinated main clauses ("... und du kommst") and relative clauses
   ("..., der dort steht") never trigger this path at all, because they
   attach to the coordinating conjunction or the antecedent noun
   respectively, never directly to another finite verb. A finite verb
   attached straight to another finite verb with no subordinator is
   indistinguishable from a run-on / a missing conjunction, so it is
   rejected.

3. **Sentence completeness.** Capitalised start, terminal punctuation, at
   least a handful of tokens, and the sentence does not end on a token whose
   own tag demands a continuation (a bare article, preposition or
   conjunction) -- a dangling fragment a generation call got cut off on.

## What is a real check versus a documented proxy

Every rejection below is conservative by design (CLAUDE.md: discarding a
good sentence is cheap, keeping a bad one is expensive), which means several
checks knowingly reject sentences that are actually fine German, because
this module cannot tell the difference confidently enough to accept them:

* **Coordinated subjects** ("Der Mann und die Frau tanzen") are detected
  (a ``cd`` child on the subject noun) and rejected as
  ``agreement_undecidable`` rather than resolved -- German coordinate-subject
  person resolution ("du und ich" takes "wir"-agreement, "du und er" takes
  "ihr"-agreement) is a real rule this module does not implement.
* **Subjectless dative-experiencer constructions** ("Mir ist kalt.") have no
  ``sb``/``ep`` child at all and are rejected as ``no_subject_found``, even
  though they are perfectly grammatical -- there is nothing to check
  agreement against, and a genuinely subject-dropped error looks identical
  from here.
* **Bare informal imperatives** ("Geh nach Hause!") are rejected, but not by
  design: verified empirically that ``de_core_news_sm`` systematically
  mistags a subjectless second-person imperative as a noun (``NN``/``PROPN``
  ROOT, not ``VVFIN``), so it never reaches the finite-verb check at all and
  is discarded as ``no_finite_verb``. Formal imperatives with an explicit
  "Sie" ("Kommen Sie bitte her.") are unaffected -- they have a real subject
  and tag correctly.
* **Person/Number syncretism tolerance.** German verb morphology has four
  real, categorical surface ambiguities where two different (Person, Number)
  cells always share one form: 1st-plural/3rd-plural in every tense ("wir
  machen"/"sie machen"), 1st-singular/3rd-singular in the preterite and
  Konjunktiv II ("ich sah"/"er sah", "ich hätte"/"er hätte"), 2nd-plural/
  3rd-singular in the present tense ("ihr macht"/"er macht"), and 2nd-/
  3rd-singular present for sibilant-stem verbs ("du vergisst"/"er
  vergisst"). ``_is_syncretism_tolerated`` treats all four as agreement
  rather than disagreement -- verified empirically for the 2nd-plural case
  that ``de_core_news_sm``'s morphologizer resolves the ambiguity toward
  3rd-singular regardless of the actual subject (every "ihr <verb>t"
  sentence tested tags Person=3/Number=Sing; "ihr habt"/"ihr seid", which
  are NOT syncretic, tag correctly as Person=2/Plur), and the other three
  are the same shape of fact even where this module has not separately
  logged a verification run for every lexeme. The known cost: a genuine
  substitution of the wrong cell within a tolerated pair (e.g. a
  3rd-singular ablauted form used where the syncretic-but-different
  2nd-plural form was meant on a strong verb) is surface-identical to the
  tolerated case and will not be caught.

Two things that are checks, not proxies, worth calling out because they
look like they might be gaps: **subject-gapped coordination** ("Ein Mann
steht vor der Tür und wartet." -- one subject, two coordinated verbs) is
resolved, not rejected: the second conjunct's agreement is checked against
the first conjunct's real subject (``_resolve_subject``), not reported as
"no subject found". And **"man"/"jemand"/other indefinite pronoun
subjects** default to 3rd person the same way a noun subject does (they are
3rd person by definition in German, regardless of notional number), so
"Während der Prüfung darf man nicht sprechen." is accepted, not discarded
for lacking a Person feature.

Semantic incoherence (a sentence that parses and agrees but makes no sense)
is explicitly out of scope -- not mechanically checkable, per the task that
commissioned this module.

## Why this module loads its own spaCy pipeline

``src.taxonomy.tagger`` and ``src.generation.blanking.sentence_tagger`` both
exclude the dependency parser (neither of their callers ever needed it, and
it is the most expensive component to run). Subject-verb agreement is
fundamentally a dependency-tree question -- word order alone cannot answer
it, per this module's own reason for existing -- so this module needs the
parser and cannot reuse either loader. It mirrors both of their fail-safe
contracts exactly: lazy load, ``functools.lru_cache``, never raises, ``None``
whenever spaCy or the model is missing, degrading to a single counted
rejection reason rather than a crash.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from src.taxonomy.tagger import MODEL_NAME

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Token as SpacyToken

# -- Rejection reasons, named so callers and tests can key off a stable
# string rather than re-deriving prose -----------------------------------
REASON_EMPTY_SENTENCE = "empty_sentence"
REASON_SPACY_UNAVAILABLE = "spacy_unavailable"
REASON_NOT_CAPITALIZED = "not_capitalized"
REASON_NO_TERMINAL_PUNCTUATION = "no_terminal_punctuation"
REASON_FRAGMENT_TOO_SHORT = "fragment_too_short"
REASON_DANGLING_FRAGMENT = "dangling_fragment"
REASON_MULTIPLE_SENTENCES = "multiple_sentences"
REASON_NO_FINITE_VERB = "no_finite_verb"
REASON_MISSING_CLAUSE_CONNECTOR = "missing_clause_connector"
REASON_NO_SUBJECT_FOUND = "no_subject_found"
REASON_AGREEMENT_UNDECIDABLE = "agreement_undecidable"
REASON_SUBJECT_VERB_DISAGREEMENT = "subject_verb_disagreement"

# STTS fine-grained tags for a finite verb: full verb, auxiliary, modal, and
# their imperative counterparts (imperative is a finite mood, not a
# non-finite form -- see the module docstring's note on why bare informal
# imperatives are rejected anyway, for an unrelated reason).
_FINITE_TAGS: frozenset[str] = frozenset({"VVFIN", "VAFIN", "VMFIN", "VVIMP", "VAIMP"})

# Dependency labels de_core_news_sm uses for a clause's grammatical subject
# ("sb") and an expletive/dummy subject ("ep" -- "es regnet", "es gibt").
_SUBJECT_DEPS: frozenset[str] = frozenset({"sb", "ep"})

# A token bearing one of these fine-grained tags at the very end of a
# sentence (its final non-punctuation token) is still expecting something to
# follow -- a bare article, preposition, or conjunction, the shape a
# truncated generation call leaves behind.
_CONTINUATION_EXPECTING_TAGS: frozenset[str] = frozenset(
    {
        "ART",
        "PIAT",
        "PPOSAT",
        "APPR",
        "APPRART",
        "APPO",
        "KON",
        "KOUS",
        "KOUI",
        "KOKOM",
        "PTKZU",
    }
)

_TERMINAL_PUNCTUATION = re.compile(r"[.!?…]['\"”’)]*\s*$")
_MIN_TOKEN_COUNT = 3


@lru_cache(maxsize=1)
def _load_model() -> Language | None:
    """Load ``de_core_news_sm`` WITH the dependency parser, once per process.

    Deliberately its own loader rather than reaching into
    ``src.taxonomy.tagger``'s or ``src.generation.blanking.sentence_tagger``'s
    private ``_load_model`` -- both exclude the parser this module needs.
    Never raises: missing spaCy or a missing model both degrade to ``None``,
    exactly as those two modules do.
    """
    try:
        import spacy
    except ImportError:
        return None
    try:
        # ner is the only pipe this module never reads; tagger, morphologizer
        # and the parser all feed the checks above directly, and the
        # lemmatizer stays in (cheap, and keeps this loader interchangeable
        # with sentence_tagger's if a future check needs a lemma).
        return spacy.load(MODEL_NAME, exclude=["ner"])
    except OSError:
        return None


def analysis_available() -> bool:
    """Whether this module's own parser-enabled pipeline loaded.

    Not delegated to ``src.taxonomy.tagger.analysis_available()``: that
    checks a *different* loaded pipeline (parser excluded). The two happen to
    fail under the same condition (model not installed at all), but this
    module's own cache is what actually answers whether ITS calls will work,
    so it asks its own loader.
    """
    return _load_model() is not None


@dataclass(frozen=True)
class CarrierValidation:
    """The verdict for one candidate carrier sentence."""

    sentence: str
    accepted: bool
    reason: str | None = None


@dataclass
class CarrierValidationSummary:
    """A batch of carrier validations, aggregated the way a pilot report
    needs: which sentences survived, and a count per rejection reason for
    everything that did not."""

    accepted: list[str] = field(default_factory=list)
    rejected_by_reason: Counter[str] = field(default_factory=Counter)
    total: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return self.total - len(self.accepted)


def _is_finite(token: SpacyToken) -> bool:
    """A token is treated as a finite verb only when its fine-grained tag AND
    its coarse POS agree that it is one. Both are required rather than the
    fine-grained tag alone: the attribute ruler occasionally assigns a
    verb-shaped fine tag (``VVFIN``) to a token whose coarse POS is
    ``ADJ`` -- confirmed on "Er spart bewusst Wasser..." ("bewusst" tags
    ``ADJ``/``VVFIN``, a direct contradiction) -- and a token the pipeline
    itself cannot agree on is exactly the "cannot evaluate confidently"
    case this module is conservative about."""
    return token.tag_ in _FINITE_TAGS and token.pos_ in ("VERB", "AUX")


def _sentence_shape_reason(text: str) -> str | None:
    """Cheap, parser-free structural checks: capitalisation, terminal
    punctuation. Run before spaCy touches the sentence at all, since these
    never need a parse to decide."""
    stripped = text.strip()
    first_alpha = next((ch for ch in stripped if ch.isalpha()), None)
    if first_alpha is not None and not first_alpha.isupper():
        return REASON_NOT_CAPITALIZED
    if not _TERMINAL_PUNCTUATION.search(stripped):
        return REASON_NO_TERMINAL_PUNCTUATION
    return None


def _resolve_subject(verb: SpacyToken, *, _depth: int = 0) -> SpacyToken | None:
    """The token that governs agreement for ``verb``, or ``None`` if there is
    not exactly one in a checkable way.

    Direct case: a single ``sb``/``ep`` child. The one indirect case handled:
    subject-gapping in coordination -- "Ein Mann steht vor der Tür und
    wartet." is correct German with only ONE subject ("Mann"), shared by
    both coordinated verbs; the second conjunct ("wartet") has no subject
    child of its own at all. When a finite verb is itself a coordinated
    conjunct (``cj``, attached to the coordinating conjunction) and carries
    no subject of its own, this looks at the first conjunct (the
    coordinator's own head) and inherits its subject instead of reporting
    "no subject found" for ordinary, correct coordination. Depth-bounded
    (``_depth``) purely as a defensive guard against a malformed parse
    creating a cycle; real coordination chains never nest more than a
    handful deep."""
    subjects = [c for c in verb.children if c.dep_ in _SUBJECT_DEPS]
    if len(subjects) == 1:
        return subjects[0]
    if len(subjects) == 0 and verb.dep_ == "cj" and _depth < 5:
        coordinator = verb.head
        first_conjunct = coordinator.head
        if _is_finite(first_conjunct) and first_conjunct is not verb:
            return _resolve_subject(first_conjunct, _depth=_depth + 1)
    return None


def _subject_agreement_reason(verb: SpacyToken) -> str | None:
    """Subject-verb agreement for one finite verb, or ``None`` if it agrees
    (subject resolution, including the subject-gapped coordination case, is
    ``_resolve_subject``'s job). See the module docstring for exactly which
    real cases this deliberately treats as undecidable rather than
    guessing."""
    subject = _resolve_subject(verb)
    if subject is None:
        return REASON_NO_SUBJECT_FOUND

    # A coordinated subject ("Der Mann und die Frau tanzen") shows up as a
    # "cd" (coordinating conjunction) child on the subject noun itself, not
    # on the verb -- German's coordinate-subject person-resolution rule
    # ("du und ich" -> wir-agreement) is real but not implemented here.
    if any(grandchild.dep_ == "cd" for grandchild in subject.children):
        return REASON_AGREEMENT_UNDECIDABLE

    subject_feats = dict(subject.morph.to_dict())
    subject_number = subject_feats.get("Number")
    if subject.pos_ == "PRON":
        # A relative, demonstrative, or indefinite pronoun used as a subject
        # ("der Mann, der dort steht" / "man kann das nicht wissen") carries
        # no Person feature in UD either -- it is not deictic the way
        # "ich"/"du" are -- but all three are always grammatically 3rd
        # person in practice (a relative clause's antecedent is a noun
        # phrase, and "man"/"jemand"/"etwas" are 3rd person by definition
        # regardless of notional number), so the same safe default applies
        # as for a noun subject. Personal pronouns still resolve their real
        # Person here.
        subject_person = subject_feats.get("Person")
        if subject_person is None and subject_feats.get("PronType") in ("Rel", "Dem", "Ind"):
            subject_person = "3"
    elif subject.pos_ in ("NOUN", "PROPN"):
        # A common or proper noun subject carries no Person feature in UD --
        # it is always grammatically 3rd person, so that much is safe to
        # assume rather than treat as missing.
        subject_person = "3"
    else:
        subject_person = None

    if subject_person is None or subject_number is None:
        return REASON_AGREEMENT_UNDECIDABLE

    verb_feats = dict(verb.morph.to_dict())
    verb_person = verb_feats.get("Person")
    verb_number = verb_feats.get("Number")
    if verb_person is None or verb_number is None:
        return REASON_AGREEMENT_UNDECIDABLE

    if subject_person == verb_person and subject_number == verb_number:
        return None

    if _is_syncretism_tolerated(subject_person, subject_number, verb_person, verb_number, verb):
        return None

    return REASON_SUBJECT_VERB_DISAGREEMENT


_SIBILANT_STEM_ENDINGS: tuple[str, ...] = ("ss", "s", "ß", "z", "tz", "x")


def _has_sibilant_stem(verb: SpacyToken) -> bool:
    """Whether ``verb``'s infinitive stem ends in a sibilant (s/ss/ß/z/tz/x)
    -- the class of German verbs ("vergessen", "reisen", "sitzen", "heißen")
    whose 2nd- and 3rd-singular present forms genuinely contract to the same
    surface form ("du/er vergisst", "du/er reist"). Reads ``verb.lemma_``,
    which this module's loader keeps enabled specifically for this."""
    lemma = verb.lemma_
    stem = lemma.removesuffix("en").removesuffix("n")
    return stem.endswith(_SIBILANT_STEM_ENDINGS)


def _is_syncretism_tolerated(
    subject_person: str,
    subject_number: str,
    verb_person: str,
    verb_number: str,
    verb: SpacyToken,
) -> bool:
    """Four real, distinct German verb-paradigm syncretisms, each confirmed
    empirically against ``de_core_news_sm`` where noted (see the module
    docstring for the confirmation of 1 and 2):

    1. **1st-plural / 3rd-plural, every tense and every verb, no
       restriction.** German never morphologically distinguishes "wir" from
       "sie" (plural) on the verb -- "wir machen"/"sie machen", "wir
       hatten"/"sie hatten", "wir würden"/"sie würden" are identical in
       every paradigm without exception.
    2. **2nd-plural / 3rd-singular, present tense only, verb form ending in
       "-t".** Restricted to ``Tense=Pres`` because the same two cells are
       NOT syncretic in the preterite ("ihr machtet" / "er machte" differ).
    3. **1st-singular / 3rd-singular, preterite or Konjunktiv II, every
       verb, no restriction.** Another categorical German rule, not a
       present-tense-only one: "ich machte"/"er machte", "ich sah"/"er sah",
       "ich hätte"/"er hätte", "ich wäre"/"er wäre" are always identical.
       Excluded from the present tense on purpose ("ich mache" / "er macht"
       are genuinely distinct there).
    4. **2nd-singular / 3rd-singular, present tense only, and only for a
       sibilant-stem verb** ("vergessen", "reisen", "heißen", "sitzen"):
       German orthography contracts the 2nd-singular "-st" ending against a
       stem-final sibilant, making "du vergisst" and "er vergisst" the same
       word. Checked against the verb's own lemma stem, not assumed."""
    pair = {(subject_person, subject_number), (verb_person, verb_number)}
    tense = dict(verb.morph.to_dict()).get("Tense")
    mood = dict(verb.morph.to_dict()).get("Mood")

    if pair == {("1", "Plur"), ("3", "Plur")}:
        return True
    if pair == {("1", "Sing"), ("3", "Sing")}:
        return tense == "Past" or mood == "Sub"
    if pair == {("2", "Plur"), ("3", "Sing")}:
        return tense == "Pres" and verb.text.endswith(("t", "T"))
    if pair == {("2", "Sing"), ("3", "Sing")}:
        return tense == "Pres" and _has_sibilant_stem(verb)
    return False


def _clause_connector_reason(verb: SpacyToken) -> str | None:
    """A finite verb that is not the sentence ROOT and whose head is itself
    a finite verb is only a legitimate subordinate clause if it carries an
    explicit subordinating conjunction. Coordinated clauses and relative
    clauses never reach this branch (see module docstring): they attach to
    the conjunction word or the antecedent noun, never straight to another
    finite verb."""
    if verb.dep_ == "ROOT":
        return None
    head = verb.head
    if not _is_finite(head):
        return None
    has_complementizer = any(child.dep_ == "cp" for child in verb.children)
    if has_complementizer:
        return None
    return REASON_MISSING_CLAUSE_CONNECTOR


def _dangling_fragment_reason(tokens: list[SpacyToken]) -> str | None:
    """Sentence completeness beyond capitalisation/terminal punctuation:
    enough tokens to be a sentence, and the last non-punctuation token is not
    something that is still expecting a continuation (a bare article,
    preposition, or conjunction) -- the shape a truncated generation call
    leaves behind."""
    content_tokens = [t for t in tokens if t.pos_ != "PUNCT"]
    if len(content_tokens) < _MIN_TOKEN_COUNT:
        return REASON_FRAGMENT_TOO_SHORT
    if content_tokens[-1].tag_ in _CONTINUATION_EXPECTING_TAGS:
        return REASON_DANGLING_FRAGMENT
    return None


def validate_carrier(sentence: str) -> CarrierValidation:
    """Validate one plain, generated German sentence as a sound carrier.

    Never raises. Returns ``accepted=False`` with a specific ``reason``
    whenever the sentence is empty, spaCy is unavailable, or any check
    fails or cannot be evaluated confidently -- conservative by design (see
    module docstring): keeping a bad carrier is far more expensive than
    losing a good one.
    """
    stripped = sentence.strip()
    if not stripped:
        return CarrierValidation(sentence, False, REASON_EMPTY_SENTENCE)

    shape_reason = _sentence_shape_reason(stripped)
    if shape_reason is not None:
        return CarrierValidation(sentence, False, shape_reason)

    nlp = _load_model()
    if nlp is None:
        return CarrierValidation(sentence, False, REASON_SPACY_UNAVAILABLE)

    doc = nlp(stripped)
    sentence_spans = list(doc.sents)
    if len(sentence_spans) > 1:
        return CarrierValidation(sentence, False, REASON_MULTIPLE_SENTENCES)

    tokens = list(doc)
    dangling_reason = _dangling_fragment_reason(tokens)
    if dangling_reason is not None:
        return CarrierValidation(sentence, False, dangling_reason)

    finite_verbs = [t for t in tokens if _is_finite(t)]
    if not finite_verbs:
        return CarrierValidation(sentence, False, REASON_NO_FINITE_VERB)

    for verb in finite_verbs:
        connector_reason = _clause_connector_reason(verb)
        if connector_reason is not None:
            return CarrierValidation(sentence, False, connector_reason)

    for verb in finite_verbs:
        agreement_reason = _subject_agreement_reason(verb)
        if agreement_reason is not None:
            return CarrierValidation(sentence, False, agreement_reason)

    return CarrierValidation(sentence, True, None)


def validate_carriers(sentences: Iterable[str]) -> CarrierValidationSummary:
    """Validate a batch, aggregated for a pilot report: accepted sentences in
    original order, and a count per rejection reason for everything
    discarded. Never raises, matching ``validate_carrier``."""
    summary = CarrierValidationSummary()
    for sentence in sentences:
        summary.total += 1
        result = validate_carrier(sentence)
        if result.accepted:
            summary.accepted.append(sentence)
        else:
            reason = result.reason or "unknown_skip_reason"
            summary.rejected_by_reason[reason] += 1
    return summary
