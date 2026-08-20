"""Per-topic generation demand, computed from real bank stock.

## The defect this fixes

``sentence_source.generate_sentence_pool`` used to build one general pool of
everyday sentences and hand it to ``pipeline.blank_sentences``, which then
harvested whatever topics happened to fall out of it. Nothing told the
generator which topics actually needed items, so a topic could go through an
entire pilot run with zero requests aimed at it and simply never appear --
docs/audits/cycle-08-report.md: 18 of 49 topics ended at zero, every one of
them for the same reason, ``no_candidate_for_topic = 226``: not one of the
226 valid carriers in that run's pool happened to contain the construction,
because nothing ever asked for it.

This module is the fix for the first half of that: **demand**, computed from
the bank, not guessed from pool composition. ``SqliteItemBank.stock(tag_id,
difficulty)`` already counts UNSEEN items for a topic -- items with no row in
``review_logs``, i.e. items the learner has not yet met. That is exactly the
right measure of "how much runway does this topic have left": an item the
learner has already answered is not stock, regardless of how many rows for
that topic sit in the database.

``compute_demand`` is: ``deficit = max(0, target_unseen - current_unseen)``
per topic. A topic at or above its target has zero deficit and gets no
demand at all -- the project owner's own words: "there is no point of asking
for a sentence from a topic if we have already many unseen exercises from
it." This is deliberately simpler than ``src.generation.deficits`` (the
LLM-direct pipeline's own deficit calculation, which multiplies a projected
14-day FSRS demand by a safety factor): that pipeline is fed by a real
scheduler forecast this one does not have access to, and folding in a
speculative "how many will the learner actually consume" projection here
would only make an already-hard-to-audit number harder to audit, for a
generation-loop bug this task is specifically about, not the deficit
formula. If ``src.generation.deficits``'s FSRS-driven demand forecast should
also apply to this pipeline, that is a separate change, made deliberately,
not folded in here.

## Forcing, for pilots

The same owner instruction, second half: "however during pilot you need to
see exercises from problematic topics to identify issues. even if those
problematic have exercises, ask sentences with those topics specifically."
A pilot needs coverage, not top-up: it wants to SEE what a topic's own
construction hint currently produces, even for a topic that already has
plenty of stock. ``forced`` names topics that get demand regardless of
deficit -- ``FORCED_FLOOR`` items' worth, or the topic's own (possibly
larger) organic deficit, whichever is bigger. Forcing is additive to
demand, never a replacement for it: a forced, badly-understocked topic still
gets its full deficit, not just the floor.

## No database in tests

``stock_lookup`` is taken as an injected callable (``StockLookup``), not a
``SqliteItemBank`` instance, so this module needs no database at all to be
exercised (CLAUDE.md section 8: "functions that touch the network, the
filesystem, or the clock take those as injected dependencies so they can be
faked in tests"). ``SqliteItemBank.stock`` matches this signature exactly
(``tag_id: str, difficulty: Difficulty) -> int``), so a real bank can be
plugged in with ``stock_lookup=bank.stock`` with no adapter needed.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from src.contracts import MIN_STOCK_PER_TIER, Difficulty

StockLookup = Callable[[str, Difficulty], int]

# 02-content-pipeline.md's own cold-seed target ("12 items/topic, A1-B2,
# cold-seeded" -- src/contracts.py's own comment on MIN_STOCK_PER_TIER),
# reused here as the default "how many unseen items should this topic
# always have on hand" target rather than inventing a second number that
# means almost the same thing. A plain assignment, not ``import ... as``:
# mypy --strict does not treat a renaming import as an explicitly re-
# exported attribute, so another module importing ``DEFAULT_TARGET_UNSEEN``
# from here would fail ``attr-defined`` under a renamed import but not under
# this plain module-level name.
DEFAULT_TARGET_UNSEEN = MIN_STOCK_PER_TIER

# How much demand a forced topic gets when its own organic deficit is zero
# or small -- enough to see several fresh examples of what the topic's own
# construction hint currently produces (a pilot's actual job, per this
# module's own docstring), not a full top-up's worth of budget spent on a
# topic that is not short of anything.
DEFAULT_FORCED_FLOOR = 5


@dataclass(frozen=True)
class TopicDemand:
    """Demand for one topic at one difficulty tier.

    ``deficit`` is the organic, stock-driven number
    (``max(0, target_unseen - stock)``); ``demand`` is what a caller should
    actually request -- equal to ``deficit`` unless ``forced`` is ``True``,
    in which case it is ``max(deficit, forced_floor)``. Both are kept, not
    just the final ``demand``, so a report can show a forced topic's real
    stock situation alongside the number actually requested (task 4: "a
    topic that ends at zero must be named in the output, not silently
    absent" applies just as much to *why* a topic was asked for as to why it
    got nothing back)."""

    topic_id: str
    difficulty: Difficulty
    stock: int
    target_unseen: int
    deficit: int
    forced: bool
    demand: int


def compute_demand(
    topic_ids: Sequence[str],
    stock_lookup: StockLookup,
    *,
    difficulty: Difficulty = 1,
    target_unseen: int = DEFAULT_TARGET_UNSEEN,
    forced: Collection[str] = (),
    forced_floor: int = DEFAULT_FORCED_FLOOR,
) -> list[TopicDemand]:
    """Demand for every topic in ``topic_ids``, one ``stock_lookup`` call per
    topic, at a single ``difficulty`` tier.

    A topic is included only when its resulting ``demand`` is greater than
    zero: a topic at or above ``target_unseen`` and not in ``forced`` has
    ``deficit == 0`` and ``demand == 0`` and is dropped entirely, exactly
    the owner's "no point asking for a sentence from a topic we already have
    many unseen exercises from." A forced topic is never dropped, because
    ``max(deficit, forced_floor)`` is at least ``forced_floor`` (assumed
    positive; a ``forced_floor`` of zero would let a forced, fully-stocked
    topic drop out too, which defeats the point of forcing it, so callers
    should not pass zero there).

    Returned sorted by ``deficit`` descending (ties broken by ``topic_id``
    for a deterministic order), so the scarcest topic is served first if a
    caller's total call budget runs out partway through -- forcing does not
    change this ordering, because forcing is about guaranteeing a topic is
    *included*, not about jumping it to the front of an already-adequately-
    stocked queue ahead of topics that are genuinely running out."""
    demands: list[TopicDemand] = []
    for topic_id in topic_ids:
        stock = stock_lookup(topic_id, difficulty)
        deficit = max(0, target_unseen - stock)
        is_forced = topic_id in forced
        demand = max(deficit, forced_floor) if is_forced else deficit
        if demand <= 0:
            continue
        demands.append(
            TopicDemand(
                topic_id=topic_id,
                difficulty=difficulty,
                stock=stock,
                target_unseen=target_unseen,
                deficit=deficit,
                forced=is_forced,
                demand=demand,
            )
        )

    demands.sort(key=lambda d: (-d.deficit, d.topic_id))
    return demands
