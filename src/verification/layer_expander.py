"""Answer-set expander: cheap contraction expansion plus the model-backed
semantic/collocation check docs/audits/stage-00-quota.md's routing table
calls "Answer-Set Expansion (Stage 4)".

Two independent responsibilities live here, both about the same question --
"is the accepted-answers set for this item right?" -- from opposite ends:

- ``expand_answers`` is free and deterministic: known contractions and
  sentence-initial capitalisation, so an equally-correct variant of the
  proposed answer is not rejected as wrong at review or grading time.
- ``verify_semantic_validity`` is the one model-backed layer in the chain
  (``gemini-3.7-flash`` / ``MODEL_VERIFY``, per the routing table), reserved
  for exactly the defect class no regex or closed-class table can catch:
  wrong connectors, broken collocations, hallucinated tokens, and carrier
  sentences that are structurally fine but semantically incoherent. See
  docs/audits/stage-04-pilot-2026-08-14.md, whose items 34, 35, 39, 40, 41,
  43 and 44 are all in this class -- the audit's finding was that this class
  accounted for the largest share of defects reaching acceptance specifically
  because this layer existed but was never wired into
  ``src.verification.pipeline.VerificationPipeline``.
"""

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import MODEL_VERIFY, CandidateItem

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient


class SemanticVerificationResult(BaseModel):
    """Verdict from the model-backed semantic/collocation check.

    ``valid=True`` with no ``additional_accepted_answers`` is also what a
    caller gets when the check could not run at all (no client configured,
    budget ceiling reached, unparseable response): a missing verdict must
    never reject an item that already passed every cheaper layer, so absence
    of signal degrades to "accept", never to "reject" -- the same fail-open
    posture ``ProductionGrader`` uses for its own model call.
    """

    model_config = ConfigDict(frozen=True)
    valid: bool = True
    reason: str | None = None
    additional_accepted_answers: list[str] = Field(default_factory=list)


