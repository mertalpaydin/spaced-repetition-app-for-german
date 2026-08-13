"""Verification pipeline orchestrating 4 layers of quality gates and batch kill gate."""

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import (
    VERIFICATION_KILL_GATE_THRESHOLD,
    CandidateItem,
    Topic,
    VerificationResult,
)
from src.generation.spec import TopicSpec
from src.lexicon.vocabulary import VocabularyStore
from src.verification.classifier import ErrorClassifier
from src.verification.layer1_syntax import Layer1SyntaxValidator
from src.verification.layer2_morphology import Layer2MorphologyValidator
from src.verification.layer3_solver import Layer3AdversarialSolver


class BatchVerificationReport(BaseModel):
    """Aggregated quality report for a batch run."""

    model_config = ConfigDict(frozen=True)
    total_candidates: int
    passed_count: int
    failed_count: int
    error_rate: float
    kill_gate_tripped: bool
    results: list[VerificationResult] = Field(default_factory=list)


class VerificationPipeline:
    """Orchestrates candidate items through the multi-layer quality chain."""

    def __init__(
        self,
        vocab_store: VocabularyStore | None = None,
        topics: list[Topic] | None = None,
    ) -> None:
        self.vocab_store = vocab_store
        self.topics_map: dict[str, Topic] = {t.id: t for t in (topics or [])}
        if not self.topics_map:
            try:
                from src.taxonomy.loader import load_taxonomy

                self.topics_map = {t.id: t for t in load_taxonomy()}
            except Exception:
                self.topics_map = {}

        self.layer1 = Layer1SyntaxValidator(vocab_store=vocab_store)
        self.layer2 = Layer2MorphologyValidator()
        self.layer3 = Layer3AdversarialSolver()

    def verify_item(
        self,
        item: CandidateItem,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
    ) -> VerificationResult:
        """Run candidate item through all 4 verification layers."""
        effective_topic = topic or self.topics_map.get(item.topic_id)
        effective_spec = spec

        # Layer 1: Syntax & Structural Invariants
        ok1, reason1 = self.layer1.validate(item, spec=effective_spec)
        if not ok1:
            err_type = ErrorClassifier.classify(layer=1, reason=reason1 or "")
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=1,
                reason=reason1,
                error_type=err_type,
            )

        # Layer 2: Morphosyntax & Capitalisation
        ok2, reason2 = self.layer2.validate(item, topic=effective_topic)
        if not ok2:
            err_type = ErrorClassifier.classify(layer=2, reason=reason2 or "")
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=2,
                reason=reason2,
                error_type=err_type,
            )

        # Layer 3: Adversarial Solver & Ambiguity Check
        ok3, reason3 = self.layer3.validate(item)
        if not ok3:
            err_type = ErrorClassifier.classify(layer=3, reason=reason3 or "")
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=3,
                reason=reason3,
                error_type=err_type,
            )

        # Passed all layers
        return VerificationResult(
            item=item,
            passed=True,
            accepted=True,
            accepted_answers=[item.proposed_answer],
        )

    def verify_batch(
        self,
        items: list[CandidateItem],
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
    ) -> BatchVerificationReport:
        """Verify an entire candidate batch and check the 15% error rate kill gate."""
        results: list[VerificationResult] = []
        for it in items:
            res = self.verify_item(it, spec=spec, topic=topic)
            results.append(res)

        total = len(items)
        failed = sum(1 for r in results if not r.passed)
        passed = total - failed
        error_rate = (failed / total) if total > 0 else 0.0

        kill_gate_tripped = error_rate > VERIFICATION_KILL_GATE_THRESHOLD

        return BatchVerificationReport(
            total_candidates=total,
            passed_count=passed,
            failed_count=failed,
            error_rate=round(error_rate, 4),
            kill_gate_tripped=kill_gate_tripped,
            results=results,
        )
