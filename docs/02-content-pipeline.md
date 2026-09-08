# 02: Content Pipeline (Stages 3 to 5)

> **STALE as of 2026-09-08.** This describes the German grammar trainer, which was shut down and pruned on `feat/phrase-deck`. Kept for the reasoning only. The current project is the phrase trainer; read `docs/project-state.md`.
>
> **Design record, written before most of the code existed. Not a description
> of what the code does now, and the furthest from it of the five.**
>
> The corpus pipeline that makes every exercise today, and the English
> translation shown under every one, are absent from these contracts entirely:
> neither "blank" nor "Azure" appears in this document. `docs/project-state.md`
> has both.
>
> Named departures: stage 3's model table lists a "topic-leak check" model call,
> and there is no such call, only a deterministic blocklist
> (`src/verification/layer_topic_leak.py`); it says item generation runs with
> thinking off, and every Flash-Lite workload now runs `low`; stage 4's
> seven-layer chain does not match the five the code ships
> (`src/verification/pipeline.py`). Its Definition-of-Done boxes are unticked
> because nobody ticked them, not because the work is undone.

This document contains the kill gate. Stage 4 decides whether the project continues.

---

## Stage 3: Generation

**Branch:** `stage/03-generation`

### Goal

Turn a topic plus a deficit count into candidate items, via the batch endpoint, at controlled cost.

### Deliverables

- `data/specs/<topic_id>.yaml`: one generation spec sheet per topic
- Prompt builder: spec plus seed sentences to a structured request
- Batch client: submit, poll, retrieve, with idempotency
- Cost accounting on every call

### Models

| Step | Model | Mode | Thinking |
|---|---|---|---|
| Item generation | `gemini-3.5-flash-lite` | batch | **off** |
| Topic-leak check | `gemini-3.5-flash-lite` | batch | off |
| Answer-set expansion | `gemini-3.7-flash` | batch | low or medium |

Thinking tokens bill as output. Generation runs with thinking off; leaving it on multiplies the largest cost line. The verification thinking level is a config value, tuned against measured recall on the adversarial fixture rather than fixed once.

### Spec sheet contract

```yaml
topic_id: dativ_nach_praeposition
cefr: A2
target_form: "Dativ article or pronoun following a Wechselpräposition in a static reading"
item_types: [cloze_free, cloze_cued, error_correction]   # from topic.eligible_types
max_tokens_per_sentence: 14
vocabulary_ceiling: A2
difficulty_tiers:
  1: "one clause, present tense, concrete noun"
  2: "one subordinate clause, past tense permitted"
  3: "embedded clause, an Akkusativ distractor structure elsewhere in the sentence"
forbidden:
  - "any grammatical terminology in the prompt"
  - "any instruction naming case, tense, or mood"
  - "the answer appearing elsewhere in the sentence"
gold_examples:
  - prompt: "Das Buch liegt auf ___ Tisch."
    accepted_answers: ["dem"]
    difficulty: 1
  # three minimum [AGENT-CRITICAL]
```

**[AGENT-CRITICAL]** Gold examples are the few-shot anchor and double as a smoke test that the spec is coherent. A leaky or wrong gold example teaches the generator to leak across every item that topic ever produces. External anchor where available: a human-authored item scraped in stage 2c.

### Paragraph cloze generation

Paragraph blocks are generated differently: a short coherent text of 60 to 110 words with 3 to 5 gaps, each gap tagged to exactly one topic.

```yaml
block_request:
  primary_topic: konjunktiv_i_indirekte_rede   # the requires_context topic
  filler_topics: [perfekt_haben_sein, adjektivendungen_bestimmt]
  gap_count: 4
  cefr: B2
```

Rules the generator must satisfy:

- The primary topic is the reason the block exists. Filler gaps ride along, which is what keeps block count low.
- **No two adjacent gaps share a `tag_id`**, so the interleaving invariant holds inside the block.
- The carrier text must genuinely require context: if every gap is answerable from its own sentence alone, the block has no reason to exist and should be rejected.
- Gaps are free text. Three distractors per gap are generated at ingest and stored for hint level 2; they are never generated at runtime.

