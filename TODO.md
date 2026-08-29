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

## 1. The large pilot (6.5)

**Do.** Phase A at a much larger scale than any previous cycle, then phase B with
`--verification-passes 2 --verification-batch-size 5`.

**Phase A is DONE** (2026-08-29): 1,225 items over 49 topics, zero topics short
of quota, in `data/corpus_candidate_pool.json`. Phase B has verified 80 of them
(the ones that had a trusted gloss) and banked 73; see
`docs/audits/cycle-28-glossed-verification.md`. The remaining 1,145 are waiting
on translations, not on verification.

**Two passes at batch 5 is now measured, not assumed.** Cycle 28 found 2
disagreements in 80 items, both real defects that one pass would have banked.
2.5%, matching the batch-size experiment's own 2.5% from a different variable.
The question this item used to carry, whether two passes at ONE size samples
only run-to-run noise, is answered: it does not.

**Batch size is 5, never 20.** Hand-audited on 475 identical candidates: batch 20
gave 444 accepted / 31 rejected, batch 5 gave 438 / 37, and of the 12 items the
two disagreed on, **all 12 were genuinely bad**. Batch 5 caught 9 of them, batch
20 caught 3. `DEFAULT_VERIFICATION_BATCH_SIZE` in the code is still 20, so the
flag has to be passed explicitly every time.

**Decided by the owner, 2026-08-28: two passes, both at batch 5.** Batch 10 was
considered and rejected. The measured effect is not a dial where 10 splits the
difference: batch 5 and batch 20 *disagree*, and all 12 disagreements were real
defects, 9 of which only batch 5 caught. Batch 10 would lose batch 5's closer
scrutiny without gaining the diversity effect, and it is the one value with no
hand-audited data behind it.

That caveat has since been settled by measurement and is recorded above: two
passes at one size catch real defects, not merely run-to-run noise.
`pass_disagreements` in the report remains the direct measure.

**Done when.** A pilot report exists with per-topic counts, `pass_disagreements`,
and a rejected file large enough for item 2 to sample from.

---

## 2. Measure the true false-positive rate (6.6)

**Do.** Review `data/corpus_pilot_rejected.jsonl` with agents and count how many
rejected items were actually good. The false-positive rate is that count over
the reviewed sample.

**Why this, and not the pilot itself.** A pilot cannot measure a false-positive
rate. The rate is defined against items known to be good, and a rejection says
nothing about whether the item was good; that is precisely the unknown. Only
reviewing the rejections produces the number. Scaling the pilot gives more
rejections, not more knowledge. Scaling the **reviewed sample** is what narrows
the estimate.

**This is agent-critical, so CLAUDE.md section 10 applies in full.** The number
decides whether the bank build runs at one pass or two, and everything
downstream is measured against it. So: an independent second pass by an agent
**from a different vendor**, disagreements committed rather than silently
reconciled, and the vendor recorded for each pass. There is a sharper reason
than the general rule here: the artefact being audited is an LLM verifier's
rejections, so an LLM auditor shares its failure modes, and agreement between
the two is worth much less than it looks. **Hand-check a subsample** to
calibrate what that agreement is actually worth.

**Done when.** A measured false-positive rate is written up with its sample size,
the two vendors' conflict list, and the hand-checked subsample.

---

## 3. Zero defects that reach a learner (6.7)

The target is zero defects for the overall pipeline. Zero *produced* is not
reachable: machine translation, the tagger's own accuracy ceiling
(`docs/audits/tagger-accuracy-vs-gold.md`) and the verifier's disagreement with
itself (`docs/known-defects.md` 2.9) are three independent and irreducible
sources. Worse, the only lever for approaching it is rejecting more, and
rejection is already the expensive failure: 12.9% discards about 181 good
candidates per bank, and two passes compound it to somewhere between 12.9% and
24.1%.

Zero defects that **survive contact with a learner** is reachable and
measurable. Three pieces, in value order:

