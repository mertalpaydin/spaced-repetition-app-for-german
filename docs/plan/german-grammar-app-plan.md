# Interleaved German Grammar Trainer: Project Plan

> **STALE as of 2026-09-08.** This describes the German grammar trainer, which was shut down and pruned on `feat/phrase-deck`. Kept for the reasoning only. The current project is the phrase trainer; read `docs/project-state.md`.
>
> **Design record, written before most of the code existed. Not a description
> of what the code does now.** The product argument still holds; the numbers and
> the mechanics have moved.
>
> The corpus pipeline that makes every exercise today, and the English
> translation shown under every one, are absent from this document entirely:
> neither "blank" nor "Azure" appears in it. `docs/project-state.md` has both.
>
> Named departures: the recurring budget here is 5 EUR/month, and the ceiling
> enforced in code is 7.50 USD; the cold-start estimate of 11 to 15 EUR is
> superseded by a measured 3.28 USD in `docs/building-the-bank.md`. The stage
> documents' 89 Definition-of-Done boxes are all unticked, which means nobody
> ticked them, not that the work is undone.

**Scope:** German only, A1 to B2. Personal daily-use tool, built to also serve as a portfolio project.
**Budget:** hosting 0 EUR/month (target ceiling 1-2 EUR). LLM spend up to 5 EUR/month recurring, plus a one-time bank build in the low tens of euros.
**Platform:** installable PWA. Chrome is the primary target; built Safari-safe throughout.

---

## 1. Core Thesis

Standard language apps practise **blocked**: a set of Dativ questions, then a set of Perfekt questions. The learner knows which rule applies before reading the sentence, so they never practise the hardest part of grammar, which is *selecting* the right rule under uncertainty.

This app practises **interleaved**: single-topic items drawn from different topics presented back to back, scheduled by spaced repetition over grammar topics rather than over flashcards.

Two consequences drive the whole design:

1. **Items must never announce their topic.** No "Setze ins Dativ" instruction line. If the prompt names the grammar point, the interleaving effect is destroyed and the app degenerates into a blocked trainer with shuffled pages.
2. **AI is justified by item supply, not by cleverness.** A fixed deck teaches you the answer to card 47, not the rule. Fresh, interchangeable items per topic is the thing a static deck structurally cannot provide.

### Design principles

- Progress is arithmetic, computed in code. An LLM may narrate the scorecard for the user but must never be its source of truth.
- One item, one topic tag, one clean pass/fail signal. No compound attribution.
- Zero API calls on the critical path of answering an item.
- The bank's shape follows the user's demand, not a guessed distribution.
- Nothing gets built on top of a component whose error rate has not been measured.

### Known competition

Cornelsen's *Grammatik aktiv* app already covers 70 grammar topics across A1 to B1 with AI-driven adaptive learning paths. This is not a reason to stop, but it is a reason to be precise about differentiation: interleaving discipline, error-correction items, error-specific diagnostic explanations, and interference-targeted minimal pairs. Position honestly.

---

## 2. Grammar Topic Taxonomy

### Sources (all free)

| Source | URL | Use |
|---|---|---|
| Goethe-Institut Prüfungsziele / Testbeschreibung, one PDF per level (A1, A2, B1, B2) | goethe.de | **Primary.** Each contains an inventory giving the complete grammar, vocabulary and language functions for that level. A CEFR-tagged grammar taxonomy published by the exam authority. |
| BAMF Rahmencurriculum für Integrationskurse | bamf.de | Cross-check and teaching-order reference. Developed by the Goethe-Institut with LMU Munich and FSU Jena for the Federal Ministry of the Interior. |
| grammis (Leibniz-Institut für Deutsche Sprache) | grammis.ids-mannheim.de | Reference for writing topic definitions and explanations. Nearly 1000 detailed texts on the IDS grammar plus a systematic terminology taxonomy. Not CEFR-levelled, not openly licensed for bulk reuse. Read it, do not scrape it. |
| Goethe wordlists (A1, A2, B1) | goethe.de | Vocabulary ceiling per level. B2 has no equivalent official list; fall back on Leipzig frequency banding. |
| Leipzig Corpora Collection frequency lists | wortschatz.uni-leipzig.de | Frequency banding for vocabulary control. Check terms, parts lean non-commercial. |
| Tatoeba | tatoeba.org | CC-BY DE/EN sentence pairs. Seed material so generated sentences read naturally. |
| Falko-MERLIN GEC corpus | HF `matejklemen/falko_merlin` | ~24,000 learner sentences A1-C2 with corrections and ERRANT edit tags. Source for error-correction items and for deriving confusion groups empirically. Contains edit tags, **not** grammar topics: mapping is manual and lossy. |
| MERLIN | merlin-platform.eu | ~1,000 CEFR-rated German learner texts, CC BY-SA 4.0. |
| ERRANT-German | github.com/adrianeboyd/errant (branch `german`) | Automatic German error tagging on a spaCy pipeline. Reusable in the morphology verifier. |
| schubert-verlag.de, mein-deutschbuch.de | see §13 | Human-authored exercises A1-C2. Scraped for gold examples and as a distribution baseline, not for bulk supply. |
| Wiktionary via Wiktextract | kaikki.org | Inflection tables for answer validation. CC BY-SA, share-alike obligations apply. |

### Method

1. Extract the Grammatik inventory from the Goethe A1, A2, B1 and B2 PDFs.
2. Deduplicate across levels, normalise naming.
3. Derive the prerequisite edges (Kasus before Adjektivendungen, Nebensatz before Relativsatz, and so on). **[AGENT-CRITICAL]**
4. Assign confusion groups. **[AGENT-CRITICAL]**

Expect 75 to 90 topics across A1 to B2. Store as YAML in the repo; it is the spine of the entire system.

**Note on A1.** Including A1 adds perhaps 20 topics that most learners past absolute beginner already control. They are not wasted: they are the foundation of the prerequisite DAG, which is what keeps the Kalibrierung short despite the larger taxonomy (see §7). Most will be marked acquired by inference rather than by direct testing.

### Topic record

```yaml
- id: dativ_nach_praeposition
  name_de: Dativ nach Wechselpräposition (Ort)
  cefr: A2
  prereqs: [kasus_dativ_formen, artikel_bestimmt]
  confusion_group: kasus_wechselpraeposition
  description: |
    Wechselpräpositionen governing Dativ when the phrase answers "wo",
    describing static position rather than directed movement.
```

**Confusion groups matter more than they look.** Random interleaving captures maybe half the available benefit. The effect is strongest when consecutive items are discriminable but confusable. Worth defining at minimum:

- `kasus_wechselpraeposition`: Akkusativ vs Dativ after in, an, auf, über, unter, vor, hinter, neben, zwischen
- `vergangenheit`: Perfekt vs Präteritum vs Plusquamperfekt
- `konjunktiv`: Konjunktiv I vs Konjunktiv II vs Indikativ
- `temporal_praep`: seit / vor / für / ab / in
- `perfekt_hilfsverb`: haben vs sein
- `nebensatz_konnektoren`: weil / denn / da / obwohl / trotzdem
- `relativpronomen`: case and gender selection
- `artikelwoerter`: der/ein/kein/mein declension (A1-heavy, but a lifelong error source)

---

## 3. Data Model

```sql
topic(id, name_de, cefr, prereq_ids[], confusion_group, description)

item(
  id, type, dimension,        -- dimension: 'grammar' | 'vocab'
  tag_id,                     -- topic.id or lemma.id, exactly one
  cefr, difficulty,           -- difficulty: 1-3 within the topic
  prompt, context_before, context_after,
  accepted_answers[],         -- array, never a single string
  cue,                        -- e.g. infinitive shown in brackets
  carrier_lemmas[],           -- content words in the sentence
  domain,                     -- optional interest tag for personalisation
  source_sentence_id,         -- Tatoeba provenance
  quality_flags[]
)

review_log(item_id, tag_id, ts, correct, response_ms, user_answer)

tag_state(
  tag_id, dimension, state,   -- state: unseen | learning | acquired
  acquired_via,               -- kalibrierung | inferred | earned | null
  fsrs_stability, fsrs_difficulty, due_at,
  attempts, correct, introduced_at, consecutive_failures
)

seen_items(item_id, ts)       -- prevents item repetition
```

