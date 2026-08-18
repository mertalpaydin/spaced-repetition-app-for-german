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

## Cycle 5 additions: adjective declension, dass/das, Swiss spelling

`docs/audits/cycle-04-report.md` found two error classes the checks above
never looked at, plus one seen once. All three are real, mechanically-checked
properties, not semantic proxies -- but each has an honestly-scoped limit,
recorded here rather than left implicit:

4. **Attributive adjective declension after its determiner.** An attributive
   adjective's ending is fixed by what precedes it: weak after a definite
   article, mixed after an ein-word (indefinite article, negation, or
   possessive), strong after a quantifier such as "viele"/"einige"/
   "mehrere"/"wenige" or after nothing at all. The three ending tables
   (`paradigms.ADJ_ENDING_BY_CELL`) are reused, not duplicated, exactly as
   this package's own convention requires.

   The hard part is Case. "die" is nominative-or-accusative, singular
   feminine or plural-any-gender; "der" is masculine nominative, feminine
   dative-or-genitive, or genitive plural -- and a determiner never carries
   enough information on its own to pick one reading. This check never
   guesses: it reads the head noun's own Gender and Number off spaCy (both
   are lexical facts about the noun, not context-dependent the way Case is,
   so they are trustworthy here the same way Number already is for
   subject-verb agreement), enumerates EVERY Case reading consistent with
   the determiner's own surface form under that Gender/Number, and only
   rejects when the adjective's actual ending matches NONE of them --
   impossible under every reading, never merely improbable under the most
   likely one. Where the determiner's own surface form cannot be classified
   at all (an unrecognised word, or an ein-word ending the paradigm has no
   plural Nominative/Accusative row for -- see `paradigms.py`'s own
   docstring on that exact gap), the check is skipped for that noun phrase
   rather than guessed. Deliberately narrow scope, to keep false positives
   at zero: only `ART` (definite and indefinite article), `PPOSAT`
   (possessive), and `PIAT` (the closed quantifier set, plus `kein`-family
   words, which `de_core_news_sm` also tags `PIAT`) are read as determiners;
   a demonstrative (`dieser`), `jeder`/`manche` (`PIDAT`), or a determiner
   fused into a preposition ("im", "zum") are not recognised at all, so an
   adjective after one of those falls through to the "no determiner found"
   branch, which checks against all four cases and is correspondingly
   looser (it still catches a wrong DECLENSION family, e.g. a weak "-en"
   ending where every strong reading needs "-er"/"-es"/"-em", just not
   every wrong CASE within the strong paradigm). The adjective's own ending
   is read off its literal surface suffix, not off spaCy's morphology:
   verified empirically that the morphologizer assigns a wrong attributive
   adjective ("nassen" where "nasse" was needed) the SAME Case/Gender/Number
   features as the correct form would have gotten, because it infers those
   features from the surrounding noun phrase rather than from the
   adjective's own ending -- exactly the case this check exists to catch,
   so trusting that feature would silently defeat the check.

