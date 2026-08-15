# Recovery plan, 15 August 2026

## The headline

**The generator is not the problem. The verification chain is destroying good items.**

Of the 84 rejections in `batch_51fc18e48f7b`, **65 are chain defects, not model
defects.** The items were correct German and were thrown away by bugs and by
over-tight thresholds in our own code.

| Rejection reason | Count | Verdict |
|---|---:|---|
| Proposed answer is present among distractors | 26 | **chain defect.** The sentence is fine. A malformed *distractor list* is not a malformed item |
| Vocabulary ceiling exceeded | 25 | **chain defect.** Broken wordlist, see below |
| Distractor contains invalid non-German characters | 10 | **chain defect.** The "invalid character" is the space. `hat gekauft` is German |
| Duplicate distractors | 4 | **chain defect.** Deduplicate and move on |
| Missing or multiple gap placeholder | 5 | genuine |
| No governing element narrows the gap | 4 | genuine, and correctly caught |
| Answer/expansion collides with a distractor | 3 | genuine |
| Form not recognised in paradigm | 2 | genuine |
| Answers span two determiner types / exceed threshold | 2 | genuine |
| Topic leak | 1 | genuine |
| Duplicate sentence in bank | 1 | genuine |
| Prompt length 25 tokens vs limit 24 | 1 | off-by-one on a tight limit |

**16 accepted becomes roughly 75 accepted once the 65 stop being discarded.**
Nothing about the model, the prompt, or the taxonomy has to change to get there.

---

## Fix A: repair the distractor list, do not reject the item

`src/verification/layer3_solver.py:177-187`

Distractors are presentation metadata for multiple choice. They are not part
of the sentence being tested. Rejecting a correct German item because the model
also emitted a sloppy distractor list is throwing away the baby.

Replace both distractor rejections with a repair pass that runs **before**
validation:

1. Drop any distractor whose text equals an accepted answer (case-insensitive).
2. Deduplicate the remainder, preserving order.
3. Only then, if fewer than the required minimum remain, reject with
   `insufficient_distractors`.

Recovers 30 items.

## Fix B: allow spaces and apostrophes in distractors

`src/verification/layer3_solver.py:183`

```python
if not re.match(r"^[A-ZÄÖÜa-zäöüß\-]+$", d.text.strip()):
```

This regex has no space in the character class, so every multi-word distractor
is "non-German": `hat gekauft`, `haben geholfen`, `bist gefahren`,
`gewesen wäre`, `am größten`, `interessiert an`. All of these are correct
German and several are exactly the contrast the item needs.

Multi-word distractors are mandatory for periphrastic tenses, the whole
Konjunktiv II system, superlatives and verb-preposition topics. Banning them
bans the B1/B2 half of the taxonomy.

```python
if not re.match(r"^[A-ZÄÖÜa-zäöüß\-'\s]+$", d.text.strip()):
```

Add a separate length or word-count bound if runaway distractors are the real
worry. Recovers 10 items.

## Fix C: the vocabulary ceiling is measuring the wordlist, not the item

`src/verification/layer1_syntax.py:127-140`, `data/fixtures/corpus/vocab_levels.json`

Three compounding faults:

1. **Absent means "too hard".** A word missing from an 11,633-entry list is
   treated as above ceiling. `Vorstand`, `Projektleiter`, `Analyse` and `These`
   are all simply absent, and all four are ordinary B2 words. No scraped list
   will ever be complete, so absence must mean *unknown*, never *too hard*.
2. **Inflected forms carry their own level.** `liegen` is A1 but `lag` is
   tagged B1, so the past tense of an A1 verb fails an A1 ceiling. The
   lemmatizer never fires because the surface form has a direct hit at the
   wrong level. Look up the **lemma's** level, and let a lemma hit override a
   surface hit.
3. **B2 has 808 lemmas.** Real B2 is several thousand. The ceiling is
   effectively "reject anything interesting".

Changes:

- Absent from the list → pass, and count it. Print the unknown-word rate in the
  pilot summary so the list's coverage is visible instead of silently punitive.
