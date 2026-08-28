# 04: Application (Stages 8 to 11)

> **Design record, written before most of the code existed. Not a description
> of what the code does now.**
>
> The corpus pipeline that makes every exercise today, and the English
> translation shown under every one, are absent from these contracts entirely:
> neither "blank" nor "Azure" appears in this document. `docs/project-state.md`
> has both.
>
> Named departure: it puts the static PWA on Cloudflare Pages, and the only
> deploy workflow in the repository, `.github/workflows/deploy_pages.yml`,
> publishes to GitHub Pages. Its Definition-of-Done boxes are unticked because
> nobody ticked them, not because the work is undone.

---

## Stage 8: PWA

**Branch:** `stage/08-pwa`

### Goal

An installable offline web app that runs the same scheduling and grading logic as the CLI. Chrome is the primary target, built to Safari's constraints throughout.

### Deliverables

- Static PWA on Cloudflare Pages
- Service worker caching `bank.json` and the app shell
- `ts-fsrs` scheduler, parity-tested against `py-fsrs`
- Grading module ported from stage 7, parity-tested against it
- IndexedDB persistence
- Web app manifest, icons, offline fallback

### Design constraints

Before writing UI, read `/mnt/skills/public/frontend-design/SKILL.md` if available in the environment. The interface is a text input and a sentence; the design work is in typography, feedback timing, and progress presentation, not in layout complexity.

Hard requirements:

- Every text input carries `autocorrect="off" autocapitalize="off" spellcheck="false"`. Without these the browser silently capitalises or "fixes" the exact ending under test.
- ä ö ü ß buttons above the input on touch viewports.
- Progress view shows topic stability and retention curves. **Round accuracy is never the headline number.**
- **Daily progress bar: full means the obligation is met, not that a maximum was reached.** The bar fills as the due queue clears. Bonus rounds render as a visually distinct overflow segment past full, so extra practice reads as optional rather than as progress toward a target. A bar whose full point is the ceiling invites chasing the ceiling, which inverts the intent.
- Seven-day forecast visible alongside today's count, so the cost of taking on new topics is legible before taking them on.
- **Settings surface** holds the round size (4 to 10, default 6) and the grammar-to-vocabulary slider (50/50 to 100/0, default 70/30, with 100/0 disabling vocabulary entirely). Changes take effect from the next round, never mid-round.
- **Load threshold is a settings control**, labelled by effect rather than by name: *pause new topics when any of the next 7 days would exceed ___ reviews.* Shown with a window selector (7 / 14 / 30 days, default 14), the suggested value, and the three numbers behind it: median items per active day, clear rate, active-day rate. Applying the suggestion is always an explicit tap.
- Answer submission is instant. No spinner, no network, no perceptible latency.

### Tests

```typescript
// --- Parity with the Python implementation ---

test('fsrs parity vectors match py-fsrs')
  // loads data/fixtures/fsrs/parity_vectors.json, the same file
  // asserted in stage 6. Divergence here means the CLI and the PWA
  // schedule the same user differently.

test('grading parity: every row of grading_table.csv matches')
  // the same 80-row fixture from stage 7, run against the TS port.
  // Two grading implementations is the second data-corruption risk
  // after FSRS.

test('bank export schema matches the fixture asserted in stage 5')

// --- Offline ---

test('service worker serves a full round with network disabled')
  // Playwright, offline mode, complete a 20-item round end to end

test('app shell loads from cache on second visit with network disabled')

test('IndexedDB state survives a reload')

test('IndexedDB state survives a service worker update')
  // a naive cache-bust that clears storage destroys user progress

// --- Input handling ---

test('every text input has autocorrect, autocapitalize and spellcheck off')
  // DOM scan across all rendered exercise types

test('umlaut buttons insert at cursor position, not at end')

test('paste of a transliterated answer grades identically to typed')

// --- Round behaviour ---

test('no two consecutive rendered items share a tag id')
  // the interleaving invariant, asserted at the UI layer too

test('answer submission performs no network request')
  // fail the test if any fetch fires during grading

test('progress view does not display round accuracy as a primary metric')

test('progress bar reaches full exactly when the due queue is cleared')

test('bonus rounds render past full as a distinct overflow segment')
  // never as additional fill: that would make the ceiling look like the goal

test('no UI path blocks the user from starting another round')

test('daily notices render only between rounds, never mid-round')

test('threshold suggestion shows its three inputs, not just the number')
  // an unexplained number gets applied blindly or ignored entirely

test('applying the suggestion requires an explicit tap')

test('window selector defaults to 14 days and offers 7 and 30')

test('round size and vocab ratio changes apply from the next round, not mid-round')

test('vocab slider at 100/0 removes vocabulary items entirely')

test('insufficient-history state renders an explanation, not a placeholder number')
```