Notes:

- `tag_id` is singular and `dimension` unifies grammar and vocabulary into one scheduler. No second SRS engine.
- `accepted_answers` is always an array. The most common failure mode in generated exercises is marking a valid answer wrong.
- `seen_items` is not optional. A repeated sentence measures recall of the sentence, not of the rule, which is the exact failure this project exists to avoid.
- `difficulty` enables escalation within a topic as stability grows, rather than treating a topic as binary.

---

## 4. Exercise Types

| Type | Grading | Notes |
|---|---|---|
| Free-text cloze | Code | Type the missing form. |
| Cued cloze | Code | Base form shown: `Er ___ (gehen) gestern nach Hause.` |
| Error correction | Code | One error in the sentence, find and fix. |
| Transformation | Code, template match | Aktiv to Passiv, two sentences to Relativsatz, Präsens to Perfekt. |
| Paragraph cloze | Code | 3 to 5 free-text gaps in a short coherent text, each tagged to one topic. |
| Short production | LLM | EN to DE, or answer a question in German. |

### Why there is no global share table

An earlier draft assigned each type a fixed percentage of the bank. That was incoherent, because **type eligibility is a property of the topic, not a global quota.**

Konjunktiv I cannot be tested honestly in a single sentence: indirect speech needs a reporting context to exist at all. So 100% of its items are paragraph cloze regardless of what a global 15% claims. The same holds for connectors referring back to a previous sentence (deshalb, trotzdem, allerdings, dennoch), da-compounds and pronominal reference, Plusquamperfekt, and Nominalstil and register at B2. Roughly 8 to 12 topics out of 85.

Two separate constraints, deliberately not merged:

**Eligibility, per topic, hard.** Each topic declares `eligible_types`, plus `requires_context: true` where a single sentence cannot test it honestly. Never overridden. An item type that cannot test a topic honestly is not a stylistic choice.

**Pacing, per round, soft.** See §8 for what a round is. At most one heavy element per round, where heavy means a paragraph block or a production item.

The global distribution is then **emergent, not a target**, exactly as with the CEFR curve in §6. If paragraph cloze lands at 22% of gaps, that is a fact about the taxonomy rather than a miss.

On conflict, eligibility wins and the scheduler smooths across days: four context-requiring topics coming due together are spread, not stuffed into one round.

**A paragraph block is a carrier for a mix.** One Konjunktiv I gap rides along with three ordinary ones, so far fewer blocks are needed than "12% of topics" suggests, and interleaving happens inside the block because the gaps carry different tags. One paragraph read yields four or five SRS signals instead of one.

**Paragraph cloze stays free text, not multiple choice.** If telc is chosen later, converting to Sprachbausteine is trivial: add three distractors per gap and switch the input. Converting the other way loses information.

### Hints: a ladder, not partial credit

Prompts never name the topic, which is the product thesis. Without an escape hatch that makes some items unanswerable and the app quit-inducing.

First, a distinction that matters: **an item with no determinate answer is broken, not hard.** Answer-set expansion should have caught it. Do not paper over defective items with hints.

For genuinely hard but valid items, a graduated ladder. **Multiple choice is a hint level, not an item type**, so free text stays the default and MC is the escape hatch rather than the format that lets you eliminate your way through everything.

| Level | What you get | FSRS rating if then correct |
|---|---|---|
| 0 | Nothing | Good, or Easy if fast |
| 1 | Shape: word count, first letter | Hard |
| 2 | Three options | Hard |
| 3 | The rule, stated | **Again** |
| 4 | Answer revealed | **Again** |

**No partial credit reaches FSRS.** Half points feel right and are mechanically wrong: FSRS takes Again/Hard/Good/Easy and those ratings set interval length, so partial credit inflates stability, a topic you can only do with help returns too late, and the dependency never surfaces. If you needed the rule stated, you did not retrieve it. That is Again.

Two mechanisms recover what partial credit was reaching for:

- **Hint dependency is tracked separately from accuracy.** A topic always answered correctly at hint level 2 is not acquired. Promotion to `acquired` requires consecutive unhinted passes, and hint dependency appears in stats as its own number.
- **Hints unlock on a delay**, after a few seconds or one wrong attempt. An instantly available button gets tapped reflexively, and the retrieval effort is what makes the exercise work.

**Free text over multiple choice.** MC tests recognition and permits elimination. Typing forces retrieval, which is both a stronger memory operation and closer to production.

**Error correction deserves its 20%.** It is the only type that structurally cannot announce its topic, making it the purest expression of the interleaving thesis. It also mirrors the actual target skill: noticing that something is off.

**Production is bounded but never zero.** Cloze practice transfers weakly to speaking. An app that is only gap-filling will make you measurably better at gap-filling. Target roughly one production item every second round.

### Grading rules

- **Do not lowercase before comparison.** Capitalisation is grammatically meaningful in German and is itself worth testing.
- **Typo tolerance is scoped.** Allow edit distance in the parts of the word outside the tested morpheme; treat the tested ending as exact. `dem Mnn` is a typo. `den Mann` where Dativ is required is a fail.
- **Umlauts:** accept `ae` / `oe` / `ue` / `ss` as equivalent, and provide ä ö ü ß buttons above the input.
- Grade against `accepted_answers`, not a single string.

---

## 5. Generation Pipeline

### Per-topic spec sheet, not a prompt

Each topic gets a spec file containing: topic ID, CEFR, target form, distractor policy, maximum sentence length, vocabulary ceiling, difficulty-tier definitions, an explicit prohibition on naming or hinting at the grammar point, and three gold examples **[AGENT-CRITICAL]**.

Generate 20 items per call with structured output. Use batch pricing for anything not user-facing.

### Verification chain

This is where the real engineering sits. Every stage runs at ingest, never at runtime.

1. **Schema and format validation.** Cheap reject.
2. **Answer-set expansion.** Separate call: sentence with the gap masked, asked to list every grammatically acceptable filler. Union into `accepted_answers`. This is the single highest-value check.
3. **Topic-leak check.** Reject any item whose prompt contains grammatical terminology or otherwise signals which rule is being tested. Automatable with a term blocklist plus a model pass.
4. **Morphology check.** spaCy `de_core_news_lg` or HanTa. Does the expected answer actually carry the case, number and tense the topic claims?
5. **Level check.** Every content word inside the Goethe wordlist for that level, cross-checked against a Leipzig frequency band.
6. **Deduplication.** Embedding similarity against the existing bank; drop near-duplicates.
7. **Independent audit.** 5% per topic, second agent, external keys where they exist.

### Kill criterion

After the verification chain, audit 100 items from one topic family and compute the error rate. **[AGENT-CRITICAL]**: two independent agents, both scores recorded and never averaged. **If post-verifier error rate exceeds roughly 15%, stop and rework the pipeline before building any UI.**

Publish this number per topic family in the repo README. It is the most credible artefact the project will produce, and it is the part a hiring manager should read first.

---

## 6. Generation Economics: Demand-Driven, Not Curve-Driven

The instinct to shape the bank like a normal curve centred on the user's level is directionally right but implemented the wrong way round. **A curve is a static guess about a moving target.** The frontier advances as topics are acquired, and a topic believed acquired can collapse without warning and suddenly need forty items. Rather than assume a distribution, derive it.

### Three stages

**Stage A: cold seed (one-time, before first use).**
12 items per topic across all A1 to B2, uniformly. Roughly 90 × 12 ≈ 1,100 items. Enough to run the Kalibrierung and cover the first two weeks. Deliberately uniform, because before the Kalibrierung you know nothing about the user and any curve you impose is a guess.

