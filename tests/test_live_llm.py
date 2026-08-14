"""Unit tests for live LLM explanations, 3D grader, minimal pairs, and reports."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.contracts import BankItem, Distractor
from src.llm.cache import LlmCache
from src.llm.live_explainer import LiveExplainer
from src.llm.minimal_pairs import MinimalPairGenerator
from src.llm.production_grader import ProductionGrader
from src.llm.provider import LlmProvider, MockLlmClient
from src.llm.weekly_report import WeeklyReportGenerator, WeeklyReportTrigger


@pytest.fixture
def sample_item() -> BankItem:
    return BankItem(
        id="item_expl_01",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Das Buch liegt auf ___ Tisch.",
        accepted_answers=["dem"],
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
        rule_hint="Wechselpräposition auf + Dativ bei Wo? (Lage).",
    )


def test_live_explainer_generates_explanation(sample_item: BankItem, tmp_path: Path) -> None:
    """Test generating a targeted grammar explanation for an incorrect answer."""
    cache = LlmCache(cache_dir=tmp_path / "cache")
    explainer = LiveExplainer(provider=MockLlmClient(), cache=cache)
    result = explainer.explain_mistake(sample_item, user_answer="den")

    assert result.item_id == sample_item.id
    assert result.topic_id == "dativ_nach_praeposition"
    assert result.user_answer == "den"
    assert result.correct_answer == "dem"
    assert len(result.explanation) > 10
    assert result.rule_summary == sample_item.rule_hint
    assert result.cache_hit is False


def test_explanation_cache_keyed_on_item_and_answer_not_item_alone(
    sample_item: BankItem, tmp_path: Path
) -> None:
    """The cache key is (item_id, user_answer): two different wrong answers to the
    same item must never share a cached explanation, and the same (item, answer)
    pair must hit on the second call."""
    calls: list[str] = []

    class _RecordingProvider:
        def generate_text(
            self,
            prompt: str,
            system_prompt: str | None = None,
            *,
            purpose: str = "generation",
            is_user_content: bool = False,
        ) -> str:
            calls.append(prompt)
            return f"Erklärung für Antwort in Aufruf {len(calls)}."

    cache = LlmCache(cache_dir=tmp_path / "cache")
    explainer = LiveExplainer(provider=_RecordingProvider(), cache=cache)

    first = explainer.explain_mistake(sample_item, user_answer="den")
    assert first.cache_hit is False
    assert len(calls) == 1

    # Same item, same wrong answer -> cache hit, no second provider call.
    repeat = explainer.explain_mistake(sample_item, user_answer="den")
    assert repeat.cache_hit is True
    assert len(calls) == 1
    assert repeat.explanation == first.explanation

    # Same item, a *different* wrong answer -> must NOT reuse the cached entry.
    different_answer = explainer.explain_mistake(sample_item, user_answer="des")
    assert different_answer.cache_hit is False
    assert len(calls) == 2
    assert different_answer.explanation != first.explanation


def test_cache_hit_rate_measured_and_logged(
    sample_item: BankItem, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The hit rate is tracked on the explainer and logged, so it is measurable."""
    provider: LlmProvider = MockLlmClient(canned_response="Erklärung.")
    cache = LlmCache(cache_dir=tmp_path / "cache")
    explainer = LiveExplainer(provider=provider, cache=cache)

    assert explainer.cache_hit_rate == 0.0

    with caplog.at_level("INFO"):
        explainer.explain_mistake(sample_item, user_answer="den")  # miss
        explainer.explain_mistake(sample_item, user_answer="den")  # hit

    assert explainer.cache_hits == 1
    assert explainer.cache_misses == 1
    assert explainer.cache_hit_rate == pytest.approx(0.5)
    assert any("cache" in record.message.lower() for record in caplog.records)


def test_production_grader_evaluates_3d_rubric() -> None:
    """Test grading an open-ended production sentence across 3 dimensions."""
    grader = ProductionGrader(provider=MockLlmClient())
    result = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Kausalsatz mit 'weil'.",
        student_submission="Ich lerne Deutsch, weil ich in Berlin studieren möchte.",
    )

    assert result.target_structure_used is True
    assert result.grammatical_accuracy == 1.0
    assert result.naturalness == 1.0
    assert result.is_pass is True


def test_target_structure_detection_independent_of_overall_correctness() -> None:
    """A sentence that is grammatical (high accuracy) but avoids the target structure
    must be flagged (is_pass is False), not passed on the strength of accuracy alone."""
    canned = (
        '{"target_structure_used": false, "grammatical_accuracy": 1.0, '
        '"naturalness": 1.0, "feedback": "Grammatisch einwandfrei, aber kein Konjunktiv II."}'
    )
    grader = ProductionGrader(provider=MockLlmClient(canned_response=canned))
    result = grader.grade_production(
        target_topic_id="konjunktiv_ii_irreal_gegenwart",
        cefr="B1",
        prompt_instruction="Formuliere einen irrealen Wunsch im Konjunktiv II.",
        student_submission="Ich habe viel Geld.",
    )

    assert result.target_structure_used is False
    assert result.grammatical_accuracy == 1.0
    assert result.is_pass is False


