"""LLM Provider interfaces and mock client implementation for offline execution."""

from typing import Protocol


class LlmProvider(Protocol):
    """Protocol for LLM inference providers."""

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str: ...


class MockLlmClient:
    """Deterministic mock LLM client for unit tests and offline development."""

    def __init__(self, canned_response: str | None = None) -> None:
        self.canned_response = canned_response

    def generate_text(self, prompt: str, system_prompt: str | None = None) -> str:
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
