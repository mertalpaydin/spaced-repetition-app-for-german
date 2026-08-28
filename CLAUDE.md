# CLAUDE.md

Guidance for any agent or human working in this repository. Read this before writing code. Then read the stage document for the stage you are working on, in `docs/`.

---

## 1. What this project is

An interleaved German grammar trainer, A1 to B2. Single-topic exercises drawn from different grammar topics are presented back to back, scheduled by FSRS spaced repetition over grammar topics.

The product thesis and full design rationale live in `docs/plan/german-grammar-app-plan.md`. Do not re-derive design decisions from first principles; they are already argued there. If you believe a decision is wrong, say so and stop. Do not silently deviate.

---

## 2. Non-negotiable rules

These are invariants. Violating any of them is a defect regardless of whether tests pass.

1. **An LLM is never the source of truth for user progress.** All progress numbers are computed in code from `review_log`. An LLM may narrate them. It may not produce them.
2. **No item may name or hint at the grammar topic it tests.** No "Setze ins Dativ." Prompts are topic-agnostic. This is the entire product thesis; an item that leaks its topic is worse than no item.
3. **No LLM call sits on the critical path of answering an exercise.** Round flow is bank retrieval plus deterministic grading. Explanations and production grading are explicitly off-path and asynchronous or on-demand.
4. **Every LLM call goes through `src/llm/client.py`.** Direct SDK calls anywhere else are forbidden. The wrapper handles retries, token accounting, cost logging, and the spend ceiling. A call that bypasses it is invisible to the budget.
5. **One item, one `tag_id`.** No compound tagging, no multi-topic attribution.
6. **`accepted_answers` is always a list.** Never a single string, never `None`.
7. **Do not weaken a failing test to make it pass.** If a test is wrong, say so and explain why before changing it.
8. **Do not change a contract defined in `docs/` without flagging it.** Interfaces between stages are specified there deliberately so stages can be built independently.

---

## 3. Repository layout

The repo root is the working directory. There is no nested project folder.

```
.
├── CLAUDE.md
├── README.md                       # setup, and where to read next
├── TODO.md                         # open work only
├── pyproject.toml
├── config.yaml                     # non-secret config
├── .env.example                    # every required var, no values
├── docs/
│   ├── project-state.md            # what is built, what is not, what bites
│   ├── known-defects.md            # every defect class, with real examples
│   ├── building-the-bank.md        # runbook: the bank build
│   ├── monthly-translation-job.md  # runbook: the Azure top-up on Windows
│   ├── plan/                       # product plan, design rationale
│   ├── 00-index.md                 # stage index, conventions, DoD
│   ├── 01-foundation.md            # stages 0-2
│   ├── 02-content-pipeline.md      # stages 3-5
│   ├── 03-learning-engine.md       # stages 6-7
│   ├── 04-application.md           # stages 8-11
│   └── audits/                     # dated record, never instructions; has its own README.md
├── data/
│   ├── taxonomy.yaml               # topics and confusion groups, one file
│   ├── specs/                      # per-topic generation spec sheets
│   ├── fixtures/                   # golden sets, adversarial sets
│   └── raw/                        # staged corpora, gitignored
├── src/
│   ├── contracts.py                # every shared type and constant, one file
│   ├── main.py                     # CLI entrypoint
│   ├── taxonomy/                   # loading, DAG validation, tagging
│   ├── corpus/                     # Tatoeba, scraping, learner errors
│   ├── lexicon/                    # frequency bands, wordlists, lemmatiser
│   ├── llm/                        # client wrapper, cache, cost log, translation
│   ├── generation/                 # spec to prompt to items
│   │   └── blanking/               # the corpus pipeline: carrier, tagger, blanker
│   ├── verification/               # the verification chain
│   ├── bank/                       # storage, export, dedup
│   ├── audit/                      # health checks over a finished bank
│   ├── engine/                     # FSRS, interleaving, Kalibrierung, simulation
│   ├── sync/                       # review-log sync client
│   └── cli/
├── scripts/                        # 21 entry points. The operator interface.
├── web/                            # PWA
├── worker/                         # Cloudflare Worker
├── tests/                          # flat, one file per area
└── .github/workflows/
```

Two things the diagram cannot show:

- **`scripts/` is how this project is actually operated.** Pilots, evals, the
  web export, cost reconciliation and the translation top-up all live there, and
  the two runbooks in `docs/` drive them. It is not a scratch directory.
- **The stage documents are a design record, not a description of the code.**
  Each carries a header naming where it departs. `docs/project-state.md` is the
  current picture.

---

## 4. Branching

`main` is always production-ready. It builds, all tests pass, and the deployed PWA works. Nothing lands on `main` except via pull request.

**Branch naming:** `<type>/<stage>-<short-slug>`

| Type | Use |
|---|---|
| `stage` | Implements a numbered stage from `docs/` |
| `feat` | New capability outside the stage plan |
| `fix` | Bug fix |
| `refactor` | Behaviour-preserving change |
| `test` | Tests only |
| `docs` | Documentation only |
| `chore` | Tooling, CI, dependencies |
| `spike` | Throwaway exploration. **Never merged.** Deleted after findings are written into `docs/`. |

Examples:

```
stage/04-verification-chain
stage/06-fsrs-scheduler
fix/03-batch-retry-on-429
spike/00-hanta-vs-spacy-morphology
docs/plan-revision-a1
```

Rules:

- One stage per branch. Do not implement stage 5 on the stage 4 branch.
- Branch from current `main`, rebase on `main` before opening the PR.
- Delete the branch after merge.
- A branch that has not been touched in 14 days is stale: finish it, or close and delete it.

---

## 5. Commits

Conventional Commits, imperative mood, scope is the module or stage.

```
<type>(<scope>): <subject>

<body: why, not what>

<footer: refs, breaking changes>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `revert`.

```
feat(verification): reject items whose prompt contains grammar terminology

Topic leakage was the highest-severity risk in the plan and the spec-sheet
prohibition alone caught only part of it. Adds a blocklist pass plus a model
check, both run before morphology so cheap rejects happen first.

