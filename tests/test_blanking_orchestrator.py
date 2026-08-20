"""Tests for src/generation/blanking/orchestrator.py: the per-topic,
demand-driven generate -> validate -> select -> blank loop that replaces the
harvest model (module docstring; docs/audits/cycle-08-report.md's own
finding that 18 of 49 topics got zero items because nothing ever asked for
them specifically).

Exercised offline throughout, against the deterministic mock pool
(``sentence_source.MockSentenceGenerator``) and against small, fully
scripted fake generators built in this file -- no network, no live model
call, matching CLAUDE.md section 7."""

from __future__ import annotations

import pytest
from src.generation.blanking import orchestrator, sentence_tagger
from src.generation.blanking.sentence_source import CONSTRUCTION_HINTS, MockSentenceGenerator
from src.generation.blanking.topic_demand import TopicDemand, compute_demand

pytestmark = pytest.mark.skipif(
    not sentence_tagger.analysis_available(),
    reason="spaCy de_core_news_sm is not installed in this environment",
)

_HINT_BY_TOPIC: dict[str, str] = dict(CONSTRUCTION_HINTS)


class _ScriptedGenerator:
    """A ``SentenceGenerator`` whose response is keyed on the
    ``construction`` hint text a call carries -- lets a test control exactly
    what each TOPIC's own requests get back, since the topic id itself is
    never passed to ``generate`` (CLAUDE.md rule 2). Every call is recorded
    so a test can assert on how many calls were made and with which hints."""

    def __init__(self, sentences_by_hint: dict[str, list[str]]) -> None:
        self._sentences_by_hint = sentences_by_hint
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        cefr: str,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]:
        self.calls.append(
            {
                "cefr": cefr,
                "theme": theme,
                "count": count,
                "person": person,
                "tense": tense,
                "register": register,
                "structure": structure,
                "construction": construction,
            }
        )
        return list(self._sentences_by_hint.get(construction or "", []))


def _demand(
    topic_id: str, demand: int, *, forced: bool = False, deficit: int | None = None
) -> TopicDemand:
    return TopicDemand(
        topic_id=topic_id,
        difficulty=1,
        stock=0,
        target_unseen=12,
        deficit=demand if deficit is None else deficit,
        forced=forced,
        demand=demand,
    )


def test_topic_above_target_is_never_requested() -> None:
    """``compute_demand`` already excludes a well-stocked, unforced topic;
    this confirms the orchestrator never calls the generator for a topic
    that never appears in its demand list at all."""

    def stock(topic_id: str, difficulty: int) -> int:
        return 999 if topic_id == "futur_i" else 0

    demands = compute_demand(["futur_i", "perfekt_sein"], stock, target_unseen=12)
    assert {d.topic_id for d in demands} == {"perfekt_sein"}

    generator = _ScriptedGenerator({_HINT_BY_TOPIC["perfekt_sein"]: []})
    orchestrator.run_demand_driven_generation(generator, "A2", demands, max_retries_per_topic=1)

    requested_hints = {c["construction"] for c in generator.calls}
    assert _HINT_BY_TOPIC["futur_i"] not in requested_hints


def test_forced_topic_above_target_is_requested() -> None:
    """The pilot's own requirement: a topic already at or above target must
    still be asked for specifically when it is forced."""

    def stock(topic_id: str, difficulty: int) -> int:
        return 999

    demands = compute_demand(
        ["futur_i"], stock, target_unseen=12, forced={"futur_i"}, forced_floor=3
    )
    assert len(demands) == 1

    generator = _ScriptedGenerator({_HINT_BY_TOPIC["futur_i"]: []})
    orchestrator.run_demand_driven_generation(generator, "A2", demands, max_retries_per_topic=1)

    requested_hints = {c["construction"] for c in generator.calls}
    assert _HINT_BY_TOPIC["futur_i"] in requested_hints


def test_topic_that_yields_nothing_stops_after_its_retry_budget() -> None:
    """A topic whose every batch comes back empty must not be asked forever
    -- exactly ``1 + max_retries_per_topic`` calls, then it stops, and the
    run records why."""
    generator = _ScriptedGenerator({_HINT_BY_TOPIC["perfekt_sein"]: []})
    demands = [_demand("perfekt_sein", 12)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=2
    )

    assert len(generator.calls) == 3, "1 initial attempt + 2 retries, never a fourth"
    topic_report = report.topic_reports[0]
    assert topic_report.calls_made == 3
    assert topic_report.retries_used == 2
    assert topic_report.exhausted_retries is True
    assert topic_report.items_produced == 0
    assert "perfekt_sein" in report.topics_with_zero_items
    assert topic_report.shortfall_reasons == ["model_produced_no_usable_carrier"]


