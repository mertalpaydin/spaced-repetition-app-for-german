# Plan vs. Code: Gap Analysis

> **Historical snapshot. Do not read this as a description of the repository today.**
>
> Written 14 August 2026. Filed here unchanged on 2026-08-28 as part of the
> project record, not as current instructions. Its content has deliberately not
> been updated, so every claim below describes the tree as it stood on 14 August
> 2026 and most of them are now false.
>
> Most of what it lists has since been addressed. Two examples, so the gap is
> concrete: it says the repository contains no real API calls, and the pipeline
> now makes real Gemini and Azure Translator calls in production paths; it
> describes `.env.example` as declaring a single key, and it now declares nine
> variables across two Gemini lanes and Azure Translator. Its build-state table
> (105 tests, 84% coverage) is an August 2026 measurement, not a current one.
>
> For the current state of the code, read `README.md`, `CLAUDE.md` and
> `docs/known-defects.md`. Use this file only to understand what the project
> looked like at that date and which problems drove the work that followed.

Audit of the `Language_Learning_App` codebase against its stage plan documents.
Date: 14 August 2026, second pass after remediation commits `ad91872` and `40a2e8c`.

**Scope of comparison:** `CLAUDE.md`, `00-index.md`, `01-foundation.md`, `02-content-pipeline.md`, `03-learning-engine.md`, `04-application.md`, `german-grammar-app-plan.md`, `README.md`, `COMMIT_RULES.md`.

**How this was verified:** full source read of `src/`, `tests/`, `web/`, `worker/`, `scripts/`, `.github/`, `data/`, plus a clean reconstruction of the repo in an isolated environment where the test suite, `ruff`, `mypy --strict` and coverage were executed, the taxonomy and fixtures were parsed and counted, and the grader, verification chain, error classifier and CLI were run directly against live inputs. Every claim below was checked against the tree or against executed output. A passing test was never accepted as proof.

---

## 1. Summary

The remediation pass fixed real things, and they are listed in section 2. It did not move the structural picture: the repo remains a complete architectural skeleton of the plan with the load-bearing substance replaced by mocks, hardcoded tables, and constants that are declared but never read.

Of 57 specific defects re-checked across all stages, **9 are fixed, 8 are partially fixed, and 40 are unchanged.** The fixes cluster in tooling, fixtures and small wiring. The five findings the previous review ranked most consequential are all still open, and the remediation introduced 14 new defects, one of which is more serious than most of what it fixed.

Build state, measured on a clean checkout:

| Check | Result |
|---|---|
| `pytest tests/` | 105 passed, 0 failed |
| `ruff check src tests scripts` | clean |
| `ruff format --check src tests` | 76 files formatted |
| `mypy --strict src/` | no issues, 56 files |
| `pytest --cov=src` | **84%**, against a CI gate of `--cov-fail-under=85` |

Coverage is still below the gate the repo sets for itself. Five tests were added and none of them closed a plan-named gap in the stages that matter.

### The single most urgent item

`src/cli/app.py:94` and `:132`:

```python
ans = input("Lösung: ").strip() or it.accepted_answers[0]
```

This was introduced by the interactive-input fix. **Pressing Enter on an empty answer substitutes the correct answer**, which is then graded as a pass, rated `good` in FSRS, counted as an unhinted pass toward topic promotion, and written to `review_logs` as a truthful record. Verified by execution: four empty responses to `kalibrierung` yielded `Topics Mastered: 1`. This corrupts the exact data the stage 7 usability gate exists to collect, and it corrupts it silently. Fix before any manual use of the CLI.

---

## 2. What the remediation fixed

Confirmed by inspection and execution.

| Item | Evidence |
|---|---|
| `MODEL_LIVE` and `MODEL_GENERATE` back on the correct tier | `src/contracts.py:66-67` both now `"gemini-3.5-flash-lite"`; `MODEL_VERIFY` correctly `"gemini-3.7-flash"` |
| Python 3.12 across the toolchain | `pyproject.toml:6,38,56` now `>=3.12` / `py312` / `3.12`, matching CI |
| `hypothesis` added | `pyproject.toml:23` |
| CI now lints and type-checks `scripts/` | `ci.yml:30-40` runs ruff and `mypy --strict` over `src/ scripts/`; `scripts/__init__.py` added |
| `scripts/step1_extract_vocab.py` no longer calls a nonexistent method | now calls `extractor.extract_all()` and iterates the correct `{lemma: level}` shape |
| Golden taxonomy fixture no longer self-heals | `tests/test_taxonomy.py:92` now `assert golden_file.exists()` before comparing, instead of writing the file it is meant to check |
| Adversarial and known-good fixtures at specified size | both now exactly 60 lines, and the adversarial set is 8 per reason across six reasons plus 12 `pedagogical_flaw` |
| Stale `adversarial_suite.jsonl` deleted | the tracked-but-missing file is gone |
| Grading table and labelled answers at specified size | `grading_table.csv` 90 data rows against a spec of 80+; `labelled_answers.jsonl` 30 rows as specified |
| PWA init bug fixed | `web/app.js:318` now calls `getTopicStates()`, which exists at `web/db.js:48` |
| Service worker registered | `web/app.js:310-314` |
| Injected clock, mostly | most call sites now take `now: datetime \| None = None`; see section 6 for the four that do not |

