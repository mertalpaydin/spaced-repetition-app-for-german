# TODO

Open work only. **An item is a thing that is not done.** When you close one,
delete it from here and write it up in `docs/audits/fix-log.md`.

- New to the project? Read `docs/project-state.md` first.
- Known limits that are accepted rather than fixed are **not work**. They are
  explained, with real examples, in `docs/known-defects.md`.
- Finished work is in `docs/audits/fix-log.md`, with what was measured.

Item ids are kept from the old numbering because code comments and other
documents cite them. They are labels, not an order. The order is top to bottom.

---

## 0b. Decide what to do about local models (2.8)

The measurement is **done** and is in `docs/audits/local-verifier-eval.md`.
What is left is a decision, and it is small.

**The finding.** No local model tested can replace the hosted verifier. Ranked
by false-positive rate, which is what decides affordability:

| Model | Recall (visible) | False positives |
|---|---:|---:|
| Gemini 3.7 Flash (baseline) | 75.0% | 12.9% |
| Gemma 4 12B Q3_K_S | 11.1% | 3.4% |
| Qwen3.5 9B IQ4_XS | 47.4% | 26.9% |
| Ministral 3 14B Instruct UD-IQ2_M | 60.0% | 51.7% |
| Granite 4.2 3B Q5_K_M | 69.2% | 53.3% |

Every model that catches a useful share of defects rejects a quarter to half of
all good candidates. Gemma is the exception and catches almost nothing. The
saving would be about $3.28 per bank build; the cost would be most of the
corpus's good candidates, on a bank whose scarce topics already struggle to
reach 25 items.

**Do.** Nothing, unless you disagree. The default is: keep the hosted verifier,
keep `src/llm/local_client.py` and `scripts/eval_local_verifier.py` so the next
cheap model release is a one-command question, and stop here.

**If you want one more datapoint,** the untested one is Ministral 3 14B
Reasoning, stopped after 3 calls because it needs about 2.8 hours for the
fixtures and 17.7 hours at best for a real bank build. It is disqualified on
throughput whatever its accuracy.

**Done when.** This item is deleted, or a follow-up is written saying what else
to try.

---

## 1. Decide the verification pass count (2.1c)

Do this first. It costs nothing and it changes the bank build.

**Do.** Re-read the `--verification-passes 2` line in
`docs/building-the-bank.md`. Either confirm it in writing or change it. To
get evidence, run a pilot with `--verification-passes 2
--verification-batch-size 20` and read `pass_disagreements` in the report.

**Why.** Two passes reject anything either pass rejects. That unions the
catches and the false positives together. One pass discards about 1 good
candidate in 8; two passes discard more, somewhere below double, and nobody
has measured where. Landing 1,225 items at one pass throws away about 181 good
candidates, and at two passes up to about 295. The topics that struggle to
reach 25 items at all are exactly the ones that pushes under the floor.

Against that: at one pass, about 2.5% of items are decided by which run you
happen to look at. Two passes may still be right. The point is that the choice
was made before the false-positive rate existed. See `docs/project-state.md`
for both measurements.

**Done when.** `docs/building-the-bank.md` states the pass count and the
number behind it.

---

## 2. Build the item bank (6.1)

The biggest single piece of open work. Item 1 is the only thing that goes
first, and it is a decision, not code. Nothing below is a reason to delay this.

**Do.** Follow `docs/building-the-bank.md`. Prerequisites, then four commands,
in order.

**Why.** There is no bank. Every downstream thing, the web bundle included, is
a demo until this runs.

**Watch out.** Two flags will quietly ruin the run if left at their defaults,
`--limit` and `--max-translation-characters`, and the run is silent for hours.
`docs/building-the-bank.md` says why for each. The cost is in the same file,
and it does not fit in what is left of the August ceiling.

**Done when.** `data/bank.db` holds about 1,225 items across 49 topics,
`web/data/` exports them with their English translations, and the run report
lists the per-topic counts.

**It also produces the input items 8 and 9 are waiting for:** the list of
topics the corpus cannot fill to 25.

---

## 3. Schedule the monthly translation job (2.6)

**Do.** One `schtasks /Create` line on the owner's machine, per
`docs/monthly-translation-job.md`. The script and its tests exist.

**Why.** Until that task exists, nothing runs and the corpus does not get
translated. Translating the whole corpus takes about 14 monthly runs, so every
month it is not scheduled is a month lost. Cheapest item on this list.

