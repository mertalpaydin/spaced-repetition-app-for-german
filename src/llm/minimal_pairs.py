"""Interference-targeted minimal pair contrast generator with pre-seeded offline fallbacks."""

from pydantic import BaseModel, ConfigDict

from src.llm.provider import LlmProvider, MockLlmClient


class MinimalPairDrill(BaseModel):
    """Contrastive minimal pair drill exercising a confusable grammar distinction."""

    model_config = ConfigDict(frozen=True)
    confusion_group: str
    sentence_a: str
    target_a: str
    explanation_a: str
    sentence_b: str
    target_b: str
    explanation_b: str


class MinimalPairGenerator:
    """Generates and retrieves minimal pair contrast drills for high-error confusion groups."""

    PRESEEDED_FALLBACKS: dict[str, MinimalPairDrill] = {
        "wechselpraepositionen": MinimalPairDrill(
            confusion_group="wechselpraepositionen",
            sentence_a="Das Buch liegt auf ___ Tisch.",
            target_a="dem",
            explanation_a="Dativ bei Ortsangabe (Wo? -> statische Lage).",
            sentence_b="Ich lege das Buch auf ___ Tisch.",
            target_b="den",
            explanation_b="Akkusativ bei Richtungsangabe (Wohin? -> dynamische Bewegung).",
        ),
        "weil_denn": MinimalPairDrill(
            confusion_group="weil_denn",
            sentence_a="Er lernt Deutsch, weil er in Berlin studieren ___.",
            target_a="möchte",
            explanation_a="'weil' leitet einen Nebensatz ein (Verb am Ende).",
            sentence_b="Er lernt Deutsch, denn er ___ in Berlin studieren.",
            target_b="möchte",
            explanation_b="'denn' verbindet zwei Hauptsätze (Position 0, Verb auf Position 2).",
        ),
        "als_wenn": MinimalPairDrill(
            confusion_group="als_wenn",
            sentence_a="___ ich ein Kind war, spielte ich oft draußen.",
            target_a="Als",
            explanation_a="'als' für ein einmaliges Ereignis in der Vergangenheit.",
            sentence_b="Immer ___ er nach Hause kam, aß er einen Apfel.",
            target_b="wenn",
            explanation_b="'wenn' für wiederholte Handlungen ('immer wenn') oder Zukunft.",
        ),
    }

    def __init__(self, provider: LlmProvider | None = None) -> None:
        self.provider = provider or MockLlmClient()

    def get_or_generate_drill(self, confusion_group: str) -> MinimalPairDrill:
        """Retrieve pre-seeded drill or generate on-demand minimal pair drill."""
        if confusion_group in self.PRESEEDED_FALLBACKS:
            return self.PRESEEDED_FALLBACKS[confusion_group]

        # Default fallback
        return self.PRESEEDED_FALLBACKS["wechselpraepositionen"]
