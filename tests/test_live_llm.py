"""Unit tests for live LLM explanations, production grader, and weekly progress reports."""

import pytest
from src.contracts import BankItem, Distractor
from src.llm.live_explainer import LiveExplainer
from src.llm.production_grader import ProductionGrader
from src.llm.provider import MockLlmClient
from src.llm.weekly_report import WeeklyReportGenerator


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


def test_live_explainer_generates_explanation(sample_item: BankItem) -> None:
    """Test generating a targeted grammar explanation for an incorrect answer."""
    explainer = LiveExplainer(provider=MockLlmClient())
    result = explainer.explain_mistake(sample_item, user_answer="den")

    assert result.item_id == sample_item.id
    assert result.topic_id == "dativ_nach_praeposition"
    assert result.user_answer == "den"
    assert result.correct_answer == "dem"
    assert len(result.explanation) > 10
    assert result.rule_summary == sample_item.rule_hint


def test_production_grader_evaluates_sentence() -> None:
    """Test grading an open-ended production sentence submission."""
    grader = ProductionGrader(provider=MockLlmClient())
    result = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Kausalsatz mit 'weil'.",
        student_submission="Ich lerne Deutsch, weil ich in Berlin studieren möchte.",
    )

    assert result.passed is True
    assert result.score == 1.0
    assert "korrekt" in result.feedback.lower()


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