**Done when.** The task is in Windows Task Scheduler and one run has written
its month into `data/fixtures/translations/azure_f0_ledger.json`.

---

## 4. Fix the translation check's blindness to singular against plural (2.2c)

New, from the 2026-08-28 gloss eval. The eval itself is done; this is what it
found.

**Do.** Pick one and build it:
- Add a fifth question to the verification instruction, asking whether the
  English translation matches the German sentence. Today no question asks
  that, so any catch is incidental.
- Or add a deterministic check: compare the answer's number against the number
  of the matching English noun phrase. `en_core_web_sm` is already installed
  and `src/generation/gloss_validation.py` already uses it.

**Why.** The learner is shown the translation and uses it. A translation with
the wrong number points at the wrong answer. The measured recall on this kind
is 0 of 6. `docs/project-state.md` has the example.

**Done when.** `uv run python -m scripts.eval_gloss_adversarial` catches more
than 0 of the 6 `wrong_number` rows, and still reports 0 false positives on
the 36 correct ones.

---

## 5. Decide whether the gloss consistency check rejects (2.1b)

**Do.** Run a pilot, read the flag count, then set the default. The check runs
and reports today; `--enforce-gloss-check` makes it reject.

**Why.** It is wired but toothless. Every measurement of it so far was taken
against glosses that were mostly Tatoeba's, and Tatoeba glosses are no longer
used for exercises, so those numbers describe a pipeline that no longer runs.
Re-measure before deciding.

Two things to keep in mind. The check only bites on items whose answer carries
tense or person, so a wrong translation on an item that blanks a determiner
passes untouched. And the one real defect it has ever caught was caught by
luck. The signal that actually separates a mispaired translation from a merely
loose one is that it shares no proper noun or numeral with its German. **That
content-overlap check is a different check and is worth building.**

**Done when.** The default is set deliberately, with a flag count from a pilot
whose glosses are machine translations.

---

## 6. Run the gloss purge on the real store, then re-gloss (2.2b)

**Do.** `scripts/purge_mismatched_glosses.py --dry-run` first, read the
examples, then apply, then re-run `scripts/build_translations.py
--carriers-from` for the affected carriers.

**Why.** 7,365 Leipzig records in the owner's store are labelled
`source="tatoeba"` and are therefore joined to the wrong sentence. A Leipzig
sentence's English cannot come from Tatoeba. The cause is fixed and the tool
is written and tested, but neither has been run against the real store, which
exists only on the owner's machine.

This no longer blocks a pilot, because a stored Tatoeba record is now treated
as a cache miss and re-translated. It still matters for feature 12, which
reads the same store and would show a Leipzig sentence with an unrelated
English one.

**Done when.** A second `--dry-run` reports no mismatches.

---

## 7. Decide what to do about the defects the model verifier cannot see (2.7)

**Do.** The owner picks one of three. Do not start any of them before he does.

1. **Nothing.** Defensible. The rules ship, tests pin them, and the cost is
   that the next regression is found by hand, months later.
2. **A post-hoc check over finished items,** in `src/audit/bank_health.py`,
   which already walks every item and does have the `topic_id` the verifier is
   correctly denied. Cheapest entry: `cue[:1].isupper() != answer[:1].isupper()`.
   No spaCy, no model call.
3. **Re-run the selectors over accepted items and diff.** Strongest, most
   expensive, and it needs a decision about what a disagreement means.

**Why.** Fifteen defects the verifier missed fall into five families. All five
already have deterministic rules, so none reaches a learner today. But every
one of those rules runs at generation time. There is no check on a finished
item anywhere. If a selector regresses, the model verifier is all that is
left, and it will catch nothing, because four of the five families need the
topic and the verifier is never told the topic.

**Why after the bank build.** Option 2 changes code that runs over a bank, and
there is no bank. And the real question, whether a second layer earns its
keep, is better answered against 1,225 real items than against a 38-record
fixture.

`docs/known-defects.md` 2.10 to 2.14 has the five families with real examples.

**Done when.** The choice is written down here or in the fix log.

---

## 8. The AI-generation pilot (2.4)

Explicitly open. Not deferred, not folded into item 9.

**Do.** Two parts.
- Decide about `.github/workflows/generate-submit.yml` and
  `generate-ingest.yml`. They run this path nightly and spend real money the
  moment repository secrets exist. Nothing has ever measured what they
  produce. Turn them off, or measure them.