def test_only_target_structure_dimension_reaches_fsrs() -> None:
    """A response that correctly uses Konjunktiv II but with wrong word order must
    still rate the tag as a pass: accuracy and naturalness are feedback only."""
    canned = (
        '{"target_structure_used": true, "grammatical_accuracy": 0.2, '
        '"naturalness": 0.3, "feedback": "Zielstruktur korrekt, aber Wortstellung falsch."}'
    )
    grader = ProductionGrader(provider=MockLlmClient(canned_response=canned))
    result = grader.grade_production(
        target_topic_id="konjunktiv_ii_irreal_gegenwart",
        cefr="B1",
        prompt_instruction="Formuliere einen irrealen Wunsch im Konjunktiv II.",
        student_submission="Wenn ich hätte viel Geld, ich würde reisen.",
    )

    assert result.target_structure_used is True
    assert result.is_pass is True
    assert result.grammatical_accuracy == 0.2
    assert result.naturalness == 0.3


def test_accuracy_and_naturalness_are_surfaced_but_not_scheduled() -> None:
    """Accuracy/naturalness are present on the result for learner feedback, but
    is_pass is defined to equal target_structure_used exactly, never a blend."""
    canned = (
        '{"target_structure_used": true, "grammatical_accuracy": 0.4, '
        '"naturalness": 0.5, "feedback": "..."}'
    )
    grader = ProductionGrader(provider=MockLlmClient(canned_response=canned))
    result = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Kausalsatz mit 'weil'.",
        student_submission="Ich lerne, weil ich reise.",
    )
    assert result.is_pass == result.target_structure_used
    assert result.grammatical_accuracy == 0.4
    assert result.naturalness == 0.5


def test_grading_failure_degrades_to_self_assessment() -> None:
    """Malformed model output (or an unavailable LLM) must never silently pass the
    learner at a perfect score. It degrades to self-assessment and never blocks."""
    grader = ProductionGrader(provider=MockLlmClient(canned_response="not valid json"))
    result = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Kausalsatz mit 'weil'.",
        student_submission="Ich lerne, weil ich reise.",
    )

    assert result.requires_self_assessment is True
    assert result.is_pass is False
    assert result.target_structure_used is False


class _RaisingProvider:
    """Provider stub that always raises, simulating an unavailable LLM."""

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        purpose: str = "generation",
        is_user_content: bool = False,
    ) -> str:
        raise ConnectionError("simulated network failure")


def test_grading_unavailable_llm_degrades_to_self_assessment_never_blocks() -> None:
    """An unreachable LLM must degrade gracefully, not raise out of grade_production."""
    grader = ProductionGrader(provider=_RaisingProvider())
    result = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Kausalsatz mit 'weil'.",
        student_submission="Ich lerne, weil ich reise.",
    )
    assert result.requires_self_assessment is True
    assert result.is_pass is False


def test_minimal_pair_generator_preseeded() -> None:
    """Test retrieving preseeded contrastive minimal pair drills."""
    generator = MinimalPairGenerator()
    drill = generator.get_or_generate_drill("wechselpraepositionen")

    assert drill is not None
    assert drill.confusion_group == "wechselpraepositionen"
    assert "auf ___ Tisch" in drill.sentence_a
    assert drill.target_a == "dem"
    assert drill.target_b == "den"


def test_minimal_pair_generator_generates_for_unseeded_confusion_group() -> None:
    """An unseeded confusion group is generated through the injected provider, not
    silently substituted with an unrelated pre-seeded drill."""
    canned = (
        '{"sentence_a": "Wegen ___ Wetters bleiben wir zu Hause.", "target_a": "des", '
        '"explanation_a": "Genitiv nach wegen.", '
        '"sentence_b": "Trotz ___ Wetters gehen wir raus.", "target_b": "des", '
        '"explanation_b": "Genitiv nach trotz."}'
    )
    generator = MinimalPairGenerator(provider=MockLlmClient(canned_response=canned))
    drill = generator.get_or_generate_drill("praepositionen_genitiv_gehoben")

    assert drill is not None
    assert drill.confusion_group == "praepositionen_genitiv_gehoben"
    assert drill.target_a == "des"


def test_minimal_pair_generator_never_mislabels_on_genuine_miss() -> None:
    """A generation failure for an unknown group must return None, never a drill
    labelled with a group the caller did not ask for (e.g. the wechselpraepositionen
    fallback)."""
    generator = MinimalPairGenerator(provider=MockLlmClient(canned_response="not json"))
    drill = generator.get_or_generate_drill("kasus_genitiv")

    assert drill is None


def test_minimal_pair_generator_forces_requested_label_even_if_model_echoes_wrong_one() -> None:
    """Even if the model's JSON claims a different confusion_group, the returned
    drill is always labelled with what the caller asked for."""
    canned = (
        '{"sentence_a": "a", "target_a": "x", "explanation_a": "e1", '
        '"sentence_b": "b", "target_b": "y", "explanation_b": "e2", '
        '"confusion_group": "some_other_group"}'
    )
    generator = MinimalPairGenerator(provider=MockLlmClient(canned_response=canned))
    drill = generator.get_or_generate_drill("kasus_genitiv")

    assert drill is not None
    assert drill.confusion_group == "kasus_genitiv"


