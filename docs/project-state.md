# Project state, 28 August 2026

Written for the next person to work on this repository. It says what the
project is, how it works, what is built, what is not, and what will trip you
up.

Read `README.md` first for setup and where things live. Read `CLAUDE.md` for
the rules you have to follow while working here. Open work is in `TODO.md`.

---

## What this is

A German grammar trainer, A1 to B2. The learner reads a German sentence with
one word missing and types the missing word.

Exercises from different grammar topics are shown back to back, on purpose.
Ten dative questions in a row is easier and teaches less. Mixed topics force
the learner to work out which rule applies before applying it. That work is
the product.

Which topic comes up when is scheduled by spaced repetition. The scheduler is
FSRS, and it schedules grammar topics, not individual sentences.

The full design argument is in `docs/plan/german-grammar-app-plan.md`. It is
already argued there. Do not re-derive it.

---

## The one rule that shapes everything

**An exercise must never say which grammar point it tests.** No "put this in
the dative". The learner has to work that out from the sentence.

This is CLAUDE.md rule 2. Everything bends around it. It is why the model that
checks finished exercises is not told the topic either, and that is why the
model is blind to a whole family of defect. See "What we know because we
measured it" below.

---

## How an exercise is made

Five steps. No step invents German.

1. **Read a real sentence** from a corpus. Two corpora: Tatoeba (everyday
   sentences) and Leipzig (news prose).
2. **Check the sentence can carry an exercise.** Scraped junk, a headline with
   no main verb, Swiss spelling, a quote that starts mid-sentence: dropped.
3. **Tag it** with spaCy (`de_core_news_sm`), and work out which grammar topic
   the sentence can teach.
4. **Blank one word.** That word is the answer. A short cue in brackets, like
   `(gehen)`, names the word but not the grammar.
5. **Check the finished exercise.** First a chain of deterministic rules, then
   one call to a Gemini model as a backstop.

Every exercise also carries an English translation of the whole sentence. It
is machine translation from Azure Translator's free tier. The learner always
sees it. The checker in step 5 sees it too, and may use it to rule out a
second possible answer. Example: `(können)` alone leaves `kann`, `konnte` and
`könnte` all open, and the English settles which one.

The code is `src/generation/blanking/` (steps 2 to 4) and `src/verification/`
plus `src/generation/blanking/model_verification.py` (step 5).

---

## What is built and working

As of 2026-08-28.

- **The corpus pipeline, end to end.** 450,490 usable sentences from the two
  corpora, counted on the owner's machine. `scripts/step7_corpus_pilot.py`
  runs the whole thing.
- **The verification chain.** Deterministic rules for schema, topic leak,
  morphology, answer uniqueness, vocabulary level and duplicates, plus the one
  model call.
- **Topic coverage.** `data/taxonomy.yaml` has 87 topics. 49 of them have a
  selector, so 49 can be filled from the corpus today.
- **Translation.** Azure Translator free tier, Gemini as fallback, a store on
  disk, and a monthly top-up script that remembers what it has already spent.
- **Cost accounting.** Every API attempt writes a row, failures included. The
  monthly ceiling is enforced in code. `scripts/reconcile_cost_log.py` compares
  the log against Google's own bill.
- **The learning engine.** FSRS over topics, the Kalibrierung diagnostic, a
  synthetic-learner simulation harness.
- **A terminal client** with typo-tolerant grading.
- **A PWA** in `web/`. Offline, installable, IndexedDB, and it renders the
  English translation under the sentence.
- **A Cloudflare Worker** in `worker/` for review-log sync.
- **1788 tests pass.** `ruff check`, `ruff format --check` and
  `mypy --strict src/` are all clean.

---

## What is not built

### No item bank has been built yet

This is the biggest gap and it is not written down anywhere else.

`data/bank.db` holds 84 items left over from development runs. It is not a
bank. The plan is 25 items for each of the 49 topics, about 1,225 items, built
in one run. `docs/building-the-bank.md` is the command sequence, with the cost
and the duration of each step.

Everything downstream waits on this. `web/data/` is a demo export from those
84 items. Nothing in the bank carries an English translation yet, because the
pilot writes its items to a review file and not to the bank.

### The AI-generation path is running in CI and nobody has measured it

There are two ways to make an exercise in this repository. The corpus path
above is the one that is used, measured and audited.

The other one asks a Gemini model to write German sentences, then blanks and
verifies those. It lives in `src/generation/batch_client.py`. It has unit
tests with fake clients, and it is wired into two scheduled workflows:

- `.github/workflows/generate-submit.yml`, daily at 02:00 UTC
- `.github/workflows/generate-ingest.yml`, daily at 08:00 UTC

Both read `GEMINI_FREE_API_KEY` and `GEMINI_PAID_API_KEY` from repository
secrets. With no secrets set they do nothing. Set the secrets, on a fork or
anywhere else, and this path starts generating and spending every night.

Its output has not been audited since the project moved to the corpus path.
The last real measurements of it are the stage 4 pilots of 13 to 15 August
2026 in `docs/audits/`. Nothing in `TODO.md`, `docs/known-defects.md` or the
fix log mentions these workflows at all.

### Smaller gaps

- **Vocabulary FSRS.** Specified, not started. `TODO.md`.
- **Click a word to see it in other sentences.** Specified, not started. It
  needs the translation store finished first.
- **CSS wiring bugs in `web/`.** Three confirmed, all class-name mismatches
  between the markup and `web/styles.css`:
  - `web/app.js` sets `feedback-box correct` and `feedback-box incorrect`;
    the stylesheet defines `.feedback-box.success` and `.feedback-box.error`.
    Right and wrong answers get no colour.
  - `web/app.js` toggles `active` on the hint box; the stylesheet defines
    `.hint-content.visible`, and `.hint-content` is `display: none`. The hint
    never appears.
  - `web/index.html` gives the topic map `class="dag-tree-grid"`; the
    stylesheet defines `.dag-grid`. The grid layout never applies.
