# Project state, 11 September 2026

Written for the next person to work on this repository. It says what the
project is, what is built, what is not, and what will trip you up.

Read `README.md` first for setup. Read `CLAUDE.md` for the rules. Open work is
in `TODO.md`, by phase.

---

## What this is

A personal, German-only phrase spaced-repetition trainer. The learner reads a
real German sentence with the tokens of one phrase blanked, sees the sentence's
English translation, and types the missing tokens. FSRS schedules phrase units;
units are introduced most-frequent first, trivial function words are skipped,
and a one-time triage lets the learner mark phrases already known.

A phrase unit is a verb with its preposition (`warten auf`), a reflexive verb
(`sich interessieren für`), a separable verb (`aufstehen`), a noun-verb or
adjective-noun collocation (`eine Entscheidung treffen`), a connector including
two-part ones (`trotzdem`, `zwar … aber`), or a fixed expression
(`auf jeden Fall`). A unit may be discontinuous in the sentence and is taught
across its surface forms, never as one fixed string. Sentence-initial connectors
get a generated preceding sentence so the connector has something to connect.

## Where it came from

Until 2026-09-08 this repository was an interleaved German grammar trainer.
It was shut down that day after its first bank audit found 3.7% defective
items, 76% of them wrong topic attribution that the design could not catch
(`docs/audits/cycle-31-bank-audit.md`). The owner decided to salvage the
corpus, the translation store, the sentence validator and the frequency data
into a phrase trainer instead.

The grammar trainer's code survives on `main` and `feat/generate-then-blank`.
Its design record (`docs/plan/`, `docs/00-` to `04-*.md`,
`docs/building-the-bank.md`, `docs/known-defects.md`) carries a dated STALE
header and is kept for the reasoning, not as instructions. `docs/audits/` is
untouched.

## The phases

Owner's instruction: strictly ordered, each confirmed before the next starts.

1. **Exercise generation.** Mine every phrase kind from the corpus, pick
   glossed sentences, export a deck. If the mined units are junk, stop here.
2. **FSRS and a terminal client.** Usable on the laptop without any web part.
3. **The PWA.** GitHub Pages, offline, phone. Least critical.

---

## What is built and working

As of 2026-09-08, phase 0 (the prune) is done on branch `feat/phrase-deck`.

- **The corpus reader.** `scripts/corpus_reading.py` reads both corpora: about
  400,000 Tatoeba and 205,000 Leipzig lines under `data/raw/_extract/`.
- **The sentence validator.** `src/phrases/carrier_validation.py`,
  21 deterministic rules over a spaCy parse, measured on the grammar trainer.
  It is the only module that loads the German parser.
- **Verb tables and government.** `src/phrases/paradigms.py` (reflexive hand
  lists, modal and auxiliary lemmas, prefix tables) and
  `src/phrases/verb_government.py` over the frozen fixture
  `data/fixtures/verb_government/lexicon.v1.jsonl` (2,720 verbs, corpus
  counts of reflexive and object case).
- **Frequency and CEFR.** `src/lexicon/` with the OpenSubtitles 50k list and
  a 16,825-lemma CEFR list.
- **The gloss store and its top-up.** `data/fixtures/translations/de_en.jsonl`
  holds 258,806 records: 199,837 Tatoeba (never shown), 58,709 Azure, 260
  Gemini. `scripts/monthly_translation_topup.py` spends Azure's free 2,000,000
  characters a month and now takes `--carriers-file` so the deck can steer it.
- **The LLM client** with cost log, cache and the two-lane policy, unchanged.
  `client_from_env` moved to `src/llm/env.py`.
- **The FSRS wrapper** (`src/engine/fsrs.py`, over the `fsrs` package) and the
  typo grader, kept for phase 2.
- **675 tests pass.** `ruff`, `ruff format --check` and
  `mypy --strict src/ scripts/` are clean. Coverage on `src/` is 87%.

- **The deck build, phase 1.** `scripts/build_phrase_deck.py` parses both
  corpora once (about 10 minutes), mines every phrase kind, ranks units by
  distinct sentence count, picks glossed cards covering distinct surface
  forms, and exports `data/deck/` with a content-hashed `deck_version`.
  `docs/phrase-deck.md` is the runbook; `data/phrases/build/report.json` is
  what to read after a build. The one model stage (context sentences for
  sentence-initial connectors) is opt-in and has not been run.

- **Eight review rounds on the first deck, 2026-09-08/09.** Every unit and
  every card was read by Claude Opus agents under the owner's zero-defect
  policy (`docs/audits/phase-1-review/`). All systematic causes are rules
  with tests; the rest are curated-list entries with reasons.
- **The wide corpus, 2026-09-09.** Six sources (Tatoeba, three Leipzig 1M
  packages, the full 2025 news file, an OpenSubtitles sample), ranked by the
  mean per-source sentences per million so no register dominates. Parsing
  takes about six hours; `--stage mine` onward reruns from the stored parse.