### Generation contract

```python
class GenerationRequest(BaseModel):
    topic_id: str
    count: int
    difficulty: Difficulty
    item_types: list[ItemType]
    seed_sentence_ids: list[str]

class CandidateItem(BaseModel):
    topic_id: str
    type: ItemType
    difficulty: Difficulty
    prompt: str
    cue: str | None
    proposed_answer: str
    distractors: list[Distractor]   # exactly 3, for hint level 2
    block_id: str | None            # set for paragraph_cloze gaps
    block_position: int | None
    source_sentence_id: str | None

class BatchClient(Protocol):
    def submit(self, requests: list[GenerationRequest]) -> BatchId: ...
    def poll(self, batch_id: BatchId) -> BatchStatus: ...
    def retrieve(self, batch_id: BatchId) -> list[CandidateItem]: ...
```

All generation is batch. A synchronous generation call is a defect. Batch is roughly half price and 24-hour latency is irrelevant to a pipeline that maintains 14 days of stock.

### Tests

```python
@pytest.mark.golden
def test_prompt_construction_is_stable()
    # fixed spec + fixed seed ids -> byte-identical prompt.
    # Prompt drift silently changes item quality; this catches it in review.

def test_prompt_never_contains_grammar_terminology()
    # parameterised over every spec sheet in data/specs/ and a blocklist
    # (Dativ, Akkusativ, Konjunktiv, Perfekt, case, tense, mood, ...)
    # This is stage 3's half of the topic-leak defence.

def test_every_spec_sheet_validates_and_has_three_gold_examples()

def test_every_topic_in_taxonomy_has_a_spec_sheet()

def test_spec_item_types_are_a_subset_of_topic_eligible_types()
    # a spec sheet may not request a type the taxonomy says
    # cannot honestly test that topic

def test_requires_context_topics_only_request_paragraph_blocks()

def test_paragraph_block_has_no_two_adjacent_gaps_with_same_tag()

def test_paragraph_block_rejected_if_every_gap_is_context_free()
    # mask the rest of the text and check each gap in isolation.
    # if all remain answerable, the block earns nothing over
    # single sentences and must be rejected.

def test_every_candidate_carries_exactly_three_distractors()

def test_every_distractor_carries_an_implied_topic_or_explicit_null()
    # the confusion matrix is built from these; a silent null loses
    # the signal without anything failing

def test_implied_topic_id_resolves_to_a_real_taxonomy_topic()

def test_at_least_one_distractor_per_item_implies_a_confusion_sibling()
    # a distractor implying an unrelated topic teaches nothing about
    # the confusion the item is positioned against

def test_no_error_items_generated_at_the_configured_share()

def test_no_error_items_pass_a_whole_sentence_error_check()
    # not just the topic's own error type. A no-error item containing
    # any error is the worst possible item in the bank.
    # hint level 2 must never trigger a runtime generation call

def test_distractors_are_not_in_accepted_answers()

def test_gold_examples_do_not_leak_topic()
    # the gold examples are few-shot anchors; a leaky example
    # teaches the model to leak

def test_batch_submit_is_idempotent()
    # resubmitting the same request set returns the existing batch id
    # and makes no second API call

def test_batch_poll_retries_on_429_with_backoff()
    # mock transport; assert backoff schedule, assert bounded attempts

def test_batch_retrieve_on_incomplete_batch_returns_pending_not_partial()
    # partial ingestion would corrupt deficit accounting

def test_malformed_model_output_is_rejected_not_coerced()
    # a candidate missing accepted answers is dropped and counted,
    # never defaulted

def test_generation_never_uses_synchronous_endpoint()
    # AST scan: no non-batch call path reachable from src/generation/

def test_cost_recorded_per_batch_with_token_counts()

@pytest.mark.live
def test_batch_round_trip_small()
    # 5 items, real endpoint, nightly only
```

### Definition of Done

- [ ] Spec sheets for every topic in the taxonomy
- [ ] Batch submit, poll, retrieve working against a mock and against the live endpoint
- [ ] Prompt golden test committed
- [ ] Cost log populated with real token counts from the live round trip

---

