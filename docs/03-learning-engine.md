# 03: Learning Engine (Stages 6 to 7)

> **STALE as of 2026-09-08.** This describes the German grammar trainer, which was shut down and pruned on `feat/phrase-deck`. Kept for the reasoning only. The current project is the phrase trainer; read `docs/project-state.md`.
>
> **Design record, written before most of the code existed. Not a description
> of what the code does now.**
>
> The corpus pipeline that makes every exercise today, and the English
> translation shown under every one, are absent from these contracts entirely:
> neither "blank" nor "Azure" appears in this document. `docs/project-state.md`
> has both.
>
> Named departure: it names the module `src/scheduler/`, and the module is
> `src/engine/`. Its Definition-of-Done boxes are unticked because nobody ticked
> them, not because the work is undone.

---

## Stage 6: Scheduler and Kalibrierung

**Branch:** `stage/06-scheduler`

### Goal

Given `tag_state`, produce a round. Given a new user, produce `tag_state`. Both entirely in code, no LLM.

### Contract

```python
class TagState(BaseModel):
    tag_id: str
    dimension: Dimension
    state: TagState_
    acquired_via: AcquiredVia
    fsrs_stability: float
    fsrs_difficulty: float
    due_at: datetime
    attempts: int
    correct: int
    consecutive_failures: int
    consecutive_unhinted_passes: int
    facets_seen_in_streak: set[str]
    introduced_at: datetime | None

class Scheduler(Protocol):
    def build_round(self, states: list[TagState], size: int,
                    now: datetime) -> Round: ...
    def day_budget(self, now: datetime) -> DayBudget: ...
    def forecast(self, states: list[TagState], now: datetime,
                 horizon_days: int) -> list[int]: ...
    def build_duel(self, confusion_group: str, now: datetime) -> list[BankItem]: ...
    def build_recalibration(self, states: list[TagState],
                            now: datetime) -> list[BankItem]: ...

class DayBudget(BaseModel):
    due_count: int          # the obligation: tags due before end of day
    ceiling: int            # where diminishing returns begin, derived not fixed
    new_topics_allowed: int # 0 when the forecast is over threshold
    forecast: list[int]     # projected review count per day, next 7 days
    def record(self, item: BankItem, correct: bool,
               now: datetime) -> TagState: ...

class Kalibrierung(Protocol):
    def next_item(self, answered: list[Answer]) -> BankItem | None: ...
    def finalise(self, answered: list[Answer]) -> list[TagState]: ...
```

`now` is always injected. No module in `src/scheduler/` calls `datetime.now()`.

### Round, day, queue

| Unit | Size | Governs |
|---|---|---|
| **Round** | 6 items, range 5 to 8 | every density and pacing rule |
| **Day** | a budget | new-topic introductions, review load |
| **Queue** | FSRS schedule | long-term intervals |

A sitting is however many rounds the user wants and has no rules of its own. The word "session" does not appear in this codebase.

New-topic introductions are a **daily** budget. Capping them per round means ten rounds introduces twenty topics in one evening, which is the opposite of spaced.

### Round assembly rules

1. Tags where `due_at <= now`, ordered by overdue ratio
2. Filter to tags whose prerequisites all have `stability >= MIN_PREREQ_STABILITY`
3. Prefer same-`confusion_group` neighbours when several tags are due
4. Retrieve items at the difficulty tier matching current stability
5. Exclude anything in `seen_items`
6. **Interleave: no two consecutive items share a `tag_id`**
7. New-topic introductions drawn from the DAY budget, not per round
8. Grammar to vocab ratio from the UI slider, default 70/30, 100/0 permitted
9. At most one HEAVY element per round: a paragraph block OR a
   production item, never both
10. Paragraph gaps count individually toward round size
11. When several `requires_context` topics come due together,
    spread them across days rather than stuffing one round
12. Past the due queue, offer bonus rounds from least-stable topics,
    rated and logged normally and marked as bonus

### Eligibility beats pacing

There is no global item-type quota. `topic.eligible_types` is a hard correctness constraint; the per-round caps above are soft pacing constraints. On conflict, eligibility wins and pacing is recovered by smoothing across days.

A paragraph block is a **carrier for a mix**: one `requires_context` gap alongside three ordinary ones. Interleaving therefore holds inside the block, since consecutive gaps carry different `tag_id`s, and one paragraph read yields four or five SRS signals instead of one.

### Daily load

