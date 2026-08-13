"""Free-form production sentence grader evaluating learner text against grammar constraints."""

import json

from pydantic import BaseModel, ConfigDict

from src.contracts import CEFR
from src.llm.provider import LlmProvider, MockLlmClient


class GradeResult(BaseModel):
    """Evaluation result of open-ended production response."""

    model_config = ConfigDict(frozen=True)
    passed: bool
    score: float  # 0.0 to 1.0
    feedback: str
    corrected_sentence: str | None = None


class ProductionGrader:
    """Evaluates free-form production exercises using LLM structured assessment."""

    SYSTEM_PROMPT = (
        "Du bist ein fairer Deutsch-Prüfer. Bewerte den Satz des Lernenden "
        "bezüglich grammatikalischer Korrektheit.\n"
        "Antworte ausschließlich im JSON-Format:\n"
        '{"passed": true, "score": 1.0, "feedback": "...", "corrected_sentence": null}'
    )

    def __init__(self, provider: LlmProvider | None = None) -> None:
        self.provider = provider or MockLlmClient()

    def grade_production(
        self,
        target_topic_id: str,
        cefr: CEFR,
        prompt_instruction: str,
        student_submission: str,
    ) -> GradeResult:
        """Grade student sentence submission."""
        prompt = (
            f"Grammatikthema: {target_topic_id} ({cefr})\n"
            f"Aufgabenstellung: {prompt_instruction}\n"
            f"Eingereichter Satz des Lernenden: '{student_submission}'\n\n"
            "Bewerte die Antwort."
        )

        response_text = self.provider.generate_text(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
        )

        try:
            parsed = json.loads(response_text)
            return GradeResult(
                passed=bool(parsed.get("passed", True)),
                score=float(parsed.get("score", 1.0)),
                feedback=str(parsed.get("feedback", "Gut gemacht.")),
                corrected_sentence=parsed.get("corrected_sentence"),
            )
        except Exception:
            # Fallback for plain text response
            return GradeResult(
                passed=True,
                score=1.0,
                feedback=response_text,
            )
