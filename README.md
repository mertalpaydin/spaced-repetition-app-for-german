# Phrasen: a German phrase trainer

A spaced-repetition trainer for German **phrases**, not words, in the spirit of
Lingvist. Every card is a real sentence from a corpus with the tokens of one
phrase blanked out, plus its English translation. You type the missing tokens;
FSRS decides when you see the phrase again.

**Try it:** <https://mertalpaydin.github.io/spaced-repetition-app-for-german/>
(works offline after the first visit, installs as an app on Android and desktop Chrome).

<p align="center">
  <img src="docs/screenshots/practice-dark.png" width="640" alt="A card: a German sentence with two gaps and its English translation">
</p>

## Why phrases

Word lists teach `warten`. German is spoken in `warten auf + Akk`, `sich
interessieren für`, `aufstehen`, `eine Entscheidung treffen`, `zwar … aber`,
`auf jeden Fall`. The unit of learning here is the phrase, and a phrase is
taught in every surface form the corpus uses it in: `wartet auf`, `wartete
auf`, `warte … auf`, `gewartet auf`. A phrase may be split across the
sentence; then the card has several gaps.

Eight kinds of phrase are mined: verb + preposition, reflexive verb, separable
verb, noun-verb and adjective-noun collocations, connectors, two-part
connectors, and fixed expressions. 7,452 phrases, 40,397 cards, introduced
most-frequent first; every phrase shows its English after the answer, most
common rendering first (`aussehen: to look / to appear`).

## What it looks like

| Answer graded, phrase revealed | Your phrases by stage |
|---|---|
| ![feedback](docs/screenshots/feedback-light.png) | ![units](docs/screenshots/units-dark.png) |

| Statistics | On a phone |
|---|---|
| ![stats](docs/screenshots/stats-dark.png) | <img src="docs/screenshots/practice-phone.png" width="300" alt="phone"> |

Typing is graded with a scoped typo tolerance: one edit outside the inflected
ending counts as a typo (rated "hard"), an edit that changes the grammar
(`dem` for `den`, `hatte` for `hätte`) counts as wrong. `ae`, `oe`, `ue` and
`ss` are accepted for umlauts and ß. A sentence-initial gap accepts lower case.

## How it is built

**The deck is mined, not written.** No model writes German. Six corpora
(Tatoeba, four Leipzig packages, an OpenSubtitles sample, about four million
sentences) are parsed once with spaCy. Deterministic detectors find each
phrase kind from the dependency parse: a preposition governed by a verb, a
reflexive pronoun that agrees with the subject, a separable prefix attached
to its verb, a noun-verb pair with a high log-likelihood ratio. Phrases are
ranked by how many distinct sentences use them, with everyday corpora
weighted up so that `jedoch` does not outrank `nicht mehr`.

**Cards need a trusted translation.** Only sentences with a machine
translation (Azure Translator or Gemini) become cards; the corpus's own
crowd translations are never shown. A sentence-initial connector (`Trotzdem
…`) gets a one-sentence context so the connector is answerable.

**Two reviewers read every card.** The deck was reviewed sentence by
sentence by two independent model reviewers under a zero-defect policy,
and every systematic finding became a mining rule with a test; the record is
in `docs/audits/phase-1-review/`. New cards from a rebuild go through the
same review before they are committed.

**The page is the whole product.** `web/` is plain ES modules with no build
step and no npm dependency. The FSRS-6 scheduler, the grader, the session
logic and the review-log replay are ports of the Python originals in `src/`,
and node tests pin each port against fixtures the Python side generates, so
a log replays to the same due dates on both. The review log is an
append-only JSONL; it lives in IndexedDB and, with a fine-grained GitHub
token (gist scope only), in a private gist that every device merges, so the
laptop and the phone keep one log. A service worker caches the shell and the
deck by version for offline use.

```text
corpora ──parse──▶ occurrences ──mine──▶ ranked phrases ──cards──▶ deck (JSON shards)
                                                                       │
                                        GitHub Pages ◀──── web/ ◀──────┘
                                        browser: FSRS · grader · IndexedDB log · gist sync
```

## Stack

Python 3.12, spaCy (`de_core_news_sm`), pydantic, py-fsrs, pytest with
hypothesis and a coverage gate, ruff, mypy strict. Browser: vanilla ES
modules, IndexedDB, service worker, node:test. CI on GitHub Actions; the
page deploys from the repository on every push.

## Run it yourself

```bash
uv sync                                                   # Python deps and the German spaCy model
uv run pytest -q                                          # the whole suite, no network, no key
uv run python -m src.cli.serve                            # the page on http://localhost:8765
uv run python scripts/build_phrase_deck.py --stage all    # rebuild the deck (needs the corpora, see docs/phrase-deck.md)
```

The terminal client (`uv run python -m src.cli.train practice`) runs the same
engine on the same log format without a browser. Growing the deck (new
translations, review, rebuild) is a manual round described in
`docs/phrase-deck.md`.

## Where to read next

| File | What it gives you |
|---|---|
| `docs/project-state.md` | What works, what does not, what is risky. |
| `docs/phrase-deck.md` | The deck build runbook. |
| `docs/monthly-translation-job.md` | The Azure gloss top-up. |
| `docs/audits/README.md` | The review record, and the earlier grammar trainer this repository grew out of. |

Licence: `LICENSE.md`.
