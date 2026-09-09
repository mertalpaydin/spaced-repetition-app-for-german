"""Shared types and constants.

Every persisted record of the phrase deck is defined here, once: the units
the deck teaches, the cards that test them, the generated context sentences,
and the exported deck's manifest. Nothing else may define a shared type.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ==============================================================================
# Core enumerations
# ==============================================================================

CEFR = Literal["A1", "A2", "B1", "B2"]
Difficulty = Literal[1, 2, 3]
FsrsRating = Literal["again", "hard", "good", "easy"]

# ==============================================================================
# Model routing (CLAUDE.md section 9). No model string appears inline anywhere
# else in ``src/``.
# ==============================================================================

MODEL_LIVE: str = "gemini-3.5-flash-lite"
MODEL_GENERATE: str = "gemini-3.5-flash-lite"
MODEL_VERIFY: str = "gemini-3.7-flash"
# gemini-3.7-flash's own default is "medium"; restated so the level stays a
# named, greppable config value.
THINKING_VERIFY: str = "medium"
# Flash-Lite's own default is "minimal"; "low" is the smallest step up. Keyed
# on the model, not the purpose (``GeminiLlmClient._thinking_config_for``).
THINKING_FLASH_LITE: str = "low"

# ``purpose=`` values passed to ``GeminiLlmClient.generate``. One constant per
# workload so the cost log and the thinking gate never drift from a hand-typed
# literal.
PURPOSE_SENTENCE_GENERATION: str = "sentence_generation"
PURPOSE_PHRASE_CONTEXT: str = "phrase_context"

# ==============================================================================
# The phrase deck
# ==============================================================================

PhraseKind = Literal[
    "verb_prep",
    "reflexive_verb",
    "separable_verb",
    "noun_verb",
    "adj_noun",
    "connector",
    "two_part_connector",
    "idiom",
]

#: ``unit_id`` prefix per kind. Ids are stable across builds because they are
#: derived from the unit's lemma key, never from a row number: the review log
#: joins on them.
UNIT_ID_PREFIX: dict[str, str] = {
    "verb_prep": "vp",
    "reflexive_verb": "rv",
    "separable_verb": "sv",
    "noun_verb": "nv",
    "adj_noun": "an",
    "connector": "cn",
    "two_part_connector": "c2",
    "idiom": "id",
}

Case = Literal["Akk", "Dat", "Gen"]
UnitSource = Literal["mined", "curated", "mined+curated"]
#: Only machine glosses reach a learner (CLAUDE.md rule 9). ``tatoeba`` is
#: deliberately not a member.
GlossSource = Literal["azure", "gemini"]
#: A corpus name from the build script's corpus list ("tatoeba",
#: "leipzig_news_2025", "opensubtitles_2018", ...). Widened from a two-value
#: Literal on 2026-09-09 when the corpus grew; flagged per CLAUDE.md rule 8.
CorpusSource = str

DECK_SCHEMA_VERSION: int = 1


class GapSpan(BaseModel):
    """One blank: a character span of ``sentence_de`` and the token it covers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    answer: str = Field(min_length=1)
    token_index: int = Field(ge=0)

    @model_validator(mode="after")
    def _span_is_forward(self) -> "GapSpan":
        if self.start >= self.end:
            raise ValueError(f"gap span must be forward, got {self.start}..{self.end}")
        return self


class PhraseUnit(BaseModel):
    """A phrase the deck teaches. What FSRS schedules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(min_length=4)
    kind: PhraseKind
    #: Lowercased lemma key the miners aggregate on, e.g. ``"warten auf"``.
    lemma_key: str = Field(min_length=1)
    #: The unit's parts in citation order, e.g. ``["warten", "auf"]``.
    parts: list[str] = Field(min_length=1)
    #: What the learner sees after answering, e.g. ``"warten auf"`` or
    #: ``"zur Verfügung stehen"``.
    display_de: str = Field(min_length=1)
    case: Case | None = None
    cefr: CEFR | None = None
    #: Unit-level English, curated lists only. Mined units have none until an
    #: opt-in gloss run adds them.
    gloss_en: str | None = None
    #: Distinct corpus sentences containing the unit, all corpora together.
    sentence_count: int = Field(ge=0)
    count_by_source: dict[str, int] = Field(default_factory=dict)
    #: Mean over the corpora of the unit's sentences per million sentences
    #: of that corpus. Ranking uses this, so no single register dominates;
    #: additive field, 2026-09-09.
    per_million: float = Field(default=0.0, ge=0)
    #: 1 is the most frequent unit in the deck.
    rank: int = Field(ge=1)
    trivial: bool = False
    trivial_reason: str | None = None
    source: UnitSource
    card_count: int = Field(ge=0)
    glossed_card_count: int = Field(default=0, ge=0)


class PhraseCard(BaseModel):
    """One corpus sentence with one unit's tokens blanked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ``sha1(unit_id + "\n" + sentence_de)[:12]``; stable across builds.
    card_id: str = Field(min_length=12, max_length=12)
    unit_id: str
    kind: PhraseKind
    sentence_de: str = Field(min_length=1)
    #: ``None`` until the monthly Azure job has glossed this sentence; a
    #: client shows only cards with a gloss (the translation is the cue).
    gloss_en: str | None = None
    gloss_source: GlossSource | None = None
    gaps: list[GapSpan] = Field(min_length=1)
    #: Always a list, one per gap, in gap order (CLAUDE.md rule 6).
    answers: list[str] = Field(min_length=1)
    #: Which surface realisation of the unit this card shows, e.g.
    #: ``"Fin|Pres|3|Sing|discontinuous"``; cards for one unit are chosen to
    #: cover distinct keys so the unit is taught, not memorised as a string.
    form_key: str
    corpus_source: CorpusSource
    corpus_line_id: str
    #: A sentence-initial connector needs a preceding sentence to connect to.
    needs_context: bool = False
    context_de: str | None = None
    context_en: str | None = None
    context_source: Literal["gemini"] | None = None

    @model_validator(mode="after")
    def _gaps_slice_back(self) -> "PhraseCard":
        if [g.answer for g in self.gaps] != self.answers:
            raise ValueError("answers must equal the gap answers, in gap order")
        previous_end = -1
        for gap in self.gaps:
            if gap.start < previous_end:
                raise ValueError("gaps must be sorted and disjoint")
            if self.sentence_de[gap.start : gap.end] != gap.answer:
                raise ValueError(
                    f"gap {gap.start}..{gap.end} does not slice to {gap.answer!r} "
                    f"in {self.sentence_de!r}"
                )
            previous_end = gap.end
        if (self.gloss_en is None) != (self.gloss_source is None):
            raise ValueError("gloss_en and gloss_source come together or not at all")
        if (self.context_de is None) != (self.context_en is None):
            raise ValueError("context_de and context_en come together or not at all")
        if self.context_de is not None and self.context_source is None:
            raise ValueError("a context needs a context_source")
        return self


