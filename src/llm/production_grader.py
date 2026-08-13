"""Free-form production sentence grader evaluating learner text across 3 dimensions."""

import json

from pydantic import BaseModel, ConfigDict

from src.contracts import CEFR
from src.llm.provider import LlmProvider, MockLlmClient


class GradeResult(BaseModel):
    """Evaluation result of open-ended production response with 3-dimensional rubric."""

    model_config = ConfigDict(frozen=True)
    target_structure_used: bool  # ONLY this feeds FSRS rating
    grammatical_accuracy: float  # 0.0 to 1.0
    naturalness: float  # 0.0 to 1.0
    is_pass: bool
    feedback: str
    corrected_sentence: str | None = None


class ProductionGrader:
    """Evaluates free-form production exercises across 3 dimensions."""

    SYSTEM_PROMPT = (
        "Du bist ein didaktischer Deutsch-Prüfer. Bewerte den Satz des Lernenden "
        "strikt anhand von drei getrennten Dimensionen:\n"
        "1. target_structure_used (true/false): Wurde die geforderte Zielstruktur verwendet?\n"
        "2. grammatical_accuracy (0.0-1.0): Morphosyntax, Kasus, Verbflexion, Wortstellung.\n"
        "3. naturalness (0.0-1.0): Idiomatik und sprachliche Natürlichkeit.\n\n"
        "Antworte ausschließlich im JSON-Format:\n"
        '{"target_structure_used": true, "grammatical_accuracy": 1.0, "naturalness": 1.0, '
        '"is_pass": true, "feedback": "...", "corrected_sentence": null}'
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
        """Grade student sentence submission across 3 dimensions."""
        prompt = (
            f"Zielstruktur / Thema: {target_topic_id} ({cefr})\n"
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
            target_used = bool(parsed.get("target_structure_used", True))
            accuracy = float(parsed.get("grammatical_accuracy", 1.0))
            naturalness = float(parsed.get("naturalness", 1.0))
            is_pass = target_used and accuracy >= 0.75

            return GradeResult(
                target_structure_used=target_used,
                grammatical_accuracy=accuracy,
                naturalness=naturalness,
                is_pass=is_pass,
                feedback=str(parsed.get("feedback", "Gut gemacht.")),
                corrected_sentence=parsed.get("corrected_sentence"),
            )
        except Exception:
            return GradeResult(
                target_structure_used=True,
                grammatical_accuracy=1.0,
                naturalness=1.0,
                is_pass=True,
                feedback=response_text,
            )
