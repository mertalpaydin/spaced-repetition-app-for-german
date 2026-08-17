"""Shared Pydantic data contracts, core enumerations, and pacing invariants."""

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ==============================================================================
# Core Enumerations
# ==============================================================================

CEFR = Literal["A1", "A2", "B1", "B2"]
Dimension = Literal["grammar", "vocab"]
TagState = Literal["unseen", "learning", "acquired", "locked", "ready", "dormant"]
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
# docs/audits/generation-track-plan.md "Topic triage": which of the four
# verification strategies a topic's gap admits. "computable" answers are
# derivable from a closed morphological paradigm; "lexical_table" answers are
# a finite dictionary lookup; "structural" answers are a checkable word
# position; "semantic" topics have no mechanically checkable answer at all.
VerificationClass = Literal["computable", "lexical_table", "structural", "semantic"]
HintLevel = Literal[0, 1, 2, 3, 4]  # 0: none, 1: shape, 2: options, 3: rule stated, 4: revealed
FsrsRating = Literal["again", "hard", "good", "easy"]
ReviewMode = Literal["review", "duel", "challenge", "recalibration"]

BatchId = str
BatchStatus = Literal["pending", "completed", "failed"]
ErrorTaxonomy = Literal[
    "structural_malformation",
    "topic_leak",
    "morphosyntactic_error",
    "morphological_defect",
    "register_mismatch",
    "ambiguity",
    "duplicate",
    "vocabulary_ceiling_violation",
    "pedagogical_flaw",
    # docs/audits/stage-04-recovery-plan.md fix D: a parenthetical cue that
    # hands over the answer is its own defect, distinct from a topic leak
    # (it doesn't name the grammar topic) and from structural_malformation
    # (the item's shape is fine; it just tests nothing).
    "answer_leak",
]

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
FORECAST_LOAD_THRESHOLD_DEFAULT: int = 50  # user-configurable; see stage 6
SUGGESTION_WINDOWS: tuple[int, ...] = (7, 14, 30)  # days; default 14
SUGGESTION_MIN_ACTIVE_DAYS: int = 10  # below this, no suggestion is offered
THRESHOLD_CLAMP: tuple[int, int] = (10, 200)  # guards degenerate histories
INFERRED_STABILITY_CEILING_DAYS: float = 4.0  # hard cap for acquired_via="inferred"
OVERRIDE_UPHELD_RATE_ALERT: float = 0.03  # above this, the verification chain is failing
PROMOTION_CONSECUTIVE_PASSES: int = 3  # learning -> acquired, unhinted
PROMOTION_MIN_DISTINCT_FACETS: int = 2  # evidence must span cells, not repeat one
SPLIT_MIN_ATTEMPTS_PER_FACET: int = 20  # before a topic can be flagged for splitting
SPLIT_ACCURACY_GAP: float = 0.40  # facet accuracy spread that flags a candidate

MODEL_LIVE: str = "gemini-3.5-flash-lite"
MODEL_GENERATE: str = "gemini-3.5-flash-lite"
MODEL_VERIFY: str = "gemini-3.7-flash"
THINKING_VERIFY: str = "low"
# Gemini 3.5 Flash-Lite supports thinking levels ("minimal", "low", "medium",
# "high"), superseding the earlier assumption that this model line had no
# thinking capability at all. Generation quality (the "kauft ich" A1
# agreement defect: a fronted adverbial forces V2 inversion, and an
# autoregressive model with no planning step can commit to a high-probability
# verb form before it has chosen the subject that has to agree with it) is
# the reason to spend the smallest available thinking budget on this
# workload, not zero. Named for, and used only by, the sentence-generation
# purpose below -- MODEL_LIVE and MODEL_GENERATE are the SAME model string,
# so this cannot be gated on model id alone without also turning thinking on
# for explanations, production grading, and the weekly report narrative,
# none of which this change is about.
THINKING_GENERATE: str = "minimal"
# The ``purpose=`` value ``LiveSentenceGenerator.generate`` (src/generation/
# blanking/sentence_source.py) passes to ``GeminiLlmClient.generate``. Shared
# as one constant, imported by both that call site and
# ``GeminiLlmClient._thinking_config_for``, so the two can never drift out of
# sync the way two independently hand-typed string literals could.
PURPOSE_SENTENCE_GENERATION: str = "sentence_generation"

DUEL_LENGTH: int = 8  # items per duel, range 6-8
DUEL_MIN_ATTEMPTS_TO_SUGGEST: int = 15  # per confusion group, before ranking it
DORMANCY_DAYS: int = 21  # triggers a recalibration round on return
RECALIBRATION_ROUND_SIZE: int = 10
NO_ERROR_ITEM_SHARE: float = 0.22  # share of error-correction items with no error
VERIFICATION_KILL_GATE_THRESHOLD: float = 0.15
MIN_STOCK_PER_TIER: int = 12  # DoD: 12 items/topic, A1-B2, cold-seeded (02-content-pipeline.md)
MIN_PREREQ_STABILITY: float = 7.0  # days; a prereq below this blocks its dependents