`due_count` is the obligation. `ceiling` is where extra practice starts returning less per minute, derived as `due_count + new_topics_allowed * BLOCKED_SET_SIZE + bonus_allowance`. Never a constant.

**The forecast gates introductions.** If any of the next `FORECAST_HORIZON_DAYS` projects more than `FORECAST_LOAD_THRESHOLD` reviews, `new_topics_allowed` is 0 today. Each introduction schedules reviews at roughly 1, 3, 7 and 16 days, so unchecked introductions compound into a review-debt spiral, which is the most common reason people abandon spaced repetition.

**The threshold is configurable and suggested, not fixed.**

```python
class ThresholdSuggestion(BaseModel):
    window_days: int                   # 7, 14 or 30
    active_days: int
    median_items_per_active_day: float
    active_day_rate: float             # active days / calendar days in window
    clear_rate: float                  # active days queue cleared / active days
    suggested: int | None              # None below SUGGESTION_MIN_ACTIVE_DAYS
```

```
suggested = clamp(
    median_items_per_active_day * active_day_rate * clear_adjustment,
    *THRESHOLD_CLAMP)

clear_adjustment = 1.1 if clear_rate >= 0.85
                   1.0 if clear_rate >= 0.60
                   0.8 otherwise
```

Two corrections encoded here:

- Items completed per day is **censored by supply**. Clearing a 20-item queue proves capacity of at least 20. Only days that ran into bonus rounds are uncensored.
- The **active-day rate scales the threshold down**, because the forecast is per calendar day while skipped days concentrate load into the sittings that happen.

**Backlog cap.** Never present more than `MAX_REVIEWS_PER_DAY`. Order by overdue-ness and let the remainder slip. FSRS copes with lateness; the person does not.

**Nothing is ever blocked.** The user may keep going past the ceiling indefinitely. Notices appear at round boundaries only, phrased as information rather than instruction.

### Promotion: learning to acquired

```
promote if dimension == "grammar" and
   consecutive_unhinted_passes >= PROMOTION_CONSECUTIVE_PASSES and
   len(facets_seen_in_streak) >= PROMOTION_MIN_DISTINCT_FACETS
```

A grammar topic is not a word. `dativ_nach_praeposition` spans four genders, two article types and nine prepositions; one pass tests one cell and may only prove a lucky two-way case guess.

Consecutive means no failure in between, not that items appeared back to back; under interleaving they never will. Any failure, and any pass at hint level 1 or above, resets both counters.

**This rule touches only our own transition. FSRS stability updates are untouched.** FSRS has no stages, and gating its continuous updates behind a hand-written heuristic would override an algorithm fitted on very large datasets.

**Vocabulary is exempt.** A lemma is atomic, so the evidence argument does not reach it. Vocab promotes on stability alone.

**Degradation for unfaceted topics.** A topic with an empty `morph_spec` has no facet space; the facet clause is skipped and three consecutive unhinted passes suffice. An explicit branch, never an accidental pass on an empty set.

**Scheduler preference.** When selecting an item for a topic in `learning`, prefer a facet not yet seen in the current streak. Accelerates legitimate promotion and improves diagnostic coverage at no cost.

### Facet diagnostics and split candidates

Per-facet accuracy is derivable from `review_log` joined to `item.facet`. No new table.

A topic is flagged a **split candidate** when every facet has at least `SPLIT_MIN_ATTEMPTS_PER_FACET` attempts and the accuracy spread across facets exceeds `SPLIT_ACCURACY_GAP`. That is evidence the topic holds two independent memories rather than one rule, which is the case where full atomisation would actually have been correct.

**Splitting is proposed, never automatic.** Topic IDs are referenced by user history and by every generated item; a silent rename orphans both. The output is a report.

### Split procedure

Triggered manually: `grammar split <topic_id> --axis Gender [--dry-run]`.

Dry run prints the resulting DAG, item counts per child, history rows reassigned, and the projected daily-load delta. The load cost is real: each split adds a review unit.

**1. Choose the axis from the data, not by judgement.** The split axis is the facet dimension whose accuracy diverged. Nothing is invented.

**2. One axis, minimum partition.** Two children where possible. Never the full cross-product: an eight-way split reintroduces the over-review problem the trigger condition exists to prevent. A child may be split again later on its own facet evidence.

**3. Taxonomy changes.**

