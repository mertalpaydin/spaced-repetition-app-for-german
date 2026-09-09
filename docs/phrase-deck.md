# Building the phrase deck

The phase 1 runbook. `scripts/build_phrase_deck.py` is the only entry point;
everything it writes is under `data/phrases/build/` (gitignored) until the
export stage writes `data/deck/` (committed).

## Inputs

| Input | Path | Notes |
|---|---|---|
| Tatoeba German sentences | `data/raw/_extract/tatoeba_deu.tsv` | `id<TAB>lang<TAB>sentence`, gitignored, staged by hand |
| Leipzig news 2025 | `data/raw/_extract/leipzig_news_2025.txt` | 1M lines, `id<TAB>sentence`, gitignored |
| Leipzig news 2024, mixed 2011, web 2021 | `data/raw/_extract/<package>-sentences.txt` | 1M lines each; the `-sentences.txt` member of each `*_1M.tar.gz` from `downloads.wortschatz-leipzig.de/corpora/` |
| OpenSubtitles 2018, German | `data/raw/_extract/opensubtitles_2018_sample.txt` | a seeded 1M-line reservoir sample of plausible carriers from OPUS `v2018/mono/de.txt.gz`, numbered |
| Gloss store | `data/fixtures/translations/de_en.jsonl` | operational, gitignored, never delete; only `azure`/`gemini` rows become cards |
| Frequency list | `data/fixtures/corpus/frequency/de_opensubtitles2018_top50k.txt` | surface ranks, used for the trivial flag |
| CEFR lemmas | `data/fixtures/corpus/vocab_levels.json` | |
| Verb government | `data/fixtures/verb_government/lexicon.v1.jsonl` | frozen fixture; reflexive pronoun case fallback |
| Curated lists | `data/phrases/*.yaml` | see below |

A missing corpus file is an error. The build never produces a smaller deck
silently. `--no-default-extras` reads only Tatoeba and Leipzig news 2025;
`--extra-corpus name=path` adds another `id<TAB>sentence` file.

Ranking uses the mean, over the corpora, of a unit's sentences per million
sentences of that corpus, so a 400k everyday corpus and a 1M news corpus get
one vote each and no register dominates. Thresholds still use raw counts.
Subtitle lines become cards only when the sentence validator passes them.

## The curated lists

| File | Holds | Effect |
|---|---|---|
| `connectors.yaml` | connectors and two-part connectors, with forms, POS guards, `needs_context_when_initial`, `trivial` | matched, never mined |
| `idioms.yaml` | fixed expressions as token patterns with `max_gap` | matched, never mined |
| `verb_prep_seed.yaml` | verb + preposition (and reflexive) units with case, CEFR, gloss | unit whatever the counts say; seed case wins, disagreement reported |
| `collocation_seed.yaml` | noun-verb collocations with display form, CEFR, gloss | unit whatever the association says |
| `trivial_stoplist.yaml` | lemma keys flagged trivial | skipped by the scheduler by default |
| `exclude.yaml` | lemma keys dropped outright, each with its reason | the curation round; grows from `report.json` and from reviews |
| `excluded_cards.yaml` | card ids a review rejected | never selected again |
| `unit_overrides.yaml` | reviewer corrections: case, citation form, CEFR | applied after the unit decision |

## Stages

```
uv run python scripts/build_phrase_deck.py --stage parse     # ~10 min: spaCy over both corpora
uv run python scripts/build_phrase_deck.py --stage mine      # seconds: build/units.jsonl, build/report.json
uv run python scripts/build_phrase_deck.py --stage cards     # minutes: build/cards.jsonl, wanted_carriers.txt
uv run python scripts/build_phrase_deck.py --stage export    # seconds: data/deck/, the JSON schema fixture
uv run python scripts/build_phrase_deck.py --stage all       # the four above, never contexts
uv run python scripts/build_phrase_deck.py --check           # validate data/deck/ (CI, no corpus needed)
```

Every stage is deterministic and idempotent over `build/`; re-run `mine`
after editing a curated list without re-parsing. A lock file in `build/`
stops two builds from overlapping.

### What each stage decides

- **parse** writes one `Occurrence` per phrase match per sentence, generously.
  Detectors: verb + preposition (`op`/`mo` prepositions on a lexical verb,
  time and measure frames excluded), reflexive verbs (pronouns with
  `Reflex=Yes` or subject agreement), separable verbs (`svp` particle;
  fused infinitives and participles), noun-verb and adjective-noun
  collocations (objects only, `cvc` for Funktionsverbgefüge), connectors and
  idioms from the lists.
- **mine** applies the thresholds in `src/phrases/units.py` (`Thresholds`):
  count, verb ratio and lift for verb + preposition; share or hand list for
  reflexives, with `sich V prep` replacing `sich V` when it covers 60%; a
  fused-only separable verb is dropped; collocations need count, both lemmas
  attested 20 times, G² ≥ 15.13 and lift ≥ 5, capped per verb and per noun.
  Ranks by distinct sentence count. Flags trivial units. Writes
  `report.json`: top 200 per kind, rejections by reason, seed disagreements,
  zero-hit curated entries.
- **cards** picks from the whole corpus: one occurrence of the unit per
  sentence, no Konjunktiv I, no answer token repeated outside the gaps, no
  stray quotation mark, passes `carrier_validation`, not on the excluded
  list; covers distinct surface forms first, a glossed sentence winning a
  tie, then fills to 6 per unit (2 for trivial ones). A card without an
  Azure or Gemini gloss is exported with `gloss_en: null` and is not shown
  until glossed. Writes `wanted_carriers.txt`: every chosen sentence still
  without a gloss, for the monthly Azure job.
- **export** joins accepted contexts, sets `card_count`, and writes
  `manifest.json`, `units.json` and `shards/band_NNN.json` (200 units each,
  ranked). `deck_version` is a content hash.

## The one model stage

```
uv run python scripts/build_phrase_deck.py --stage contexts --generate-contexts --approved-by-owner
```

Cards whose connector opens the sentence need a preceding sentence. The stage
asks `gemini-3.5-flash-lite` on the free lane only (refuses without
`GEMINI_FREE_API_KEY`), parses the JSON strictly, rejects deterministically
(length, punctuation, quotes, the connector itself, `carrier_validation`),
and stores every result in `data/phrases/contexts.jsonl` (committed) so a
re-run never re-asks. Without both flags it reports and does nothing. The
owner approves each run in chat on top of the flag.

## Growing the deck

Cards are picked first and glossed afterwards. The monthly Azure job
(`docs/monthly-translation-job.md`) reads `build/wanted_carriers.txt` as
pass 0, so each month's 2,000,000 characters go to the sentences the deck
wants first; the whole wanted list is about 1.3M characters. After it runs: `--stage cards` then `--stage export`, commit
`data/deck/`.

## Review

Zero-defect policy: every card is read before a learner sees it. The record
of each round is in `docs/audits/phase-1-review/`. A round reads the deck as
plain-text batches (600 cards or 700 units per agent), writes findings as
JSON lines, and those become curated-list entries with reasons; systematic
causes become code rules with tests. A round after a rebuild reads only the
card ids not in the previous round's `reviewed-card-ids-*.txt`.

## Tuning

Read `build/report.json` after every build. Noise goes into `exclude.yaml`;
a missing unit goes into a seed list; a threshold change is a code change to
`Thresholds` with the report numbers in the commit body. The golden test
`tests/test_build_phrase_deck.py` pins the output over a 300-line sample, so
any of those changes needs its expected files regenerated in the same commit.
