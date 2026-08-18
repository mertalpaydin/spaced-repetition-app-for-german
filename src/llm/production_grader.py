"""Free-form production sentence grader evaluating learner text across 3 dimensions."""

import json

from pydantic import BaseModel, ConfigDict

from src.contracts import CEFR
from src.llm.provider import LlmProvider, default_llm_provider


class GradeResult(BaseModel):
    """Evaluation result of open-ended production response with 3-dimensional rubric.

    Only ``target_structure_used`` feeds FSRS: ``is_pass`` is defined to equal
    it exactly. ``grammatical_accuracy`` and ``naturalness`` are shown to the
    learner as feedback and never gate the verdict -- a response that nails
    the target structure with a word-order slip must still pass the schedule,
    and a fluent response that dodges the target structure must still fail it.
    """

    model_config = ConfigDict(frozen=True)
    target_structure_used: bool  # ONLY this feeds FSRS rating
    grammatical_accuracy: float  # 0.0 to 1.0, feedback only
    naturalness: float  # 0.0 to 1.0, feedback only
    is_pass: bool  # == target_structure_used; the only dimension that reaches FSRS
    feedback: str
    corrected_sentence: str | None = None
    requires_self_assessment: bool = False  # True when no verdict could be produced


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
        # Grades the learner's just-submitted text while they wait on the
        # verdict: synchronous, never a silent batch fallback. See
        # ``default_llm_provider``'s docstring for the lane-policy rationale.
        self.provider = provider or default_llm_provider(forbid_paid_lane=True)

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

        try:
            response_text = self.provider.generate_text(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                purpose="production_grading",
                is_user_content=True,  # carries the learner's submitted sentence
            )
            parsed = json.loads(response_text)
            target_used = bool(parsed.get("target_structure_used", True))
            accuracy = float(parsed.get("grammatical_accuracy", 1.0))
            naturalness = float(parsed.get("naturalness", 1.0))

            # Only the target-structure dimension feeds FSRS. Accuracy and
            # naturalness are surfaced as feedback but never gate the verdict.
            is_pass = target_used

            return GradeResult(
                target_structure_used=target_used,
                grammatical_accuracy=accuracy,
                naturalness=naturalness,
                is_pass=is_pass,
                feedback=str(parsed.get("feedback", "Gut gemacht.")),
                corrected_sentence=parsed.get("corrected_sentence"),
            )
        except Exception:
            # The grader could not produce a verdict (network failure or
            # malformed model output). Never silently pass the learner at a
            # perfect score: degrade to self-assessment instead, and never
            # block the round. The caller is expected to show a model answer
            # and let the learner grade themselves.
            return self._self_assessment_fallback()

    @staticmethod
    def _self_assessment_fallback() -> GradeResult:
        """Degrade to learner self-assessment when no verdict can be produced."""
        return GradeResult(
            target_structure_used=False,
            grammatical_accuracy=0.0,
            naturalness=0.0,
            is_pass=False,
            feedback=(
                "Automatische Bewertung nicht verfügbar. Bitte vergleiche deine Antwort "
                "selbst mit einer Musterlösung."
            ),
            requires_self_assessment=True,
        )