**Stage B: frontier fill (one-time, immediately after Kalibrierung).**
Once `tag_state` is populated, identify the **frontier band**: topics in `learning`, plus `unseen` topics whose prerequisites are all acquired, plus acquired topics that were only weakly confirmed. Typically 15 to 25 topics. Generate 60 to 80 items each, roughly 1,500 items. This is the burst that makes the first month feel deep.

**Stage C: nightly top-up (recurring, the steady state).**
A scheduled GitHub Actions workflow, split into a submit run and an ingest run (see §11). For each tag:

```
projected_demand = items the scheduler will consume in the next 14 days
                   (derived from due_at across the FSRS queue)
stock            = unseen items in bank for this tag
deficit          = projected_demand * safety_factor - stock
if deficit > threshold: generate deficit items at the tier
                        matching current fsrs_stability
```

Typical output is 30 to 80 items per night, concentrated exactly where the user is working.

### Why this is better than a curve

The bank's CEFR distribution ends up bell-shaped and peaked at the user's frontier, which is the intuition, but as an **emergent property rather than an assumption**. It also handles three things a fixed curve cannot:

- The frontier moves; the distribution moves with it, nightly.
- A collapsed "acquired" topic gets depth within 24 hours of collapsing.
- Topics the user never reaches never consume generation budget at all.

### Difficulty escalation

Within a topic, generate at the tier matching current stability rather than treating a topic as binary:

| Tier | Triggered when | Characteristics |
|---|---|---|
| 1 | stability < 7d | short sentence, high-frequency vocabulary, no embedded clause |
| 2 | 7d ≤ stability < 30d | longer sentence, one subordinate clause, mid-frequency vocabulary |
| 3 | stability ≥ 30d | embedded or nested clause, lower-frequency vocabulary, distractor structures present |

This is what stops a mastered topic from becoming trivial review noise.

### Cold-start budget

**Model routing.**

| Workload | Model | Mode | Thinking |
|---|---|---|---|
| Live: explanations, production grading, report narrative | `gemini-3.5-flash-lite` | sync | off |
| Item generation, topic-leak check, nightly top-up | `gemini-3.5-flash-lite` | batch | **off** |
| Answer-set expansion, override verification | `gemini-3.6-flash` | batch | low or medium |

Assumptions: 2,500 items kept, ~3,500 generated to absorb a 30% rejection rate, 20 items per call.

| Step | Model | Input | Output | Cost |
|---|---|---|---|---|
| Generation | 3.5 Flash-Lite batch | 0.26M | 0.26M | $0.36 |
| Spec-driven generation (2,610 items) | 3.5 Flash-Lite batch, thinking OFF | 1.8M | 0.8M | $0.19 |
| Answer-set expansion | 3.7 Flash batch, low thinking | 0.18M | 0.35M | $1.45 |
| **Total bootstrap** | | | | **~$1.65** |

3.7 Flash also returns fewer output tokens than 3.5 Flash for the same work, so the real figure will likely come in under this estimate. Generation lands almost entirely inside free-tier quota, which means realistic paid spend is close to the expansion line on its own.

**Verify these prices before implementing.** The table reflects rates at the time of writing and the Gemini lineup moved repeatedly through 2026: 3.7 Flash launched below 3.5 Flash on output, 2.5 Flash-Lite retires in October, and free-tier quotas were cut sharply in December 2025. The stage 0 agent reads Google's current pricing page and the live AI Studio rate-limit view for both projects, records both in `docs/audits/stage-00-quota.md`, and recommends a swap if a slot is now better filled by another model. Two things worth checking specifically: whether a newer Lite tier is more expensive than the one it replaced, which has happened, and whether the model in a free-tier slot is still free-tier eligible.

Expect eight to ten full runs while tuning spec sheets. **Realistic cold-start total: 11 to 15 EUR**, or nearer 7 to 9 once the local cache absorbs repeat expansion across reruns.

### Two lanes: free synchronous, paid batch

Gemini quota is enforced per Cloud project, not per API key, so extra keys add nothing. Enabling billing on a project also removes its free tier outright, which means two separate projects are mandatory rather than clever.

Free-tier Flash-Lite runs at roughly 15 requests per minute and 1,000 per day, resetting at midnight Pacific. A full pipeline run is around 700 calls, of which generation and the topic-leak check are about 350. **That half fits inside one day of free quota and takes roughly 25 minutes of wall time**, which is irrelevant for an offline pipeline.

| Work | Lane | Rationale |
|---|---|---|
| Generation, topic-leak check | Free project, Flash-Lite, synchronous | High volume, low stakes, fits the daily quota |
| Answer-set expansion | Billed project, Flash, batch | Accuracy-critical, and Flash free quota is far smaller |
| Overflow from either | Billed project, batch | Half price, latency irrelevant |

Two design rules follow:

- **Overflow accumulates rather than failing over per request.** When the free lane closes, queue the remainder and ship it as a single batch. Per-request failover forfeits the batch discount on precisely the requests you pay for.
- **RPM and RPD exhaustion are different events.** RPM means back off and continue free. RPD means close the free lane until the Pacific reset and flush to batch.

Verify limits in the AI Studio rate-limit view for your own project rather than from published tables. Google cut free-tier quotas substantially in December 2025 and the numbers move.

### Caching: local, not provider-side

Provider context caching is the wrong tool here. It discounts input tokens, which are around 15% of the bill, leaves output tokens untouched, and adds an hourly storage charge that can exceed the saving at this volume.

A local content-addressed cache, keyed on a hash of the full request, is where the money is:

- **Development iteration.** Eight to ten pipeline runs produce heavily overlapping candidate sentences. Caching answer-set expansion by hash of (masked sentence, topic) lets repeat runs skip the most expensive step. Expect a 30 to 40% cut in total cold-start spend.
- **Runtime explanations**, keyed on `(item_id, user_answer)`. Wrong answers cluster hard; hit rate passes 80% within weeks.
- **Idempotency.** A rerun after a crash must not regenerate what already landed.

Three things that would break this estimate:

- **Thinking tokens bill as output.** Left enabled on generation they can triple the output line. Disable thinking for generation; keep it for answer-set expansion if it measurably helps recall.
- **Do not use Flash-Lite for answer-set expansion.** That step decides whether the app tells the user a correct answer is wrong. Generate cheap, verify one tier up. It is deliberately the largest line in the table.
- **Free tier covers early iteration.** Flash models are free in AI Studio within rate limits (roughly 1,000 requests/day), and a full run is around 700 calls. Two catches: free-tier content is used to improve Google's products, and batch is not available on it.

### Budget guardrails

- **Nightly item cap** (e.g. 120 items). A bug cannot burn the month's budget overnight.
- **Monthly spend ceiling with a hard stop.** On hitting it, fall back to serving least-recently-seen items from the existing bank. The app degrades gracefully rather than breaking.
- **Batch pricing for all Stage C generation, as a single nightly batch.** One batch request per night covering every topic's deficit, not one call per topic. Not user-facing, so 24-hour latency is irrelevant, and the bank always holds 14 days of stock as buffer.
- **Log cost per call** into a small table so spend is observable rather than a surprise. This chart also belongs in the portfolio README.

---

## 7. Kalibrierung (the initial assessment)

Not a placement test. Its purpose is not to assign a level but to populate `tag_state` so the scheduler knows what is already **acquired**.

### Topic state machine

```
unseen ──(Kalibrierung pass)──────────────→ acquired
unseen ──(Kalibrierung partial)───────────→ learning
unseen ──(Kalibrierung fail / untested)───→ unseen

unseen ──(intro card, then 6-8 blocked items)→ learning
learning ──(3 consecutive unhinted passes, >=2 facets)→ acquired
acquired ──(2 consecutive failures)───────→ learning
```

#### Promotion requires evidence across facets, not a single pass

A grammar topic is not a word. `dativ_nach_praeposition` spans four genders, definite and indefinite articles, and nine prepositions. A single pass tests one cell of that grid, and may only prove a lucky two-way case guess. Vocabulary does not have this problem, because a lemma really is atomic.

