# Plan vs. Code: Gap Analysis

Audit of the `Language_Learning_App` codebase against its stage plan documents.
Date: 14 August 2026.

**Scope of comparison:** `CLAUDE.md`, `00-index.md`, `01-foundation.md`, `02-content-pipeline.md`, `03-learning-engine.md`, `04-application.md`, `german-grammar-app-plan.md`, `README.md`, `COMMIT_RULES.md`.

**How this was verified:** full source read of `src/`, `tests/`, `web/`, `worker/`, `scripts/`, `.github/`, `data/`, plus a clean reconstruction of the repo in an isolated environment where the test suite, `ruff`, `mypy --strict` and coverage were executed, and where the taxonomy, fixtures and spec sheets were parsed and counted directly. Every count and every claim of absence below was checked against the tree, not inferred.

---

## 1. Summary

The repo is a complete architectural skeleton of the plan with the load-bearing substance replaced by mocks, hardcoded tables, and constants that are declared but never read. Every stage 0 to 11 has a module with the right name, the right class names, and passing tests. Most stages do not deliver the behaviour their plan document specifies.

The build is green and should not be read as a signal of completeness:

| Check | Result |
|---|---|
| `pytest tests/` | 100 passed, 0 failed |
| `ruff check src tests` | clean |
| `ruff format --check src tests` | 73 files formatted |
| `mypy --strict src/` | no issues, 54 files |
| `pytest --cov=src` | 84%, against a CI gate of `--cov-fail-under=85` |

Coverage is the only gate that is currently marginal, and it may move a point either way on exact dependency versions in CI. The tests pass because they largely assert that mocks return what the mocks were written to return, and because they are not the tests the plan specifies.

**Named-test coverage against the plan documents, counted test by test:**

| Stages | Present / named in plan |
|---|---|
| 0 | 0 / 19 |
| 1 | 7 / 28 |
| 2 | 1 / 9 |
| 2b | 0 / 9 |
| 2c | 1 / 8 |
| Foundation total | **9 / 73** |

Stages 3 to 11 name roughly another 180 tests. The repo contains 100 tests in total across all fourteen stages.

**Both stage gates are unsatisfied, and the work past them was done anyway.** Stage 4 is a kill gate whose quantity has never been measured; stage 7 is a usability gate that cannot be attempted with the current CLI.

---

## 2. Stage status

| Stage | Name | Plan doc | Status |
|---|---|---|---|
| 0 | Repo scaffold & CI | `01-foundation.md` | **PARTIAL.** CI scaffold real; the LLM client, cost log, spend ceiling, two-lane architecture and cache are all absent |
| 1 | Taxonomy & DAG | `01-foundation.md` | **DIVERGED.** Strong content (87 topics, valid DAG); contract materially narrower than spec; `morph_spec` keys mostly invalid |
| 2 | Corpus & lexicon | `01-foundation.md` | **MISSING as specified.** 8 seed sentences against a DoD of 20,000; no frequency bands; spaCy never used |
| 2b | Learner error corpus | `01-foundation.md` | **DIVERGED.** The mapping is unfalsifiable: rules and external anchor were authored as one closed set |
| 2c | Exercise scraping | `01-foundation.md` | **MISSING.** One parser over one synthetic fixture; no scraper exists |
| 3 | Batch generation | `02-content-pipeline.md` | **PARTIAL.** 87 spec sheets exist; no real batch client, no API call anywhere |
| 4 | Verification chain | `02-content-pipeline.md` | **DIVERGED. KILL GATE UNMEASURED** |
| 5 | Bank storage & export | `02-content-pipeline.md` | **PARTIAL.** Schema good; no delta export, no facet derivation, insert not idempotent |
| 6 | Scheduler & Kalibrierung | `03-learning-engine.md` | **PARTIAL / DIVERGED.** About half the pacing rules unenforced; duel, challenge and recalibration are stubs |
| 7 | CLI & grading | `03-learning-engine.md` | **PARTIAL. USABILITY GATE NOT ATTEMPTED.** CLI has no persistence and auto-answers itself |
| 8 | Offline PWA | `04-application.md` | **MISSING / DIVERGED.** No ts-fsrs, no SW registration, a fatal init bug, 6 of 11 UX surfaces absent |
| 9 | Worker, D1, sync | `04-application.md` | **MISSING.** Three of five endpoints return constants; no `review_log` table; no auth |
| 10 | Nightly automation | `04-application.md` | **PARTIAL.** Correct crons wrapping a mock; the ingest job crashes every night |
| 11 | Live LLM features | `04-application.md` | **MISSING.** Zero API calls in the entire repo |

---

## 3. CLAUDE.md invariant compliance

`CLAUDE.md` section 2: *"These are invariants. Violating any of them is a defect regardless of whether tests pass."*

| # | Invariant | Status |
|---|---|---|
| 1 | An LLM is never the source of truth for user progress; all progress computed in code from `review_log` | **Cannot be satisfied.** No `review_log` table exists: not in `src/bank/migrations.py`, not in `worker/schema.sql`. No progress number is computed from a log |
| 2 | No item may name or hint at the grammar topic it tests | **Upheld for generated items** (blocklist at `src/generation/prompt_builder.py:15,52`, applied at `src/verification/layer1_syntax.py:45`). **Not applied to scraped items.** The one scraping fixture's heading is "Setzen Sie den richtigen Artikel im Dativ ein (Wo?)", and `parse_html_string` discards it rather than recording and checking it |
| 3 | No LLM call on the critical path of answering an exercise | **Upheld.** `web/app.js:463` and the Python grader are both pure local. The one invariant fully honoured |
| 4 | Every LLM call goes through `src/llm/client.py` | **Violated structurally. The file does not exist** |
| 5 | One item, one `tag_id` | Upheld in the contract |
| 6 | `accepted_answers` is always a list | Upheld (`src/contracts.py`) |
| 7 | Do not weaken a failing test to make it pass | **Violated twice.** `src/taxonomy/validator.py:86-88` downgrades the "confusion group needs two members" check from error to warning, so `report.is_valid` stays `True` over twelve singleton groups. `tests/test_taxonomy.py:92-95` writes the golden fixture when missing, then asserts against what it just wrote |
| 8 | Do not change a contract in the stage docs without flagging it | **Violated.** See section 5 |

### 3.1 The missing cost architecture

`CLAUDE.md` section 9 and `01-foundation.md` stage 0 specify a complete spend-control system. A grep over `src/` for each component returns zero hits:

`client.py` · `cost_log` · `BudgetExceeded` · `cache` · `lane` · `GEMINI_FREE_API_KEY` · `GEMINI_PAID_API_KEY` · `restrict_user_content_to_paid_lane` · `config.yaml`

So there is no single entry point, no per-call cost rows, no month-to-date spend read, no `BudgetExceeded` raised at the 5 EUR ceiling, no free/paid lane split, no RPM-versus-RPD 429 discrimination, no overflow accumulation into a single batch, no local content-addressed cache (which the plan also designates as the crash-recovery idempotency mechanism), and no privacy flag.

`.env.example` declares one key, `GEMINI_API_KEY`. The two-lane setup requires two, in two separate Google Cloud projects, which `CLAUDE.md:217` calls "mandatory, not an optimisation" because quota is per project and enabling billing destroys the free tier.

The nearest existing thing is `CostTracker` (`src/generation/batch_client.py:29-60`): in-memory, per-batch, generation-only, no persistence, no lane, no ceiling enforcement, and it never raises.

`01-foundation.md:91` is explicit about the guard that was meant to prevent this:

> The SDK-import scan test is the enforcement mechanism for rule 4 in `CLAUDE.md`. It is not decorative. Write it now, before there is anything to violate it.

`test_llm_client_is_the_only_module_importing_the_sdk` was not written. There is currently nothing to violate it because there is no SDK import at all, but there is also no wrapper for future calls to route through.

### 3.2 Other CLAUDE.md standards not met

- **Section 8, TypeScript for `web/` and `worker/`, `tsc --noEmit` and ESLint clean.** Zero `.ts` files in the repo. No `tsconfig`, no `package.json`, no ESLint config. `ci.yml` has no JS or TS step of any kind.
- **Section 9, "Model IDs live in one config block. No model string appears inline anywhere in `src/`."** Violated at `src/generation/batch_client.py:34-35` (pricing dict keys) and `:87` (`model="gemini-3.5-flash-lite"` hardcoded inside `MockBatchClient.submit`).
- **Section 8, Python 3.12.** `pyproject.toml` sets `requires-python = ">=3.11"`, ruff `target-version = "py311"`, mypy `python_version = "3.11"`. CI installs 3.12.
- **Section 8, injected clock.** `datetime.now()` is called directly at `src/engine/scheduler.py:69,198`, `src/engine/topic_state.py:25,41,52,68,88,126`, `src/engine/fsrs.py:18,105,120`.
- **Stage 0 deliverable `hypothesis`.** Not a dependency, despite `CLAUDE.md:163` calling for property-based tests where stage documents specify them.
- **Section 7, `tests/unit/`, `tests/integration/`, `tests/simulation/`.** Tests are flat in `tests/`. The `simulation` and `live` markers are declared in `pyproject.toml` and used nowhere.
- **`.gitignore`**, a stage 0 deliverable, is absent from the tracked tree.

---

## 4. Repository layout divergence

`CLAUDE.md` section 3 specifies a layout. Several mismatches are load-bearing rather than cosmetic.

| Specified | Reality | Consequence |
|---|---|---|
| `docs/` holding `00-index.md` through `04-application.md` and `docs/plan/` | All six sit at repo root; `docs/` holds only `audits/` | Cross-references to `docs/` paths in `CLAUDE.md` and the stage docs resolve to nothing |
| `data/taxonomy/topics.yaml` + `confusion_groups.yaml` | `data/taxonomy.yaml`, single flat file, confusion group as an inline string field | Confusion groups have no independent artefact to second-agent diff |
| `data/taxonomy/errant_mapping.yaml` **[AGENT-CRITICAL]** | Absent; rules hardcoded at `src/corpus/learner_errors.py:151-165` | An agent-critical artefact exists only as Python literals, so it cannot be diffed or reviewed as data |
| `data/fixtures/taxonomy/goethe_inventory_checklist.yaml` | Absent | The taxonomy's only external anchor is missing |
| `ATTRIBUTION.md` | Absent | Stage 2 DoD item, and a licensing exposure |
| `src/llm/client.py`, `src/llm/cache.py` | Absent | Invariant 4 |
| `src/scheduler/` | `src/engine/` | Cosmetic |
| `tests/unit|integration|simulation/` | Flat `tests/` | Marker taxonomy unenforced |
| `config.yaml` | Absent | Privacy flag has nowhere to live |

---

## 5. Contract drift

`CLAUDE.md` rule 8 and `00-index.md:44` both require a stage-document update in the same PR as any contract change.

| Constant | Plan | Code |
|---|---|---|
| `TagState` | `unseen, learning, acquired` (3), `00-index.md:51` | `unseen, learning, acquired, locked, ready, dormant` (6), `src/contracts.py:14` |
| `MODEL_LIVE` | `gemini-3.5-flash-lite`, `00-index.md:82` and `CLAUDE.md:197-199` | `gemini-3.7-flash`, `src/contracts.py:66` |
| `MODEL_GENERATE` | `gemini-3.5-flash-lite`, thinking off, `00-index.md:83` and `CLAUDE.md:200-202` | `gemini-3.7-flash`, `src/contracts.py:67` |

`TagStateModel.state` also defaults to `"locked"` (`src/contracts.py:240`), not `"unseen"`.

### 5.1 Model routing defect

`MODEL_VERIFY = "gemini-3.7-flash"` is correct and intended. `MODEL_LIVE` and `MODEL_GENERATE` were swapped to `gemini-3.7-flash` at the same time and should not have been. Both should read `gemini-3.5-flash-lite`.

The consequential one is `MODEL_GENERATE`, which puts the highest-volume workload on the verifier tier: by the repo's own price table (`src/generation/batch_client.py:34-35`) that is roughly 2x input and 2x output cost against a 5 EUR per month ceiling that is not enforced anywhere. `CostTracker.PRICING_PER_MILLION` still prices `gemini-3.5-flash-lite`, so the cost model and the contract disagree.

**Fix:** set `src/contracts.py:66-67` to `"gemini-3.5-flash-lite"`. `00-index.md`, `CLAUDE.md` and `.env.example` are already correct and need no change.

### 5.2 Constants declared but never read outside `contracts.py`

Verified by grep across `src/`:

`MAX_REVIEWS_PER_DAY` · `VOCAB_RATIO_DEFAULT` · `SUGGESTION_WINDOWS` · `SUGGESTION_MIN_ACTIVE_DAYS` · `THRESHOLD_CLAMP` · `DUEL_MIN_ATTEMPTS_TO_SUGGEST` · `OVERRIDE_UPHELD_RATE_ALERT` · `WEEKLY_REPORT_TRIGGER_ITEMS` · `WEEKLY_REPORT_MANUAL_MIN_ITEMS` · `NO_ERROR_ITEM_SHARE`

Each is a feature the plan specifies. Ten declared, zero enforced. `MAX_NEW_TOPICS_PER_DAY` and `MAX_HEAVY_PER_ROUND` are read; see section 8.

### 5.3 Declared dependencies never imported

`pyproject.toml` declares `spacy`, `google-genai` and `httpx`. None is imported anywhere in `src/`. This is the clearest single indicator of the stub-versus-implementation pattern: every "spaCy morphology analysis" and every "model call" in the plan is, in the code, a hardcoded table or a regex.

---

## 6. Stages 0 to 2c: foundation

### Stage 0

CI itself is honest. `ci.yml` runs `ruff format --check`, `ruff check`, `mypy --strict src/`, and `pytest --cov=src --cov-fail-under=85`, with no `continue-on-error`.

Everything else the stage is about is absent, per section 3.1. **Named tests: 0 of 19.** Two near-misses do not qualify:

- `tests/test_application_pipeline.py:28-36` computes `is_blocked = tracker.total_cost_usd >= ceiling_usd` inside the test body and asserts the comparison it just wrote. No production code consults a ceiling.
- `tests/test_generation.py:127` asserts `total_cost_usd > 0.0` after a batch submit: per batch, not per call, with no row schema.

**DoD: 0 of 6.** `docs/audits/stage-00-quota.md` exists and has a routing table plus a generic quota line, but contains no per-million prices for any model, no per-project observed figures, and no swap analysis. The DoD explicitly requires pricing there, and both `CLAUDE.md:209` and `01-foundation.md:110-112` spend a paragraph each on why.

### Stage 1

**The content is the best work in the repo and is worth protecting.** Verified directly: 87 topics (band is 75 to 90), distributed A1 21 / A2 22 / B1 24 / B2 20; the prereq DAG is acyclic with 150 edges and zero dangling references; no prereq edge inverts CEFR (0 violations, though nothing tests this); 34 confusion groups; `rule_hint` and `intro_card` populated for all 87.

The contract is materially narrower than `01-foundation.md:142-159`:

| Spec field | Status |
|---|---|
| `id`, `name_de`, `cefr`, `prereqs`, `confusion_group`, `description`, `eligible_types`, `requires_context` | Present |
| `morph_spec: dict[str, str]` | Typed `dict[str, Any] \| None`; optional where the spec requires it |
| `rule_hint: str` | Typed `str \| None`; populated for all 87 but optional in the type |
| `sibling_group` | On the model, **set on 0 of 87 topics**, yet `src/engine/scheduler.py:144` interleaves on it. Permanent no-op |
| `intro_card: str` | **Type mismatch.** Spec says a static string; code is a nested `IntroCard` model |
| `derived_from`, `split_into`, `split_axis` | **Absent from the model and the data.** The topic-splitting lineage mechanism has no representation |
| `class Taxonomy` with `topological_order()`, `transitive_prereqs()`, `descendants()` | **Does not exist.** `topological_order` has zero hits repo-wide. The other two exist only as methods on `TaxonomyValidator`, not on the contract type downstream stages were told to depend on |

Three data defects the specified tests would have caught, all verified directly:

1. **`morph_spec` keys are mostly not Universal Dependencies features.** Of 15 distinct keys, only 7 are real UD features (`Case` 20, `Tense` 16, `Mood` 6, `Voice` 6, `Aspect` 4, `Number` 1, `Degree` 1). Eight are invented: `Pos` (17), `Subordinate` (12), `VerbType` (7), `ArtType` (6), `Declension` (3), `Reflexive` (2), `VowelChange` (1), `Separable` (1). UD uses `Reflex`, not `Reflexive`, and has no `Pos`, `ArtType`, `Declension`, `Subordinate`, `VowelChange` or `Separable` feature. `01-foundation.md:267-269` names this exact failure mode: keys that "would silently disable the stage 4 morphology check".
2. **Twelve singleton confusion groups:** `nomen_endungen`, `modalverben_bedeutung`, `verben_trennbar_praefix`, `satzbau_modus`, `wortstellung_objekte`, `adjektiv_vergleich`, `temporal_praep`, `pronominaladverbien`, `temporal_als_wenn`, `finale_konnektoren`, `modalpartikeln_nuance`, `diskurs_konnektoren`. A group of one cannot produce interleaved contrast. 22 real groups remain, still above the DoD's eight.
3. **All six `requires_context` topics are also eligible for single-sentence types.** Clearest cases: `plusquamperfekt` and `modalpartikeln`, both eligible for `cloze_free` and `cloze_cued`. `01-foundation.md:213-216` calls this "the assertion that stops a single-sentence item from pretending to test indirect speech". Only 6 topics are `requires_context` against the spec's "roughly 8 to 12".

Separately, **`morph_spec` is specified to define facets** as the features it leaves unspecified (`01-foundation.md:168`), so facets are derived rather than hand-written. **No facet derivation exists anywhere in the repo.** This is the root of the stage 6 deadlock in section 8.

**Named tests: 7 of 28.** Absent: the CEFR-ordering check, snake_case validation, the two-member confusion-group check, all six lineage and split tests, `test_facet_space_derivable_from_morph_spec`, `test_morph_spec_keys_are_valid_universal_dependencies_features`, `test_goethe_inventory_coverage`. Two present tests are weaker than specified: the intro-card worked-example check asserts one example where the spec says two (the data does satisfy two), and `transitive_prereqs` is tested for membership but never for self-exclusion.

**DoD: 5 of 8.** The two that matter: `goethe_inventory_checklist.yaml` does not exist, so the external anchor `01-foundation.md:137` calls the point of the stage is absent and `test_goethe_inventory_coverage` cannot run; and no second-agent diff or conflict list exists anywhere for the DAG or confusion groups, which the agent-critical protocol requires since those two artefacts have no external key.

### Stage 2