class AnswerSetExpander:
    """Expands valid German grammatical variants and, when an LLM client is
    supplied, runs the model-backed semantic/collocation check."""

    CONTRACTION_MAP: dict[str, list[str]] = {
        "ans": ["an das"],
        "an das": ["ans"],
        "aufs": ["auf das"],
        "auf das": ["aufs"],
        "beim": ["bei dem"],
        "bei dem": ["beim"],
        "im": ["in dem"],
        "in dem": ["im"],
        "ins": ["in das"],
        "in das": ["ins"],
        "vom": ["von dem"],
        "von dem": ["vom"],
        "zum": ["zu dem"],
        "zu dem": ["zum"],
        "zur": ["zu der"],
        "zu der": ["zur"],
    }

    SYSTEM_PROMPT = (
        "Du bist ein erfahrener Deutschlehrer und prüfst Lückentext-Aufgaben "
        "gegen, nachdem sie bereits automatisch auf Struktur, Morphologie und "
        "Mehrdeutigkeit geprüft wurden. Deine einzige Aufgabe: beurteile, ob "
        "der VOLLSTÄNDIGE Satz (Lücke durch die vorgeschlagene Antwort "
        "ersetzt) für einen Muttersprachler semantisch kohärent, idiomatisch "
        "und inhaltlich sinnvoll ist -- und liste jede weitere Antwort, die "
        "ebenfalls grammatisch und inhaltlich korrekt wäre.\n\n"
        "Typische Fehler, auf die du achten musst: falsche Konnektoren "
        "(z.B. 'demnach' statt 'deshalb' oder 'folglich'), falsche "
        "Kollokationen (z.B. 'Kritik auf sich nehmen' statt 'auf sich "
        "ziehen'), erfundene oder unpassende Wörter, ungrammatische "
        "Konstruktionen, und inhaltlich widersprüchliche oder unlogische "
        "Sätze.\n\n"
        "Antworte ausschließlich im JSON-Format, ohne Markdown-Codeblock:\n"
        '{"valid": true, "reason": null, "additional_accepted_answers": []}\n'
        'Bei einem Fehler: {"valid": false, "reason": "kurze Begründung auf '
        'Deutsch", "additional_accepted_answers": []}'
    )

    @classmethod
    def expand_answers(cls, item: CandidateItem) -> list[str]:
        """Expand proposed answer into deduplicated accepted answers set."""
        accepted = [item.proposed_answer]
        raw_ans = item.proposed_answer.strip()

        # 1. Contraction expansion
        lower_ans = raw_ans.lower()
        if lower_ans in cls.CONTRACTION_MAP:
            for variant in cls.CONTRACTION_MAP[lower_ans]:
                if raw_ans[0].isupper():
                    expanded_v = variant.capitalize()
                else:
                    expanded_v = variant
                if expanded_v not in accepted:
                    accepted.append(expanded_v)

        # 2. Sentence initial position check
        if item.prompt.strip().startswith("___"):
            capitalized = raw_ans.capitalize()
            if capitalized not in accepted:
                accepted.append(capitalized)

        return accepted

    @classmethod
    def _build_user_prompt(cls, item: CandidateItem) -> str:
        filled = item.prompt.replace("___", item.proposed_answer)
        return (
            f"Aufgabe (mit Lücke): {item.prompt}\n"
            f"Vorgeschlagene Antwort: {item.proposed_answer}\n"
            f"Vollständiger Satz: {filled}\n\n"
            "Ist dieser Satz semantisch kohärent, idiomatisch und inhaltlich korrekt?"
        )

    @staticmethod
    def _parse_semantic_response(response_text: str) -> SemanticVerificationResult:
        """Parse the model's JSON verdict.

        Strips a markdown code fence first (the transport sends no
        ``response_mime_type``, so Gemini routinely wraps JSON in one -- see
        ``src.generation.batch_client._parse_response`` for the same fix
        against the same live behaviour). Any parse failure or unexpected
        shape degrades to ``valid=True`` rather than rejecting on a defect in
        this layer's own plumbing, not the candidate's German.
        """
        text = response_text.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.removesuffix("```").strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return SemanticVerificationResult()
        if not isinstance(payload, dict):
            return SemanticVerificationResult()

        valid = payload.get("valid", True)
        if not isinstance(valid, bool):
            valid = True

        reason = payload.get("reason")
        reason = reason if isinstance(reason, str) else None

        extra = payload.get("additional_accepted_answers")
        additional = [a for a in extra if isinstance(a, str)] if isinstance(extra, list) else []

        return SemanticVerificationResult(
            valid=valid, reason=reason, additional_accepted_answers=additional
        )

    @classmethod
    def verify_semantic_validity(
        cls, item: CandidateItem, llm_client: "GeminiLlmClient"
    ) -> SemanticVerificationResult:
        """Run the model-backed semantic/collocation check for one candidate.

        Every call goes through ``src.llm.client.GeminiLlmClient`` (CLAUDE.md
        rule 4): cost accounting, the spend ceiling, and the two-lane routing
        all apply exactly as they do to generation.

        Two errors this layer explicitly degrades on rather than propagates,
        both confirmed live against the real API, not hypothetical:

        - ``BudgetExceeded`` -- CLAUDE.md 9: "Callers handle it by degrading,
          never by retrying."
        - ``ServerUnavailableError`` -- transient 5xx overload that survives
          the transport's own bounded retries (``SERVER_ERROR_MAX_RETRIES``);
          observed live as a sustained-enough outage on ``gemini-3.7-flash``
          to exhaust them.

        Both are infrastructure trouble, not a verdict on the candidate's
        German, and this layer is a refinement on top of layers 1-4, not a
        required gate: either one skips the check rather than rejecting, or
        crashing the whole batch on, a candidate that already passed every
        free/cheap check.
        """
        from src.llm.client import BudgetExceeded, ServerUnavailableError

        prompt = f"{cls.SYSTEM_PROMPT}\n\n{cls._build_user_prompt(item)}"
        try:
            response_text = llm_client.generate(
                prompt, model=MODEL_VERIFY, purpose="answer_expansion"
            )
        except (BudgetExceeded, ServerUnavailableError):
            return SemanticVerificationResult()
        return cls._parse_semantic_response(response_text)
