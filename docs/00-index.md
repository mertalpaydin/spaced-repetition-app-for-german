# 00: Stage Index and Conventions

The implementation is split into 14 stages across four documents.

**Terminology.** A **round** is 5 to 8 items (default 6) and is the unit every pacing rule applies to. A **day** carries budgets that must not scale with round count, chiefly new-topic introductions. A **sitting** is however many rounds the user chooses and has no rules of its own. The word "session" is not used. Each stage has a contract, deliverables, a strict test set, and a Definition of Done. A stage is finished when its DoD is fully ticked and its tests pass on a clean checkout.

Read `CLAUDE.md` first. Read the product plan in `docs/plan/` for rationale.

---

## Stage map

| Stage | Name | Document | Branch | Gate |
|---|---|---|---|---|
| 0 | Repo scaffold and CI | `01-foundation.md` | `stage/00-scaffold` | |
| 1 | Taxonomy | `01-foundation.md` | `stage/01-taxonomy` | |
| 2 | Corpus and lexical resources | `01-foundation.md` | `stage/02-corpus` | |
| 2b | Learner error corpus | `01-foundation.md` | `stage/02b-learner-corpus` | |
| 2c | Exercise scraping | `01-foundation.md` | `stage/02c-scraping` | |
| 3 | Generation | `02-content-pipeline.md` | `stage/03-generation` | |
| 4 | Verification chain | `02-content-pipeline.md` | `stage/04-verification` | **KILL GATE** |
| 5 | Bank storage and export | `02-content-pipeline.md` | `stage/05-bank` | |
| 6 | Scheduler and Kalibrierung | `03-learning-engine.md` | `stage/06-scheduler` | |
| 7 | CLI and grading | `03-learning-engine.md` | `stage/07-cli` | **USABILITY GATE** |
| 8 | PWA | `04-application.md` | `stage/08-pwa` | |
| 9 | Worker, D1, sync | `04-application.md` | `stage/09-worker` | |
| 10 | Nightly automation | `04-application.md` | `stage/10-automation` | |
| 11 | Live LLM features | `04-application.md` | `stage/11-live-features` | |

### Gates

**Stage 4 is a kill gate.** **[AGENT-CRITICAL]** After the verification chain is built, audit 100 items and compute post-verifier error rate. Above roughly 15%, stop and rework the pipeline. Do not proceed to stage 5 with an unmeasured or failing bank.

**Stage 7 is a usability gate.** Use the CLI daily for two weeks before starting stage 8. If interleaved rounds are intolerable or the scheduling feels wrong, that is discovered here at the cost of zero frontend work.

---

## Shared conventions

### Contracts between stages

Every stage exposes a typed interface. Downstream stages depend on the interface, never on the implementation. Interfaces are declared in the stage document and mirrored as Pydantic models or TypedDicts in `src/<module>/contracts.py`.

If a contract must change, update the stage document in the same PR.

### Core enumerations