- Run a generation pilot for the topics the corpus cannot fill, and audit it
  the way the corpus pilots were audited.

**Why.** Some topics are too rare to reach 25 items even in the whole corpus.
`futur_i`, `zustandspassiv_zeiten` and `futur_ii` are the current suspects.
Item 2's run gives the real list.

**Done when.** A pilot has been run and hand-audited, and the two workflows
are either measured or disabled.

---

## 9. Write down the split (2.5)

**Do.** After item 8. Write which topics are corpus-sourced, which are
generated, and the rule for deciding. That becomes the standing policy.

**Done when.** The policy is in `docs/` and the pipeline follows it.

---

## 12. Two one-line defects, neither on the bank path

Deferred deliberately. Both are known, both are small, and neither blocks the
bank build. Do them when the bank build is not the active work.

**Do.**

- **Delete the topic from `scripts/step4_run_app.py:68`.** It prints
  `Thema: {topic_id} ({cefr})` above the sentence and then asks for the
  answer, so the learner is handed the grammar point before answering. That is
  CLAUDE.md rule 2, the product thesis, broken in the script
  `docs/building-the-bank.md` step 4 tells you to run to confirm the bank
  works. `src/cli/` and the PWA both render sentence and cue only, so this
  script misrepresents the app as well as leaking. Keep the `[3/6]` counter and
  drop the rest of the line; the CEFR level goes too, because level and topic
  correlate closely enough to leak.

- **Decide what `.github/workflows/ci.yml`'s `simulation-tests` and
  `live-tests` jobs are for.** Both run `pytest -m <marker>`; both markers are
  declared in `pyproject.toml` and applied to zero tests; pytest exits 5 on an
  empty selection and GitHub reads that as failure. So the pull-request gate to
  `main` is red before any code is written, and the nightly on `main` fails
  identically. Either mark the tests that belong to each lane
  (`tests/test_typo_simulation.py` is the simulation suite; nothing in the repo
  is a live-API test) or delete the two jobs and the two markers. Do not paper
  over it with `|| [ $? -eq 5 ]`, which makes an empty lane indistinguishable
  from a working one.

- **`mypy --strict src/ scripts/` fails on a clean checkout.** Five errors in
  four files, none of them recent: `build_verb_government.py:338` (an unused
  `type: ignore` masking a real `call-overload`), `check_gold_examples.py:211`,
  `step5_pilot_generation.py:95` (unused `type: ignore`), and
  `eval_tatoeba_translation_quality.py:224`. CI's `quality-checks` job runs
  exactly that command on **every push and every pull request**, so that gate
  is red too, independently of the marker problem above. Verified by stashing
  all working-tree changes and running against HEAD. `mypy --strict src/` alone
  is clean, which is presumably why this went unnoticed.

**Why later.** The first is a one-line deletion that changes no test. The
second is the owner's call, because it decides whether those two test lanes
exist at all. The third is four small type fixes. None of them blocks item 2,
but between them **every CI job in the repository currently fails**, so the
first green build will need all three.

**Done when.** `step4_run_app.py` names no topic before the answer,
`mypy --strict src/ scripts/` is clean on a fresh checkout, and a pull request
to `main` goes green.

---

## Smaller, any time

- **2.3b Reconcile the cost log monthly.** Run
  `scripts/reconcile_cost_log.py` at the end of every month, or after any run
  that reports retries. Both August cost bugs were found by putting the log
  next to Google's bill, not by reading code. This is a habit, not a task that
  finishes.

- **2.7b Two adversarial fixture records do not encode the defect they name.**
  `c08_04` and `c08_05` in
  `data/fixtures/verification/blanking_model_verifier_adversarial.jsonl` are
  filed as capitalisation traps, but their carriers were reconstructed with
  lowercase `ihrer` and `ihren`, which makes them ordinary correct sentences.
  This is a golden fixture, so correcting a record changes a published recall
  number and needs the owner's say-so plus a commit that explains it. Until
  then, note the caveat wherever that family's number is quoted.

- **Three CSS class-name mismatches in `web/`.** Listed in
  `docs/project-state.md`. Each one is a one-word edit. Nothing renders wrong
  enough to fail a test, which is why they have survived.

