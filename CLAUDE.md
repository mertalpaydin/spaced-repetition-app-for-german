# CLAUDE.md

Guidance for any agent or human working in this repository. Read this before writing code.

**New here? Read `docs/project-state.md` first.** It says what is built, what is not, and what will bite you. Then come back to this file for the rules.

---

## 1. What this project is

A personal, German-only **phrase** spaced-repetition trainer, in the spirit of Lingvist. The learner sees a real corpus sentence with the tokens of one phrase blanked, plus the sentence's English translation, and types the missing tokens. FSRS schedules phrase units, introduced in corpus-frequency order.

A phrase unit is one of: a verb with its preposition, a reflexive verb, a separable verb, a noun-verb or adjective-noun collocation, a connector (including two-part ones such as `zwar … aber`), or a fixed expression. Units may be discontinuous in the sentence (`warte … auf`) and are taught across surface forms, never as one fixed string.

This repository was a German grammar trainer until 2026-09-08. That project was shut down after its bank audit and the code was pruned on the branch `feat/phrase-deck`; the grammar trainer survives on `main` and `feat/generate-then-blank` and in `docs/audits/`. The pivot plan and its reasoning are recorded in `docs/project-state.md`. Do not re-derive it.

The work is **strictly phased**, at the owner's instruction: phase 1 (exercise generation for every phrase kind) must be shown to work before phase 2 (FSRS with a terminal client) starts, and phase 3 (the PWA) is last and least critical.

---

## 2. Non-negotiable rules

These are invariants. Violating any of them is a defect regardless of whether tests pass.

1. **An LLM is never the source of truth for user progress.** All progress numbers are computed in code from the review log. An LLM may narrate them. It may not produce them.
2. **A card never reveals the phrase before the answer.** The citation form (`warten auf + Akk`) and any unit-level gloss are shown only after grading. Before it, the learner has the sentence with gaps and the sentence's English translation, nothing else.
3. **No LLM call sits on the critical path of answering a card.** Deck build is offline. The one LLM use, generating a preceding context sentence for sentence-initial connectors, is an opt-in build stage that runs only after the owner approves that specific run.
4. **Every LLM call goes through `src/llm/client.py`.** Direct SDK calls anywhere else are forbidden. The wrapper handles retries, token accounting, cost logging, and the spend ceiling. A call that bypasses it is invisible to the budget.
5. **One card, one `unit_id`.** A card tests exactly one phrase unit. Sentences that host several units get one card per unit, with non-overlapping gaps.
6. **`answers` is always a list**, one entry per gap, in gap order, and each gap's text slices back out of `sentence_de` exactly. Never a single string, never `None`.
7. **Do not weaken a failing test to make it pass.** If a test is wrong, say so and explain why before changing it.
8. **Do not change a persisted format silently.** The review log, the deck JSON, the translation store and `cost_log` are all read by more than one program. Additive changes with defaults are fine; anything else is flagged in the commit body and in `docs/project-state.md`.
9. **Only Azure or Gemini glosses reach a learner.** Tatoeba's own English is never shown on a card (owner's decision, kept from the grammar trainer). Tatoeba sentences still count for frequency.

---

## 3. Repository layout

The repo root is the working directory.

```
.
├── CLAUDE.md
├── README.md                       # setup, and where to read next
├── TODO.md                         # open work only, by phase
├── pyproject.toml
├── config.yaml
├── .env.example                    # every required var, no values
├── docs/
│   ├── project-state.md            # what is built, what is not, what bites
│   ├── phrase-deck.md              # runbook: building the deck
│   ├── monthly-translation-job.md  # runbook: the Azure top-up on Windows
│   ├── known-defects.md            # STALE: grammar-trainer defect classes
│   ├── building-the-bank.md        # STALE: grammar-trainer bank build
│   ├── plan/, 00-04-*.md           # STALE: grammar-trainer design record
│   └── audits/                     # dated record, never instructions
├── data/
│   ├── phrases/                    # curated lists (connectors, idioms, seeds), contexts.jsonl
│   │   └── build/                  # gitignored intermediates of the deck build
│   ├── fixtures/                   # golden sets; translations/ is an operational store
│   └── raw/                        # staged corpora, gitignored
├── src/
│   ├── contracts.py                # every shared type and constant, one file
│   ├── phrases/                    # parse, mining, units, cards, contexts, export, carrier validation
│   ├── lexicon/                    # frequency bands, CEFR list, lemmatiser
│   ├── llm/                        # client wrapper, cache, cost log, translators, env
│   └── engine/                     # FSRS wrapper, typo grader (phase 2: review log, session)
├── scripts/                        # operator entry points
├── web/                            # the PWA; web/data/deck/ is the exported deck (committed)
└── tests/                          # flat, one file per area
```

`scripts/` is how this project is operated: `build_phrase_deck.py` (phase 1), `monthly_translation_topup.py` and `build_translations.py` (the gloss store), `reconcile_cost_log.py` and `repair_cost_log.py` (billing), `purge_mismatched_glosses.py`.

---

## 4. Branching

`main` still holds the grammar trainer. The phrase trainer is built on `feat/phrase-deck`; it becomes `main` when phase 2 is usable. Until then, branch from `feat/phrase-deck`.