`SeedSentence` and `Lexicon` do not exist. `CarrierSentence` (`src/corpus/tatoeba.py:32-41`) is missing `max_freq_band`, `licence`, `attribution`, `lemmas` and `source`. `01-foundation.md:338` says "licence hygiene is enforced by the schema, not by memory"; the schema cannot enforce fields it does not have. `VocabularyStore` implements none of `band()`, `in_goethe_list()` or `level_ceiling_ok()`.

- **`data/fixtures/corpus/tatoeba_sample.tsv` contains 8 sentences.** The DoD requires at least 20,000 surviving filters. There is no ingest script for the real Tatoeba dump; `scripts/` has step1 through step4 and none touches Tatoeba.
- **Leipzig frequency bands are absent entirely.** `FrequencyBander` is not frequency banding: with no word list supplied it degenerates to "is any word at least 12 characters" (`src/lexicon/frequency.py:26-31`). Band coverage is unmeasurable.
- **spaCy `de_core_news_lg` is never used.** One hit repo-wide, the dependency line. Tokenisation is a regex; "lemmas" are surface tokens of length 3 or more minus a function-word set. **No lemmatisation exists**, so separable verbs cannot be handled, which `01-foundation.md:363` singles out as the failure that "silently corrupts vocab tagging". Morphology extraction is also unavailable to stage 4, which is why that chain is hand-rolled.
- Three of four filtering rules do not exist: no proper-noun whitelist, no finite-verb check, no parse check. Token bounds are 3 to 30 against a spec of 4 to 18.
- Umlaut normalisation is `.lower()` only, so `"Straße"` and `"Strasse"` are distinct keys.
- `vocab_levels.json` (11,626 entries) is a surface-form dump from PDF regex extraction, not lemmas: it contains inflected forms and noise assigned to A1 (`"spiele"`, `"antwortet"`, `"kurzes"`, `"geh"`, `"fitzpatrick"`).
- **Bug:** `scripts/step1_extract_vocab.py:28` calls `extractor.extract_from_directory(raw_dir)`, which does not exist on `WordlistPdfExtractor`, then iterates the result with the wrong shape. It is shielded from failure because `tests/test_lexicon.py:135-142` short-circuits when `data/raw/` is absent, and because `ci.yml` scopes ruff to `src tests` and mypy to `src/`, so **`scripts/` is linted by nothing**.

**Named tests: 1 of 9. DoD: 0 of 4.**

### Stage 2b

`LearnerErrorItem` does not exist. `LearnerError` is missing `feature_delta`, `governor_pos` and `governor_lemma`, the three fields the mapping pipeline is built on. `cefr` defaults to `"A2"` rather than `None`, so unknown CEFR silently reads as A2.

Of the four pipeline steps in `01-foundation.md:405-425`, none is implemented as specified:

1. **ERRANT tag filter: absent**, and inverted in practice. Running the mapper against real ERRANT-German tags, **every tag on the spec's keep-list returns `None`**: `R:DET:FORM`, `R:VERB:FORM`, `R:PRON`, `R:WO`, `M:PREP`. Meanwhile `R:CONJ:SUBORD` maps to `nebensatz_weil_da`, and `CONJ` is on the spec's discard list.
2. **Morphological diff: absent.** Nothing computes `{"Case": ("Acc", "Dat")}`.
3. **Context routing via `errant_mapping.yaml` [AGENT-CRITICAL]: absent.** Replaced by a 13-entry dict plus a substring cascade over raw context text that checks `"in" in context_lower`, which matches inside a large fraction of German words.
4. **Single-error filter: absent.** No edit counting anywhere.

**The golden fixture is circular.** `mapped_sample.jsonl` has 100 rows, and all 100 have a non-null `mapped_topic_id`: **100% yield against a specified band of 15% to 30%**, in the direction `01-foundation.md:460-462` calls "the dangerous direction: it mislabels errors and corrupts the confusion-group analysis downstream". The 23 tag strings are not ERRANT tags: `R:PREP:WECHSEL`, `R:PRON:REL`, `R:DET:CASE:GEN`, `R:VERB:TENSE:FUT2` and so on. Real ERRANT deliberately cannot say "Wechselpräposition"; deriving that is the entire work of this stage, and these labels encode the answer. The fixture's tag set and the mapper's heuristic keys are the same closed set, authored together. The golden test never invokes the mapper: it asserts that each topic id exists in the taxonomy and that each row id starts with `merlin_`. **The 0.85 agreement rate is never computed.** Line 184 asserts every row is mapped, the exact inverse of the specified `test_unmapped_items_are_retained_with_topic_id_none`.

`EmpiricalConfusionMatrix` is a working data structure that is never fed any corpus.

**Named tests: 0 of 9. DoD: 0 of 5.**

### Stage 2c

`ScrapedItem` does not exist. `ScrapedExercise` is missing `source_url`, `prompt_raw`, `prompt_stripped`, `mapped_topic_id`, `cefr_claimed`, `scraped_at`, `source_topic_label` and `exercise_type`.

Two substitutions are actively harmful:

- **`topic_id` is required and injected by the parser constructor**, defaulting to `"dativ_nach_praeposition"`. The spec's "low-confidence mappings are left null rather than guessed" (`01-foundation.md:525`) is structurally impossible: every scraped item is force-labelled with whatever the caller passed.
- **With no `prompt_raw` / `prompt_stripped` pair there is no way to verify that stripping happened.** In the one fixture the discarded heading is "Setzen Sie den richtigen Artikel im Dativ ein (Wo?)", a textbook topic leak, dropped silently rather than recorded and checked. `01-foundation.md:526` says the stripping "must be verified rather than assumed".

There is no scraper: no `robots.txt` handling, no rate limiting, no user agent, no HTML disk cache, no network code. **One parser, not the DoD's two**, and its frozen fixture is 22 lines of hand-written synthetic markup (`<li data-id="scraped_001" data-answer="dem">`) rather than a capture of the real site, so it cannot serve its stated purpose of detecting a site redesign.

The downstream cost exceeds the stage. `02-content-pipeline.md:270` requires `known_good.jsonl` to be sourced from scraped teacher answer keys, because "agent-generated 'known good' items are only agreement with the generator". And the highest-value deliverable, 20 to 30 human-authored gold examples per topic family wired into spec sheets, did not happen: **all 87 spec files carry exactly 3 gold examples each, 261 total**, none with a provenance field and none traceable to scraped material.

**Named tests: 1 of 8. DoD: 0 of 4.**

---

## 7. Stages 3 to 5: content pipeline

### Stage 3

**87 spec sheets exist**, one per taxonomy topic, carrying the specified fields.

