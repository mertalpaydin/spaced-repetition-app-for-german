"""Tests verifying core contracts, enums, constants, and schema models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from src.contracts import (
    DUEL_LENGTH,
    FORECAST_HORIZON_DAYS,
    FORECAST_LOAD_THRESHOLD_DEFAULT,
    INFERRED_STABILITY_CEILING_DAYS,
    MAX_HEAVY_PER_ROUND,
    MAX_NEW_TOPICS_PER_DAY,
    MAX_REVIEWS_PER_DAY,
    NO_ERROR_ITEM_SHARE,
    PROMOTION_CONSECUTIVE_PASSES,
    PROMOTION_MIN_DISTINCT_FACETS,
    ROUND_SIZE_DEFAULT,
    SPLIT_ACCURACY_GAP,
    SPLIT_MIN_ATTEMPTS_PER_FACET,
    SUGGESTION_MIN_ACTIVE_DAYS,
    SUGGESTION_WINDOWS,
    THRESHOLD_CLAMP,
    VOCAB_RATIO_DEFAULT,
    WEEKLY_REPORT_TRIGGER_ITEMS,
    BankExport,
    BankItem,
    CandidateItem,
    DayBudget,
    Distractor,
    IntroCard,
    TagStateModel,
    ThresholdSuggestion,
    Topic,
    VerificationResult,
)


def test_global_constants_sanity() -> None:
    """Verify that all core constants align with the stage map and mathematical bounds."""
    assert 4 <= ROUND_SIZE_DEFAULT <= 10
    assert 0.0 <= VOCAB_RATIO_DEFAULT <= 0.50
    assert MAX_HEAVY_PER_ROUND == 1
    assert MAX_NEW_TOPICS_PER_DAY == 2
    assert MAX_REVIEWS_PER_DAY == 60
    assert FORECAST_HORIZON_DAYS == 7
    assert FORECAST_LOAD_THRESHOLD_DEFAULT == 50
    assert SUGGESTION_WINDOWS == (7, 14, 30)
    assert SUGGESTION_MIN_ACTIVE_DAYS == 10
    assert THRESHOLD_CLAMP == (10, 200)
    assert INFERRED_STABILITY_CEILING_DAYS == 4.0
    assert PROMOTION_CONSECUTIVE_PASSES == 3
    assert PROMOTION_MIN_DISTINCT_FACETS == 2
    assert SPLIT_MIN_ATTEMPTS_PER_FACET == 20
    assert SPLIT_ACCURACY_GAP == 0.40
    assert DUEL_LENGTH == 8
    assert 0.15 <= NO_ERROR_ITEM_SHARE <= 0.30
    assert WEEKLY_REPORT_TRIGGER_ITEMS == 40


def test_topic_model_validation() -> None:
    """Verify Topic model structure and immutability."""
    card = IntroCard(
        summary="Dativ after Wechselpräposition",
        rule_de=(
            "An / auf / in / über / unter / vor / hinter / neben / zwischen + Dativ bei Ort (Wo?)."
        ),
        worked_examples=["Das Buch liegt auf dem Tisch."],
        contrast_note="Akkusativ bei Richtung (Wohin?).",
    )
    topic = Topic(
        id="dativ_nach_praeposition",
        name_de="Dativ nach Wechselpräposition (Ort)",
        cefr="A2",
        prereqs=["kasus_dativ_formen", "artikel_bestimmt"],
        confusion_group="kasus_wechselpraeposition",
        description="Wechselpräpositionen governing Dativ when answering 'wo'.",
        eligible_types=["cloze_free", "cloze_cued", "error_correction"],
        requires_context=False,
        morph_spec={"Case": "Dat"},
        rule_hint="Wechselpräposition + Dativ bei Ortsangabe (Wo?)",
        intro_card=card,
    )
    assert topic.id == "dativ_nach_praeposition"
    assert topic.cefr == "A2"
    assert len(topic.prereqs) == 2

    # Immutability check
    with pytest.raises(ValidationError):
        topic.cefr = "B1"  # type: ignore[misc]


def test_candidate_item_validation() -> None:
    """Verify CandidateItem structure with distractors."""
    distractor = Distractor(text="den", implied_topic_id="kasus_akkusativ")
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[distractor],
        carrier_lemmas=["Buch", "liegen", "Tisch"],
    )
    assert item.proposed_answer == "dem"
    assert item.distractors[0].implied_topic_id == "kasus_akkusativ"


def test_verification_result_contract() -> None:
    """Verify VerificationResult model for Stage 4 pipeline."""
    item = CandidateItem(
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
    )
    res = VerificationResult(
        item=item,
        accepted=True,
        accepted_answers=["dem"],
        rejections=[],
    )
    assert res.accepted is True
    assert res.accepted_answers == ["dem"]
    assert len(res.rejections) == 0


def test_bank_item_and_export_contract() -> None:
    """Verify BankItem and BankExport models."""
    bank_item = BankItem(
        id="item_001",
        tag_id="dativ_nach_praeposition",
        dimension="grammar",
        type="cloze_free",
        cefr="A2",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        accepted_answers=["dem"],
        distractors=["den", "des", "das"],
        confusion_group="kasus_wechselpraeposition",
        facet="Masc_Def",
    )
    export = BankExport(
        schema_version=1,
        generated_at="2026-08-13T20:00:00Z",
        items=[bank_item],
    )
    assert export.schema_version == 1
    assert len(export.items) == 1
    assert export.items[0].id == "item_001"


def test_candidate_item_gloss_en_defaults_to_none_and_is_purely_additive() -> None:
    """docs/audits/generation-track-plan.md Cycle 3: ``gloss_en`` is a new,
    OPTIONAL field on ``CandidateItem``. An item built with no ``gloss_en``
    at all (every existing caller, before this change) must keep working
    unchanged, and the field must round-trip when supplied."""
    bare = CandidateItem(
        topic_id="praeteritum_vollverben",
        type="cloze_cued",
        difficulty=1,
        prompt="Weisst du, wo er ___ (wohnen)?",
        proposed_answer="wohnte",
    )
    assert bare.gloss_en is None

    glossed = CandidateItem(
        topic_id="praeteritum_vollverben",
        type="cloze_cued",
        difficulty=1,
        prompt="Weisst du, wo er ___ (wohnen)?",
        proposed_answer="wohnte",
        gloss_en="Do you know where he lived?",
    )
    assert glossed.gloss_en == "Do you know where he lived?"


def test_bank_item_gloss_en_defaults_to_none_and_is_purely_additive() -> None:
    """Same additive guarantee as ``CandidateItem.gloss_en``, for the bank's
    own item model -- the field must carry through from a verified
    candidate without breaking any existing bare ``BankItem`` construction."""
    bare = BankItem(
        id="item_002",
        tag_id="praeteritum_vollverben",
        type="cloze_cued",
        cefr="A2",
        difficulty=1,
        prompt="Weisst du, wo er ___ (wohnen)?",
        accepted_answers=["wohnte"],
    )
    assert bare.gloss_en is None

    glossed = BankItem(
        id="item_003",
        tag_id="praeteritum_vollverben",
        type="cloze_cued",
        cefr="A2",
        difficulty=1,
        prompt="Weisst du, wo er ___ (wohnen)?",
        accepted_answers=["wohnte"],
        gloss_en="Do you know where he lived?",
    )
    assert glossed.gloss_en == "Do you know where he lived?"


def test_learning_engine_models() -> None:
    """Verify TagStateModel, DayBudget, and ReviewLogEntry."""
    now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
    tag_state = TagStateModel(
        tag_id="dativ_nach_praeposition",
        dimension="grammar",
        state="learning",
        due_at=now,
        attempts=5,
        correct=4,
        consecutive_unhinted_passes=2,
        facets_seen_in_streak={"Masc_Def", "Fem_Def"},
    )
    assert tag_state.state == "learning"
    assert len(tag_state.facets_seen_in_streak) == 2

    budget = DayBudget(
        due_count=15,
        ceiling=25,
        new_topics_allowed=2,
        forecast=[15, 18, 12, 10, 14, 20, 11],
    )
    assert budget.due_count == 15
    assert len(budget.forecast) == 7

    suggestion = ThresholdSuggestion(
        window_days=14,
        active_days=12,
        median_items_per_active_day=30.0,
        active_day_rate=12 / 14,
        clear_rate=0.90,
        suggested=28,
    )
    assert suggestion.suggested == 28