That is genuine progress on stage 0 tooling and on two PWA blockers. It is not progress on any stage gate.

---

## 3. Stage status

| Stage | Name | Status | Change since last review |
|---|---|---|---|
| 0 | Repo scaffold & CI | **PARTIAL** | Improved. Toolchain and lint scope fixed; `src/llm/client.py` now exists but is orphaned and its transport is mocked |
| 1 | Taxonomy & DAG | **DIVERGED** | Unchanged. `data/taxonomy.yaml` was not modified |
| 2 | Corpus & lexicon | **MISSING as specified** | Unchanged apart from the `step1` script bug |
| 2b | Learner error corpus | **DIVERGED** | Unchanged |
| 2c | Exercise scraping | **MISSING** | Unchanged |
| 3 | Batch generation | **PARTIAL** | Unchanged. Still no real batch client |
| 4 | Verification chain | **DIVERGED. KILL GATE UNMEASURED** | Fixtures resized; chain and gate semantics unchanged |
| 5 | Bank storage & export | **PARTIAL** | `review_logs` table added; every other item unchanged |
| 6 | Scheduler & Kalibrierung | **PARTIAL / DIVERGED** | `src/engine/scheduler.py` is byte-identical. Facet plumbing added, derivation still absent |
| 7 | CLI & grading | **PARTIAL. GATE NOT ATTEMPTED** | Interactive input added, with a new correctness bug. Grading unchanged |
| 8 | Offline PWA | **MISSING / DIVERGED** | Two blockers fixed, one remains. All 11 UX surfaces unchanged |
| 9 | Worker, D1, sync | **MISSING** | One dead table added. Endpoints, auth and merge semantics unchanged |
| 10 | Nightly automation | **PARTIAL** | Unchanged. Ingest still crashes every night |
| 11 | Live LLM features | **MISSING** | Unchanged. Still zero real API calls in the repo |

---

## 4. CLAUDE.md invariant compliance

| # | Invariant | Status |
|---|---|---|
| 1 | Progress computed in code from `review_log`, never by an LLM | **Still unsatisfied.** A `review_logs` table now exists and the CLI writes to it, but nothing reads it back into state. `grep -rn "recompute\|rebuild_from_log\|from_review_log" src/ web/ worker/ tests/` returns zero hits. The table also has **no `mode` column** (`src/bank/migrations.py:68-78`), which `03-learning-engine.md:201` requires for the `mode in ("review","recalibration")` filter, so the recomputation it was added to enable is not expressible against it |
| 2 | No item names or hints at its topic | Upheld for generated items. Still not applied to scraped items |
| 3 | No LLM call on the answering critical path | **Upheld** |
| 4 | Every LLM call goes through `src/llm/client.py` | **Formally satisfied, substantively not.** The file exists and `tests/test_llm_client.py:11` AST-scans for SDK imports. But no SDK is imported anywhere, so the scan passes vacuously, and `GeminiLlmClient` is imported by nothing except its own re-export and its own tests. See section 5 |
| 5 | One item, one `tag_id` | Upheld in the contract, though `tag_id` still has no column |
| 6 | `accepted_answers` is always a list | Upheld |
| 7 | Do not weaken a failing test to make it pass | **Still violated once.** `src/taxonomy/validator.py:86-88` still downgrades the "confusion group needs two members" check to a warning, so `report.is_valid` stays `True` over 12 singleton groups. The golden-fixture self-heal, the other instance, is fixed |
| 8 | Do not change a contract without flagging it | `TagState` still carries six values (`src/contracts.py:14`) against the plan's three, and `TagStateModel.state` still defaults to `"locked"` rather than `"unseen"` |

### Standards still unmet