- **Decide whether Leipzig needs a content filter.** Leipzig is news prose and
  has turned up a quote about genocide and a report of an assault. Neither is
  a grammar defect. A blocklist applied to Leipzig only is cheap if the owner
  wants one.

---

## Specified, not started

### 10. Vocabulary FSRS (5.2)

The owner's spec:

- Same shape as the grammar trainer. One or two sentences, the tracked word is
  the missing token, no cue, just the English translation.
- Multi-word units are the one extension asked for: separable verbs, reflexive
  verbs, and verb-plus-preposition pairs (`warten auf`,
  `sich interessieren für`).
- The unit must be taught, not memorised as a string. `warten auf` has to be
  shown as `wartet auf`, `wartete auf`, `warte ... auf`. A fixed-string match
  is explicitly not what is wanted. The lemma plus the preposition is the
  unit; the surface form varies.

### 11. Click a word to see it in context (5.3)

Clicking a word in an exercise shows several sentences from our own corpus
containing that word, each with its English translation. The learner reads the
word in context, works out the meaning, and decides whether to add it to a
vocabulary list.

Needs no dictionary. It reuses the corpus, the translations, and the
lemmatiser already in the repo. Blocked on the translation store being
finished, so on item 3.

---

## Standing rules, not tasks

- **Anything the model verifier catches twice becomes a deterministic rule.**
  It is a discovery instrument. It must never be the only thing between a
  known defect class and a learner.
- **Anything the model verifier misses every time becomes a deterministic rule
  too.** Measured: 6 of 6 on reflexive case routing, 2 of 2 on Futur I, 2 of 2
  on topic misfiling, and it will score the same next run, because the cause is
  structural rather than inattention. Asking it again, at any batch size, will
  not help.

---

## Do not change these without asking the owner

Three separate times an agent reverted an edit the owner made by hand. All of
these are pinned by tests. A failing test here means ask, not fix.

**Constants in `src/llm/client.py`:**

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5`.
- `spend_ceiling_usd = 7.50`.
- The 5xx retry shape. Paid lane: `SERVER_ERROR_MAX_RETRIES = 4`,
  `SERVER_ERROR_BACKOFF_SCHEDULE = (30, 120, 480, 900)`,
  `SERVER_ERROR_BATCH_MAX_RETRIES = 2`. Free lane has its own:
  `FREE_LANE_SERVER_ERROR_MAX_RETRIES = 4`,
  `FREE_LANE_SERVER_ERROR_BACKOFF_SCHEDULE = (15, 30, 60, 120)`. Do not
  collapse the two lanes into one constant, and do not restore the older
  values: 40 retries 15 seconds apart, and a 12-retry free lane that sat for
  two and a half hours on a job that should take minutes. The reasoning is in
  the fix log.
- The free-to-paid fallback when retries are exhausted. It stays.

**Settled decisions:**

- **Tatoeba's own English translations are not trusted for exercises.**
  `build_translations.DEFAULT_TRUST_TATOEBA` and
  `step7_corpus_pilot.DEFAULT_TRUST_STORED_TATOEBA` are both `False`. A hand
  audit of 430 exercises found 4 wrong translations and 3 of the 4 were
  Tatoeba's. Machine translation measured better on the only metric that
  matters and structurally cannot pair a sentence with the wrong English.
  **Do not delete the Tatoeba records.** They are feature 11's whole corpus.
- **Every exercise shows its English translation, always.** A pedagogical
  decision, not a workaround. It follows that the verifier may treat the
  translation as something the learner has.
- **Where a German sentence has several English translations, keep the
  shortest.** Short is literal; long paraphrases, and paraphrase is where
  tense and determiners drift.
- **When a translation is wrong, replace the English and keep the item.** The
  German still works.
- **The cue uses the plain citation form,** not one already agreed for gender.
  A cue that equals the answer is fine when the learner still has to work out
  case and declension.
- **Vocabulary is filtered at each topic's own CEFR level,** never one global
  ceiling. A B1 grammar topic is not restricted to A1 words.
- **An unresolved reference is a property of the blank, not the sentence.**
  `Er sagte das damals nicht` is a good carrier; only a blank on `er` is bad.
- **Pilots forbid batch, not the paid lane.** Once free quota is spent they
  keep going on the paid lane, synchronously.
- **Ship with a full bank: 25 items per topic, built once, up front.** Nightly
  top-up is the last resort, not the build path.