# ==============================================================================
# Data Models
# ==============================================================================


class Distractor(BaseModel):
    """Multiple choice distractor option with optional misconception attribution."""

    model_config = ConfigDict(frozen=True, extra="allow")
    text: str
    implied_topic_id: str | None = None
    misconception: str | None = None


class IntroCard(BaseModel):
    """Static introductory teaching card for a grammar topic."""

    model_config = ConfigDict(frozen=True, extra="allow")
    topic_id: str | None = None
    summary: str | None = None
    rule_de: str = ""
    worked_examples: list[str] = Field(default_factory=list)
    contrast_note: str | None = None


class Topic(BaseModel):
    """Single node in the German grammar taxonomy DAG."""

    model_config = ConfigDict(frozen=True, extra="allow")
    id: str
    name_de: str
    cefr: CEFR
    description: str
    prereqs: list[str] = Field(default_factory=list)
    transitive_prereqs: list[str] = Field(default_factory=list)
    eligible_types: list[ItemType] = Field(default_factory=list)
    requires_context: bool = False
    sibling_group: str | None = None
    confusion_group: str | None = None
    morph_spec: dict[str, Any] | None = None
    # Optional (not required on the model) so that Topic instances built ad
    # hoc in unrelated tests/fixtures keep working; data/taxonomy.yaml itself
    # is expected to set this on every entry, and
    # tests/test_taxonomy.py enforces that as a data invariant.
    verification_class: VerificationClass | None = None
    syntax_tags: dict[str, str] = Field(default_factory=dict)
    rule_hint: str | None = None
    rule_de: str | None = None
    worked_examples: list[str] = Field(default_factory=list)
    intro_card: IntroCard | None = None


class CandidateItem(BaseModel):
    """Unverified generated grammar item output from the LLM pipeline."""

    model_config = ConfigDict(frozen=True, extra="allow")
    topic_id: str
    type: ItemType
    difficulty: Difficulty
    prompt: str
    proposed_answer: str
    distractors: list[Distractor] = Field(default_factory=list)
    cue: str | None = None
    rule_hint: str | None = None
    facet: str | None = None
    domain: str | None = "general"
    carrier_lemmas: list[str] = Field(default_factory=list)
    source_sentence_id: str | None = None
    # docs/audits/generation-track-plan.md Cycle 3, and CLAUDE.md rule 2's
    # gloss-not-label resolution: a natural English translation of the
    # COMPLETE carrier sentence with the gap filled by the intended answer.
    # Disambiguates gaps no preposition/verb/determiner in the German
    # sentence narrows to one lexeme (the "Weisst du, wo er ___ (wohnen)?"
    # case), without naming the grammar topic the way a category label
    # would. Optional here; whether it is REQUIRED for a given item is a
    # verification-time decision (see src/generation/gloss_validation.py),
    # not a contract-level constraint, so this stays purely additive.
    # For the narrow fallback list (English marks no distinction at all,
    # e.g. du vs Sie), this field instead carries a short category tag from
    # gloss_validation's controlled vocabulary -- see that module.
    gloss_en: str | None = None

    @field_validator("distractors", mode="before")
    @classmethod
    def parse_distractors(cls, v: Any) -> list[Distractor]:
        if not isinstance(v, list):
            return []
        parsed: list[Distractor] = []
        for item in v:
            if isinstance(item, str):
                parsed.append(Distractor(text=item))
            elif isinstance(item, dict):
                parsed.append(Distractor(**item))
            elif isinstance(item, Distractor):
                parsed.append(item)
        return parsed


class BankItem(BaseModel):
    """Fully verified exercise item stored in the SQLite bank and exported to static JSON."""

    model_config = ConfigDict(frozen=True, extra="allow")
    id: str
    topic_id: str = ""
    tag_id: str | None = None
    dimension: Dimension = "grammar"
    type: ItemType
    difficulty: Difficulty
    cefr: CEFR
    prompt: str
    accepted_answers: list[str]
    distractors: list[Distractor] = Field(default_factory=list)
    cue: str | None = None
    rule_hint: str | None = None
    facet: str | None = None
    confusion_group: str | None = None
    block_id: str | None = None
    block_position: int | None = None
    domain: str | None = "general"
    carrier_lemmas: list[str] = Field(default_factory=list)
    source_sentence_id: str | None = None
    # See CandidateItem.gloss_en's docstring comment: carried through
    # unchanged from the verified candidate into the bank item so it can be
    # rendered alongside the item for the learner.
    gloss_en: str | None = None

    @field_validator("distractors", mode="before")
    @classmethod
    def parse_distractors(cls, v: Any) -> list[Distractor]:
        if not isinstance(v, list):
            return []
        parsed: list[Distractor] = []
        for item in v:
            if isinstance(item, str):
                parsed.append(Distractor(text=item))
            elif isinstance(item, dict):
                parsed.append(Distractor(**item))
            elif isinstance(item, Distractor):
                parsed.append(item)
        return parsed

    @field_validator("accepted_answers")
    @classmethod
    def validate_accepted_answers(cls, v: list[str]) -> list[str]:
        """Enforce the contract's ``non-empty, deduplicated`` invariant on
        ``accepted_answers``: reject items with no accepted answer at all, and
        silently collapse exact duplicates while preserving first-seen order
        (so ``["dem", "dem"]`` becomes ``["dem"]`` rather than being rejected)."""
        if not v:
            raise ValueError("accepted_answers must be non-empty")
        seen: set[str] = set()
        deduped: list[str] = []
        for answer in v:
            if answer not in seen:
                seen.add(answer)
                deduped.append(answer)
        return deduped