- **TypeScript for `web/` and `worker/`** (`CLAUDE.md:181`). Zero `.ts` files, no `tsconfig`, no `package.json`, no ESLint config. `ci.yml` has no JS or TS step.
- **Test marker taxonomy** (`CLAUDE.md:159-160`, `00-index.md:139-148`). `ci.yml:40` still runs `pytest` unfiltered. No `-m "not live"`, no PR-only `simulation` job, no nightly `live` job. The markers remain declared and unused.
- **`tests/unit|integration|simulation/`**. Tests are still flat.
- **Repository layout** (`CLAUDE.md` section 3). The stage docs still sit at repo root rather than `docs/`; `data/taxonomy/topics.yaml`, `data/taxonomy/confusion_groups.yaml`, `data/taxonomy/errant_mapping.yaml`, `data/fixtures/taxonomy/goethe_inventory_checklist.yaml`, `ATTRIBUTION.md` and `config.yaml` do not exist.
- **`.env.example` still declares one key.** The two-lane architecture needs `GEMINI_FREE_API_KEY` and `GEMINI_PAID_API_KEY` in separate Cloud projects (`01-foundation.md:98-99`). `GeminiLlmClient.__init__` reads both names but nothing documents them.

---

## 5. The new LLM client

`src/llm/client.py` (165 lines) and `src/llm/cache.py` (47 lines) are new and implement more of the plan than anything that preceded them: a `CostLogRow` model, a JSONL cost log that persists and reloads, `get_month_to_date_spend`, a `BudgetExceeded` exception, a content-addressed disk cache checked before transport, and a `Lane` literal.

Four problems make it non-functional as the thing invariant 4 describes.

**1. The transport is still a mock.** `src/llm/client.py:142-144`:

```python
# 4. Generate Response (Mocked transport when no active network / keys)
prompt_tokens = len(prompt.split()) * 2
response_text = f"Mocked LLM generation response for {purpose}"
```

There is no `google.genai`, `httpx` or `requests` import in the file. `self.free_api_key` and `self.paid_api_key` are assigned at `:51-54` and never read again. The repo still contains zero real API calls.

**2. Nothing routes through it.** `GeminiLlmClient` appears outside its own module only in `src/llm/__init__.py:4` and `tests/test_llm_client.py`. `live_explainer.py:32`, `production_grader.py:38`, `minimal_pairs.py:55` and `weekly_report.py:30` all still default to `MockLlmClient()`, and `src/generation/batch_client.py` does not import `src.llm` at all.

**3. It cannot be wired in as written.** `GeminiLlmClient.generate(prompt, model, purpose, ...)` does not satisfy the `LlmProvider` Protocol, which requires `generate_text(prompt, system_prompt)` (`src/llm/provider.py:15`). The two halves of the LLM layer are structurally incompatible and no adapter exists.

**4. The spend ceiling cannot fire.** `_estimate_cost` returns `0.0` for `lane == "free"` (`client.py:91-92`), `lane` defaults to `"free"` (`:136`), and `self.free_lane_open` is set `True` at `:60` and **never mutated anywhere in the repo**. So every ordinary call logs `cost_usd=0.0`, month-to-date spend stays at zero, and `BudgetExceeded` at `:112` is unreachable. The test that appears to cover this (`tests/test_llm_client.py:37`) hand-injects a `$5.50` cost row.

Also missing from the stage 0 spec: RPM-versus-RPD 429 discrimination, retry and backoff, overflow accumulation into a single batch, and the `config.yaml` privacy flag (the flag is a constructor argument with no config file behind it). **Named tests: 5 of 19**, and the one that matters most passes vacuously.

Two smaller defects in the new code: `client.py:70-71` swallows cost-log corruption with `except Exception: pass`, silently under-counting spend, against `CLAUDE.md:178`; and `cache.py:197` returns `str(data.get("response"))`, so a cache file with a null response yields the literal string `"None"` instead of a miss.

---

## 6. Stage-by-stage detail

### Stages 1, 2, 2b, 2c: unchanged

`data/taxonomy.yaml`, `src/corpus/`, `src/lexicon/` and `data/specs/` were not modified. Re-verified directly:

- **`morph_spec` keys are still mostly not Universal Dependencies features.** Eight invented keys remain: `Pos` (17 topics), `Subordinate` (12), `VerbType` (7), `ArtType` (6), `Declension` (3), `Reflexive` (2), `VowelChange` (1), `Separable` (1). UD spells it `Reflex` and has no `Pos`, `ArtType`, `Declension`, `Subordinate`, `VowelChange` or `Separable` feature. `01-foundation.md:267-269` names this exact failure: keys that "would silently disable the stage 4 morphology check".
- **12 singleton confusion groups** remain, and the validator still downgrades the check to a warning.
- **`derived_from`, `split_into`, `split_axis` are absent** from both the `Topic` model and the data, so topic splitting has no representation.
- **`sibling_group` is set on 0 of 87 topics**, while `src/engine/scheduler.py:144` still claims to interleave on it.
- **`class Taxonomy` with `topological_order()` does not exist**; zero hits repo-wide.
- **All six `requires_context` topics remain eligible for single-sentence types**, `plusquamperfekt` and `modalpartikeln` most clearly.
- **`goethe_inventory_checklist.yaml` still absent**, so the taxonomy's only external anchor is missing.
- **Stage 2 corpus is still 8 seed sentences** against a DoD of 20,000. No Leipzig bands. **spaCy is still imported in zero files under `src/`**, so there is no lemmatisation and no morphological analysis anywhere in the repo.
- **Stage 2b mapping is still unfalsifiable**: every ERRANT keep-list tag maps to `None`, `R:CONJ:SUBORD` maps to a topic despite `CONJ` being on the discard list, `mapped_sample.jsonl` is 100/100 mapped against a specified 15 to 30 percent band, and the golden test never invokes the mapper.
- **Stage 2c is still one parser over one synthetic fixture**, with no scraper, and `topic_id` still force-injected by the constructor so low-confidence mappings cannot be left null.