# ------------------------------------------------------------------------------
# Context generation (the one LLM stage; opt-in)
# ------------------------------------------------------------------------------


class ContextRequest(BaseModel):
    """What the prompt is built from, and therefore what the cache key covers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: str
    unit_id: str
    connector_display: str
    sentence_de: str
    gloss_en: str
    prompt_version: int = Field(ge=1)


class ContextResponse(BaseModel):
    """The JSON the model must return. Anything else is a rejection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_de: str = Field(min_length=1)
    context_en: str = Field(min_length=1)


class ContextRecord(BaseModel):
    """One row of ``data/phrases/contexts.jsonl``: accepted or rejected, with why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    card_id: str
    unit_id: str
    sentence_de: str
    context_de: str | None = None
    context_en: str | None = None
    accepted: bool
    reject_reason: str | None = None
    model: str
    prompt_version: int
    generated_at: datetime


# ------------------------------------------------------------------------------
# The exported deck
# ------------------------------------------------------------------------------


class ShardInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    band: int = Field(ge=0)
    rank_from: int = Field(ge=1)
    rank_to: int = Field(ge=1)
    unit_count: int = Field(ge=0)
    card_count: int = Field(ge=0)


class DeckManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = DECK_SCHEMA_VERSION
    #: Changes whenever the deck content changes; the PWA keys its cache on it.
    deck_version: str
    built_at: datetime
    corpus: dict[str, int] = Field(default_factory=dict)
    unit_count: int = Field(ge=0)
    card_count: int = Field(ge=0)
    #: Cards a client can show today.
    glossed_card_count: int = Field(default=0, ge=0)
    trivial_count: int = Field(ge=0)
    contexts_generated: int = Field(ge=0)
    kinds: dict[str, int] = Field(default_factory=dict)
    units_file: str
    shards: list[ShardInfo]


class UnitsIndex(BaseModel):
    """Every unit, no cards: what the triage screen loads."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = DECK_SCHEMA_VERSION
    units: list[PhraseUnit]


class DeckShard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = DECK_SCHEMA_VERSION
    band: int = Field(ge=0)
    units: list[PhraseUnit]
    cards: list[PhraseCard]


# ==============================================================================
# Phase 2: the review log (the single source of truth for progress, rule 1)
# ==============================================================================

#: The three ratings the grader can produce. There is no "easy": the learner
#: never self-rates, the grade decides.
ReviewRating = Literal["again", "hard", "good"]
#: What happened to the typed answers, for the stats and for replay.
ReviewOutcome = Literal["exact", "translit", "typo", "case", "wrong", "revealed"]


class ReviewEntry(BaseModel):
    """One graded card. ``ts`` is UTC; ``seq`` is the log's own counter, so two
    logs (laptop, phone) can be merged on ``(unit_id, ts)`` and replayed in
    order. ``deck_version`` records which deck the card came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["review"] = "review"
    seq: int = Field(ge=1)
    ts: datetime
    unit_id: str
    card_id: str
    rating: ReviewRating
    outcome: ReviewOutcome
    answers: list[str]
    expected: list[str]
    elapsed_ms: int = Field(ge=0)
    deck_version: str


class MarkEntry(BaseModel):
    """The learner marked a unit known (``known=True``, skipped by the
    scheduler) or unmarked it. ``source`` says where: the one-time triage, or
    the "Kannte ich schon" button in practice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["mark"] = "mark"
    seq: int = Field(ge=1)
    ts: datetime
    unit_id: str
    known: bool
    source: Literal["triage", "practice"]


LogEntry = ReviewEntry | MarkEntry
