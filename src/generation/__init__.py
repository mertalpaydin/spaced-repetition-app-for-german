"""Generation package for spec-driven batch candidate item creation."""

from src.generation.batch_client import (
    CostTracker,
    GeminiBatchClient,
    MockBatchClient,
    SpecSheetMissingError,
    VerifiedIngestResult,
    run_ingest,
    run_submit,
)
from src.generation.deficits import (
    MIN_BATCH_THRESHOLD,
    NIGHTLY_ITEM_CAP,
    SAFETY_FACTOR,
    TopicDeficit,
    build_generation_requests,
    compute_deficit,
    compute_topic_deficits,
    should_generate,
)
from src.generation.pilot import (
    PilotRunReport,
    build_pilot_requests,
    run_pilot,
    select_pilot_topics,
)
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import GoldExample, TopicSpec, load_spec, save_spec

__all__ = [
    "TopicSpec",
    "GoldExample",
    "load_spec",
    "save_spec",
    "PromptBuilder",
    "MockBatchClient",
    "GeminiBatchClient",
    "SpecSheetMissingError",
    "VerifiedIngestResult",
    "CostTracker",
    "run_submit",
    "run_ingest",
    "SAFETY_FACTOR",
    "MIN_BATCH_THRESHOLD",
    "NIGHTLY_ITEM_CAP",
    "TopicDeficit",
    "compute_deficit",
    "should_generate",
    "compute_topic_deficits",
    "build_generation_requests",
    "PilotRunReport",
    "select_pilot_topics",
    "build_pilot_requests",
    "run_pilot",
]
