# TODO

Open work only. **An item is a thing that is not done.** When you close one,
delete it from here and record it in `docs/project-state.md`.

The work is strictly phased (owner's instruction, 2026-09-08). A phase starts
only when the previous one is shown to work, and the owner confirms each step.

---

## First thing next session

- **Gemini's last 16 gloss batches** (`gloss1/gemini/rest`, cards_174 and
  180 to 194): rerun `review_deck.py gemini <dir> --model
  gemini-3.8-flash-medium` when the quota is back, apply as gloss2c.
- **Gloss the 1,136 replacement sentences** (`agy_jobs.py glosses`, once
  the agent quota is back), rebuild, review what `batches` lists.

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

- **Rerun the review after each Azure gloss month.** Newly glossed cards
  get their gloss checked (`review_deck.py batches` lists only unread
  ids; a card read without a gloss is not read again, so add
  `--everything` for the glossed band, or extend the script to track
  "read with gloss").
- **The owner's kill decision** on the reviewed deck.
- **Gloss the picked sentences.** `data/phrases/build/wanted_carriers.txt`
  holds every card sentence without a machine gloss (about 2.9M characters
  on the wide corpus). The monthly job reads it as pass 0; October's
  allowance is the first that can be spent on it.
- **Contexts for the remaining sentence-initial connector cards.** 21 of 30
  have one; the other nine wait for a gloss, then
  `uv run python scripts/agy_jobs.py contexts` again.
- **Unit-level glosses** for mined units: none yet; a later opt-in batch.
- **Adjective-noun citation forms without a nominative or a governing
  preposition** still show the commonest oblique form; a gender lookup
  would fix the rest.
- **Noun-verb displays with a complement** (`ein Auge werfen auf`) still
  blank only noun and verb; the adjective-noun case is solved, this one
  would need the same treatment in the collocation miner.

## Before phase 2 (owner's instruction, 2026-09-09)

- **Connectors in the ranking.** They are ranked already (matched inside
  every sentence, initial or medial). What the corpus cannot supply is the
  preceding sentence, which is the contexts item above. Re-check the ranks
  once the stepped review is done.
- Check the top pairs against DWDS Wortprofil where its terms allow.

## Phase 2: FSRS and the laptop client (built 2026-09-09)

- **Use it for a week** before phase 3: triage the first bands, practise
  daily, watch `stats`. Report what feels wrong in the scheduler or grader.
- Reviewed card sentences with a Gemini-agent gloss have not had the gloss
  itself reviewed; the next review step (cards new to the reviewers) covers
  them.

## Phase 3: the web client

Phase 3a (done 2026-09-09): `uv run python -m src.cli.serve`, the page in
`web/` on the laptop and on the phone over the LAN, engine and log in Python.

- **Use 3a for a week on both devices** and note what the page needs
  (keyboard on the phone, font size, what to show after an answer).
- **Phase 3b, offline PWA on GitHub Pages:** vendor ts-fsrs, port
  `grading.py` and `session.py` to `web/lib/` with a replay test against the
  Python engine on a fixture log, IndexedDB log, deck-versioned service
  worker, export/import of the log for `train merge`. Only after 3a has
  been used.

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
