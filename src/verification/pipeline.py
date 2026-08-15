"""Verification pipeline orchestrating the quality verification chain and kill gate."""

from typing import TYPE_CHECKING, Literal

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
from src.verification.layer_expander import AnswerSetExpander
from src.verification.layer_topic_leak import TopicLeakValidator

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient

GateStatus = Literal["passed", "tripped", "unmeasured"]


class BatchVerificationReport(BaseModel):
    """Aggregated quality report for a batch run.

    Two distinct quantities are reported and must never be conflated:

    - ``rejection_rate``: the fraction of candidates the chain REJECTED. This is
      useful operational information (batch yield / drop rate) but says nothing
      about whether the chain is actually correct, since a chain that rejects
      every defective candidate scores a "100% rejection rate" on a fully
      defective batch, which is a *good* outcome, not a failure.
    - ``post_verifier_error_rate``: the fraction of ACCEPTED items that are
      still wrong, per docs/00-index.md:32 and docs/02-content-pipeline.md
      stage 4. This is the quantity the kill gate is actually defined over.
      The pipeline cannot determine on its own whether an accepted item is
      defective, so this is only computable when external audit labels are
      supplied (see ``VerificationPipeline.verify_batch``). When no labels are
      supplied, this field is ``None`` (never ``0.0``): a missing measurement
      must never silently read as a pass.
    """

    model_config = ConfigDict(frozen=True)
    total_candidates: int
    passed_count: int
    failed_count: int
    rejection_rate: float
    post_verifier_error_rate: float | None = None
    audited_accepted_count: int = 0
    kill_gate_tripped: bool | None
    gate_status: GateStatus
    results: list[VerificationResult] = Field(default_factory=list)