The client is not real. `src/generation/batch_client.py:62` defines `MockBatchClient`; `main()` at `:167` instantiates it. `MockBatchClient.submit:85` fabricates token counts as `total_items * 150` in and `* 180` out. `CostTracker.records` is in-memory only; the `batches` table exists but nothing in the generation path writes to it.

Also missing:

- `CandidateItem.block_id` and `block_position`, so paragraph-cloze gaps have nowhere to land.
- Paragraph-block validation ("no two adjacent gaps share a `tag_id`", "reject if every gap is context-free") exists only as English text inside the prompt payload.
- `NO_ERROR_ITEM_SHARE` is never referenced, so no-error error-correction items are never requested.
- `build_spec_for_topic` **fabricates gold examples**, padding to three with `"Hier steht Beispielsatz Nummer {idx} mit ___ Lücke."` (`src/generation/spec.py:94-102`), against a plan that calls a leaky or wrong gold example the thing that "teaches the generator to leak across every item that topic ever produces".
- `extra="allow"` on every contract plus `parse_distractors` silently dropping bad entries means malformed model output is coerced rather than rejected.

`.github/workflows/generate-submit.yml:30` passes `GEMINI_API_KEY` into a code path that instantiates `MockBatchClient` and never reads the key. **The nightly generation submits nothing.**

### Stage 4: the kill gate measures the wrong quantity

This is the most consequential finding in the audit.

`00-index.md:32` requires: audit 100 items and compute the **post-verifier error rate**, meaning of 100 items the chain **accepted**, how many are still wrong, judged by two independent auditors.

`src/verification/pipeline.py:175-176`:

```python
error_rate = round(failed_count / len(candidates), 4)
kill_gate_tripped = error_rate > kill_gate_threshold
```

That is the **candidate rejection rate**. The two are opposite signals: under this implementation a more effective verifier trips the gate. `docs/audits/stage-04-2026-08-13.md:37` states the inversion explicitly, and `tests/test_verification.py:139` asserts the drop-rate semantics, so the divergence is locked in by test.

**The gate quantity has never been measured.** The committed audit reports fixture recall (100%) and false-rejection rate (0%) over 30 adversarial plus 30 known-good items. The plan specifies 60 plus 60 fixtures and a **separate** 100-item sample of accepted output. There is no second auditor, no different vendor, no recorded disagreements, and no statement of the correlated-auditor limitation, all required by `CLAUDE.md` section 10. The audit cites `adversarial_suite.jsonl`, which is deleted from disk while still tracked in git, and names the constant `KILL_GATE_DROP_THRESHOLD` while the code calls it `VERIFICATION_KILL_GATE_THRESHOLD`.

`CLAUDE.md:274`: *"Report the kill criteria honestly. Stage 4 has a measured error-rate threshold that determines whether the project continues. Do not tune the audit to pass it."*

100% recall and 0% false positives is implausible given what the chain contains:

| # | Plan layer | Code | Real or stub |
|---|---|---|---|
| 1 | Schema validation | `layer1_syntax.py:17` | Real |
| 2 | Topic-leak check | blocklist in Layer 1 plus `layer_topic_leak.py` | **Blocklist only.** Plan specifies a cheap batched model pass |
| 3 | Answer-set expansion | `layer_expander.py` | **Stub, and dead.** 16 hardcoded contractions, never called from `pipeline.py`, 25% test coverage |
| 4 | Morphology (spaCy) | `layer2_morphology.py` | **Not spaCy.** Hardcoded word lists for exactly 2 topic IDs plus one regex. **65 of the 68 topics with a `morph_spec` get no morphological check at all** |
| 5 | Level check | folded into Layer 1 | Real, but silently skipped unless both `vocab_store` and `spec` are passed |
| 6 | Embedding dedup | `dedup.py:41` | **Not embeddings.** Jaccard token overlap at 0.85; only runs against an existing bank, never within a batch |
| 7 | Human audit, 5% sample | | **Missing** |

The claimed audit run covered `kasus_wechselpraeposition`, which contains exactly the two topics Layer 2 hardcodes. Whatever was measured was measured on the only topics with real morphology coverage.

Two further contract problems: `VerificationResult.accepted_answers` and `.rejections` are **never populated** by any return path, so the bank stores only the single proposed answer. And `RejectionReason` from the plan does not exist; the code uses a different `ErrorTaxonomy` vocabulary, and `ErrorClassifier.classify` routes nearly every real layer message to `pedagogical_flaw`, making `topic_leak`, `structural_malformation` and `morphosyntactic_error` unreachable. The plan's stated purpose for recording reasons, diagnosing a bad spec sheet from its rejection profile, is defeated.

### Stage 5

The schema is sound: `items`, `distractors`, `carrier_lemmas`, `verification_log`, `batches`, `schema_version` plus indexes, with a re-runnable `run_migrations`. The export `manifest.json` shape matches `web/data/manifest.json` exactly.

- **`facet` is never derived at ingest.** No facet computation exists in `src/generation`, `src/verification` or `src/bank`; the only hits are field declarations and consumers in `src/engine`. Because `PROMOTION_MIN_DISTINCT_FACETS = 2`, **no faceted topic can ever be promoted out of `learning`**. Stage 6 depends on a stage 5 invariant with no implementation, and the derivation rule was specified back in stage 1.
- **`confusion_group` is never written at ingest**, though the plan notes the offline minimal-pair fallback cannot derive it at runtime.
- **`dimension` has no column**, so the grammar/vocab split does not survive a round trip. `tag_id` exists on the model but is never written or read; `topic_id` is the real field. Vocabulary items cannot be represented.
- **`export_delta(since_id)` does not exist.** Zero hits. `BankExport` is declared and never constructed. `worker/src/index.js:45` returns a hardcoded `delta_items: []`. Both ends of the delta contract are stubs, despite `README.md:52` advertising delta export as a stage 5 deliverable.
- **Insert is not idempotent.** `src/bank/storage.py:31` uses plain `INSERT`; re-inserting raises `IntegrityError`. Untested.
- `insert(list) -> InsertReport` and `stock(tag_id, difficulty)` do not exist; `InsertReport` is not a type in the repo.
- **The shared export-schema fixture is missing**, so server and client cannot diverge-detect.
- DoD requires cold-seeding 12 items per topic across A1 to B2. `web/data/manifest.json` records the actual result: **8 items total, 1 per topic, 8 of 87 topics.** `MIN_STOCK_PER_TIER` is 5, not 12.