### Distractors carry their implied topic

```python
class Distractor(BaseModel):
    text: str
    implied_topic_id: str | None   # the topic under which this would be correct
```

`den` as a distractor for a Dativ gap implies `kasus_akkusativ`. Recording that at ingest is what lets a wrong answer be classified **in the browser, offline, with no parser and no network**. Client-side morphological analysis is not possible; this is the substitute, and it must be complete at ingest.

Free-text wrong answers outside the distractor set are logged unclassified and analysed server-side on the next sync.

### Error-correction items with no error

`NO_ERROR_ITEM_SHARE` of error-correction items are correct as written, and "no error" is an available answer. Without them the item type teaches a false prior: find something to change.

**Verification is stricter on these.** A "no error" item that actually contains an error is the single worst artefact the bank can hold, because it punishes the learner for being right and teaches the wrong rule. They pass the full chain plus an explicit check for any error of any type, not merely the topic's own.

---

## Stage 4: Verification chain (KILL GATE)

**Branch:** `stage/04-verification`

### Goal

Reject bad items before they reach the bank. This is the highest-risk component in the project and the only one with a numeric threshold that stops the work.

### The chain

Ordered cheapest-reject-first.

| # | Stage | Catches | Cost |
|---|---|---|---|
| 1 | Schema validation | Malformed output | Free |
| 2 | Topic-leak check | Prompts naming the grammar point | Blocklist free, model pass cheap |
| 3 | Answer-set expansion | Valid alternative answers being marked wrong | One model call per item, batched |
| 4 | Morphology check | Answer does not carry the claimed features | Free, spaCy |
| 5 | Level check | Vocabulary above the target level | Free, lexicon |
| 6 | Deduplication | Near-identical items | Embedding, cheap |
| 7 | Human audit | Everything else | 5% sample |

### Contract

```python
class VerificationResult(BaseModel):
    item: CandidateItem
    accepted: bool
    accepted_answers: list[str]      # populated by stage 3 of the chain
    rejections: list[RejectionReason]

RejectionReason = Literal[
    "schema", "topic_leak", "ambiguous_answer", "morphology_mismatch",
    "level_violation", "duplicate", "answer_in_prompt",
]

class Verifier(Protocol):
    def verify(self, items: list[CandidateItem]) -> list[VerificationResult]: ...
```

Every rejection is recorded with its reason. Rejection-reason distribution per topic is a diagnostic: a topic rejecting 60% on `ambiguous_answer` has a bad spec sheet, not a bad model.

**The answer-set expander rejects; it does not widen.** Its purpose is to discover whether an item admits more than one answer, and an item that does is ambiguous and must be rejected. Appending every alternative the model returns to `accepted_answers` inverts the layer: it converts a rejection into an item that accepts anything and therefore tests nothing. An item tagged `futur_i` that accepts `kann` produces a pass which is not evidence the learner knows Futur I, and `accepted_answers` is what the grader compares against.

**Threshold on distinct forms, not on answer count.** Ten answers are fine when they are ten lexemes carrying one form: `des, eines, meines, deines` are all genitive, and the tested feature is unique. Two answers are fatal when they are two forms: `werden` and `können` are a future auxiliary and a modal. Map each candidate answer to its morphological form using the taxonomy paradigm tables, and reject as `ambiguity` when the accepted set spans more than one form of the topic's target feature.

**Expansion is constrained to the target form.** The expander must be given the topic's `morph_spec` and the spec sheet's target form, and must drop any returned alternative that does not carry it. An alternative that changes what is being tested is not an alternative answer, it is a different exercise.

**Distractors are validated after expansion, not before.** A structural check that runs before the answer set is widened will pass items whose distractors later become correct answers. Re-run it against the final `accepted_answers`, and reject on collision rather than repairing: a distractor that is also correct means the item was under-constrained to begin with.

**An item's type must be in its topic's `eligible_types`.** See the solvability rule in `01-foundation.md` stage 1. A topic that cannot be honestly tested by a free cloze must not be generated as one, and the spec sheet for any topic that keeps `cloze_free` must name the element in the carrier that forces the answer.

### Tests