**Branch naming:** `<type>/<short-slug>` with types `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `spike` (throwaway, never merged).

- Branch from the current integration branch, rebase before opening the PR.
- Delete the branch after merge.

---

## 5. Commits

Conventional Commits, imperative mood, scope is the module or phase.

```
<type>(<scope>): <subject>

<body: why, not what>
```

- **The body explains why.** The diff already shows what.
- **No `wip`, `fixup`, `asdf`, or `.` on a pushed branch.**
- **One logical change per commit.**
- **Never commit secrets, `.env`, API keys, or raw API responses containing them.**
- The exported deck (`web/data/deck/`) and `data/phrases/contexts.jsonl` **are** committed: GitHub Pages serves the deck verbatim and CI cannot rebuild it without the gitignored corpus and translation store.
- A commit that changes behaviour changes tests in the same commit.

---

## 6. Pull requests

A PR may only be opened when `uv run pytest -q` is green, `ruff check` and `ruff format --check` are clean, `mypy --strict src/ scripts/` is clean, and coverage on `src/` is at or above 85%. No new dependency without a one-line justification in the PR body.

---

## 7. Testing rules

- `pytest`. Tests live in `tests/`, **flat**: one file per area, `test_<area>.py`.
- **No test touches the network.** All LLM calls use `MockLlmClient`. A test that makes a real API call is a defect.
- **The whole suite is the default run.** `uv run pytest -q` runs everything; nothing needs a key. Tests that need the spaCy model skip cleanly without it, and say so.
- One marker, `golden`, for tests that compare against a versioned fixture in `data/fixtures/`. Changing a golden file requires a commit that explains why the expected output changed.
- `data/fixtures/translations/` is not a fixture. It is gitignored and operational; see `docs/project-state.md`.
- Every bug fix starts with a failing test that reproduces the bug.
- **Test naming:** `test_<unit>_<condition>_<expected>`.

---

## 8. Code standards

- Python 3.12. `ruff` for lint and format. `mypy --strict` on `src/` and `scripts/`.
- **Pydantic models for every persisted record and every LLM input and output.** No raw dicts crossing a module boundary.
- No bare `except`. Catch what you can handle.
- Functions that touch the network, the filesystem, or the clock take those as injected dependencies so they can be faked in tests.
- Anything that runs over the corpus is deterministic: seeded, sorted, byte-reproducible output.
- TypeScript or plain ES modules for `web/`; no build step, no npm dependency.

---

## 9. Cost discipline

The recurring LLM budget is **7.50 USD/month**, enforced in code (`GeminiLlmClient.spend_ceiling_usd`, pinned by a test). The phrase trainer's steady state spends nothing: the deck build is deterministic, glosses come from Azure Translator's free F0 tier, and the only Gemini use is the opt-in context-generation stage on the free lane.

The client is unchanged from the grammar trainer and its rules still bind:

- Every **attempt** through `src/llm/client.py` writes a `cost_log` row (timestamp, purpose, lane, mode, model, token counts, cost, outcome, attempt, call id, quota type). Failed attempts are logged with zero tokens. `scripts/reconcile_cost_log.py` compares the log against Google's billing export; the log cannot audit itself.
- Two lanes, two Google Cloud projects: `free` (unbilled, synchronous, rate-limited) and `paid` (billed). Enabling billing on a project destroys its free tier, so two projects are mandatory. **Never rotate keys to multiply free quota.**
- **Set `GEMINI_FREE_API_KEY` explicitly.** The client falls back to `GEMINI_API_KEY`, which on the owner's machine is the billed key. `client_from_env(free_lane_only=True)` in `src/llm/env.py` refuses to start without the free key set.
- Distinguish the two 429s: RPM means back off, RPD means the free lane is closed until the Pacific-midnight reset.
- A local content-addressed cache (`src/llm/cache.py`) is checked before every call; hits write a `lane="cache"` row with zero cost. The cache is also the idempotency mechanism for reruns. Do not use provider-side context caching.
- Model routing: `MODEL_GENERATE` (`gemini-3.5-flash-lite`, thinking `low`) for context generation; `MODEL_VERIFY` (`gemini-3.7-flash`, thinking `medium`) is kept in `contracts.py` for the client's thinking gate and is not called by any phrase-trainer workload. Model IDs live only in `src/contracts.py`.

**No API run without the owner approving that run.** A stage that would call Gemini or Azure prints what it is about to spend and refuses without `--approved-by-owner`; the agent asks in chat before passing that flag.

---

## 10. Secrets

- `.env` is gitignored. `.env.example` lists every variable with no values.
- If a secret is ever committed, rotate it. Do not merely delete the commit.

---

## 11. Working style for agents

- **Read `docs/project-state.md`, then `docs/phrase-deck.md` for the build, before touching code.**
- **Do not skip a phase.** Phase 2 assumes the deck format of phase 1; phase 3 assumes the review-log format of phase 2.
- **Stop and ask when a contract is ambiguous.** Guessing an interface and building on it is the expensive failure mode here.
- **Keep `docs/project-state.md` and `TODO.md` current.** Finished work leaves `TODO.md`; superseded documents get a dated STALE header, never a silent edit.
- **Ask before starting the next task.** The owner confirms each step.
- Prose in `docs/` and in commit messages uses no em dashes.