- **The stepped review, from 2026-09-09.** The wide-corpus deck is read from
  the top of the ranking down, by Claude Opus and Gemini Flash (the
  `gemini-executor` skill), with a rebuild after each step so the rules of
  one step remove the same defects from the unread part. Five steps on 9
  September 2026 read every unit and every glossed card, both vendors,
  and converged: the last rebuild produced nothing unread. 26 causes
  became rules; the audit README has each step's cross-vendor diff.

- **Phase 2, the laptop client, 2026-09-09.** `src/engine/review_log.py`
  (append-only JSONL at `data/review_log.jsonl`, gitignored; `derive_state`
  replays it through FSRS deterministically), `grading.py` (per-gap grading
  with the scoped typo grader; exact or transliteration is good, a scoped
  typo is hard, anything else is again; a sentence-initial gap accepts the
  lower-case form), `session.py` (due units by retrievability, then new
  units in rank order up to `new_per_day`, then learn-ahead; only cards with
  a machine gloss are shown, and a sentence-initial connector card only with
  its context sentence), `stats.py`, and `src/cli/train.py` with triage,
  practice and stats modes. `uv run python -m src.cli.train practice`.
- **Deck jobs through the Gemini agent, 2026-09-09.** `scripts/agy_jobs.py`
  generates connector context sentences (21 of 22 accepted) and English
  glosses for the picked sentences through `agy`, checked deterministically
  before they are stored; glosses land in the translation store with
  `source: "gemini"`.
- **Complement prepositions are gaps.** A unit whose display carries a
  governing preposition its mined parts lack (`Wert legen auf +Akk`) gets
  that preposition as one more gap on every card that has exactly one
  candidate token for it.
- **Prepositional adjective-noun units.** An adjective-noun pair governed by
  one preposition in at least 80% of its sentences is a three-token unit
  with three gaps (`auf freiem Fuß`); 44 such units.

- **Everyday corpora weigh tenfold in the ranking, 2026-09-11.** With
  one vote per corpus the four news and web corpora put `jedoch` and
  `zudem` (B1, B2) at ranks 9 and 13; with Tatoeba and subtitles weighted
  ten they sit at 27 and 42 behind `anrufen`, `anfangen` and `zu Hause`.
  Unit ids are unchanged, so the review lists and the learner's log still
  apply; only the order of introduction moved.