```
parent P (prereqs A,B; dependents X,Y) split on Gender -> P1, P2

P.split_into        = [P1, P2]      # P becomes a lineage node
P.split_axis        = "Gender"      # never scheduled, never generated for
P1.prereqs = P2.prereqs = [A, B]    # children inherit the full foundation
P1.cefr = P2.cefr = P.cefr
P1.confusion_group  = P.confusion_group
P1.derived_from = P2.derived_from = P
P1.sibling_group = P2.sibling_group = new group id
P1.morph_spec = P.morph_spec | {"Gender": "Masc"}
X.prereqs, Y.prereqs: P replaced by [P1, P2]   # all children required
```

Rerun the acyclicity check.

**4. Interleaving strengthens.** The invariant becomes: no two consecutive items share a `tag_id` **or** a `sibling_group`.

Without this, two cells of a formerly single topic carry different tags, so every existing test passes while the learner experiences the same thing twice in a row. The thesis erodes silently, which is the failure mode that made full atomisation unacceptable.

**Sibling separation outranks confusion adjacency.** A `confusion_group` means "adjacent, the contrast teaches." A `sibling_group` means "never adjacent, one rule at two cells, adjacency is massing." On conflict, separation wins.

**5. History migrates deterministically.** Every item carries a `facet` computed at ingest and the split axis is a facet dimension, so each historical review reassigns to the child its item's facet identifies. Child `tag_state` is recomputed from the reassigned log. No estimation. This works only because `review_log` is append-only and facets were recorded at ingest.

Rows whose item facet does not determine the axis stay on the lineage node and are excluded from child seeding. Explicit, never guessed.

**6. Bank migrates the same way.** Items retag to a child by facet; no regeneration. Undeterminable items are quarantined for review. Stock will be uneven, which is expected, since an under-practised facet is often why the split was flagged. Nightly top-up corrects it.

**7. Reversible.** The lineage node is retained and log rows carry item facets, so a merge is a re-tag back to the parent.

### Modes outside the scheduler

`review_log` gains a `mode` field. `tag_state` recomputation filters to `mode in ("review", "recalibration")`. Duel and challenge rows are practice and never reach FSRS.

| Mode | Feeds FSRS | Topic may be named |
|---|---|---|
| `review` | Yes | Never |
| `duel` | No | Yes, the group is chosen by name |
| `challenge` | No | Yes |
| `recalibration` | Yes | Never |

#### Duels

6 to 8 back-to-back items from the two contrasting topics of a confusion group, minimal pairs where the bank holds them. Available for **every** confusion group on demand.

This is blocked practice, which the app otherwise exists to avoid. The exception holds because contrastive discrimination between two confusable categories is a different intervention from drilling one topic, and because keeping duels out of FSRS means a blocked result can never inflate a scheduled interval.

**Duels prefer already-seen items.** Unseen items are the measurement pool and must not be burned by practice. Duel exposure tracks in a separate `duel_seen` set, and duel demand does not enter Stage C generation deficits.

**Suggestion ranking uses the empirical confusion rate**, not raw accuracy: how often an error on topic A produced an answer valid under sibling B, from `implied_topic_id`. Low accuracy alone may just mean a hard topic. Suppressed below `DUEL_MIN_ATTEMPTS_TO_SUGGEST`.

#### Daily challenge

One free-writing prompt per day. Topic selected deterministically in code from `learning` or recently acquired topics. **The prompt may name the grammar point**, uniquely in the system, because the challenge sits outside the interleaved retrieval loop: the prohibition protects rule selection under uncertainty, and here the rule has been selected for you.

#### Recalibration after dormancy

After `DORMANCY_DAYS` of inactivity, the backlog is not presented. A recalibration round of `RECALIBRATION_ROUND_SIZE` items samples mostly from the highest-stability topics, which are the ones most likely to have decayed unnoticed, plus a couple from `learning`. Results rebuild the queue.

**The overdue count is never shown on return.** Recalibration items feed FSRS normally, being genuine reviews merely reordered.

#### Lapse records

Every demotion from `acquired` writes a lapse record: topic, failing facet, prior stability, streak length, hint level, days since last review, and the implied topic of the wrong answer.

Repeated lapses on one facet are the strongest available split evidence, so lapse records feed the split detector directly.

### Introduction cards

A topic entering `learning` from `unseen` shows its static `intro_card` before the first item of its blocked set. Cards are also shown on demotion from `acquired`, and on demand from stats.

`unseen` → intro card → blocked set of 6 to 8 items → `learning`

Topics arriving as `acquired` from the Kalibrierung skip the card but can pull it up on demand.

