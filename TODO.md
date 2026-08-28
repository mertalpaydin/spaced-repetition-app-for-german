# TODO

Open work only. Closed work is in `docs/audits/fix-log.md` with its reasoning
and measurements; git history has the same in commit bodies.

Rule for anyone adding here, human or agent: **an item is a thing that is not
done.** When you close one, delete it and put the writeup in the fix log. This
file was allowed to reach 1,772 lines of finished work and was useless as a
work list. Do not do that again.

---

## 1. Known limits, recorded rather than solved

Not tasks. Do not "fix" these without a decision from the owner.

**`docs/known-defects.md` explains every one of these in plain English**, with
real examples, why no rule catches it and what would have to exist before one
could, plus the measured cost of the whole set. Read that file to understand a
limit; read the entries below to work on one. The entries stay here because
this is the work list; the doc is the explanation, and neither replaces the
other.

- **`Strässchen` and Swiss orthography with `ä`.** Standard German is
  `Sträßchen`. The rule added for Swiss spelling covers diphthongs before
  `ss` plus a closed list, and cannot decide `ä`: Swiss in `Strässchen` (long
  vowel), standard in `Fässer`, `Pässe`, `Gässchen` (short vowel). German
  orthography does not mark vowel length reliably enough. Recommendation
  already given and accepted: report it in each audit rather than pretend a
  rule exists.

- **Free datives and adjective-governed datives are out of reach of the verb
  government lexicon.** `Ich wasche mir die Hände` and `Meiner Schwester ist
  es kalt` have no governing verb to look up. They stay dropped.

- **`ausweichen` has no usable corpus evidence** and so is absent from the
  lexicon. A real coverage gap, not a threshold artefact. `handeln` is a
  second, structural instance: `sich handeln um` only ever occurs with the
  ambiguous `sich`, so the lexicon can never have an entry for it. Cycle 11
  fixed that one item's routing structurally instead (`CARD` in the
  preposition walk-back), not by adding evidence that cannot exist.

- **A colon is not itself a defect, so the junk-text filter is narrow on
  purpose.** Requiring every colon-delimited segment to be a clause rejected
  12 Tatoeba lines of which 11 are correct German. The shipped rule is four
  specific shapes instead (`colon_joined_fragment`), and scraped-web junk
  that does not match one of them still gets through. Widening it means
  measuring against those 12 again first.

- **"Spazieren gehen" and a capitalised verb that should be lowercase.**
  A single Leipzig source typo. German nominalisation is fully productive,
  so "das Spazieren" is a real word and no dictionary can call the string
  wrong; only its role beside "gehen" makes it wrong, and neither the
  vendored word list nor the lemmatiser carries that signal. spaCy's own
  tag is circular here (NOUN because of the capitalisation).

- **3rd person singular is out of reach for `pronomen_personal_nom`.** No
  German verb form distinguishes "er" from "sie" from "es", so the verb can
  never settle a blanked 3rd-singular subject, and recovering the intended
  one needs coreference this package does not have. The cell is dropped.

- **The four CEFR frequency-rank boundaries are tunable, not settled.** The
  owner set the shape (A1 against roughly the first 5,000 words, widening by
  level) and said outright the numbers were a guess. They are constants in
  `src/lexicon/vocabulary.py`. Raising A1 and A2 is the first lever if 70%
  retention proves too steep.