def test_topic_that_meets_demand_on_the_first_batch_does_not_retry() -> None:
    """The converse: a topic whose first batch already satisfies its demand
    must not spend any of its retry budget."""
    sentences = [
        "Meine Schwester ist gestern nach Berlin gefahren.",
        "Er ist heute Morgen sehr früh aufgewacht.",
        "Wir sind letzten Sommer nach Italien geflogen.",
    ]
    generator = _ScriptedGenerator({_HINT_BY_TOPIC["perfekt_sein"]: sentences})
    demands = [_demand("perfekt_sein", 2)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=2
    )

    topic_report = report.topic_reports[0]
    assert topic_report.retries_used == 0
    assert topic_report.calls_made == 1
    assert topic_report.met_demand is True


def test_total_call_ceiling_is_never_exceeded() -> None:
    """CLAUDE.md 9's cost discipline applied to this loop: an explicit,
    small ceiling bounds the WHOLE run's calls, even when every topic in it
    would otherwise be willing to spend its full retry budget."""
    generator = _ScriptedGenerator(
        {
            _HINT_BY_TOPIC["perfekt_sein"]: [],
            _HINT_BY_TOPIC["futur_i"]: [],
            _HINT_BY_TOPIC["nomen_plural"]: [],
        }
    )
    demands = [_demand("perfekt_sein", 12), _demand("futur_i", 12), _demand("nomen_plural", 12)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=5, call_ceiling=4
    )

    assert len(generator.calls) <= 4
    assert report.calls_made <= 4
    assert report.call_ceiling == 4
    assert report.call_ceiling_hit is True
    # Not every topic can have gotten its full 6 attempts out of a 4-call
    # budget -- at least one must show it was cut short by the ceiling
    # rather than by exhausting its own retry budget.
    assert any(t.stopped_by_call_ceiling for t in report.topic_reports)


def test_default_call_ceiling_is_the_projected_worst_case() -> None:
    """With no explicit ``call_ceiling``, the loop still cannot exceed the
    worst-case number it would have printed before starting."""
    generator = _ScriptedGenerator({_HINT_BY_TOPIC["perfekt_sein"]: []})
    demands = [_demand("perfekt_sein", 12)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=2
    )

    assert report.call_ceiling == orchestrator.projected_call_count(
        demands, max_retries_per_topic=2
    )
    assert report.calls_made <= report.call_ceiling


def test_per_topic_report_numbers_add_up() -> None:
    """The sum of every topic's own ``items_produced`` must equal the run's
    published total -- no topic's items are silently double counted, and no
    bonus item for an undemanded topic silently inflates the total (module
    docstring: the final pass is restricted to demanded topics)."""
    demands = compute_demand(
        ["perfekt_sein", "perfekt_haben", "modalverben_praesens", "nomen_plural"],
        lambda topic_id, difficulty: 0,
        target_unseen=6,
    )
    generator = MockSentenceGenerator()

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=2
    )

    assert sum(t.items_produced for t in report.topic_reports) == report.blanking_report.total_items
    assert sum(report.items_by_topic.values()) == report.blanking_report.total_items


def test_a_topic_with_zero_demand_never_receives_a_bonus_item() -> None:
    """A topic absent from ``demands`` entirely must end up with zero items
    in the final report even if a sentence requested for a DIFFERENT
    topic's construction hint would incidentally have satisfied its
    selector too -- the owner's "no point asking for a sentence from a
    topic we already have many unseen exercises from" extends to what the
    run keeps, not only to what it explicitly requests."""
    demands = [_demand("perfekt_sein", 6)]
    generator = MockSentenceGenerator()

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=2
    )

    assert set(report.items_by_topic) <= {"perfekt_sein"}
    assert all(item.topic_id == "perfekt_sein" for item in report.items)


def test_projected_call_count_is_the_worst_case_bound() -> None:
    demands = [_demand("a", 1), _demand("b", 1), _demand("c", 1)]
    assert orchestrator.projected_call_count(demands, max_retries_per_topic=2) == 3 * 3
    assert orchestrator.projected_call_count(demands, max_retries_per_topic=0) == 3