class VerificationPipeline:
    """Orchestrates candidate items through the multi-layer quality chain."""

    def __init__(
        self,
        vocab_store: VocabularyStore | None = None,
        topics: list[Topic] | None = None,
        llm_client: "GeminiLlmClient | None" = None,
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
        # Layer 5 (see verify_item) is model-backed and therefore optional:
        # ``None`` here (the default, and what every offline/golden test
        # uses) makes it a no-op rather than a hard dependency on a
        # configured API key.
        self.llm_client = llm_client

    def verify_item(
        self,
        item: CandidateItem,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
        existing_bank_items: list[BankItem] | None = None,
    ) -> VerificationResult:
        """Run candidate item through all verification layers.

        Canonical layer numbering (this is the numbering reported in
        ``VerificationResult.layer_failed`` and the one
        ``data/fixtures/verification/adversarial.jsonl`` is written against;
        docs/02-content-pipeline.md's 7-row table describes the conceptual
        stage-4 chain and does not map 1:1 onto these 4 code layers, since
        schema validation and topic-leak detection are both cheap, free
        checks that this implementation groups into a single layer 1 pass so
        that either one short-circuits before any paid layer runs):

        1. Syntax & structural invariants (``layer1_syntax``): gap
           presence/count, distractor shape, prompt token length, register,
           vocabulary ceiling (prompt AND accepted answer) -- plus topic-leak
           detection, both the blocklist check (``layer1_syntax``) and the
           answer-appears-in-prompt check (``layer1_topic_leak``). Both are
           free and deterministic, so both run before anything else.
        2. Morphosyntax & agreement (``layer2_morphology``): case, gender,
           and subject-verb agreement, driven by ``topic.morph_spec`` /
           ``topic.syntax_tags``.
        3. Adversarial solver & ambiguity (``layer3_solver``): distractor
           collisions and under-constrained gaps.
        4. Deduplication (``ItemDeduplicator``): only runs once an item has
           survived 1-3, since it is the one layer that needs the existing
           bank as input and gains nothing from running earlier.
        5. Semantic & answer-set expansion (``layer_expander.AnswerSetExpander``,
           model-backed, ``gemini-3.7-flash`` / ``MODEL_VERIFY``): wrong
           connectors, broken collocations, hallucinated tokens and
           incoherent carriers -- the defect class docs/audits/
           stage-04-pilot-2026-08-14.md identified as the largest single
           gap, and which no earlier, free layer can catch. Runs last,
           after every free layer including dedup, so an LLM call is never
           spent on a candidate a cheap check would have rejected anyway.
           A ``None`` ``self.llm_client`` (the default) makes this a no-op,
           not a hard dependency on a configured API key -- see ``__init__``.
        """
        effective_topic = topic or self.topics_map.get(item.topic_id)
        effective_spec = spec

        # Layer 1: Syntax, Structural Invariants & Topic Leak
        ok1, reason1, code1 = self.layer1_syntax.validate(item, spec=effective_spec)
        if not ok1:
            err_type = ErrorClassifier.classify(layer=1, reason=reason1 or "", code=code1)
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=1,
                reason=reason1,
                error_type=err_type,
            )

        ok1b, reason1b, code1b = self.layer1_topic_leak.validate(item, spec=effective_spec)
        if not ok1b:
            err_type = ErrorClassifier.classify(layer=1, reason=reason1b or "", code=code1b)
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=1,
                reason=reason1b,
                error_type=err_type,
            )

        # Layer 2: Morphosyntax & Agreement
        ok2, reason2, code2 = self.layer2_morphology.validate(item, topic=effective_topic)
        if not ok2:
            err_type = ErrorClassifier.classify(layer=2, reason=reason2 or "", code=code2)
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=2,
                reason=reason2,
                error_type=err_type,
            )

        # Layer 3: Adversarial Solver & Ambiguity
        ok3, reason3, code3 = self.layer3_solver.validate(item, topic=effective_topic)
        if not ok3:
            err_type = ErrorClassifier.classify(layer=3, reason=reason3 or "", code=code3)
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

        # Layer 5: Semantic & Answer-Set Expansion (model-backed, optional)
        if self.llm_client is not None:
            semantic = AnswerSetExpander.verify_semantic_validity(item, self.llm_client)
            if not semantic.valid:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=semantic.reason or "Semantic or collocation check failed.",
                    error_type="pedagogical_flaw",
                )
            accepted_answers = AnswerSetExpander.expand_answers(item)
            for extra in semantic.additional_accepted_answers:
                if extra not in accepted_answers:
                    accepted_answers.append(extra)
            return VerificationResult(
                item=item,
                passed=True,
                accepted=True,
                accepted_answers=accepted_answers,
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
        audit_labels: dict[str, bool] | None = None,
    ) -> BatchVerificationReport:
        """Run batch of candidates through verification pipeline and evaluate the kill gate.

        ``audit_labels`` is an optional mapping of item id (see ``_item_id``) to
        ``is_defective``, produced by an independent human or cross-vendor audit
        of a sample of ACCEPTED items (docs/02-content-pipeline.md stage 4, "Kill
        gate procedure"). The kill gate is driven exclusively by
        ``post_verifier_error_rate``, computed only over the labelled subset of
        accepted items, never by the rejection rate: a chain that rejects
        candidates correctly must never trip its own gate.

        When ``audit_labels`` is omitted, or labels no accepted item, the error
        rate is genuinely unmeasured: ``post_verifier_error_rate`` is ``None``,
        ``kill_gate_tripped`` is ``None``, and ``gate_status`` is
        ``"unmeasured"``. A missing measurement must never read as a pass.
        """
        if not candidates:
            return BatchVerificationReport(
                total_candidates=0,
                passed_count=0,
                failed_count=0,
                rejection_rate=0.0,
                post_verifier_error_rate=None,
                audited_accepted_count=0,
                kill_gate_tripped=None,
                gate_status="unmeasured",
                results=[],
            )

        results: list[VerificationResult] = []
        passed_count = 0
        failed_count = 0
        audited_accepted_count = 0
        defective_accepted_count = 0

        for idx, c in enumerate(candidates):
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

            if audit_labels is not None and res.accepted:
                item_id = self._item_id(c, idx)
                if item_id in audit_labels:
                    audited_accepted_count += 1
                    if audit_labels[item_id]:
                        defective_accepted_count += 1

        rejection_rate = round(failed_count / len(candidates), 4)

        post_verifier_error_rate: float | None
        kill_gate_tripped: bool | None
        gate_status: GateStatus
        if audit_labels is not None and audited_accepted_count > 0:
            post_verifier_error_rate = round(defective_accepted_count / audited_accepted_count, 4)
            kill_gate_tripped = post_verifier_error_rate > kill_gate_threshold
            gate_status = "tripped" if kill_gate_tripped else "passed"
        else:
            post_verifier_error_rate = None
            kill_gate_tripped = None
            gate_status = "unmeasured"

        return BatchVerificationReport(
            total_candidates=len(candidates),
            passed_count=passed_count,
            failed_count=failed_count,
            rejection_rate=rejection_rate,
            post_verifier_error_rate=post_verifier_error_rate,
            audited_accepted_count=audited_accepted_count,
            kill_gate_tripped=kill_gate_tripped,
            gate_status=gate_status,
            results=results,
        )

    @staticmethod
    def _item_id(item: CandidateItem, index: int) -> str:
        """Resolve a stable audit-label key for a candidate: its own ``id`` if
        the source data carried one (CandidateItem allows extra fields), else
        its position in the batch."""
        extra_id = item.model_extra.get("id") if item.model_extra else None
        if isinstance(extra_id, str) and extra_id:
            return extra_id
        return str(index)
