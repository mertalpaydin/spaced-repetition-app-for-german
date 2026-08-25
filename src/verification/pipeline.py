"""Verification pipeline orchestrating the quality verification chain and kill gate."""

from dataclasses import dataclass
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
from src.generation.gloss_validation import validate_gloss_consistency
from src.generation.spec import TopicSpec
from src.lexicon.vocabulary import VocabularyStore
from src.verification.classifier import ErrorClassifier
from src.verification.layer1_syntax import Layer1SyntaxValidator
from src.verification.layer2_morphology import Layer2MorphologyValidator
from src.verification.layer3_solver import Layer3AdversarialSolver
from src.verification.layer_expander import AnswerSetExpander, SemanticVerificationResult
from src.verification.layer_topic_leak import TopicLeakValidator
from src.verification.repair import repair_candidate

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient

GateStatus = Literal["passed", "tripped", "unmeasured"]

# Task 4 (cycle-2 report): near-duplicate detection on the carrier.
# ``ItemDeduplicator.is_duplicate`` (src/bank/dedup.py, not owned by this
# module) computes whole-prompt Jaccard token similarity at a 0.85
# threshold, which is right for near-paraphrases but blind to the actual
# defect found: "Ungeachtet ___ schlechten Wetters gingen die Kinder im
# Park spielen." and "Ungeachtet ___ schlechten Wetters unternahmen die
# Wanderer eine lange Tour." share the entire fixed carrier phrase around
# the gap verbatim and then diverge completely -- whole-sentence Jaccard on
# that pair is ~0.33 (5 shared tokens of a 15-token union), nowhere near
# 0.85, because the divergent tail dominates the bag-of-words comparison.
# The signal that actually matters is not "how much of the whole sentence
# matches" but "is the phrase immediately surrounding the blank a reused
# template" -- so this check measures the longest run of identical
# normalized tokens that CONTAINS the gap marker itself, independent of how
# the rest of the sentence reads.
#
# Threshold chosen: 4 tokens, including the gap marker itself (so at least
# 3 tokens of real surrounding text must match verbatim). Justification:
# German's free word order and productive compounding make a coincidental
# 3+ word verbatim run immediately around an independently-chosen blank
# position vanishingly unlikely across two independently generated
# sentences; in practice it only happens when the same fixed
# collocation/idiom was reused as the carrier, which is exactly the
# defect. A lower threshold (2, matching a bare subject-verb opener like
# "Er ging") risks flagging two genuinely distinct items that merely open
# the same common way, which the task explicitly warns against -- but this
# check is anchored on the GAP's own neighbourhood, not the sentence
# opening in general, which already rules out most "shares a common
# opening but the gap is elsewhere" false positives on its own; the run
# length is the second line of defence for the (rarer) case where the gap
# itself happens to sit inside a shared opening.
_CARRIER_NEAR_DUPLICATE_MIN_RUN = 4

#: The prefix every gloss-driven rejection's ``VerificationResult.reason``
#: carries, so a caller counting "how many items did the GLOSS reject" can
#: tell those apart from every other rejection reason without matching on
#: the free-text detail that follows. Exported rather than inlined because
#: ``scripts/step7_corpus_pilot.py`` reports exactly that number and must
#: not carry its own copy of this string.
GLOSS_REJECTION_PREFIX = "Gloss validation failed: "


@dataclass(frozen=True)
class GlossRejection:
    """One item's gloss-driven rejection: the ``reason`` (already carrying
    :data:`GLOSS_REJECTION_PREFIX`) and the ``error_type`` the more specific
    of the three gloss failure shapes maps to."""

    reason: str
    error_type: str


