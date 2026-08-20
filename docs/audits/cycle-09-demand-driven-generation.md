# Cycle 9: demand-driven generation replaces the harvest model

## The defect

Generation was not driven by the topic list. `generate_sentence_pool` built
one general pool of everyday sentences, cycling theme, person, tense,
register and (since the previous cycle) a construction hint round-robin
across all 49 topics, never keyed to which topic actually needed items.
`pipeline.blank_sentences` then harvested whatever topics happened to fall
out of that pool. docs/audits/cycle-08-report.md: 18 of 49 topics ended a
pilot run at zero, every one of them `no_candidate_for_topic`: not one
carrier in that run's pool happened to contain the construction, because
nothing ever asked for it specifically.

## The fix

Two new modules under `src/generation/blanking/`:

- `topic_demand.py`: `compute_demand(topic_ids, stock_lookup, ...)`.
  `deficit = max(0, target_unseen - stock)` per topic, where `stock` is
  UNSEEN-item count (`SqliteItemBank.stock`, no row in `review_logs`). A
  topic at or above target gets zero demand and is dropped from the result
  entirely -- "there is no point of asking for a sentence from a topic if we
  have already many unseen exercises from it." `forced` names topics that
  get at least a floor of demand regardless of deficit, additive to any real
  deficit, never a replacement for it -- the pilot's own need to see
  problematic topics even when they already have stock. `stock_lookup` is an
  injected callable, so the module needs no database in tests.
- `orchestrator.py`: `run_demand_driven_generation`. For every topic with
  demand, in demand order (scarcest first), requests sentences using THAT
  topic's own construction hint, varying theme/person/tense/register/
  structure across the requests; carrier-validates them; runs them through
  that topic's own selector (`pipeline.blank_sentences(..., topic_ids=[t])`)
  to decide, immediately, whether to retry; retries up to
  `max_retries_per_topic` (default 2) and then stops and records the topic,
  permanently, for the rest of the run. A final, unrestricted-but-topic-
  scoped `blank_sentences` pass over every carrier-valid sentence the whole
  run collected (deduplicated by exact text) is what actually determines the
  accepted item set -- the same cross-topic dedup, caps, uniqueness gate and
  type-eligibility check that ran before this cycle, unchanged. Two explicit
  budgets: `max_retries_per_topic` (per topic) and `call_ceiling` (total for
  the run, defaulting to the worst-case `projected_call_count`, both printed
  before generation starts).

`scripts/step6_blank_pilot.py` is rewired onto this loop. It has no learner
and usually no bank, so its job is coverage, not top-up: it defaults to
forcing every one of the 49 topics. `--force-topics a,b,c` narrows a run to
exactly that topic list (and also scopes `compute_demand`'s topic universe
to it -- see "one thing the brief got wrong" below) for a targeted run
against a bank with real stock, via the new `--db` flag. `--sentences` (an
overall sentence-pool size hint) is replaced by `--per-topic-target` (a
per-topic UNSEEN-item target fed to `compute_demand`), documented as a
**contract change** in the script's own module docstring per CLAUDE.md rule
8, because "how many sentences to request in total" stopped being a
meaningful number once requests are keyed per topic.

`pipeline.blank_sentences` gained one optional parameter, `topic_ids`,
defaulting to `None` (every existing caller unaffected): when given, it
restricts which topics' selectors run over a batch of sentences, which is
what lets the orchestrator ask "did this topic's own request produce
anything for this topic" without every other selector also getting a look
at a sentence it had no hand in requesting.

CLAUDE.md rule 2 (the topic id is never sent to the model) is asserted at
the seam: `orchestrator._request_batch` asserts the topic id is not equal to
any of the actual strings about to be passed to `generator.generate`
(`cefr`, theme, person/tense/register/structure/construction hints), and
`tests/test_blanking_orchestrator.py::test_request_batch_asserts_topic_id_never_reaches_the_model`
exercises the failure path directly.

## Offline coverage numbers

