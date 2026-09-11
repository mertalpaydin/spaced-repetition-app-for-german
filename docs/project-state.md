# Project state, 8 September 2026

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

- **Phase 3a, the browser page on a local server, 2026-09-09.**
  `uv run python -m src.cli.serve` serves `web/` and a JSON API over the
  phase 2 engine (standard library only); the page has practice (inline
  gap inputs, umlaut bar, reveal, "Kannte ich schon"), triage and stats
  views and works on the laptop and on the phone over the same Wi-Fi. The
  review log stays on the laptop. Not yet offline and not on GitHub Pages:
  that is phase 3b, the engine in JavaScript.

## What is not built

- **Gemini's second reading of 15,882 agent glosses** (Claude has read all
  38,882; Gemini's quota ended at 23,000). First item in TODO.
- **Phase 3b, the offline PWA.** `web/` now only talks to the local server;
  the GitHub Pages build needs the scheduler and grader in JavaScript
  (ts-fsrs, same parameters), IndexedDB for the log, a service worker for
  the deck shards, and log export/import in the `ReviewEntry`/`MarkEntry`
  shape so laptop and phone logs merge.
- **Nothing else of phase 2**; `train merge <other.jsonl>` folds a second
  device's log in, so the phone log can be merged once phase 3 exists.

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
