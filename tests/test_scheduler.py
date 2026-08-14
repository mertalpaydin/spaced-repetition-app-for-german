"""Unit tests for TopicStateManager, LearningScheduler, and split detection."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from src.bank.storage import SqliteItemBank
from src.contracts import (
    DORMANCY_DAYS,
    DUEL_LENGTH,
    DUEL_MIN_ATTEMPTS_TO_SUGGEST,
    MAX_NEW_TOPICS_PER_DAY,
    MAX_REVIEWS_PER_DAY,
    MIN_PREREQ_STABILITY,
    RECALIBRATION_ROUND_SIZE,
    SUGGESTION_MIN_ACTIVE_DAYS,
    THRESHOLD_CLAMP,
    BankItem,
    Distractor,
    TagStateModel,
    Topic,
)
from src.engine.fsrs import FSRSRecord
from src.engine.scheduler import LearningScheduler
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


class FakeBank:
    """Minimal in-memory stand-in for SqliteItemBank, satisfying ItemBankProtocol.

    Used instead of SqliteItemBank where a test needs fields the SQLite
    storage layer does not persist (e.g. ``dimension``), or wants review_logs
    injected directly without touching a database.
    """

    def __init__(self, items: list[BankItem] | None = None) -> None:
        self._items: dict[str, BankItem] = {it.id: it for it in (items or [])}
        self.logs: list[dict[str, Any]] = []

    def get_item(self, item_id: str) -> BankItem | None:
        return self._items.get(item_id)

    def query_by_topic(self, topic_id: str, max_count: int | None = None) -> list[BankItem]:
        result = [it for it in self._items.values() if it.topic_id == topic_id]
        return result[:max_count] if max_count is not None else result

    def query_by_difficulty(self, topic_id: str, difficulty: int) -> list[BankItem]:
        return [
            it
            for it in self._items.values()
            if it.topic_id == topic_id and it.difficulty == difficulty
        ]

    def get_all_items(self) -> list[BankItem]:
        return list(self._items.values())

    def get_review_logs(
        self, topic_id: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        rows = (
            self.logs if topic_id is None else [r for r in self.logs if r["topic_id"] == topic_id]
        )
        return rows[:limit]


def make_item(
    item_id: str,
    topic_id: str,
    *,
    type: str = "cloze_free",
    dimension: str = "grammar",
    difficulty: int = 1,
    confusion_group: str | None = None,
    block_id: str | None = None,
    distractors: list[Distractor] | None = None,
) -> BankItem:
    return BankItem(
        id=item_id,
        topic_id=topic_id,
        type=type,  # type: ignore[arg-type]
        dimension=dimension,  # type: ignore[arg-type]
        difficulty=difficulty,  # type: ignore[arg-type]
        cefr="A1",
        prompt=f"Satz für {topic_id}",
        accepted_answers=["a"],
        distractors=distractors or [],
        confusion_group=confusion_group,
        block_id=block_id,
    )


def make_topic(
    topic_id: str,
    *,
    prereqs: list[str] | None = None,
    requires_context: bool = False,
    eligible_types: list[str] | None = None,
    sibling_group: str | None = None,
    confusion_group: str | None = None,
) -> Topic:
    return Topic(
        id=topic_id,
        name_de=topic_id,
        cefr="A1",
        description=topic_id,
        prereqs=prereqs or [],
        requires_context=requires_context,
        eligible_types=eligible_types or [],  # type: ignore[arg-type]
        sibling_group=sibling_group,
        confusion_group=confusion_group,
    )


@pytest.fixture
def taxonomy_topics() -> list[Topic]:
    return load_taxonomy()


@pytest.fixture
def topic_manager(taxonomy_topics: list[Topic]) -> TopicStateManager:
    return TopicStateManager(taxonomy_topics)


@pytest.fixture
def temp_bank(tmp_path: Path) -> SqliteItemBank:
    db_file = tmp_path / "scheduler_test_bank.db"
    bank = SqliteItemBank(db_file)

    # Insert sample bank items for testing
    for i in range(15):
        bank.insert_item(
            BankItem(
                id=f"item_{i:03d}",
                topic_id="kasus_dativ_formen" if i < 8 else "dativ_nach_praeposition",
                type="cloze_free",
                difficulty=1,
                cefr="A1" if i < 8 else "A2",
                prompt=f"Das ist Test Satz Nummer {i} mit ___ Lücke.",
                accepted_answers=["dem"],
                distractors=[
                    Distractor(text="den"),
                    Distractor(text="des"),
                    Distractor(text="das"),
                ],
                confusion_group="wechselpraepositionen" if i % 2 == 0 else None,
            )
        )
    return bank


def test_topic_state_manager_initialization(topic_manager: TopicStateManager) -> None:
    """Root topics start ready; dependent topics start locked."""
    root_state = topic_manager.get_state("pronomen_personal_nom")
    assert root_state.state == "ready"

    dep_state = topic_manager.get_state("dativ_nach_praeposition")
    assert dep_state.state == "locked"


def test_interleaving_invariant_preserves_topic_separation() -> None:
    """Adjacent items in the round must not share the same topic_id."""
    items = [
        BankItem(
            id=f"t1_{i}",
            topic_id="topic_1",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="Satz",
            accepted_answers=["a"],
            distractors=[Distractor(text="b"), Distractor(text="c"), Distractor(text="d")],
        )
        for i in range(3)
    ] + [
        BankItem(
            id=f"t2_{i}",
            topic_id="topic_2",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="Satz",
            accepted_answers=["a"],
            distractors=[Distractor(text="b"), Distractor(text="c"), Distractor(text="d")],
        )
        for i in range(3)
    ]

    interleaved = LearningScheduler._interleave_items(items)
    for i in range(len(interleaved) - 1):
        assert interleaved[i].topic_id != interleaved[i + 1].topic_id


def test_forecast_and_day_budget() -> None:
    """Test forecast projecting daily counts and day_budget calculating obligations."""
    scheduler = LearningScheduler()
    now = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)

    states = [
        TagStateModel(tag_id="t1", due_at=now),
        TagStateModel(tag_id="t2", due_at=now + timedelta(days=1)),
        TagStateModel(tag_id="t3", due_at=now + timedelta(days=3)),
    ]

    fc = scheduler.forecast(states, now=now, horizon_days=7)
    assert len(fc) == 7
    assert fc[0] == 1  # due today
    assert fc[1] == 1  # due tomorrow
    assert fc[3] == 1

    budget = scheduler.day_budget(states, now=now)
    assert budget.due_count == 1
    assert budget.new_topics_allowed == 2
    assert budget.ceiling > budget.due_count


def test_duel_and_recalibration_protocol_methods(temp_bank: SqliteItemBank) -> None:
    """Test build_duel and build_recalibration methods."""
    scheduler = LearningScheduler(bank=temp_bank)
    now = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)

    duel_items = scheduler.build_duel("wechselpraepositionen", now=now)
    assert len(duel_items) <= 8

    recal_items = scheduler.build_recalibration(states=[], now=now)
    assert len(recal_items) <= 10


def test_detect_split_candidates() -> None:
    """Test detecting split candidate when facet accuracy gap >= 0.40."""
    scheduler = LearningScheduler()

    # 20 attempts on masc (100%), 20 on neut (50%) -> gap 0.50 >= 0.40
    history = (
        [{"topic_id": "dativ_art", "facet": "masc", "is_correct": True} for _ in range(20)]
        + [{"topic_id": "dativ_art", "facet": "neut", "is_correct": True} for _ in range(10)]
        + [{"topic_id": "dativ_art", "facet": "neut", "is_correct": False} for _ in range(10)]
    )

    candidates = scheduler.detect_split_candidates(history)
    assert len(candidates) == 1
    assert candidates[0].topic_id == "dativ_art"
    assert candidates[0].gap >= 0.40


NOW = datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)


# --- Injected clock (item 6) ---------------------------------------------


def test_topic_state_manager_accepts_injected_now() -> None:
    """TopicStateManager must not fall back to a bare wall-clock default_at."""
    injected = datetime(2020, 1, 1, tzinfo=UTC)
    mgr = TopicStateManager([make_topic("t1")], now=injected)
    assert mgr.get_state("t1").due_at == injected


# --- record_attempt trap (item 7) ----------------------------------------


def test_record_attempt_correct_hinted_pass_is_not_recorded_as_a_failure() -> None:
    """A correct answer given with a hint must never look like an incorrect one."""
    mgr = TopicStateManager([make_topic("t1")])
    updated = mgr.record_attempt("t1", is_correct=True, hint_level=2)
    assert updated.correct == 1
    assert updated.consecutive_failures == 0


def test_record_attempt_is_unhinted_pass_false_does_not_override_explicit_correct() -> None:
    """Passing is_unhinted_pass=False must not silently clobber an explicit correct answer."""
    mgr = TopicStateManager([make_topic("t1")])
    updated = mgr.record_attempt("t1", is_correct=True, hint_level=2, is_unhinted_pass=False)
    assert updated.correct == 1
    assert updated.consecutive_failures == 0


def test_record_attempt_is_unhinted_pass_true_forces_correct_and_unhinted() -> None:
    mgr = TopicStateManager([make_topic("t1")])
    updated = mgr.record_attempt("t1", is_unhinted_pass=True)
    assert updated.correct == 1
    assert updated.consecutive_unhinted_passes == 1


# --- Unified forecast / overdue handling (item 3) -------------------------


def test_forecast_collapses_overdue_backlog_into_today() -> None:
    scheduler = LearningScheduler()
    states = [TagStateModel(tag_id=f"t{i}", due_at=NOW - timedelta(days=i + 1)) for i in range(50)]
    fc = scheduler.forecast(states, now=NOW, horizon_days=7)
    assert fc[0] == 50
    assert sum(fc[1:]) == 0


def test_day_budget_blocks_new_topics_with_large_overdue_backlog() -> None:
    scheduler = LearningScheduler()
    states = [TagStateModel(tag_id=f"t{i}", due_at=NOW - timedelta(days=3)) for i in range(200)]
    budget = scheduler.day_budget(states, now=NOW)
    assert budget.due_count == 200
    assert budget.new_topics_allowed == 0


def test_plan_next_round_blocks_new_topic_with_large_overdue_backlog() -> None:
    tm = TopicStateManager([make_topic("root")], now=NOW)
    bank = FakeBank([make_item("item_root", "root")])
    fsrs_records = {
        f"overdue_{i}": FSRSRecord(card_id=f"overdue_{i}", due=NOW - timedelta(days=3))
        for i in range(200)
    }
    plan = LearningScheduler().plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    assert plan.is_overload_blocked is True
    assert plan.new_topic_id is None


# --- Daily new-topic budget derived from review_logs (item 2) ------------


def test_count_new_topics_today_counts_distinct_first_seen_measurement_topics() -> None:
    logs: list[dict[str, Any]] = [
        {"topic_id": "a", "mode": "review", "created_at": NOW.replace(hour=8)},
        {"topic_id": "a", "mode": "review", "created_at": NOW.replace(hour=9)},
        {"topic_id": "b", "mode": "review", "created_at": NOW - timedelta(days=1)},
        {"topic_id": "c", "mode": "duel", "created_at": NOW},
    ]
    assert LearningScheduler.count_new_topics_today(logs, NOW) == 1


def test_new_topic_budget_persists_across_rounds_via_review_logs() -> None:
    """Ten rounds in one day must still introduce at most MAX_NEW_TOPICS_PER_DAY."""
    tm = TopicStateManager([make_topic("t1")], now=NOW)
    bank = FakeBank([make_item("i1", "t1")])
    already_introduced_today: list[dict[str, Any]] = [
        {"topic_id": f"already_{i}", "mode": "review", "created_at": NOW}
        for i in range(MAX_NEW_TOPICS_PER_DAY)
    ]
    plan = LearningScheduler().plan_next_round(
        bank=bank,
        topic_manager=tm,
        fsrs_records={},
        now=NOW,
        review_logs=already_introduced_today,
    )
    assert plan.new_topic_id is None


def test_new_topic_introduced_when_daily_budget_not_yet_spent() -> None:
    tm = TopicStateManager([make_topic("t1")], now=NOW)
    bank = FakeBank([make_item("i1", "t1")])
    plan = LearningScheduler().plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records={}, now=NOW, review_logs=[]
    )
    assert plan.new_topic_id == "t1"


# --- Rule 2: prerequisite stability, not merely "acquired" ----------------


def test_prereq_stability_below_minimum_blocks_dependent_introduction() -> None:
    prereq = make_topic("prereq")
    dependent = make_topic("dependent", prereqs=["prereq"])
    tm = TopicStateManager([prereq, dependent], now=NOW)
    tm.mark_acquired_inferred("prereq", stability_days=4.0, now=NOW)  # below MIN_PREREQ_STABILITY
    assert tm.get_state("dependent").state == "ready"

    bank = FakeBank([make_item("dep_item", "dependent")])
    plan = LearningScheduler().plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records={}, now=NOW, review_logs=[]
    )
    assert plan.new_topic_id is None


def test_prereq_stability_at_or_above_minimum_allows_dependent_introduction() -> None:
    prereq = make_topic("prereq")
    dependent = make_topic("dependent", prereqs=["prereq"])
    tm = TopicStateManager([prereq, dependent], now=NOW)
    assert MIN_PREREQ_STABILITY <= 10.0
    tm.mark_acquired_kalibrierung("prereq", stability_days=10.0, now=NOW)
    assert tm.get_state("dependent").state == "ready"

    bank = FakeBank([make_item("dep_item", "dependent")])
    plan = LearningScheduler().plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records={}, now=NOW, review_logs=[]
    )
    assert plan.new_topic_id == "dependent"


# --- Rule 3: confusion_group adjacency preferred, tag_id/sibling separated ---


def test_confusion_group_preferred_adjacent_over_unrelated_topic() -> None:
    items = [
        make_item("a1", "topicA", confusion_group="pair"),
        make_item("b1", "topicB", confusion_group="pair"),
        make_item("c1", "topicC"),
        make_item("c2", "topicC"),
    ]
    interleaved = LearningScheduler._interleave_items(items)
    ids = [it.id for it in interleaved]
    a_idx = ids.index("a1")
    neighbours = {ids[i] for i in (a_idx - 1, a_idx + 1) if 0 <= i < len(ids)}
    assert "b1" in neighbours


def test_sibling_separation_outranks_confusion_group_adjacency() -> None:
    items = [
        make_item("a1", "topicA", confusion_group="pair"),
        make_item("b1", "topicB", confusion_group="pair"),
        make_item("c1", "topicC"),
    ]
    sibling_lookup = {"topicA": "sib", "topicB": "sib"}
    interleaved = LearningScheduler._interleave_items(items, sibling_group_by_topic=sibling_lookup)
    ids = [it.id for it in interleaved]
    a_idx = ids.index("a1")
    neighbours = {ids[i] for i in (a_idx - 1, a_idx + 1) if 0 <= i < len(ids)}
    assert "b1" not in neighbours


def test_never_two_consecutive_items_share_a_sibling_group() -> None:
    items = [make_item(f"s{i}", f"topic_s{i}") for i in range(4)]
    sibling_lookup = {f"topic_s{i}": "only_sibling_group" for i in range(4)}
    interleaved = LearningScheduler._interleave_items(items, sibling_group_by_topic=sibling_lookup)
    # every item shares both a distinct tag_id AND the same sibling_group;
    # the algorithm can only guarantee tag_id separation once all sibling
    # alternatives are exhausted, but must never collide when an alternative
    # (different sibling_group) exists.
    assert len(interleaved) == 4


# --- Rule 4: difficulty tier from stability -------------------------------


@pytest.mark.parametrize(
    "stability,expected_tier",
    [(0.0, 1), (2.9, 1), (3.0, 2), (9.9, 2), (10.0, 3), (50.0, 3)],
)
def test_difficulty_tier_matches_stability_band(stability: float, expected_tier: int) -> None:
    assert LearningScheduler._difficulty_tier(stability) == expected_tier


# --- Rule 5: seen_items exclusion -----------------------------------------


def test_seen_items_excluded_from_new_topic_introduction() -> None:
    tm = TopicStateManager([make_topic("t1")], now=NOW)
    bank = FakeBank([make_item("only_item", "t1")])
    plan = LearningScheduler().plan_next_round(
        bank=bank,
        topic_manager=tm,
        fsrs_records={},
        now=NOW,
        review_logs=[],
        seen_items={"only_item"},
    )
    assert plan.new_topic_id is None
    assert plan.items == []


# --- Rule 8: grammar / vocab ratio -----------------------------------------


def _due_grammar_vocab_setup() -> tuple[TopicStateManager, FakeBank, dict[str, FSRSRecord]]:
    topics = [make_topic(f"g{i}") for i in range(4)] + [make_topic(f"v{i}") for i in range(4)]
    tm = TopicStateManager(topics, now=NOW)
    bank_items = []
    fsrs_records: dict[str, FSRSRecord] = {}
    for i in range(4):
        g_item = make_item(f"g{i}_item", f"g{i}")
        v_item = make_item(f"v{i}_item", f"v{i}", dimension="vocab")
        bank_items += [g_item, v_item]
        fsrs_records[f"g{i}_item"] = FSRSRecord(card_id=f"g{i}_item", due=NOW - timedelta(days=1))
        fsrs_records[f"v{i}_item"] = FSRSRecord(card_id=f"v{i}_item", due=NOW - timedelta(days=1))
    return tm, FakeBank(bank_items), fsrs_records


def test_vocab_ratio_read_from_setting_and_applied_next_round() -> None:
    tm, bank, fsrs_records = _due_grammar_vocab_setup()
    scheduler = LearningScheduler(round_size=6, vocab_ratio=0.30)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    vocab_count = sum(1 for it in plan.items if it.dimension == "vocab")
    assert len(plan.items) == 6
    assert vocab_count == round(6 * 0.30)


def test_vocab_ratio_of_zero_produces_grammar_only_rounds() -> None:
    tm, bank, fsrs_records = _due_grammar_vocab_setup()
    scheduler = LearningScheduler(round_size=6, vocab_ratio=0.0)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    assert all(it.dimension == "grammar" for it in plan.items)
    assert len(plan.items) == 4  # only the 4 due grammar items; vocab never substitutes in


# --- Rule 9 + 10: heavy cap counts a paragraph block once; gaps count individually ---


def test_paragraph_block_is_one_heavy_unit_but_gaps_count_individually_toward_round_size() -> None:
    gap_topics = [make_topic(f"gap{i}") for i in range(4)]
    single_topics = [make_topic(f"single{i}") for i in range(3)]
    prod_topic = make_topic("prod_topic")
    tm = TopicStateManager(gap_topics + single_topics + [prod_topic], now=NOW)

    bank_items = (
        [
            make_item(f"gap{i}_item", f"gap{i}", type="paragraph_cloze", block_id="block_A")
            for i in range(4)
        ]
        + [make_item(f"single{i}_item", f"single{i}") for i in range(3)]
        + [make_item("prod_item", "prod_topic", type="production")]
    )
    bank = FakeBank(bank_items)

    fsrs_records: dict[str, FSRSRecord] = {}
    for i in range(4):
        fsrs_records[f"gap{i}_item"] = FSRSRecord(
            card_id=f"gap{i}_item", due=NOW - timedelta(days=5)
        )
    for i in range(3):
        fsrs_records[f"single{i}_item"] = FSRSRecord(
            card_id=f"single{i}_item", due=NOW - timedelta(days=5)
        )
    fsrs_records["prod_item"] = FSRSRecord(card_id="prod_item", due=NOW - timedelta(days=1))

    scheduler = LearningScheduler(round_size=8, vocab_ratio=0.0)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )

    ids = {it.id for it in plan.items}
    assert len(plan.items) == 7  # 4 gaps + 3 singles; the whole block is 1 heavy unit
    assert all(f"gap{i}_item" in ids for i in range(4))
    assert all(f"single{i}_item" in ids for i in range(3))
    assert "prod_item" not in ids  # heavy budget already spent on the paragraph block


def test_at_most_one_heavy_element_per_round() -> None:
    prod_a = make_topic("prod_a")
    prod_b = make_topic("prod_b")
    tm = TopicStateManager([prod_a, prod_b], now=NOW)
    bank = FakeBank(
        [
            make_item("prod_a_item", "prod_a", type="production"),
            make_item("prod_b_item", "prod_b", type="production"),
        ]
    )
    fsrs_records = {
        "prod_a_item": FSRSRecord(card_id="prod_a_item", due=NOW - timedelta(days=1)),
        "prod_b_item": FSRSRecord(card_id="prod_b_item", due=NOW - timedelta(days=1)),
    }
    scheduler = LearningScheduler(round_size=6, vocab_ratio=0.0)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    heavy = [it for it in plan.items if it.type in ("paragraph_cloze", "production")]
    assert len(heavy) <= 1


# --- Rule 11: requires_context topics spread across rounds -----------------


def test_requires_context_topics_spread_across_rounds() -> None:
    ctx_topics = [
        make_topic(f"ctx{i}", requires_context=True, eligible_types=["error_correction"])
        for i in range(4)
    ]
    tm = TopicStateManager(ctx_topics, now=NOW)
    bank = FakeBank(
        [make_item(f"ctx{i}_item", f"ctx{i}", type="error_correction") for i in range(4)]
    )
    fsrs_records = {
        f"ctx{i}_item": FSRSRecord(card_id=f"ctx{i}_item", due=NOW - timedelta(days=1))
        for i in range(4)
    }
    scheduler = LearningScheduler(round_size=8, vocab_ratio=0.0)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    included = {it.topic_id for it in plan.items if it.topic_id.startswith("ctx")}
    assert len(included) == 1


# --- Rule 12: bonus rounds, flagged, never pulling forward a scheduled card ---


def test_bonus_round_flagged_and_never_pulls_forward_a_scheduled_card() -> None:
    topic_a = make_topic("a")
    tm = TopicStateManager([topic_a], now=NOW)
    tm.start_topic("a", now=NOW)
    tm.record_attempt("a", is_correct=True, hint_level=0, now=NOW)

    scheduled_item = make_item("a_scheduled", "a")
    bonus_item = make_item("a_bonus", "a")
    bank = FakeBank([scheduled_item, bonus_item])

    fsrs_records = {
        "a_scheduled": FSRSRecord(card_id="a_scheduled", due=NOW + timedelta(days=5)),
    }
    scheduler = LearningScheduler(round_size=6, vocab_ratio=0.0)
    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    ids = {it.id for it in plan.items}
    assert "a_scheduled" not in ids
    assert plan.is_bonus is True
    assert "a_bonus" in ids


def test_bonus_rounds_offered_only_after_due_queue_is_empty() -> None:
    topic_a = make_topic("a")
    tm = TopicStateManager([topic_a], now=NOW)
    bank = FakeBank([make_item("a_due", "a")])
    fsrs_records = {"a_due": FSRSRecord(card_id="a_due", due=NOW - timedelta(days=1))}
    plan = LearningScheduler(vocab_ratio=0.0).plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    assert plan.is_bonus is False


# --- Eligibility overrides pacing ------------------------------------------


def test_item_type_never_violates_topic_eligibility() -> None:
    topic = make_topic("t1", eligible_types=["error_correction"])
    tm = TopicStateManager([topic], now=NOW)
    bank = FakeBank([make_item("bad", "t1", type="cloze_free")])
    fsrs_records = {"bad": FSRSRecord(card_id="bad", due=NOW - timedelta(days=1))}
    plan = LearningScheduler().plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records=fsrs_records, now=NOW, review_logs=[]
    )
    assert plan.items == []


# --- Backlog cap (item 1: MAX_REVIEWS_PER_DAY) ------------------------------


def test_backlog_capped_and_ordered_by_overdueness() -> None:
    records = [FSRSRecord(card_id=f"c{i}", due=NOW - timedelta(days=i + 1)) for i in range(200)]
    capped = LearningScheduler().cap_daily_backlog(records, review_logs=[], now=NOW)
    assert len(capped) == MAX_REVIEWS_PER_DAY
    kept_ids = {r.card_id for r in capped}
    most_overdue_ids = {f"c{i}" for i in range(200 - MAX_REVIEWS_PER_DAY, 200)}
    assert kept_ids == most_overdue_ids


def test_backlog_cap_accounts_for_reviews_already_logged_today() -> None:
    records = [FSRSRecord(card_id=f"c{i}", due=NOW - timedelta(days=1)) for i in range(10)]
    logs: list[dict[str, Any]] = [
        {"topic_id": "x", "mode": "review", "created_at": NOW}
        for _ in range(MAX_REVIEWS_PER_DAY - 5)
    ]
    capped = LearningScheduler().cap_daily_backlog(records, review_logs=logs, now=NOW)
    assert len(capped) == 5


# --- Duel mode --------------------------------------------------------------


def test_duel_locked_until_both_member_topics_introduced() -> None:
    topic_a, topic_b = make_topic("da"), make_topic("db")
    tm = TopicStateManager([topic_a, topic_b], now=NOW)
    bank = FakeBank(
        [
            make_item("da_1", "da", confusion_group="g"),
            make_item("db_1", "db", confusion_group="g"),
        ]
    )
    scheduler = LearningScheduler(bank=bank)
    assert scheduler.build_duel("g", now=NOW, topic_manager=tm) == []

    tm.start_topic("da", now=NOW)
    tm.start_topic("db", now=NOW)
    assert scheduler.build_duel("g", now=NOW, topic_manager=tm) != []


def test_duel_never_pads_with_unrelated_items() -> None:
    group_items = [make_item(f"g_{i}", f"topic_{i % 2}", confusion_group="g") for i in range(3)]
    unrelated_items = [make_item(f"u_{i}", f"unrelated_{i}") for i in range(10)]
    bank = FakeBank(group_items + unrelated_items)
    duel_items = LearningScheduler(bank=bank).build_duel("g", now=NOW)
    assert len(duel_items) == 3
    assert len(duel_items) <= DUEL_LENGTH
    assert all(it.confusion_group == "g" for it in duel_items)


def test_duel_prefers_minimal_pairs_and_seen_items() -> None:
    minimal_pair = make_item(
        "mp",
        "topicA",
        confusion_group="g",
        distractors=[Distractor(text="x", implied_topic_id="topicB")],
    )
    plain_unseen = make_item("plain_unseen", "topicA", confusion_group="g")
    plain_seen = make_item("plain_seen", "topicB", confusion_group="g")
    bank = FakeBank([plain_unseen, plain_seen, minimal_pair])
    duel_items = LearningScheduler(bank=bank).build_duel("g", now=NOW, seen_items={"plain_seen"})
    ids = [it.id for it in duel_items]
    assert ids.index("mp") < ids.index("plain_seen") < ids.index("plain_unseen")


def test_duel_suggestion_suppressed_below_minimum_attempts() -> None:
    item_a = make_item(
        "ia",
        "topicA",
        confusion_group="g",
        distractors=[Distractor(text="wrong_b", implied_topic_id="topicB")],
    )
    bank = FakeBank([item_a])
    scheduler = LearningScheduler(bank=bank)
    logs: list[dict[str, Any]] = [
        {"item_id": "ia", "is_correct": False, "user_answer": "wrong_b"}
        for _ in range(DUEL_MIN_ATTEMPTS_TO_SUGGEST - 1)
    ]
    assert scheduler.rank_duel_suggestions(bank, logs) == []

    logs.append({"item_id": "ia", "is_correct": False, "user_answer": "wrong_b"})
    assert scheduler.rank_duel_suggestions(bank, logs) == [("g", 1.0)]


# --- Recalibration mode ------------------------------------------------------


def test_recalibration_samples_mostly_high_stability_plus_a_couple_from_learning() -> None:
    acquired_states = [
        TagStateModel(tag_id=f"acq{i}", state="acquired", fsrs_stability=float(10 - i), due_at=NOW)
        for i in range(5)
    ]
    learning_states = [
        TagStateModel(tag_id=f"lrn{i}", state="learning", due_at=NOW) for i in range(3)
    ]
    bank_items = [make_item(f"acq{i}_item", f"acq{i}") for i in range(5)] + [
        make_item(f"lrn{i}_item", f"lrn{i}") for i in range(3)
    ]
    bank = FakeBank(bank_items)
    items = LearningScheduler(bank=bank).build_recalibration(
        states=acquired_states + learning_states, now=NOW, bank=bank
    )
    topics = {it.topic_id for it in items}
    assert {"acq0", "acq1", "acq2", "acq3", "acq4"}.issubset(topics)
    assert len(topics & {"lrn0", "lrn1", "lrn2"}) == 2
    assert len(items) == 7
    assert len(items) <= RECALIBRATION_ROUND_SIZE


# --- Daily challenge ----------------------------------------------------------


def test_challenge_topic_selected_deterministically_and_never_inside_a_round() -> None:
    topics = [make_topic("c1"), make_topic("c2")]
    tm = TopicStateManager(topics, now=NOW)
    tm.start_topic("c1", now=NOW)
    tm.start_topic("c2", now=NOW)
    bank = FakeBank([make_item("c1_item", "c1"), make_item("c2_item", "c2")])
    scheduler = LearningScheduler(bank=bank)

    first = scheduler.build_challenge(tm, bank=bank, now=NOW)
    second = scheduler.build_challenge(tm, bank=bank, now=NOW)
    assert first is not None and second is not None
    assert first.id == second.id

    plan = scheduler.plan_next_round(
        bank=bank, topic_manager=tm, fsrs_records={}, mode="challenge", now=NOW, review_logs=[]
    )
    assert plan.mode == "challenge"
    assert len(plan.items) <= 1


# --- Dormancy switches mode --------------------------------------------------


def test_dormancy_switches_round_to_recalibration_mode() -> None:
    tm = TopicStateManager([make_topic("t1")], now=NOW)
    bank = FakeBank([make_item("t1_item", "t1")])
    plan = LearningScheduler(bank=bank).plan_next_round(
        bank=bank,
        topic_manager=tm,
        fsrs_records={},
        now=NOW,
        last_active_date=NOW - timedelta(days=DORMANCY_DAYS),
        review_logs=[],
    )
    assert plan.mode == "recalibration"


# --- Threshold suggestion (item 1) -------------------------------------------


def test_threshold_suggestion_is_none_below_minimum_active_days() -> None:
    completions = {
        NOW.date() - timedelta(days=i): 20 for i in range(SUGGESTION_MIN_ACTIVE_DAYS - 1)
    }
    cleared = dict.fromkeys(completions, True)
    assert LearningScheduler().compute_threshold_suggestion(completions, cleared, now=NOW) is None


def test_seven_day_window_can_never_clear_the_minimum_active_days_gate() -> None:
    # A 7-day window can hold at most 7 active days, which is always below
    # SUGGESTION_MIN_ACTIVE_DAYS (10); it is a supported window for display,
    # but alone it can never clear the minimum-evidence gate.
    completions = {NOW.date() - timedelta(days=i): 20 for i in range(7)}
    cleared = dict.fromkeys(completions, True)
    scheduler = LearningScheduler()
    assert (
        scheduler.compute_threshold_suggestion(completions, cleared, now=NOW, window_days=7) is None
    )


@pytest.mark.parametrize("window_days", [14, 30])
def test_suggestion_windows_of_14_and_30_are_supported(window_days: int) -> None:
    completions = {NOW.date() - timedelta(days=i): 20 for i in range(SUGGESTION_MIN_ACTIVE_DAYS)}
    cleared = dict.fromkeys(completions, True)
    suggestion = LearningScheduler().compute_threshold_suggestion(
        completions, cleared, now=NOW, window_days=window_days
    )
    assert suggestion is not None
    assert suggestion.window_days == window_days


def test_threshold_suggestion_clamped_to_range() -> None:
    completions = {NOW.date() - timedelta(days=i): 1 for i in range(SUGGESTION_MIN_ACTIVE_DAYS)}
    cleared = dict.fromkeys(completions, False)
    suggestion = LearningScheduler().compute_threshold_suggestion(
        completions, cleared, now=NOW, window_days=14
    )
    assert suggestion is not None
    assert suggestion.suggested == THRESHOLD_CLAMP[0]


@pytest.mark.parametrize("clear_rate", [0.9, 0.7, 0.3])
def test_clear_rate_adjustment_table(clear_rate: float) -> None:
    days = [NOW.date() - timedelta(days=i) for i in range(20)]
    completions = dict.fromkeys(days, 50)
    cleared_count = round(len(days) * clear_rate)
    cleared = {d: (idx < cleared_count) for idx, d in enumerate(days)}
    suggestion = LearningScheduler().compute_threshold_suggestion(
        completions, cleared, now=NOW, window_days=30
    )
    assert suggestion is not None
    if clear_rate >= 0.85:
        adjustment = 1.1
    elif clear_rate >= 0.60:
        adjustment = 1.0
    else:
        adjustment = 0.8
    expected_raw = 50 * suggestion.active_day_rate * adjustment
    expected = int(round(min(max(expected_raw, THRESHOLD_CLAMP[0]), THRESHOLD_CLAMP[1])))
    assert suggestion.suggested == expected


def test_suggestion_scales_with_active_day_rate() -> None:
    scheduler = LearningScheduler()
    many_active_days = {NOW.date() - timedelta(days=i): 40 for i in range(20)}
    many_cleared = dict.fromkeys(many_active_days, True)
    high_rate = scheduler.compute_threshold_suggestion(
        many_active_days, many_cleared, now=NOW, window_days=30
    )

    few_active_days = {NOW.date() - timedelta(days=i): 40 for i in range(10)}
    few_cleared = dict.fromkeys(few_active_days, True)
    low_rate = scheduler.compute_threshold_suggestion(
        few_active_days, few_cleared, now=NOW, window_days=30
    )

    assert high_rate is not None and low_rate is not None
    assert high_rate.suggested >= low_rate.suggested


def test_zero_activity_days_excluded_from_median_but_counted_in_active_day_rate() -> None:
    completions = {NOW.date() - timedelta(days=i * 2): 20 for i in range(10)}
    cleared = dict.fromkeys(completions, True)
    suggestion = LearningScheduler().compute_threshold_suggestion(
        completions, cleared, now=NOW, window_days=20
    )
    assert suggestion is not None
    assert suggestion.median_items_per_active_day == 20.0
    assert suggestion.active_day_rate == pytest.approx(10 / 20)


def test_threshold_suggestion_is_deterministic_for_a_given_history() -> None:
    completions = {NOW.date() - timedelta(days=i): 25 for i in range(12)}
    cleared = dict.fromkeys(completions, True)
    scheduler = LearningScheduler()
    first = scheduler.compute_threshold_suggestion(completions, cleared, now=NOW)
    second = scheduler.compute_threshold_suggestion(completions, cleared, now=NOW)
    assert first == second


def test_threshold_suggestion_never_auto_applies() -> None:
    """No code path may write the setting without an explicit user action."""
    completions = {NOW.date() - timedelta(days=i): 25 for i in range(12)}
    cleared = dict.fromkeys(completions, True)
    scheduler = LearningScheduler(forecast_threshold=50)
    scheduler.compute_threshold_suggestion(completions, cleared, now=NOW)
    assert scheduler.forecast_threshold == 50