**Cards never appear during interleaved review.** Showing one there names the topic of whatever comes next and destroys the interleaving effect.

### Hints

| Level | Shown | FSRS rating if then correct | Cost |
|---|---|---|---|
| 0 | Nothing | good, or easy if fast | free |
| 1 | Word count, first letter | hard | free |
| 2 | Three options | hard | free, distractors precomputed at ingest |
| 3 | `topic.rule_hint` | **again** | free, static text |
| 4 | Answer revealed | **again** | free |

**No partial credit reaches FSRS.** Ratings set interval length; partial credit inflates stability, so a topic answerable only with help returns too late and the dependency never surfaces. Needing the rule stated means the rule was not retrieved.

Hint dependency is a separate signal: promotion from `learning` to `acquired` requires consecutive **unhinted** passes spanning distinct facets (see below), and hint dependency is surfaced in stats in its own right.

### Kalibrierung rules

| Result | State | Initial stability | `acquired_via` |
|---|---|---|---|
| 2/2 correct | acquired | **~10 days** | kalibrierung |
| 1/2 correct | learning | ~7 days | null |
| 0/2 correct | unseen | none | null |
| Inferred from DAG | acquired | **4 days, hard ceiling** | inferred |

Demotion: `acquired_via == "inferred"` demotes to `learning` on the **first** failure. `acquired_via == "kalibrierung"` or `"earned"` demotes on the **second consecutive** failure.

**The 4-day ceiling on inferred topics is not tunable.** Adult L2 competence is fragmented in ways the DAG cannot see: a learner may build relative clauses from memorised frames while failing article declension, so passing a dependent topic is weak evidence about its prerequisites. A long inferred stability produces demotion loops, where the topic surfaces late, fails, demotes and is reintroduced repeatedly.

No `inferred_acquired` state is added. `acquired_via` already carries the distinction and a fourth state would branch every scheduler path for no information gain. There is no special promotion rule either: FSRS runs normally from the short stability.

Termination: start around B1, propagate passes downward through `transitive_prereqs`, treat failures as suspicion over `descendants`, branch on two consecutive outcomes, stop when every topic is directly tested or inferred.

### Tests

This stage is tested primarily by simulation. Unit tests alone will not catch a scheduler that starves a topic on day 40.

