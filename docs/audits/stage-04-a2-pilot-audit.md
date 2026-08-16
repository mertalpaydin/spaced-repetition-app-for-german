# Stage 4 kill gate: A2 pilot audit

Batch: the `--cefr A2` run. 98 retrieved, 50 accepted, 48 rejected.

## Verdict

**Gate not passed, but for the first time the failure has a single dominant
cause and that cause is one code path.**

Post-verifier error rate on the accepted 50:

- **17 of 50 defective, 34%**, counting every item a teacher would mark wrong.
- **11 of 50, 22%**, counting only indefensible items and excluding the
  over-permissive-but-sound ones.

Threshold is roughly 15%, so both readings fail.

**However: 11 of the 17 defects share one root cause. Fix it and the rate
falls to 6 of 50, or 12%, which passes.** That is the recommendation, and it
is a much smaller job than anything proposed in the last three rounds.

Acceptance itself moved 16 → 50 out of 100. Fixes A to D worked.

## The dominant defect: the accepted-answer set carries more than one form

Eleven items have a correct primary answer and a polluted alternatives list.
The rule established on 15 August still holds: **ten answers are fine when
they all carry ONE form; two answers are fatal when they carry TWO.**

| # | Topic | Prompt | Accepted set | Fault |
|---|---|---|---|---|
| 37 | `nebensatz_indirekte_frage` | Weißt du, wo er ___ (wohnen)? | wohnt, wohnte | Two tenses. Nothing forces past |
| 38 | `nebensatz_indirekte_frage` | Sie möchte wissen, wann das Konzert ___ (beginnen). | beginnt, beginnen wird, begann | Three tenses |
| 39 | `nebensatz_indirekte_frage` | ...wollte wissen, wann der Zug ___. | kommt, kam, ankommt, ankam, abfährt, abfuhr, eintrifft, eintraf, fährt, fuhr | Tense AND lexeme both free |
| 41 | `nebensatz_wenn` | Wenn du Zeit ___, helfen wir dir. | hast, findest, hättest | Indicative and Konjunktiv II mixed. `hättest` needs a subjunctive main clause |
| 42 | `nebensatz_wenn` | Wenn er Hunger hat, ___ er sich eine Suppe. (cue: kochen) | kocht, macht, bestellt, holt, kauft, gönnt | The cue says `kochen` and six other lexemes are accepted. A cued item whose answer set ignores its own cue is self-contradictory |
| 50 | `verben_reflexiv_akk` | Ich wasche ___ jeden Morgen... | mich, mein Gesicht, meine Haare, meine Hände | Only `mich` is reflexive. The rest are ordinary direct objects, so the item stops testing its own topic |
| 9 | `adjektivdeklination_nullartikel` | Weil er ___ Milch trinkt... | frische, immer, gerne, keine, warme, kalte | `immer` and `gerne` are adverbs and do not decline. `keine` is a negative article |
| 3 | `adjektivdeklination_bestimmt` | Das Auto hat das ___ (groß) Fenster. | große, größte | Superlative admitted under a positive cue |
| 12 | `adjektivdeklination_unbestimmt` | Sie hat eine ___ (klein) Katze. | kleine, kleinere | Comparative admitted under a positive cue |
| 16 | `adjektivdeklination_unbestimmt` | Sie half einer ___ (alt) Dame... | alten, älteren | Same |
| 48 | `temporal_praepositionen` | ...arbeitet ___ drei Wochen in unserer Firma. | seit, für | Different meanings. `seit` is duration up to now, `für` is a planned span |

### The mechanism, confirmed in the code

`pipeline.py` lines 311 to 323:

```python
candidate_extras = semantic.additional_accepted_answers
if topic is not None and candidate_extras:
    candidate_extras = AnswerSetExpander.filter_alternatives_by_target_form(
        candidate_extras, item, topic
    )
...
accepted_answers = AnswerSetExpander.expand_answers(item)
for extra in candidate_extras:
    if extra not in accepted_answers:
        accepted_answers.append(extra)
```

`filter_alternatives_by_target_form` is applied **only to
`candidate_extras`**, the alternatives layer 5 proposes. The base set on line
320 comes from the generator's own `accepted_answers` field and is **never
filtered by target form at all**.

So the chain carefully vets the answers it adds, and waves through the ones it
was handed. Every item in the table above got its multi-form list straight
from the generator.

### Recommended fix, for the coding agent

Apply `filter_alternatives_by_target_form` to the **whole** accepted set, not
only to the extras. Concretely: derive the facet of the primary
`proposed_answer`, then drop every accepted answer whose facet differs from
it. Keep the primary answer unconditionally, so the filter can never empty the
set.

Two rules that need to hold and currently do not:

1. **A cued item's accepted set must be consistent with its cue.** If
   `cue = kochen`, every accepted answer must be a form of `kochen`. This
   alone kills 42 and half of 39.
2. **When facet derivation returns `Unk`, do not silently accept.** An
   unresolvable facet is why `wohnt`/`wohnte` survived: the filter cannot
   compare two things it cannot analyse. Where the facet is `Unk` and the set
   has more than one member, reject as under-constrained rather than pass.
   That is the honest default and it is the opposite of today's.

Expected effect: 17 defects becomes 6, and 34% becomes 12%.

## The remaining six

