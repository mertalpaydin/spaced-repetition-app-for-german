"""Core enumerations, constants, and typed contracts for the German Grammar Trainer.

Defined once here and imported across all pipeline modules and learning engine.
Contracts follow the specification in 00-index.md, 02-content-pipeline.md,
and 03-learning-engine.md.
"""

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# ==============================================================================
# Core Enumerations & Types
# ==============================================================================

CEFR = Literal["A1", "A2", "B1", "B2"]
Dimension = Literal["grammar", "vocab"]
TagState = Literal["locked", "ready", "unseen", "learning", "acquired", "dormant"]
AcquiredVia = Literal["kalibrierung", "inferred", "earned"] | None
ItemType = Literal[
    "cloze_free",
    "cloze_cued",
    "error_correction",
    "transformation",
    "paragraph_cloze",
    "production",
]
Difficulty = Literal[1, 2, 3]
HintLevel = Literal[0, 1, 2, 3, 4]  # 0: none, 1: shape, 2: options, 3: rule stated, 4: revealed
FsrsRating = Literal["again", "hard", "good", "easy"]
ReviewMode = Literal["review", "duel", "challenge", "recalibration"]

# ==============================================================================
# Global Constants & Pacing Limits
# ==============================================================================

ROUND_SIZE_DEFAULT: int = 6  # UI setting, range 4-10; unit all pacing rules use
VOCAB_RATIO_DEFAULT: float = 0.30  # UI slider, 0.00 to 0.50; 0.00 disables vocabulary
WEEKLY_REPORT_TRIGGER_ITEMS: int = 40  # items since last report that auto-fire it
WEEKLY_REPORT_MANUAL_MIN_ITEMS: int = 10  # gate on the manual button
MAX_HEAVY_PER_ROUND: int = 1  # heavy = paragraph block OR production item
MAX_NEW_TOPICS_PER_DAY: int = 2  # a DAILY budget, never per round
MAX_REVIEWS_PER_DAY: int = 60  # backlog cap; overflow slips rather than piling up
FORECAST_HORIZON_DAYS: int = 7  # window checked before allowing new introductions
FORECAST_LOAD_THRESHOLD_DEFAULT: int = 50  # user-configurable
SUGGESTION_WINDOWS: tuple[int, int, int] = (7, 14, 30)  # days; default 14
SUGGESTION_MIN_ACTIVE_DAYS: int = 10  # below this, no suggestion is offered
THRESHOLD_CLAMP: tuple[int, int] = (10, 200)  # guards degenerate histories
INFERRED_STABILITY_CEILING_DAYS: float = 4.0  # hard cap for acquired_via="inferred"
OVERRIDE_UPHELD_RATE_ALERT: float = 0.03  # above this, the verification chain is failing
PROMOTION_CONSECUTIVE_PASSES: int = 3  # learning -> acquired, unhinted
PROMOTION_MIN_DISTINCT_FACETS: int = 2  # evidence must span cells, not repeat one
SPLIT_MIN_ATTEMPTS_PER_FACET: int = 20  # before a topic can be flagged for splitting
SPLIT_ACCURACY_GAP: float = 0.40  # facet accuracy spread that flags a candidate
VERIFICATION_KILL_GATE_THRESHOLD: float = 0.15  # drop rate exceeding 15% trips the kill gate
KILL_GATE_DROP_THRESHOLD: float = 0.15
MIN_STOCK_PER_TIER: int = 8

MODEL_LIVE: str = "gemini-3.5-flash-lite"  # explanations, production grading, report narrative
MODEL_GENERATE: str = "gemini-3.5-flash-lite"  # batch, thinking OFF
MODEL_VERIFY: str = "gemini-3.6-flash"  # batch, thinking low/medium
THINKING_VERIFY: str = "low"  # tune against measured recall