- **CLAUDE.md's repository map is slightly stale.** Tests are flat in
  `tests/`, not split into `tests/unit`, `tests/integration` and
  `tests/simulation`. The markers exist; the directories do not.

---

## What we know because we measured it

`docs/known-defects.md` explains every defect class in plain English, with real
examples. Read that for the detail. What follows is only what the numbers mean.

**The model backstop was measured for the first time on 2026-08-28.** Recall
60.5%, false positives 12.9%.

- **The 60.5% understates it, and the fixture is why.** 18 of its 38 records
  are exercises that are correct German with one right answer, and are defects
  only because of which topic they were filed under. The model is never told
  the topic, per the rule above, so it cannot see those at all. On the 20
  records whose defect is visible in what the model is actually shown, it
  caught 15.
- The 14 records that this pass had never screened before were all 14 caught.
  Read that with a caveat: 9 of the 14 are quoted as sentence fragments rather
  than whole sentences, and a fragment gets rejected on its form whatever it
  was filed for.
- **The 15 misses do not reach a learner.** They fall into five families, every
  one of which already has a deterministic rule that runs earlier. Four of the
  five were verified closed by running the shipped code against the fixture's
  own sentences. The model is a weak second net over a working first net. It is
  a discovery instrument, not a gate.
- The 12.9% is the number that costs money. It throws away roughly one good
  exercise in eight, silently. Nothing says why an item is missing.

**The translation checker was measured the same day.** It catches 22 of 36
deliberately wrong translations, with 0 false positives on 36 correct ones. It
catches **0 of 6** where the translation has the wrong number, singular against
plural.

That last one matters more than the total. The learner sees the translation and
uses it. A translation with the wrong number points at the wrong answer:

    Ich habe schöne ___ gesehen.        answer: Häuser
    "I saw a beautiful house."

**An open question, not a settled one.** `docs/building-the-bank.md` specifies
two verification passes. Two passes reject anything either pass rejects, so
they union their false positives as well as their catches. That decision was
made before the 12.9% existed. Re-read it before you build the bank. `TODO.md`
carries it as an item.

**Free-tier reality.** The verify model gets roughly 25 to 36 requests a day on
Google's free tier, and most come back 503. Verifying a 1,225-item bank is 245
requests for one pass and 490 for two. Neither finishes on the free lane in one
sitting. `docs/building-the-bank.md` has the way to spread a one-pass build
across several days without spending anything.

---

## Money

The recurring budget is **7.50 USD a month**. It is enforced in code, in
`GeminiLlmClient.spend_ceiling_usd`, and pinned by a test. August 2026 spent
5.21 USD of it.

There are two Gemini lanes, backed by two separate Google Cloud projects: a
free one and a billed one. Two projects are mandatory, not an optimisation.
Enabling billing on a project destroys its free tier.

`--free-lane-only` on the eval and pilot scripts guarantees zero spend. It
refuses to start rather than risk billing you.

The English translation is Azure Translator's free F0 tier: 2,000,000
characters a month, no card. Translating the whole corpus at that rate takes
about 14 monthly runs.

**Read CLAUDE.md section 9 before you make any API call.** It has the lane
rules, the model routing, the two kinds of 429, and the caching policy. Do not
work around it.

Because 5.21 of 7.50 is already spent, the 3.28 USD bank build does not fit in
August. September resets it.

---

## What would bite you first

1. **`data/fixtures/translations/de_en.jsonl` is not a test fixture.** The path
   says `fixtures` and it is a lie inherited from a default argument. It is a
   35 MB operational store of about 200,000 machine translations, built a bit
   at a time against a free monthly allowance. It is gitignored, so it exists
   only on the owner's machine. Rebuilding it costs weeks of free tier.
   Deleting it is the most expensive mistake available in this repository.
   Never delete it, never commit it, do not "tidy" `data/fixtures/`.

2. **Set `GEMINI_FREE_API_KEY` to a key from an unbilled project.** If it is
   unset, the code falls back to `GEMINI_API_KEY`. On the owner's machine that
   was the billed key, so every "free" call would have billed. This is why
   `--free-lane-only` refuses to run without `GEMINI_FREE_API_KEY` set
   explicitly.

3. **The nightly workflows spend money on an unmeasured path.** See "What is
   not built". Do not enable repository secrets on a fork without deciding
   about those two workflows first.

4. **The repo root is the working directory.** There is no nested project
   folder. Old documents referring to a `Language_Learning_App/` directory are
   wrong.

5. **`uv sync` installs both spaCy models.** German and English, from pinned
   wheels, pinned in `pyproject.toml`. There is no separate `spacy download`
   step, and adding one would pull a version the pins do not expect.

6. **The corpora are not in the repository.** They are staged by hand at
   `data/raw/_extract/tatoeba_deu.tsv` and
   `data/raw/_extract/leipzig_sample.txt`. If they are missing, the pipeline
   does not fail. It quietly produces fewer items. Check the files are there
   rather than trusting the exit code.

7. **`docs/audits/` is a historical record, not instructions.** Those files
   describe the repository on the day they were written and several are now
   wrong on purpose. Where an audit and `CLAUDE.md` disagree, `CLAUDE.md`
   wins. One of them proposes relaxing rule 2. That proposal was not adopted.

8. **Do not weaken a test to make it pass.** Several constants are pinned by a
   test whose whole job is to make you ask the owner first: the spend ceiling,
   the retry shape, and the decision not to trust Tatoeba's own English. The
   test failing is the feature.