**Promotion from `learning` to `acquired` requires 3 consecutive unhinted passes spanning at least 2 distinct facets.**

Consecutive means no failure in between, not that the items appeared back to back. Under interleaving they never will.

Three scoping decisions matter as much as the rule:

**It applies only to our own `learning` to `acquired` transition, never to FSRS.** FSRS has no stages; that is SM-2's model. FSRS fits a continuous stability value per review against very large datasets, and gating those updates behind a hand-written heuristic would override it with a guess. Intervals would also barely grow: three passes at four days each merely to get past four days.

Scoping it here also disposes of the usual objection to consecutive-pass rules. They are brutal at long intervals, where three in a row means months and one lapse resets everything. At the `learning` stage intervals run 1 to 4 days, so three consecutive costs a week or two, and a failure there genuinely does mean not acquired.

**Facets are derived, not hand-written.** `morph_spec` already declares which features a topic fixes. The features it leaves unspecified are exactly the dimensions that vary, so an item's facet is computed at ingest from the answer token's morphology minus the fixed features. No decomposition work for 85 topics.

Topics with an empty `morph_spec` (word order, for instance) have no facets. The requirement degrades explicitly to 3 consecutive unhinted passes, never silently.

**Grammar only.** A lemma is atomic, so the argument does not reach vocabulary. Applying the rule there would triple the work for no evidential gain.

#### Facet diagnostics and evidence-driven splitting

Facets pay for themselves twice. Beyond promotion they give per-cell accuracy, so "you fail feminine dative" becomes visible where topic-level accuracy shows only a middling number.

They also answer the question of whether a topic should have been split in the first place. Full atomisation, one SRS unit per cell, is theoretically cleaner but multiplies unit count roughly fivefold, breaks the independence assumption SRS rests on (knowing "auf dem Tisch" and "auf der Straße" is one rule plus one paradigm, not two memories), quietly weakens interleaving because two cells of one topic feel identical to the learner while carrying different tags, and discards the external validation the Goethe inventories provide.

So split on evidence instead. When a topic reaches enough attempts per facet and the accuracy gap between facets is large, flag it as a split candidate for review. Costs nothing upfront, will apply to perhaps five or ten topics, and each split is justified by data rather than by a guess. Splitting is proposed, never automatic: topic IDs are referenced by user history and must not mutate silently.

#### Splitting a topic: the procedure

Full atomisation up front is rejected for four reasons. Splitting one topic on evidence answers all four, which is why it is the supported path.

| Objection to atomisation | How an evidence-driven split answers it |
|---|---|
| No natural atomic floor; the decomposition is invented | The data names the axis. If masculine and feminine accuracy diverged, the split is on Gender. Nothing is invented. |
| Cells are not independent, so SRS over-reviews | The trigger *is* evidence of independence. A topic only splits once the accuracy gap proves it holds two memories. |
| Children feel identical to the learner, weakening interleaving | Children share a `sibling_group`, and the interleaving rule strengthens to forbid consecutive items sharing a `tag_id` **or** a `sibling_group`. |
| Loses the external validation the Goethe inventories give | Children keep a `derived_from` pointer. Inventory coverage is checked against topic lineages, not leaf IDs, so the coverage test still holds. |

**Split on one axis only.** Two children, or the minimum partition the axis requires. Never the full cross-product: an eight-way split reintroduces exactly the over-review problem the trigger condition was guarding against. A child can be split again later if its own facet data justifies it.

**Sibling separation outranks confusion adjacency.** A `confusion_group` says "place these adjacent, the contrast teaches." A `sibling_group` says "never adjacent, they are one rule at different cells and adjacency is massing." When a split topic also sits in a confusion group, separation wins.

**Nothing is renamed or deleted.** The parent is retained as a lineage node, marked `split_into`, never scheduled and never generated for. Topic IDs are referenced by every generated item and every history row.

**History migrates deterministically, with no estimation.** Every item carries the `facet` computed at ingest, and the split axis is a facet dimension, so each historical review reassigns to the child its item's facet identifies. Child `tag_state` is then recomputed from the reassigned log. This works only because `review_log` is append-only and facets were recorded at ingest; both decisions pay off here.

**The bank migrates the same way.** Existing items retag to a child by facet. No regeneration. Items whose facet does not determine the axis are quarantined for review rather than guessed at.

**Splits are proposed and applied manually**, with a dry run showing the resulting DAG, item counts per child, history rows reassigned, and the projected daily-load delta. The load cost is real: each split adds a unit to the review queue.

**Acquired does not mean excluded.** It means the topic enters the interleaved review pool with a seeded FSRS stability instead of being taught from scratch. An acquired topic still surfaces, just at long intervals, which is exactly what spaced repetition is for.

### FSRS seeding from Kalibrierung

| Kalibrierung result | State | Initial stability | `acquired_via` |
|---|---|---|---|
| 2/2 correct | acquired | **~10 days** | kalibrierung |
| 1/2 correct | learning | ~7 days | null |
| 0/2 correct | unseen | none, needs blocked intro | null |
| Not tested, inferred from DAG | acquired | **4 days, hard ceiling** | inferred |

Items marked `acquired_via: inferred` demote to `learning` on the **first** failure rather than the second. The inference is weaker evidence than a direct pass and the system should not defend it as hard.

**Why 10 days for a 2/2 pass and not 30.** If three consecutive unhinted passes across two facets is the in-app bar for `acquired`, granting a month of stability on two probe items is inconsistent with it. Ten days lets a genuinely acquired topic earn length quickly while surfacing a wrongly-claimed one within a fortnight.

**Why 4 days and not 14.** Adult L2 competence is fragmented in ways the DAG cannot see. A learner can produce relative clauses from memorised sentence frames while failing basic article declension, so passing `relativsatz_genitiv` is weak evidence about `kasus_dativ_formen`. A 14-day inferred stability produces demotion loops: the topic surfaces late, fails, demotes, gets reintroduced, and the learner spends weeks bouncing. Four days makes the inference cheap to disprove.

No separate `inferred_acquired` state is introduced. `acquired_via` already carries the distinction, and a fourth state would add a branch to every scheduler path for no information gain. Nor is there a special promotion rule: FSRS runs normally from the short stability, and a topic that keeps passing earns length on its own.

### Keeping it short despite 90 topics

Do not test 90 topics. Target 25 to 30 items total, using the prerequisite DAG to do the rest of the work:

1. **Start mid-taxonomy**, around B1, not at A1.
2. **Pass propagates downward.** Correctly forming a Relativsatz with Genitiv implies control of Relativpronomen, Kasus and Artikelwörter. Mark those acquired by inference.
3. **Fail propagates upward as suspicion.** Failing Kasus Dativ means everything depending on it is untrustworthy; test one representative descendant rather than all of them.
4. **Branch on outcome.** Two consecutive passes at a level moves testing up one band; two failures moves it down.
5. **Stop at a coverage threshold**, not an item count: when every topic is either directly tested or inferred with acceptable confidence.

This is where including A1 pays for itself. A1 topics are almost all prerequisites of something, so they are resolved by inference in bulk rather than tested.

### Deliberate imprecision

The Kalibrierung produces a **prior, not a measurement.** It will be wrong about a handful of topics. That is acceptable and cheap to fix, because the first two weeks of real rounds correct it automatically through the demotion rule. Do not spend engineering effort making it precise; spend it making the demotion path fast.

---

## 8. Scheduler

Pure code, no LLM.

```
1. Pull tags where due_at <= now, ordered by overdue-ness
2. Filter to tags whose prerequisites meet a minimum stability
3. Select the round set, preferring same confusion_group neighbours
   when multiple tags are due
4. Retrieve items matching those tags at the difficulty tier matching
   current stability, excluding seen_items
5. Interleave: no two consecutive items share a tag_id
6. New-topic introductions drawn from the DAY budget, not per round
7. Grammar / vocab ratio ~70/30, configurable
8. At most one HEAVY element per round: a paragraph block OR a
   production item, never both
9. Paragraph gaps count individually toward round size
10. Context-requiring topics spread across days when several come due
11. Past the due queue, offer bonus rounds from least-stable topics
```