class GenerationRequest(BaseModel):
    """Batch generation specification sent to LLM batch client."""

    model_config = ConfigDict(frozen=True, extra="allow")
    topic_id: str
    count: int
    difficulty: Difficulty
    item_types: list[ItemType]


class VerificationResult(BaseModel):
    """Result of passing a candidate item through a verification layer."""

    model_config = ConfigDict(frozen=True, extra="allow")
    item: CandidateItem | None = None
    passed: bool = True
    accepted: bool = True
    accepted_answers: list[str] = Field(default_factory=list)
    rejections: list[str] = Field(default_factory=list)
    layer_failed: int | None = None
    reason: str | None = None
    error_type: str | None = None
    # docs/audits/generation-track-plan.md Cycle 3: how many of THIS item's
    # gloss dimensions (tense, person) src.generation.gloss_validation could
    # neither confirm nor contradict -- "no evidence" is not "consistent",
    # and must never be silently folded into ``passed``. Set only when the
    # item actually carried a ``gloss_en`` and that gloss did not already
    # fail outright (a failing gloss is a rejection, not an unverified
    # count). Purely additive, defaults to 0 like ``gloss_en`` itself, so
    # every existing caller/test that builds a ``VerificationResult`` with
    # no opinion on gloss validation is unaffected.
    gloss_unverified_count: int = 0


class Answer(BaseModel):
    """User response to an assessment or practice item."""

    model_config = ConfigDict(frozen=True, extra="allow")
    item_id: str
    topic_id: str
    user_answer: str
    is_correct: bool
    hint_level: HintLevel = 0


class TagStateModel(BaseModel):
    """Learning state and progress metrics for a single grammar or vocabulary tag."""

    model_config = ConfigDict(frozen=True, extra="allow")
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
    """A single item inside a round bundle."""

    model_config = ConfigDict(frozen=True, extra="allow")
    item: BankItem
    mode: ReviewMode = "review"


class Round(BaseModel):
    """Complete bundle of items forming one practice round."""

    model_config = ConfigDict(frozen=True, extra="allow")
    items: list[RoundItem]
    is_bonus: bool = False


class DayBudget(BaseModel):
    """Daily capacity and new introduction limits."""

    model_config = ConfigDict(frozen=True, extra="allow")
    due_count: int  # tags due before end of day
    ceiling: int  # where diminishing returns begin, derived dynamically
    new_topics_allowed: int  # 0 when 7-day forecast load exceeds threshold
    forecast: list[int]  # projected review count per day, next 7 days


class ThresholdSuggestion(BaseModel):
    """Calculated suggestion for user forecast load threshold."""

    model_config = ConfigDict(frozen=True, extra="allow")
    suggested: int = 50
    suggested_threshold: int = 50
    window_days: int = 14
    active_days: int = 10
    median_items_per_active_day: float = 30.0
    median_reviews_per_active_day: float = 30.0
    active_day_rate: float = 0.8
    clear_rate: float = 0.9
    reason: str = "History analysis"


class BankExport(BaseModel):
    """Schema for bank JSON export bundle."""

    model_config = ConfigDict(frozen=True, extra="allow")
    version: str = "1.0"
    schema_version: int = 1
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    generated_at: str | datetime | None = None
    total_items: int = 0
    topic_count: int = 0
    items: list[BankItem] = Field(default_factory=list)


class InsertReport(BaseModel):
    """Outcome summary for a batch insert into the item bank.

    ``duplicates`` counts items whose ``id`` already existed in the bank (the
    insert was a no-op, not an error). ``rejected`` counts items that failed
    a bank-level integrity check (e.g. the prompt leaks an accepted answer)
    and were never written; ``rejection_reasons`` carries one human-readable
    string per rejected item so the caller knows *why*.
    """

    model_config = ConfigDict(frozen=True)
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0
    rejection_reasons: list[str] = Field(default_factory=list)


class Bank(Protocol):
    """Storage contract for the verified item bank (02-content-pipeline.md stage 5)."""

    def insert(self, items: list[BankItem]) -> InsertReport:
        """Insert a batch of items, idempotently on ``id``, returning a report."""
        ...

    def stock(self, tag_id: str, difficulty: Difficulty) -> int:
        """Count UNSEEN items (no review_logs entry) for a tag at a difficulty tier."""
        ...

    def export_full(self) -> BankExport:
        """Export every item currently in the bank."""
        ...

    def export_delta(self, since_id: str) -> BankExport:
        """Export only items inserted after ``since_id``."""
        ...