- **Two cycle 12 carrier defects, one each, no rule written.** A fixed
  construction filed as a tense ("Das Geschäft hat noch bis zum 19. Mai
  geöffnet" is "is open", not the perfect of "öffnen"); and a Tatoeba
  translation with German words in English order. Each needs a different
  rule, each would be written on a single example, and cycle 11's colon
  rule already showed what that costs. Left to the verifier. The third
  defect this item used to carry, the Leipzig headline with no main clause
  ("Ein Film, der die Frage aufwirft, ..."), was caught a second time in
  cycle 13 and is now the deterministic rule `no_main_clause_verb`; see the
  fix log's cycle 13 section for the measurement.

- **Collocation errors are the verifier's job and cannot be a rule here.**
  Cycle 13 found five of them among the eight items the verifier caught in
  one run and missed in the next ("einen Erfolg erreichen", "Eindruck
  über", "schnaubten wegen ihres Gehalts"). Every one needs a collocation
  lexicon (which verbs take which nouns, which nouns take which
  prepositions) that this repository does not have and that no structural
  check substitutes for. Recorded so the standing "caught twice becomes a
  rule" rule is not read as applying to them.

- **Caption residue in parentheses cannot be told from journalistic
  apposition by structure.** "Masi Pfand (am Ball) befindet sich ..." is
  scraped caption furniture; "Friedrich Merz (CDU) hat ... telefoniert." is
  correct German, and the two have the identical parse. The structural rule
  (a short verbless parenthesised insert between a proper-noun subject and
  its finite verb) was measured over 40,000 Leipzig lines in cycle 13: of
  the 150 hits the module otherwise accepts, 23 are position markers and
  127 are party affiliations, ages, abbreviation glosses and goal minutes.
  Widening `_PARENTHESISED_MARKER`'s closed list lexeme by lexeme is the
  only safe direction, and only for a phrase a pilot actually turns up.

- **Leipzig carriers have no content filter.** Cycle 11 turned up a quote
  about genocide, cycle 12 a report of a sledgehammer assault. Not grammar
  defects. A blocklist applied to Leipzig only is cheap if the owner wants
  one.

- **Anything the verifier catches twice becomes a deterministic rule.** A
  standing rule, not a task. The verifier is a discovery instrument and must
  never be the only thing between a known defect class and a learner.

- **A whole-corpus `step7_corpus_pilot.py` run is silent for hours and its
  rejected file is enormous.** Both stand, both are recorded rather than
  fixed. The carrier-validation pass and the tagging pass each parse every
  corpus line with spaCy and neither reports progress; measured in this
  project's own container at 78 and 92 sentences/second, 450,000 lines is
  about 2.5 hours with nothing on the console. The script now prints that
  estimate before the wait starts, which is the honest minimum; real
  progress reporting would need a callback through `blank_sentences` and
  `validate_carriers` and has not been built. Separately, the rejected JSONL
  scales at roughly 1.4 rows per corpus line (8,667 rows for 6,000 lines),
  so a whole-corpus run writes on the order of 650,000 rows, held in memory
  as a list first. That is large, not fatal, and it is the file the audit
  actually needs.

---

## 2. Open work

**Doing work this month? Section 6 is the order it goes in.** Building the
bank comes first and nothing below is a reason to delay it.

- [x] **2.1 Tense ambiguity on the modal topics. Settled by the gloss.**
  The cue names the verb, not the tense, so "(können)" left `kann`,
  `konnte` and `könnte` all open. Cycle 13 measured the cost: 44 of 105
  model rejections, 42%, named a tense or time alternative as an equally
  good answer.

  Cycle 14, same 475 candidates, with the verifier now reading the English
  gloss: **tense rejections 44 to 6, total rejections 105 to 42, accepted
  376 to 437.** The six survivors all cite the translation as their
  evidence ("Die englische Übersetzung 'are to' verlangt Präsens", "passend
  zur englischen Übersetzung ('didn't want')"), which is the check working,
  not failing. No time anchor in the carrier is needed. The recommendation
  in `docs/audits/cycle-12-corpus-report.md` is withdrawn.

- [ ] **2.1c The model verifier is not stable run to run, and that is now
  the largest open risk.** Cycle 14 diffed its accepted set against cycle
  13's on the identical 475 candidates. 64 items were newly accepted. 56 of
  those are explained by the gloss (37 were tense rejections, 19 other
  Frage 2 ambiguity). **The other 8 were rejected last run for bad GERMAN,
  which the gloss says nothing about, and accepted this run.** Every one of
  the 8 was a correct rejection last time:

  - `Masi Pfand (am Ball) befindet sich ...` scraped caption residue
  - `Sie erreichte einen großen Erfolg ...` unidiomatic collocation
  - `Ja", gesteht Norris, ...` opens mid-quotation
  - `Erst am 6. November 2021 wurde damals ...` date plus "damals"
  - `... war mein Eindruck über die jeweiligen Landsleute klar.` wrong preposition
  - `Sie schnaubten wegen ihres kleinen Gehalts.` unidiomatic
  - `... Bilder anhand der Google-Bildersuche entlarvt werden.` wrong collocation
  - `Ein Film, der die Frage aufwirft, ...` no main clause

  Two now have deterministic rules (`opens_mid_quotation`,
  `no_main_clause_verb`), five are collocation errors no rule here can
  reach (section 1), one is too narrow to rule. So the verifier remains the
  only thing between roughly six defects a cycle and a learner, and it
  agrees with itself about 92% of the time on this judgment.

  **The batch-size experiment has now been run, and it did not find what it
  was looking for. It found something better.** The identical 475 candidates
  were verified at batch size 20 and at batch size 5, everything else held
  fixed:

  ```
  batch 20:  444 accepted, 31 rejected
  batch  5:  438 accepted, 37 rejected
  ```

  A 6-item difference in the totals, which reads like noise. Diffed item by
  item it is not:

  - **9 items were accepted at batch 20 and rejected at batch 5.**
  - **3 items were accepted at batch 5 and rejected at batch 20.**

  All 12 were read by hand. **Every one of the 12 is a genuinely bad item**
  (fragmented quotations, a wrong preposition in "Eindruck über", an archaic
  Dante line, wrong word order, a Swiss-formatted number, genuine tense
  ambiguity the gloss does not settle). So batch size is not the lever:
  neither size catches everything, and 12 defects in 475 items, 2.5%, are
  decided by which run you happen to look at. The union of the two runs
  catches all 12; either run alone does not.

  **That makes the third option the one to build, and it is built.**
  `scripts/step7_corpus_pilot.py --verification-passes N` runs the
  verification pass N times over the same items and rejects an item that ANY
  pass rejects: union of rejections, intersection of acceptances, the reason
  kept from the first pass that rejected. Default 1, which is byte-for-byte
  the previous behaviour. Passes after the first bypass the local response
  cache (`verify_items(use_cache=False)`), without which an identical prompt
  would replay pass 1's verdict for free and the whole feature would measure
  nothing. The report now carries `verification_passes`, each pass's own
  counts, how many rejections were unique to that pass,
  `rejected_by_any_pass` and **`pass_disagreements`** -- items at least one
  pass rejected and at least one accepted, which is the direct measure of
  this instability and the number to watch.

  Cost, against the $7.50/month ceiling and measured from `cost_log`: about
  $0.18 per pilot cycle at batch size 20, about $0.36 at batch size 5. Two
  passes roughly doubles whichever is chosen. A later pass that cannot run
  (`BudgetExceeded`) degrades: the run reports what it managed, says the
  pass did not run, and keeps the passes that did.

  **What is still open here:** the flag makes the union measurable, it does
  not yet say how many passes are worth buying. Run `--verification-passes
  2` once at batch 20 and read `pass_disagreements` against the 12 this
  experiment found by hand. Asking the naturalness question in its own
  separate call is still untried and is the next option if two passes are
  not enough.

- [ ] **2.1b Decide whether the gloss check enforces.** Wiring done.
  `step7_corpus_pilot.py` fills `gloss_en` from the translation store,
  translating and storing whatever the store lacks. **As of 2026-08-27 a
  stored gloss whose source is Tatoeba does not count as "the store has it"
  (section 4): it is re-translated and the record overwritten.** All the
  measurements below predate that, so the glosses this check was measured
  against were largely Tatoeba's; re-measure before drawing a conclusion
  about enforcing. The consistency check
  (a gloss whose tense or person contradicts the answer) **runs and reports
  but does not reject**, by default; `--enforce-gloss-check` makes it real.

  **Cycle 13 measured it and the first reading was damning: 34 flags on
  376 accepted items, of which 33 were the check being wrong and 1 was a
  real defect.** Four bugs, all now fixed: English contractions were not
  read as tense marking, so "I'll be lonely" counted as present; English
  marks the subjunctive with its past forms, which a German conditional
  target treated as a contradiction; the person check demanded an English
  pronoun even where the subject is a noun, and was fooled by German 1st
  and 3rd singular being identical in Konjunktiv II; and German match
  strings were being found inside English text, so "Ministers usually
  fall" leaked the grammar term "Fall" and "the Ukraine war" leaked the
  answer "war". Re-measured after the fix: **1 flag on 376**, and it is
  the real defect.

  **A second pilot with those four fixes in place measured 3 flags on 437
  accepted items, and all 3 were again the check being wrong.** Three more
  bugs, all now fixed: impersonal `man` is grammatically 3rd singular but
  has no English pronoun counterpart (`In der Schule kann man nicht
  rauchen.` / "You can't smoke at school." demanded he/she/it), so a
  nominative `man` now widens the expected pronoun set to you/we/they and
  promotes an oblique object to subject the way impersonal `es` already
  did; an English modal carries no tense feature at all, so a German modal
  target glossed with one (`Schüler sollten nicht arbeiten ...` /
  "Students should not work ...") can be confirmed but never contradicted,
  the same treatment Konjunktiv II already gets; and the English noun
  "will" was read as the future auxiliary (`... nach Gottes Willen ...` /
  "According to God's will, ..."), now settled by asking the English
  tagger this module already loads whether every "will" in the gloss is a
  NOUN. Re-measured after those three: **0 flags on 437, and still exactly
  1 flag on 376, still the real defect.**

  Still measure-only. One flag is not enough evidence to enforce, and the
  surviving catch is lucky rather than designed: the bad Tatoeba pairing
  (`Nachdem der Vorfall an die Öffentlichkeit gekommen war ...` glossed
  `The trouble is that I don't have much money now.`) is caught only
  because the unrelated English happens to be present tense. The signal
  that actually separates it from the 33 is that it shares no proper noun
  or numeral with its German. **A content-overlap check is the one to
  build for bad pairings, and it is a different check from this one.**

  Known limit that remains: the check compares the gloss against the
  **answer's** own tense and person, so it only bites on items whose
  answer carries that morphology. A wrong gloss on an item that blanks a
  determiner or an adjective ending passes untouched. Verified on a real
  example: one carrier with a deliberately wrong past-tense gloss produced
  two items, and only the one answering a verb was caught. This caps its
  recall structurally and bears directly on 2.2.

- [ ] **2.2 Prove the verifier catches a WRONG English translation.** Owner's
  requirement, and the condition the whole gloss plan rests on. The cycle 12
  measurement hand-checked 120 Tatoeba pairs and found 2 wrong: one tense
  (`Das Kind sah aus wie aus dem Ei gepellt.` / "The child looks as neat as
  a pin.") and one determiner (`der Kuchen` / "this cake"). Both land on
  grammar the gloss is supposed to disambiguate, so the verifier reading the
  gloss against its own German sentence is what keeps a bad gloss out of the
  bank. A gloss check that does not catch these is worse than no gloss check,
  because the verifier would then be relaxing its uniqueness judgment on
  evidence it never validated.

  **The fixture and the harness are built. The run is what is still open, and
  it needs the owner's key.**

  ```
  uv run python -m scripts.eval_gloss_adversarial
  uv run python -m scripts.eval_gloss_adversarial --batch-size 5
  ```

  `data/fixtures/adversarial/wrong_glosses.jsonl` is 36 matched pairs, 72
  rows, every German prompt, answer and cue copied verbatim from the last
  pilot's own 430 accepted items. Each pair carries one row with a
  deliberately wrong gloss and one row with the item's real gloss, so the run
  produces a false-positive rate as well as a recall figure, and a rejection
  that happens in BOTH arms is reported separately rather than counted as a
  catch. Six defect kinds, six pairs each: wrong tense, wrong person, wrong
  number, wrong definiteness, wrong polarity, completely unrelated. The two
  arms are verified in separate calls so the model never sees a pair's two
  glosses side by side.

  **The predictions are written into the script's own docstring, before the
  run, so the result can contradict them**: polarity and unrelated are
  expected to be caught, definiteness is expected to be missed. With no key
  configured the runner reports "NOT RUN" and exits non-zero; it never prints
  a zero recall for a run that did not happen.

  **What building the harness already showed, without a single API call:** no
  question in the live verification instruction asks whether the gloss is
  correct. The four questions are about the German, the answer's uniqueness,
  whether every word exists, and the cue. The gloss enters question 2 only as
  a reason to rule an alternative OUT. So any catch here is incidental, and a
  poor recall points at adding a fifth question rather than at tuning
  anything. That question was deliberately not added before measuring, so the
  number describes the pass as it actually ships.

- [ ] **2.2b Run the gloss purge on the owner's real store, then re-gloss.**
  The cross-corpus id collision that poisoned it is fixed and the cleanup
  tool is written and tested (`scripts/purge_mismatched_glosses.py`,
  `docs/audits/fix-log.md`), but neither has been run against the real
  `data/fixtures/translations/de_en.jsonl`, which does not exist in the
  sandbox this was built in. Measured there by the owner: **7,365 of the
  7,499 Leipzig carriers in the store, 98.2%, are labelled
  `source="tatoeba"` and are therefore wrong**, because a Leipzig
  sentence's text cannot legitimately come from Tatoeba unless that exact
  sentence is in Tatoeba too. Two of them reached the last pilot's 430
  accepted items:

  - `Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung
    schlimmer ist als gedacht.` glossed `"She crossed the street."`
  - `Jetzt gibt sie ein Update zu ihrem Alltag während der Chemotherapie.`
    glossed `"Bye!"`

  Sequence: `--dry-run` first and read the examples, then apply, then
  re-run `build_translations.py --carriers-from` for the affected pilot so
  the removed sentences get a real machine translation. **This blocks 2.2
  and any re-audit of the last pilot**: the verifier now READS the gloss
  and relaxes its uniqueness judgment against it, so until the purge runs,
  every accepted-item count drawn from a Leipzig carrier rests on evidence
  that may be an unrelated sentence.

  **Partly overtaken by the 2026-08-27 Tatoeba distrust (section 4), which
  nobody planned as a fix for this.** Every one of those 7,365 poisoned
  Leipzig records carries `source="tatoeba"`, and `step7_corpus_pilot.py`
  now treats any `source="tatoeba"` record as a cache miss by default. So
  the next pilot re-translates them whether or not the purge has run, and no
  poisoned gloss can reach an item on the default path. That does NOT make
  the purge pointless: the mislabelled records are still in the store and
  still feed 5.3, where they are actively wrong (a Leipzig sentence shown
  with an unrelated Tatoeba sentence's English), and the distrust does not
  distinguish them from legitimate Tatoeba records. Run the purge for 5.3's
  sake; it is no longer the thing blocking a pilot.

- [ ] **2.3 Get real numbers out of `scripts/eval_verifier.py`.** The
  adversarial set (38 confirmed defects, 31 confirmed clean) and the script
  both exist. They have never been run with a key, so the verifier's recall
  and false-positive rate are still unmeasured. **This half of 2.3 is still
  open.**

  **The batch-size half is done and is answered: batch size is not the
  lever.** 475 identical candidates at batch 20 gave 444 accepted / 31
  rejected, at batch 5 gave 438 accepted / 37 rejected. Item by item, 9 were
  accepted at 20 and rejected at 5 and 3 went the other way, and all 12 were
  read by hand and all 12 are genuinely bad items. Items late in a large
  batch are not the problem; the verifier is simply unstable on about 2.5%
  of this corpus, in both directions. See 2.1c for the full finding.

  **What that produced:** `scripts/step7_corpus_pilot.py
  --verification-passes N` (default 1, unchanged behaviour), which runs the
  verification pass N times over the same items and rejects on ANY pass's
  rejection. The union of two passes is the instrument that catches all 12;
  either run alone does not. Passes after the first bypass the response
  cache on purpose, since an identical prompt would otherwise replay the
  first pass's verdict. `pass_disagreements` in the run report is the number
  to watch: it is how unstable the verifier was on that run's own items.
  Cost against the $7.50/month ceiling, from `cost_log`: about $0.18 per
  cycle at batch 20, about $0.36 at batch 5, roughly doubled per extra pass.

  `scripts/eval_verifier.py` itself does not have this flag. It runs against
  a fixed 69-item adversarial set with known ground truth, where the honest
  measurement is a single pass's own recall and false-positive rate; adding
  a union there would measure the union rather than the verifier. If the
  recall run below shows the same instability, that is the point to decide
  whether the eval should report both.

- [ ] **2.3b Keep reconciling, monthly.** The August repair itself is done
  and is written up in `docs/audits/fix-log.md` cycle 16: the log went from
  $1.986058 to $5.040919 against a bill of $5.040919, an exact match, via
  690 repriced rows, 847 rows given a transport mode by invariant and 10
  labelled adjustment rows. What stays open is the habit, not that run.

  Run `scripts/reconcile_cost_log.py` at the end of every month, or after
  any run that reports retries. Neither of the two August cost bugs was
  found by reading code; both were found by putting the log next to the
  bill, and only after the owner pushed back on a number he had been
  given. The comparison is now a script so the next discrepancy is found
  by running it rather than by him noticing.

  Note the honest limit of the repair: it reprices what the bill can
  classify and labels the rest. The unlogged attempts stay unlogged, because
  their token counts do not exist anywhere. The adjustment rows carry
  dollars and zero tokens on purpose. An exact match after repair therefore
  means the dollars agree; it does not mean the missing token counts came
  back.

- [ ] **2.4 The AI generation pilot, last. Stays OPEN, explicitly.** The owner
  said so on 2026-08-27: it is not closed, not deferred indefinitely, and not
  to be quietly folded into 2.5. Owner's sequencing: corpus path proven first,
  then generation for what the corpus cannot reach. On current evidence that
  is `futur_i`, `zustandspassiv_zeiten` and `futur_ii`, which are too rare
  even in 80,000 corpus sentences. Building the bank (section 6) will say
  exactly which topics fall short of 25 items from the whole corpus, and that
  list is this item's real input.

- [ ] **2.5 Decide the split and write it down.** After 2.1 and 2.4: which
  topics are corpus-sourced, which are generated, and the rule for choosing.
  That becomes the standing generation policy.

- [ ] **2.6 Schedule the monthly translation job on the owner's machine.**
  Built and tested; not yet scheduled, and it is the only remaining step.
  `scripts/monthly_translation_topup.py`, set up per
  `docs/monthly-translation-job.md` (one `schtasks /Create` line). Until that
  task exists in Windows Task Scheduler, nothing runs and the corpus does not
  progress. See section 5.1 for the arithmetic and the standing job's contract.

---

## 3. Owner changes that must never be overturned

Pinned by tests. Do not change without the owner saying so explicitly.

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5` in `src/llm/client.py`.
- The 5xx retry shape in `src/llm/client.py`. **The owner has changed these
  himself; the values below are the current ones, not the ones this section
  used to pin.** History, so nobody "restores" the older numbers: he first
  raised the count to 5 with a flat `SERVER_ERROR_BACKOFF_SECONDS = 15.0`
  after a pilot died on a 503, then raised it to 40 and added a free-to-paid
  lane fallback once those retries are exhausted. He has since replaced that
  with the opposite shape, verbatim: *"instead of making 40 request make it
  like 4 but with much longer intervals."* 40 attempts 15 seconds apart is
  ten minutes of hammering a service that is already down; a real outage
  lasts minutes. What stands now:
  - `SERVER_ERROR_MAX_RETRIES = 4`.
  - `SERVER_ERROR_BACKOFF_SCHEDULE = (30.0, 120.0, 480.0, 900.0)`, an
    escalating schedule covering roughly 25 minutes, replacing the flat
    15.0. `SERVER_ERROR_BACKOFF_SECONDS = 900.0` survives only as the
    scalar fallback for an attempt index past the end of the schedule.
  - `SERVER_ERROR_BATCH_MAX_RETRIES = 2`, a lower cap for the batch path.
    **This one is not the owner's instruction**, it is the 2026-08-27
    cycle's judgement, from cost asymmetry: a sync 503 served nothing and
    billed nothing, but a failed batch job has already been billed for the
    requests it processed before it failed and
    `_call_batch_many_with_retry` resubmits the whole job. His Aug 14-17
    bill shows batch usage on days where the cost log has zero rows. Those
    days would no longer be empty: every failed batch attempt now writes a
    row (zero tokens, truthful outcome tag), including the
    `JOB_STATE_FAILED` case that is not retried at all.
  - **The free-to-paid fallback stays.** When the retries are exhausted on
    the free lane, the call moves to the paid lane instead of raising
    (only when the paid lane is permitted and a paid key is configured).
    Do not remove it.
  - **The budget above is the PAID lane's.** The free lane has its own,
    `FREE_LANE_SERVER_ERROR_MAX_RETRIES = 4` with
    `FREE_LANE_SERVER_ERROR_BACKOFF_SCHEDULE = (15, 30, 60, 120)`, worst
    case 225 seconds of sleeping per call, and 900 seconds (4 concurrency
    waves) for a 15-call group. **Not the owner's instruction as a
    number**; it is this cycle's judgement. Do not collapse the two lanes
    back into one constant, and do not restore the values this line used to
    carry (`12` retries on `(30, 60, 120, 240, 480, 900 x 7)`, worst case
    7230 seconds per call and roughly 8 hours for a 15-call group). That
    shape shipped on 2026-08-28 and was wrong: the owner watched
    `scripts/eval_verifier.py`, a job that should take minutes, sit for over
    two and a half hours with no output. The argument for it (a free-lane
    retry cannot bill anything, so retrying is free) was true but
    incomplete. **The local content-addressed cache is what completes it:**
    verdicts that already landed are replayed at zero cost on the next run
    (`lane="cache"`), so progress is durable across runs and a dying run
    loses almost nothing. Exiting quickly and being re-run therefore beats
    sleeping, and gives the operator a live process instead of silence he
    cannot distinguish from a hang.
- `spend_ceiling_usd` defaults to **7.50** in `src/llm/client.py`, raised
  from 5.00 at the owner's instruction on 2026-08-27. CLAUDE.md section 9
  and `docs/audits/stage-00-quota.md` were corrected in the same commit.
- **Tatoeba translations are not trusted for exercises.**
  `build_translations.DEFAULT_TRUST_TATOEBA` and
  `step7_corpus_pilot.DEFAULT_TRUST_STORED_TATOEBA` are both `False`, and
  both flags that flip them are opt-in. The evidence and the full reasoning
  are in section 4; the short version is that a hand audit of 430 accepted
  exercises found 4 wrong glosses and 3 of the 4 were Tatoeba's own. Do not
  flip either default back. Do NOT delete the Tatoeba records either: they
  are 5.3's whole corpus.

Three separate cycles reverted an owner edit to this file. Both groups now
have a pinning test that says to ask rather than update the assertion.

---

## 4. Decisions the owner has made, so nobody relitigates them

- **Cue with the invariant citation form**, not a gender-agreed one. A cue
  equal to its answer is fine when the learner still had to work out case and
  gender. Verbatim: "cue being the answer is not a problem if the problem
  still requires student to identify case, declension etc."
- **Vocabulary is filtered at each topic's own CEFR level**, never one global
  ceiling. A B1 grammar topic is not restricted to A1 words.
- **Unresolved references are a property of the slot, not the sentence.**
  `Er sagte das damals nicht` is a good carrier; only a blank on `er` is bad.
- **Pilots forbid batch, not the paid lane.** Once free quota is spent they
  continue on paid, on demand.
- **Every exercise shows its English translation, always.** Owner's call,
  made on pedagogical grounds, not as a fallback for ambiguity. It follows
  that the verifier may treat the translation as available to the learner
  when judging whether an answer is unique.
- **Where a German sentence has several English translations, keep the
  shortest.** 13.1% of Tatoeba's German sentences have more than one. A
  short translation is the more literal one, and literal is what maps word
  to word for a learner; a long one paraphrases, and paraphrase is where
  tense and determiners drift.

- **When a gloss is wrong, replace the English and keep the item.** The
  German still works as an exercise; only the translation failed. Dropping
  the item throws away a good carrier to punish a bad string.

- **Stop using Tatoeba translations for exercises. Machine translate them
  all.** Decided 2026-08-27, from a hand audit of all 430 accepted exercises
  in the last pilot. Ten defects; four were items whose German is correct and
  whose English gloss is wrong, and **three of those four came from Tatoeba's
  own human translations, not from machine translation**:

  ```
  Wenn ich im Lotto gewänne, würde ich mir ein neues Auto kaufen.
  "If I won the lottery, I'd buy you a new car."      <- mir is himself
      [Tatoeba]

  Ich habe eine Freundin, die sich selbst die Haare schneidet.
  "I have a friend who cuts his own hair."            <- Freundin is female
      [Tatoeba]

  Das Haus, in dem man lacht, wird vom Glück bedacht.
  "The house in which one laughs is considered by luck."  <- meaningless
      [Tatoeba]

  Aber: Das Thema ist damit nicht beendet ...
  "But: The topic is not over there ..."              <- damit is not "over there"
      [machine]
  ```

  A separate hand check of 120 Tatoeba pairs found 2 outright wrong and 6
  loose, so this is a rate, not three unlucky rows.

  **The machine-translation counterpart is now measured too, 2026-08-27,
  n=120 hand-checked, and it settles the comparison.**

  | | Machine | Tatoeba |
  |---|---:|---:|
  | Sample | 120 | 120 |
  | Clean | 116 | 112 |
  | Loose but usable | about 4 | 6 |
  | **Outright wrong** | **0** | **2** |
  | Mispaired with the wrong German | 0 | n/a |

  The four machine cases are drift, not error: `Du darfst gehen.` rendered
  "You can go", which loses the permission sense; `Man stellt diese Kiste aus
  Holz her.` rendered as a passive, "This box is made of wood"; `Sie können
  Einer dem Anderen helfen.` rendered "help one to the other" instead of
  "help each other". None of the three would mislead a learner about the
  grammar the item tests.

  Two things follow. Machine translation is **measurably better on the metric
  that matters**, zero outright wrong against two. And it **structurally
  cannot mispair**: it translates the sentence it is handed, whereas a lookup
  table can return the wrong row, and did, 7,365 times in the owner's own
  store (2.2b). That second point is not a quality difference, it is a
  difference in what can go wrong at all. Full write-up in
  `docs/known-defects.md`.

  **This is not "re-translate all 200,555 Tatoeba records".** That is about
  13,000,000 characters, roughly half a year of Azure F0, and it is
  explicitly not what was asked for. Glosses are only needed for sentences
  that actually become exercises, about 475 per pilot cycle, roughly 20,000
  to 31,000 characters. The store converts itself over time, for exactly the
  sentences that matter.

  **The Tatoeba records are retained, not deleted.** They are the only thing
  feeding planned feature 5.3 (click a word, see it in several corpus
  sentences with their translations), which needs breadth far more than it
  needs precision. They are distrusted on the exercise path only. Deleting
  them would cost 5.3 its entire corpus for a defect rate 5.3 does not care
  about.

  Built as two opt-ins, both defaulting to distrust:

  - `scripts/build_translations.py --trust-tatoeba` (default OFF). With it
    off, `_fill_from_tatoeba` does not run and those carriers go to machine
    translation. With it on, the pre-2026-08-27 behaviour, which is how the
    5.3 corpus store gets rebuilt cheaply.
  - `scripts/step7_corpus_pilot.py --trust-stored-tatoeba` (default OFF).
    With it off, a store record whose `source` is `"tatoeba"` is treated as
    a cache MISS: the carrier is re-translated and the record is overwritten
    with the machine translation. A record from `azure` or `gemini` is used
    as-is. With it on, an earlier run can be reproduced exactly.

  A distrusted gloss that could not be replaced (failure, budget, no
  translator) leaves the item at `gloss_en=None` rather than falling back on
  the Tatoeba English, and is reported as `gloss_missing_stale_tatoeba`.

- **Ship with a full bank. 25 items per topic, built once, up front.**
  Decided 2026-08-27. 49 topics at 25 items is about **1,225 items**, read
  from the whole corpus rather than 40,000 lines per source, verified by two
  passes at batch size 5. Nightly top-up is the last resort, not the build
  path.

  **Cost: $0.00268 an item, so $3.28 for the whole bank**, measured from the
  owner's own Google bill rather than estimated from the cost log. Against a
  $7.50/month ceiling. Glossing 1,225 carriers is about 73,000 characters,
  one night of Azure F0's 2,000,000 a month.

  `scripts/step7_corpus_pilot.py --write-bank data/bank.db` is the hop that
  makes this possible; before it, no script in the repository ever wrote a
  corpus-pilot item into `data/bank.db`. The exact command sequence, with
  the per-step cost, duration and "did it work" check, is
  `docs/building-the-bank.md`.

  **Two things about that run that are not the defaults.** `--limit` is per
  source and defaults to 40,000, so a whole-corpus run has to pass a large
  one. And `--max-translation-characters` defaults to 60,000, which was
  sized for a 475-item cycle: 1,225 carriers at the measured mean of 61.7
  characters is about 75,600, so the default guard would stop at a batch
  boundary and leave several hundred items unglossed. Pass 120,000. The
  default is deliberately left alone because it is a runaway guard and the
  right size for it is a decision about a specific run, not a constant.

---

## 5. Planned features, specified but not started

Not defects. Recorded here so the spec is not lost between sessions.

### 5.1 English translation on every exercise

Decided (section 4). Three parts, in order:

1. **Fetch Tatoeba's German-English export** and measure translation
   accuracy on a hand-checked sample. The export does not mark INDIRECT
   translations (German to X to English), which drift, so the sample must
   count those specifically. This number decides whether the rest is worth
   building.
2. **Translate the rest** with a dedicated translation API, not an LLM.
   Built and run once. `scripts/build_translations.py`, backed by
   `src/llm/translation.py` (Azure Translator F0 primary, Gemini fallback),
   every call logged through `src/llm/client.py` per CLAUDE.md rule 4.

   **First run, 2026-08-24, and what it settled.** 450,490 distinct
   carriers were read. Tatoeba's own pairs glossed 200,555 of them at zero
   cost, in one pass, permanently. 1,000 more were machine translated.
   248,935 were left.

   Those 248,935 are about 17,000,000 characters, which is roughly eight
   and a half months of the free tier. That kills the whole-corpus
   backfill: the earlier "six weeks" estimate assumed only the
   carrier-valid subset, and carrier validation only removes about 30%
   (55,939 of 80,000 in cycle 12), so it does not rescue the number.

   **The fix is to translate per build, not per corpus.** Measured against
   cycle 12's own 396 accepted items: 230 of their carriers already have a
   Tatoeba gloss, so a whole pilot needs 166 new translations, about 11,000
   characters, one run. `--carriers-from` on `build_translations.py` is
   that mode. The whole-corpus mode stays for feature 5.3, which does want
   many corpus sentences glossed, and the free Tatoeba 200,555 already
   covers 5.3 without another paid character.

   **Superseded in part on 2026-08-27: Tatoeba's own translations are no
   longer used for exercises** (section 4). The 230-of-396 figure above is
   what a pilot USED to get free and is now what it re-translates instead.
   The arithmetic for one 475-item cycle under the new behaviour: the last
   pilot's 392 review items measure at a mean carrier length of 61.7
   characters, so the worst case, a store that helps not at all, is 475 x
   61.7 = about **29,300 characters**, and the expected case (about 300
   Tatoeba glosses replaced) is about 18,500 plus whatever the store has
   never held. `--max-translation-characters` stays at **60,000**: it still
   covers a whole cycle with roughly 2x headroom, so it was not raised. The
   whole-corpus mode plus `--trust-tatoeba` remains 5.3's cheap rebuild
   path, and the 200,555 free Tatoeba records stay on disk for it.

   **Superseded again on 2026-08-27, by the owner: the whole corpus is to be
   translated, on a standing schedule.** Verbatim: *"I want to run a azure
   free translation run every week or month whenever limits are reset so that
   we maintain a healthy translated corpus. I do not want to use potentially
   bad translations for anything."* That is a wider scope than "translate per
   build", and it applies everywhere, 5.3 included, not just to exercises.

   The arithmetic, measured 2026-08-27:

   | | |
   |---|---|
   | Distinct carriers | 450,490 |
   | Corpus characters | 26,933,263 (mean 59.8) |
   | Store: Tatoeba (distrusted) / azure / gemini | 200,555 / 1,139 / 100 |
   | **Carriers with no trusted machine translation** | **449,251** |
   | Azure F0 allowance | 2,000,000 characters/month |
   | **Whole corpus at that rate** | **13.4 months** (26,933,263 / 2,000,000 = 13.47, so 14 monthly runs) |

   Built as `scripts/monthly_translation_topup.py`, backed by
   `src/llm/translation_ledger.py`. What it adds over
   `build_translations.py`, which could not have been scheduled:

   - **Month-to-date spend survives the process.**
     `AzureTranslator.characters_used` is per process and `--max-characters`
     is per invocation, so before this a second run in one month spent the
     allowance twice and found out by taking an HTTP 403 mid-batch. The
     ledger is `data/fixtures/translations/azure_f0_ledger.json`, keyed
     `YYYY-MM` in UTC, written atomically after every successful batch.
     Rollover is automatic: an unseen month has spent nothing.
   - **Two passes, in order.** Carriers with no gloss at all, then carriers
     whose stored gloss is Tatoeba's. Order inside each pass is a keyed
     BLAKE2b of the sentence, not a shuffle, so a carrier's rank does not move
     when the corpus grows and thirteen consecutive runs walk forward instead
     of re-drawing overlapping slices.
   - **The report says how many months are left**, recomputed from what is
     actually still untrusted rather than quoted from this table.

   Not yet scheduled: see 2.6 and `docs/monthly-translation-job.md`.
   `build_translations.py` is unchanged for its own two modes; the monthly job
   imports its store reader, store writer, translator construction and batch
   loop rather than copying any of them.

3. **Show the translation in the app.** Done. It renders under the German
   sentence, before the learner answers and still visible after grading,
   and an item without a gloss renders nothing at all.

   Building it found that `gloss_en` was being dropped TWICE on the way to
   the browser: the `items` table had no `gloss_en` column, so any item
   carrying one lost it on insert, and `EXPORTED_BANK_ITEM_FIELDS` is an
   explicit allowlist that filtered it out of the bundle as well. Fixed by
   migration v4 plus the allowlist entry. Nothing in `data/bank.db` has a
   glossed row yet, because the pilot writes items to a review JSONL and
   not to the bank; the export will carry them once a glossed pilot is
   banked.

4. **Give the verifier the gloss.** Done, and licensed by step 3 landing
   first: relaxing against information the learner never gets would
   manufacture the non-unique-answer defect class cycles 11 and 12 closed.
   The item block now carries `Englische Übersetzung:` (or `(keine)`), and
   question 2 says an alternative the translation rules out is not a
   second correct answer.

   The relaxation is bounded in the instruction itself, in as many words:
   a translation settles **tense, person, number and definiteness** only.
   It says nothing about German case, gender, adjective endings, reflexive
   pronouns or preposition government, and must never rule an alternative
   out on those. Those are the majority of the gates and stay untouched.

   Unmeasured until the next pilot. Expected recovery is most of the 44
   tense-ambiguity rejections in item 2.1. If accepted items rise and no
   new wrong-answer defect appears in the audit, this is settled; if a
   wrong answer does appear, the bound above is where to look first.

### 5.2 Vocabulary FSRS

Owner's spec, verbatim in substance:

- **Identical in shape to the grammar trainer.** One or two sentences
  depending on the word, the tracked word is the missing token, no cue,
  just the English translation.
- **Multi-word units are the one extension asked for**: separable verbs,
  reflexive verbs, and verb-plus-preposition phrases (`warten auf`,
  `sich interessieren für`).
- **The unit must be taught, not memorised as a string.** `warten auf` has
  to be recognised and presented as `wartet auf`, `wartete auf`, `warte
  ... auf` and so on. A fixed-string match is explicitly not what is
  wanted. The lemma-plus-preposition pair is the unit; the surface form
  varies.

### 5.3 Click a word to see it in context

Replaces an earlier hover-for-a-word-gloss idea, which the owner withdrew
after it turned out to need a German-English dictionary we do not have.

Clicking a word in an exercise shows **several sentences from our own
corpus containing that word, each with its English translation**. The
learner reads the word in context, works out the meaning, and decides
whether to add it to the vocabulary list.

Needs no dictionary at all. It reuses the corpus and the translations 5.1
already produces, plus the lemmatiser we already have to match inflected
forms back to one word. Blocked on 5.1 only.

**Scope change, 2026-08-27.** The earlier plan was that 5.3 would be fed by
the 200,555 free Tatoeba glosses, because 5.3 "needs breadth far more than it
needs precision". The owner's standing-job instruction overrides that: *"I do
not want to use potentially bad translations for anything."* The monthly job
(section 5.1, item 2.6) therefore replaces those glosses too, at the rate of
about 33,000 carriers a month, and 5.3 reads the same store as the exercise
path with no separate trust setting. The Tatoeba records still are not
deleted, and each one is only overwritten when a machine translation actually
lands, so 5.3's corpus shrinks at no point.

---

## 6. September

The order the next month's work goes in. Not a new backlog: every item here
already exists above, and this section says which one comes first and why.
Sections 1 to 5 keep their numbering, so nothing that cites "TODO.md section
4" has moved.

### 6.1 Build the bank. First, before anything else.

`docs/building-the-bank.md` is the command sequence, five commands, each with
its cost, its duration and its own "did it work" check. Nothing else in this
list is a reason to delay it, and it unblocks several of them.

- **25 items per topic, built once, up front.** The owner's decision
  (section 4). 49 topics at 25 items is about **1,225 items**, read from the
  whole corpus rather than 40,000 lines per source, verified by two passes at
  batch size 5. Nightly top-up is the last resort, not the build path.
- **Cost: $0.00268 an item, so $3.28 for the whole bank.** Measured from the
  owner's own Google bill, not estimated from the cost log. Against a
  $7.50/month ceiling. Glossing 1,225 carriers is about 73,000 characters,
  one night of Azure F0's 2,000,000 a month.
- **Pass `--max-translation-characters 120000`.** This is the one flag that
  will silently ruin the run if it is left alone. The default is 60,000,
  sized for a 475-item cycle; 1,225 carriers at the measured mean of 61.7
  characters is about 75,600, so the default guard stops at a batch boundary
  and leaves several hundred items with no English at all. The default is
  deliberately not raised: it is a runaway guard, and the right size for it
  is a decision about a specific run.
- **Pass `--limit 1000000` too.** `--limit` is per source and defaults to
  40,000, which is not the whole corpus.
- **Expect it to be slow and silent.** Section 1's last entry has the
  measured throughput and the arithmetic. The script prints the estimate
  before the wait starts.

**What the run itself produces, beyond a bank:** the per-topic counts that
tell 2.4 and 2.5 which topics the corpus genuinely cannot fill. Read the run
report for that before starting either.

### 6.2 Then, in order

1. **2.6, schedule the monthly translation job.** One `schtasks /Create`
   line, per `docs/monthly-translation-job.md`. Until that task exists,
   nothing runs and the corpus does not progress. Cheapest item on this list
   by a wide margin.
2. **2.2, run `scripts/eval_gloss_adversarial.py`.** Needs a key. It is the
   only measurement standing under the gloss relaxation the whole verifier
   now depends on.
3. **2.2b, run the gloss purge**, then re-gloss, for 5.3's sake. No longer
   blocks a pilot.
4. **2.3, run `scripts/eval_verifier.py` with a key**, for the verifier's own
   recall and false-positive rate.
5. ~~**2.3b, run the cost-log repair** against the real August log~~. Done
   2026-08-27, exact match to the bill; see cycle 16 in the fix log. What
   remains under 2.3b is the monthly reconcile.
6. **2.4, the AI generation pilot**, on the topic list 6.1 produces. Still
   open (see 2.4), still last.
7. **2.5, write down the split** once 2.4 has run.
