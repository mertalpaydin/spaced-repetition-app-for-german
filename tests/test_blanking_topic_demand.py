"""Tests for src/generation/blanking/topic_demand.py: demand computed from
real bank stock, never guessed from pool composition.

No database anywhere in this file (CLAUDE.md section 8): ``stock_lookup`` is
always a plain Python callable built inline, exactly the shape
``SqliteItemBank.stock`` has, but never a real ``SqliteItemBank``."""

from __future__ import annotations

from src.generation.blanking.topic_demand import StockLookup, TopicDemand, compute_demand


def _fixed_stock(values: dict[str, int]) -> StockLookup:
    def lookup(topic_id: str, difficulty: int) -> int:
        return values.get(topic_id, 0)

    return lookup


def test_topic_at_or_above_target_gets_zero_demand_and_is_dropped() -> None:
    """The owner's own words: "there is no point of asking for a sentence
    from a topic if we have already many unseen exercises from it." A topic
    whose stock already meets or exceeds the target is not merely given a
    demand of zero, it is absent from the result entirely."""
    stock = _fixed_stock({"perfekt_sein": 12, "futur_i": 20})
    demands = compute_demand(["perfekt_sein", "futur_i"], stock, target_unseen=12)

    assert demands == []


def test_topic_below_target_gets_exactly_the_deficit() -> None:
    stock = _fixed_stock({"perfekt_sein": 4})
    demands = compute_demand(["perfekt_sein"], stock, target_unseen=12)

    assert len(demands) == 1
    demand = demands[0]
    assert demand.topic_id == "perfekt_sein"
    assert demand.stock == 4
    assert demand.target_unseen == 12
    assert demand.deficit == 8
    assert demand.demand == 8
    assert demand.forced is False


def test_unstocked_topic_gets_full_target_as_deficit() -> None:
    stock = _fixed_stock({})
    demands = compute_demand(["perfekt_sein"], stock, target_unseen=12)

    assert demands[0].deficit == 12
    assert demands[0].demand == 12


def test_forced_topic_above_target_is_still_requested() -> None:
    """The second half of the owner's instruction: during a pilot, a topic
    that already has stock must still be asked for specifically, so
    problems in its own construction hint are visible. Forcing does not
    depend on the topic having any organic deficit at all."""
    stock = _fixed_stock({"perfekt_sein": 999})
    demands = compute_demand(
        ["perfekt_sein"], stock, target_unseen=12, forced={"perfekt_sein"}, forced_floor=5
    )

    assert len(demands) == 1
    demand = demands[0]
    assert demand.forced is True
    assert demand.deficit == 0
    assert demand.demand == 5, "forced floor applies when the organic deficit is zero"


def test_forcing_is_additive_never_a_replacement_for_a_real_deficit() -> None:
    """ "Forcing is additive to demand, never a replacement for it" -- a
    forced topic with a real, larger deficit gets the full deficit, not
    just the floor."""
    stock = _fixed_stock({"perfekt_sein": 0})
    demands = compute_demand(
        ["perfekt_sein"], stock, target_unseen=12, forced={"perfekt_sein"}, forced_floor=5
    )

    assert demands[0].deficit == 12
    assert demands[0].demand == 12, "the real deficit (12) beats the floor (5)"


def test_unforced_topic_with_zero_deficit_is_never_requested() -> None:
    """The mirror image of forcing: an ordinary (non-pilot) run must never
    ask a fully-stocked topic for more items just because some OTHER topic
    in the same call happens to be forced."""
    stock = _fixed_stock({"well_stocked": 50, "understocked": 0})
    demands = compute_demand(
        ["well_stocked", "understocked"],
        stock,
        target_unseen=12,
        forced={"understocked"},
    )

    topic_ids = {d.topic_id for d in demands}
    assert topic_ids == {"understocked"}


def test_demands_sorted_by_deficit_descending() -> None:
    """The scarcest topic is served first when a caller's budget runs out
    partway through a run."""
    stock = _fixed_stock({"a": 10, "b": 0, "c": 6})
    demands = compute_demand(["a", "b", "c"], stock, target_unseen=12)

    assert [d.topic_id for d in demands] == ["b", "c", "a"]
    assert [d.deficit for d in demands] == [12, 6, 2]


def test_demands_tie_broken_by_topic_id_for_a_deterministic_order() -> None:
    stock = _fixed_stock({})
    demands = compute_demand(["zeta", "alpha", "mu"], stock, target_unseen=12)

    assert [d.topic_id for d in demands] == ["alpha", "mu", "zeta"]


def test_compute_demand_calls_stock_lookup_once_per_topic_with_difficulty() -> None:
    calls: list[tuple[str, int]] = []

    def spy_lookup(topic_id: str, difficulty: int) -> int:
        calls.append((topic_id, difficulty))
        return 0

    compute_demand(["a", "b"], spy_lookup, difficulty=2, target_unseen=12)

    assert calls == [("a", 2), ("b", 2)]


def test_topic_demand_is_a_frozen_dataclass() -> None:
    demand = TopicDemand(
        topic_id="x",
        difficulty=1,
        stock=0,
        target_unseen=12,
        deficit=12,
        forced=False,
        demand=12,
    )
    assert demand.topic_id == "x"