The core of this stage is two fixture sets. **[AGENT-CRITICAL]**, and built before the implementation so the chain cannot be tuned to its own test set.

**`data/fixtures/verification/adversarial.jsonl`**: 60 items, at least 8 per rejection reason, each labelled with the defect it contains. Deliberately broken items are the one fixture a model produces well, since the defect is specified up front rather than judged. The second agent confirms each item carries the labelled defect and no other. Examples:

- A cloze where both `dem` and `den` are grammatically valid
- A prompt reading "Setze das Verb ins Perfekt" (topic leak, explicit)
- A prompt reading "Wo liegt das Buch? Das Buch liegt auf ___ Tisch." (topic leak, subtle: the "Wo" question gives away the Dativ)
- An answer key that is the wrong case
- A B2-band noun in an A1 item
- Two items that are the same sentence with one noun swapped

**`data/fixtures/verification/known_good.jsonl`**: 60 correct items spread across topics and types. **Source these from scraped answer keys wherever possible.** Teacher-authored items with published keys are external ground truth; agent-generated "known good" items are only agreement with the generator.

```python
@pytest.mark.golden
def test_adversarial_recall_per_reason()
    # for each rejection reason, assert the chain catches >= 90% of the
    # adversarial items carrying that defect. Report per-reason, not aggregate:
    # 95% aggregate can hide 40% recall on topic_leak.

@pytest.mark.golden
def test_false_positive_rate_on_known_good_below_ten_percent()
    # an over-eager verifier destroys yield and inflates cost.
    # Both failure directions are measured.

def test_topic_leak_blocklist_covers_every_grammar_term_in_taxonomy_names()
    # derive the blocklist from topic name_de fields so it cannot drift
    # out of sync with the taxonomy

def test_subtle_topic_leak_via_question_word_is_caught()
    # "Wo ..." preceding a Wechselpräposition gap gives away Dativ.
    # Explicit test because a blocklist alone will not catch it.

def test_answer_set_expansion_returns_superset_of_proposed_answer()
    # if expansion drops the original answer, the item is broken,
    # not merely ambiguous

def test_answer_set_expansion_flags_ambiguity_above_threshold()
    # more than N valid fillers where the topic expects one -> reject

def test_morphology_check_uses_topic_morph_spec()
    # parameterised over taxonomy: for each topic with non-empty morph_spec,
    # a correct answer passes and a wrong-case answer fails

def test_morphology_check_skipped_explicitly_for_empty_morph_spec()
    # assert it is skipped, not silently passed

def test_answer_never_appears_elsewhere_in_prompt()

def test_deduplication_threshold_separates_paraphrase_from_duplicate()
    # golden pairs: labelled duplicate / not-duplicate,
    # assert the threshold classifies all correctly

def test_chain_is_idempotent()
    # running twice over the same input yields identical accept/reject sets

def test_chain_short_circuits_on_cheap_rejection()
    # an item failing schema never reaches the answer-expansion API call.
    # Directly protects the budget.

def test_rejection_reasons_recorded_for_every_rejected_item()
```

### On auditing an automated pipeline with automated auditors

The kill gate exists to be an *independent* check. Two agents from the same model family share failure modes, so their agreement is weaker evidence than it looks, and an error correlated with the generator can pass unnoticed.

Three mitigations, strongest first:

1. **External keys.** Scraped exercises carry answer keys written by German teachers with no relationship to this pipeline. Measuring the chain against them is the only genuinely independent signal available.
2. **A different model family for the second agent.**
3. **Both audit scores recorded, never averaged.** A wide gap between auditors is a finding about the auditors and belongs in the audit document.

State this limitation in the audit document rather than implying the number is stronger than it is.

### Kill gate procedure

1. Run the full chain over one confusion group, recommended `kasus_wechselpraeposition`.
2. Sample 100 accepted items at random.
3. Audit each **[AGENT-CRITICAL]**: is the prompt topic-agnostic, is the answer set complete and correct, is the level right. Two agents audit independently and both scores are recorded; a gap between them is itself a finding.
4. Compute post-verifier error rate.