**Named-test coverage for stages 0 to 2c: 14 of 73**, up from 9 (the five new stage 0 client tests).

### Stage 3: unchanged

`MockBatchClient` is still what `main()` instantiates (`src/generation/batch_client.py:168`). `CandidateItem.block_id` and `block_position` still absent. Paragraph-block validation still only English text inside the prompt payload. `NO_ERROR_ITEM_SHARE` still unreferenced. `build_spec_for_topic` still fabricates gold examples with `"Hier steht Beispielsatz Nummer {idx} mit ___ Lücke."`. All 87 spec sheets still carry exactly 3 gold examples, none traceable to human-authored material.

### Stage 4: the kill gate still measures the wrong quantity

`src/verification/pipeline.py:175-176` is unchanged:

```python
error_rate = round(failed_count / len(candidates), 4)
kill_gate_tripped = error_rate > kill_gate_threshold
```

`failed_count` counts candidates the chain **rejected**. The plan requires the post-verifier error rate among 100 **accepted** items, judged by two independent auditors (`00-index.md:32`, `02-content-pipeline.md:337-341`). These remain opposite signals: a chain that catches every defect scores a 100 percent "error rate". `tests/test_verification.py:170-178` still hardcodes the inversion, asserting that a chain which correctly caught both defects in a 10-item batch has failed.

No code path anywhere samples accepted items. `docs/audits/` still contains only `stage-00-quota.md` and `stage-04-2026-08-13.md`; the stage 4 audit is untouched and still cites the now-deleted `adversarial_suite.jsonl`, still reports 30 items, still credits "Layer 2 (spaCy Morphosyntax)" to code containing no spaCy, and still never computes the gate quantity. There is no second auditor and no new audit document.

The chain itself is unchanged:

| # | Plan layer | Code | Real or stub |
|---|---|---|---|
| 1 | Schema validation | `layer1_syntax.py:17` | Real |
| 2 | Topic-leak check | blocklist plus `layer_topic_leak.py` | Blocklist only; no model pass |
| 3 | Answer-set expansion | `layer_expander.py` | **Still dead.** Referenced only by its own definition and the `__init__` re-export; `pipeline.py` never imports it |
| 4 | Morphology | `layer2_morphology.py` | **Still hardcoded to 3 topic IDs.** 65 of the 68 topics with a `morph_spec` get no morphological check |
| 5 | Level check | folded into Layer 1 | Real, but silently skipped unless both `vocab_store` and `spec` are passed |
| 6 | Dedup | `dedup.py:21-33` | Still Jaccard, not embeddings; still only against an existing bank, never within a batch |
| 7 | Human audit, 5% sample | | Still missing |

`VerificationResult.accepted_answers` and `.rejections` are still never populated: all six `return VerificationResult(...)` sites in `pipeline.py` omit both.

`ErrorClassifier.classify` is still broken. Fed the 20 literal reason strings the four layers actually emit, **15 of 20 return `pedagogical_flaw`**; `topic_leak`, `structural_malformation` and `morphosyntactic_error` are returned by no branch and are unreachable by construction. `"Topic leak: answer appears in sentence part ..."` hits the `"topic" in r_lower` branch at `classifier.py:27` and returns `pedagogical_flaw`.

**New defects found in the resized fixtures:**

