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

## Phase 1: exercise generation (built; stepped review in progress)

The pipeline runs end to end over six corpora (`docs/phrase-deck.md`). The
deck built from the wide corpus is reviewed step by step, top of the ranking
first, by Claude Opus and Gemini Flash (`docs/audits/phase-1-review/`,
"Step 1"): read a band, turn the systematic causes into rules, rebuild, read
the next band of what survives. Open:

- **Step 3 onward.** Steps 1 and 2 covered every glossed card and every
  unit once; the next step reads only what the rebuild replaced.
  After each rebuild: `uv run python scripts/review_deck.py batches <dir>`
  (skips reviewed ids), Claude agents in waves of at most 8, Gemini via
  `review_deck.py gemini <dir> --model gemini-3.8-flash-low --workers 4`
  with 200-card batches, merge with the `reviewer` tag, apply, rebuild.
  Stop when a step finds no new systematic cause and few findings.
- **The owner's kill decision** on the reviewed deck.
- **Gloss the picked sentences.** `data/phrases/build/wanted_carriers.txt`
  holds every card sentence without a machine gloss (about 2.9M characters
  on the wide corpus). The monthly job reads it as pass 0; October's
  allowance is the first that can be spent on it.
- **Contexts via `gemini-executor`.** No context sentence has been generated
  yet; sentence-initial connector cards show a single sentence. Generate the
  preceding sentence with the executor skill (no key, no spend); the
  deterministic acceptance checks in `src/phrases/contexts.py` still apply.
- **Unit-level glosses** for mined units: none yet; a later opt-in batch.
- **Adjective-noun citation forms without a nominative or a governing
  preposition** still show the commonest oblique form; a gender lookup
  would fix the rest.
- **Gaps for prepositional displays.** `auf freiem Fuß`, `ein Auge werfen
  auf` blank only the unit's own tokens; both reviewers flag the
  unbracketed preposition. Decide whether the miner should record the
  preposition token so it can be gapped too.

## Before phase 2 (owner's instruction, 2026-09-09)

- **Connectors in the ranking.** They are ranked already (matched inside
  every sentence, initial or medial). What the corpus cannot supply is the
  preceding sentence, which is the contexts item above. Re-check the ranks
  once the stepped review is done.
- Check the top pairs against DWDS Wortprofil where its terms allow.

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