Run against the deterministic offline mock pool
(`sentence_source.MockSentenceGenerator`), 49 topics, `target_unseen=12`,
`forced_floor=5`, `max_retries_per_topic=2` (the script's actual defaults),
every topic forced (the pilot's own default), zero stock (no `--db`):

| | Before (round-robin harvest, cycle 8's own report) | After (demand-driven loop, this cycle) |
|---|---|---|
| Topics with at least one item | 31 of 49 | 47 of 49 |
| Topics with zero items | 18 of 49 | 2 of 49 |
| Total items produced | 286 (accepted, post-verification) | 413 (pre-model-verification; not a like-for-like item count, only topic coverage is directly comparable) |

The two topics still at zero, and why, both honestly distinguishable per
this cycle's own report categories:

- `artikel_bestimmt_nom`: `selector_found_no_candidate` -- the mock pool's
  fixed sentences for this run's particular theme/person/tense/register
  cycling never happened to land on one of its uniqueness-anchoring
  carriers (relative clause / superlative / ordinal). Real model generation
  is not bound by the mock's fixed, hash-indexed slices and is expected to
  do better; this is a known limitation of the deterministic offline pool
  (`sentence_source.MockSentenceGenerator`'s own docstring), not a defect in
  the loop.
- `zustandspassiv`: `capped_or_lost_to_cross_topic_duplicate` -- it produced
  candidates in the provisional pass, but lost them in the final,
  globally-gated pass to caps or a cross-topic duplicate. A real quality-
  gating outcome, not a coverage failure.

Not every topic that gets at least one item meets its full demand of 12
within 2 retries against the small offline mock pool (16 of 49 fully met
demand in this run); this is a separate, secondary tuning question (batch
size, retry budget, or pool richness), not the defect this cycle fixes,
which was topics receiving *zero* items with no request ever aimed at them.

## Projected call count for a full 49-topic pilot run

`orchestrator.projected_call_count`, worst case (every topic spends its
full retry budget): **147 calls** (49 topics x (1 initial + 2 retries)),
printed by `step6_blank_pilot.py` before generation starts. This is well
above the free lane's daily quota of roughly 50 calls (CLAUDE.md 9), so a
default pilot run is expected to spill onto the paid lane, on demand,
partway through -- exactly the visibility this task's brief asked for
("the operator should see it coming rather than discover it in
`cost_log`"), not a change to lane policy itself (`forbid_batch=True`,
`forbid_paid_lane=False` is unchanged, from `sentence_source.client_from_env`).

## One thing the brief got wrong: no nightly/cold-start call site exists yet for this pipeline

The brief asked to "point [nightly and cold-start generation] at
`compute_demand` with no forced list" and to "find the actual call sites
rather than assuming where they are." Having looked: **no such call site
exists**. The generate-then-blank pipeline (`sentence_source.py`,
`pipeline.py`, and now `orchestrator.py`/`topic_demand.py`) is wired only
into `scripts/step6_blank_pilot.py`, which writes review/rejected JSONL
files for hand audit and does not call `bank.insert`.

The repository's actual nightly/cold-start automation
(`.github/workflows/generate-submit.yml`, `generate-ingest.yml`, and
`src/generation/batch_client.py`'s `run_submit`/`run_ingest`) drives a
**different, older pipeline**: the LLM-direct path
(`src.generation.prompt_builder.PromptBuilder`, where the model proposes
both the carrier and the answer, verified afterward by
`src.verification.pipeline`), fed by `src.generation.deficits`'s own
deficit calculation -- a **different formula** (FSRS-projected 14-day
demand x a safety factor, compared against ALL bank items for a topic, not
UNSEEN-only) with its own documented contract
(`docs/04-application.md`'s "Deficit calculation", per `deficits.py`'s own
docstring; that stage document does not exist in this checkout, only the
`docs/audits/` history does).

Pointing the LLM-direct pipeline's nightly automation at
`topic_demand.compute_demand` instead of `src.generation.deficits` would
mean dropping the FSRS-projected-demand term entirely in favour of a flat
UNSEEN-stock target, and swapping "all items" stock for "unseen items"
stock -- a real, and arguably overdue, correctness fix (nightly top-up
currently cannot tell a topic the learner has already burned through from
one still full of fresh material), but it is a change to a **different
pipeline's documented contract**, not a change this cycle's brief actually
described building. Per CLAUDE.md rule 8, that is flagged here rather than
made silently or guessed at with an invented call site. The generate-then-
blank pipeline itself has no nightly/cold-start entry point to redirect at
all; building one (bank insertion, workflow wiring, cost-log integration
for a THIRD generation path) is new infrastructure, not a fix to the
demand-loop defect this cycle's brief is about, and was left out of this
change for that reason.