def check_gloss(
    prompt: str, answer: str, gloss_en: str | None, topic: Topic | None
) -> tuple[GlossRejection | None, int]:
    """Validate a PRESENT ``gloss_en`` against the German answer's own
    computed tense/person features, via ``gloss_validation.
    validate_gloss_consistency``.

    Returns ``(rejection_or_None, unverified_dimension_count)``. An absent or
    blank gloss is a clean no-op (``(None, 0)``) -- this function validates
    what is there and has no opinion on whether a gloss was REQUIRED (that is
    ``gloss_validation.check_requirement``'s separate, broader policy
    question). Free and deterministic: no network, no clock, no filesystem.

    Lifted out of ``VerificationPipeline._gloss_check`` (which now calls it)
    so that a caller which is NOT running the full verification chain --
    ``scripts/step7_corpus_pilot.py``, whose only model-backed pass is
    ``generation.blanking.model_verification.verify_items`` -- can run the
    identical check, with the identical reason prefix and the identical
    error-type mapping, instead of growing a second, drifting copy of it.
    """
    if gloss_en is None or not gloss_en.strip():
        return None, 0

    result = validate_gloss_consistency(prompt, answer, gloss_en, topic)
    if result.consistent:
        return None, len(result.unverified_dimensions)

    reason = result.reason or "Gloss is inconsistent with the answer."
    if "bare answer token" in reason:
        error_type = "answer_leak"
    elif "leaks grammar terminology" in reason:
        error_type = "topic_leak"
    else:
        error_type = "pedagogical_flaw"
    return GlossRejection(reason=f"{GLOSS_REJECTION_PREFIX}{reason}", error_type=error_type), 0


def _shared_gap_anchored_run(prompt_a: str, prompt_b: str) -> int:
    """Longest run of identical ``ItemDeduplicator.normalize_prompt``
    tokens shared between ``prompt_a`` and ``prompt_b`` that includes the
    gap marker itself, or ``0`` if either prompt has no gap.

    Anchoring on the gap position (rather than, say, the longest common
    substring anywhere in the two prompts) is deliberate: it is
    specifically the phrase immediately surrounding the BLANK -- the
    "carrier" -- that matters for this check, not any other part of the
    sentence the two items might coincidentally share.
    """
    tokens_a = ItemDeduplicator.normalize_prompt(prompt_a).split()
    tokens_b = ItemDeduplicator.normalize_prompt(prompt_b).split()
    # ``ItemDeduplicator.normalize_prompt`` lowercases the prompt BEFORE
    # substituting the gap marker, so the marker itself survives as the
    # uppercase literal "_GAP_", not "_gap_" -- matching its exact casing
    # here rather than re-deriving it keeps this function a pure consumer
    # of that module's own normalization, never a second implementation of it.
    gap_marker = "_GAP_"
    if gap_marker not in tokens_a or gap_marker not in tokens_b:
        return 0
    idx_a = tokens_a.index(gap_marker)
    idx_b = tokens_b.index(gap_marker)

    left = 0
    while (
        idx_a - left - 1 >= 0
        and idx_b - left - 1 >= 0
        and tokens_a[idx_a - left - 1] == tokens_b[idx_b - left - 1]
    ):
        left += 1

    right = 0
    while (
        idx_a + right + 1 < len(tokens_a)
        and idx_b + right + 1 < len(tokens_b)
        and tokens_a[idx_a + right + 1] == tokens_b[idx_b + right + 1]
    ):
        right += 1

    return left + right + 1


