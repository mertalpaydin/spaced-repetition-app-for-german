"""Unit tests for the CLI session, diagnostic calibration, and command dispatchers."""

from pathlib import Path

import pytest
from src.bank.storage import SqliteItemBank
from src.cli.app import cmd_kalibrierung, cmd_report, cmd_round, cmd_stats, run_cli
from src.cli.calibration import CalibrationRunner
from src.cli.reports import ReportStore
from src.cli.session import InteractiveSession
from src.contracts import Answer, BankItem, Distractor, Topic
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy
from src.engine.scheduler import RoundPlan
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


@pytest.fixture
def taxonomy_topics() -> list[Topic]:
    return load_taxonomy()


@pytest.fixture
def topic_manager(taxonomy_topics: list[Topic]) -> TopicStateManager:
    return TopicStateManager(taxonomy_topics)


@pytest.fixture
def sample_items() -> list[BankItem]:
    return [
        BankItem(
            id="item_a1",
            topic_id="pronomen_personal_nom",
            type="cloze_free",
            difficulty=1,
            cefr="A1",
            prompt="___ heiße Max.",
            accepted_answers=["Ich", "ich"],
            distractors=[Distractor(text="Du"), Distractor(text="Er"), Distractor(text="Wir")],
        ),
        BankItem(
            id="item_a2",
            topic_id="dativ_nach_praeposition",
            type="cloze_free",
            difficulty=1,
            cefr="A2",
            prompt="Das Buch liegt auf ___ Tisch.",
            accepted_answers=["dem"],
            distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
        ),
    ]


def _bank_item(topic_id: str, idx: int, answer: str = "x") -> BankItem:
    return BankItem(
        id=f"{topic_id}_probe_{idx}",
        topic_id=topic_id,
        type="cloze_free",
        difficulty=1,
        cefr="A1",
        prompt=f"Probe für {topic_id} #{idx}",
        accepted_answers=[answer],
    )


def test_kalibrierung_seeds_all_four_fsrs_rows() -> None:
    """The four-row FSRS seeding table: 2/2 -> acquired/kalibrierung, 1/2 -> learning,
    0/2 -> unseen, and DAG-inferred (never directly tested) -> acquired/inferred."""
    prereq = Topic(id="prereq_topic", name_de="Prereq", cefr="A1", description="d", prereqs=[])
    parent = Topic(
        id="parent_topic", name_de="Parent", cefr="B1", description="d", prereqs=["prereq_topic"]
    )
    partial = Topic(id="partial_topic", name_de="Partial", cefr="B1", description="d", prereqs=[])
    failed = Topic(id="failed_topic", name_de="Failed", cefr="B1", description="d", prereqs=[])
    topics = [prereq, parent, partial, failed]
    topic_mgr = TopicStateManager(topics)

    items = [
        _bank_item("parent_topic", 0),
        _bank_item("parent_topic", 1),
        _bank_item("partial_topic", 0),
        _bank_item("partial_topic", 1),
        _bank_item("failed_topic", 0),
        _bank_item("failed_topic", 1),
    ]
    runner = CalibrationRunner(items=items, topic_manager=topic_mgr)

    answered = [
        Answer(
            item_id="parent_topic_probe_0",
            topic_id="parent_topic",
            user_answer="x",
            is_correct=True,
        ),
        Answer(
            item_id="parent_topic_probe_1",
            topic_id="parent_topic",
            user_answer="x",
            is_correct=True,
        ),
        Answer(
            item_id="partial_topic_probe_0",
            topic_id="partial_topic",
            user_answer="x",
            is_correct=True,
        ),
        Answer(
            item_id="partial_topic_probe_1",
            topic_id="partial_topic",
            user_answer="y",
            is_correct=False,
        ),
        Answer(
            item_id="failed_topic_probe_0",
            topic_id="failed_topic",
            user_answer="y",
            is_correct=False,
        ),
        Answer(
            item_id="failed_topic_probe_1",
            topic_id="failed_topic",
            user_answer="y",
            is_correct=False,
        ),
    ]

    report = runner.finalise(answered)

    parent_state = topic_mgr.get_state("parent_topic")
    assert parent_state.state == "acquired"
    assert parent_state.acquired_via == "kalibrierung"
    assert parent_state.fsrs_stability == pytest.approx(10.0)

    prereq_state = topic_mgr.get_state("prereq_topic")
    assert prereq_state.state == "acquired"
    assert prereq_state.acquired_via == "inferred"
    assert prereq_state.fsrs_stability <= 4.0

    partial_state = topic_mgr.get_state("partial_topic")
    assert partial_state.state == "learning"
    assert partial_state.acquired_via is None
    assert partial_state.fsrs_stability == pytest.approx(7.0)

    failed_state = topic_mgr.get_state("failed_topic")
    assert failed_state.state == "unseen"
    assert failed_state.acquired_via is None
    assert failed_state.fsrs_stability == 0.0

    assert "parent_topic" in report.acquired_topics
    assert "prereq_topic" in report.acquired_topics
    assert "partial_topic" in report.learning_topics
    assert "prereq_topic" not in report.directly_tested_topics