- Lemmatize first; the lemma's level wins.
- Above A2, demote the ceiling from a rejection to a warning recorded on the
  item. A1 and A2 keep the hard gate, which is where a vocabulary ceiling
  actually earns its keep.

Recovers 25 items.

---

## Fix D: un-ban the parenthetical cue

`src/verification/layer1_syntax.py:75-84`

This is my error and it should be reversed.

That check traces to item 7 of the 14 August audit,
`Weil das Haus so alt ist, wohnt dort ___ (Katze) drin.`, which I described as
"a parenthetical cue leaked into the carrier". The diagnosis was wrong. That
item was bad because `dort ... drin` is not idiomatic, and because `(Katze)`
supplied the **noun** while the gap wanted the **article**, so the cue cued the
wrong constituent. The parentheses were never the fault. The rule got hardened
into an unconditional ban, and the ban removed the cheapest and most standard
way to constrain a German cloze item.

`CLAUDE.md` rule 2 forbids naming the **grammar topic**. A lexeme in
parentheses does not name a grammar topic. `Gestern ___ (gehen) ich nach Hause.`
is what every German textbook prints, and it does not tell the learner the word
"Präteritum". The grammar-terminology blocklist already catches the thing rule 2
actually cares about, and it keeps working.

**Decision: delete the unconditional parenthesis rejection.** Replace it with
two narrow checks that encode the real constraint:

1. The parenthetical must contain a **lemma**, not an inflected form matching
   the answer. `(gehen)` passes; `(ging)` is an answer leak and fails.
2. The parenthetical must be the constituent the gap tests. If the gap takes an
   article, the cue may not be the noun. This is checkable against the topic's
   `morph_spec`, and it is exactly what item 7 got wrong.

**Consequence for the fix-5 branch:** most of the bucket 2 machinery collapses.
Twelve topics needed `cloze_cued` as a separate type with a separate `cue`
field precisely because the natural rendering was banned. Keep the branch, keep
buckets 1 and 3 (the discourse and transformation topics genuinely need what
they were given), but bucket 2 is now "print the lemma in parentheses" rather
than a distinct type with its own plumbing. Do not author `forcing_element` for
61 topics until after the pilot below is re-run; a large part of that work was
compensating for this ban.

---

## Fix E: stop gating the product on the content pipeline

This is the important one and it is not a code change.

The thesis of this project is **interleaved practice over grammar topics with
FSRS scheduling**. That thesis is testable at roughly 150 to 200 items. It does
not need 1,000, and it does not need every topic. The bank holds 34 items across
10 topics right now, and `scripts/step4_run_app.py` exists.

Stage 7 is the usability gate: two weeks of daily CLI use. **That gate can start
running now, in parallel, on the current bank.** It is measuring the scheduler
and the interleaving, not the breadth of the content. Waiting for a full bank
before touching stage 7 has meant weeks of no signal on the part of the system
that is actually novel.

Concretely: run the app daily starting today. Let the pipeline fill the bank in
the background. If the scheduler turns out to be wrong, that is far more
important to learn now than another decimal place on the acceptance rate.

---

## Order of work

1. **Fixes A and B.** Two functions in one file, maybe 20 lines. Recovers 40 items.
2. **Fix C.** One lookup path plus a summary line. Recovers 25 items.
3. **Fix D.** Delete the ban, add the two narrow checks.
4. **Re-run the pilot.** Expect 70 to 80 accepted out of 100. If it lands below
   60, stop and report rather than tightening anything.
5. **Hand-audit that larger accepted set.** The error rate on it is the real
   stage 4 number. Everything before this has been measuring the filter.
6. Only then revisit `forcing_element` breadth.

## What I got wrong

Three rounds of my recommendations tightened the filter. Acceptance went 46,
then 33, then 16, and I read each drop as evidence the generator was worse than
I thought. It was evidence that the filter was eating more of a roughly constant
supply of decent items.

Verification can only remove quality, never add it. Once the source is around
80% good, every further tightening costs more good items than bad ones. The
chain passed that crossover several fixes ago and I kept going.
