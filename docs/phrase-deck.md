# Building the phrase deck

> **Phase 1 is not implemented yet.** This runbook is filled in stage by stage
> as `scripts/build_phrase_deck.py` lands. Until then it records the intended
> commands and the inputs the build needs.

## Inputs

| Input | Path | Notes |
|---|---|---|
| Tatoeba German sentences | `data/raw/_extract/tatoeba_deu.tsv` | `id<TAB>lang<TAB>sentence`, gitignored, staged by hand |
| Leipzig news sample | `data/raw/_extract/leipzig_sample.txt` | `id<TAB>sentence`, gitignored |
| Gloss store | `data/fixtures/translations/de_en.jsonl` | operational, gitignored, never delete; only `azure`/`gemini` rows become cards |
| Frequency list | `data/fixtures/corpus/frequency/de_opensubtitles2018_top50k.txt` | surface ranks, used for the trivial flag |
| CEFR lemmas | `data/fixtures/corpus/vocab_levels.json` | |
| Verb government | `data/fixtures/verb_government/lexicon.v1.jsonl` | frozen fixture |
| Curated lists | `data/phrases/*.yaml` | connectors, idioms, verb-prep and collocation seeds, trivial stoplist |

If a corpus file is missing the build must fail loudly, not produce a smaller
deck.

## Stages

```
uv run python scripts/build_phrase_deck.py --stage parse     # 15-40 min, spaCy over both corpora
uv run python scripts/build_phrase_deck.py --stage mine      # seconds; writes build/units.jsonl and report.json
uv run python scripts/build_phrase_deck.py --stage cards     # 5-10 min; writes build/cards.jsonl, wanted_carriers.txt
uv run python scripts/build_phrase_deck.py --stage export    # seconds; writes data/deck/
uv run python scripts/build_phrase_deck.py --stage all       # the four above, never contexts
```

The one stage that calls a model is opt-in and needs two flags plus the
owner's say-so in chat:

```
uv run python scripts/build_phrase_deck.py --stage contexts --generate-contexts --approved-by-owner
```

It runs on the free lane only, refuses without `GEMINI_FREE_API_KEY`, and
stores accepted context sentences in `data/phrases/contexts.jsonl`.

## Growing the deck

`--stage cards` writes `data/phrases/build/wanted_carriers.txt`: sentences that
would give under-filled units a card if they had a gloss. The monthly Azure job
(`docs/monthly-translation-job.md`) reads it as pass 0, so each month's
allowance goes to the sentences the deck wants first. Re-run `--stage cards
export` after the job to pick them up.