DUEL_LENGTH: int = 8  # items per duel, range 6-8
DUEL_MIN_ATTEMPTS_TO_SUGGEST: int = 15  # per confusion group, before ranking it
DORMANCY_DAYS: int = 21  # triggers a recalibration round on return
RECALIBRATION_ROUND_SIZE: int = 10
NO_ERROR_ITEM_SHARE: float = 0.22  # share of error-correction items with no error

# ==============================================================================
# Topic and Taxonomy Models
# ==============================================================================


class IntroCard(BaseModel):
    model_config = ConfigDict(frozen=True)
    summary: str
    rule_de: str
    worked_examples: list[str]
    contrast_note: str | None = None


class Topic(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name_de: str
    cefr: CEFR
    prereqs: list[str] = Field(default_factory=list)
    confusion_group: str | None = None
    description: str
    eligible_types: list[ItemType] = Field(default_factory=list)
    requires_context: bool = False
    morph_spec: dict[str, Any] = Field(default_factory=dict)
    rule_hint: str = ""
    intro_card: IntroCard | None = None
    split_into: list[str] | None = None
    split_axis: str | None = None
    derived_from: str | None = None
    sibling_group: str | None = None


# ==============================================================================
# Content Pipeline: Generation Contracts
# ==============================================================================


class Distractor(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    implied_topic_id: str | None = None  # the topic under which this distractor would be correct


class GenerationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    topic_id: str
    count: int
    difficulty: Difficulty
    item_types: list[ItemType]
    seed_sentence_ids: list[str] = Field(default_factory=list)


class CandidateItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    topic_id: str
    type: ItemType
    difficulty: Difficulty
    prompt: str
    cue: str | None = None
    proposed_answer: str
    distractors: list[Distractor] = Field(default_factory=list)  # exactly 3 for level 2 hints
    block_id: str | None = None  # set for paragraph_cloze gaps
    block_position: int | None = None
    source_sentence_id: str | None = None
    carrier_lemmas: list[str] = Field(default_factory=list)
    domain: str | None = None


BatchId = str
BatchStatus = Literal["pending", "running", "completed", "failed"]


class BatchClient(Protocol):
    def submit(self, requests: list[GenerationRequest]) -> BatchId: ...
    def poll(self, batch_id: BatchId) -> BatchStatus: ...
    def retrieve(self, batch_id: BatchId) -> list[CandidateItem]: ...


# ==============================================================================
# Content Pipeline: Verification Contracts
# ==============================================================================

ErrorTaxonomy = Literal[
    "schema",
    "topic_leak",
    "ambiguity",
    "morphological_defect",
    "vocabulary_ceiling_violation",
    "pedagogical_flaw",
    "register_mismatch",
    "duplicate",
]

RejectionReason = Literal[
    "schema",
    "topic_leak",
    "ambiguous_answer",
    "morphology_mismatch",
    "level_violation",
    "duplicate",
    "answer_in_prompt",
]


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    item: CandidateItem | None = None
    accepted: bool = True
    passed: bool = True
    accepted_answers: list[str] = Field(default_factory=list)
    rejections: list[RejectionReason] = Field(default_factory=list)
    layer_failed: int | None = None
    reason: str | None = None
    error_type: ErrorTaxonomy | None = None


class Verifier(Protocol):
    def verify(self, items: list[CandidateItem]) -> list[VerificationResult]: ...


# ==============================================================================
# Bank Storage & Export Contracts
# ==============================================================================


class BankItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    tag_id: str = ""
    topic_id: str = ""
    dimension: Dimension = "grammar"
    type: ItemType
    cefr: CEFR
    difficulty: Difficulty
    prompt: str
    cue: str | None = None
    accepted_answers: list[str]  # non-empty, deduplicated
    distractors: list[Distractor] | list[str]  # 3, for hint level 2
    rule_hint: str = ""
    block_id: str | None = None  # paragraph_cloze grouping
    block_position: int | None = None
    confusion_group: str | None = None  # copied from topic; enables offline minimal-pair fallback
    facet: str | None = None  # derived at ingest from answer token's morphology minus morph_spec
    carrier_lemmas: list[str] = Field(default_factory=list)
    domain: str | None = None
    source_sentence_id: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.topic_id and not self.tag_id:
            object.__setattr__(self, "tag_id", self.topic_id)
        elif self.tag_id and not self.topic_id:
            object.__setattr__(self, "topic_id", self.tag_id)


class InsertReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    inserted: int
    duplicates_skipped: int
    errors: list[str] = Field(default_factory=list)


class BankExport(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    generated_at: str
    items: list[BankItem]


class Bank(Protocol):
    def insert(self, items: list[BankItem]) -> InsertReport: ...
    def stock(self, tag_id: str, difficulty: Difficulty) -> int: ...
    def export_full(self) -> BankExport: ...
    def export_delta(self, since_id: str) -> BankExport: ...


# ==============================================================================
# Learning Engine & Scheduler Contracts
# ==============================================================================


class TagStateModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    tag_id: str
    dimension: Dimension = "grammar"
    state: TagState = "locked"
    acquired_via: AcquiredVia = None
    fsrs_stability: float = 0.0
    fsrs_difficulty: float = 0.0
    due_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attempts: int = 0
    correct: int = 0
    consecutive_failures: int = 0
    consecutive_unhinted_passes: int = 0
    promotion_consecutive_passes: int = 0
    promotion_distinct_facets: list[str] = Field(default_factory=list)
    facets_seen_in_streak: set[str] = Field(default_factory=set)
    inferred_stability_cap: float | None = None
    introduced_at: datetime | None = None
    last_review_at: datetime | None = None


class RoundItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    item: BankItem
    mode: ReviewMode = "review"


class Round(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[RoundItem]
    is_bonus: bool = False


class DayBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    due_count: int  # tags due before end of day
    ceiling: int  # where diminishing returns begin, derived dynamically
    new_topics_allowed: int  # 0 when 7-day forecast load exceeds threshold
    forecast: list[int]  # projected review count per day, next 7 days


class ThresholdSuggestion(BaseModel):
    model_config = ConfigDict(frozen=True)
    window_days: int  # 7, 14, or 30
    active_days: int
    median_items_per_active_day: float
    active_day_rate: float  # active days / calendar days in window
    clear_rate: float  # active days queue cleared / active days
    suggested: int | None  # None below SUGGESTION_MIN_ACTIVE_DAYS


class Answer(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str
    tag_id: str
    given_answer: str
    correct: bool
    hint_level_used: HintLevel
    response_ms: int
    timestamp: datetime


class ReviewLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str
    tag_id: str
    timestamp: datetime
    correct: bool
    response_ms: int
    user_answer: str
    hint_level_used: HintLevel = 0
    mode: ReviewMode = "review"
    is_bonus: bool = False
    is_override: bool = False
    facet: str | None = None


class LapseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    topic_id: str
    failing_facet: str | None
    prior_stability: float
    streak_length: int
    hint_level_used: HintLevel
    days_since_last_review: float
    implied_topic_id: str | None
    timestamp: datetime


class Scheduler(Protocol):
    def build_round(self, states: list[TagStateModel], size: int, now: datetime) -> Round: ...
    def day_budget(self, states: list[TagStateModel], now: datetime) -> DayBudget: ...
    def forecast(
        self, states: list[TagStateModel], now: datetime, horizon_days: int
    ) -> list[int]: ...
    def build_duel(self, confusion_group: str, now: datetime) -> list[BankItem]: ...
    def build_recalibration(self, states: list[TagStateModel], now: datetime) -> list[BankItem]: ...


class Kalibrierung(Protocol):
    def next_item(self, answered: list[Answer]) -> BankItem | None: ...
    def finalise(self, answered: list[Answer]) -> list[TagStateModel]: ...


# ==============================================================================
# Grading Contracts
# ==============================================================================


class GradingVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)
    is_correct: bool
    is_scoped_typo: bool = False
    is_transliteration: bool = False
    feedback_message: str | None = None
    highlight_span: tuple[int, int] | None = None
    target_morphene_tested: str | None = None
