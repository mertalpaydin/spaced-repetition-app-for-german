# Generation track: plan to 200 clean items from 300

## The target

300 candidates per pilot, all levels, no CEFR restriction. At least 200
accepted, which is the 2/3 bar. Zero defective among the accepted. Five
cycles, each one code wave plus one pilot plus one audit.

## What has actually been wrong

Three rounds of work treated this as a filter-tuning problem. It is not. The
root cause is a single design decision made at the very start:

**The model proposes the answer, and the chain tries to falsify it.**

That is unwinnable. Falsifying a grammar claim requires either a rule that
covers the case or a judgment call, and the chain has rules for perhaps half
the taxonomy. Everywhere it has no rule it either waves the item through (the
defects) or rejects on a proxy (the lost good items). Tightening moves the
line between those two failure modes without reducing either.

**The fix is to stop asking the model for the answer.** German inflectional
morphology is a closed system. Given a carrier sentence and the features of
the gap, the correct form is *computable*, not judgeable. Ask the model for
the thing it is good at, a natural sentence, and compute the thing it is bad
at.

That inverts the economics. Today a defect gets through when no rule covers
it. After the inversion, an item can only be generated when a rule covers it,
so an uncovered case produces no item rather than a wrong one. Zero defects
becomes reachable because correctness is derived rather than estimated.

## Rule changes to the planning documents

These are the restrictions I am removing, with the reason each was wrong.

### 1. The topic-naming ban

You are right that this was a mistake, and I want to state the corrected rule
precisely, because the original was not merely too strict, it was aimed at the
wrong thing.

**Old rule:** the prompt must never name the grammar topic.

**New rule:** the prompt must never contain the answer, or anything the
learner could copy instead of produce. The prompt MAY disclose the task.

Naming "Präteritum" does not tell a learner that the answer to `(gehen)` is
`ging`. They still have to know the ablaut. The original rule confused
disclosing the *category* with disclosing the *form*, and only the second one
destroys the exercise.

### 2. How to disambiguate, and why the choice matters

Your example is exactly right and cannot be solved any other way:

> Weißt du, wo er ___ (wohnen)?

Nothing in that sentence chooses between `wohnt` and `wohnte`. You proposed
two solutions. **I recommend the English gloss, not the tense label, and the
reason is the core thesis of your project.**

Interleaving works because the learner must decide *which rule applies* before
applying it. That decision is the entire benefit over topic-by-topic drilling.

- `Präteritum: Weißt du, wo er ___ (wohnen)?` makes the decision for them. It
  reinstates the topic-labelled textbook this project exists to escape,
  one item at a time.
- `Weißt du, wo er ___ (wohnen)?` / *Do you know where he lives?* supplies
  the **meaning** and leaves the learner to map meaning to category to form.
  That mapping is the discrimination interleaving trains.

So the gloss preserves the mechanism and the label quietly dismantles it.

**Decision: an English gloss is the default disambiguator.** A category label
is a fallback used only where English does not mark the distinction at all,
which is a short list: `du` versus `Sie`, and some case contrasts English
collapses. For those, a minimal task tag is permitted.

The gloss has a second payoff. You mentioned wanting to extend the scheduler
to vocabulary later. A German sentence with a verified English gloss is
exactly the raw material a vocabulary scheduler needs, so this is not a
detour.

### 3. Vocabulary ceiling

Zero tolerance becomes a budget: up to two content words at most one band
above the ceiling, reject anything two or more bands above. Closed-class
function words are exempt entirely, because a conjunction is grammar, not
vocabulary.

### 4. Smaller ones

- Prompt length at A2 goes from 18 tokens to 30. Several of the best items in
  the last audit were rejected at 20 to 25.
- Distractors become optional for typed-answer items rather than exactly
  three.
- `cloze_free` stops being the default shape. A bracketed cue is normal.

## Topic triage

87 topics. They do not all admit the same treatment, and pretending otherwise
is what produced the semantic defects in every audit so far.

**Class A, computable from a paradigm. Roughly 47 topics** (every one carrying
a `morph_spec`, plus the infinitive topics). Article and adjective declension,
all case topics, verb conjugation across every tense, Konjunktiv, passive,
comparatives, reflexives, relative pronouns, participles. Given the gap's
case, gender, number, person and tense, exactly one form is correct and a
table produces it.

**Class B, closed-list lexical. Roughly 8 topics.** The three
`*_feste_praepositionen`, the two-part connectors, `konnektoren_je_desto`,
`pronominaladverbien_da_wo`. Not a paradigm but still a finite table:
`interessiert an`, `stolz auf`. Computable once the table exists, and the
table is a known quantity, not an open-ended research task.

**Class C, structural. Roughly 12 topics.** `satzbau_*`, the `nebensatz_*`
family where the test is verb-final placement, `passiversatz_*`,
`nominalstil_verbalstil`. The answer is a position, and a position is
checkable without semantics.

**Class D, genuinely semantic. Roughly 15 topics.**
`diskurs_adverbialanschluss`, `funktionsverbgefuege`, `modalpartikeln`, the
two `modalverben_subjektiv_*`, `nebensatz_sodass_deshalb`,
`nebensatz_obwohl_trotzdem`, `konjunktionen_position_0`,
`temporal_praepositionen`, `lokal_wohin_woher` and neighbours.

**Every semantic defect in every audit has come from Class D.** There is no
computable answer for "is `deshalb` or `demnach` right here", because it
depends on the argument structure of the sentence.

**I want to be straight with you about this: zero defects is reachable for
Classes A, B and C, and it is not reachable for Class D by generation.** No
amount of cycles changes that, because the property being checked is not
mechanically checkable. Class D needs curated items, written or vetted once by
a human and reused, not generated fresh.

