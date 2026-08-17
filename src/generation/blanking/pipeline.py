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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from src.contracts import CandidateItem, Difficulty
from src.generation.blanking import sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.selectors import SELECTORS

TOPIC_IDS: tuple[str, ...] = tuple(SELECTORS)

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
#   token).
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
    "futur_ii": frozenset({"futur_i"}),
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