---

## 8. Stages 6 and 7: learning engine

### What is real

py-fsrs is used properly and is not hand-rolled, with a correct rating map, lapse counting and retrievability. The forecast gate, the heavy-item cap, the dormancy-to-recalibration switch, the split-candidate detector, promotion (3 unhinted passes, 2 facets), demotion asymmetry, the `INFERRED_STABILITY_CEILING_DAYS` clamp, and the hint-to-rating ladder are all present and correctly shaped.

### Pacing constants: declared versus enforced

| Constant | Enforced in `scheduler.py`? |
|---|---|
| `MAX_HEAVY_PER_ROUND` | Yes (`:244`, `:278`) |
| `FORECAST_HORIZON_DAYS`, `FORECAST_LOAD_THRESHOLD_DEFAULT` | Yes |
| `ROUND_SIZE_DEFAULT` | Used as a cap; the 4 to 10 range is never validated |
| `MAX_NEW_TOPICS_PER_DAY` | Read at `:256`, but **the day counter is a parameter defaulting to 0 with no persistence**, so ten rounds in one process each pass 0 and each introduce a topic. Precisely the bug the round/day distinction exists to prevent |
| `MAX_REVIEWS_PER_DAY` | **Never imported.** No backlog cap, no overdue-ordered slipping |
| `VOCAB_RATIO_DEFAULT` | **Never referenced.** No grammar/vocab mixing exists; `dimension` is never read |
| `SUGGESTION_WINDOWS`, `SUGGESTION_MIN_ACTIVE_DAYS`, `THRESHOLD_CLAMP` | **Never used.** `ThresholdSuggestion` is a bag of hardcoded defaults with no computation function |

**Two inconsistent forecasts.** `forecast()` (`:90-95`) drops any state with `day_offset < 0`, so overdue backlog contributes zero: 200 overdue tags yield `[0,0,0,0,0,0,0]` and `new_topics_allowed = 2`, defeating the debt-spiral guard exactly when it matters most. `forecast_7day_load()` (`:63-81`) counts overdue but buckets them under past dates and takes a max. `day_budget` uses the first, `plan_next_round` the second.

### Round assembly: 12 rules specified, 4 implemented

Missing: prerequisite-stability filter (`MIN_PREREQ_STABILITY` is not defined anywhere, and the fill step at `:272-285` pulls arbitrary bank items with no topic-state, prereq or seen filter, so locked-topic items land in rounds); difficulty-tier matching to stability; `seen_items` exclusion (the identifier appears nowhere in the repo); `sibling_group` separation; grammar/vocab ratio; paragraph gaps counting individually; `requires_context` day-spreading; bonus-round flagging (`Round` and `RoundItem` are never constructed anywhere); `eligible_types` override.

**Rule 3 is inverted.** The plan says prefer same-`confusion_group` neighbours. `_interleave_items:159` uses `it.confusion_group or it.topic_id` as the bucket key, which forces confusion-group members apart.

### The facet deadlock

`facet` is never computed at ingest, so `new_facets` in `src/engine/topic_state.py:133-137` stays empty and `facet_condition_met` is permanently `False` for every topic with a `morph_spec`. **All 68 faceted topics can never reach `acquired`.** The same cause collapses every row in `detect_split_candidates` to `"default"`, so a split candidate can never be raised. Since the lineage fields (`split_into`, `derived_from`, `split_axis`) do not exist on `Topic`, a split could not be represented even if one were detected.

### `acquired_via`

`mark_acquired_kalibrierung` (about 10 days stability) is **dead code with zero call sites**. Calibration instead calls `mark_acquired_inferred` for topics the user answered correctly, so everything gets the 4-day ceiling and demotes on a single failure. The plan's four-row table is implemented for none of its rows.

### Review modes

| Mode | Status |
|---|---|
| `review` | Partial |
| `duel` | **Stub.** Filters by `confusion_group`, then pads with arbitrary unrelated bank items. No `duel_seen` set, no minimal-pair preference, no lock-until-both-introduced, no ranking, no CLI command |
| `challenge` | **Missing in Python entirely.** Only a string literal and a PWA view |
| `recalibration` | **Stub.** `build_recalibration` ignores its own `states` argument and returns `all_items[:10]` |

### Kalibrierung is not adaptive, and answers itself

It takes the first 12 or fewer items by CEFR order; `next_item` returns the next unanswered item in list order. No branch on consecutive outcomes, no start-at-B1, no termination condition, no 35-item bound, no DAG propagation, no per-topic 2-of-2 rule. `evaluate_diagnostic` grades with `clean_ans in item.accepted_answers`, bypassing `ScopedTypoGrader`, so scoped typos fail during calibration and pass during review.

`src/cli/app.py:90` reads `responses = [(it, it.accepted_answers[0]) for it in items]`. The user is never prompted; every probed topic is marked acquired on every run.

### Grading is approximated where the plan requires scoping

The plan's signature is `grade(expected, given, topic_id)` with typo tolerance **scoped to the tested morpheme**, derived from `morph_spec`. The code's `grade(user_input, accepted_answers)` takes no `topic_id` and no `morph_spec`. Scoping is approximated by an 80-word hardcoded closed-class list plus a 16-entry `CRITICAL_MINIMAL_PAIRS` set; anything outside those two sets gets blanket edit-distance-1 tolerance:

- `grade("großem", ["großen"])` passes: wrong adjective declension ending
- `grade("geht", ["gehst"])` passes: wrong person ending

These are exactly the "mutate the target morpheme, must fail" cases the plan's property test specifies. The `größer` / `grosser` case is handled by a literal hardcoded special case. The test claiming to cover this is a 6-row list drawn from the same hardcoded set the implementation uses: it tests the lookup table against itself. This defect is downstream of stage 2, where the absence of spaCy means there is no morphological analysis to scope against.

### Stage 7 usability gate: not attempted, and not attemptable

`run_cli` rebuilds `TopicStateManager` from taxonomy on every invocation and passes `fsrs_records={}`. **Nothing is written back. There is no user-state DB and no `review_log` table.** Every run starts from zero, and `grammar round` auto-answers every item correctly (`src/cli/app.py:122`).

"Use the CLI daily for two weeks before starting stage 8" is not physically possible with this CLI. `docs/audits/stage-07-usage.md` does not exist. Stages 8 and 9 were built anyway.

