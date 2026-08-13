"""Live interactive grammar explanation generator using LLM inference."""

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import BankItem
from src.llm.provider import LlmProvider, MockLlmClient


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


class LiveExplainer:
    """Provides concise German grammar explanations tailored to learner mistakes."""

    SYSTEM_PROMPT = (
        "Du bist ein präziser, didaktischer Deutsch-Tutor. Erkläre dem Lernenden kurz "
        "und verständlich, warum seine Antwort falsch war und wie die zugrunde liegende "
        "Grammatikregel lautet. Bleibe sachlich und vermeide lange Abschweifungen."
    )

    def __init__(self, provider: LlmProvider | None = None) -> None:
        self.provider = provider or MockLlmClient()

    def explain_mistake(
        self,
        item: BankItem,
        user_answer: str,
    ) -> ExplanationResult:
        """Generate targeted pedagogical explanation for an incorrect attempt."""
        prompt = (
            f"Thema: {item.topic_id} (Niveau: {item.cefr})\n"
            f"Satz: {item.prompt}\n"
            f"Lernenden-Antwort: '{user_answer}'\n"
            f"Richtige Antwort: '{item.accepted_answers[0]}'\n"
            f"Regel-Hinweis: {item.rule_hint}\n\n"
            "Erkläre den Fehler in 2-3 prägnanten Sätzen auf Deutsch."
        )

        response_text = self.provider.generate_text(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
        )

        return ExplanationResult(
            item_id=item.id,
            topic_id=item.topic_id,
            user_answer=user_answer,
            correct_answer=item.accepted_answers[0],
            explanation=response_text,
            rule_summary=item.rule_hint,
        )