def test_kalibrierung_terminates_within_35_items_for_all_ability_profiles(
    taxonomy_topics: list[Topic],
) -> None:
    """CLAUDE.md:169 names this test explicitly. The adaptive walk must halt at or
    before 35 items regardless of whether the simulated learner always passes,
    always fails, or answers inconsistently."""
    items = [_bank_item(t.id, i) for t in taxonomy_topics for i in range(3)]

    def profile_answer(profile: str, count_so_far: int) -> bool:
        if profile == "always_pass":
            return True
        if profile == "always_fail":
            return False
        return count_so_far % 2 == 0  # mixed

    for profile in ("always_pass", "always_fail", "mixed"):
        topic_mgr = TopicStateManager(taxonomy_topics)
        runner = CalibrationRunner(items=items, topic_manager=topic_mgr)
        answered: list[Answer] = []
        guard = 0
        while guard < 200:
            guard += 1
            item = runner.next_item(answered)
            if item is None:
                break
            correct = profile_answer(profile, len(answered))
            answered.append(
                Answer(
                    item_id=item.id,
                    topic_id=item.topic_id,
                    user_answer="x" if correct else "y",
                    is_correct=correct,
                )
            )
            assert len(answered) <= 35, f"{profile} profile exceeded the 35-item bound"

        assert len(answered) <= 35
        report = runner.finalise(answered)
        assert report.total_items == len(answered)


def test_kalibrierung_grades_with_scoped_typo_grader_not_bare_equality() -> None:
    """A scoped typo (edit distance 1 outside the tested morpheme) must pass during
    Kalibrierung exactly as it does during a normal review round."""
    topic = Topic(id="typo_topic", name_de="Typo Topic", cefr="B1", description="d", prereqs=[])
    topic_mgr = TopicStateManager([topic])
    items = [
        BankItem(
            id="typo_topic_probe_0",
            topic_id="typo_topic",
            type="cloze_free",
            difficulty=1,
            cefr="B1",
            prompt="Er legt das Buch auf dem Tisch ab.",
            accepted_answers=["dem Mann"],
        ),
        BankItem(
            id="typo_topic_probe_1",
            topic_id="typo_topic",
            type="cloze_free",
            difficulty=1,
            cefr="B1",
            prompt="Sie gibt dem Mann das Buch.",
            accepted_answers=["dem Mann"],
        ),
    ]
    runner = CalibrationRunner(items=items, topic_manager=topic_mgr)

    from src.engine.typo_grader import ScopedTypoGrader

    answered: list[Answer] = []
    for _ in range(2):
        item = runner.next_item(answered)
        assert item is not None
        given = "dem Mnn"  # typo outside the tested morpheme ("dem")
        grade = ScopedTypoGrader.grade(given, item.accepted_answers, item.topic_id)
        answered.append(
            Answer(
                item_id=item.id,
                topic_id=item.topic_id,
                user_answer=given,
                is_correct=grade.is_correct,
            )
        )

    assert all(a.is_correct for a in answered), "scoped typo must be graded correct"
    report = runner.finalise(answered)
    assert "typo_topic" in report.acquired_topics
    assert topic_mgr.get_state("typo_topic").acquired_via == "kalibrierung"


def test_interactive_session_lifecycle(
    topic_manager: TopicStateManager, sample_items: list[BankItem]
) -> None:
    """Test session attempt processing, hint degradation, and summary generation."""
    fsrs_engine = FSRSEngine()
    fsrs_records: dict[str, FSRSRecord] = {}

    plan = RoundPlan(
        items=sample_items,
        mode="review",
    )

    session = InteractiveSession(
        round_plan=plan,
        topic_manager=topic_manager,
        fsrs_engine=fsrs_engine,
        fsrs_records=fsrs_records,
    )

    # Attempt 1: unhinted correct
    att1 = session.process_item_attempt(
        item=sample_items[0],
        user_answer="Ich",
        hint_level=0,
    )
    assert att1.is_correct is True
    assert att1.is_unhinted_pass is True
    assert att1.fsrs_rating == "good"

    # Check FSRS record was created
    assert "item_a1" in fsrs_records
    assert fsrs_records["item_a1"].reps == 1

    # Attempt 2: hinted correct (hint level 2: options)
    att2 = session.process_item_attempt(
        item=sample_items[1],
        user_answer="dem",
        hint_level=2,
    )
    assert att2.is_correct is True
    assert att2.is_unhinted_pass is False
    assert att2.fsrs_rating == "hard"

    summary = session.get_summary()
    assert summary.total_items == 2
    assert summary.correct_items == 2
    assert summary.accuracy == 1.0
    assert summary.unhinted_passes == 1