```python
# --- Invariants, asserted on every generated round ---

def test_no_two_consecutive_items_share_a_tag_id()
    # property test over random tag_state configurations.
    # This is the product thesis expressed as an assertion.

def test_no_two_consecutive_items_share_a_sibling_group()
    # children of a split carry different tag_ids but feel identical
    # to the learner. Without this, interleaving erodes while every
    # other test stays green.

def test_sibling_separation_outranks_confusion_adjacency()
    # confusion pairs are different rules that look alike (adjacency
    # teaches); siblings are one rule at two cells (adjacency is massing)

def test_never_schedules_a_topic_with_unacquired_prerequisites()
    # property test across the real taxonomy DAG

# --- Round and day ---

def test_round_size_within_configured_bounds()
    # UI setting, 4 to 10, default 6

def test_vocab_ratio_read_from_setting_and_applied_next_round()

def test_vocab_ratio_of_zero_produces_grammar_only_rounds()
    # the slider must reach 100/0 cleanly, not asymptotically

def test_at_most_one_heavy_element_per_round()
    # a paragraph block and a production item must never share a round

def test_paragraph_gaps_count_individually_toward_round_size()
    # a 4-gap block plus 3 singles is a 7-item round, not a 4-item one

def test_due_count_equals_tags_due_before_end_of_day()

def test_ceiling_is_derived_not_constant()
    # property test: ceiling must move with due_count.
    # a fixed number is wrong on most days.

def test_forecast_suppresses_new_topics_when_any_day_over_threshold()
    # the guardrail that prevents the review-debt spiral

def test_forecast_covers_the_full_horizon_not_just_tomorrow()

def test_forecast_threshold_is_read_from_config_not_constant()

# --- Threshold suggestion ---

def test_suggestion_is_none_below_minimum_active_days()
    # a number derived from three data points is worse than no number

def test_suggestion_scales_inversely_with_active_day_rate()
    # property test: same median items, fewer active days -> lower threshold.
    # skipped days concentrate load into the sittings that happen.

def test_suggestion_clamped_to_range()
    # two active days at three items each must not suggest 2, which would
    # suppress new topics permanently and stall progress silently

def test_zero_activity_days_excluded_from_median_but_counted_in_rate()
    # absence is not low capacity, but it does reduce the active-day rate

def test_clear_rate_adjustment_table()
    # table-driven over the three bands

def test_suggestion_is_deterministic_for_a_given_history()

def test_suggestion_never_auto_applies()
    # assert no code path writes the setting without an explicit user action

def test_all_three_windows_supported_with_default_fourteen()

def test_threshold_change_applies_forward_only()
    # past forecasts are not recomputed

def test_backlog_capped_and_ordered_by_overdueness()
    # 200 overdue items must present as MAX_REVIEWS_PER_DAY,
    # most overdue first, remainder slipping

def test_user_is_never_blocked_from_further_rounds()
    # past the ceiling the scheduler must still return rounds.
    # locking the user out is paternalistic and gets routed around.

def test_notices_fire_only_at_round_boundaries()
    # never mid-round

def test_new_topic_budget_is_daily_not_per_round()
    # ten rounds in one day must still introduce at most MAX_NEW_TOPICS_PER_DAY.
    # This is the bug the round/day split exists to prevent.

def test_bonus_rounds_offered_only_after_due_queue_is_empty()

def test_bonus_rounds_never_pull_forward_scheduled_items()
    # early review yields less stability gain and corrupts intervals

def test_bonus_rounds_are_flagged_in_review_log()

def test_the_word_session_appears_nowhere_in_scheduler_source()
    # source scan. The term collapsed three time scales and every
    # density rule written against it was meaningless.

# --- Introduction cards ---

def test_intro_card_shown_before_first_item_of_a_blocked_set()

def test_intro_card_never_shown_during_interleaved_review()
    # showing one names the topic of the next item and destroys
    # the entire premise of the app

def test_intro_card_reshown_on_demotion_from_acquired()

def test_kalibrierung_acquired_topics_skip_the_intro_card()

def test_intro_card_is_read_from_taxonomy_not_generated()
    # assert no LLM call path is reachable from the card renderer


def test_seen_items_never_reappear()

def test_difficulty_tier_matches_stability_band()

def test_production_count_within_round_bounds_and_not_clustered()
    # no two production items adjacent

# --- Eligibility and paragraph blocks ---

def test_item_type_never_violates_topic_eligibility()
    # property test across the taxonomy. A cloze_free item for a
    # requires_context topic must never be schedulable, no matter
    # what the pacing caps say.

def test_eligibility_overrides_pacing_caps_on_conflict()
    # when the only due topic needs a paragraph block and the cap
    # is already met, the block is scheduled anyway

def test_context_requiring_topics_are_spread_across_days()
    # four such topics due together must not all land in one round

def test_paragraph_block_gaps_carry_distinct_tag_ids()
    # the interleaving invariant holds inside the block too

def test_paragraph_block_gaps_are_independent_srs_events()
    # failing gap 2 must not affect the tag_state of gaps 1, 3 and 4

def test_no_global_item_type_quota_exists_in_the_scheduler()
    # AST or config scan. The observed distribution is emergent;
    # a hardcoded percentage would silently override eligibility.

# --- Hints ---

def test_hint_level_maps_to_fsrs_rating()
    # table-driven over all five levels

def test_hint_level_three_or_four_always_rates_again()
    # explicit: no configuration may soften this

def test_no_partial_credit_path_reaches_fsrs()
    # assert record() accepts only the four FSRS ratings,
    # never a fractional score

def test_hints_are_gated_until_delay_or_first_wrong_attempt()
    # injected clock; an instantly available button gets tapped
    # reflexively and destroys the retrieval effort

# --- Modes ---

def test_duel_and_challenge_rows_never_reach_tag_state()
    # the single assertion that keeps blocked practice safe

def test_recalibration_rows_do_feed_fsrs()
    # genuine reviews, merely reordered

def test_duel_prefers_seen_items_and_falls_back_only_when_short()
    # unseen items are the measurement pool

def test_duel_exposure_tracked_separately_from_seen_items()

def test_duel_demand_does_not_enter_generation_deficits()

def test_duel_available_for_every_confusion_group()

def test_duel_locked_until_both_member_topics_introduced()
    # visible but locked, with the reason shown

def test_duel_ranking_uses_implied_topic_confusion_rate_not_accuracy()
    # low accuracy may mean a hard topic; a high A-answered-as-B rate
    # is direct evidence of the confusion a duel fixes

def test_duel_suggestion_suppressed_below_minimum_attempts()

def test_challenge_topic_selected_deterministically_in_code()

def test_challenge_prompt_may_name_the_topic()
    # the one place in the system where this is allowed

def test_challenge_never_appears_inside_a_round()

# --- Recalibration ---

def test_recalibration_triggers_only_after_dormancy_threshold()

def test_recalibration_samples_mostly_high_stability_topics()

def test_overdue_count_never_surfaced_on_return_from_dormancy()

def test_recalibration_rebuilds_the_queue_from_its_results()

# --- Lapses ---

def test_every_demotion_writes_a_lapse_record()

def test_lapse_record_captures_the_failing_facet()

def test_repeated_single_facet_lapses_raise_a_split_candidate()

# --- Wrong-answer analysis ---

def test_wrong_answer_matching_a_distractor_resolves_offline()
    # no network, no parser; this is why implied_topic_id is
    # precomputed at ingest

def test_free_text_wrong_answer_logged_unclassified_for_server_analysis()

def test_confusion_matrix_built_from_intended_versus_implied_topic()

def test_no_error_item_edited_by_user_logged_as_over_application()
    # changing a correct sentence is its own signal, not a plain miss

# --- Promotion ---

def test_promotion_requires_three_consecutive_unhinted_passes()

def test_promotion_requires_two_distinct_facets()
    # three passes all on masculine definite article prove one cell
    # three times, which is the failure this rule exists to prevent

def test_non_adjacent_passes_still_count_as_consecutive()
    # under interleaving the three items never appear back to back

def test_any_failure_resets_both_counters()

def test_any_hinted_pass_resets_both_counters()

def test_unfaceted_topic_promotes_on_three_passes_without_facet_clause()
    # explicit branch, not an accidental pass on an empty set

def test_vocab_promotes_on_stability_without_the_facet_rule()

def test_promotion_rule_does_not_alter_fsrs_stability_updates()
    # the rule governs our transition only; FSRS maths is untouched

def test_scheduler_prefers_an_unseen_facet_for_learning_topics()

@pytest.mark.simulation
def test_promotion_rule_does_not_stall_acquisition_over_ninety_days()
    # slower acquisition is expected and fine; a topic that never
    # reaches acquired because the bank lacks facet variety is not

# --- Split candidates ---

def test_split_candidate_requires_minimum_attempts_per_facet()
    # a 40-point gap on three attempts is noise

def test_split_candidate_is_reported_never_applied()

# --- Split execution ---

def test_split_reassigns_every_history_row_by_item_facet()
    # deterministic, no estimation

def test_split_recomputes_child_tag_state_from_reassigned_log()

def test_rows_with_undeterminable_facet_stay_on_the_lineage_node()
    # explicit, never guessed into a child

def test_split_retags_bank_items_without_regeneration()

def test_split_quarantines_items_whose_facet_misses_the_axis()

def test_split_is_reversible_by_retagging_to_the_parent()

def test_dry_run_reports_dag_item_counts_history_rows_and_load_delta()

def test_split_never_renames_or_deletes_the_parent()
    # every generated item and history row references the id

def test_split_rejects_a_cross_product_partition()
    # one axis at a time; an eight-way split reintroduces the
    # over-review problem the trigger condition guards against
    # topic ids are referenced by user history; silent renames orphan it

def test_facet_accuracy_derived_from_review_log_without_a_new_table()

def test_promotion_to_acquired_requires_consecutive_unhinted_passes()

def test_hint_dependency_tracked_separately_from_accuracy()

def test_level_two_distractors_are_precomputed_not_generated_at_runtime()
    # hints must never sit on the critical path or cost money

def test_round_degrades_gracefully_when_bank_stock_is_short()
    # returns a shorter round; never returns a seen item,
    # never returns an item from an unacquired-prereq topic

# --- FSRS behaviour ---

def test_kalibrierung_result_to_initial_state_mapping()
    # table-driven over all four rows above

def test_inferred_acquired_demotes_after_one_failure()

def test_kalibrierung_acquired_demotes_after_two_consecutive_failures()

def test_non_consecutive_failures_do_not_demote()
    # fail, pass, fail must not demote a kalibrierung-acquired topic

def test_scheduler_never_calls_datetime_now()
    # AST scan of src/scheduler/

# --- Kalibrierung ---

@pytest.mark.simulation
def test_kalibrierung_terminates_within_35_items_for_all_ability_profiles()
    # hypothesis: generate 200 random true-ability vectors over the taxonomy,
    # simulate an honest answerer, assert termination and item count bound

def test_dag_inference_marks_exactly_transitive_prereqs()
    # passing a topic marks its transitive prereqs acquired and nothing else.
    # over-marking is the dangerous direction: it hides real gaps.

def test_failure_does_not_mark_descendants_acquired()

@pytest.mark.simulation
def test_kalibrierung_accuracy_against_known_ability()
    # synthetic learner with known true acquired set.
    # assert precision >= 0.85 on topics marked acquired.
    # precision matters more than recall: wrongly marking acquired
    # skips instruction the learner needs.

# --- Long-run simulation ---

@pytest.mark.simulation
def test_ninety_day_run_converges_to_true_ability()
    # synthetic learner, fixed seed, 90 simulated days.
    # assert: every topic in the true-acquired set ends acquired,
    # every topic outside it has been introduced

def test_no_topic_starves_over_ninety_days()
    # max gap between reviews of any non-acquired topic bounded.
    # a scheduler that quietly abandons a topic passes every unit test.

def test_review_load_per_day_stays_within_bounds()
    # assert no day requires more than 2x the target round count;
    # FSRS pile-ups are the classic SRS failure and they cause quitting

def test_collapsed_acquired_topic_recovers_within_two_weeks()
    # simulate a topic the learner has actually forgotten;
    # assert demotion, reintroduction, and stability recovery

@pytest.mark.golden
def test_fsrs_parity_vectors()
    # data/fixtures/fsrs/parity_vectors.json:
    # (stability, difficulty, rating) -> next interval.
    # The same fixture is asserted in the web test suite against ts-fsrs.
    # Without this, py-fsrs and ts-fsrs drift and the CLI and PWA
    # schedule the same user differently.
```

