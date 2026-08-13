"""Verification pipeline orchestrating the quality verification chain and kill gate."""

from pydantic import BaseModel, ConfigDict, Field

from src.bank.dedup import ItemDeduplicator
from src.contracts import (
    VERIFICATION_KILL_GATE_THRESHOLD,
    BankItem,
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
from src.verification.layer_topic_leak import TopicLeakValidator


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

        self.layer1_syntax = Layer1SyntaxValidator(vocab_store=vocab_store)
        self.layer1_topic_leak = TopicLeakValidator()
        self.layer2_morphology = Layer2MorphologyValidator()
        self.layer3_solver = Layer3AdversarialSolver()

    def verify_item(
        self,
        item: CandidateItem,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
        existing_bank_items: list[BankItem] | None = None,
    ) -> VerificationResult:
        """Run candidate item through all verification layers."""
        effective_topic = topic or self.topics_map.get(item.topic_id)
        effective_spec = spec

        # Layer 1: Syntax, Structural Invariants & Topic Leak
        ok1, reason1 = self.layer1_syntax.validate(item, spec=effective_spec)
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

        ok1b, reason1b = self.layer1_topic_leak.validate(item, spec=effective_spec)
        if not ok1b:
            err_type = ErrorClassifier.classify(layer=1, reason=reason1b or "")
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=1,
                reason=reason1b,
                error_type=err_type,
            )

        # Layer 2: Morphosyntax & Agreement
        ok2, reason2 = self.layer2_morphology.validate(item, topic=effective_topic)
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

        # Layer 3: Adversarial Solver & Ambiguity
        ok3, reason3 = self.layer3_solver.validate(item)
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

        # Layer 4: Deduplication check
        if existing_bank_items:
            existing_prompts = [b_it.prompt for b_it in existing_bank_items]
            is_dup, _ = ItemDeduplicator.is_duplicate(item.prompt, existing_prompts)
            if is_dup:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=4,
                    reason="Duplicate sentence found in existing item bank",
                    error_type="duplicate",
                )

        return VerificationResult(
            item=item,
            passed=True,
            accepted=True,
        )

    def verify_batch(
        self,
        candidates: list[CandidateItem],
        specs: dict[str, TopicSpec] | None = None,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
        kill_gate_threshold: float = VERIFICATION_KILL_GATE_THRESHOLD,
        existing_bank_items: list[BankItem] | None = None,
    ) -> BatchVerificationReport:
        """Run batch of candidates through verification pipeline and evaluate Kill Gate."""
        if not candidates:
            return BatchVerificationReport(
                total_candidates=0,
                passed_count=0,
                failed_count=0,
                error_rate=0.0,
                kill_gate_tripped=False,
                results=[],
            )

        results: list[VerificationResult] = []
        passed_count = 0
        failed_count = 0

        for c in candidates:
            item_spec = spec or (specs.get(c.topic_id) if specs else None)
            item_topic = topic or self.topics_map.get(c.topic_id)
            res = self.verify_item(
                c, spec=item_spec, topic=item_topic, existing_bank_items=existing_bank_items
            )
            results.append(res)
            if res.passed:
                passed_count += 1
            else:
                failed_count += 1

        error_rate = round(failed_count / len(candidates), 4)
        kill_gate_tripped = error_rate > kill_gate_threshold

        return BatchVerificationReport(
            total_candidates=len(candidates),
            passed_count=passed_count,
            failed_count=failed_count,
            error_rate=error_rate,
            kill_gate_tripped=kill_gate_tripped,
            results=results,
        )