- **The adversarial golden test asserts nothing about which layer or reason fires.** `tests/test_verification.py:63` pops `"expected_layer"`, but the fixture field is `expected_layer_failed`. The key is never present, so the guard at `:72` makes the layer assertion dead code, and `expected_error_type` is asserted nowhere. The test checks only aggregate `not res.passed` across all 60 items, which is precisely the aggregate-hides-per-reason failure `02-content-pipeline.md:274-277` warns against.
- **True `duplicate` recall is zero.** Items `adv_041` through `adv_048` are labelled `expected_error_type: "duplicate"`, `expected_layer_failed: 3`, but all eight ship with empty distractor lists and fail at layer 1 with `"Expected exactly 3 distractors, found 0."`. The dedup layer never executes in any test. Combined with the key mismatch, the suite reports 60/60 recall while the deduplication reason has never once been exercised.
- **The false-positive measurement uses a weaker chain than the recall measurement.** `tests/test_verification.py:108` calls `verify_item(item)` with no `spec`, while the adversarial test at `:68` passes `spec=sample_spec`. With `spec=None` the vocabulary ceiling check, the spec `forbidden` rules and one topic-leak branch are all skipped. Running the same 60 known-good items with a spec attached yields 16 rejections. The reported 0 percent false-positive rate is not measured against the chain that produces the recall number.
- **`TopicLeakValidator` uses substring containment.** `layer_topic_leak.py:35` tests `if ans_lower in part:` against the raw prompt segment rather than tokenising as its own section 1 does at `:24`. Answer `der` therefore matches inside `Kinder`, `oder`, `wieder`.

### Stage 5: one table added, everything else unchanged

**Added:** a `review_logs` table in `src/bank/migrations.py:68-78` with `append_review_log` and `get_review_logs` on `SqliteItemBank` (`storage.py:249-299`), written by the CLI at `app.py:137-144`. Verified: two `round` invocations produced 6 rows.

Three defects in it:

- **No `mode` column**, so the mode-filtered `tag_state` recomputation the table exists to support cannot be expressed.
- **No `facet` column**, though the plan treats the log as the source of truth for facet accuracy.
- **`fsrs_rating` is declared `INTEGER NOT NULL`** (`migrations.py:75`) but written as the `FsrsRating` string literal (`app.py:143`). Verified stored value: `'good'` with type `text`. SQLite's non-strict typing accepts it; the column will hold mixed types the moment the D1 sync or the PWA appends.

Everything else stands:

- **`facet` is still never derived.** It is read in `topic_state.py:105`, `scheduler.py:305`, `cli/app.py:171`, stored at `storage.py:50` and columned at `migrations.py:22`, but computed nowhere. `grep -rn facet src/generation src/verification src/taxonomy` returns nothing. The only facets in existence are hand-written literals in the 8-item fixture (`"sg1"`, `"masc_dat"`), which `01-foundation.md:168` explicitly forbids: "Facets are therefore derived, never hand-written for 85 topics." Any pipeline-produced item has `facet=None`, so **all 68 faceted topics remain unpromotable in production**.
- **`confusion_group` still never written at ingest.** All 8 fixture rows have it absent.
- **No `dimension` column and no `tag_id` column.** Verified by round trip: a `BankItem` inserted with `tag_id='dativ_nach_praeposition', dimension='grammar'` reads back as `tag_id: None` and the default `dimension`. Export is therefore lossy, and `test_referential_integrity_every_tag_id_exists` cannot be written against this schema.
- **`export_delta(since_id)` still does not exist**; `BankExport` is constructed only in a model-shape test.
- **Insert is still not idempotent.** `storage.py:34` is a plain `INSERT`; re-inserting raises `sqlite3.IntegrityError`. Note `carrier_lemmas` at `:76` does use `INSERT OR IGNORE`, so the inconsistency is visible in the same function.
- **`insert(list) -> InsertReport` and `stock(tag_id, difficulty)` still absent**; `InsertReport` is not a type in the repo.
- **No shared export-schema fixture.** `tests/test_web.py` still has the server asserting against itself.
- **Bank stock is still 8 items across 8 of 87 topics** against a DoD of 12 per topic. `MIN_STOCK_PER_TIER` is still 5.

### Stage 6: `src/engine/scheduler.py` was not modified

Every scheduler finding stands verbatim. Re-verified:

- **Six pacing constants still have zero readers outside `contracts.py`**: `MAX_REVIEWS_PER_DAY`, `VOCAB_RATIO_DEFAULT`, `SUGGESTION_WINDOWS`, `SUGGESTION_MIN_ACTIVE_DAYS`, `THRESHOLD_CLAMP`, `DUEL_MIN_ATTEMPTS_TO_SUGGEST`. `MIN_PREREQ_STABILITY` is still undefined anywhere.
- **`MAX_NEW_TOPICS_PER_DAY` day counter** is still a caller-supplied parameter defaulting to 0 (`scheduler.py:192`) that no caller ever supplies. With no state reload, every round re-enters at 0 and can introduce a new topic.
- **Two inconsistent forecasts** remain: `forecast()` still drops `day_offset < 0` so overdue backlog contributes zero to the debt-spiral guard, while `forecast_7day_load()` counts it differently.
- **Round assembly is still 4 of 12 rules.** Missing: prereq-stability filter, difficulty-tier matching (`:261` hardcodes `difficulty=1`), `seen_items` exclusion, `sibling_group` separation, grammar/vocab ratio, paragraph-gap counting, `requires_context` day-spreading, bonus-round flagging (`Round` and `RoundItem` still have zero construction sites), `eligible_types` override.
- **`_interleave_items` still inverts rule 3**, using `it.confusion_group or it.topic_id` as the bucket key and driving confusion-group members apart.
- **Review modes unchanged.** `build_duel` still pads with arbitrary unrelated items; `build_recalibration` still ignores its `states` argument and returns `all_items[:10]`; `challenge` still does not exist in Python, and `plan_next_round` silently falls through to the review path for it.
- **`mark_acquired_kalibrierung` is still dead code**, zero call sites. Calibration still uses `mark_acquired_inferred`, so every calibration-acquired topic gets the 4-day ceiling and demotes on the first failure rather than the second.
- **Kalibrierung is still not adaptive.** First 12 items by CEFR order, no branch on outcomes, no B1 start, no 35-item bound, no DAG propagation, no per-topic 2-of-2 rule. `calibration.py:81` still grades with `clean_ans in item.accepted_answers`, bypassing `ScopedTypoGrader`, which is not even imported there.