def _batch_internal_duplicate_reason(prompt: str, seen_prompts: list[str]) -> str | None:
    """Batch-internal counterpart of the existing-bank duplicate checks
    ``_verify_layers_1_to_4`` runs (exact/near-duplicate whole-prompt
    Jaccard via ``ItemDeduplicator.is_duplicate``, plus the gap-anchored
    carrier run via ``_shared_gap_anchored_run``): is ``prompt`` an exact or
    near duplicate of a prompt that has ALREADY been accepted earlier in
    the SAME batch. Two candidates minted together are both absent from the
    bank, so the existing-bank check alone never sees this case. Returns a
    human-readable reason, or ``None`` if ``prompt`` is distinct from
    everything in ``seen_prompts``.
    """
    is_dup, reason = ItemDeduplicator.is_duplicate(prompt, seen_prompts)
    if is_dup:
        return reason
    for other in seen_prompts:
        run = _shared_gap_anchored_run(prompt, other)
        if run >= _CARRIER_NEAR_DUPLICATE_MIN_RUN:
            return (
                f"Near-duplicate carrier: shares a {run}-token phrase around "
                f"the gap with another candidate already accepted earlier in "
                f"this batch ('{other}')."
            )
    return None


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

    A third quantity, ``gloss_unverified_count``, is a different kind of
    honesty problem from either of the above: it is not a rate over
    candidates at all, but the sum, across every candidate whose gloss was
    checked, of dimensions (tense, person) ``src.generation.
    gloss_validation`` could neither confirm nor contradict. This project
    has already had to correct one report that read "87 pass" when 14 of
    those had never actually been checked (docs/audits/
    generation-track-plan.md Cycle 3) -- this field exists so an
    UNVERIFIED gloss dimension is always visible as its own number, never
    folded into ``passed_count`` where it would read as a clean pass.
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
    gloss_unverified_count: int = 0


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

    def _gloss_check(
        self, item: CandidateItem, topic: Topic | None
    ) -> tuple[VerificationResult | None, int]:
        """Cycle 3 (docs/audits/generation-track-plan.md): mechanically
        validate ``item.gloss_en`` against the German answer's own computed
        tense/person features, via ``gloss_validation.
        validate_gloss_consistency``, whenever a gloss is actually present.

        Returns ``(rejection_or_None, unverified_dimension_count)``. Free
        and deterministic (no network, no morphology-heavy parsing beyond
        what the gloss module itself already does), so callers run it
        before the layers 1-4 chain, matching "cheap rejects happen first".

        A gloss that CONTRADICTS the answer's tense or person is a
        REJECTION, never a warning: a wrong gloss actively teaches the
        wrong thing, which is worse than no gloss at all (this exact
        wording is the task's own). A gloss that leaks grammar terminology
        or the bare answer token is rejected the same way, as a leak, not
        a consistency failure -- ``validate_gloss_consistency`` already
        distinguishes the two in its own ``reason`` text, matched here to
        pick the more specific ``error_type``.

        Whether a gloss is REQUIRED for this item at all
        (``gloss_validation.check_requirement``) is a separate, broader
        policy question this method does not enforce -- only a gloss that
        IS present gets validated. Enforcing the requirement would reject
        every existing tense/person-selecting-topic candidate that has no
        gloss at all, which is a real gap but a materially bigger, riskier
        change than "validate what's there"; left for a follow-up rather
        than folded in silently here.

        The second element of the return value is returned even when the
        item is NOT rejected, so the caller can attach it to whatever the
        item's eventual final ``VerificationResult`` turns out to be
        (``_with_gloss_unverified``), regardless of which later layer
        produces it -- an unverified dimension must never quietly vanish
        just because the item went on to pass everything else.

        The judgment itself lives in the module-level :func:`check_gloss`, so
        a caller outside this class (``scripts/step7_corpus_pilot.py``) can
        run the identical check without going through the whole chain; this
        method only wraps its verdict in a ``VerificationResult``.
        """
        rejection, unverified = check_gloss(item.prompt, item.proposed_answer, item.gloss_en, topic)
        if rejection is not None:
            return (
                VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=1,
                    reason=rejection.reason,
                    error_type=rejection.error_type,
                ),
                0,
            )
        return None, unverified

    @staticmethod
    def _with_gloss_unverified(result: VerificationResult, count: int) -> VerificationResult:
        """Attach ``count`` (from ``_gloss_check``) to an already-finalized
        ``VerificationResult`` without disturbing anything else about it.
        A no-op copy when ``count`` is 0, which is both the common case and
        harmless either way (``VerificationResult`` is frozen)."""
        if count == 0:
            return result
        return result.model_copy(update={"gloss_unverified_count": count})

    def verify_item(
        self,
        item: CandidateItem,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
        existing_bank_items: list[BankItem] | None = None,
    ) -> VerificationResult:
        """Run one candidate item through all verification layers.

        A thin single-item wrapper around ``_verify_layers_1_to_4`` +
        ``AnswerSetExpander.verify_semantic_validity``: ``verify_batch`` does
        NOT call this method per candidate (see its own docstring for why --
        it batches layer 5's model calls across the whole group instead).
        This method exists for standalone single-item verification (tests,
        one-off checks) where batching has nothing to batch against.
        """
        effective_topic = topic or self.topics_map.get(item.topic_id)
        # See verify_batch: repair runs at the entry point so the repaired
        # item is what gets returned, banked, and rendered.
        item, _ = repair_candidate(item)

        gloss_rejection, gloss_unverified = self._gloss_check(item, effective_topic)
        if gloss_rejection is not None:
            return gloss_rejection

        result = self._verify_layers_1_to_4(
            item, spec=spec, topic=effective_topic, existing_bank_items=existing_bank_items
        )
        if result is not None:
            return self._with_gloss_unverified(result, gloss_unverified)
        if self.llm_client is None:
            # Task 1: a candidate carrying its own pre-computed accepted
            # set (see ``AnswerSetExpander.get_computed_accepted_answers``)
            # must still go through ``_finalize_layer5`` even with no LLM
            # configured, so its computed set is finalized (ambiguity,
            # pronoun-class and distractor-collision checks still run)
            # rather than silently dropped -- an ordinary candidate with no
            # computed set is completely unaffected by this branch and
            # keeps the original bare fast path.
            if AnswerSetExpander.get_computed_accepted_answers(item) is not None:
                return self._with_gloss_unverified(
                    self._finalize_layer5(
                        item, SemanticVerificationResult(), topic=effective_topic, spec=spec
                    ),
                    gloss_unverified,
                )
            return VerificationResult(
                item=item, passed=True, accepted=True, gloss_unverified_count=gloss_unverified
            )
        semantic = AnswerSetExpander.verify_semantic_validity(item, self.llm_client)
        return self._with_gloss_unverified(
            self._finalize_layer5(item, semantic, topic=effective_topic, spec=spec),
            gloss_unverified,
        )

    def _verify_layers_1_to_4(
        self,
        item: CandidateItem,
        spec: TopicSpec | None = None,
        topic: Topic | None = None,
        existing_bank_items: list[BankItem] | None = None,
    ) -> VerificationResult | None:
        """Run the free, deterministic layers (1-4). Returns a final
        ``VerificationResult`` if the item is rejected by one of them, or
        ``None`` if it survives and is ready for layer 5 (the caller decides
        whether layer 5 runs at all, and whether to batch it across several
        survivors -- this method never touches ``self.llm_client``).

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
           answer-appears-in-prompt check (``layer1_topic_leak``), plus
           ``item.type in topic.eligible_types`` enforcement (a topic that
           cannot be honestly tested by a free cloze must not be accepted as
           one, docs/audits/stage-04-pilot-2026-08-15.md fix 5). All are free
           and deterministic, so all run before anything else.
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

        # Layer 1: eligible_types enforcement. Cheapest possible check (list
        # membership, no parsing), so it runs before even layer1_syntax.
        # 01-foundation.md's solvability rule / docs/audits/
        # stage-04-pilot-2026-08-15.md fix 5: "An item's type must be in its
        # topic's eligible_types... a topic that cannot be honestly tested by
        # a free cloze must not be generated as one." This is the backstop
        # for that rule -- generation is instructed to pick only from
        # eligible_types (see PromptBuilder.build_generation_prompt), but an
        # instruction is not a guarantee, and this check is what actually
        # enforces it regardless of whether generation complied.
        if effective_topic is not None and item.type not in effective_topic.eligible_types:
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=1,
                reason=(
                    f"Item type {item.type!r} is not in topic "
                    f"{effective_topic.id!r}'s eligible_types "
                    f"{effective_topic.eligible_types}."
                ),
                error_type="structural_malformation",
            )

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

            # Task 4: near-duplicate on the carrier -- see
            # ``_shared_gap_anchored_run``'s module-level docstring for the
            # defect this catches and the threshold's justification.
            for existing_prompt in existing_prompts:
                run = _shared_gap_anchored_run(item.prompt, existing_prompt)
                if run >= _CARRIER_NEAR_DUPLICATE_MIN_RUN:
                    return VerificationResult(
                        item=item,
                        passed=False,
                        accepted=False,
                        layer_failed=4,
                        reason=(
                            f"Near-duplicate carrier: shares a {run}-token phrase "
                            f"around the gap with an existing prompt ('{existing_prompt}')."
                        ),
                        error_type="duplicate",
                    )

        # Survived layers 1-4: layer 5 (model-backed, optional) is the
        # caller's decision -- see ``verify_item`` and ``verify_batch``.
        return None

    def _finalize_layer5(
        self,
        item: CandidateItem,
        semantic: SemanticVerificationResult,
        topic: Topic | None = None,
        spec: TopicSpec | None = None,
    ) -> VerificationResult:
        """Build the final ``VerificationResult`` for an item that survived
        layers 1-4, given its layer-5 semantic verdict. Shared by
        ``verify_item`` (one item) and ``verify_batch`` (a batched group),
        so the accept/reject logic itself cannot drift between the two.

        docs/audits/stage-04-pilot-2026-08-15.md fixes 1-4, and
        docs/audits/stage-04-a2-pilot-audit.md's four follow-on fixes, in
        the order applied here:

        2. **Constrain expansion to the target form.** An alternative that
           does not carry ``item.proposed_answer``'s own target-feature form
           is a different exercise, not a valid alternative -- dropped
           before it can ever reach the accepted set.
        4. **Validate expanded alternatives are real, correct words.** Any
           surviving alternative must pass the same vocabulary-ceiling and
           morphology checks a generated ``proposed_answer`` does, rather
           than being trusted because the model returned it. Dropped, not
           rejected: this is a repair pass.
        A. **Cue consistency** (a2-pilot-audit "rules that need to hold" 1):
           a cued item's WHOLE accepted set, not just the semantic layer's
           extras, must be forms of the cue's own lemma. A primary answer
           that itself contradicts its own cue is rejected outright as
           self-contradictory, before anything else runs.
        D. **Cue degree consistency.** A cue names the adjective's positive
           (citation) form; an accepted answer of a DIFFERENT degree
           (comparative/superlative) is a different word, the same category
           of defect as A applied to Degree instead of lexeme --
           "das ___ (groß) Fenster" accepting both "große" (Pos, correct)
           and "größte" (Sup, wrong) is the concrete pilot defect.
        B. **Filter the WHOLE accepted set by target form, not only the
           extras** (a2-pilot-audit's dominant finding, 11 of 17 audited
           defects: "the chain carefully vets the answers it adds, and
           waves through the ones it was handed" -- the generator's own
           base ``accepted_answers`` was never filtered at all). The primary
           answer is re-inserted unconditionally afterwards so this can
           never empty the set.
        E. **Possessive person agreement.** For a possessive-article topic,
           every accepted answer must agree in person/number with the
           possessor established in the carrier sentence -- "Anna kocht
           gern, weil ___ Küche sehr groß ist." accepting "ihre" (correct,
           Anna is 3rd singular) alongside "unsere" (1st plural) and "meine"
           (1st singular) tests three different possessors at once. Rejects
           outright as under-constrained when no single possessor can be
           established at all.
        C. **Unk-facet honesty** (a2-pilot-audit rule 2): if the topic's own
           target facet cannot be derived for this item's answer at all,
           and the set still has more than one member, reject as
           under-constrained rather than silently accept -- this is what
           actually catches "wohnt"/"wohnte" once B's tagger-based verb
           comparison still could not pin down a shared Tense.
        1. **Threshold on distinct forms, not on answer count.** Once the
           accepted set is built (contraction expansion + the alternatives
           that survived 2, 4, A, D, B, E, C), reject the whole item as
           ``ambiguity`` if it still spans more than one distinct form of
           the topic's target feature -- including a demonstrative form
           ("Dem") now distinguished from Def/Ind/Neg/Poss, closing the gap
           where a demonstrative read as compatible with everything.
        3. **Re-run the distractor check after expansion.** A distractor
           that collides with the FINAL accepted set means the item was
           under-constrained to begin with; reject rather than repair.

        Task 1 (cycle-2 report): a candidate carrying its own pre-computed,
        AUTHORITATIVE accepted set (``AnswerSetExpander.
        get_computed_accepted_answers``, e.g. every item the generate-then-
        blank pipeline produces) skips every fix above -- ``expand_answers``,
        A, D, B, E, C and the semantic layer's own
        ``additional_accepted_answers`` all exist to build or repair a
        HEURISTIC set from a single model-proposed answer, which is not
        what this is. See ``_finalize_computed_answer_set``.
        """
        if not semantic.valid:
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=5,
                reason=semantic.reason or "Semantic or collocation check failed.",
                error_type="pedagogical_flaw",
            )

        computed_answers = AnswerSetExpander.get_computed_accepted_answers(item)
        if computed_answers is not None:
            return self._finalize_computed_answer_set(item, computed_answers, topic)

        candidate_extras = semantic.additional_accepted_answers
        if topic is not None and candidate_extras:
            candidate_extras = AnswerSetExpander.filter_alternatives_by_target_form(
                candidate_extras, item, topic
            )
        candidate_extras = [
            a for a in candidate_extras if self._alternative_answer_is_valid(a, item, topic, spec)
        ]

        accepted_answers = AnswerSetExpander.expand_answers(item)
        for extra in candidate_extras:
            if extra not in accepted_answers:
                accepted_answers.append(extra)

        # Fix A: cue consistency, applied to the whole set. Reject outright
        # if the primary answer itself contradicts its own cue.
        if item.cue is not None:
            accepted_answers, cue_reason = AnswerSetExpander.filter_by_cue_consistency(
                accepted_answers, item
            )
            if cue_reason is not None:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=cue_reason,
                    error_type="pedagogical_flaw",
                )

        # Fix D: cue DEGREE consistency -- a cue's positive form and a
        # superlative/comparative accepted answer are different words, not
        # alternative inflections of the same one.
        if item.cue is not None:
            accepted_answers, degree_reason = AnswerSetExpander.filter_by_cue_degree(
                accepted_answers, item
            )
            if degree_reason is not None:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=degree_reason,
                    error_type="pedagogical_flaw",
                )

        # Fix B: filter the WHOLE accepted set by target form (docs/audits/
        # stage-04-a2-pilot-audit.md's dominant defect), not only the
        # extras as fix 2 above already does. The primary answer is kept
        # unconditionally so this can never empty the set.
        if topic is not None:
            accepted_answers = AnswerSetExpander.filter_alternatives_by_target_form(
                accepted_answers, item, topic
            )
            if item.proposed_answer not in accepted_answers:
                accepted_answers.insert(0, item.proposed_answer)

        # Fix E: possessive person/number agreement -- a no-op for every
        # topic that is not a possessive-article topic.
        if topic is not None:
            accepted_answers, person_reason = AnswerSetExpander.check_possessive_person_agreement(
                accepted_answers, item, topic
            )
            if person_reason is not None:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=person_reason,
                    error_type="ambiguity",
                )

        # Fix C: Unk-facet honesty -- reject as under-constrained rather
        # than silently accept when the topic's own facet cannot be
        # resolved for this answer at all and the set still has >1 member.
        if topic is not None:
            facet_reason = AnswerSetExpander.check_facet_derivability(accepted_answers, item, topic)
            if facet_reason is not None:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=facet_reason,
                    error_type="ambiguity",
                )

        # Task 5: a pronoun answer set must not span pronoun classes
        # (personal/demonstrative/interrogative) -- a no-op for every topic
        # that is not a pronoun topic.
        if topic is not None:
            pronoun_reason = AnswerSetExpander.check_pronoun_class_consistency(
                accepted_answers, topic
            )
            if pronoun_reason:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=pronoun_reason,
                    error_type="ambiguity",
                )

        if topic is not None:
            ambiguity_reason = AnswerSetExpander.check_ambiguity(accepted_answers, topic, item=item)
            if ambiguity_reason:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=ambiguity_reason,
                    error_type="ambiguity",
                )

        distractor_texts = {d.text.strip().lower() for d in item.distractors}
        collisions = sorted({a for a in accepted_answers if a.strip().lower() in distractor_texts})
        if collisions:
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=5,
                reason=(
                    "Expanded accepted answers collide with a distractor "
                    f"({', '.join(collisions)}), so the item was under-constrained."
                ),
                error_type="structural_malformation",
            )

        return VerificationResult(
            item=item,
            passed=True,
            accepted=True,
            accepted_answers=accepted_answers,
        )

    def _finalize_computed_answer_set(
        self,
        item: CandidateItem,
        accepted_answers: list[str],
        topic: Topic | None,
    ) -> VerificationResult:
        """Task 1 (cycle-2 report): finalize a candidate that carries its
        own pre-computed, authoritative accepted-answer set (see
        ``AnswerSetExpander.get_computed_accepted_answers``) -- the
        generate-then-blank pipeline's whole reason for existing is that
        its answer is OBSERVED and its accepted set is COMPUTED from a
        closed paradigm, not proposed by a model and heuristically
        widened. This finalizer therefore runs NONE of
        ``_finalize_layer5``'s expansion/widening machinery (contraction
        expansion, cue/degree/target-form filtering, possessive person
        agreement, Unk-facet honesty, or the semantic layer's
        model-proposed ``additional_accepted_answers``) -- every one of
        those exists to repair or gate a set this method's caller never
        built in the first place.

        What DOES still run, per this cycle's task: pronoun-class and
        form-identity ambiguity, and distractor collision. Dedup and
        vocabulary are already enforced earlier, in
        ``_verify_layers_1_to_4``, before this method is ever reached.
        """
        accepted_answers = list(accepted_answers)

        if topic is not None:
            pronoun_reason = AnswerSetExpander.check_pronoun_class_consistency(
                accepted_answers, topic
            )
            if pronoun_reason:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=pronoun_reason,
                    error_type="ambiguity",
                )

            ambiguity_reason = AnswerSetExpander.check_ambiguity(accepted_answers, topic, item=item)
            if ambiguity_reason:
                return VerificationResult(
                    item=item,
                    passed=False,
                    accepted=False,
                    layer_failed=5,
                    reason=ambiguity_reason,
                    error_type="ambiguity",
                )

        distractor_texts = {d.text.strip().lower() for d in item.distractors}
        collisions = sorted({a for a in accepted_answers if a.strip().lower() in distractor_texts})
        if collisions:
            return VerificationResult(
                item=item,
                passed=False,
                accepted=False,
                layer_failed=5,
                reason=(
                    "Computed accepted answers collide with a distractor "
                    f"({', '.join(collisions)}), so the item was under-constrained."
                ),
                error_type="structural_malformation",
            )

        return VerificationResult(
            item=item,
            passed=True,
            accepted=True,
            accepted_answers=accepted_answers,
        )

    def _alternative_answer_is_valid(
        self,
        alternative: str,
        item: CandidateItem,
        topic: Topic | None,
        spec: TopicSpec | None,
    ) -> bool:
        """Fix 4: an alternative the semantic expander proposes must pass the
        same vocabulary-ceiling and morphology checks a generated
        ``proposed_answer`` does, not be trusted on the model's word alone.
        Catches a hallucinated non-word (docs/audits/
        stage-04-pilot-2026-08-15.md item 20: "paratstünden", not a German
        word) exactly the same way a generated answer's own vocabulary
        violation is caught, plus any alternative that fails case, gender,
        or subject-verb agreement for this specific carrier sentence.
        """
        if self.vocab_store is not None and spec is not None:
            if self.vocab_store.validate_sentence(alternative, spec.vocabulary_ceiling):
                return False

        synthetic = item.model_copy(update={"proposed_answer": alternative})
        ok, _reason, _code = self.layer2_morphology.validate(synthetic, topic=topic)
        return ok

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

        Layers 1-4 run per candidate (cheap, no network). Layer 5, when
        ``self.llm_client`` is configured, is batched ONCE across every
        candidate that survives layers 1-4 (``AnswerSetExpander
        .verify_semantic_validity_many``), not called once per candidate in
        a loop -- this is what lets a whole pilot run's worth of semantic
        checks become one grouped free-lane dispatch or one paid-lane batch
        job instead of N serial calls. ``verify_item`` remains the
        single-item path (used directly by tests and one-off callers) and is
        NOT what this method calls per candidate.

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
                gloss_unverified_count=0,
            )

        # Repair before judging. docs/audits/stage-04-recovery-plan.md fix A:
        # a sloppy distractor list is metadata noise, not a defective item,
        # and rejecting the carrier sentence to punish it discarded 30 correct
        # items in batch_51fc18e48f7b. Done HERE rather than inside
        # ``_verify_layers_1_to_4``, because that method returns ``None`` on
        # success and so has no way to hand the repaired item back -- the
        # original, still carrying its answer among its distractors, would be
        # what reached the bank.
        candidates = [repair_candidate(c)[0] for c in candidates]

        # Resolved once, reused by both passes so layer 5 sees the exact same
        # topic/spec layers 1-4 were checked against.
        resolved_specs = [spec or (specs.get(c.topic_id) if specs else None) for c in candidates]
        resolved_topics = [topic or self.topics_map.get(c.topic_id) for c in candidates]

        # Pass 0: gloss validation, per candidate -- free and deterministic
        # (see ``_gloss_check``), so it runs before layers 1-4 spend any
        # more effort. A rejection here is final immediately; a survivor's
        # unverified-dimension count is carried forward and attached to
        # whatever its eventual VerificationResult turns out to be, however
        # many further layers it passes through.
        gloss_checks = [self._gloss_check(c, resolved_topics[i]) for i, c in enumerate(candidates)]
        gloss_unverified_counts = [count for _rejection, count in gloss_checks]

        # Pass 1: layers 1-4, per candidate. Either a final (rejected) result,
        # or None meaning "survived, needs layer 5". A candidate the gloss
        # check already rejected skips layers 1-4 entirely.
        pending: list[VerificationResult | None] = [
            gloss_checks[i][0]
            if gloss_checks[i][0] is not None
            else self._verify_layers_1_to_4(
                c,
                spec=resolved_specs[i],
                topic=resolved_topics[i],
                existing_bank_items=existing_bank_items,
            )
            for i, c in enumerate(candidates)
        ]
        survivor_indices = [i for i, res in enumerate(pending) if res is None]

        # Pass 1.5: batch-internal near-duplicate detection. The layer-4
        # dedup check inside ``_verify_layers_1_to_4`` only ever compares a
        # candidate against ``existing_bank_items`` -- items already
        # committed to the bank from a PRIOR run. Two candidates minted in
        # the SAME batch are invisible to each other there, so a generation
        # run that (by model repetition, or two topics drawing the same
        # carrier) produces the same exercise twice in one batch previously
        # sailed both copies through untouched. First-survives-wins, in
        # batch order: the earliest candidate to reach this point is
        # trusted, and every later one that is an exact or near duplicate
        # of it (same two checks ``_verify_layers_1_to_4`` runs against the
        # bank: whole-prompt Jaccard via ``ItemDeduplicator.is_duplicate``,
        # and the gap-anchored carrier run via ``_shared_gap_anchored_run``)
        # is rejected here, before layer 5 ever sees it.
        seen_prompts: list[str] = []
        kept_survivor_indices: list[int] = []
        for i in survivor_indices:
            dup_reason = _batch_internal_duplicate_reason(candidates[i].prompt, seen_prompts)
            if dup_reason is not None:
                pending[i] = VerificationResult(
                    item=candidates[i],
                    passed=False,
                    accepted=False,
                    layer_failed=4,
                    reason=dup_reason,
                    error_type="duplicate",
                )
            else:
                seen_prompts.append(candidates[i].prompt)
                kept_survivor_indices.append(i)
        survivor_indices = kept_survivor_indices

        # Pass 2: layer 5, batched once across every survivor.
        if survivor_indices and self.llm_client is not None:
            survivors = [candidates[i] for i in survivor_indices]
            semantics = AnswerSetExpander.verify_semantic_validity_many(survivors, self.llm_client)
            for i, semantic in zip(survivor_indices, semantics, strict=True):
                pending[i] = self._finalize_layer5(
                    candidates[i], semantic, topic=resolved_topics[i], spec=resolved_specs[i]
                )
        else:
            for i in survivor_indices:
                # Task 1: even with no LLM configured, a candidate carrying
                # its own pre-computed accepted set must still be finalized
                # through ``_finalize_layer5`` (ambiguity, pronoun-class and
                # distractor-collision checks), not waved through bare --
                # see ``verify_item``'s identical branch for the same
                # reasoning.
                if AnswerSetExpander.get_computed_accepted_answers(candidates[i]) is not None:
                    pending[i] = self._finalize_layer5(
                        candidates[i],
                        SemanticVerificationResult(),
                        topic=resolved_topics[i],
                        spec=resolved_specs[i],
                    )
                else:
                    pending[i] = VerificationResult(item=candidates[i], passed=True, accepted=True)

        assert all(res is not None for res in pending), (
            "every candidate must have a final VerificationResult after layers 1-5"
        )
        results: list[VerificationResult] = [
            self._with_gloss_unverified(res, gloss_unverified_counts[i])
            for i, res in enumerate(pending)
            if res is not None
        ]

        passed_count = 0
        failed_count = 0
        audited_accepted_count = 0
        defective_accepted_count = 0
        gloss_unverified_total = sum(gloss_unverified_counts)

        for idx, (c, res) in enumerate(zip(candidates, results, strict=True)):
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
            gloss_unverified_count=gloss_unverified_total,
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