`grammar stats` also diverges: it prints item-bank statistics, not topic states, stability or retention. `grammar report` prints "Flagged for review" and writes nothing.

The absence of a `review_log` makes five requirements impossible at once: invariant 1, mode-filtered `tag_state` recomputation, override append-only semantics, lapse records for split detection, and the two-week gate.

---

## 9. Stages 8 to 11: application

### Stage 8: the PWA does not run

1. **`web/app.js:312` calls `window.offlineStorage.getAll('topic_states')`.** `OfflineStorage` exposes `getTopicStates()` and a private `_getAll()`; there is no public `getAll`. The line sits outside the surrounding `try`, so the `TypeError` rejects `initStorageAndItems()` and `startNewRound()` never runs. **No round is ever startable.**
2. **The service worker is never registered.** No `navigator.serviceWorker.register` anywhere. `sw.js` is dead code, so nothing is cached, the app is not installable-offline, and the Lighthouse PWA check cannot pass.
3. **Nothing persists.** `saveTopicState`, `saveFSRSCard` and `appendReviewLog` are never called from anywhere. IndexedDB is read-only in practice.

**ts-fsrs is absent.** No `package.json`, no `node_modules`, no vendored library, no CDN tag, no `.ts` file. **No scheduling code exists client-side**: `startNewRound()` is `bankItems.slice(0, config.roundSize)`. The parity fixture exists but is read only by the Python side, and even there the test asserts `state` and `stability > 0` rather than the **next interval**, which the plan names as the fixture's key quantity. The plan calls cross-language parity "the single most important test in this stage"; it is absent on both sides.

**The two graders have already diverged.** The JS `GRAMMATICAL_MORPHEMES` is a strict subset of the Python set (missing `unserem` and `unseren` and friends, `als`, `denn`, `da`, `während`, `wegen`, `trotz`, `statt`, `anstatt`); the Python `CRITICAL_MINIMAL_PAIRS` set has no JS counterpart; the JS adds a one-off `"grosser"` / `"größer"` hack absent from Python.

**The 11 retention surfaces:**

| # | Surface | Status |
|---|---|---|
| 1 | Diff highlighting on the differing morpheme | **DIVERGED.** Renders whole-answer `given to expected`, not `d[en -> em]` |
| 2 | "Correct, but..." | **PARTIAL.** Typo and transliteration paths emit a message, but capitalisation error is graded as wrong, where the plan requires passing the item and noting what was off |
| 3 | Round preview from median response time | **MISSING.** Static literal; no response-time measurement exists |
| 4 | Grammar coverage bar | **PARTIAL.** Two segments not three, no `unseen`; percentages hardcoded for A1 and A2, B1 and B2 never written; label says "Stufen", which the plan forbids |
| 5 | Pace estimate, 30-day window | **MISSING** |
| 6 | Streak with 2 freezes per month and repair | **MISSING.** `let streak = 3;` hardcoded |
| 7 | Named progress report | **MISSING.** Static text; `#weekly-report-text` never written |
| 8 | Interactive DAG topic map | **MISSING.** Flat grid of 6 hardcoded topics, no edges, not read from taxonomy |
| 9 | Duel library, 7 columns | **MISSING.** Three hardcoded cards, none of the 7 columns, button has no handler |
| 10 | Daily challenge | **PARTIAL.** Grading is `val.toLowerCase().includes('weil')` with hardcoded "100% / 95%" output |
| 11 | Data export and import | **DIVERGED.** Omits `review_log` and settings; import assigns `tag_state` directly from the file, which `04-application.md:185` explicitly forbids |

Other divergences: umlaut buttons append at end rather than at cursor; the challenge textarea lacks the required `autocorrect`, `autocapitalize` and `spellcheck` attributes; the progress bar shows within-round position rather than due-queue clearance, with no bonus segment; no 7-day forecast; the load-threshold control is a bare slider missing the window selector and suggested-value triple; `manifest.json` references icon files that do not exist.

`tests/test_web.py` is 37 lines and 3 tests, two of which are file-existence checks. **There is no JS or TS test runner anywhere in the repo**, so none of the roughly 50 TypeScript tests in `04-application.md` exists.

### Stage 9: three of five endpoints are stubs, and there is no auth

| Plan endpoint | Code | Status |
|---|---|---|
| `POST /sync` | `worker/src/index.js:51-116` | **DIVERGED.** Writes `sync_events` and last-write-wins upserts `user_fsrs_cards`. `topic_states` is accepted, counted, and silently discarded. No `review_log`, no union, no `tag_state` recomputation |
| `GET /bank/delta?since=<item_id>` | `:41-48` | **STUB.** Always `delta_items: []`, never touches `env.DB`, treats `since` as a timestamp not an item id |
| `POST /override` | `:154-160` | **STUB.** Fixed `{status:'received'}`; no body parsed, no DB write, no verifier |
| `POST /explain` | `:163-168` | **STUB.** Constant string for every request; no cache |
| `POST /grade` | `:171-179` | **STUB.** Constant all-pass verdict |

**No auth exists.** `userId` falls back to the literal `'anonymous'`, so any caller can read or overwrite any user's rows by supplying `X-User-ID`. Method guards are missing: `/override`, `/explain`, `/grade` and `/bank/delta` respond identically to GET, POST and PUT.

`worker/schema.sql` is missing `review_log`, any bank or items table (so `/bank/delta` cannot ever return anything), the override queue, the explanation cache and the cost table. `sync_events` has **no uniqueness constraint on (user_id, item_id, timestamp)**, so the plan's idempotent-duplicate-rows requirement fails by construction. `user_topic_states` is dead; nothing writes to it.

**`src/sync/client.py` does the one thing the plan forbids.** `04-application.md:363`: *"Never merge `tag_state` directly. It is derived data and merging derived data is how progress corrupts."* `merge_inbound_topics:91-126` is timestamp-based last-write-wins on `TagStateModel`, with no recomputation function in the module, and `tests/test_worker_sync.py:64` asserts that behaviour as correct.

The override verification loop does not exist end to end. `OVERRIDE_UPHELD_RATE_ALERT` appears exactly once in the repo, its declaration.

### Stage 10: correct crons wrapping a mock

The workflow shells are right: submit at `0 2 * * *`, ingest at `0 8 * * *`, both `uv sync`, no `continue-on-error`, secrets wired. What they invoke is not.