**Partial progress:** facet *plumbing* now works. `src/cli/sitting.py:90` passes `facet=item.facet` into `record_attempt`, and promotion fires correctly when a real facet is supplied. Executed: three unhinted passes with facets `['sg1','sg3','sg1']` promoted to `acquired`/`earned`. The derivation that would supply those facets in production still does not exist.

### Stage 7: interactive input added, grading unchanged, gate still unattempted

**Grading is byte-identical.** `src/engine/typo_grader.py:171` is still `grade(cls, user_input, accepted_answers)` with no `topic_id` and no `morph_spec`. Executed against the current code:

```
grade('großem', ['großen'])  -> is_correct=True   (wrong adjective declension)
grade('geht',   ['gehst'])   -> is_correct=True   (wrong person ending)
grade('kleinen',['kleinem']) -> is_correct=True   (dative/accusative confusion)
```

All three are scored `Richtig (Tippfehler)`. These are the "mutate the target morpheme, must fail" cases the plan's property test specifies. The only caller, `src/cli/sitting.py:67`, has `item.topic_id` available on the same line and does not pass it.

**Interactive input is partial.** `app.py:92-96` and `:131-134` branch on `sys.stdin.isatty()`. Real prompting exists in a terminal, but the non-tty path still auto-answers (`ans = item.accepted_answers[0]`), there is no injectable input seam for tests, and the empty-input substitution described in section 1 makes the collected data untrustworthy.

**Persistence is partial.** `review_logs` is written, but `run_cli` still rebuilds `TopicStateManager` from taxonomy on every invocation (`app.py:212-213`) and still passes `fsrs_records={}` (`:115`, `:121`). There is no read path from the log back into state. Executed proof: two consecutive `round --size 3` calls served the identical three items in the identical order.

**Still missing:** `grammar stats` prints item-bank statistics rather than topic states, stability and retention (`FSRSEngine.get_retrievability` exists and is never called from the CLI); `grammar report` prints a line and writes nothing; the hint content ladder for levels 1 to 4 exists only in JS, and `cmd_round` hardcodes `hint_level=0`; FSRS parity still asserts only `state` and `stability > 0`, never the next interval, and the fixture has no interval field to assert against; `docs/audits/stage-07-usage.md` does not exist.

**The gate remains unattemptable.** The CLI forgets all state between invocations, offers no hints, reports no progress, and scores empty answers as correct.

### Stage 8: two blockers fixed, one remains

Fixed: the `getAll` TypeError and the missing service-worker registration. `appendReviewLog` is now called at `web/app.js:480`.

**The third blocker is still open and now produces a silent no-op.** `saveTopicState` (`db.js:44`) and `saveFSRSCard` (`db.js:52`) still have zero call sites. `app.js:318` now reads topic state back, but nothing ever writes it, so the read is permanently empty and `statDueTopics` (`:362`) renders `0 Themen` forever.

**ts-fsrs is still absent.** No `package.json`, no vendored library, no CDN tag, no `.ts` file. `startNewRound()` is still `bankItems.slice(0, config.roundSize)` with no due queue, no rating, no interleaving. `fsrsRecords` is a dead `{}` written only by JSON import.

**The two graders still diverge.** The JS `GRAMMATICAL_MORPHEMES` set is missing 16 entries the Python set has (`unser` and its inflections, `als`, `denn`, `da`, `während`, `wegen`, `trotz`, `statt`, `anstatt`), and Python's 16-pair `CRITICAL_MINIMAL_PAIRS` has no JS counterpart, so JS will grade `hatte` to `hätte`, `war` to `wäre`, `konnte` to `könnte` as scoped typos where Python correctly fails them. The one-off `grosser`/`größer` hack is still at `app.js:145`.

