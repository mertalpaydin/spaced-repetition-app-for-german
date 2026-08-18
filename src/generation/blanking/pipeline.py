"""Orchestrates tag -> select -> blank across every in-scope topic and every
generated sentence, and reports what happened -- sentences tagged, items
produced per topic, and skips by reason -- so a caller (``scripts.
step6_blank_pilot``, or a test) never has to re-derive that bookkeeping.

"Generate" itself (asking an LLM for plain sentences, then carrier-validating
them) lives in ``sentence_source.py`` and is deliberately NOT called from
here: this module takes already-generated, already-validated sentence
strings, so it can be exercised in tests with a fixed, hand-written corpus
and no LLM (or spaCy dependency parser) involved at all.

## Two problems this module also fixes, beyond tag -> select -> blank

An audit of a pilot run found two further defects, both about what happens
*after* a candidate item is built, not about whether any single item is
individually correct:

1. **The same (prompt, answer) pair accepted under two different topics at
   once.** Several selectors are deliberately permissive rather than
   mutually exclusive -- e.g. ``verb_praesens_regelm`` does not know or care
   whether the regular present-tense verb it just matched is also a
   separable verb, which is ``verben_trennbar_praesens``'s own, narrower
   condition -- so the exact same blanked sentence can be produced twice,
   once under each topic. Because FSRS review state is scheduled per
   ``tag_id`` (CLAUDE.md rule 5: one item, one ``tag_id``), one piece of
   learner evidence would otherwise silently update two topics' review
   state. ``_drop_cross_topic_duplicates`` resolves every such collision
   after the full tag -> select -> blank pass, keeping exactly one item per
   (prompt, answer) pair for the entire run, not just within one sentence.
2. **One or two high-frequency topics dominating a run.** A first-person,
   present-tense narrative contains a nominative personal pronoun and a
   regular present-tense verb in nearly every sentence, so those topics
   consume a wildly disproportionate share of the accepted items while
   narrower topics (a Genitiv determiner, a modal verb) get one or none.
   ``_apply_caps`` bounds both how many items one topic can claim and how
   many items one source sentence can contribute across all topics, so a
   pilot's item counts by topic actually reflect coverage decisions, not an
   artefact of which grammar happens to be common in first-person prose.

Both are counted, not silently discarded: CLAUDE.md 12's report-honestly
standard (this project has previously lost a stage to an unreported quiet
truncation) means a caller must be able to tell "no item for this topic
because no sentence in the pool needed it" apart from "an item existed and
was cut for balance". ``BlankingReport.skips_by_reason`` covers the first
kind (a selector genuinely found nothing, or a candidate failed paradigm
reconstruction); ``cross_topic_duplicates_dropped``,
``items_dropped_by_topic_cap``, and ``items_dropped_by_sentence_cap`` are
kept as separate counters for the second kind precisely so the two are never
conflated in a report.

## A third problem: a correct item is not necessarily a SOLVABLE one

docs/audits/cycle-04-report.md found a third, architecturally distinct
defect, on top of the two above: blanking proves the removed token was
correct, never that it was the only grammatically possible one. A modal verb
or a free-choice object pronoun is very often one of several equally
grammatical fillers, and a plural noun with no cue is nearly always one of
many -- the item is correct and unsolvable at once.

``src.generation.blanking.uniqueness.check_uniqueness`` is the gate for
this, run once per successfully built ``CandidateItem`` (after
``blank_candidate`` already confirmed it is correct, before it is eligible
for cross-topic dedup or a cap). It is a THIRD, distinct outcome from the
two above, not folded into either: it is not a "no candidate, or the
candidate failed paradigm reconstruction" quality judgment
(``skips_by_reason``), because the item it rejects was neither of those --
it built cleanly and reconstructed correctly. It is not a cap/dedup balance
decision either, because nothing else claimed the same (prompt, answer) pair
and no cap was reached; the item is simply not solvable on its own terms.
``BlankingReport.skips_by_uniqueness`` and ``uniqueness_skips`` are its own
counter and detail list for exactly that reason -- CLAUDE.md 12's
report-honestly standard applied a third time, not just the two the module
docstring above already argues for.

## A fourth problem: a correct, solvable item can still be the WRONG TYPE

docs/audits/cycle-06-modal-leak.md audited this pipeline's own output
against ``data/taxonomy.yaml``'s ``eligible_types`` -- the declared,
per-topic list of item types that can honestly test that topic (see
``src.verification.pipeline``'s own enforcement of the identical invariant
for the other, LLM-direct generation path) -- and found 72 of 354 items on a
300-sentence pilot run whose ``type`` was not in their own topic's declared
list. Every outcome builder in ``blanker.py`` decides ``CandidateItem.type``
locally (``_cued_item_type``: ``"cloze_cued"`` when a selector supplied a
cue, ``"cloze_free"`` otherwise), with nothing anywhere checking that
against the topic actually being built for. Two different situations
produced this, and they need two different fixes, neither of them a fixture
relabel (an earlier, adjacent audit -- docs/audits/
stage-04-pilot-2026-08-15.md decision D4 -- explicitly rejected relabelling
a non-conforming item's type to satisfy a type check as "converting a
quality measure into a laundered defect"; the same reasoning applies here):

1. **Eight topics wanted a citation cue and the mechanism to supply one
   already existed** (cycle 5's own lexical-verb cue extension) but had
   never been wired to them: ``adjektiv_komparativ_superlativ``,
   ``partizip_i_attributiv``, ``partizip_ii_attributiv_erweitert``,
   ``verb_sein_haben`` and ``praeteritum_sein_haben_modal`` are now cued
   (``selectors.py``'s own per-selector docstrings), which resolves the
   violation for those five by making the emitted type actually match.
2. **Three topics (the ``artikel_*`` Nominative-case ones) cannot be
   honestly tested by any single, standalone sentence at all** --
   definiteness, indefiniteness and possession are discourse properties, and
   this pipeline's whole architecture (module docstring above) is built on
   independent, unpaired sentences with no mechanism to establish a referent
   in one and refer back to it in another. Building that mechanism is new
   architecture, not a fix to this cycle's own defect, so it is deliberately
   NOT attempted here (see ``blanker.py``'s own module docstring, final
   section). These three topics simply report zero.

The hard assertion below (after ``blank_candidate`` succeeds AND after the
uniqueness gate -- see the enforcement site's own comment for why that
order, not the reverse, keeps the uniqueness gate's specific, diagnostic
skip reasons intact for every cue-gated candidate kind) is what makes
situation 2 resolve to "reports zero" rather than silently continuing to
leak a ``cloze_free`` item: every item
``_determiner_outcome`` builds for one of the three ``artikel_*`` topics is
unconditionally ``type="cloze_free"``, which is never in their own
``eligible_types: [paragraph_cloze]``, so it is always caught and skipped
here. ``BlankingReport.skips_by_type_ineligibility`` and
``type_ineligibility_skips`` are the FOURTH counter/detail-list pair this
module now keeps, for the same "never conflate a distinct outcome with an
existing bucket" reason ``skips_by_uniqueness`` already established --
``TypeIneligibilitySkip``'s own docstring makes the full case for why.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from src.contracts import CandidateItem, Difficulty, ItemType
from src.generation.blanking import sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.selectors import SELECTORS
from src.generation.blanking.uniqueness import check_uniqueness
from src.taxonomy.loader import load_taxonomy

TOPIC_IDS: tuple[str, ...] = tuple(SELECTORS)


@lru_cache(maxsize=1)
def _eligible_types_by_topic() -> dict[str, frozenset[str]]:
    """``topic.id -> frozenset(topic.eligible_types)`` for every topic in
    ``data/taxonomy.yaml``, loaded and cached once per process.

    This is the enforcement side of a defect an audit of this pipeline's own
    output found (docs/audits/cycle-06-modal-leak.md): every outcome builder
    in ``blanker.py`` decides its own ``CandidateItem.type`` locally (
    ``"cloze_free"``, or ``"cloze_cued"`` via ``_cued_item_type`` when a
    selector supplied a cue), with no check anywhere that the topic it is
    building for actually permits that type. ``src.verification.pipeline``
    enforces exactly this same ``item.type in topic.eligible_types``
    invariant for the OTHER (LLM-direct) generation path (see that module's
    own comment at the enforcement site) -- this pipeline had no equivalent
    at all, and eight topics were confirmed emitting a type their own
    ``eligible_types`` does not list on a 300-sentence pilot run before this
    fix. ``blank_sentences`` is where that check now runs, once per
    successfully built item, exactly mirroring where the uniqueness gate
    runs (module docstring's own third-outcome section)."""
    return {topic.id: frozenset(topic.eligible_types) for topic in load_taxonomy()}


# A topic that can dominate a run gets capped, but the cap does not reassign
# the freed-up "slots" to a starved topic -- each topic is matched against
# the whole sentence pool independently, so a starved topic's real fix is
# sentence-pool variety (``sentence_source.generate_sentence_pool``), not a
# smaller cap here. These defaults exist purely to bound dominance and
# repetition; 20 items is comfortably more than any one topic needs for a
# single pilot review pass, and 4 items from one carrier sentence keeps the
# "several topics share one good sentence" feature (praised, not a bug) from
# turning into "the learner keeps meeting the same sentence".
DEFAULT_MAX_ITEMS_PER_TOPIC = 20
DEFAULT_MAX_ITEMS_PER_SENTENCE = 4

DropReason = Literal["cross_topic_duplicate", "topic_cap", "sentence_cap"]


@dataclass(frozen=True)
class SkipDetail:
    """One (sentence, topic) pair that produced no item, and why -- a
    selector found nothing, or a found candidate failed paradigm
    reconstruction (never a hallucinated answer; see ``blanker.py``)."""

    topic_id: str
    sentence: str
    reason: str


@dataclass(frozen=True)
class DroppedItem:
    """An item that WAS successfully built (a real prompt and answer exist)
    but was not kept in the final run, either because a same-run duplicate
    under another topic won it (``"cross_topic_duplicate"``) or because a
    cap was reached (``"topic_cap"``, ``"sentence_cap"``). Deliberately its
    own type, not folded into ``SkipDetail``: a cap/dedup drop is a balance
    decision made about a genuinely good item, not a quality judgment about
    the item itself, and CLAUDE.md 12's report-honestly standard asks for
    that distinction to survive into the report, not be flattened into one
    undifferentiated "skip" bucket."""

    topic_id: str
    prompt: str
    proposed_answer: str
    reason: DropReason
    kept_topic_id: str | None = None


@dataclass(frozen=True)
class UniquenessSkip:
    """One item that WAS successfully built and paradigm-verified (a real
    prompt and answer exist, exactly like ``DroppedItem``) but was rejected
    because a different member of the blanked token's own closed class would
    ALSO have been grammatical in the same slot -- a solvability judgment,
    not a quality defect in the item (``blanker.py`` already guarantees
    that) and not a balance decision (``DroppedItem``'s own territory). Its
    own type, its own counter, its own report section: see this module's
    docstring for why the three must never be conflated."""

    topic_id: str
    prompt: str
    proposed_answer: str
    reason: str


@dataclass(frozen=True)
class TypeIneligibilitySkip:
    """One item that WAS successfully built and paradigm-verified (a real
    prompt and answer exist, exactly like ``UniquenessSkip``) but whose own
    ``CandidateItem.type`` is not in its topic's declared ``eligible_types``
    -- docs/audits/cycle-06-modal-leak.md's finding. A FOURTH, distinct
    outcome, not folded into any of the other three: it is not a "no
    candidate, or a candidate that failed paradigm reconstruction" quality
    judgment (``skips_by_reason``), not a cap/dedup balance decision
    (``DroppedItem``), and not a solvability judgment about whether some
    OTHER member of a closed class would also fit (``UniquenessSkip``) --
    this item genuinely is the unique, correct, paradigm-verified answer to
    its own slot, and is still rejected, because the item TYPE it was built
    as (``cloze_free`` when a cue mechanism failed to produce one; that same
    ``cloze_free`` unconditionally for the three ``artikel_*`` topics, which
    have no cue mechanism at all) is one ``eligible_types`` never lists for
    this topic. ``allowed_types`` is carried alongside so a caller reading
    the rejected file does not have to cross-reference ``data/taxonomy.yaml``
    by hand to see why."""

    topic_id: str
    prompt: str
    proposed_answer: str
    item_type: ItemType
    allowed_types: tuple[str, ...]


# Selector pairs identified by direct inspection of every entry in
# ``selectors.SELECTORS`` where one topic's selector is a strict structural
# subset of another's -- every token the specific selector accepts, the
# general one also accepts, because the specific one checks everything the
# general one does plus at least one more real condition (a separable
# particle later in the clause, a following participle, an extra aux). Maps
# a specific topic to every general topic it is more specific than, i.e. the
# specific topic wins whenever both select the identical (prompt, answer).
#
# * ``verben_trennbar_praesens`` requires a ``PTKVZ`` particle in the same
#   clause on top of everything ``verb_praesens_regelm``/
#   ``verb_praesens_vokalwechsel`` already check.
# * ``perfekt_haben``/``perfekt_sein`` require a following Partizip II
#   forming a genuine compound tense; ``verb_sein_haben`` accepts any
#   present-tense sein/haben with no such requirement, including the very
#   auxiliary a Perfekt sentence uses.
# * ``passiv_modalverben`` requires a following Partizip II + ``werden``
#   infinitive; ``modalverben_praesens`` accepts any present-tense modal.
# * ``plusquamperfekt`` requires an anteriority marker ("nachdem"/"bevor")
#   plus a following/preceding participle; ``praeteritum_sein_haben_modal``
#   accepts any past-tense sein/haben/modal with neither requirement.
# * ``zustandspassiv``/``zustandspassiv_zeiten`` require a following
#   Partizip II of a transitive verb; ``verb_sein_haben``/
#   ``praeteritum_sein_haben_modal`` accept a bare sein form with no such
#   requirement.
# * ``futur_ii`` requires the trailing aux-infinitive Futur II shape on top
#   of everything ``futur_i``'s "werden + infinitive" shape already checks
#   for (a rare but real overlap: a transitive-participle-then-aux-infinitive
#   sentence satisfies both selectors' preconditions on the same "werden"
#   token). It also beats ``passiv_praesens``: both fire on the same "wird"
#   token whenever the participle's lemma is in ``paradigms.TRANSITIVE_LEMMAS``
#   ("Er wird das Buch gelesen haben." -- ``passiv_praesens`` stops looking
#   after the participle, ``futur_ii`` additionally requires and finds the
#   trailing aux infinitive "haben" past it). Without this entry a genuine
#   Futur II item loses the dedup to the passive reading of the same "wird"
#   and is silently dropped as a cross-topic duplicate.
# * ``partizip_i_attributiv``/``partizip_ii_attributiv_erweitert`` both
#   require the ``ADJA`` token's lemma to be a recognised participle;
#   ``adjektivdeklination_*`` accepts any ``ADJA`` with a resolvable
#   declension trigger, participle or not.
_SPECIFICITY_OVERRIDES: dict[str, frozenset[str]] = {
    "verben_trennbar_praesens": frozenset({"verb_praesens_regelm", "verb_praesens_vokalwechsel"}),
    "perfekt_haben": frozenset({"verb_sein_haben"}),
    "perfekt_sein": frozenset({"verb_sein_haben"}),
    "passiv_modalverben": frozenset({"modalverben_praesens"}),
    "plusquamperfekt": frozenset({"praeteritum_sein_haben_modal"}),
    "zustandspassiv": frozenset({"verb_sein_haben"}),
    "zustandspassiv_zeiten": frozenset({"praeteritum_sein_haben_modal", "verb_sein_haben"}),
    "futur_ii": frozenset({"futur_i", "passiv_praesens"}),
    "partizip_i_attributiv": frozenset(
        {
            "adjektivdeklination_bestimmt",
            "adjektivdeklination_unbestimmt",
            "adjektivdeklination_nullartikel",
        }
    ),
    "partizip_ii_attributiv_erweitert": frozenset(
        {
            "adjektivdeklination_bestimmt",
            "adjektivdeklination_unbestimmt",
            "adjektivdeklination_nullartikel",
        }
    ),
}


def _beats(specific: str, general: str) -> bool:
    return general in _SPECIFICITY_OVERRIDES.get(specific, frozenset())


def _pick_winner(champion: CandidateItem, challenger: CandidateItem) -> CandidateItem:
    """Which of two items claiming the same (prompt, answer) is kept.

    Resolved by ``_SPECIFICITY_OVERRIDES`` wherever the pair was actually
    analysed (see that table's own comment); every conflict this run has
    ever produced falls into one of those analysed pairs. For any conflict
    outside that table -- none identified, but the selectors are permissive
    by design and a future one is not ruled out -- the earlier-produced item
    (``champion``, by construction of the fold this is called from) is kept,
    purely so the outcome is deterministic rather than because "earlier" is
    itself a specificity judgment.
    """
    if _beats(challenger.topic_id, champion.topic_id):
        return challenger
    if _beats(champion.topic_id, challenger.topic_id):
        return champion
    return champion


def _drop_cross_topic_duplicates(
    items: list[CandidateItem],
) -> tuple[list[CandidateItem], Counter[str], list[DroppedItem]]:
    """Deduplicate on (prompt, proposed_answer) across every item this run
    produced, regardless of which sentence or topic loop iteration built it.
    Exactly one item survives per pair; every other one is counted under its
    own (losing) topic and recorded with which topic's item was kept."""
    groups: dict[tuple[str, str], list[CandidateItem]] = {}
    for item in items:
        groups.setdefault((item.prompt, item.proposed_answer), []).append(item)

    kept: list[CandidateItem] = []
    dropped_counts: Counter[str] = Counter()
    dropped_details: list[DroppedItem] = []

    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        winner = group[0]
        for challenger in group[1:]:
            winner = _pick_winner(winner, challenger)
        for item in group:
            if item is winner:
                kept.append(item)
            else:
                dropped_counts[item.topic_id] += 1
                dropped_details.append(
                    DroppedItem(
                        topic_id=item.topic_id,
                        prompt=item.prompt,
                        proposed_answer=item.proposed_answer,
                        reason="cross_topic_duplicate",
                        kept_topic_id=winner.topic_id,
                    )
                )
    # Restore original generation order (dict grouping above does not
    # preserve it once groups interleave) so downstream cap application sees
    # items in the same sentence-then-topic order they were produced in.
    kept_ids = {id(item) for item in kept}
    ordered_kept = [item for item in items if id(item) in kept_ids]
    return ordered_kept, dropped_counts, dropped_details


def _apply_caps(
    items: list[CandidateItem],
    *,
    max_items_per_topic: int,
    max_items_per_sentence: int,
) -> tuple[list[CandidateItem], Counter[str], Counter[str], list[DroppedItem]]:
    """Walk ``items`` in generation order, keeping each one unless its topic
    or its source sentence has already reached its cap -- whichever limit is
    hit first for that item is the reason it is recorded under. Caps are
    applied together in one pass (not topic-cap-then-sentence-cap as two
    separate passes) so which sentences an over-cap topic's surviving items
    come from is not itself an artefact of pass order."""
    topic_counts: Counter[str] = Counter()
    sentence_counts: Counter[str] = Counter()
    kept: list[CandidateItem] = []
    topic_cap_drops: Counter[str] = Counter()
    sentence_cap_drops: Counter[str] = Counter()
    details: list[DroppedItem] = []

    for item in items:
        sentence_key = item.source_sentence_id or item.prompt
        if topic_counts[item.topic_id] >= max_items_per_topic:
            topic_cap_drops[item.topic_id] += 1
            details.append(
                DroppedItem(
                    topic_id=item.topic_id,
                    prompt=item.prompt,
                    proposed_answer=item.proposed_answer,
                    reason="topic_cap",
                )
            )
            continue
        if sentence_counts[sentence_key] >= max_items_per_sentence:
            sentence_cap_drops[item.topic_id] += 1
            details.append(
                DroppedItem(
                    topic_id=item.topic_id,
                    prompt=item.prompt,
                    proposed_answer=item.proposed_answer,
                    reason="sentence_cap",
                )
            )
            continue
        kept.append(item)
        topic_counts[item.topic_id] += 1
        sentence_counts[sentence_key] += 1

    return kept, topic_cap_drops, sentence_cap_drops, details


@dataclass
class BlankingReport:
    """Everything a human running the pilot script (or a test) needs to
    audit what the pipeline did with a batch of sentences."""

    sentences_requested: int = 0
    sentences_tagged: int = 0
    items_by_topic: dict[str, int] = field(default_factory=dict)
    skips_by_reason: Counter[str] = field(default_factory=Counter)
    skip_details: list[SkipDetail] = field(default_factory=list)
    items: list[CandidateItem] = field(default_factory=list)
    # Balance decisions about genuinely good items -- never a quality
    # judgment, always counted separately from ``skips_by_reason`` (see
    # module docstring). Keyed by the topic whose item was dropped.
    cross_topic_duplicates_dropped: Counter[str] = field(default_factory=Counter)
    items_dropped_by_topic_cap: Counter[str] = field(default_factory=Counter)
    items_dropped_by_sentence_cap: Counter[str] = field(default_factory=Counter)
    dropped_details: list[DroppedItem] = field(default_factory=list)
    # A third category, distinct from both of the above (module docstring):
    # an item that built and reconstructed cleanly but was not the unique
    # grammatical filler of its own slot.
    skips_by_uniqueness: Counter[str] = field(default_factory=Counter)
    uniqueness_skips: list[UniquenessSkip] = field(default_factory=list)
    # A fourth category (``TypeIneligibilitySkip``'s own docstring): an item
    # that built and reconstructed cleanly, and would have passed the
    # uniqueness gate, but whose own ``type`` its topic's ``eligible_types``
    # does not list at all.
    skips_by_type_ineligibility: Counter[str] = field(default_factory=Counter)
    type_ineligibility_skips: list[TypeIneligibilitySkip] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return sum(self.items_by_topic.values())


def blank_sentences(
    sentences: list[str],
    difficulty: Difficulty = 1,
    *,
    max_items_per_topic: int = DEFAULT_MAX_ITEMS_PER_TOPIC,
    max_items_per_sentence: int = DEFAULT_MAX_ITEMS_PER_SENTENCE,
) -> BlankingReport:
    """Run every sentence in ``sentences`` through tag -> select -> blank for
    every topic in ``SELECTORS``, then deduplicate and cap the resulting
    items for the whole run (module docstring).

    Degrades cleanly with an explicit, countable skip reason when spaCy is
    unavailable (``"spacy_unavailable"``) or a given sentence fails to parse
    (``"untaggable_sentence"``) -- never raises, matching every degrade
    contract in this package.

    At most one item is produced per (sentence, topic) pair even when a
    selector finds several qualifying tokens (requirement: never two items
    from the same sentence for the same topic); the first token position a
    selector returns is used, and the sentence's other matches for that same
    topic are simply not spent on a second item. Across DIFFERENT topics,
    the same (prompt, answer) pair can still be produced more than once (two
    selectors agreeing on the same token) -- that is what
    ``_drop_cross_topic_duplicates`` resolves afterwards, not something this
    loop itself prevents.
    """
    report = BlankingReport(sentences_requested=len(sentences))
    if not sentence_tagger.analysis_available():
        report.skips_by_reason["spacy_unavailable"] += len(sentences) * len(SELECTORS)
        return report

    raw_items: list[CandidateItem] = []

    for raw in sentences:
        tagged = sentence_tagger.tag_sentence(raw)
        if tagged is None:
            report.skips_by_reason["untaggable_sentence"] += len(SELECTORS)
            report.skip_details.extend(
                SkipDetail(topic_id, raw, "untaggable_sentence") for topic_id in SELECTORS
            )
            continue
        report.sentences_tagged += 1

        for topic_id, selector in SELECTORS.items():
            candidates = selector(tagged)
            if not candidates:
                report.skips_by_reason["no_candidate_for_topic"] += 1
                report.skip_details.append(SkipDetail(topic_id, raw, "no_candidate_for_topic"))
                continue
            outcome = blank_candidate(topic_id, tagged, candidates[0], difficulty=difficulty)
            if outcome.item is None:
                reason = outcome.skip_reason or "unknown_skip_reason"
                report.skips_by_reason[reason] += 1
                report.skip_details.append(SkipDetail(topic_id, raw, reason))
                continue
            uniqueness_outcome = check_uniqueness(tagged, candidates[0])
            if not uniqueness_outcome.unique:
                reason = uniqueness_outcome.reason or "unknown_uniqueness_reason"
                report.skips_by_uniqueness[reason] += 1
                report.uniqueness_skips.append(
                    UniquenessSkip(
                        topic_id, outcome.item.prompt, outcome.item.proposed_answer, reason
                    )
                )
                continue
            # Hard assertion (docs/audits/cycle-06-modal-leak.md): an item
            # whose own type is not in its topic's declared ``eligible_types``
            # must never be emitted, regardless of how correct or how
            # solvable it otherwise is -- ``TypeIneligibilitySkip``'s own
            # docstring is the full "why a fourth bucket" argument. Checked
            # AFTER the uniqueness gate, deliberately: for every candidate
            # kind the uniqueness gate applies real logic to (modal,
            # personal pronoun, plural noun, lexical verb form), passing
            # uniqueness already implies the type is eligible -- a cue-gated
            # kind only ever passes BECAUSE a cue was supplied (uniqueness.py
            # itself), which is exactly what makes ``_cued_item_type`` return
            # ``"cloze_cued"``, the type every one of those topics' own
            # ``eligible_types`` lists. Checking type eligibility first would
            # instead mask the SPECIFIC, already-diagnostic uniqueness reason
            # (e.g. "modal_verb_interchangeable") behind a generic
            # "wrong type" one for exactly the no-cue cases cycle 4/5 already
            # built a dedicated, informative reason for. For every OTHER kind
            # (determiner, degree, adjective -- none of which the uniqueness
            # gate constrains at all, see its own module docstring's policy
            # table), this check is the only gate that ever applies, which is
            # exactly the three ``artikel_*`` topics' own situation.
            allowed_types = _eligible_types_by_topic().get(topic_id, frozenset())
            if outcome.item.type not in allowed_types:
                report.skips_by_type_ineligibility[topic_id] += 1
                report.type_ineligibility_skips.append(
                    TypeIneligibilitySkip(
                        topic_id,
                        outcome.item.prompt,
                        outcome.item.proposed_answer,
                        outcome.item.type,
                        tuple(sorted(allowed_types)),
                    )
                )
                continue
            raw_items.append(outcome.item)

    deduped_items, duplicate_counts, duplicate_details = _drop_cross_topic_duplicates(raw_items)
    report.cross_topic_duplicates_dropped = duplicate_counts

    kept_items, topic_cap_drops, sentence_cap_drops, cap_details = _apply_caps(
        deduped_items,
        max_items_per_topic=max_items_per_topic,
        max_items_per_sentence=max_items_per_sentence,
    )
    report.items_dropped_by_topic_cap = topic_cap_drops
    report.items_dropped_by_sentence_cap = sentence_cap_drops
    report.dropped_details = duplicate_details + cap_details

    report.items = kept_items
    for item in kept_items:
        report.items_by_topic[item.topic_id] = report.items_by_topic.get(item.topic_id, 0) + 1

    return report
