"""Live interactive grammar explanation generator using LLM inference.

Cached on ``(item_id, user_answer)``, never on ``item_id`` alone: the
explanation is specific to the mistake the learner actually made
(``docs/04-application.md:391``), so two learners (or the same learner twice)
giving different wrong answers to the same item must never share a cached
explanation.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MODEL_LIVE, BankItem
from src.llm.cache import LlmCache
from src.llm.provider import LlmProvider, default_llm_provider

logger = logging.getLogger(__name__)


class ExplanationResult(BaseModel):
    """Pedagogical explanation for a learner mistake."""

    model_config = ConfigDict(frozen=True)
    item_id: str
    topic_id: str
    user_answer: str
    correct_answer: str
    explanation: str
    rule_summary: str = ""
    examples: list[str] = Field(default_factory=list)
    cache_hit: bool = False


class LiveExplainer:
    """Provides concise German grammar explanations tailored to learner mistakes."""

    SYSTEM_PROMPT = (
        "Du bist ein präziser, didaktischer Deutsch-Tutor. Erkläre dem Lernenden kurz "
        "und verständlich, warum seine Antwort falsch war und wie die zugrunde liegende "
        "Grammatikregel lautet. Bleibe sachlich und vermeide lange Abschweifungen."
    )

    def __init__(self, provider: LlmProvider | None = None, cache: LlmCache | None = None) -> None:
        # A learner just answered and is waiting on this explanation: it must
        # be synchronous, never silently fall over to a batch job. See
        # ``default_llm_provider``'s docstring for the lane-policy rationale.
        self.provider = provider or default_llm_provider(forbid_paid_lane=True)
        self.cache = cache or LlmCache()
        self.cache_hits = 0
        self.cache_misses = 0

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of ``explain_mistake`` calls served from cache, for measurement."""
        total = self.cache_hits + self.cache_misses
        return round(self.cache_hits / total, 4) if total else 0.0

    def explain_mistake(
        self,
        item: BankItem,
        user_answer: str,
    ) -> ExplanationResult:
        """Generate a targeted pedagogical explanation for an incorrect attempt.

        Cached on ``(item.id, user_answer)`` via the injected ``LlmCache``'s
        keyword parameters, not on the prompt text alone: the key is explicit
        and stable even if the prompt's exact wording changes later.
        """
        prompt = (
            f"Thema: {item.topic_id} (Niveau: {item.cefr})\n"
            f"Satz: {item.prompt}\n"
            f"Lernenden-Antwort: '{user_answer}'\n"
            f"Richtige Antwort: '{item.accepted_answers[0]}'\n"
            f"Regel-Hinweis: {item.rule_hint}\n\n"
            "Erkläre den Fehler in 2-3 prägnanten Sätzen auf Deutsch."
        )

        cached = self.cache.get(MODEL_LIVE, prompt, item_id=item.id, user_answer=user_answer)
        if cached is not None:
            self.cache_hits += 1
            cache_hit = True
            response_text = cached
        else:
            self.cache_misses += 1
            cache_hit = False
            response_text = self.provider.generate_text(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                purpose="explanation",
                is_user_content=True,  # carries the learner's wrong answer
            )
            self.cache.set(
                MODEL_LIVE, prompt, response_text, item_id=item.id, user_answer=user_answer
            )

        logger.info(
            "live_explainer cache %s item_id=%s hit_rate=%.4f",
            "hit" if cache_hit else "miss",
            item.id,
            self.cache_hit_rate,
        )

        return ExplanationResult(
            item_id=item.id,
            topic_id=item.topic_id,
            user_answer=user_answer,
            correct_answer=item.accepted_answers[0],
            explanation=response_text,
            rule_summary=item.rule_hint or "",
            cache_hit=cache_hit,
        )