5. **`dass` written where `das` belongs.** `dass` (`KOUS`, introducing a
   clause attached with a `cp` dependency) can never itself fill a
   grammatical role inside its own clause; every argument slot the clause's
   verb needs must be filled from words already inside the clause. `das`
   used as a relative pronoun is different: it IS one of those arguments,
   referring back to an antecedent outside the clause. The reliable signal
   is a gap: a clause introduced by `dass` whose verb is missing an argument
   it structurally needs is exactly the shape a wrongly-typed `dass` leaves
   behind, because the relative pronoun that should have filled that slot
   is gone.

   **This catches exactly one direction, and only for a closed, two-verb
   list.** Knowing which verbs need which argument (transitivity/valency)
   is not something spaCy's dependency labels give for free, and German
   verbs are unusually promiscuous about dropping objects when the context
   allows it ("Ich lese." is fine; "Ich esse." is fine) -- a general
   "verb X normally takes an object" rule would misfire constantly against
   ordinary, correct German. So this checks only `kaufen` and `schenken`,
   picked because the audit's own example uses one of them and both are
   about as close to obligatorily transitive as German verbs get in
   ordinary written prose. A `dass`-clause whose content verb (walking down
   any auxiliary/modal `oc` chain to find it, so "..., dass sie es gekauft
   hatten" is checked on "gekauft", not "hatten") lemmatises to one of
   those two AND has no accusative object (`oa`/`oa2`) child is rejected.
   A verb carrying its own separable-prefix particle (`svp`, e.g.
   "einkaufen" split as "kauft ... ein") is excluded first: it is a
   different verb with different valency, not a transitivity gap in
   "kaufen" itself. **The reverse error -- `das` written where a real
   `dass`-complement clause was meant -- is NOT caught.** Confirming that
   direction needs knowing which verbs take a sentential complement at all
   (`glauben`, `wissen`, `sagen`, `hoffen`, ... an open, much larger lexical
   class than "which two verbs are obligatorily transitive"), which this
   module does not attempt to enumerate.

6. **Swiss `ss` for standard `ß`.** Seen once (`heisse` for `heiße`). The
   general rule -- `ß` after a long vowel or diphthong, `ss` after a short
   one -- is exactly the kind of thing this module refuses to guess at
   without a word list: "Fluss", "dass", and "muss" are correct with `ss`
   precisely because their vowel is short, and nothing in the surface
   spelling of a single vowel letter reliably says whether German
   pronounces it long or short (contrast "Fluss" short /background of "u"
   vs. "Fuß" long, spelled with the same single letter "u"). Flagging every
   `ss` would reject a large fraction of genuinely correct sentences, and
   this module has no vetted word list of which lemmas take `ß` to consult
   instead, so **no general ss/ß rule is implemented.** What IS implemented
   is much narrower and needs no word list at all: a fixed, closed set of
   literal Swiss-spelled surface forms of exactly one verb, "heißen"
   ("heisse", "heisst", "heissen", "heissend", "hiess", "hiessen",
   "geheissen"), matched on the raw sentence text before spaCy ever sees
   it. "heißen" always takes `ß` in every standard-German form regardless
   of context, so there is no ambiguity to misjudge for this one closed
   list, but it catches nothing outside these seven forms.

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

from src.generation.blanking.paradigms import (
    ADJ_ENDING_BY_CELL,
    DEFINITE_ARTICLE_BY_CELL,
    EIN_ENDING_BY_CELL,
    Cell,
    Declension,
    adjective_ending,
    match_ein_word,
)
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
REASON_ADJECTIVE_DECLENSION_MISMATCH = "adjective_declension_mismatch"
REASON_DASS_CLAUSE_MISSING_OBJECT = "dass_clause_missing_object"
REASON_SWISS_SPELLING = "swiss_spelling"

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

# A fixed, closed list of literal Swiss-spelled surface forms of "heißen" --
# not a general ss/ß rule (see module docstring section 6 for why a general
# rule is not implemented). Word-boundary matched, case-insensitive, on the
# raw text, so no parse is needed to evaluate this one.
_SWISS_HEISSEN_PATTERN = re.compile(
    r"\b(?:heissend|heissen|heisst|heisse|hiessen|hiess|geheissen)\b",
    re.IGNORECASE,
)


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
    punctuation, and the closed Swiss-spelling word list. Run before spaCy
    touches the sentence at all, since none of these need a parse to
    decide."""
    stripped = text.strip()
    first_alpha = next((ch for ch in stripped if ch.isalpha()), None)
    if first_alpha is not None and not first_alpha.isupper():
        return REASON_NOT_CAPITALIZED
    if not _TERMINAL_PUNCTUATION.search(stripped):
        return REASON_NO_TERMINAL_PUNCTUATION
    if _SWISS_HEISSEN_PATTERN.search(stripped):
        return REASON_SWISS_SPELLING
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


def _reverse_form_to_cells(cell_to_form: dict[Cell, str]) -> dict[str, tuple[Cell, ...]]:
    """Invert a ``cell -> surface form`` paradigm dict from ``paradigms.py``
    into ``surface form -> every cell that produces it``, so a determiner's
    OWN surface text can be read back into the set of grammatical cells it
    is consistent with -- the genuine ambiguity (e.g. "der" is Nom Masc Sing
    or Dat/Gen Fem Sing or Gen Plur) that the adjective-declension check
    below must enumerate rather than guess past."""
    reverse: dict[str, list[Cell]] = {}
    for cell, form in cell_to_form.items():
        reverse.setdefault(form, []).append(cell)
    return {form: tuple(cells) for form, cells in reverse.items()}


_DEFINITE_ARTICLE_CELLS_BY_FORM: dict[str, tuple[Cell, ...]] = _reverse_form_to_cells(
    DEFINITE_ARTICLE_BY_CELL
)
_EIN_ENDING_CELLS_BY_ENDING: dict[str, tuple[Cell, ...]] = _reverse_form_to_cells(
    EIN_ENDING_BY_CELL
)


def _strong_plural_ending_to_cases() -> dict[str, frozenset[str]]:
    """Derive ``ending -> {Case, ...}`` for the PLURAL rows of the strong
    adjective paradigm from ``paradigms.ADJ_ENDING_BY_CELL`` -- not a new
    fact, just a regrouping of the same table already imported, used to read
    a plural quantifier's OWN ending (below) back into the Case(s) it is
    consistent with. Strong plural endings do not vary by Gender, so this
    collapses Gender away entirely."""
    reverse: dict[str, set[str]] = {}
    for (case, _gender, number), ending in ADJ_ENDING_BY_CELL["strong"].items():
        if number == "Plur":
            reverse.setdefault(ending, set()).add(case)
    return {ending: frozenset(cases) for ending, cases in reverse.items()}


_STRONG_PLURAL_ENDING_TO_CASES: dict[str, frozenset[str]] = _strong_plural_ending_to_cases()

# The closed set of plural quantifiers that trigger STRONG adjective
# endings (CLAUDE.md task: "viele", "einige", "mehrere", "wenige"). Each
# declines exactly like a strong plural adjective/article on this same
# stem ("viele"/"vielen"/"vieler"), so matching stem + a strong-plural
# ending is sufficient to read off which Case(s) the quantifier's own
# surface form is consistent with.
_STRONG_QUANTIFIER_STEMS: tuple[str, ...] = ("viel", "wenig", "einig", "mehrer")


def _match_strong_quantifier(text: str) -> str | None:
    """The quantifier's own strong-plural ending ("e"/"en"/"er"), or
    ``None`` if ``text`` does not match one of the closed quantifier stems
    with a recognised strong-plural ending."""
    lower = text.strip().lower()
    for stem in _STRONG_QUANTIFIER_STEMS:
        if lower.startswith(stem):
            ending = lower[len(stem) :]
            if ending in _STRONG_PLURAL_ENDING_TO_CASES:
                return ending
    return None


# The only endings any of the three adjective declension paradigms ever
# assign (checked longest-first purely so a 2-letter ending is identified
# over a coincidental trailing "e" -- the sets are otherwise disjoint, since
# none of "em"/"en"/"es"/"er" ends in "e").
_ADJ_SURFACE_ENDINGS: tuple[str, ...] = ("em", "en", "es", "er", "e")


def _adjective_surface_ending(text: str) -> str | None:
    """The attributive adjective's OWN ending, read off its literal surface
    suffix -- never off spaCy's morphology (see module docstring: the
    morphologizer assigns a wrong ending the SAME features the right one
    would have gotten, because it infers Case/Gender/Number from the noun
    phrase's context, not from the adjective's own spelling)."""
    lower = text.strip().lower()
    for ending in _ADJ_SURFACE_ENDINGS:
        if len(lower) > len(ending) and lower.endswith(ending):
            return ending
    return None


_ATTRIBUTIVE_DETERMINER_TAGS: frozenset[str] = frozenset({"ART", "PIAT", "PPOSAT"})


def _declension_for_determiner(
    det: SpacyToken, noun_gender: str, noun_number: str
) -> tuple[Declension, frozenset[str]] | None:
    """The declension family an attributive adjective must follow given its
    determiner ``det``, and every Case that determiner's own surface form is
    consistent with for a head noun of ``noun_gender``/``noun_number`` --
    ``None`` if ``det`` cannot be confidently classified at all (an
    unrecognised surface form, or a paradigm gap such as the ein-word
    family's missing Nominative/Accusative plural row), in which case the
    caller skips the check for this noun phrase rather than guesses."""
    feats = dict(det.morph.to_dict())
    if det.tag_ == "ART" and feats.get("Definite") == "Def":
        cells = _DEFINITE_ARTICLE_CELLS_BY_FORM.get(det.text.strip().lower(), ())
        cases = frozenset(c[0] for c in cells if c[1] == noun_gender and c[2] == noun_number)
        return ("weak", cases) if cases else None

    if det.tag_ == "PIAT":
        quantifier_ending = _match_strong_quantifier(det.text)
        if quantifier_ending is not None:
            return ("strong", _STRONG_PLURAL_ENDING_TO_CASES[quantifier_ending])
        # Falls through to the ein-word family: de_core_news_sm tags
        # "kein"/"keine"/... as PIAT too, not ART, and "kein" takes the
        # same mixed declension as "ein"/"mein"/... .

    if det.tag_ in ("ART", "PPOSAT", "PIAT"):
        match = match_ein_word(det.text)
        if match is None:
            return None
        _stem, ending = match
        cells = _EIN_ENDING_CELLS_BY_ENDING.get(ending, ())
        cases = frozenset(c[0] for c in cells if c[1] == noun_gender and c[2] == noun_number)
        return ("mixed", cases) if cases else None

    return None


def _adjective_declension_reason(tokens: list[SpacyToken]) -> str | None:
    """Attributive adjective declension: an adjective's ending must be a
    member of the declension family its determiner selects (weak/mixed/
    strong), for at least one Case reading consistent with the determiner's
    own surface form and the head noun's real Gender/Number. See the module
    docstring section 4 for the full reasoning and the deliberately narrow
    scope (only ``ART``/``PPOSAT``/``PIAT`` determiners are recognised; a
    determiner-less noun phrase is checked as strong across all four Cases,
    which is looser but never wrong)."""
    for noun in tokens:
        if noun.pos_ not in ("NOUN", "PROPN"):
            continue
        noun_feats = dict(noun.morph.to_dict())
        noun_gender = noun_feats.get("Gender")
        noun_number = noun_feats.get("Number")
        if noun_gender is None or noun_number is None:
            continue

        adjectives = [c for c in noun.children if c.dep_ == "nk" and c.tag_ == "ADJA"]
        if not adjectives:
            continue

        determiners = [
            c for c in noun.children if c.dep_ == "nk" and c.tag_ in _ATTRIBUTIVE_DETERMINER_TAGS
        ]
        if len(determiners) == 1:
            resolved = _declension_for_determiner(determiners[0], noun_gender, noun_number)
            if resolved is None:
                continue
            declension, cases = resolved
        elif len(determiners) == 0:
            declension, cases = "strong", frozenset({"Nom", "Acc", "Dat", "Gen"})
        else:
            # More than one determiner-tagged child of one noun is not a
            # shape this check anticipates; skip rather than guess which one
            # governs the adjective.
            continue

        candidate_endings = {
            ending
            for case in cases
            if (ending := adjective_ending(declension, (case, noun_gender, noun_number)))
            is not None
        }
        if not candidate_endings:
            continue

        for adjective in adjectives:
            actual_ending = _adjective_surface_ending(adjective.text)
            if actual_ending is not None and actual_ending not in candidate_endings:
                return REASON_ADJECTIVE_DECLENSION_MISMATCH
    return None


# The closed, two-verb list task 5 (module docstring) is scoped to: about as
# close to obligatorily transitive as German verbs get in ordinary written
# prose, so a missing accusative object is a reliable gap signal rather than
# the normal object-dropping German otherwise tolerates freely.
_DASS_CLAUSE_TRANSITIVE_LEMMAS: frozenset[str] = frozenset({"kaufen", "schenken"})


def _content_verb(finite_verb: SpacyToken) -> SpacyToken:
    """Walk down an auxiliary/modal ``oc`` chain to the deepest verb/aux
    carrying the clause's real lexical content (e.g. "gekauft" inside
    "..., dass sie es gekauft hatten"), or ``finite_verb`` itself if there is
    no such chain. Depth-bounded via the visited set purely as a defensive
    guard against a malformed parse creating a cycle."""
    current = finite_verb
    seen = {current.i}
    while True:
        content_children = [
            c for c in current.children if c.dep_ == "oc" and c.pos_ in ("VERB", "AUX")
        ]
        if len(content_children) != 1 or content_children[0].i in seen:
            return current
        current = content_children[0]
        seen.add(current.i)


def _dass_clause_reason(tokens: list[SpacyToken]) -> str | None:
    """``dass`` versus ``das``: see module docstring section 5 for the full
    reasoning. Catches only "dass" written where relative "das" belongs, and
    only when the clause's content verb is one of a closed two-verb list
    that has no accusative object at all -- the gap the missing relative
    pronoun leaves behind. Does not catch the reverse error."""
    for kous in tokens:
        if kous.tag_ != "KOUS" or kous.dep_ != "cp" or kous.text.strip().lower() != "dass":
            continue
        finite_verb = kous.head
        if not _is_finite(finite_verb):
            continue
        content_verb = _content_verb(finite_verb)
        if content_verb.lemma_ not in _DASS_CLAUSE_TRANSITIVE_LEMMAS:
            continue
        if any(c.dep_ == "svp" for c in content_verb.children):
            # A separable-prefix compound (e.g. "kaufen" + "ein" ->
            # "einkaufen") is a different verb with different valency, not a
            # transitivity gap in "kaufen"/"schenken" themselves.
            continue
        has_object = any(c.dep_ in ("oa", "oa2") for c in content_verb.children)
        if not has_object:
            return REASON_DASS_CLAUSE_MISSING_OBJECT
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

    declension_reason = _adjective_declension_reason(tokens)
    if declension_reason is not None:
        return CarrierValidation(sentence, False, declension_reason)

    dass_reason = _dass_clause_reason(tokens)
    if dass_reason is not None:
        return CarrierValidation(sentence, False, dass_reason)

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