Use `py-fsrs` offline and `ts-fsrs` client-side. Do not write your own SM-2.

### Round, day, queue

"Round" was three different time scales sharing one word, which made every density rule meaningless: two paragraph blocks in a 60-item sitting and two in a 10-item sitting are not the same product.

| Unit | Size | Governs |
|---|---|---|
| **Round** | 6 items, range 5 to 8 | All density and pacing rules |
| **Day** | a budget | New-topic introductions, review load |
| **Queue** | FSRS schedule | Long-term intervals |

A sitting is however many rounds the user wants. Five minutes is one round; an hour is ten.

**New-topic introductions are a daily budget, not a per-round one.** One or two per day regardless of round count. Capping per round means ten rounds introduces twenty new topics in one evening, which is the opposite of spaced.

**At most one heavy element per round.** Heavy means a paragraph block or a production item. A round is one block plus two singles, or one production item plus five singles, or six singles. Never a block and a production item together. Paragraph gaps count individually toward round size, since each is its own SRS event.

**When the due queue runs out**, offer bonus rounds drawn from the least-stable topics, rated and logged normally and clearly marked as past the due queue. Do not pull forward scheduled items: reviewing early yields less stability gain and quietly corrupts intervals.

### Daily load: obligation, ceiling, and forecast

Long practice is not uniformly bad, and the common framing gets the mechanism wrong.

**Review volume is mostly harmless.** Sixty due reviews in an evening are spread across many topics, genuinely due, and handled normally by FSRS.

**New topics are the risk.** Each introduction schedules reviews at roughly 1, 3, 7 and 16 days. Ten introductions today produce a spike next week, and a week of that builds review debt that cannot be cleared. This is the standard way people abandon spaced repetition. Cap introductions tightly, leave review volume loose.

A smaller third effect: practising past the due queue means reviewing early, and early reviews yield less stability gain per review. Not harmful, just poor value per minute.

#### Two numbers, computed not fixed

| Number | Meaning | Derivation |
|---|---|---|
| **Minimum** | Obligation | Count of tags with `due_at` before end of day |
| **Ceiling** | Diminishing returns begin | Due count + (new-topic budget x ~8 blocked items) + bonus allowance |

The ceiling moves with the queue. A constant such as "max 50" is wrong on most days.

#### Seven-day forecast

Today's count cannot tell you whether to take on new topics. Project load across the next 7 days from the FSRS queue, and **act on it rather than merely displaying it**: if any upcoming day exceeds a load threshold, suppress new introductions today. A silent guardrail beats a reminder nobody reads.

#### The threshold is user-configurable, with a data-derived suggestion

`FORECAST_LOAD_THRESHOLD` is a UI setting, not a constant. The default is a guess; the right value is personal and only observable from use.

Labelled in the interface by what it does, not by its name: *pause new topics when any of the next 7 days would exceed ___ reviews.*

**Deriving the suggestion.** The naive basis, items completed per day, is wrong twice over.

- **It is censored by supply.** Clearing a 20-item queue shows capacity of at least 20, not exactly 20. Days that ran into bonus rounds are the uncensored observations; days that ended exactly at the queue are lower bounds only.
- **It ignores frequency.** Someone active 4 days a week needs a *lower* threshold than someone active 7. The forecast projects per calendar day, but skipped days concentrate that load into the sittings that do happen.

```
suggested = median_items_per_active_day
          x active_day_rate
          x clear_rate_adjustment      # 1.1 if queue cleared on >=85% of
                                       # active days, 1.0 for 60-85%, 0.8 below
```

Worked example: 30 items on a typical active day, active 5 days in 7, clearing 90% of the time gives 30 x 0.71 x 1.1 = 23. Substantially below the 50 the default guesses.

**Window: 7, 14 or 30 days, default 14.** Seven days is noisy enough that one holiday skews it; thirty lags behind a real change in habit.

Three guardrails:

- **No suggestion below 10 active days** in the window. Say so plainly rather than showing a number derived from three data points.
- **Clamp the output to a sane range.** Two active days at three items each would suggest a threshold of 2, permanently suppressing new topics and silently stalling all progress.
- **Never auto-apply.** Show the suggestion alongside the three numbers behind it (median items per active day, clear rate, active-day rate) and an apply button. A setting that moves on its own is worse than a wrong setting.

#### Progress bar: full means obligation met

A bar implies a goal, and if the ceiling is the goal, people chase it, which inverts the intent.

The bar fills as the due queue is cleared. **Full means the obligation is met, not that a maximum was reached.** Bonus rounds render as a visually distinct overflow segment past full, so extra practice reads as optional rather than as progress toward something.

#### The reminder

**Never block.** Locking a user out is paternalistic and gets routed around or resented. Show notices at a round boundary, never mid-round, and phrase them as information:

- At full: the due queue is cleared and further rounds are bonus practice
- Past the ceiling: this is reviewing ahead of schedule, which gains less than reviewing on the day

#### Backlog

Miss three days and the queue balloons, which is demoralising and self-reinforcing. Cap the daily review count and order by overdue-ness, letting the remainder slip rather than presenting 200 items. FSRS copes with lateness; the person does not.

### Answer overrides

Answer-set expansion is good, not perfect. It misses regional variants, stylistic alternatives and occasional valid readings, and each miss marks a correct answer wrong. The existing `report` command flags an item for later, which does nothing about the false negative the learner just suffered.

Overrides are split deliberately, because the local and global consequences differ enormously.

**Locally: trust the user immediately.** Tapping "I was right" appends an override event and recomputes `tag_state` from the log. Instant, offline, no argument mid-round.

Note this is an **appended event, not an edit**. `review_log` stays append-only, so sync remains a union with no conflict resolution and no special case.

**Globally: verify before adopting.** The override queues for sync. The Worker runs the disputed answer through the same answer-set expansion check that built the original set.

- Verifier agrees: the answer joins `accepted_answers` and ships in the next bank delta.
- Verifier disagrees: the learner is shown why, which is a teaching moment.

**Never propagate a user answer directly into the bank.** A learner convinced that `den Tisch` is correct in a Dativ context would otherwise inject that belief into `accepted_answers`, after which the app stops teaching them the exact thing they are wrong about. Silent, self-reinforcing, and the worst available failure mode for a grammar trainer.

#### Override rate is a bank quality metric

Two rates, measuring different things:

| Metric | Meaning | Action |
|---|---|---|
| **Upheld override rate** | Verifier agreed. The bank was wrong. | Above ~3%, the verification chain is failing. Revisit stage 4. |
| **Rejected override rate** | Verifier disagreed. The learner was wrong. | A named misconception. Target that topic with contrastive items. |

The first is the quality signal the stage 4 audit can only estimate once; this measures it continuously from real use and belongs in the README. The second is a diagnostic: insisting three times that `den Tisch` is right identifies a topic worth hammering.

### Agent-critical artefacts

Nothing in this project is built by hand. Some artefacts are still **ground truth or foundations**: everything downstream is measured against them, so an error propagates silently instead of failing loudly. Those carry an `**[AGENT-CRITICAL]**` marker and follow one protocol, specified in `CLAUDE.md` section 10.

A capable-tier agent produces the artefact. A second agent independently re-derives it from the same inputs without seeing the first output, then diffs. Disagreements are committed as a conflict list, never silently reconciled.

**The two agents come from different vendors: one Claude, one Gemini.** This is a requirement rather than a preference. Two agents from one family share failure modes, so their agreement is close to worthless as evidence, while a cross-vendor disagreement is genuinely informative. The conflict list records which vendor produced which pass.