@pytest.fixture
def empty_bank(tmp_path: Path) -> SqliteItemBank:
    return SqliteItemBank(tmp_path / "cli_empty_bank.db")


def test_cmd_kalibrierung_empty_response_is_incorrect_and_promotes_nothing(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Four empty responses must never be substituted with the reference answer."""
    cmd_kalibrierung(
        empty_bank,
        topic_manager,
        input_fn=lambda _prompt: "",
        interactive=True,
    )

    out = capsys.readouterr().out
    assert "Topics Mastered: 0" in out

    state = topic_manager.get_state("pronomen_personal_nom")
    assert state.state != "acquired"
    assert state.acquired_via is None


def test_cmd_kalibrierung_whitespace_response_is_incorrect(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whitespace-only input is graded incorrect, not silently substituted."""
    cmd_kalibrierung(
        empty_bank,
        topic_manager,
        input_fn=lambda _prompt: "   ",
        interactive=True,
    )

    out = capsys.readouterr().out
    assert "Topics Mastered: 0" in out
    assert topic_manager.get_state("pronomen_personal_nom").state != "acquired"


def test_cmd_kalibrierung_non_interactive_skips_items_without_recording(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-interactive session must not fabricate or record learner attempts."""

    def _fail(_prompt: str) -> str:
        raise AssertionError("input_fn must not be called in non-interactive mode")

    cmd_kalibrierung(empty_bank, topic_manager, input_fn=_fail, interactive=False)

    out = capsys.readouterr().out
    assert "Topics Mastered: 0" in out
    assert topic_manager.get_state("pronomen_personal_nom").state == "ready"


def test_cmd_round_empty_response_recorded_as_incorrect_no_promotion(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    sample_items: list[BankItem],
) -> None:
    """An empty answer in a round must be logged as an incorrect, real attempt."""
    empty_bank.insert_item(sample_items[0])  # topic_id="pronomen_personal_nom", "ready" state

    cmd_round(
        empty_bank,
        topic_manager,
        round_size=1,
        input_fn=lambda _prompt: "",
        interactive=True,
    )

    logs = empty_bank.get_review_logs(topic_id="pronomen_personal_nom")
    assert len(logs) == 1
    assert logs[0]["is_correct"] == 0
    assert logs[0]["user_answer"] == ""
    assert logs[0]["mode"] == "review"
    assert logs[0]["facet"] is None

    assert topic_manager.get_state("pronomen_personal_nom").state != "acquired"


def test_cmd_round_non_interactive_skips_items_without_recording(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    sample_items: list[BankItem],
) -> None:
    """A non-interactive round must not auto-answer, grade, or log anything."""
    empty_bank.insert_item(sample_items[0])

    def _fail(_prompt: str) -> str:
        raise AssertionError("input_fn must not be called in non-interactive mode")

    cmd_round(empty_bank, topic_manager, round_size=1, input_fn=_fail, interactive=False)

    assert empty_bank.get_review_logs() == []
    # The scheduler may introduce the topic (ready -> learning) as part of
    # planning the round, but a skipped, non-interactive item must never
    # promote it all the way to "acquired".
    assert topic_manager.get_state("pronomen_personal_nom").state != "acquired"


def test_cli_subcommands(tmp_path: Path) -> None:
    """Test CLI dispatcher commands (stats, topics, export, help)."""
    assert run_cli([]) == 0
    assert run_cli(["stats"]) == 0
    assert run_cli(["topics"]) == 0

    export_out = str(tmp_path / "cli_export")
    assert run_cli(["export", "--out", export_out]) == 0


# --- cmd_stats: topic states, stability, retention (never round accuracy as headline) ---


def test_stats_never_shows_round_accuracy_as_headline(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Interleaving depresses round accuracy by design; leading with it is a quit
    trigger (docs/plan). Assert stability and retention are the primary output and
    no "round accuracy" style metric is ever printed."""
    cmd_stats(empty_bank, topic_manager)
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]

    assert "accuracy" not in lines[0].lower()
    assert "round accuracy" not in out.lower()
    assert "Average Stability" in out
    assert "Estimated Retention" in out
    assert out.index("Average Stability") < out.index("(Item bank")


def test_stats_reports_topic_acquisition_counts_by_state(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """cmd_stats must report topic states, not item-bank composition, as the headline."""
    topic_manager.mark_acquired_kalibrierung("pronomen_personal_nom", stability_days=10.0)

    cmd_stats(empty_bank, topic_manager)
    out = capsys.readouterr().out

    assert "Topics Acquired: 1" in out
    assert "10.0 days" in out


# --- cmd_report: persists the report and excludes the item from future rounds ---


def test_report_command_marks_item_and_excludes_it(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    sample_items: list[BankItem],
) -> None:
    empty_bank.insert_item(sample_items[0])
    empty_bank.insert_item(sample_items[1])

    cmd_report(empty_bank, sample_items[0].id)

    store = ReportStore.for_bank(empty_bank)
    assert sample_items[0].id in store.reported_item_ids()

    responses = iter(["dem"])
    cmd_round(
        empty_bank,
        topic_manager,
        round_size=2,
        input_fn=lambda _prompt: next(responses, ""),
        interactive=True,
    )

    logged_item_ids = {row["item_id"] for row in empty_bank.get_review_logs()}
    assert sample_items[0].id not in logged_item_ids


def test_report_unknown_item_id_does_not_persist_a_report(
    empty_bank: SqliteItemBank,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cmd_report(empty_bank, "no_such_item")
    out = capsys.readouterr().out
    assert "not found" in out
    store = ReportStore.for_bank(empty_bank)
    assert store.reported_item_ids() == set()


# --- Hint ladder ---


def test_hint_policy_level_1_returns_length_and_first_letter(
    sample_items: list[BankItem],
) -> None:
    hint = HintPolicy.get_hint(sample_items[0], 1)
    target = sample_items[0].accepted_answers[0]
    assert str(len(target)) in hint
    assert target[0] in hint


def test_hint_policy_level_2_offers_the_answer_among_distractor_options(
    sample_items: list[BankItem],
) -> None:
    hint = HintPolicy.get_hint(sample_items[1], 2)
    assert sample_items[1].accepted_answers[0] in hint
    for d in sample_items[1].distractors:
        assert d.text in hint


def test_hint_policy_level_3_returns_the_topic_rule_hint() -> None:
    item = BankItem(
        id="rule_item",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="p",
        accepted_answers=["dem"],
        rule_hint="Wechselpräposition + Dativ bei Lage.",
    )
    assert HintPolicy.get_hint(item, 3) == "Wechselpräposition + Dativ bei Lage."


def test_hint_policy_level_4_reveals_the_accepted_answer(sample_items: list[BankItem]) -> None:
    hint = HintPolicy.get_hint(sample_items[0], 4)
    assert sample_items[0].accepted_answers[0] in hint


def test_cmd_round_hint_command_steps_ladder_before_grading_final_answer(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    sample_items: list[BankItem],
) -> None:
    """Typing 'h' twice climbs two hint levels; the following real answer is graded
    at that hint level rather than being treated as another hint request."""
    empty_bank.insert_item(sample_items[0])
    responses = iter(["h", "h", "Ich"])

    cmd_round(
        empty_bank,
        topic_manager,
        round_size=1,
        input_fn=lambda _prompt: next(responses),
        interactive=True,
    )

    logs = empty_bank.get_review_logs(topic_id="pronomen_personal_nom")
    assert len(logs) == 1
    assert logs[0]["hint_level"] == 2
    assert logs[0]["is_correct"] == 1
    assert logs[0]["user_answer"] == "Ich"


def test_cmd_round_hint_ladder_caps_at_level_4(
    empty_bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    sample_items: list[BankItem],
) -> None:
    """A 5th 'h' past level 4 is treated as the literal (wrong) answer, so the loop
    always terminates instead of hanging on a learner who keeps hitting hint."""
    empty_bank.insert_item(sample_items[0])
    responses = iter(["h", "h", "h", "h", "h"])

    cmd_round(
        empty_bank,
        topic_manager,
        round_size=1,
        input_fn=lambda _prompt: next(responses),
        interactive=True,
    )

    logs = empty_bank.get_review_logs(topic_id="pronomen_personal_nom")
    assert len(logs) == 1
    assert logs[0]["hint_level"] == 4
    assert logs[0]["user_answer"] == "h"
    assert logs[0]["is_correct"] == 0
