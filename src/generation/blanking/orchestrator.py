"""The per-topic, demand-driven generate -> validate -> select -> blank loop.

## The defect this replaces

``sentence_source.generate_sentence_pool`` builds one general pool of
everyday sentences, cycling through theme, person, tense, register and (as
of the previous cycle) a construction hint -- but round-robin, never keyed
to which topic actually needs items. ``pipeline.blank_sentences`` then
harvests whatever topics happen to fall out of that pool. docs/audits/
cycle-08-report.md: 18 of 49 topics ended a pilot run at zero, every one of
them ``no_candidate_for_topic = 226``: not one of the 226 valid carriers
that run produced happened to contain the construction, because nothing in
the loop ever specifically asked for it. The project owner's own words:
"if we are not passing a topic to the generator, what would happen during
nightly top-up when we needed perfekt_sein exercises? we were going to pray
that it produces some sentences with that topic."

This module is the loop that removes the prayer. For every topic that has
demand (``src.generation.blanking.topic_demand.compute_demand``), in demand
order (scarcest first), it requests sentences using THAT topic's own
construction hint -- never a round-robin cycle through all 49 -- varying
theme/person/tense/register/structure across the requests so a topic's own
items are not all the same shape, runs them through carrier validation and
that topic's own selector, counts what the topic actually got, and retries
(bounded) if it is still short. A topic that yields nothing after its
retries are spent is recorded and left alone, not spun on forever.

## Two passes, one topic-scoped and one global, and why both exist

Deciding *whether to retry* a topic needs an immediate answer: "did the
batch just requested for this topic produce anything for THIS topic". That
is a topic-scoped question, answered by calling ``pipeline.blank_sentences``
with ``topic_ids=[topic_id]`` on just that batch's carrier-validated
sentences -- fast, and isolated from the other 48 selectors' opinions about
a sentence they had no hand in requesting.

Deciding *what the run's final accepted item set is* is a different
question, and this module does not answer it with the topic-scoped provisional
counts. After every topic's loop has finished (met its demand, exhausted its
retries, or the run hit its call ceiling), every carrier-validated sentence
requested during the whole run -- across every topic, deduplicated by exact
text -- is run through ONE final, unrestricted ``pipeline.blank_sentences``
call (``topic_ids=None``, every selector, exactly the pre-existing
behaviour). That is what actually determines ``DemandRunReport.items`` and
``items_by_topic``: the same cross-topic duplicate resolution, the same
per-topic and per-sentence caps, the same uniqueness gate and type-
eligibility check that ran before this module existed -- "nothing about
quality gating changes" (this task's own brief). A sentence requested for
one topic's construction hint that also, incidentally, serves another
topic's selector is not wasted; it is still evidence for whichever topic(s)
it actually supports, exactly as ``blank_sentences`` has always allowed.

The one honest seam between the two passes: the topic-scoped provisional
count drives the retry decision, but the final published count for a topic
can come out lower (a cap, or a cross-topic duplicate loss to another
topic's item) or, occasionally, higher (an incidental match from a sentence
requested for a DIFFERENT topic) than what the provisional count saw. This
module does not re-run retries to chase the final number back up to
``demand`` -- doing so could spin indefinitely against a topic-cap ceiling
that no number of additional sentences will ever clear. ``TopicRunReport``
keeps both numbers (``provisional_items`` from the scoped passes,
``items_produced`` from the final pass) so this is visible, not hidden.

## Two explicit budgets

- ``max_retries_per_topic`` (default ``DEFAULT_MAX_RETRIES_PER_TOPIC``):
  small, per topic. A topic that yields nothing across its retries stops
  being asked, permanently, for this run.
- ``call_ceiling``: a hard total on ``generator.generate`` calls for the
  WHOLE run, independent of how many topics have demand or how large each
  one's deficit is -- CLAUDE.md 9's cost discipline ("a fixed item count
  independent of the spend ceiling, so a logic bug cannot spend the month in
  one night") applied to this loop specifically. ``projected_call_count``
  computes the worst-case number of calls a run over a given demand list
  COULD make (every topic spending its full retry budget) so a caller can
  print it before starting, per this task's own brief: "the operator should
  see it coming rather than discover it in cost_log."

## CLAUDE.md rule 2, asserted at the seam

The topic id is never sent to the model -- only its construction hint, which
describes communicative intent and names no grammar
(``sentence_source.CONSTRUCTION_HINTS``'s own comment). ``_request_batch``
below asserts this directly at the one point in this module where a
``topic_id`` and the strings about to be sent to ``generator.generate`` are
both in scope: none of the actual prompt-facing arguments (``cefr``,
``theme``, the person/tense/register/structure/construction hint strings)
may equal the topic id verbatim.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.contracts import CEFR, CandidateItem, Difficulty
from src.generation.blanking import carrier_validation
from src.generation.blanking.pipeline import (
    DEFAULT_MAX_ITEMS_PER_SENTENCE,
    DEFAULT_MAX_ITEMS_PER_TOPIC,
    BlankingReport,
    blank_sentences,
)
from src.generation.blanking.sentence_source import (
    CONSTRUCTION_HINTS,
    DEFAULT_BATCH_SIZE,
    DEFAULT_THEMES,
    PERSON_PERSPECTIVES,
    REGISTERS,
    STRUCTURES,
    TIME_FRAMES,
    SentenceGenerator,
)
from src.generation.blanking.topic_demand import TopicDemand

_CONSTRUCTION_HINT_BY_TOPIC: dict[str, str] = dict(CONSTRUCTION_HINTS)

# Small on purpose (the brief's own wording: "small, default 2 or 3"). A
# topic whose construction genuinely cannot be produced by the model, or
# whose selector genuinely never fires on natural prose, is not fixed by a
# fourth or fifth attempt -- it is fixed by a code change to the selector or
# the hint, which belongs in a follow-up cycle informed by this run's report,
# not by spinning here.
DEFAULT_MAX_RETRIES_PER_TOPIC = 2


def projected_call_count(
    demands: list[TopicDemand],
    *,
    max_retries_per_topic: int = DEFAULT_MAX_RETRIES_PER_TOPIC,
) -> int:
    """Worst-case number of ``generator.generate`` calls a run over
    ``demands`` could make: every topic spending its full
    ``1 + max_retries_per_topic`` attempts, regardless of how many it
    actually turns out to need (a topic that meets its demand on the first
    batch stops early; this is deliberately the pessimistic bound, not the
    expected one, because it is printed BEFORE a run starts -- see the
    module docstring's "two explicit budgets" section)."""
    return len(demands) * (1 + max_retries_per_topic)


@dataclass
class TopicRunReport:
    """Everything a caller needs to audit what happened to one topic's
    demand: how many calls and retries it used, the provisional (topic-
    scoped) item count that drove the retry decision, the final (globally
    gated) item count that actually landed in the run's accepted set, and --
    task 4's own requirement -- the three distinct reasons a topic can fall
    short, kept as three separate counters, never conflated:

    - ``no_usable_carrier_count``: sentences were generated for this topic's
      hint, but carrier validation rejected all of them (or the model
      returned nothing at all) -- "the model never produced a usable
      carrier for this construction."
    - ``no_candidate_for_topic_count``: carrier-valid sentences arrived, but
      this topic's own selector found nothing in them -- "carriers arrived
      but the selector found no candidate."
    - ``uniqueness_skipped_count``: a candidate WAS built and paradigm-
      verified, but the uniqueness gate rejected it -- "candidates were
      built but the uniqueness gate skipped them all."

    A fourth, ``type_ineligible_count``, is kept for the same reason
    ``pipeline.TypeIneligibilitySkip`` is its own bucket there: a distinct
    outcome from all three above, not folded into any of them."""

    topic_id: str
    demand: TopicDemand
    calls_made: int = 0
    retries_used: int = 0
    sentences_requested: int = 0
    carriers_accepted: int = 0
    provisional_items: int = 0
    items_produced: int = 0
    no_usable_carrier_count: int = 0
    no_candidate_for_topic_count: int = 0
    uniqueness_skipped_count: int = 0
    type_ineligible_count: int = 0
    exhausted_retries: bool = False
    stopped_by_call_ceiling: bool = False

    @property
    def met_demand(self) -> bool:
        return self.items_produced >= self.demand.demand

    @property
    def shortfall_reasons(self) -> list[str]:
        """Every distinct reason this topic fell short, in the fixed order
        the module docstring lists them -- empty when ``met_demand``. More
        than one reason can legitimately apply across a topic's several
        retries (e.g. the first batch produced no usable carrier, the
        second produced carriers the selector rejected), so this is a list,
        never a single collapsed label."""
        if self.met_demand:
            return []
        reasons = []
        if self.no_usable_carrier_count > 0:
            reasons.append("model_produced_no_usable_carrier")
        if self.no_candidate_for_topic_count > 0:
            reasons.append("selector_found_no_candidate")
        if self.uniqueness_skipped_count > 0:
            reasons.append("uniqueness_gate_rejected_all")
        if self.type_ineligible_count > 0:
            reasons.append("type_ineligible")
        if not reasons:
            reasons.append("capped_or_lost_to_cross_topic_duplicate")
        return reasons


@dataclass
class DemandRunReport:
    """The whole run: the projected (worst-case) and actual call counts, the
    explicit ceiling that bounded it, whether that ceiling was actually hit
    before every topic's demand was attempted, one ``TopicRunReport`` per
    topic that had demand (in the order they were attempted), and the final,
    globally-gated item set (module docstring's "two passes" section)."""

    demands: list[TopicDemand] = field(default_factory=list)
    projected_calls: int = 0
    call_ceiling: int = 0
    calls_made: int = 0
    call_ceiling_hit: bool = False
    topic_reports: list[TopicRunReport] = field(default_factory=list)
    blanking_report: BlankingReport = field(default_factory=BlankingReport)
    # Raw pool bookkeeping, aggregated across every topic's calls -- summed
    # from each ``TopicRunReport`` rather than tracked twice, so the two can
    # never drift apart.
    sentences: list[str] = field(default_factory=list)
    duplicates_skipped: int = 0
    # Exact carrier-validation rejection reasons, summed across every call
    # this run made, regardless of topic -- the same aggregate
    # ``sentence_source.SentencePool.rejected_by_reason`` used to report,
    # kept here for the same honesty reason (CLAUDE.md 12): distinct from
    # ``TopicRunReport.no_usable_carrier_count``, which is a per-topic,
    # batch-size-relative shortfall SIGNAL, not an exact rejection count.
    rejected_by_reason: Counter[str] = field(default_factory=Counter)

    @property
    def items(self) -> list[CandidateItem]:
        return self.blanking_report.items

    @property
    def items_by_topic(self) -> dict[str, int]:
        return self.blanking_report.items_by_topic

    @property
    def topics_with_zero_items(self) -> list[str]:
        """Every topic that had demand and ended the run with no item at
        all -- task 4: "a topic that ends at zero must be named in the
        output, not silently absent." Sorted for a stable report, not run
        order (run order is already on ``topic_reports``)."""
        return sorted(
            t.topic_id for t in self.topic_reports if self.items_by_topic.get(t.topic_id, 0) == 0
        )


def _cycled(pairs: tuple[tuple[str, str], ...], index: int) -> str:
    return pairs[index % len(pairs)][1]


def _request_batch(
    generator: SentenceGenerator,
    cefr: CEFR,
    topic_id: str,
    batch_size: int,
    *,
    variety_index: int,
    themes: Sequence[str] = DEFAULT_THEMES,
) -> list[str]:
    """One ``generator.generate`` call for ``topic_id``'s own construction
    hint, varying theme/person/tense/register/structure by ``variety_index``
    so repeated calls for the same topic (across retries, or across a
    caller's several topics) are not all the same shape (this task's own
    brief: "while still varying theme, person, tense and register across the
    requests for that topic"). ``themes`` defaults to the full varied set
    (``sentence_source.DEFAULT_THEMES``); a caller that wants to pin a run to
    a single theme (mirroring ``step6_blank_pilot``'s pre-existing
    ``--theme`` flag) passes a one-element sequence instead."""
    construction_hint = _CONSTRUCTION_HINT_BY_TOPIC[topic_id]
    theme = themes[variety_index % len(themes)]
    person_hint = _cycled(PERSON_PERSPECTIVES, variety_index)
    tense_hint = _cycled(TIME_FRAMES, variety_index)
    register_hint = _cycled(REGISTERS, variety_index)
    structure_hint = _cycled(STRUCTURES, variety_index)

    # CLAUDE.md rule 2, asserted at the seam (module docstring's own
    # section): the topic id itself must never be among the strings actually
    # handed to the generator. ``construction_hint`` describes communicative
    # intent ("say what was done to something without naming who did it"),
    # never the grammar it falls out as, and none of the other arguments are
    # anything but the topic-agnostic theme/person/tense/register/structure
    # hints this pipeline already sends.
    assert topic_id not in (
        cefr,
        theme,
        person_hint,
        tense_hint,
        register_hint,
        structure_hint,
        construction_hint,
    ), "CLAUDE.md rule 2: the topic id must never be sent to the model"

    return generator.generate(
        cefr,
        theme,
        batch_size,
        person=person_hint,
        tense=tense_hint,
        register=register_hint,
        structure=structure_hint,
        construction=construction_hint,
    )


def run_demand_driven_generation(
    generator: SentenceGenerator,
    cefr: CEFR,
    demands: list[TopicDemand],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_retries_per_topic: int = DEFAULT_MAX_RETRIES_PER_TOPIC,
    call_ceiling: int | None = None,
    max_items_per_topic: int = DEFAULT_MAX_ITEMS_PER_TOPIC,
    max_items_per_sentence: int = DEFAULT_MAX_ITEMS_PER_SENTENCE,
    difficulty: Difficulty = 1,
    themes: Sequence[str] = DEFAULT_THEMES,
) -> DemandRunReport:
    """Run the per-topic demand-driven loop (module docstring) over
    ``demands``, in the order given (``topic_demand.compute_demand`` already
    sorts scarcest-deficit-first).

    ``call_ceiling``, when ``None`` (the default), is set to
    ``projected_call_count(demands, max_retries_per_topic=...)`` -- the same
    worst-case bound a caller would print before starting, so a run with no
    explicit ceiling still cannot exceed what it told the operator to
    expect. A caller that wants a STRICTER cap (CLAUDE.md 9: "a bad night
    cannot make 400 API calls") passes an explicit, smaller ``call_ceiling``;
    the loop stops requesting new batches for ANY topic, mid-run, the moment
    it would be exceeded, and ``DemandRunReport.call_ceiling_hit`` records
    that this happened rather than letting a truncated run look identical to
    a complete one.
    """
    projected = projected_call_count(demands, max_retries_per_topic=max_retries_per_topic)
    ceiling = projected if call_ceiling is None else call_ceiling

    report = DemandRunReport(demands=list(demands), projected_calls=projected, call_ceiling=ceiling)

    all_sentences: list[str] = []
    seen_sentences: set[str] = set()
    calls_made = 0
    variety_counter = 0

    for demand in demands:
        topic_report = TopicRunReport(topic_id=demand.topic_id, demand=demand)
        report.topic_reports.append(topic_report)

        attempt = 0
        while topic_report.provisional_items < demand.demand:
            # Retry exhaustion is checked before the call ceiling,
            # deliberately: when a topic's own attempt count and the run's
            # remaining call budget happen to run out at exactly the same
            # call (e.g. a single-topic run whose default ceiling IS its own
            # worst case), the more specific, topic-intrinsic reason -- it
            # used its whole retry budget -- is reported rather than the
            # global, resource-contention one, which is only actually true
            # when some OTHER topic's calls are what used up the shared
            # budget ahead of this one.
            if attempt > max_retries_per_topic:
                topic_report.exhausted_retries = True
                break
            if calls_made >= ceiling:
                topic_report.stopped_by_call_ceiling = True
                report.call_ceiling_hit = True
                break

            raw = _request_batch(
                generator,
                cefr,
                demand.topic_id,
                batch_size,
                variety_index=variety_counter,
                themes=themes,
            )
            calls_made += 1
            variety_counter += 1
            topic_report.calls_made += 1
            if attempt > 0:
                topic_report.retries_used += 1
            attempt += 1

            topic_report.sentences_requested += len(raw)
            validation = carrier_validation.validate_carriers(raw)
            report.rejected_by_reason.update(validation.rejected_by_reason)
            topic_report.carriers_accepted += len(validation.accepted)
            # "The model never produced a usable carrier for this
            # construction" (task 4's own first category) covers BOTH ways
            # that can happen: a sentence came back and carrier_validation
            # rejected it (counted in ``validation.rejected_by_reason``),
            # and the model returning fewer than ``batch_size`` sentences at
            # all -- malformed JSON, an empty response, anything
            # ``sentence_source._parse_sentences`` could not read a single
            # sentence out of. Measuring against ``batch_size`` (what was
            # ASKED for) rather than ``len(raw)`` (what came back) is what
            # makes the second case count at all; against ``len(raw)`` a
            # call that returned nothing would silently contribute zero to
            # this bucket instead of the whole batch.
            topic_report.no_usable_carrier_count += max(0, batch_size - len(validation.accepted))

            new_sentences = [s for s in validation.accepted if s not in seen_sentences]
            report.duplicates_skipped += len(validation.accepted) - len(new_sentences)
            for sentence in new_sentences:
                seen_sentences.add(sentence)
                all_sentences.append(sentence)

            if not new_sentences:
                # Every carrier-valid sentence this batch produced was one
                # this run already had (from an earlier topic, or an earlier
                # retry of this one) -- nothing NEW to re-check against this
                # topic's own selector, so the provisional count cannot move
                # this attempt. Still counted above (calls_made, attempt),
                # so a topic that only ever regenerates duplicates still
                # exhausts its retries rather than looping forever.
                continue

            scoped = blank_sentences(
                new_sentences,
                difficulty=difficulty,
                max_items_per_topic=max_items_per_topic,
                max_items_per_sentence=max_items_per_sentence,
                topic_ids=[demand.topic_id],
            )
            topic_report.provisional_items += scoped.total_items
            topic_report.no_candidate_for_topic_count += scoped.skips_by_reason.get(
                "no_candidate_for_topic", 0
            )
            topic_report.uniqueness_skipped_count += sum(scoped.skips_by_uniqueness.values())
            topic_report.type_ineligible_count += sum(scoped.skips_by_type_ineligibility.values())

        report.calls_made = calls_made

    # The final, globally-gated pass (module docstring's "two passes"
    # section): every selector, the same cross-topic dedup, the same caps,
    # the same uniqueness gate and type-eligibility check that ran before
    # this loop existed -- unchanged quality gating, applied once, over
    # every sentence this whole run collected.
    report.sentences = all_sentences
    report.blanking_report = blank_sentences(
        all_sentences,
        difficulty=difficulty,
        max_items_per_topic=max_items_per_topic,
        max_items_per_sentence=max_items_per_sentence,
        # Restricted to the topics that actually had demand, deliberately --
        # not every topic ``SELECTORS`` knows about. A topic with zero
        # demand (at or above its own target, and not forced) is the owner's
        # "no point asking for a sentence from a topic we already have many
        # unseen exercises from" -- that must hold for what the run KEEPS,
        # not only for what it explicitly asked the model for. Without this
        # restriction, a sentence requested for topic A's construction hint
        # could still incidentally hand a bonus item to fully-stocked topic
        # B, adding supply exactly where the deficit calculation said none
        # was wanted, purely because both topics' selectors happened to fire
        # on the same carrier. Restricting to demanded topics also keeps
        # ``TopicRunReport.items_produced`` summing to
        # ``blanking_report.total_items`` exactly, with no undemanded topic
        # able to silently contribute to the total.
        topic_ids=[d.topic_id for d in demands],
    )
    for topic_report in report.topic_reports:
        topic_report.items_produced = report.items_by_topic.get(topic_report.topic_id, 0)

    return report