```python
CEFR      = Literal["A1", "A2", "B1", "B2"]
Dimension = Literal["grammar", "vocab"]
TagState  = Literal["unseen", "learning", "acquired"]
AcquiredVia = Literal["kalibrierung", "inferred", "earned"] | None
ItemType  = Literal["cloze_free", "cloze_cued", "error_correction",
                    "transformation", "paragraph_cloze", "production"]
Difficulty = Literal[1, 2, 3]
HintLevel  = Literal[0, 1, 2, 3, 4]   # 0 none, 1 shape, 2 options,
                                      # 3 rule stated, 4 revealed
FsrsRating = Literal["again", "hard", "good", "easy"]
ReviewMode = Literal["review", "duel", "challenge", "recalibration"]
# tag_state recomputation filters to mode in ("review", "recalibration").
# duel and challenge are practice, never measurement.

ROUND_SIZE_DEFAULT = 6      # UI setting, range 4-10; unit all pacing rules use
VOCAB_RATIO_DEFAULT = 0.30  # UI slider, 0.00 to 0.50; 0.00 disables vocabulary
WEEKLY_REPORT_TRIGGER_ITEMS = 40   # items since last report that auto-fire it
WEEKLY_REPORT_MANUAL_MIN_ITEMS = 10  # gate on the manual button
MAX_HEAVY_PER_ROUND = 1     # heavy = paragraph block OR production item
MAX_NEW_TOPICS_PER_DAY = 2  # a DAILY budget, never per round
MAX_REVIEWS_PER_DAY = 60    # backlog cap; overflow slips rather than piling up
FORECAST_HORIZON_DAYS = 7   # window checked before allowing new introductions
FORECAST_LOAD_THRESHOLD_DEFAULT = 50  # user-configurable; see stage 6
SUGGESTION_WINDOWS = (7, 14, 30)      # days; default 14
SUGGESTION_MIN_ACTIVE_DAYS = 10       # below this, no suggestion is offered
THRESHOLD_CLAMP = (10, 200)           # guards degenerate histories
INFERRED_STABILITY_CEILING_DAYS = 4   # hard cap for acquired_via="inferred"
OVERRIDE_UPHELD_RATE_ALERT = 0.03     # above this, the verification chain is failing
PROMOTION_CONSECUTIVE_PASSES = 3      # learning -> acquired, unhinted
PROMOTION_MIN_DISTINCT_FACETS = 2     # evidence must span cells, not repeat one
SPLIT_MIN_ATTEMPTS_PER_FACET = 20     # before a topic can be flagged for splitting
SPLIT_ACCURACY_GAP = 0.40             # facet accuracy spread that flags a candidate

MODEL_LIVE      = "gemini-3.5-flash-lite"   # explanations, production grading, report
MODEL_GENERATE  = "gemini-3.5-flash-lite"   # batch, thinking OFF
MODEL_VERIFY    = "gemini-3.7-flash"        # batch, thinking low/medium
THINKING_VERIFY = "low"                     # tune against measured recall

DUEL_LENGTH = 8                       # items per duel, range 6-8
DUEL_MIN_ATTEMPTS_TO_SUGGEST = 15     # per confusion group, before ranking it
DORMANCY_DAYS = 21                    # triggers a recalibration round on return
RECALIBRATION_ROUND_SIZE = 10
NO_ERROR_ITEM_SHARE = 0.22            # share of error-correction items with no error

# Prices and free-tier limits move. Stage 0 verifies both against Google's
# current pricing page and the live AI Studio rate-limit view, records them
# in docs/audits/stage-00-quota.md, and recommends swaps if a slot is stale.
```

These are defined once, in `src/contracts.py`, and imported everywhere. Do not redeclare them per module.

### Fixture directories

```
data/fixtures/
├── taxonomy/
│   └── expected_ids.json         # golden: topic IDs must not drift silently
├── verification/
│   ├── adversarial.jsonl         # 60 deliberately broken items, labelled by defect
│   └── known_good.jsonl          # 60 correct items, external keys preferred
├── corpus/
│   └── mapped_sample.jsonl       # 100 learner errors mapped to topic ids
├── scraping/
│   └── <site>.html               # frozen pages, one per scraper
├── grading/
│   └── grading_table.csv         # (expected, given, verdict, reason)
├── fsrs/
│   └── parity_vectors.json       # cross-language py-fsrs / ts-fsrs golden vectors
└── production/
    └── labelled_answers.jsonl    # 30 learner sentences with expert grades
```

Golden fixtures are versioned and never regenerated casually. They are produced under the **agent-critical protocol** (`CLAUDE.md` section 10): a capable-tier agent derives them, a second agent **from a different vendor** independently re-derives and diffs, disagreements are committed rather than silently resolved, and an external key anchors the result wherever one exists.

A model grading fixtures it generated itself proves nothing, which is why the external anchor outranks both agents for any artefact used as ground truth.

**`**[AGENT-CRITICAL]**` marks these items throughout the stage documents.**

### Definition of Done (applies to every stage)

- [ ] All stage-specific tests pass
- [ ] `ruff check`, `ruff format --check`, `mypy --strict src/` clean
- [ ] Coverage on changed files at or above 85%
- [ ] Contracts documented in the stage file match the code
- [ ] No `@pytest.mark.skip` or `xfail` added without an inline reason and a linked issue
- [ ] README updated if the stage adds a user-facing command
- [ ] Branch rebased on `main`, PR checklist complete

---

## Test taxonomy used throughout

| Marker | Meaning | Runs |
|---|---|---|
| (none) | Unit. No network, no filesystem outside tmp, no clock. | Every push |
| `@pytest.mark.simulation` | Synthetic-learner scheduler runs. Slow, deterministic seed. | PRs to `main` |
| `@pytest.mark.live` | Real API or real network. | Nightly on `main` only |
| `@pytest.mark.golden` | Compares against a versioned fixture. | Every push |

A unit test that makes a network call is a defect, not a slow test.