- **Submit** builds one hardcoded `GenerationRequest(topic_id="dativ_nach_praeposition", count=10, difficulty=1)`. No D1 read, no `tag_state`, no per-tag deficit, no spec-driven requests, and the batch ID is printed to stdout and lost with the runner.
- **Ingest** defaults to `target_batch = "batch_001"`, which is not in a fresh mock's `submitted_batches`, so `poll()` raises `KeyError`. **`generate-ingest.yml` exits non-zero every night**, where the plan requires the pending case to exit 0.
- Deficit calculation does not exist; neither `SAFETY_FACTOR` nor `MIN_BATCH_THRESHOLD` is defined. No spend ceiling, no nightly item cap, no cost-log persistence, no degradation path.
- `nightly_batch.yml` **duplicates the `0 2 * * *` cron** of `generate-submit.yml` and runs the bank-health audit against `data/bank.db`, a file not in the repo. `src/audit/bank_health.py` is a genuine implementation, but it is not part of the two-phase job the plan describes.

All three stage 10 tests in `tests/test_application_pipeline.py` are tautological: they recompute the assertion inline rather than calling production code.

### Stage 11: zero API calls in the repo

`src/llm/provider.py` is 40 lines: a `LlmProvider` Protocol and a `MockLlmClient` returning canned German strings by substring-matching the prompt. All four feature modules default to it. There is no Gemini client, no `genai` import, no HTTP call and no API-key read anywhere in `src/`. None of `MODEL_LIVE`, `MODEL_GENERATE` or `MODEL_VERIFY` is imported by `src/llm/`.

- **Production grading:** correct three-dimension shape, but `is_pass = target_used and accuracy >= 0.75` gates the verdict on grammatical accuracy, contradicting the plan ("a response using Konjunktiv II correctly but with wrong word order must rate the konjunktiv_ii tag as a pass"). The `except` fallback returns `is_pass=True, accuracy=1.0, naturalness=1.0` on any parse failure, silently passing everything when the model misbehaves. Nothing consumes `target_structure_used`.
- **Explanations:** the prompt includes the user's answer, but there is no cache keyed on `(item_id, user_answer)`, no hit-rate logging, no gating on explicit user action. The plan makes the cache key the central correctness property.
- **Minimal pairs:** a static dict of three preseeded drills. `get_or_generate_drill` never generates, and for any unknown group silently returns the `wechselpraepositionen` drill, so `get_or_generate_drill("kasus_genitiv")` yields a drill labelled with the wrong confusion group.
- **Weekly report:** takes pre-computed metrics as arguments. No window logic, no last-report timestamp, no trigger, no manual gate, no rate limit. Both trigger constants have zero readers repo-wide.

---

## 10. README accuracy

`README.md` presents stages 8 to 11 as delivered. Specifically: "Client-side `ts-fsrs` and IndexedDB persistence" (neither is real), "11 UX surfaces" (five present in some form, six absent), "browser-consumable delta export" (both ends are stubs), "Cloudflare Worker ... asynchronous answer override verification" (a stub returning a constant). Update it or the next reader will trust it.

Two plan-side inconsistencies worth fixing:

1. `04-application.md:110` says "Eight features" and then enumerates eleven; the DoD at `:332` says "All eleven retention surfaces". The eleven-item list is the operative one.
2. `CLAUDE.md:11` and `:39` point at `docs/plan/german-grammar-app-plan.md`; the file is at repo root.

---

## 11. Work order

Ordered by what unblocks the most downstream work, not by stage number.

1. **Build `src/llm/client.py` with `cost_log`, `BudgetExceeded` and the two-lane split, and write the SDK-import scan test first.** This is invariant 4, it is stage 0's principal deliverable, and every stage 3, 4 and 11 feature is blocked behind it. Writing the scan test now, while there is nothing to violate it, is what `01-foundation.md:91` asks for and is a ten-line test.
2. **Fix `src/contracts.py:66-67`:** set `MODEL_LIVE` and `MODEL_GENERATE` to `"gemini-3.5-flash-lite"`. `MODEL_VERIFY` stays `"gemini-3.7-flash"`. Then remove the inline model strings at `src/generation/batch_client.py:34-35,87` so the constants are the only source. Extend `tests/test_contracts.py` to assert model IDs and `Literal` memberships so the next drift is caught.
3. **Derive `facet` from `morph_spec`, and correct the `morph_spec` keys in the same change.** One missing derivation deadlocks promotion for all 68 faceted topics and disables split detection. Eight of fifteen keys are invalid UD features and would silently disable the stage 4 morphology check anyway, so both belong in one pass.
4. **Add a `review_log` table and CLI persistence.** Without it, invariant 1 plus four plan requirements are structurally impossible: mode-filtered `tag_state` recomputation, override semantics, lapse records, and the two-week usability gate.
5. **Make the CLI interactive and stop it auto-answering.** `src/cli/app.py:90` and `:122` are two lines. The usability gate is the cheaper of the two gates to satisfy and is designed to catch scheduling problems before any frontend work.
6. **Fix the kill-gate semantics and run the real audit.** Redefine `error_rate` as post-verifier error on accepted items, then sample 100 accepted items with a second auditor from a different vendor per `CLAUDE.md` section 10.
7. **Wire spaCy `de_core_news_lg`.** Already a declared dependency, and it unblocks three things at once: real lemmatisation for stage 2 vocabulary banding, real morphology for stage 4 layer 2, and `morph_spec`-scoped typo tolerance for stage 7. All three are currently hand-rolled around its absence.
8. **Scope typo tolerance to `morph_spec`** once spaCy is available. The current behaviour passes wrong declension and conjugation endings as typos, which corrupts every FSRS rating the app records.
9. **Fix the three PWA blockers** (`getAll`, SW registration, the never-called persistence writes) before adding any of the six missing UX surfaces.
10. **Add auth to the Worker** before it holds real user data.

### Four small changes that would have caught much of this

- **Lint and type-check `scripts/`.** `ci.yml` scopes ruff to `src tests` and mypy to `src/`. That is why `scripts/step1_extract_vocab.py` calls a method that does not exist and still ships green.
- **Enforce the marker taxonomy:** `-m "not live"` on push, a `simulation` job on PRs, a `live` job nightly. The markers are declared and never used.
- **Make `tests/test_taxonomy.py:92-95` fail when the golden fixture is missing** rather than writing it.
- **Promote the confusion-group check in `src/taxonomy/validator.py:86-88` back from warning to error**, or record the twelve singletons as accepted exceptions with reasons. Silently downgrading a failing check is invariant 7.