### Retention and feedback surfaces

Eight features, ordered by impact per hour of work. The first two are the highest leverage in the whole interface, because the two seconds after answering is where learning actually happens.

#### 1. Diff highlighting on wrong answers

Never show the correct answer as a bare string. Show the difference: `d[en -> em]`, with the differing morpheme highlighted. German errors are usually one or two letters and the eye needs help finding them.

#### 2. "Correct, but" feedback

A flat green tick after a scoped typo passed, or after capitalisation was wrong outside the tested morpheme, silently teaches a wrong spelling. Pass the item, and say what was off.

#### 3. Round preview

"6 items, about 2 minutes." The ask has to be legible and small. "Do one round" is a far lower barrier than "study German", and the round design already makes that true; the interface just has to say it. The time estimate is derived from the user's own median response time, not a constant.

#### 4. Grammar coverage bar

**Not** a level bar. Three corrections to the obvious version:

- **Labelled grammar coverage, never level.** A bar labelled "B1" reaching 100% claims something about the user's German that grammar topic coverage cannot support.
- **Capped at B2.** The taxonomy stops there. A segment that can never fill is a permanent visible failure.
- **Three segments per level: acquired, learning, unseen.** A single fill over 85 topics moves imperceptibly, which is the opposite of what a retention surface needs. Three segments mean a topic entering `learning` shows the same day.

#### 5. Pace estimate

A range, from a 30-day window, phrased as pace rather than prophecy: "at your recent pace, 6 to 10 weeks to cover B1 grammar."

Three reasons the naive version is worse: a 7-day window swings between "23 days" and "four months" on one bad week, linear extrapolation systematically over-promises because remaining topics are harder and review load grows, and a self-imposed deadline that gets missed is a well-documented quit trigger. **Never render a calendar date.**

Suppressed below 10 active days, same rule as the threshold suggestion.

#### 6. Streak, defined carefully

Two details decide whether this helps or harms:

- **The streak counts days the due queue was cleared**, not days the app was opened. Otherwise it rewards trivial engagement and means nothing.
- **Two automatic freezes per month, plus a repair option.** Losing a long streak is among the most common quit moments in this product category. The freeze is not a gimmick; it is the feature.

#### 7. Named progress report

"Konjunktiv II, Relativsatz im Genitiv and Passiv Präteritum went solid." Three named topics beat any number, and it is computed directly from `tag_state` transitions. This is the thing that feels like getting better.

**Rolling window, not Sunday to Sunday.** The window runs from the timestamp of the last report to now. A calendar week punishes anyone whose study rhythm is not aligned to it and produces an empty report after a quiet Monday to Friday.

**Activity-triggered, plus a manual button.** Fires automatically once completed items since the last report reach `WEEKLY_REPORT_TRIGGER_ITEMS`, so a light week waits instead of reporting on nothing. A manual button is always visible, gated on `WEEKLY_REPORT_MANUAL_MIN_ITEMS` of new activity so the window cannot be shrunk to noise, and rate-limited because the narrative line is an LLM call.

#### 8. Topic map

The prerequisite DAG rendered as a graph: acquired, learning and locked nodes, with edges showing why something is locked. It makes the frontier visible in a way no linear bar can, answers "why am I not getting Konjunktiv I yet", and is the best screenshot in the project.

#### 9. Duel library

Every confusion group listed, with the numbers that make the choice informed:

| Column | Why |
|---|---|
| Member topics, CEFR span | What the duel actually contrasts |
| Introduced yet | Locked groups stay visible with the reason shown |
| Duels attempted, last date | Recency without shame |
| Accuracy in duels | Practice performance |
| Accuracy in normal review | The real signal; duel accuracy is inflated by blocking |
| Confusion rate (A answered as B) | Why this group is suggested |
| Items available | Whether a duel can even be assembled |

**Suggestions follow the threshold-suggestion pattern**: a ranked recommendation with the numbers behind it and an explicit tap to start. Never auto-launched, never modal, never interrupting a round.

Ranked by empirical confusion rate rather than accuracy, since low accuracy alone may mean a hard topic while a high A-answered-as-B rate is direct evidence of the confusion the duel exists to fix.

#### 10. Daily challenge

One free-writing prompt per day with live AI feedback on accuracy, naturalness and whether the target structure was used. Lives on its own tab, never inside a round.

The prompt names the grammar point, which happens nowhere else in the app. Offline or past the ceiling, it shows a model answer for self-comparison, which costs nothing because it was never a measurement.

#### 11. Data export

Client-side JSON: `review_log`, `tag_state`, settings, schema version. Import restores from it. Export is a dump and import is a replay, with `tag_state` recomputed from the log rather than trusted from the file.

**No Anki export.** Anki cannot reproduce accepted-answer sets, scoped typo tolerance or facet tagging, so exported cards would grade differently from the app, and topic-level FSRS stability has no equivalent in Anki's card-level state.

#### Deliberately excluded

| Excluded | Why |
|---|---|
| XP, points, leaderboards | No other users. The numbers would mean nothing. |
| Hearts, lives, lockouts | Punitive, and directly at odds with the hint ladder. |
| Daily goal as an item count | Turns the ceiling into a target, which is what the progress bar design exists to prevent. |
| Guilt-toned notifications | Produces short-term compliance and long-term deletion. |

### Retention surface tests

```typescript
// --- Diff highlighting and "correct, but" ---

test('diff highlight marks the differing morpheme, not the whole word')
  // fixture-driven: (expected, given, expected_highlight_span)

test('diff highlight handles multi-token answers')
  // "ist gefahren" vs "hat gefahren" marks the auxiliary only

test('diff highlight handles umlaut transliteration without marking it an error')

test('correct-but fires when a scoped typo passed')

test('correct-but fires on capitalisation wrong outside the tested morpheme')

test('correct-but never fires on an exact match')

// --- Round preview ---

test('round preview time estimate derives from user median response time')
  // not a hardcoded constant; a slow reader must see a longer estimate

test('round preview states item count before the round begins')

// --- Coverage bar ---

test('coverage bar has no segment above B2')
  // a segment that can never fill is a permanent visible failure

test('coverage bar is labelled coverage, never level')
  // DOM assertion on the label text

test('each level renders acquired, learning and unseen segments separately')

test('a topic entering learning moves the bar the same day')
  // the single-fill version moves imperceptibly, which defeats the purpose

// --- Pace estimate ---

test('pace estimate uses a 30-day window')

test('pace estimate renders a range, never a point value')

test('pace estimate never renders a calendar date')
  // a self-imposed deadline that gets missed is a quit trigger

test('pace estimate suppressed below ten active days')

// --- Streak ---

test('streak increments only when the due queue was cleared')
  // opening the app must not count

test('two freezes accrue per month and apply automatically')

test('a frozen day does not reset the streak')

test('streak repair is offered after a break, not silently applied')

// --- Weekly progress ---

test('progress report names at most three topics')

test('progress report is computed from tag_state transitions')

test('progress report with no transitions renders an honest empty state')
  // not a fabricated encouragement message

test('report window runs from last report timestamp, not calendar week')
  // property test over irregular study patterns

test('report auto-fires only after the item threshold since last report')

test('manual button is gated on minimum new activity')
  // otherwise repeated taps shrink the window to noise

test('generating a report resets the window start')

test('report generation respects the spend ceiling and degrades to counts only')

// --- Topic map ---

test('topic map edges match the taxonomy DAG exactly')

test('locked node displays the specific prerequisite blocking it')

test('topic map renders without a network call')

// --- Exclusions, enforced ---

// --- Duel library ---

test('every confusion group appears, including locked ones')

test('locked group shows the specific reason it is locked')

test('duel accuracy and review accuracy shown as separate columns')
  // duel accuracy is inflated by blocking; conflating them misleads

test('suggestions ranked by confusion rate, not accuracy')

test('duel never auto-launches and never interrupts a round')

// --- Daily challenge ---

test('challenge lives on its own tab and never renders inside a round')

test('challenge feedback shows all three rubric dimensions')

test('offline challenge shows a model answer instead of failing')

// --- Export ---

test('export round trip: export, wipe, import yields identical tag_state')
  // tag_state is recomputed from the log, never read from the file

test('export includes a schema version and import rejects unknown versions')

test('export runs entirely client-side with no network call')

test('no Anki export path exists')
  // exported cards would grade differently from the app

test('no XP, points, hearts or lives appear anywhere in the source')
  // source scan. These get added back by well-meaning increments.

test('no daily goal is expressed as a target item count')
  // the ceiling must never render as a goal to reach
```