**All eleven retention surfaces are unchanged.** Diff highlighting still renders whole answers rather than the differing morpheme; capitalisation errors are still graded wrong rather than "correct, but"; round preview, pace estimate, streak freezes and the progress report are still static or hardcoded; the coverage bar still has 2 segments not 3, still hardcodes A1 and A2 percentages with B1 and B2 never written, and still uses the forbidden word "Stufen"; the DAG map is still 6 hardcoded topics with no edges; the duel library is still 3 hardcoded cards with none of the 7 required columns; the daily challenge still grades via `includes('weil')`; and export still omits `review_log` and settings while import still assigns `tag_state` directly from the file, which `04-application.md:363` forbids.

Also unchanged: umlaut buttons append at end rather than at cursor; the challenge textarea lacks the required input attributes; the progress bar shows within-round position rather than due-queue clearance; `web/manifest.json` references icon files that do not exist, and `tests/test_web.py:27` asserts only `len(icons) >= 2`, never existence.

### Stage 9: one dead table added

`worker/schema.sql:43-54` adds a `review_logs` table. **It has zero writers.** `/sync` still inserts review events into `sync_events` as opaque JSON blobs (`worker/src/index.js:62-67`), and a repo-wide grep for `review_logs` outside `schema.sql` returns nothing on the worker side. It also carries only an autoincrement primary key, so the plan's idempotent-duplicate-rows requirement still cannot hold, and `sync_events` still has no `item_id` or `timestamp` columns at all, so the uniqueness constraint is not even expressible.

Everything else unchanged: `/bank/delta` still returns a hardcoded empty list; `/override`, `/explain` and `/grade` still return constants, with `/grade` passing every submission including an empty one; `userId` still falls back to `'anonymous'` so any caller can read or overwrite any user's rows via `X-User-ID`; method guards still exist only on the two sync routes; `/sync` still counts and discards `topic_states`; there is still no bank table, override queue, explanation cache or cost table; `src/sync/client.py:91-126` still does timestamp last-write-wins directly on `tag_state` with `tests/test_worker_sync.py:64` asserting it as correct; and `OVERRIDE_UPHELD_RATE_ALERT` still has exactly one occurrence, its declaration.

### Stage 10: unchanged

`src/generation/batch_client.py:168` still instantiates `MockBatchClient` in the `__main__` block both workflows invoke. Submit still builds one hardcoded `GenerationRequest(topic_id="dativ_nach_praeposition", ...)` and prints a batch id that dies with the process. **Ingest still defaults to `batch_001` against an empty `submitted_batches` and raises `KeyError` on every nightly run.** `batch_client.py` still has no `import os` and never reads `GEMINI_API_KEY`. `SAFETY_FACTOR`, `MIN_BATCH_THRESHOLD` and any nightly item cap still do not exist. The new spend ceiling in `GeminiLlmClient` is not imported by `batch_client.py`, so the workflow path still has no ceiling check. `nightly_batch.yml` still duplicates the `0 2 * * *` cron and still audits `data/bank.db`, which is not in the repo, so that job red-fails nightly. All three stage 10 tests are still tautologies that recompute the assertion inline.

### Stage 11: unchanged

Beyond the orphaned client covered in section 5: production grading still gates the verdict on grammatical accuracy (`is_pass = target_used and accuracy >= 0.75`) against a plan that says only the target-structure dimension may decide, and its `except` fallback still returns `is_pass=True, accuracy=1.0, naturalness=1.0` on any parse failure, silently passing everything. The explainer still has no cache and does not import `LlmCache`; the new cache is keyed on a prompt hash, not on `(item_id, user_answer)`. Minimal pairs is still a static dict of 3 drills whose `get_or_generate_drill` silently returns the `wechselpraepositionen` drill for any unknown group, and its `self.provider` is assigned and never called. The weekly report still has no window logic, no trigger, no manual gate and no rate limit; both trigger constants still have zero production readers.

---

## 7. Defects introduced by the remediation

