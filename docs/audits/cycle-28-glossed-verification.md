# Cycle 28: the first two-pass verification, hand audited

29 August 2026. Eighty items, every one of them read by hand.

This is the first run in this project's history where **two verification passes
completed over the same items with nothing left unjudged**, so it is the first
direct measurement of what a second pass actually buys.

---

## What was run

```bash
uv run python -m scripts.step7_corpus_pilot --phase b \
    --pool-file data/corpus_candidate_pool.json \
    --no-translate --require-gloss \
    --verification-passes 2 --verification-batch-size 5 \
    --write-bank data/bank.db
```

Eighty of the pool's 1,225 items: the ones whose carrier already had a gloss
this project trusts. The other 1,145 were held back rather than verified,
because Azure's August allowance was spent and verifying an item that cannot
ship yet spends the LLM ceiling twice. `--require-gloss` exists for that.

| | |
|---|---:|
| Items verified | 80 |
| Pass 1 (cache on) | 75 verified, 5 rejected |
| Pass 2 (cache bypassed) | 73 verified, 7 rejected |
| Union (rejected by either) | 73 verified, 7 rejected |
| **Pass disagreements** | **2** |
| Not run | 0 |
| Inserted into the bank | 73 |
| Cost | $0.17 |

---

## 1. The two passes disagree on 2 of 80, and both are real defects

**2 of 80 is 2.5%.** The batch-20-versus-batch-5 experiment recorded in
`scripts/step7_corpus_pilot.py`'s own docstring found 12 disagreements in 475
items, also 2.5%. Two independent experiments, different variables, same
number.

Both disagreements were **accepted by pass 1 and rejected by pass 2**, and both
are genuinely bad items that a single pass would have banked.

### `futur_i`

```
Wenn du das nicht annehmen wirst, ___ ich es jemand Anderem geben.   -> werde
"If you don't accept it, I'll give it to someone else."
```

Two independent faults. `wirst` in a *wenn*-clause is an anglicism; German uses
the present (`annimmst`). And `Anderem` is capitalised where the current
orthography wants `jemand anderem`.

### `zustandspassiv_zeiten`

```
Gestern ___ 15'000 Tickets verkauft.   -> waren
"15,000 tickets were sold yesterday."
```

The model's stated objection is correct: with *gestern* the sentence needs a
Vorgangspassiv (`wurden verkauft`) or a Perfekt, not a Zustandspassiv.

**It also missed a second defect it was not looking for.** `15'000` is Swiss
number formatting, which is defect class 2.14 in `docs/known-defects.md` and is
supposed to be caught deterministically upstream, by a rule, before any model
sees the item. The model rejected this item for an unrelated reason and the
Swiss-formatting rule did not fire at all. That is a rule gap, not a verifier
success, and it is the most actionable finding in this cycle.

### What this settles

When the owner chose two passes both at batch 5, this file's predecessor
flagged an open question: the 12-defect evidence came from comparing two batch
*sizes*, so two passes at one size might sample only the model's run-to-run
noise, which is a smaller effect.

**It is not only noise.** Both disagreements here are real defects that one
pass would have missed. Two passes at batch 5 earns its cost. The open question
is closed.

---

## 2. The other five rejections: four sound, one arguable

| Item | Verdict |
|---|---|
| `Romantischer kann man kaum ___ kommen.` -> `in` | **Sound.** Not German. Also misfiled: `in` is a preposition, not an `infinitiv_mit_zu` construction |
| `Die 2017 ins Leben ___ Kooperation von mit dem UNICEF Korea Committee ...` | **Sound.** `von mit` is corrupt scraped text |
| `Man ___ schon wissen wenn man von Recht spricht, was dies bedeutet.` | **Sound.** Missing comma before the `wenn` clause |
| `Die ___ vor kurzem noch entscheiden, wer eine Kneipe ... betreten konnte.` -> `durfte` | **Sound, and notable.** Rejected because the English gloss says "they", so the plural `durften` is required. That is defect class 2.15, caught by the model reading the gloss rather than by the new deterministic rule |
| `Nachdem die Labortests gemacht ___, probieren wir den Prototypen ... aus.` -> `wurden` | **Arguable.** Prescriptively *nachdem* with a present main clause wants `worden sind`, but `wurden` is common in real usage. The one candidate false positive |

**Estimated false-positive rate: 1 of 7, about 14%.** Consistent with the 12.9%
measured on the golden fixture. Seven rejections is far too small a sample to
claim a rate from; it corroborates rather than measures. `TODO.md` item 2 is
still the real measurement.

---

## 3. The 73 accepted items: all read, none grammatically wrong

Every accepted item was read. **No item is grammatically incorrect**, and the
Azure glosses read accurately throughout. Items 12, 26 to 30, 50 and 59 to 62
are textbook quality.

Three observations, none of them a grammar defect:

**Semantically odd but correct.**

```
Ich habe ___ Sachen gegessen.   -> erstklassige
"I ate first-class things."
```

Correct German, strange sentence. Weak as an exercise, not wrong.

**Register.**

```
Ein guter Hintern findet selbst ___ Bank für sich.   -> eine
```

Grammatically fine, crude in register. See the content-filter decision below.

**Real contact data from news text.**

```
Er ___ gebeten sich bei der Polizei unter der Rufnummer 05271/962-0 zu melden.
```

A real published phone number, carried through from Leipzig news prose. Not a
grammar defect. Covered by the same decision as register, recorded below so it
is a choice rather than an oversight.

---

## 4. Content filtering: settled, no filter

**The owner's decision, 29 August 2026: content filtering is not wanted.**

`docs/known-defects.md` 2.8 recorded that Leipzig is news prose and has turned
up a quote about genocide and a report of an assault, and `TODO.md` carried
"decide whether Leipzig needs a content filter" as an open question. It is now
decided: **no filter is built and none is planned.** The corpus is real news
prose and the exercises are drawn from it as it is.

This also covers register (the `Hintern` item above) and published contact
details appearing in carrier sentences (the phone number above). Both were
surfaced in this audit and neither changes the decision.

---

## 5. What the run also measured

**The gloss consistency check flagged 4 of 80 items**, in measuring mode, so
nothing was rejected for it. That is live data for `TODO.md` item 6, which asks
whether that check should reject, and it is the first such figure taken against
machine translations rather than Tatoeba's.

**The free lane is worth almost nothing for verification.** Of 61 verification
attempts: 29 succeeded on the paid lane, 3 on the free lane, 18 were free-lane
503s and 11 were free-lane daily-quota exhaustion. The free tier contributed
three successful calls before dying. `docs/project-state.md` said to expect
this; it is now measured on a real run.

---

## 6. What to do about it

1. **The Swiss-formatting gap is the actionable finding.** `15'000` reached an
   accepted item and was caught only incidentally. Class 2.14 has a
   deterministic rule that did not fire, and the model is explicitly not a
   backstop for it (`TODO.md`: anything the verifier misses every time becomes a
   deterministic rule). Worth a failing test and a fix before the full run.
2. **Keep two passes at batch 5.** Measured, not assumed.
3. **The false-positive rate still needs its own audit** over a large rejection
   set. `TODO.md` item 2.

`uv run pytest -q`: 1922 passed at the commit this audit describes.