class _ByCallCountGenerator:
    """A ``SentenceGenerator`` whose response for a given ``construction``
    hint depends on how many times THAT hint has already been requested --
    lets a test prove a topic's redistributed second-round attempt actually
    gets exercised (returns nothing on the first N calls, then a usable
    batch), not just that ``calls_made`` went up."""

    def __init__(self, sentences_by_hint_by_call_index: dict[str, list[list[str]]]) -> None:
        self._by_hint = sentences_by_hint_by_call_index
        self._call_counts: dict[str, int] = {}
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        cefr: str,
        theme: str,
        count: int,
        *,
        person: str | None = None,
        tense: str | None = None,
        register: str | None = None,
        structure: str | None = None,
        construction: str | None = None,
    ) -> list[str]:
        hint = construction or ""
        self.calls.append({"construction": hint})
        responses = self._by_hint.get(hint, [])
        index = self._call_counts.get(hint, 0)
        self._call_counts[hint] = index + 1
        if index >= len(responses):
            return []
        return list(responses[index])


def test_topic_already_met_by_other_topics_sentences_is_skipped_with_zero_calls() -> None:
    """TODO 1.8: a topic must never spend a call finding out something it
    could already tell from sentences OTHER topics' calls collected. Here
    ``perfekt_sein``'s own sentences ("ist"/"sind" + participle) also satisfy
    ``verb_sein_haben`` (any present-tense sein/haben, no participle
    required) when ``verb_sein_haben`` is checked alone -- exactly the
    cross-topic fallout the module docstring describes."""
    sentences = [
        "Meine Schwester ist gestern nach Berlin gefahren.",
        "Er ist heute Morgen sehr früh aufgewacht.",
        "Wir sind letzten Sommer nach Italien geflogen.",
    ]
    generator = _ScriptedGenerator(
        {
            _HINT_BY_TOPIC["perfekt_sein"]: sentences,
            _HINT_BY_TOPIC["verb_sein_haben"]: ["should never be requested"],
        }
    )
    demands = [_demand("perfekt_sein", 2), _demand("verb_sein_haben", 2)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=1
    )

    verb_sein_haben_report = next(
        t for t in report.topic_reports if t.topic_id == "verb_sein_haben"
    )
    assert verb_sein_haben_report.already_met_by_other_topics is True
    assert verb_sein_haben_report.calls_made == 0

    requested_hints = {c["construction"] for c in generator.calls}
    assert _HINT_BY_TOPIC["verb_sein_haben"] not in requested_hints


def test_starved_topic_gets_a_second_budget_from_redistribution() -> None:
    """TODO 1.8's own second half: calls a topic did not need (here,
    ``perfekt_sein`` meets its demand in one call) are not simply wasted on
    topics the run already has evidence for -- they are spent on a topic
    that exhausted ITS OWN first-pass retries still short. ``futur_i``
    returns nothing for its first two attempts (its whole normal
    ``1 + max_retries_per_topic`` budget) and only succeeds on what would be
    a third attempt -- reachable only via the redistribution pass."""
    perfekt_sein_sentences = [
        "Meine Schwester ist gestern nach Berlin gefahren.",
        "Er ist heute Morgen sehr früh aufgewacht.",
    ]
    futur_i_sentences = [
        "Ich werde morgen kommen.",
        "Ich werde morgen ins Kino gehen.",
    ]
    generator = _ByCallCountGenerator(
        {
            _HINT_BY_TOPIC["perfekt_sein"]: [perfekt_sein_sentences],
            _HINT_BY_TOPIC["futur_i"]: [[], [], futur_i_sentences],
        }
    )
    demands = [_demand("perfekt_sein", 2), _demand("futur_i", 2)]

    report = orchestrator.run_demand_driven_generation(
        generator, "A2", demands, max_retries_per_topic=1
    )

    futur_i_report = next(t for t in report.topic_reports if t.topic_id == "futur_i")
    assert futur_i_report.calls_made == 3, "2 in the main pass, 1 more from redistribution"
    assert futur_i_report.used_redistributed_budget is True
    assert futur_i_report.met_demand is True
    assert report.calls_made == 4  # 1 (perfekt_sein) + 2 (futur_i main) + 1 (redistribution)
    assert report.calls_made <= report.call_ceiling


def test_request_batch_asserts_topic_id_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md rule 2, asserted directly at the seam: if a construction
    hint ever regressed to literally containing (or equalling) its own
    topic id, ``_request_batch`` must refuse to send it, not silently pass
    the leak through to the generator."""
    monkeypatch.setitem(orchestrator._CONSTRUCTION_HINT_BY_TOPIC, "leaky_topic", "leaky_topic")
    generator = _ScriptedGenerator({})

    with pytest.raises(AssertionError):
        orchestrator._request_batch(generator, "A2", "leaky_topic", 6, variety_index=0)