**Recommendation: quarantine Class D out of the pilot pool from cycle 1.** 72
topics is more than enough taxonomy to prove the generator and to run an
interleaved scheduler. Class D becomes a separate, small, hand-curated track
later. If I leave it in, every pilot carries a defect floor of roughly 5% that
no code fix will move, and you will have spent five cycles discovering that.

## The five cycles

Each cycle: one code wave, all tests green before commit, one-line commit
messages matching the existing history, one 300-item pilot, one audit by me.

### Cycle 1: close the known defects, quarantine Class D

- Filter the **whole** accepted-answer set by target form. Today
  `pipeline.py:320` filters only the alternatives layer 5 proposes and passes
  the generator's own list through untouched. This is the single largest
  defect source in the last audit, 11 of 17.
- A cued item's accepted set must consist of forms of its cue.
- When facet derivation returns `Unk` and the set has more than one member,
  reject as under-constrained. Today it passes, which is how `wohnt`/`wohnte`
  survived.
- Assert the gap actually tests the filed topic, reusing the non-`Unk` facet
  rule already in `scripts/check_gold_examples.py`. Catches the three
  wrong-topic items.
- Vocabulary budget, closed-class exemption, prompt length.

**Exit:** 300 candidates, at least 180 accepted, defect rate at or under 8%.

### Cycle 2: computed answers for Class A

The architectural change, and the one that decides whether this works.

- The generation contract changes. The model returns a carrier sentence, the
  gap position, and the **features** of the gap (case, gender, number, person,
  tense, determiner type) plus the lemma. It no longer supplies the authoritative
  answer.
- The system computes the target form and the complete accepted set from the
  paradigm tables already in `layer2_morphology.py`.
- The model's own proposed answer is kept as a **cross-check**. If the computed
  form and the proposed form disagree, reject the item and log it. Disagreement
  means either the model misanalysed its own sentence or our table is wrong,
  and both are worth seeing.
- An uncoverable case yields no item instead of an unverified one.

**Exit:** at least 200 accepted, zero defects among Class A items on a full
audit of that subset.

### Cycle 3: the gloss

- Add `gloss_en` to the item contract. Required whenever the computed accepted
  set has more than one member, or the topic is tense-selecting.
- Mechanical consistency check: the gloss's tense and person markers must agree
  with the computed features of the target. We already have those features
  from cycle 2, so this is an assertion, not a judgment.
- Category tag as the narrow fallback for distinctions English does not mark.
- Re-admit the topics that were only failing for ambiguity.

**Exit:** at least 200 accepted, zero defects, and the ambiguity rejection
class gone from the breakdown.

### Cycle 4: Class B tables and Class C structure

- Build the fixed-preposition tables and the correlative-pair table, then
  compute those answers the same way as Class A.
- Structural verification for the word-order topics: assert the finite verb
  lands in the position the topic claims to test.

**Exit:** at least 200 accepted, zero defects, Classes A through C all live.

### Cycle 5: vocabulary ground truth, and reserve

Held deliberately in reserve for whatever cycles 1 to 4 surface, plus the
vocabulary work.

**On the wordlist, one finding worth acting on:** B2 holds 808 lemmas against
3460 for A1, and the reason is that Goethe publishes official wordlists only
through B1. There is no official B2 list to scrape, so the B2 band was never
populated from a real source. That is why the ceiling collapses exactly where
the interesting grammar lives.

Above B1 the ceiling should be **frequency-derived rather than list-derived**:
take a large public German frequency list and define B2 as the next band of
lemmas by rank after B1. `src/lexicon/frequency.py` already has a
`FrequencyBander`. Candidate sources to verify before use: the Leipzig Corpora
collection, DWDS, and the telc B2 list if it is publicly published. Frequency
rank is also precisely the ordering a vocabulary scheduler would use, so this
serves the extension you mentioned rather than being throwaway work.

**Exit:** the full target. 300 in, 200 or more accepted, zero defects on a
100% audit of the accepted set.

## How "zero defects" gets measured

Sampling cannot prove zero. In cycles 1 to 4 I audit a stratified sample of 80
covering every topic family. **In cycle 5 I audit all 200 by hand**, which is
four times the 50 I did last time and is the only thing that can actually
support the claim.

One honest caveat. I am a single auditor, and `CLAUDE.md` section 10 asks for
cross-vendor confirmation precisely because same-model agreement is weak
evidence. A zero-defect claim resting on my judgment alone is softer than it
sounds. I would like a second independent pass on the final 200, either a
different model or a spot check by you on a random 20.

## Working arrangement

The blocker on me managing coding agents directly is that your `.venv` is a
Windows environment and the device bridge sees it through a Linux VM, so I
cannot run your test suite from here.

**Proposal: I clone the repo into my own container for these five cycles.**
There I can create a real 3.12 environment, let agents run `pytest`, `ruff`
and `mypy` on every change, and only hand back work that is already green. I
sync the tree back to your machine at the end of each cycle for you to inspect
and to run the live pilot.

I will verify this is workable before cycle 1 starts. If it is not, the
fallback is that I write a precise work order per cycle and your local coding
agent executes it, which is what has been happening, just with tighter
specifications.

Commit rules for every agent, non-negotiable: one-line messages matching the
existing history, no body, no trailers; no commit until the full suite, ruff
and mypy are clean; explicit paths on every `git add`, never `-A`.

## What I need from you

1. Agreement to quarantine Class D. This is the one item where I am asking you
   to accept a smaller scope than you asked for, and I would rather argue it
   now than discover it in cycle 5.
2. Agreement that the English gloss, not the category label, is the default
   disambiguator.
3. A yes or no on cloning the repo into my container.