Refs: docs/02-content-pipeline.md stage 4
```

Rules:

- **The body explains why.** The diff already shows what.
- **No `wip`, `fixup`, `asdf`, or `.` on a pushed branch.** Squash locally first.
- **One logical change per commit.** A commit that both adds a feature and reformats 40 files is two commits.
- **Never commit secrets, `.env`, API keys, or raw batch responses containing them.**
- **Never commit generated bank content to `main`** except the versioned export artefact produced by CI.
- A commit that changes behaviour changes tests in the same commit.

---

## 6. Pull requests

A PR may only be opened when:

- [ ] Every test in the stage document passes
- [ ] `ruff check` and `ruff format --check` are clean
- [ ] `mypy --strict src/` is clean
- [ ] Coverage on changed files is at or above 85%
- [ ] The stage document's Definition of Done checklist is fully ticked
- [ ] No new dependency was added without a one-line justification in the PR body

PR description states: which stage, what changed, what was tested, and anything deliberately deferred.

---

## 7. Testing rules

- `pytest`. Tests live in `tests/`, **flat**: one file per area, `test_<area>.py`. They do not mirror `src/`, and there are no `tests/unit/`, `tests/integration/` or `tests/simulation/` directories.
- **No test touches the network.** All LLM calls mocked. A test that makes a real API call is a defect. `tests/test_live_llm.py` is named for the live *features* (explanations, production grading, minimal pairs, weekly report), not for live calls; it uses `MockLlmClient` like everything else.
- **The whole suite is the default run.** `uv run pytest -q` runs every test, including the synthetic-learner simulation in `tests/test_typo_simulation.py`. Nothing is excluded and nothing needs a key.
- `pyproject.toml` declares three markers. Only `golden` is used, by four files (`test_verification.py`, `test_taxonomy.py`, `test_bank.py`, `test_learner_errors.py`). **`live` and `simulation` are declared and applied to zero tests.** Do not describe them as a working split; either use them or delete them.
- **Golden fixtures are versioned in `data/fixtures/` and are not regenerated casually.** Changing a golden file requires a commit that explains why the expected output changed. The one exception is `data/fixtures/translations/`, which is gitignored and is an operational store rather than a fixture; see `docs/project-state.md`.
- Every bug fix starts with a failing test that reproduces the bug.
- Property-based tests via `hypothesis` where the stage document calls for them.

**Test naming:** `test_<unit>_<condition>_<expected>`

```python
def test_grade_dativ_ending_wrong_case_fails(): ...
def test_kalibrierung_terminates_within_35_items_for_all_ability_profiles(): ...
```

---

## 8. Code standards

- Python 3.12. `ruff` for lint and format. `mypy --strict` on `src/`.
- **Pydantic models for every LLM input and output.** No raw dicts crossing a module boundary.
- No bare `except`. Catch what you can handle.
- Functions that touch the network, the filesystem, or the clock take those as injected dependencies so they can be faked in tests.
- SQL lives in `.sql` files or explicit query builders, not f-strings interpolating user data.
- TypeScript for `web/` and `worker/`. `tsc --noEmit` clean, ESLint clean.

---

## 9. Cost discipline

The recurring LLM budget is **7.50 USD/month**, raised from 5 EUR at the project owner's instruction on 2026-08-27 (`GeminiLlmClient.spend_ceiling_usd`, pinned by `test_spend_ceiling_default_is_the_owners_current_figure`). It is enforced in code, not by care. This paragraph used to say 5 EUR while the code tracked USD; the two are now stated in the same unit as the code, USD.

- Every **attempt** through `src/llm/client.py` writes a row to `cost_log`: timestamp, purpose, lane, **transport mode**, model, input tokens, output tokens, thinking tokens, cached-content tokens, tool-use tokens, the provider's own total token count, the model version Google says it served, estimated cost, and the attempt's **outcome**, **attempt index**, **call id** and, on a 429, **quota type**. An attempt, not a call: a call retried three times before it lands leaves four rows sharing one call id. `outcome`, `attempt` and `call_id` are additive with defaults (`"ok"`, `1`, `None`), so every historical row still parses as exactly what it was, the successful attempt. `quota_type` (`"rpm"` or `"rpd"`, default `None`) is additive on the same terms: it is set only on `outcome="quota"` rows, and `None` on a historical row is honest rather than a guess, because the log recorded only that a 429 happened. It exists because the two 429s mean opposite things to the operator: RPM is per-minute noise a re-run rides through, RPD is the free tier's daily allowance spent until the Pacific-midnight reset, and a bare `outcome="quota"` row could not tell them apart even though `_classify_quota_error` already had. Flagged per rule 8: these are changes to a persisted format, three additive ones now in the same week.
- **Log the attempts that failed, not only the ones that worked.** Until 2026-08-27 only the attempt that finally succeeded wrote a row, so a run that retried half its calls was indistinguishable in the log from a run that retried none. That hid roughly $1.58 of the same $5.04 August bill, including three days with real batch charges against days with zero rows in the log (`_call_batch_many_with_retry` resubmits a whole job, and Google has already billed whatever the failed job processed). A failed attempt is written with **zero tokens and zero cost**: the exception carries no usage metadata, so what Google billed is genuinely unknown, and an honest gap in an audit log beats a fabricated number. The point is visibility, not a corrected total. The ceiling is deliberately unaffected.
- **The log cannot audit itself.** It is written by the code whose correctness is in question, so agreement between the two is worth nothing. `scripts/reconcile_cost_log.py` compares it against Google's per-day billing export, per day, per model, per mode, with attempt counts as the explanation column. `scripts/repair_cost_log.py` reprices historical rows against that same export and appends one labelled `purpose="billing_reconciliation"` adjustment row per day for the remainder that cannot be recovered.
- **Price by mode, not by lane.** Google's 0.5x batch discount applies to a real Batch API submission only. The paid lane runs batch for nightly/initial generation and synchronous on-demand for a `forbid_batch=True` pilot, and the second is billed at full price. Applying the discount to every paid call under-reported roughly $1.90 of a measured $5.04 August bill that the log recorded as $1.99; `mode` is recorded on every row so the log reconciles against Google's separate `gemini 3.7 flash text` and `gemini 3.7 flash text batch` SKUs line for line.
- **When the provider's `total_token_count` disagrees with the fields the client reads, that is billed usage the log cannot see.** It warns and records the provider's total; it never raises, because this runs inside six-week unattended jobs.
- The wrapper reads month-to-date spend before every call and **raises `BudgetExceeded` at the ceiling**. Callers handle it by degrading, never by retrying.
- Nightly generation is capped at a fixed item count independent of the spend ceiling, so a logic bug cannot spend the month in one night.

### Model routing

Thinking is keyed on the **model**, not on the calling workload (`GeminiLlmClient._thinking_config_for` in `src/llm/client.py` reads only `model`). The two model constants that share a string (`MODEL_LIVE` and `MODEL_GENERATE` are both `"gemini-3.5-flash-lite"`) therefore share one thinking level too.

| Model | Constant(s) | Workloads | Thinking |
|---|---|---|---|
| `gemini-3.5-flash-lite` | `MODEL_LIVE`, `MODEL_GENERATE` (same string) | Explanations (live), production grading (live), minimal-pair generation, weekly report narrative, item generation (pilot, nightly top-up, cold-start), sentence generation | `low` (`THINKING_FLASH_LITE`) |
| `gemini-3.7-flash` | `MODEL_VERIFY` | The verification chain's one model-backed layer: semantic/answer-set-expansion validity (`src/verification/layer_expander.py`, `purpose="answer_expansion"`) | `medium` (`THINKING_VERIFY`) |
| operator's choice, capable tier | n/a | Agent-critical build tasks | n/a |

`THINKING_FLASH_LITE` is `"low"`, not `"minimal"`: `"minimal"` is Flash-Lite's own default, so setting it explicitly would buy nothing over leaving thinking unset. `"low"` is the smallest level that is an actual step up.

There is no separate "topic-leak check" or "override verification" LLM call. Topic-leak checking (`src/verification/layer_topic_leak.py`) is a deterministic blocklist/compound-term match, not a model call at all, and does not appear in `cost_log`. "Answer-set expansion" and what an earlier version of this table called "override verification" are the same call: layer 5 of the verification pipeline, optional per run, invoked on every candidate that survives layers 1-4 when an `llm_client` is supplied. Earlier drafts of this table listed them as two rows on `gemini-3.6-flash`; the code uses one row, on `gemini-3.7-flash`.

Model IDs live in one config block. No model string appears inline anywhere in `src/`.

**Verify pricing and free-tier limits before implementing, and propose a change if the routing is stale.** This table reflects prices at the time of writing, and the Gemini lineup moved repeatedly through 2026: 3.6 Flash launched in July at a lower output price than 3.5 Flash, 2.5 Flash-Lite retires in October, and free-tier quotas were cut sharply in December 2025. At the start of stage 0, read Google's current pricing page and the AI Studio rate-limit view for both projects, record both in `docs/audits/stage-00-quota.md`, and if a cheaper or better-performing model now occupies a slot, say so and recommend the swap rather than following this table. `docs/audits/stage-00-quota.md` section 4 found `gemini-3.6-flash` and `gemini-3.7-flash` identically priced, so the code's use of `gemini-3.7-flash` for `MODEL_VERIFY` (this table used to say `gemini-3.6-flash`) is not a cost regression, just a naming correction.

Two things to check specifically: whether a newer Lite tier is *more* expensive than the one it replaced, which has happened, and whether the model in a free-tier slot is still free-tier eligible.

**Thinking tokens bill as output.** Both routed models now spend a nonzero thinking budget: `gemini-3.7-flash` (verification) runs at `medium`, raised from `low`, and `gemini-3.5-flash-lite` runs at `low` on every workload listed above, not only sentence generation. The `low` level was first added for one purpose (sentence generation, to fix an agreement defect after a fronted adverbial) while every other Flash-Lite workload stayed at the model's own `minimal` default, which cost nothing beyond that default. Extending `low` to the whole model line is the first thinking-token spend this workload has actually incurred, and it now applies to every Flash-Lite call, including the highest-volume ones (item generation, live explanations). Raising verification from `low` to `medium` increases spend on that chain as well. The level is a config value (`THINKING_FLASH_LITE`, `THINKING_VERIFY` in `src/contracts.py`) so it can be tuned against measured recall; there is no measured recall or spend delta recorded here yet, only the routing change itself, so treat the resulting cost as an estimate to be watched against `cost_log`, not a confirmed number.

### Two lanes, two projects

Google enforces Gemini quota per Cloud project, not per API key, and enabling billing on a project destroys its free tier. Two separate projects are therefore mandatory, not an optimisation.

**This section's mode column and its "always batch" rule below supersede this document's earlier wording, at the project owner's explicit instruction** ("when I said no batch api I meant for pilot go to paid on demand api, if free lane is already expired"). The earlier wording conflated two different switches -- "no paid lane at all" (`forbid_paid_lane`) and "no real batch submission" (`forbid_batch`) -- as if they were one. They are not, and both still exist in `src/llm/client.py` (`GeminiLlmClient.forbid_paid_lane`, `GeminiLlmClient.forbid_batch`); this rewrite states which one pilots actually use. Flagged here per rule 8 rather than silently corrected, so a future reader does not mistake this for drift.

| Lane | Project | Mode | Used for |
|---|---|---|---|
| `free` | unbilled | synchronous, rate-limited | Live features (explanations, production grading, minimal pairs, weekly-report manual button), pilot generation by default, development iteration |
| `paid` | billed | batch by default; on-demand (synchronous) for a pilot built with `forbid_batch=True` once the free lane's daily quota is spent | Nightly/initial generation top-up, weekly-report auto-fire, all overflow, real Batch API runs only with an explicit `--batch` opt-in; pilot generation on-demand once the free lane closes |

Lane policy is decided per workload, not per model:

- **Pilot generation is on-demand only by default, but not paid-lane-forbidden.** `scripts/step5_pilot_generation.py` and `scripts/step6_blank_pilot.py` both build their `GeminiLlmClient` with `forbid_batch=True` (`step6` has no `--batch` flag at all, so it is always `forbid_batch=True`; `step5` sets it whenever `--batch` is not passed). Neither forbids the paid lane itself (`forbid_paid_lane=False`): once the free lane's daily quota is spent, the run keeps generating and verifying on the paid lane, synchronously, on demand. Only queuing a real Batch API job is forbidden -- attempting one raises `BatchForbiddenError`. `--batch` (`step5`'s one deliberate opt-in) sets `forbid_batch=False` instead and routes through the real Batch API.
- **Nightly and initial (cold-start) item generation permit the batch lane.** That is what the paid lane exists for.
- **Explanations, production grading, and minimal-pair generation are on-demand only, and paid-lane-forbidden.** A learner is waiting on the reply, so these use `forbid_paid_lane=True` (`src/llm/provider.default_llm_provider`'s default): unlike a pilot, there is no case where falling through to the paid lane, sync or batch, is acceptable here.
- **The weekly report is dual.** A manually-clicked report button is on-demand; an activity-triggered auto-fire, where nobody is watching synchronously, may use the batch lane (`src/llm/weekly_report.py`, `maybe_generate_report`).
- **Answer-set expansion inherits its calling run's lane policy.** It is not independently sync or batch; it runs on whichever lane the pipeline invocation around it (pilot vs. nightly) is already using.
- **`--free-lane-only` is the operator's opt-in to zero spend**, on `scripts/step7_corpus_pilot.py`, `scripts/eval_verifier.py` and `scripts/eval_gloss_adversarial.py`. Flagged per rule 8: it adds a lane-policy switch to three script interfaces, but changes no default, so every existing invocation of those three (and every other caller of `sentence_source.client_from_env`) behaves exactly as before. It builds the client with `forbid_paid_lane=True`, so a spent free-lane daily quota ends the run instead of continuing on the billed project. It also **requires `GEMINI_FREE_API_KEY` to be set explicitly and refuses to start otherwise**, because `GeminiLlmClient` resolves its free key as `GEMINI_FREE_API_KEY or GEMINI_API_KEY` and a `GEMINI_API_KEY` that belongs to the billed project would make every "free" call bill. The guard is on the variable being set, never on comparing key material.

Rules:

- **Never rotate keys to multiply free quota.** Extra keys in one project share one pool, so it does not work; extra *projects* to evade quota breaches Google's terms and has no place in a public repo.
- **The free lane is always synchronous. The paid lane is batch by default.** A non-batch call on the paid lane is a defect *unless* the client was built with `forbid_batch=True`, in which case a paid-lane synchronous call is exactly what that mode exists for; attempting a real batch submission on such a client is itself the defect, and raises `BatchForbiddenError` rather than silently queuing a job. `forbid_paid_lane` is the separate, stricter switch that forbids the paid lane outright (sync or batch) -- see the pilot-generation bullet above for which workloads use which.
- **Overflow accumulates, it does not fail over per request** for any workload that has not opted into `forbid_batch`. When the free lane closes, remaining work queues and ships as one batch. Per-request failover forfeits the batch discount on exactly the requests you pay for. A `forbid_batch=True` pilot is the deliberate exception: it fails over per request, on purpose, because a pilot needs to keep iterating rather than wait on a batch job.
- **Distinguish the two 429s.** RPM exhaustion means back off and stay on the free lane. RPD exhaustion means close the free lane until the Pacific-midnight reset and flush to the paid lane (batch by default, on-demand under `forbid_batch=True`). Treating them alike either wastes free quota or spins pointlessly.
- Free-tier content is used by Google to improve its products. Whether user-typed text may use the free lane is a config flag, `privacy.restrict_user_content_to_paid_lane`, **default `false`**. With the default, everything uses the free lane when quota allows.

Flip it to `true` before anyone other than you uses the app. Three calls carry user text: production grading sends their sentence, explanations send their wrong answer, and weekly error analysis sends their mistake history. Sending your own writing into a training pipeline is your choice to make; sending someone else's is not.

### Caching

**Do not use provider-side context caching.** Input is a small fraction of spend and output tokens, which are the bulk of it, are not cacheable. The storage charge can exceed the saving at this volume.

**Do use a local content-addressed cache**, keyed on a hash of the full request, in `src/llm/cache.py`:

- Every call checks the cache before the transport, on both lanes.
- Cache hits write a `cost_log` row with zero cost and `lane="cache"`, so hit rate is measurable.
- The cache is the idempotency mechanism as well: a rerun after a crash must not regenerate what already landed.
- Never cache anything keyed on user-identifying data.

---

## 10. Agent-critical tasks

Some build artefacts are **ground truth or foundations**: everything downstream is measured against them, so an error propagates silently rather than failing loudly. They are marked `**[AGENT-CRITICAL]**` in the stage documents.

Protocol for every marked item:

1. **Primary pass** by a coding agent on a capable model tier, not the cheap generation tier used for bank content.
2. **Independent second pass** by a second agent that re-derives the artefact from the same inputs without seeing the first output, then diffs.
3. **Disagreements are surfaced, never auto-resolved.** A conflict list is committed alongside the artefact. Silent reconciliation defeats the purpose of the second pass.
4. **The two agents must come from different vendors.** One Claude, one Gemini. This is a requirement, not a preference: two agents from one family share failure modes, so their agreement is close to worthless as evidence. Record which vendor produced which pass in the conflict list.
5. **Anchor externally wherever an external key exists.** Scraped exercise answer keys, MERLIN's expert annotations and Goethe inventories were authored by people with no relationship to this pipeline, which is the only thing that genuinely breaks correlated error.

**The honest limitation.** Cross-vendor checking is substantially stronger than same-family checking, but two models trained on overlapping web text still share some blind spots, particularly on German morphology edge cases. The protocol reduces error; it does not make the estimate fully independent. Where an external anchor exists, it outranks both agents.

## 11. Secrets

- `.env` is gitignored. `.env.example` lists every variable with no values.
- CI and the Worker read from GitHub Actions secrets and Cloudflare secrets respectively.
- If a secret is ever committed, rotate it. Do not merely delete the commit.

---

## 12. Working style for agents

- **Read the stage document first.** It defines the contract, the deliverables, and the tests. Do not start coding from the product plan alone.
- **Do not skip ahead.** Stages assume their predecessors' contracts hold. Implementing stage 7 against an imagined stage 6 wastes both.
- **Stop and ask when a contract is ambiguous.** Guessing an interface and building on it is the expensive failure mode here.
- **Report the kill criteria honestly.** Stage 4 has a measured error-rate threshold that determines whether the project continues. Do not tune the audit to pass it.
- **Write the test before the implementation** where the stage document specifies a golden or adversarial set.
- Prose in `docs/` and in commit messages uses no em dashes.
