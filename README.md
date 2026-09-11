# German Phrase Trainer

A personal German phrase spaced-repetition trainer, in the spirit of Lingvist.
Each card is a real corpus sentence with the tokens of one phrase blanked, plus
the sentence's English translation. The learner types the missing tokens. FSRS
schedules the phrases, which are introduced most-frequent first.

Phrases, not words: `warten auf`, `sich interessieren für`, `aufstehen`,
`eine Entscheidung treffen`, `zwar … aber`, `auf jeden Fall`. A phrase may be
split across the sentence, and it is taught in every surface form it takes.

Cards are mined deterministically from real sentences (Tatoeba and Leipzig).
No model writes German. Glosses are machine translations from Azure's free tier.

This repository was a German grammar trainer until 2026-09-08; that project is
shut down and its record is in `docs/audits/`. The pivot is built in three
strict phases: the deck build, then FSRS with a terminal client, then the PWA.

---

## Setup

Requires Python 3.12 or newer, [`uv`](https://github.com/astral-sh/uv), and git.

```bash
git clone <repo-url>
cd <repo>                    # the repo root is the working directory
uv sync                      # installs deps and the German spaCy model
cp .env.example .env         # only needed for the gloss store and context generation
```

`uv sync` installs `de_core_news_sm` from a pinned wheel. There is no separate
`spacy download` step.

## Run

```bash
uv run pytest -q                              # whole suite, no network, no key
uv run python scripts/build_phrase_deck.py --stage all   # phase 1: build the deck
uv run python -m src.cli.train triage                     # phase 2: sort the first units, 1 = known, 2 = learn
uv run python -m src.cli.train practice                   # phase 2: fill the gaps
uv run python -m src.cli.train stats
uv run python -m src.cli.serve                            # the page, laptop and phone on the same Wi-Fi; ⏻ stops it
```

Progress lives in `data/review_log.jsonl` (gitignored); `train merge
<other.jsonl>` folds in a second device's log.

The corpora are staged by hand under `data/raw/_extract/`; see
`docs/phrase-deck.md`. Cards come from the whole corpus, but only cards
whose sentence has a machine gloss in `data/fixtures/translations/de_en.jsonl`
are shown; the store grows through `scripts/agy_jobs.py glosses` and the
monthly `scripts/monthly_translation_topup.py`.

---

## Where to read next

| File | What it gives you |
|---|---|
| `docs/project-state.md` | **Start here.** What works, what does not, what is risky. |
| `CLAUDE.md` | The rules for working in this repo. |
| `TODO.md` | Open work, by phase. |
| `docs/phrase-deck.md` | The deck build runbook. |
| `docs/monthly-translation-job.md` | The Azure gloss top-up. |
| `docs/audits/README.md` | The grammar trainer's audit record. Historical, never instructions. |

Licence: `LICENSE.md`.