| # | Fault | Class |
|---|---|---|
| 35 | `Letzte Woche ___ (fahren) wir mit dem Zug nach München.` filed under `lokal_wohin_woher`, but the gap is the **verb**. It tests Präteritum, not directional prepositions | wrong topic |
| 43 | `Obwohl er gestern sehr müde war, ___ er pünktlich an.` filed under `nebensatz_wenn` but uses `Obwohl`. Also accepts `fuhr`, and `fuhr an` means "pulled away", not "arrived" | wrong topic plus a lexical error |
| 49 | `Die Großmutter schenkt ___ ein Buch.` typed `error_correction`, but there is no incorrect sentence to correct, it is a gap fill. Also accepts `dem`, which is ungrammatical with no following noun | wrong type |
| 47 | `Auf dem Tisch liegen viele Hefte, aber ___ Buch gehört mir.` Hefte then Buch is incoherent, and `kein` reverses the meaning against `aber` | semantic |
| 34 | Near-duplicate of 33: both are `Wir fliegen im Sommer ___ Spanien`, one with a trailing clause | dedup miss |
| 33 | Accepts `Richtung` alongside `nach` | minor lexical |

35, 43 and 49 are all one thing: **nothing verifies that the gap actually
tests the topic it is filed under.** `scripts/check_gold_examples.py` performs
exactly this check for gold examples (the non-`Unk` facet rule). It is not run
against generated items. Extending it to the verification chain is the second
recommendation, and it is worth more than its three items suggest, because a
wrong-topic item corrupts the FSRS signal for two topics at once: the one it
claims and the one it actually drills.

## What is genuinely good

33 of 50 are clean, and the best of them are textbook quality:

- Items 17 to 22, the Wechselpräpositionen. `Der Hund läuft in ___ Garten`
  against `Das Buch liegt auf ___ Tisch`: the verb forces the case, every
  accepted answer carries the same case, and the learner has to reason.
- Items 44 to 46, Präteritum. `Gestern ___ (sein) das Wetter sehr schön.`
  Temporal anchor plus bracketed cue, exactly the shape fix D unblocked.
- Items 26 to 32, `um ... zu` and Konjunktiv II politeness.

**The cued items are the strongest in the set.** Fix D is validated: the
bracketed lemma is doing precisely the constraining work it was supposed to,
and two items were correctly rejected by the new cue-equals-answer rule.

One under-inclusiveness worth noting, not a gate defect but a usability one:
items 29 to 31 accept only `Könntest`/`Könnten` where `Würdest`/`Würden` is
equally correct. That will read as a bug to the learner. It belongs on the
stage 7 list, not this one.

## On the vocabulary ceiling

The coding agent's read is correct on the facts: these words are present in
`vocab_levels.json` at B1 or B2, not absent, so fix C's absent-passes rule
does not reach them.

**Recommendation: do not re-tag the wordlist.** Two reasons. There is no
ground truth to re-tag against, so the exercise has no defensible stopping
point, and it would be the fifth consecutive attempt to fix content quality by
adjusting a filter. More importantly, the levels are mostly *right*: `kaum`,
`weshalb`, `Tatsache`, `Maßnahmen`, `Reformen`, `Ingenieur` and `Gärtner` are
legitimately B1 or B2 in the Goethe and telc lists.

**The error is the zero-tolerance rule, not the data.** Requiring every word
in a carrier to sit at or below the grammar level is stricter than any graded
reader or textbook. A learner practising A2 grammar reads a B1 noun without
difficulty, particularly when that noun is visible and the gap is elsewhere.
This is ordinary i+1.

Two changes instead:

1. **Give the ceiling a budget.** Permit up to two content words at most ONE
   band above the ceiling; reject anything two or more bands above. Mechanical,
   principled, and it requires no re-tagging.
2. **Fix one real category error.** `Bevor`, `Sobald`, `Trotz` and `weshalb`
   are closed-class function words. `FUNCTION_WORDS` holds `obwohl`, `weil`,
   `dass`, `wenn`, `als`, `ob`, `da` and `denn` but omits the rest of the
   subordinating conjunctions and interrogatives. A conjunction failing a
   *vocabulary* ceiling is a category mistake: it is grammar, and it is grammar
   this application exists to teach. Extend the closed-class set with `bevor`,
   `sobald`, `nachdem`, `während`, `bis`, `damit`, `seitdem`, `falls`,
   `sodass`, `indem`, `weshalb`, `wobei`, `trotz`, `wegen`, `statt`.

Separately, the A2 prompt-length limit of 18 tokens rejected four items at 20
to 25 tokens. Several accepted items in the clean set run past 18 and are
among the best in the batch (item 6, item 48). Raise it to roughly 25 for A2.

## Recommended order

1. **Filter the whole accepted set by target form**, plus the cue-consistency
   rule and the `Unk`-rejects default. This is the gate.
2. **Assert the gap tests the filed topic** on generated items, reusing
   `check_gold_examples.py`'s non-`Unk` facet rule.
3. Vocabulary budget, closed-class extension, prompt-length limit. Cheap, and
   worth roughly 20 more accepted items per 100.
4. Re-run the A2 pilot and re-audit. Expect 65 to 70 accepted at a defect rate
   near 10%.

## Start stage 7 now, in parallel

**33 audited-clean A2 items exist today**, on top of the 34 already banked.
That is enough to begin daily use and start measuring the scheduler, which is
the part of this project that is actually novel and the part that still has no
evidence behind it.

Nothing in step 1 above blocks it. Items already in the bank do not get worse
while the pipeline improves, and the interleaving question does not need a
full bank to produce a useful answer.