The FSRS parity fixture is the single most important test in this stage. Two implementations of the same algorithm in two languages, both authoritative, is a data-corruption bug waiting to happen.

### Definition of Done

- [ ] All invariants asserted as property tests, not examples
- [ ] Eligibility proven to override pacing, with paragraph blocks spread across days
- [ ] Hint ladder implemented with no fractional-score path into FSRS
- [ ] Round / day split enforced, with the new-topic budget proven daily
- [ ] Forecast gating verified against a simulated introduction spike
- [ ] Threshold suggestion validated on synthetic histories across all three windows
- [ ] Backlog cap verified with a 3-day absence
- [ ] Intro cards wired into the blocked introduction and blocked from review
- [ ] 90-day simulation passes with no starvation and bounded daily load
- [ ] Kalibrierung precision on synthetic learners at or above 0.85
- [ ] Inferred stability ceiling enforced, with no demotion loop in the 90-day simulation
- [ ] Facet-diverse promotion enforced, with the 90-day simulation showing no stalled topics
- [ ] Split executed end to end on one topic in a test fixture, with history preserved
- [ ] Duel and challenge rows proven inert against tag_state
- [ ] Recalibration verified against a simulated 30-day absence
- [ ] Override path appends events and never mutates the log
- [ ] Parity vectors committed and consumed by both language test suites

