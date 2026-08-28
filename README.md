# Interleaved German Grammar Trainer (A1 to B2)

A German grammar trainer that shows one exercise at a time: a real German
sentence with one word missing, plus its English translation. Exercises from
different grammar topics are deliberately mixed together, and spaced repetition
(FSRS) schedules the topics. No exercise ever names the grammar point it tests,
which is the whole idea.

Exercises are built from real corpus sentences, tagged and then checked. They
are not written by a model.

---

## Setup

Requires Python 3.12 or newer, [`uv`](https://github.com/astral-sh/uv), and git.

```bash
git clone <repo-url>
cd <repo>                    # the repo root is the working directory
uv sync                      # installs deps and both spaCy models
cp .env.example .env         # then fill in the keys you need
```

`uv sync` installs `de_core_news_sm` and `en_core_web_sm` from pinned wheels.
There is no separate `spacy download` step.

`.env.example` lists every variable. Nothing here needs a key to run the tests.

## Run

```bash
uv run pytest -q                              # whole suite, no network, no key
uv run python -m scripts.step4_run_app        # terminal trainer
```

The PWA in `web/` is static. Serve the directory and open `index.html`.
`.github/workflows/deploy_pages.yml` publishes it to GitHub Pages on every
push to `main`.

There is no item bank yet. To build one, follow `docs/building-the-bank.md`. It
takes hours and it has never been run, so read all of it before starting.

---

## Where to read next

| File | What it gives you |
|---|---|
| `docs/project-state.md` | **Start here.** What works, what does not, what is risky, what it costs. |
| `CLAUDE.md` | The rules for working in this repo: branches, commits, tests, cost discipline. |
| `TODO.md` | Open work, in priority order. |
| `docs/known-defects.md` | Every defect the pipeline lets through, with real examples. |
| `docs/building-the-bank.md` | The commands that build an item bank, and what each one costs. |
| `docs/plan/german-grammar-app-plan.md` | The product design and why it is this way. |
| `docs/00-index.md` and `docs/01-` to `04-` | The stage plans the code was built against. |
| `docs/audits/local-verifier-eval.md` | Why local models were tested as the verifier and why none was adopted. Read before retrying it. |
| `docs/audits/README.md` | Index of the audit record: what each file measured, and on what date. Historical, never instructions. |

Licence: `LICENSE.md`.