- **First learner feedback applied, 2026-09-11.** The day's limit is an
  exercise budget (`cards_per_day`, default 40), not a unit cap; one
  FSRS learning step (10 minutes) instead of two; a unit is never shown
  twice in a row; "Tagesziel erreicht" offers to continue anyway. The
  page has dark mode, a today counter in the header, a units-by-stage
  view, a history view (the ◀ button), a stop button, one font for
  context, sentence and gloss, and the unit's glosses listed after an
  answer. Connector glosses list their common renderings ("zudem:
  moreover / in addition / also"). Changing the learning steps changes
  how an existing log replays; the log written before this date was 46
  reviews and was left as is.
- **Phase 3a, the browser page on a local server, 2026-09-09.**
  `uv run python -m src.cli.serve` serves `web/` and a JSON API over the
  phase 2 engine (standard library only); the page has practice (inline
  gap inputs, umlaut bar, reveal, "Kannte ich schon"), triage and stats
  views and works on the laptop and on the phone over the same Wi-Fi. The
  review log stays on the laptop. Kept as the development server.
- **Phase 3b, the page on GitHub Pages, 2026-09-11.** The product is now
  `web/` alone: no server, no command. `web/lib/engine.js` is a direct port
  of the py-fsrs 6 scheduler as `src/engine/fsrs.py` configures it (one
  learning step, no fuzz); ts-fsrs was tried first and dropped, because it
  measures elapsed time in calendar days and orders the hard/good/easy
  intervals, and both moved due dates away from the Python engine's.
  `grader.js`, `session.js` and `log.js` port the grader, the scheduler and
  the replay; node tests (`tests/js/`, run by `tests/test_web.py`) pin them
  against fixtures the Python side generates (`--regen-web`). The log is in
  IndexedDB and, with a fine-grained GitHub token (gist scope only, entered
  under ⚙), in one private gist that every device pulls, merges and pushes,
  so laptop and phone replay the same log; the merge is by (type, unit,
  time), which is why entries carry no device id. A service worker caches
  the shell (bump `APP_VERSION` in `web/sw.js` when the shell changes) and
  the deck shards under the deck version, so the page works offline after
  one visit. The deck moved to `web/data/deck/`; `deploy_pages.yml` also
  deploys `feat/phrase-deck`. Pages must be set to deploy from GitHub
  Actions once, in the repository settings.

- **Unit glosses for the mined units, 2026-09-12.** The learner saw
  `aussehen` after answering with no English. A second opt-in model stage
  (`--stage unit-glosses`, `src/phrases/unit_glosses.py`) asks
  `gemini-3.5-flash-lite` for one to three renderings per unit, most common
  first, one per sense, 40 units a call in rank order, free lane first with
  paid overflow, approved by the owner for up to 2 USD. Results are in
  `data/phrases/unit_glosses.jsonl` (committed); the export joins them with
  " / " into `gloss_en`, curated glosses winning.
- **Partial answers stay wrong (owner's decision, 2026-09-12).** On a
  multi-gap phrase the worst gap rates the card: one wrong gap is "again".
  The feedback colours each gap so the learner sees which part failed.
- **The translation backlog is closed, 2026-09-12.** `scripts/api_jobs.py`
  runs the sentence glosses and the card review through the project's
  client when the agent quota is spent (owner's allowance 2 USD, spent
  0.50). Every sentence the deck wanted is glossed, every new card read,
  Gemini's unread tail of the gloss review read; record in
  `docs/audits/phase-1-review/README.md`. The 89 unit overrides whose
  display carried "+Case" (shown as "+Dat +Dat") are split at load time.
- **Second feedback round, 2026-09-13.** Präteritum cards (16% of the
  deck) are held back until a unit is in review state, on both engines;
  the phrase's English is shown before the answer ("Gesucht: …", rule 2
  reworded); the day's budget now stops the session even when cards are
  due, except mid learning step; connectors that translate alike accept
  each other (`also_accepted` on the curated list, `deshalb`/`deswegen`/
  `daher`/`darum`, `trotzdem`/`dennoch`, `außerdem`/`zudem`,
  `jedoch`/`allerdings`), rated correct with the sentence's own word shown;
  and "Später" parks a unit (a mark with source `defer`, additive to the
  log format) until "Wieder lernen" under Einheiten brings it back. The
  ranking residue the owner noticed is real: units introduced before the
  reweighting of 2026-09-11 stay in the log; "Später" is the way out.
- **Two seed glosses quoted their own German, 2026-09-14.** "gehen um" was
  glossed "to be about (es geht um)" and "sich handeln um" likewise, hand
  written in `verb_prep_seed.yaml` during phase 1 as a note to the deck
  author. Harmless until the phrase gloss started being shown before the
  answer on 2026-09-13, from when it printed the answer on the card. Both
  fixed; `german_leak` in `src/phrases/unit_glosses.py` now rejects such a
  gloss on the model path and `check_deck` fails the deck on any path, so
  CI catches it whatever writes it. The other 7,245 model-written glosses
  and every other curated one were checked and are clean; the parentheses
  that remain are English hints ("to answer (a question)", "since (then)").
- **Restart, and a less rigid introduction order, 2026-09-19.** "Von vorne
  anfangen" under the settings writes a `ResetEntry` to the log; `derive_state`
  replays only what follows it, so every unit is new again and the counters
  start at zero, while the old lines stay in the file and the restart reaches
  the other device like any other entry. **This adds a third entry type to the
  review log** (rule 8): both engines parse it, and a reader that does not
  know it would silently ignore the restart, so the Python and JavaScript
  sides were changed together. `entries_since_reset` is what the statistics
  and the history panel count. A new unit is now drawn at random from the next
  `new_pool` by rank (50, settable) instead of always the next one, so two
  sessions do not march down the ranking in lockstep; the draw is a runtime
  choice and does not affect replay.
## What is not built

- **An installable icon flow on iOS** is untested; Android and desktop
  Chrome install from the page's manifest.
- **Nothing else of phase 2**; `train merge <other.jsonl>` folds the page's
  exported log into the terminal client's if that is ever wanted.

---

## What would bite you first

1. **`data/fixtures/translations/de_en.jsonl` is not a test fixture.** It is a
   48 MB operational store built a bit at a time against a free monthly
   allowance, gitignored, existing only on the owner's machine. Never delete
   it, never commit it.
2. **Only 59k of the 259k stored glosses may reach a learner.** Cards need an
   Azure or Gemini gloss. Coverage grows about 33,000 sentences a month; the
   deck build tells the monthly job which sentences to gloss first.
3. **Set `GEMINI_FREE_API_KEY` from an unbilled project.** The client falls
   back to `GEMINI_API_KEY`, which on the owner's machine is the billed key.
4. **The corpora are not in the repository** and the raw corpora are already
   split into shuffled sentences: adjacent Leipzig lines are unrelated, so
   two-sentence context cannot be mined, only generated.
5. **The Windows scheduled task "LLA monthly translation" is still enabled.**
   It sends nothing until October's Azure allowance and runs the script on
   this branch's code once the branch is checked out.
6. **Do not weaken a test to make it pass.** The spend ceiling, the retry
   shapes and the Tatoeba trust decision are pinned by tests whose job is to
   make you ask the owner first.
