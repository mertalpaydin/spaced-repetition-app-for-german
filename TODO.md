# TODO

Open work only. **An item is a thing that is not done.** When you close one,
delete it from here and record it in `docs/project-state.md`.

The work is strictly phased (owner's instruction, 2026-09-08). A phase starts
only when the previous one is shown to work, and the owner confirms each step.

---

## Phase 0: prune (done 2026-09-08 on `feat/phrase-deck`)

Nothing open. Left deliberately for later phases:

- `data/taxonomy.yaml` stays until phase 1c has bootstrapped
  `data/phrases/connectors.yaml` and `collocation_seed.yaml` from it, then it
  is deleted.
- `web/` is untouched until phase 3; it still fetches the deleted
  `web/data/all_items.json` and falls back to its six seed items.
- The Windows scheduled task "LLA monthly translation" is still enabled and
  still runs `scripts/monthly_translation_topup.py`, which now reads
  `--carriers-file data/phrases/build/wanted_carriers.txt` for pass 0 (the
  file does not exist until phase 1d has run; the job then behaves as before).
- The IDE's language server held `.venv` during `uv sync`, so the removed
  dependencies (`en-core-web-sm`, `pypdf`, `rich`, `typer`) are still
  installed locally. Harmless; a later `uv sync` with the IDE closed cleans it.

## Phase 1: exercise generation

Kill criterion: after 1c, `data/phrases/build/report.json` shows sensible
top-200 units per kind and most top-500 units have at least 6 valid cards.

- 1a. `src/contracts.py`: `PhraseKind`, `GapSpan`, `PhraseUnit`, `PhraseCard`,
  context and deck models, with the slicing validators.
- 1b. `src/phrases/parse.py`: one parser load, `parse_many`, `lexical_verb`,
  `verb_lemma_key`, `is_sentence_initial`; `--stage parse` writes
  `occurrences.jsonl` and `lemma_counts.json`.
- 1c. `src/phrases/mining/`: verb_prep, reflexive, separable, collocations
  (G² with abstention), connectors (curated), idioms (curated);
  `src/phrases/units.py` thresholds, rank, trivial flag, CEFR; report.
- 1d. `src/phrases/cards.py` (Azure/Gemini glosses only, form diversity,
  `wanted_carriers.txt`); `src/phrases/contexts.py` (opt-in, free lane,
  `--approved-by-owner`); fix the top-up to check the ledger refusal flag
  before the 35-minute validity filter.
- 1e. `src/phrases/export.py`: manifest, `units.json`, rank-band shards, JSON
  schema fixture, byte-determinism; `scripts/build_phrase_deck.py` stages.
- Tests as listed in the plan; golden end-to-end on a 300-line sample corpus.

## Phase 2: FSRS and the laptop client

- `src/engine/review_log.py` (JSONL log, replay), `session.py`, `grading.py`
  (`grade_gaps`, rating map), `src/cli/train.py` (triage, practice, stats).

## Phase 3: the PWA

- `web/` rewrite as ES modules with vendored ts-fsrs, IndexedDB v2,
  deck-versioned service worker, triage/practice/stats/settings views,
  export/import v3, `tests/js` under `node:test`.

## Any time

- **Reconcile the cost log monthly** with `scripts/reconcile_cost_log.py`.
- **Run the gloss purge on the real store** (`scripts/purge_mismatched_glosses.py
  --dry-run` first): 7,365 Leipzig records labelled `source="tatoeba"`. They are
  excluded from cards by the trust rule already, so this is hygiene, not a blocker.

---

## Do not change these without asking the owner

Pinned by tests. A failing test here means ask, not fix.

- `src/llm/client.py`: `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5`, `spend_ceiling_usd = 7.50`, the 5xx
  retry shapes for both lanes, and the free-to-paid fallback.
- Tatoeba's own English is never shown to a learner
  (`build_translations.DEFAULT_TRUST_TATOEBA = False`). Do not delete the
  Tatoeba records from the store; they are overwritten by machine glosses
  month by month.
- Where a German sentence has several English translations, keep the shortest.
- Every card shows its sentence's English translation, always.