### Definition of Done

- [ ] All eleven retention surfaces implemented with their tests green
- [ ] Exclusion scan passing
- [ ] Both parity suites green against the shared fixtures
- [ ] Full offline round verified in Playwright
- [ ] Installable on Chrome desktop and iOS home screen, verified manually
- [ ] Lighthouse PWA checks passing

---

## Stage 9: Worker, D1, sync

**Branch:** `stage/09-worker`

### Goal

Progress survives storage eviction and device changes. Delta bank delivery so the client never re-downloads the whole bank.

### Why this exists

Safari clears IndexedDB after seven days of inactivity for ordinary tabs. Home-screen PWAs are exempt, but data still dies if the icon is deleted or storage runs low. Chrome on iOS is WebKit underneath, so the constraint applies there too regardless of the icon.

### Contract

```typescript
POST /sync        // client sends review_log delta + tag_state, receives merged
GET  /bank/delta  // ?since=<item_id> -> items after that id only
POST /override    // { item_id, user_answer, tag_id } -> upheld | rejected + reason
POST /explain     // { item_id, user_answer } -> cached or generated explanation
POST /grade       // { item_id, user_answer } -> production grading
```

Sync is last-write-wins per `review_log` row, keyed by `(item_id, timestamp)`. `review_log` is append-only, which makes merging trivial: union the rows and recompute `tag_state` from scratch. **Never merge `tag_state` directly.** It is derived data and merging derived data is how progress corrupts.

### Tests

```typescript
test('review_log union is order independent')
  // property test: shuffle insert order, assert identical merged result

test('tag_state is always recomputed from review_log, never merged')
  // assert the sync handler has no tag_state write path that
  // does not pass through recomputation

test('clock skew does not lose rows')
  // client clock 10 minutes behind; assert no row dropped

test('duplicate review_log rows are idempotent')
  // same (item_id, timestamp) submitted twice yields one row

test('delta returns exactly items after since_id')
  // property test over random insert orders, shared with stage 5

test('delta with a current client returns empty and does not read the bank table')

test('unauthenticated request is rejected')

test('explain cache hit returns without calling the LLM')
  // mock the LLM binding; assert zero calls on a warm key

test('explain cache key is (item_id, user_answer) not item_id alone')
  // caching by item alone returns an explanation for someone else's mistake

test('worker responds within latency budget on cache hit')

test('sync failure leaves local state intact and retries')
  // the client must never lose progress because the network failed

// --- Overrides ---

test('override endpoint runs the disputed answer through answer-set expansion')
  // the same verifier that built the original set, not a softer check

test('upheld override adds the answer to accepted_answers')

test('rejected override returns a reason and changes nothing in the bank')

test('no code path writes a user answer into accepted_answers without verification')
  // source scan. This is the bank-poisoning vector: a learner
  // convinced "den Tisch" is Dativ would otherwise inject that
  // belief and stop being taught the thing they are wrong about.

test('override queue survives being offline for days and syncs in order')

test('upheld and rejected override rates are exposed as separate metrics')
```