1. **Empty input scores as correct.** `src/cli/app.py:94` and `:132`. Covered in section 1. Highest priority in this document.
2. **`GeminiLlmClient` cannot be injected** into any feature module: `generate(...)` versus the Protocol's `generate_text(...)` (`src/llm/provider.py:15`). The LLM layer is structurally unwireable.
3. **The spend ceiling is unreachable** because the free lane costs 0 and `free_lane_open` is never mutated (section 5).
4. **`review_logs` lacks `mode` and `facet` columns**, so the table cannot support the recomputation and facet-accuracy analysis it was added to enable.
5. **`review_logs.fsrs_rating` type mismatch**: declared `INTEGER NOT NULL`, written as a string, in both the SQLite and D1 schemas.
6. **The D1 `review_logs` table has zero writers.**
7. **The PWA topic-state read is a no-op** because `saveTopicState` still has no caller.
8. **`is_unhinted_pass=False` conflates failure with a hinted pass.** `src/engine/topic_state.py:110-112` maps it to `is_correct=False, hint_level=1`, so a learner who answers correctly with a hint would be recorded as a failure and could demote an acquired topic. Latent today (only calibration uses the keyword, always `True`) but a live trap.
9. **A docstring became a dead string literal.** `src/engine/topic_state.py:113-125`. The new `is_unhinted_pass` shim was inserted between the docstring and the block documenting the promotion and demotion rules, so those rules are now an unreachable expression statement. Ruff does not flag it because B018 is not enabled.
10. **The adversarial fixture key mismatch** (`expected_layer` versus `expected_layer_failed`) makes the per-layer assertion dead code.
11. **Eight `duplicate`-labelled adversarial items fail at layer 1** for having zero distractors, so the dedup reason has never been exercised while the suite reports full recall.
12. **The known-good false-positive test passes no `spec`**, measuring a weaker chain than the recall test.
13. **`GeminiLlmClient.__init__` and `LlmCache.__init__` create directories as a side effect** of construction, writing `.cache/` into the working directory on import-and-instantiate.
14. **`LlmCache.get` returns the string `"None"`** for a cache file with a null response instead of signalling a miss.

---

## 8. Work order

Ordered by what unblocks the most downstream work.

1. **Fix the empty-input substitution.** `src/cli/app.py:94` and `:132`. Two lines. Every hour the CLI is used before this lands produces corrupt FSRS ratings, corrupt promotion evidence and untruthful `review_logs` rows.
2. **Make `GeminiLlmClient` real and wire it in.** Give it a genuine transport, reconcile its signature with `LlmProvider` (or replace the Protocol), route `live_explainer`, `production_grader`, `minimal_pairs`, `weekly_report` and `batch_client` through it, make `free_lane_open` mutable on RPD exhaustion, and add the missing 14 stage 0 tests. Until this lands, invariant 4 is satisfied only on paper and stages 3, 4 and 11 have no path to working.
3. **Derive `facet` from `morph_spec` at ingest, and fix the `morph_spec` keys in the same change.** The plumbing is now in place and proven to work; only the derivation is missing. Eight of fifteen keys are invalid UD features and would silently disable the stage 4 morphology check anyway, so both belong in one pass. This unblocks promotion for 68 topics and enables split detection.
4. **Add `mode` and `facet` columns to `review_logs`, fix the `fsrs_rating` type, and write the recomputation path** that reads the log back into `tag_state` filtered to `mode in ("review","recalibration")`. This is what makes invariant 1 true and what makes the stage 7 gate possible.
5. **Fix the kill-gate semantics and run the real audit.** Redefine `error_rate` as post-verifier error on accepted items, wire `AnswerSetExpander` into the chain so `accepted_answers` is populated, fix the `expected_layer` key mismatch and the eight zero-distractor `duplicate` items, and give the known-good test the same `spec` the adversarial test gets. Then sample 100 accepted items with a second auditor from a different vendor per `CLAUDE.md` section 10.
6. **Wire spaCy `de_core_news_lg`.** Already a declared dependency, and it unblocks three things at once: lemmatisation for stage 2 banding, real morphology for stage 4 layer 2, and `morph_spec`-scoped typo tolerance for stage 7.
7. **Scope typo tolerance to `morph_spec`** once spaCy is available. Change the signature to accept `topic_id`. Wrong declension and conjugation endings currently pass as typos, which corrupts every FSRS rating the app records.
8. **Call `saveTopicState` and `saveFSRSCard` in the PWA**, then add real client-side scheduling with ts-fsrs, then port the Python `CRITICAL_MINIMAL_PAIRS` and the 16 missing morphemes into the JS grader.
9. **Add auth to the Worker** before it holds real user data, and give `review_logs` and `sync_events` the uniqueness constraints the sync contract depends on.
10. **Fix the nightly ingest `KeyError`** and the duplicated `0 2 * * *` cron, so the automation stops red-failing every night.

### CI changes that would catch the next round of this

- **Enforce the marker taxonomy:** `-m "not live"` on push, a `simulation` job on PRs to `main`, a `live` job nightly. The markers are declared and still unused.
- **Raise coverage to the gate or lower the gate.** Measured 84 percent against a configured floor of 85.
- **Enable ruff rule B018** so a docstring that silently becomes a dead expression statement is caught.
- **Promote the confusion-group check** in `src/taxonomy/validator.py:86-88` from warning back to error, or record the 12 singletons as accepted exceptions with reasons. Silently downgrading a failing check is invariant 7.
- **Add a JS toolchain**: `package.json`, `tsc --noEmit` and ESLint in CI, per `CLAUDE.md:181`. Nothing currently checks `web/` or `worker/` at all.