def test_weekly_report_generator_synthesizes_metrics() -> None:
    """Test generating a weekly progress report narrative."""
    generator = WeeklyReportGenerator(provider=MockLlmClient())
    report = generator.generate_report(
        total_reviews=40,
        accuracy=0.95,
        newly_acquired_topics=["pronomen_personal_nom", "verb_praesens_regelm"],
        current_streak_days=5,
        forecast_7day_load=12,
    )

    assert report.total_reviews == 40
    assert report.accuracy == 0.95
    assert len(report.newly_acquired_topics) == 2
    assert report.current_streak_days == 5
    assert len(report.narrative_summary) > 20


def _report_metrics() -> dict[str, object]:
    return {
        "total_reviews": 40,
        "accuracy": 0.9,
        "newly_acquired_topics": ["pronomen_personal_nom"],
        "current_streak_days": 3,
        "forecast_7day_load": 10,
    }


def test_weekly_report_auto_fires_once_item_threshold_crossed() -> None:
    """Fires automatically once completed items since the last report reach
    WEEKLY_REPORT_TRIGGER_ITEMS (40 by default)."""
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    trigger = WeeklyReportTrigger(clock=lambda: now)
    generator = WeeklyReportGenerator(provider=MockLlmClient())

    report, decision = generator.maybe_generate_report(
        trigger=trigger,
        items_since_last_report=40,
        last_report_generated_at=None,
        **_report_metrics(),  # type: ignore[arg-type]
    )
    assert decision.should_auto_fire is True
    assert report is not None

    report_below, decision_below = generator.maybe_generate_report(
        trigger=trigger,
        items_since_last_report=39,
        last_report_generated_at=None,
        **_report_metrics(),  # type: ignore[arg-type]
    )
    assert decision_below.should_auto_fire is False
    assert report_below is None


def test_weekly_report_manual_button_gated_below_minimum() -> None:
    """The manual button stays disabled below WEEKLY_REPORT_MANUAL_MIN_ITEMS, so
    repeated taps cannot shrink the window to noise."""
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    trigger = WeeklyReportTrigger(clock=lambda: now)
    generator = WeeklyReportGenerator(provider=MockLlmClient())

    report, decision = generator.maybe_generate_report(
        trigger=trigger,
        items_since_last_report=5,
        last_report_generated_at=None,
        manual_request=True,
        **_report_metrics(),  # type: ignore[arg-type]
    )
    assert decision.manual_button_enabled is False
    assert report is None

    report_ok, decision_ok = generator.maybe_generate_report(
        trigger=trigger,
        items_since_last_report=10,
        last_report_generated_at=None,
        manual_request=True,
        **_report_metrics(),  # type: ignore[arg-type]
    )
    assert decision_ok.manual_button_enabled is True
    assert report_ok is not None


def test_weekly_report_rate_limited_blocks_refire() -> None:
    """A report generated moments ago must not refire even if the item threshold
    is crossed again immediately."""
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    trigger = WeeklyReportTrigger(clock=lambda: now, cooldown=timedelta(hours=20))
    last_report_at = now - timedelta(hours=1)

    decision = trigger.evaluate(
        items_since_last_report=100, last_report_generated_at=last_report_at
    )

    assert decision.is_rate_limited is True
    assert decision.should_auto_fire is False
    assert decision.manual_button_enabled is False


def test_weekly_report_trigger_uses_injected_clock_deterministically() -> None:
    """The clock is an injected dependency, never datetime.now() captured implicitly."""
    fixed_now = datetime(2030, 1, 1, tzinfo=UTC)
    trigger = WeeklyReportTrigger(clock=lambda: fixed_now)
    assert trigger.now() == fixed_now

    decision = trigger.evaluate(
        items_since_last_report=100,
        last_report_generated_at=fixed_now - timedelta(hours=1),
    )
    # 1 hour < default 20h cooldown -> rate limited, deterministically.
    assert decision.is_rate_limited is True


def test_production_labelled_answers_golden() -> None:
    """Verify labelled answers golden fixture has 30 items with valid rubric fields."""
    import json
    from pathlib import Path

    fixture_path = Path("data/fixtures/production/labelled_answers.jsonl")
    assert fixture_path.exists(), "labelled_answers.jsonl fixture must exist"

    with fixture_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == 30, f"Expected 30 labelled answers, found {len(lines)}"

    for line in lines:
        row = json.loads(line)
        assert "target_topic_id" in row
        assert "student_submission" in row
        assert isinstance(row["target_structure_used"], bool)
        assert 0.0 <= row["grammatical_accuracy"] <= 1.0
        assert 0.0 <= row["naturalness"] <= 1.0
        assert isinstance(row["is_pass"], bool)
        assert len(row["explanation_de"]) > 5
