"""Interference-targeted minimal pair contrast generator with pre-seeded offline fallbacks."""

import json

from pydantic import BaseModel, ConfigDict

from src.llm.provider import LlmProvider, default_llm_provider


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

    GENERATION_SYSTEM_PROMPT = (
        "Du erstellst kontrastive Minimalpaare für deutsche Grammatik. Gib ausschließlich "
        "JSON zurück mit den Feldern sentence_a, target_a, explanation_a, sentence_b, "
        "target_b, explanation_b. Die beiden Sätze müssen strukturell parallel sein und "
        "sich nur in der kontrastierten Zielstruktur unterscheiden."
    )

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
        # A minimal-pair drill is exercise content and could, in principle, be
        # prepared ahead of time on the batch lane -- but ``get_or_generate_drill``
        # is a single ad-hoc call for one ``confusion_group``, made synchronously
        # in request/response style, not batched with other drills the way
        # nightly item generation batches whole topic requests together
        # (``src/generation/batch_client.py``). Nothing in this codebase calls
        # it from a scheduled/nightly context; every existing and plausible
        # caller (a duel needing a contrast drill for a confusion group right
        # now) is a learner mid-session, waiting on the result. On demand,
        # like ``LiveExplainer`` and ``ProductionGrader``, until a genuine
        # batch-prep call site exists that can justify relaxing this.
        self.provider = provider or default_llm_provider(forbid_paid_lane=True)

    def get_or_generate_drill(self, confusion_group: str) -> MinimalPairDrill | None:
        """Retrieve a pre-seeded drill, else generate one, else return ``None``.

        Never returns a drill labelled with a group the caller did not ask
        for: an unrecognised ``confusion_group`` either produces a freshly
        generated drill for that exact group or a genuine miss (``None``),
        it never silently substitutes an unrelated pre-seeded drill.
        """
        if confusion_group in self.PRESEEDED_FALLBACKS:
            return self.PRESEEDED_FALLBACKS[confusion_group]

        return self._generate_drill(confusion_group)

    def _generate_drill(self, confusion_group: str) -> MinimalPairDrill | None:
        """Generate a minimal pair for `confusion_group` through the injected provider."""
        prompt = (
            f"Confusion group: {confusion_group}\n"
            "Erzeuge ein Minimalpaar, das genau diese beiden Strukturen kontrastiert."
        )
        try:
            response_text = self.provider.generate_text(
                prompt=prompt,
                system_prompt=self.GENERATION_SYSTEM_PROMPT,
                purpose="minimal_pair_generation",
            )
            parsed = json.loads(response_text)
            return MinimalPairDrill(
                # Forced, never trusted from the model: the drill's label must
                # always match what the caller asked for.
                confusion_group=confusion_group,
                sentence_a=str(parsed["sentence_a"]),
                target_a=str(parsed["target_a"]),
                explanation_a=str(parsed["explanation_a"]),
                sentence_b=str(parsed["sentence_b"]),
                target_b=str(parsed["target_b"]),
                explanation_b=str(parsed["explanation_b"]),
            )
        except Exception:
            # Generation failed (network, malformed/incomplete JSON). A
            # genuine miss must surface as None, never as a mislabelled
            # pre-seeded drill from an unrelated confusion group.
            return None
