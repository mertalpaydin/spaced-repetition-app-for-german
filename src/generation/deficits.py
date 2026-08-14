"""Per-topic, per-difficulty stock deficit calculation feeding nightly batch generation.

Formula, from docs/04-application.md ("Deficit calculation"):

    projected_demand = items the scheduler will consume for this tag
                        over the next 14 days, from due_at across the FSRS queue
    stock            = unseen items in bank for this tag at the relevant tier
    deficit          = ceil(projected_demand * SAFETY_FACTOR) - stock
    generate if deficit > MIN_BATCH_THRESHOLD

``projected_demand`` depends on the FSRS scheduler and ``tag_state`` (owned by
``src/engine``, a different module this agent does not own), so it is taken here
as an input rather than derived. The deficit arithmetic, the per-tier target, the
minimum-batch-worth-generating gate, and the nightly item cap below are fully
implemented and unit tested against this formula.
"""

import math
from dataclasses import dataclass

from src.contracts import MIN_STOCK_PER_TIER, Difficulty, GenerationRequest, ItemType

# ==============================================================================
# Automation guardrail constants
#
# Deliberately not in src/contracts.py: that module is owned by another agent.
# If the maintainers decide these belong alongside the other pacing constants,
# they can be moved; nothing here depends on their location.
# ==============================================================================

SAFETY_FACTOR: float = 1.5
"""Buffer multiplier applied to projected demand before comparing to stock."""

MIN_BATCH_THRESHOLD: int = 5
"""A deficit at or below this is not worth a batch submission on its own."""

NIGHTLY_ITEM_CAP: int = 200
"""Hard ceiling on items requested in one nightly run, independent of the spend
ceiling in src/llm/client.py, so a logic bug in the deficit math cannot spend the
month's budget in a single night (CLAUDE.md rule 9)."""

MIN_STOCK_PER_TIER_DEMAND_STANDIN: int = MIN_STOCK_PER_TIER
"""Placeholder for ``projected_demand`` used only by the nightly automation
entry point (src/generation/batch_client.py), pending the real 14-day FSRS
forecast from src/engine (owned by another agent). Not used by the deficit
math itself, which takes ``projected_demand`` as an explicit input and is
fully tested against the documented formula regardless of where the demand
number ultimately comes from."""


@dataclass(frozen=True)
class TopicDeficit:
    """Deficit for a single (topic, difficulty) cell."""

    topic_id: str
    difficulty: Difficulty
    projected_demand: int
    stock: int
    deficit: int


def compute_deficit(projected_demand: int, stock: int, safety_factor: float = SAFETY_FACTOR) -> int:
    """`ceil(projected_demand * safety_factor) - stock`, floored at zero."""
    target = math.ceil(projected_demand * safety_factor)
    return max(0, target - stock)


def should_generate(deficit: int, min_batch_threshold: int = MIN_BATCH_THRESHOLD) -> bool:
    """Whether a deficit is large enough to be worth a batch submission."""
    return deficit > min_batch_threshold


def compute_topic_deficits(
    demand_and_stock: dict[tuple[str, Difficulty], tuple[int, int]],
    safety_factor: float = SAFETY_FACTOR,
) -> list[TopicDeficit]:
    """Compute deficits for every (topic_id, difficulty) cell with known demand/stock.

    ``demand_and_stock`` maps (topic_id, difficulty) -> (projected_demand, stock).
    Only cells with deficit > 0 are returned.
    """
    results: list[TopicDeficit] = []
    for (topic_id, difficulty), (demand, stock) in demand_and_stock.items():
        deficit = compute_deficit(demand, stock, safety_factor=safety_factor)
        if deficit > 0:
            results.append(
                TopicDeficit(
                    topic_id=topic_id,
                    difficulty=difficulty,
                    projected_demand=demand,
                    stock=stock,
                    deficit=deficit,
                )
            )
    return results


def apply_nightly_cap(
    deficits: list[TopicDeficit], item_cap: int = NIGHTLY_ITEM_CAP
) -> list[TopicDeficit]:
    """Trim a deficit list so the sum of counts never exceeds ``item_cap``.

    A single logic bug that reports enormous deficits (e.g. a stray zero, or a
    unit error in projected_demand) must not be able to request more than
    ``item_cap`` items in one run, independent of whatever the spend ceiling
    would otherwise allow.
    """
    capped: list[TopicDeficit] = []
    remaining = item_cap
    for d in deficits:
        if remaining <= 0:
            break
        take = min(d.deficit, remaining)
        if take <= 0:
            continue
        capped.append(
            TopicDeficit(
                topic_id=d.topic_id,
                difficulty=d.difficulty,
                projected_demand=d.projected_demand,
                stock=d.stock,
                deficit=take,
            )
        )
        remaining -= take
    return capped


def build_generation_requests(
    deficits: list[TopicDeficit],
    eligible_types_by_topic: dict[str, list[ItemType]],
    min_batch_threshold: int = MIN_BATCH_THRESHOLD,
    item_cap: int = NIGHTLY_ITEM_CAP,
) -> list[GenerationRequest]:
    """Turn deficits into ``GenerationRequest``s, gated and capped.

    Only deficits that pass ``should_generate`` are included, and the nightly
    item cap is applied across the whole batch (not per topic), so a handful of
    badly-understocked topics cannot alone exhaust the cap while starving
    everything else -- deficits are processed in the order given, so callers
    that want fairness should sort (e.g. largest deficit first, or round-robin)
    before calling this.
    """
    eligible = [d for d in deficits if should_generate(d.deficit, min_batch_threshold)]
    capped = apply_nightly_cap(eligible, item_cap=item_cap)

    requests: list[GenerationRequest] = []
    for d in capped:
        item_types = eligible_types_by_topic.get(d.topic_id) or ["cloze_free"]
        requests.append(
            GenerationRequest(
                topic_id=d.topic_id,
                count=d.deficit,
                difficulty=d.difficulty,
                item_types=list(item_types),
            )
        )
    return requests
