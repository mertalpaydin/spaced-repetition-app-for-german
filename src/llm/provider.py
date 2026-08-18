"""LLM Provider interfaces and mock client implementation for offline execution."""

import os
from typing import Protocol


class LlmProvider(Protocol):
    """Protocol for LLM inference providers.

    ``purpose`` and ``is_user_content`` are keyword-only and optional so that both
    ``GeminiLlmClient`` (which uses them for cost-log attribution and lane routing)
    and simpler providers like ``MockLlmClient`` (which ignore them) satisfy this
    Protocol with the same call sites: ``provider.generate_text(prompt=..., system_prompt=...)``.
    """

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        purpose: str = "generation",
        is_user_content: bool = False,
    ) -> str: ...


class MockLlmClient:
    """Deterministic mock LLM client for unit tests and offline development."""

    def __init__(self, canned_response: str | None = None) -> None:
        self.canned_response = canned_response

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        *,
        purpose: str = "generation",
        is_user_content: bool = False,
    ) -> str:
        """Return canned or context-aware mock response."""
        if self.canned_response:
            return self.canned_response

        # Contextual mock responses
        if "Erklärung" in prompt or "Erkläre" in prompt or "Erkläre" in (system_prompt or ""):
            return (
                "Hier ist die grammatikalische Erklärung: Bei Wechselpräpositionen wie 'auf' "
                "verlangt eine statische Ortsangabe (Wo?) den Dativ ('dem Tisch')."
            )
        elif "Bewerte" in prompt or "Bewertung" in prompt or "Bewerte" in (system_prompt or ""):
            return (
                '{"passed": true, "score": 1.0, '
                '"feedback": "Grammatikalisch vollkommen korrekt.", "corrected_sentence": null}'
            )
        elif "Wochen" in prompt or "Fortschritt" in prompt or "Lerncoach" in (system_prompt or ""):
            return (
                "Hervorragende Lernwoche! Du hast 40 Wiederholungen mit 95% Genauigkeit "
                "absolviert und 2 neue Grammatikthemen erfolgreich erworben."
            )
        else:
            return "Mock LLM Response"


def _has_configured_api_key() -> bool:
    return bool(
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )


def default_llm_provider(*, forbid_paid_lane: bool = True) -> LlmProvider:
    """Return the real Gemini client when an API key is configured, else the offline mock.

    This is the wiring point CLAUDE.md rule 4 requires: every feature module
    (LiveExplainer, ProductionGrader, MinimalPairGenerator, WeeklyReportGenerator)
    defaults to this instead of hardcoding ``MockLlmClient()``, so a configured key
    is actually used, and an unconfigured environment (CI, offline development)
    degrades to the deterministic mock instead of failing.

    ``forbid_paid_lane`` is threaded straight through to ``GeminiLlmClient``
    (see its docstring and ``PaidLaneForbiddenError``): the owner's lane
    policy is "no batch for the pilot; batch for scheduled/nightly work; and
    for the four live feature modules, on demand if a human is waiting on an
    instant answer or a button click, batch if the work runs on a schedule
    with nobody watching". Every call site in this codebase that constructs a
    feature module is on-demand or dual-mode, never purely scheduled, so the
    default here is ``True`` -- the safe choice that guarantees a synchronous
    reply rather than silently falling over to a batch job a waiting learner
    would never see complete in time. Callers that are genuinely scheduled
    (``WeeklyReportGenerator``'s activity-triggered auto-fire path) pass
    ``forbid_paid_lane=False`` explicitly for that path only; nothing here
    changes the default silently under them.
    """
    if _has_configured_api_key():
        from src.llm.client import GeminiLlmClient

        return GeminiLlmClient(forbid_paid_lane=forbid_paid_lane)
    return MockLlmClient()