- **A "report this exercise" button in the PWA.** One tap, writes to the review
  log, syncs through the Worker that already exists. Highest value on this list
  and it does not exist. It converts an unbounded unknown into a queue, it is the
  only mechanism that will ever catch a defect class nobody has thought of, and
  it makes the target measurable: reports per hundred items answered.
- **A post-hoc check over finished items.** `src/audit/bank_health.py` already
  walks every item and **does** have the `topic_id` the verifier is correctly
  denied. This is the only thing that would catch classes 2.10 to 2.14 if a
  selector regressed; today those five rules have nothing standing behind them.
  Cheapest entry, no spaCy and no model call:
  `cue[:1].isupper() != answer[:1].isupper()`.
- **A quarantine state instead of deletion.** A rejected candidate is discarded
  with no trace, which is why the false-positive rate is invisible in normal
  operation. Keeping rejections with their reason turns every future audit into a
  query instead of a re-run.

This supersedes the old item 7 (2.7), which asked the same question in a
narrower form.

**Done when.** The report button ships, `bank_health` runs over the built bank,
and a reports-per-hundred-items figure exists.

---

## 4. Collocation evidence, for defect class 2.1 (6.8)

**Do.** Mine collocation evidence from the corpus the same way
`scripts/build_verb_government.py` already mines case government: count real
occurrences, no model call, no hand curation. Use it at **blank-selection**
time, not as a rejection filter.

**Why that way round.** 2.1 ("words that do not go together") is held by the
model verifier alone. A collocation *rejection* filter would add to the
false-positive rate, which is the one thing this project cannot afford, because
a rare-but-correct collocation looks exactly like a wrong one when the counts
are sparse. Using the same evidence to *prefer* blanking a word whose
collocation with its syntactic head is well attested avoids creating the defect
instead of catching it, and it costs coverage rather than correctness. That is
the trade the pipeline already makes for classes 2.4 and 2.5.

**Where the evidence comes from, best first.**

1. **The Leipzig Corpora Collection's own co-occurrence files.** Same provenance
   as a corpus already in use, computed over far more text than the local sample,
   free, and an *external* anchor in the sense CLAUDE.md section 10 means.
2. **DWDS Wortprofil**, which gives typed relations (object-of, attribute-of,
   prep-complement) over a multi-billion-token German corpus. Best quality; costs
   an API dependency and needs its terms checked.
3. **Association measures over our own 450,000 sentences** (log-likelihood or
   t-score over dependency-linked pairs, reusing the spaCy parse phase A already
   runs). No new dependency, but the sample is small enough that sparsity will
   dominate for anything but high-frequency pairs.

**Check what already exists first.** `scripts/build_verb_government.py` covers
verb case government from unambiguous pronoun evidence, which is one slice of
2.1 already. Do not rebuild it.

**Design rule.** Whatever the source, the check must **abstain by default** and
fire only where both words are frequent enough for absence of evidence to mean
something. An unattested pair from two rare words is not evidence of anything.

**Done when.** A collocation resource is built, and the blanking step prefers
well-attested pairs, measured against a pilot's defect rate for class 2.1.

---

## 5. Build the item bank (6.1)

The biggest single piece of open work. Items 1 and 2 come first: they close the
one defect nothing catches, make the run resumable, and measure the
false-positive rate that decides how the build is configured.

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

## 6. Decide whether the gloss consistency check rejects (2.1b)

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

## 7. Run the gloss purge on the real store, then re-gloss (2.2b)

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

## 10. Two one-line defects, neither on the bank path

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
exist at all. The third is four small type fixes. None of them blocks item 5,
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

- **Content filtering is decided: there is none, and none is planned.** The
  owner's decision, 2026-08-29. This was carried here as an open question
  ("decide whether Leipzig needs a content filter"); it is now closed and the
  reasoning lives in `docs/known-defects.md` 2.8. Not work. Listed only so the
  question is not re-opened by someone who finds a Leipzig sentence
  distasteful.

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