**Above roughly 15%: stop. Do not proceed to stage 5.** Diagnose from the rejection-reason distribution, revise spec sheets or chain, re-audit.

Record the audit in `docs/audits/stage-04-<date>.md` with per-defect breakdown, and publish the headline number in the README. Do not tune the audit sample to pass.

### Definition of Done

- [ ] Both fixture sets committed **[AGENT-CRITICAL]**, with second-agent diffs
- [ ] Per-reason recall above 90%, false-positive rate below 10%
- [ ] Audit document committed with the measured error rate
- [ ] Kill gate explicitly passed or the project explicitly stopped

---

## Stage 5: Bank storage and export

**Branch:** `stage/05-bank`

### Goal

A SQLite bank with integrity guarantees, and a browser-consumable export.

### Contract

```python
class BankItem(BaseModel):
    id: str
    tag_id: str
    dimension: Dimension
    type: ItemType
    cefr: CEFR
    difficulty: Difficulty
    prompt: str
    cue: str | None
    accepted_answers: list[str]       # non-empty, deduplicated
    distractors: list[str]            # 3, for hint level 2
    block_id: str | None              # paragraph_cloze grouping
    block_position: int | None
    confusion_group: str | None       # copied from topic; enables the
                                      # offline minimal-pair fallback
    facet: str | None                 # derived at ingest from the answer
                                      # token's morphology minus morph_spec;
                                      # None for topics with no facet space
    carrier_lemmas: list[str]
    domain: str | None
    source_sentence_id: str | None

class Bank(Protocol):
    def insert(self, items: list[BankItem]) -> InsertReport: ...
    def stock(self, tag_id: str, difficulty: Difficulty) -> int: ...
    def export_full(self) -> BankExport: ...
    def export_delta(self, since_id: str) -> BankExport: ...
```

### Tests

```python
def test_referential_integrity_every_tag_id_exists()
    # grammar items resolve to a topic, vocab items to a lemma

def test_accepted_answers_non_empty_and_deduplicated()

def test_no_item_where_prompt_contains_an_accepted_answer()
    # last line of defence; should never fire if stage 4 works,
    # which is exactly why it is asserted here too

def test_insert_is_idempotent_on_item_id()
    # re-ingesting a batch does not duplicate

def test_export_round_trip_is_lossless()
    # sqlite -> json -> sqlite, assert deep equality including ordering
    # of accepted_answers

def test_export_delta_returns_exactly_items_after_since_id()
    # property test over random insert orders

def test_export_delta_empty_when_client_is_current()

def test_export_contains_no_internal_fields()
    # rejection reasons, cost data, and audit flags must not ship
    # to the client bundle

def test_stock_count_matches_unseen_items_for_tag()

def test_facet_computed_at_ingest_for_every_faceted_topic()
    # derived from the answer token's morphology minus the topic's
    # morph_spec. Runtime derivation would break offline use.

def test_facet_is_none_only_for_topics_with_empty_morph_spec()

def test_facet_derivation_is_deterministic()
    # the same item must always yield the same facet key, or promotion
    # counting silently breaks across bank rebuilds

def test_bank_holds_at_least_two_facets_per_faceted_topic()
    # a topic stocked with one facet can never satisfy the promotion
    # rule and would sit in learning forever. This is a coverage
    # requirement on generation, checked at the bank.

def test_confusion_group_written_at_ingest_for_every_grammar_item()
    # the offline minimal-pair fallback cannot derive it at runtime

def test_bank_size_under_bundle_budget()
    # full export must stay under a fixed byte ceiling; a bank that
    # doubles the install size is a regression

@pytest.mark.golden
def test_export_schema_matches_client_expectation()
    # the same JSON schema fixture is asserted in the web test suite,
    # so a server-side change that breaks the client fails here first
```

The shared export-schema fixture is what stops stages 5 and 8 from drifting apart while being built weeks apart.

### Definition of Done

- [ ] Stage A cold seed ingested: 12 items per topic across A1 to B2
- [ ] Every faceted topic stocked with at least two distinct facets
- [ ] Full export produced and under the size ceiling
- [ ] Delta export tested against random insert orders
- [ ] Export schema fixture shared with `web/`