### Definition of Done

- [ ] Round-trip sync verified across two browser profiles
- [ ] Deliberate IndexedDB wipe recovers full progress from D1
- [ ] Delta endpoint verified against a client several hundred items behind
- [ ] Explanation cache hit rate measurable from logs
- [ ] Override round trip verified: local immediate, global only after verification

---

## Stage 10: Nightly automation

**Branch:** `stage/10-automation`

### Goal

The bank tops itself up nightly, batched, with hard budget limits, on infrastructure that is not your computer.

### Why GitHub Actions and not a Worker

The verification chain needs spaCy and an embedding model. Neither runs in a Cloudflare Worker's V8 isolate. GitHub Actions runs the Python pipeline directly: 2,000 free minutes/month on private repos, unlimited on public. Your machine is never involved.

Because batch results are asynchronous, the job splits in two.

| Workflow | Cron | Does |
|---|---|---|
| `generate-submit` | `0 2 * * *` | Read `tag_state` from D1, compute per-tag deficits, build spec-driven requests, submit **one** batch covering every topic. Persist batch ID. Exits in under a minute. |
| `generate-ingest` | `0 8 * * *` | Poll the batch. If complete: run the verification chain, dedupe, insert into D1, log cost. If not complete: exit 0 and retry tomorrow. |

One batch per night, not one call per topic. Half price, and a pending batch is harmless because the bank holds 14 days of stock by design.

### Deficit calculation

```
projected_demand = items the scheduler will consume for this tag
                   over the next 14 days, from due_at across the FSRS queue
stock            = unseen items in bank for this tag at the relevant tier
deficit          = ceil(projected_demand * SAFETY_FACTOR) - stock
generate if deficit > MIN_BATCH_THRESHOLD
```

### Tests

```python
def test_deficit_zero_when_stock_exceeds_projected_demand()

def test_deficit_accounts_for_difficulty_tier_separately()
    # 80 tier-1 items do not satisfy demand for a topic now at tier 3

@pytest.mark.simulation
def test_no_tag_runs_dry_over_ninety_simulated_days()
    # run the scheduler and the nightly job together against a synthetic
    # learner. Assert the scheduler never fails to fill a round
    # for lack of stock. This is the test that validates SAFETY_FACTOR.

def test_nightly_item_cap_enforced()
    # a deficit calculation returning 10,000 generates at most CAP items

def test_monthly_spend_ceiling_blocks_generation()
    # cost_log at ceiling -> zero LLM calls, graceful log, exit 0.
    # assert the workflow does not fail the build: a budget stop is
    # normal operation, not an error.

def test_degradation_serves_least_recently_seen_items_when_capped()

def test_submit_workflow_is_idempotent_across_reruns()
    # rerunning the same day does not submit a second batch

def test_ingest_on_pending_batch_exits_zero_without_partial_insert()

def test_ingest_on_failed_batch_logs_and_does_not_clear_batch_id()

def test_all_generation_calls_use_the_batch_endpoint()
    # AST scan of the workflow entry points

def test_workflow_yaml_parses_and_has_no_continue_on_error()

def test_secrets_are_read_from_environment_not_committed_files()

@pytest.mark.live
def test_full_nightly_cycle_small()
    # 20-item deficit, real batch, submit and ingest, nightly on main only
```

The ninety-day joint simulation of scheduler plus top-up is the test that actually validates the economics. Everything else here checks a guard rail.

### Definition of Done

- [ ] Both workflows scheduled and verified on real runs
- [ ] Spend ceiling triggered deliberately in a test run and degradation observed
- [ ] Joint 90-day simulation shows no tag running dry
- [ ] Cost per night logged and within budget

---

## Stage 11: Live LLM features

**Branch:** `stage/11-live-features`

### Goal

The three things the recurring budget buys, in priority order.

Models: `gemini-3.5-flash-lite` for every live call (explanations, production grading, report narrative), thinking off. `gemini-3.7-flash` with low or medium thinking for override verification, which is asynchronous.

