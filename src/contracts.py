"""Shared types and constants.

Phase 0 of the phrase-deck pivot: only what the surviving modules import.
Phase 1a adds the phrase models (``PhraseUnit``, ``PhraseCard``, the deck
manifest) here; nothing else may define a shared type.
"""

from typing import Literal

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