**Where an external key exists, it outranks both agents.** Scraped exercise answer keys, MERLIN's expert annotations and the Goethe inventories were authored by people with no relationship to this pipeline, which is the only mechanism that genuinely breaks correlated error. Use them as the anchor for the verification chain, the `known_good` fixture and the ERRANT mapping respectively.

**The honest limitation.** Cross-vendor checking is substantially stronger than same-family checking, but two models trained on overlapping web text still share some blind spots, particularly on German morphology edge cases. The protocol reduces error; it does not make the estimate fully independent. Exactly one metric has no external anchor available anywhere: production grading agreement, because free-form German has no published key. It is reported as unanchored rather than presented as if it were validated.

### Modes outside FSRS

Three activities sit deliberately outside the scheduler. They append to `review_log` with a `mode` field, and `tag_state` recomputation filters to `mode == "review"` only. Everything else is practice, not measurement.

| Mode | Feeds FSRS | May name the topic |
|---|---|---|
| `review` | Yes | Never |
| `duel` | No | Yes, the group is chosen by name |
| `challenge` | No | Yes |
| `recalibration` | Yes | Never |

`recalibration` is the exception: those are genuine reviews, merely reordered.

#### Confusion duels

A duel is 6 to 8 back-to-back items drawn from the two contrasting topics of a confusion group, using minimal pairs where the bank holds them.

**Available for every confusion group, on demand.** The user picks; the app suggests but never forces.

**This is blocked practice, and that is intentional.** The app exists to avoid blocking, so the exception needs its argument stated: contrastive discrimination between two confusable categories is a different intervention from drilling one topic in isolation, closer to minimal-pair training in phonetics. The learner is practising the choice, not the rule. Keeping duels outside FSRS is what makes the exception safe, since a blocked-practice result never inflates a scheduled interval.

**Duels prefer already-seen items.** A duel is not measuring anything, so repetition is fine and arguably better for contrast drilling. Unseen items are the measurement pool and must not be burned by practice. Duel exposure tracks in a separate `duel_seen` set, and duel demand does not enter Stage C generation deficits.

**The duel library** lists every confusion group with: member topics, CEFR span, whether both members have been introduced, duels attempted and when, accuracy in duels, accuracy in normal review, and item availability. A group whose members are not yet introduced is visible but locked, with the reason shown.

**Suggestions follow the threshold-suggestion pattern**: a recommendation with the numbers behind it and an explicit tap to start. Never auto-launched.

The ranking signal is not raw accuracy. It is the **empirical confusion rate** from wrong-answer analysis below: how often an error on topic A produces an answer that would be correct under its sibling B. That is direct evidence of the confusion the duel exists to fix, where low accuracy alone might mean the topic is simply hard. Suppressed below a minimum attempt count, since a high rate on four attempts is noise.

#### Daily production challenge

One bounded free-writing prompt per day, with live AI feedback.

**It may name the grammar point.** "Write a sentence about your commute using Konjunktiv II" is fine here and nowhere else, precisely because the challenge sits outside the interleaved retrieval loop. The prohibition protects rule *selection* under uncertainty; the challenge trains production of a rule already selected for you.

Feedback covers accuracy, naturalness and whether the target structure was used. All three shown, none scheduled. The prompt's topic is chosen deterministically in code from topics in `learning` or recently acquired.

Roughly 30 calls a month at a couple of hundred tokens each: negligible against the budget. Offline or past the ceiling, it shows a model answer for self-comparison, which costs nothing pedagogically since it was never a measurement.

### Wrong answers are signed information

A wrong answer is not a failure bit. Typing `den` where `dem` was required says the learner applied Akkusativ. That identifies *which rule they did apply*, and it is currently discarded.

**Resolved offline, at zero cost, because of a decision already made.** Three distractors per item are already generated at ingest for hint level 2. Each now carries `implied_topic_id`: the topic under which that distractor would have been correct. A wrong answer matching a distractor resolves instantly in the browser with no network and no parser.

Free-text answers outside the distractor set are recorded unclassified and analysed server-side on the next sync, where spaCy is available. Client-side morphological analysis is not possible and should not be attempted.

The result is an **empirical confusion matrix** across the taxonomy: intended topic against implied topic, built from real errors rather than assigned by hand. One artefact feeding four consumers: confusion-group validation, duel ranking, split-candidate detection, and diagnostics.

### Error correction includes items with no error

The current design guarantees exactly one error per error-correction item, which teaches a false prior: find something to change.

Roughly a fifth of error-correction items are therefore correct as written, and "no error" is an available answer.

Two consequences worth naming. Verification must be **stricter** on these, since a "no error" item that actually contains an error is the worst possible item in the bank. And a user editing a correct sentence is a signal in its own right: over-application of a rule, logged and surfaced rather than treated as a plain miss.

### Re-entry after dormancy

The backlog cap handles three missed days. A missed month is a different problem, and it is the moment people either return or leave permanently.

After a dormancy threshold, the app does not present the backlog. It runs a short **recalibration round**: roughly ten items sampled mostly from the highest-stability topics, which are the ones most likely to have decayed unnoticed, plus a couple from `learning`.

Results rebuild the queue. Topics that survive keep their stability; topics that fail demote and are reintroduced. The overdue count is never shown on return.

Recalibration items feed FSRS normally. They are genuine reviews, merely reordered.

### Lapse post-mortems

An acquired topic demoting is the highest-information event in the system and currently only flips a state field.

Each demotion writes a lapse record: topic, which facet failed, prior stability, streak length at the time, hint level used, days since last review, and what the wrong answer implied. Surfaced in stats and the report.

Repeated lapses concentrated on one facet are the strongest available evidence for a split, so lapse records feed the split detector directly. Lapses are where topic boundaries turn out to be wrong.

### Data export

Full client-side JSON export: `review_log`, `tag_state`, settings and schema version. Import restores from it.

Append-only logging makes this clean: export is a dump, import is a replay, and `tag_state` recomputes from the log rather than being trusted from the file.

**No Anki export.** Anki cannot reproduce accepted-answer sets, scoped typo tolerance or facet tagging, so exported cards would grade differently from the app, and topic-level FSRS stability does not map onto Anki's card-level state. Shipping a degraded copy that gives different answers is worse than shipping none.

### Retention and feedback design

Eight surfaces, in order of impact per hour of work. Full specification and tests in stage 8.

**The two seconds after answering carry the most weight.**

1. **Diff highlighting.** Never show a bare correct answer. Show `d[en -> em]` with the differing morpheme marked. German errors are one or two letters and the eye needs help.
2. **"Correct, but" feedback.** A flat tick after a scoped typo passed silently teaches a wrong spelling. Pass the item, say what was off.
3. **Round preview.** "6 items, about 2 minutes", timed from the user's own median response. "Do one round" is a far lower barrier than "study German".

**Progress surfaces, with the obvious versions corrected.**

4. **Grammar coverage bar.** Labelled coverage, never level: a bar reading "B1 100%" claims something about someone's German that topic coverage cannot support. Capped at B2, since a segment that can never fill is a permanent visible failure. Three segments per level (acquired, learning, unseen) so a topic entering `learning` moves it the same day; a single fill over 85 topics moves imperceptibly.
5. **Pace estimate.** A range from a 30-day window, phrased as pace not prophecy: "at your recent pace, 6 to 10 weeks to cover B1 grammar." A 7-day window swings between "23 days" and "four months" on one bad week; linear extrapolation over-promises because remaining topics are harder and review load grows; and a missed self-imposed deadline is a documented quit trigger. Never render a calendar date. Suppressed below 10 active days.
6. **Streak on queue cleared, with freezes.** Counting days the app was opened rewards trivial engagement. Two automatic freezes a month plus a repair option: losing a long streak is among the most common quit moments in this category, so the freeze is the feature, not a gimmick.
7. **Weekly named progress.** "Konjunktiv II, Relativsatz im Genitiv and Passiv Präteritum went solid this week." Three named topics beat any number, computed from `tag_state` transitions.

   **Rolling window, not Sunday to Sunday.** The window runs from the last report to now. A calendar week punishes anyone whose rhythm is not aligned to it, and produces an empty report after a quiet Monday to Friday.

   **Activity-triggered, plus a manual button.** The report fires automatically once completed items since the last report pass a threshold, so a light week waits rather than generating a report about nothing. A manual button is always available, gated on a minimum of new activity so the window cannot be shrunk to noise.