### 11a. Production grading

Rubric-based, three dimensions: grammatical accuracy, naturalness, and whether the target structure was actually used. Returns a verdict plus a short correction.

**Only the target-structure dimension feeds FSRS.** A translation tagged `konjunktiv_ii` can fail on word order, and that failure must not land on the Konjunktiv II schedule. Accuracy and naturalness are shown to the learner and ignored by the scheduler. Production is where multi-dimensional attribution actually bites; paragraph cloze already solved its version with gap-level tagging.

```python
@pytest.mark.golden
def test_production_grading_agreement_with_expert_labels()
    # data/fixtures/production/labelled_answers.jsonl: 30 learner sentences,
    # graded [AGENT-CRITICAL] by two independent agents; assert
    # agreement >= 0.85 on the pass/fail verdict. NO external key
    # exists for free-form German production, so this is the one
    # metric with no independent anchor. Report it as such.
    # Below that, the feature is doing harm: it is telling a learner
    # their correct German is wrong.

def test_target_structure_detection_independent_of_overall_correctness()
    # a sentence that is grammatical but avoids the target structure
    # must be flagged, not passed

def test_only_target_structure_dimension_reaches_fsrs()
    # a response using Konjunktiv II correctly but with wrong word order
    # must rate the konjunktiv_ii tag as a pass. Accuracy and naturalness
    # are feedback only.

def test_accuracy_and_naturalness_are_surfaced_but_not_scheduled()

def test_grading_failure_degrades_to_self_assessment()
    # LLM unavailable -> show the model answer and let the user
    # self-grade. Never block the round.
```

### 11b. Error-specific explanations

Generated on request only, after an error only, cached by `(item_id, user_answer)`.

```python
def test_explanation_requested_only_on_user_action()
    # not auto-fired on every wrong answer; that multiplies cost
    # by the error rate

def test_explanation_references_the_actual_wrong_answer()
    # golden set: assert the returned text mentions the user's answer,
    # not just the correct one. A generic rule restatement is
    # the failure mode and it is worth nothing.

def test_cache_hit_rate_measured_and_logged()
```

### 11c. Interference-targeted minimal pairs

When the error log shows systematic confusion between two topics in the same `confusion_group`, generate near-identical sentence pairs contrasting exactly those two.

```
Ich stelle die Vase auf ___ Tisch.   (Akkusativ, movement)
Die Vase steht auf ___ Tisch.        (Dativ, position)
```

```python
def test_confusion_detected_only_above_significance_threshold()
    # three errors is noise; do not spend budget on noise.
    # assert a minimum sample size and error-rate delta before triggering.

def test_minimal_pair_members_differ_only_in_the_target_feature()
    # spaCy diff: the two prompts must be structurally parallel
    # and differ in the contrasted feature. A "pair" that differs
    # in three ways teaches nothing.

def test_both_pair_members_pass_the_full_verification_chain()

def test_pair_members_are_scheduled_adjacently()
    # this is the one deliberate exception to the interleaving rule,
    # and it must be explicit rather than accidental

def test_minimal_pair_generation_respects_the_spend_ceiling()

def test_ceiling_or_offline_falls_back_to_preseeded_contrasting_items()
    # pull two bank items sharing a confusion_group instead of
    # generating. Weaker than a targeted pair, far better than nothing.

def test_fallback_pair_members_share_a_confusion_group()

def test_confusion_group_tag_written_at_ingest_not_derived_at_runtime()
    # the fallback must work fully offline
```

The adjacency exception matters: everywhere else in the system, consecutive items must differ in `tag_id`. Minimal pairs are the one case where adjacency is the point. Implement it as an explicit flag on the round assembler, not as a special case buried in the scheduler.

### Definition of Done

- [ ] Production grading agreement at or above 0.85 against hand labels
- [ ] Explanations verifiably reference the specific wrong answer
- [ ] Minimal pairs pass structural parallelism checks
- [ ] All three degrade gracefully when the budget ceiling is hit
- [ ] Minimal-pair fallback verified offline against bank-only data
- [ ] Production grading proven to attribute only the target-structure dimension