---

## Stage 7: CLI and grading (USABILITY GATE)

**Branch:** `stage/07-cli`

### Goal

A usable terminal client. You will run daily rounds on this for two weeks before any frontend work begins.

### Deliverables

- `grammar kalibrierung`: run the initial assessment
- `grammar round [--size N]`: run one round of 5-8 items
- `grammar stats`: topic states, stability, retention
- `grammar report <item_id>`: flag a broken item
- Grading module, shared by CLI and later ported to the PWA

### Grading rules

These are the rules most likely to be implemented wrongly, so they are specified exactly.

1. **Case-sensitive.** Capitalisation is grammatically meaningful in German.
2. **Typo tolerance is scoped.** Edit distance of 1 permitted in the portion of the answer outside the tested morpheme. The tested morpheme is exact.
3. **Umlaut transliteration accepted:** `ae`/`oe`/`ue`/`ss` equal `ä`/`ö`/`ü`/`ß`.
4. **Whitespace trimmed**, internal whitespace normalised.
5. **Graded against `accepted_answers`**, any match passes.
6. **Overrides are appended, never edited.** "I was right" writes a new `override` event to `review_log`; `tag_state` is recomputed from the log. The append-only invariant holds, so sync stays a union with no conflict resolution.
7. **Grading returns a verdict plus the hint level used.** The rating mapping lives in the scheduler, not the grader; the grader never emits a score.

Paragraph cloze grades gap by gap. A block is never scored as a whole.

### Tests