8. **Topic map.** The prerequisite DAG as a graph: acquired, learning, locked, with edges showing why something is locked. Makes the frontier visible in a way no linear bar can, and is the best screenshot in the project.

**Grammar to vocabulary ratio is a UI slider**, default 70/30, range 50/50 to 100/0 so vocabulary can be switched off entirely. Takes effect from the next round.

**Deliberately excluded:** XP, points and leaderboards (no other users, so the numbers mean nothing); hearts and lives (punitive, and at odds with the hint ladder); a daily goal expressed as an item count (turns the ceiling into a target, which the progress bar design exists to prevent); guilt-toned notifications (short-term compliance, long-term deletion).

### Topic introduction cards

Prompts never name their topic. That is correct for review and wrong for first contact: a learner meeting Konjunktiv II for the first time has no way to answer and no way to learn from failing.

Every topic carries a short **introduction card**, shown before its blocked introduction set.

- **Static text stored in the taxonomy.** **[AGENT-CRITICAL]** Generated once on a capable tier, second-agent checked against the structural tests in stage 1, then frozen. Zero runtime cost, works offline, deterministic. Generating per user at runtime would cost money and vary for no benefit.
- **One phone screen, no scrolling.** What it is, the rule in two or three lines, two worked examples, and a contrast with its confusion-group sibling. Longer than that is a textbook chapter, not an introduction.
- **The contrast carries most of the value.** Introducing Dativ after a Wechselpräposition without Akkusativ beside it teaches half a rule. The confusion group already names the sibling to contrast against.
- **Never shown during interleaved review.** That would leak the topic. Cards appear in the blocked introduction, on demotion from `acquired` back to `learning`, and on demand from stats.

`rule_hint` (hint level 3) is the one-line form of the same content and must stay consistent with the card.

### Blocked introduction, interleaved review

The evidence favours blocked practice during initial acquisition and interleaving for retention. Pure interleaving from item one is frustrating and slow to acquire.

- **New topic:** introduction card, then a blocked set of 6 to 8 items.
- **Thereafter:** the topic enters the interleaved review pool permanently.
- **Topics arriving as `acquired` from the Kalibrierung skip the blocked set entirely.**

---

## 9. Where AI Earns Its Place, and What 5 EUR/Month Buys

Most of this app is a scheduler and a string comparison. Be honest about that. The recurring budget is best spent in this order:

**1. Nightly demand-driven generation (§6, Stage C). Roughly 45% of budget.**
The thing that makes the bank track the user instead of being a static artefact. Batch-priced, so it goes further than its share suggests.

**2. Production grading. Roughly 25% of budget.**
What makes a 15% production slice affordable rather than a token gesture. This is the only part of the app where output skill, as opposed to recognition, actually improves. Grade against a rubric with three dimensions: grammatical accuracy, naturalness, and whether the target structure was used at all.

**Only the target-structure dimension feeds FSRS.** A translation tagged `konjunktiv_ii` can fail on word order, and that failure must not land on the Konjunktiv II schedule. Accuracy and naturalness are feedback the learner reads; the scheduler ignores them. This is the multi-attribution problem that paragraph cloze solved by gap-level tagging, and production is where it actually bites.

**3. Error-specific diagnostic explanation. Roughly 15% of budget.**
A static app says "correct answer: dem." An LLM can say: you chose Akkusativ because you saw "in," but the verb here describes position, not directed movement. Tailored to the specific wrong answer given.

Cache by `(item_id, user_answer)`. Wrong answers cluster heavily, so most requests become cache hits within weeks and this line item shrinks over time, freeing budget for the others.

**4. Interference-targeted minimal pairs. Roughly 10% of budget.**
Newly affordable and pedagogically the strongest addition. When the error log shows systematic confusion between two specific topics, generate items that contrast exactly those two in near-identical sentences:

```
Ich stelle die Vase auf ___ Tisch.   (Akkusativ, movement)
Die Vase steht auf ___ Tisch.        (Dativ, position)
```

A pre-generated bank cannot do this, because the pair only becomes worth generating once your specific error pattern is known. This is the clearest case for a live budget in the whole design.

**Fallback when the ceiling is hit or the device is offline:** serve pre-seeded contrasting items from the bank matching the active `confusion_group` instead of generating fresh pairs. Weaker than a targeted pair, far better than nothing, and it costs only a `confusion_group` tag written at ingest.

**5. Weekly error-pattern analysis and personalisation. Roughly 5% of budget.**
Feed the last week's error log and ask for patterns. Output like "Dativ is solid after prepositions but fails after verbs like helfen and danken" is a real insight a rule engine will not surface. Weekly, not per round.

The same budget line covers **domain-flavoured carrier sentences**: generating some items with vocabulary drawn from your own interest areas rather than generic textbook contexts. Cheap, improves engagement, and makes the incidental vocabulary useful rather than arbitrary.

**What AI must not do:** be the scorecard, judge its own items without an independent check, or substitute for exposure to real German.

---

## 10. Vocabulary Integration

Do not build a second SRS. The `dimension` + `tag_id` fields already unify grammar and vocabulary into one queue, one scheduler, one rendering path.

**The free win:** grammar cloze sentences already contain vocabulary. Tag every carrier sentence with its content lemmas (`carrier_lemmas`). When selecting a grammar item, prefer one whose carrier sentence happens to contain vocabulary that is due for review. One item, two learning signals, no extra generation cost.

Vocabulary scope comes from the Goethe wordlists for A1 to B1, banded by Leipzig frequency. B2 has no official list, so use frequency bands alone.

Add a "tap any word you didn't know" affordance during review. Tapped lemmas enter the vocab queue as new items and, from the next nightly run, into generation demand.

Ratio control matters: too much vocabulary dilutes the grammar signal. Start at 70/30 and make it a setting.

---

## 11. Architecture and Hosting

The round loop is entirely static. The bank is pre-generated, four of five exercise types grade deterministically, and FSRS is arithmetic. The Worker exists for the nightly job and the three live features, none of which sit on the critical path of answering a question.

```
Offline / nightly (Python + Worker cron)      Runtime (browser)
────────────────────────────────────────      ──────────────────
Goethe PDFs → taxonomy.yaml
Tatoeba → seed sentences
LLM batch generation (Stages A, B, C)         bank.json (service-worker cached)
Verification chain                            FSRS scheduler (ts-fsrs)
  ↓                                           Deterministic grading
SQLite bank                                   IndexedDB progress
  ↓                                             ↓
export → bank.json / delta                    sync → Cloudflare D1
                                                ↓
                                              Worker → LLM
                                              (production grading,
                                               explanations,
                                               minimal pairs)
```

**Hosting:** Cloudflare Pages (static, free) + one Worker + D1. Free tiers vastly exceed single-user needs. Recurring hosting cost: 0 EUR.

### Automation: nothing runs on a personal machine

Two schedulers, split by what each can execute.

**GitHub Actions** runs the Python generation and verification pipeline. This is not optional preference: the verification chain needs spaCy and an embedding model, neither of which can run inside a Cloudflare Worker's V8 isolate. Free tier is 2,000 minutes/month on private repos and unlimited on public ones.

Because batch results are asynchronous, the nightly job splits in two:

| Workflow | Schedule | Does |
|---|---|---|
| `generate-submit` | 02:00 UTC | Read `tag_state` from D1, compute per-tag deficits, build spec sheets, submit **one** batch request covering every topic. Persist the batch ID. Exits in under a minute. |
| `generate-ingest` | 08:00 UTC | Poll the batch. If complete, run the full verification chain, dedupe, write accepted items to D1, log cost. If not complete, exit cleanly and retry next run. |

One batch per night rather than one call per topic, so the entire night's generation is a single half-price request. A pending or failed batch is harmless: the bank holds 14 days of stock by design.

**Cloudflare Worker cron** handles only light work: sync reconciliation, delta endpoint warming, cache maintenance.

**Bank delivery:** once Stage C runs nightly, do not re-download the whole bank. Ship a delta endpoint: the client sends its highest known item ID, the Worker returns new items only. Keeps the install small and the sync cheap.

### Browser targets

Chrome is the primary target, but build to Safari's constraints anyway. Three reasons: they cost nothing, they are good practice regardless, and **Chrome on iOS is WebKit underneath**, so the moment you open the app on an iPhone you are on Safari's engine whatever the icon says.

- **Storage eviction.** Safari clears IndexedDB after seven days of inactivity for ordinary tabs. Home-screen PWAs are exempt, but data still dies if the icon is deleted or storage runs low. Hence D1 sync, which is also a better portfolio artefact than pure local storage.
- **Kill autocorrect on every input:** `autocorrect="off" autocapitalize="off" spellcheck="false"`. Otherwise the browser silently capitalises or "fixes" the exact ending under test and you log failures the user never made. Chrome desktop is better behaved here than iOS, but the attribute costs nothing.
- **Umlaut buttons** above the input. Long-pressing for every ä is unusable at speed on mobile.
- **PWA, not native.** A native iOS app needs a 99 EUR/year Apple Developer account; sideloading needs a Mac and re-signing every seven days. A home-screen PWA gives a full-screen icon, offline use, and push notifications since iOS 16.4.

---

## 12. Measurement

Round accuracy is the wrong metric, and under interleaving it will look bad by design. Track instead:

- **Retention at first review** after each scheduled interval
- **FSRS stability growth** per topic
- **Kalibrierung accuracy:** what fraction of topics marked acquired survived the first month without demotion. This validates the inference rules and is worth publishing.
- **Held-out exam set:** 40 items never in the rotation, taken monthly
- **Cost per active day**, from the call log

The held-out set is the only uncontaminated progress signal available, and a chart of it over six months is the single best screenshot for the README.

### The UX consequence of interleaving

Interleaved practice reliably feels worse while performing better. Learners rate blocked practice higher while scoring lower on delayed tests. Your accuracy per round will look mediocre and the natural response is to quit.

Counter it in the UI: show topic stability and retention curves, not round percentage. Never lead with "you got 62% today."

---

## 13. Phases

**Phase 0: Taxonomy.** Extract Goethe inventories for A1 to B2, build topic YAML with prereqs and confusion groups. **[AGENT-CRITICAL]** throughout.

**Phase 1: Pipeline for one topic family.** Full generation plus verification chain for one confusion group (recommend `kasus_wechselpraeposition`). Audit 100 items **[AGENT-CRITICAL]**. **Kill or continue on the 15% error-rate threshold.**

**Phase 2: Stage A cold seed.** All topics at 12 items, into SQLite. Deduplication. Export to `bank.json`.

**Phase 3: Kalibrierung plus scheduler plus CLI. Use it daily for two weeks.** No UI yet. This is where you find out whether the inference rules produce a sane `tag_state` and whether interleaved rounds are tolerable.

**Phase 4: Stage B frontier fill**, driven by your own real `tag_state` from Phase 3.

**Phase 5: PWA.** Only after Phase 3 proves the scheduling feels right in practice.

**Phase 6: Worker layer.** D1 sync, delta endpoint, production grading, diagnostic explanations with cache.

**Phase 7: Stage C nightly top-up**, with cap and spend ceiling in place from the first run.

**Phase 8: Interference minimal pairs, weekly error analysis, held-out exam set, difficulty escalation.**

### Why Phase 3 precedes Phase 5

Most projects of this shape die because the author builds the UI first and only then discovers the exercises are boring or the scheduling is annoying. Two weeks of CLI use costs nothing and answers both questions before any frontend work is committed. It also produces the real `tag_state` that Phase 4 needs, so the frontier fill targets your actual gaps rather than a hypothetical user's.

---

## 14. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Generated items have wrong or incomplete answer keys | **High** | Answer-set expansion pass, morphology check, in-app report button, 5% human audit |
| Prompts leak the topic, destroying interleaving | **High** | Spec-sheet prohibition plus an explicit topic-leak rejection stage in the verification chain |
| Interleaving feels bad, motivation collapses | **High** | Blocked introduction, stability-based progress UI, never show round percentage |
| Runaway generation spend | Medium | Nightly item cap, monthly hard stop with graceful degradation, per-call cost logging |
| Kalibrierung marks topics acquired that are not | Medium | Inferred topics demote on first failure; track Kalibrierung accuracy as a metric |
| Taxonomy is wrong or badly ordered | Medium | Cross-check Goethe against BAMF Rahmencurriculum |
| iOS storage eviction loses progress | Medium | D1 sync from Phase 6 |
| Cloze-only practice does not transfer to speaking | Medium | 15% production slice; accept the app's scope limit honestly |
| Item exhaustion within a topic | Low | Stage C top-up, `seen_items` tracking, 14-day demand horizon with safety factor |
| Licensing (Wiktionary CC BY-SA share-alike, Leipzig non-commercial parts) | Low | Attribution file; keep non-commercial-restricted data out of any published bank |

---

## 15. Open Decisions

### Needed before building

1. **Model tier for generation versus verification.** Worth an A/B on the stage 4 100-item audit; a cheaper verifier may suffice and it runs on every item.
2. **Kalibrierung 2/2 initial stability: keep ~30 days or reduce to ~10?** If one in-app pass is insufficient evidence for promotion, two probe items granting 30 days is generous by the same argument. The counterargument is that Kalibrierung items are chosen to discriminate and carry more information per item.
3. **Round size default.** Now a UI setting (default 6, range 4 to 10), so the remaining question is only whether the default is right.

### Needed at implementation time

7. **Safety factor on the 14-day demand horizon.** Too low and the bank runs dry mid-week; too high and items are generated that are never seen.
8. **Weekly error analysis: client-side on request, or a scheduled Worker job?**
9. **Production grading: live (immediate feedback, higher cost) or batched overnight (cheaper, feedback next round)?**

### Guessed constants, to calibrate from real use

None of these should be treated as designed. Each needs the two weeks of stage 7 CLI use, or the 90-day simulation, before being trusted.

| Constant | Guess | Calibrate from |
|---|---|---|
| `FORECAST_LOAD_THRESHOLD_DEFAULT` | 50 | Stage 7 usage; the suggestion algorithm supersedes it |
| `MAX_REVIEWS_PER_DAY` | 60 | Stage 7 usage |
| `INFERRED_STABILITY_CEILING_DAYS` | 4 | 90-day simulation, checking for demotion loops |
| `OVERRIDE_UPHELD_RATE_ALERT` | 0.03 | Real override data |
| `SPLIT_MIN_ATTEMPTS_PER_FACET` | 20 | Facet data volume after a few months |
| `SPLIT_ACCURACY_GAP` | 0.40 | Observed facet spread |
| `PROMOTION_CONSECUTIVE_PASSES` | 3 | Your decision; simulation checks it does not stall |

### Settled, recorded so they are not relitigated

- Free lane carries user content by default (`restrict_user_content_to_paid_lane: false`), to be flipped only if another person uses the app
- No provider-side context caching; local content-addressed cache instead
- Multiple choice is a hint level, never an item type
- No partial credit reaches FSRS
- Full atomisation rejected; evidence-driven splitting supported instead
- Kalibrierung 2/2 grants ~10 days of initial stability, not 30
- No exam-prep or mock-exam mode. Official Goethe papers serve as the held-out measurement set only, which is a measurement decision rather than a feature
- The generated bank is not published
- Grammar to vocabulary ratio is a UI slider, default 70/30
- Weekly report is activity-triggered on a rolling window, with a manual button