The grading table is the fixture. `data/fixtures/grading/grading_table.csv` with columns `topic_id, expected, given, verdict, reason`.

**Generated, not written.** Cases derive programmatically from `morph_spec`: mutate the target morpheme and expect fail, mutate outside it and expect pass, transliterate and expect pass. Property-based generation across the whole taxonomy beats any fixed list of rows for coverage.

The rows below are the cases a generator will not think of and are added explicitly **[AGENT-CRITICAL]**, because they encode exactly the rules a naive implementation gets wrong.

```python
@pytest.mark.golden
@pytest.mark.parametrize("row", load_grading_table())
def test_grading_table(row):
    assert grade(row.expected, row.given, row.topic_id) is row.verdict
```

Rows that must be present:

| expected | given | verdict | reason |
|---|---|---|---|
| `dem` | `dem` | pass | exact |
| `dem` | `den` | **fail** | wrong case, this is the tested morpheme |
| `dem Mann` | `dem Mnn` | pass | typo outside the tested morpheme |
| `dem Mann` | `den Mann` | **fail** | wrong case despite edit distance 1 |
| `dem Mann` | `dem mann` | **fail** | capitalisation is grammatical |
| `dem Mann` | `  dem Mann  ` | pass | whitespace trimmed |
| `größer` | `groesser` | pass | transliteration |
| `größer` | `grosser` | pass | transliteration |
| `größer` | `großer` | **fail** | different word, not a transliteration |
| `ist gefahren` | `ist gefahren` | pass | multi-token |
| `ist gefahren` | `hat gefahren` | **fail** | wrong auxiliary is the tested feature |
| `sie` | `Sie` | **fail** | formal vs third person, meaning-bearing |

The `großer` / `größer` row is the reason scoped typo tolerance exists. A naive edit-distance-1 rule marks it correct and silently destroys the comparative topic.

```python
def test_grading_is_pure_and_deterministic()

def test_grading_makes_no_network_call()

def test_multi_token_answers_normalise_internal_whitespace()

def test_typo_tolerance_never_crosses_the_tested_morpheme()
    # property test: for every topic with a morph_spec, mutate the
    # target morpheme by one character and assert failure

@pytest.mark.golden
def test_cli_round_transcript()
    # fixed seed, fixed bank, fixed clock -> byte-identical transcript.
    # catches accidental changes to round assembly.

def test_report_command_marks_item_and_excludes_it_from_future_rounds()

# --- Overrides ---

def test_override_appends_an_event_and_never_edits_a_row()
    # review_log must stay append-only or sync gains a conflict case

def test_override_recomputes_tag_state_from_the_log()

def test_override_takes_effect_immediately_and_offline()
    # the user is never argued with mid-round

def test_override_does_not_write_to_accepted_answers_locally()
    # global adoption happens only after Worker-side verification.
    # a learner convinced "den Tisch" is Dativ would otherwise poison
    # the bank and stop being taught the thing they are wrong about.

def test_upheld_and_rejected_override_rates_reported_separately()
    # upheld measures bank quality; rejected identifies a learner
    # misconception. Collapsing them loses both signals.

# --- Inferred stability ---

def test_inferred_initial_stability_never_exceeds_ceiling()
    # property test across all Kalibrierung outcomes

def test_no_inferred_acquired_state_exists()
    # the distinction lives in acquired_via; a fourth state would
    # branch every scheduler path for nothing

def test_inferred_topic_follows_normal_fsrs_after_first_pass()
    # no special promotion shortcut

def test_stats_never_shows_round_accuracy_as_headline()
    # deliberate: interleaving depresses round accuracy by design and
    # leading with it causes quitting. Assert stability and retention
    # are the primary output.

def test_cli_runs_fully_offline()
    # network disabled; kalibrierung, round, and stats all succeed
```

### Usability gate

Run daily rounds for two weeks. Record in `docs/audits/stage-07-usage.md`:

- Did any topic feel abandoned
- Did daily load spike
- Were any items wrong, and how many out of how many seen
- Did interleaving feel tolerable or punishing
- Did the Kalibrierung's acquired set match your honest self-assessment

**Do not start stage 8 until this document exists.** A frontend built on an unpleasant scheduler is wasted work, and this is the cheapest possible moment to find that out.

### Definition of Done

- [ ] Grading table at 80+ rows, all passing
- [ ] Transcript golden test committed
- [ ] Two weeks of real personal use logged
- [ ] Usage audit document committed with an explicit continue or revise decision
