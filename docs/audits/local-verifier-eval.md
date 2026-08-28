# Local models as the verification backstop, 28 August 2026

`TODO.md` item 0. Five local models measured against the same two golden
fixtures the hosted verifier was measured on, through the same `verify_items`
pass, on the owner's RTX 4070 Laptop (8188 MiB).

**Conclusion: no local model tested is usable as a replacement for the hosted
verifier.** The best false-positive rate belongs to a model that catches almost
nothing; every model that catches a useful share of defects rejects between a
quarter and half of all good items, against the hosted verifier's 12.9%. The
recommendation is to keep the hosted verifier and to close this item.

---

## 1. The numbers

Ranked by false-positive rate, which is the figure that decides whether a
verifier is affordable. A false positive throws away a good candidate silently,
and at 1,225 items each percentage point is about 12 items lost.

| Model | Quant | Batch | Recall (visible) | **False positives** | s/call | tok/s | Peak VRAM |
|---|---|---|---:|---:|---:|---:|---:|
| **Gemini 3.7 Flash** (baseline) | hosted | 5 | **75.0%** (15/20) | **12.9%** | n/a | n/a | n/a |
| Gemma 4 12B | Q3_K_S | 2 | 11.1% (2/18) | 3.4% (1/29) | 12.5 | 21.9 | 7534 |
| Qwen3.5 9B | IQ4_XS | 5 | 47.4% (9/19) | 26.9% (7/26) | 26.1 | 39.5 | 7585 |
| Ministral 3 14B Instruct | UD-IQ2_M | 2 | 60.0% (12/20) | 51.7% (15/29) | 39.6 | 10.2 | 7536 |
| Granite 4.2 3B | Q5_K_M | 2 | 69.2% (9/13) | 53.3% (8/15) | 43.7 | 60.9 | 5248 |
| Ministral 3 14B Reasoning | UD-IQ2_M | 2 | not completed | not completed | 104 to 644 | 9.5 to 12.7 | n/a |

**Read the recall column with its denominator.** It is not 20 for every row,
because a truncated call yields `not_run` rather than a verdict, and an item
nothing judged is neither a catch nor a miss. Granite's 69.2% is over 13
records; it never judged the other 7. Its apparent competitiveness is an
artefact of the 12 calls (of 35) that ran out of tokens.

**Why recall is quoted over 20 records and not 38.** Eighteen of the 38
adversarial records are correct German whose only defect is the topic they were
filed under, and CLAUDE.md rule 2 forbids telling the verifier the topic. No
model can see those. `scripts/eval_local_verifier.TOPIC_DEPENDENT_DEFECT_CLASSES`
names the 18; the split reproduces the one `docs/project-state.md` states
independently. Two of the remaining 20 (`c08_04`, `c08_05`) do not encode the
defect they are filed under, per `TODO.md` 2.7b, so a correct verifier is right
to pass them.

### Ministral 3 14B Reasoning: stopped, and why

It was the only model that both reasoned and terminated, judging 2/2 on its
probe. It was stopped after 3 calls because it is disqualified on throughput
regardless of accuracy: observed 104s on a clean call and 644s on one that ran
to the token cap. A 1,225-item bank at batch 2 is 613 calls, which is 17.7
hours at its best observed rate and two to three days with the truncation rate
it was showing. The hosted verifier does the same work in 30 to 45 minutes.

The with-and-without-reasoning comparison this was meant to provide is
therefore not available. What was learned instead is recorded in section 3.

---

## 2. What this cost, and what it would save

Nothing, which is the entire attraction and is not enough. A local verifier has
no daily quota, no 503s, no spend ceiling and no question about user text
reaching a provider that trains on it. Against that, the hosted verifier's
whole contribution to a bank build is about $3.28.

The trade is not $3.28 against zero. It is $3.28 against throwing away between
a quarter and half of every good candidate the corpus produces, on a bank whose
scarce topics already struggle to reach 25 items. At Ministral Instruct's 51.7%
a 1,225-item target would need roughly 2,500 accepted candidates to survive
verification, and the topics that cannot reach 25 today would not reach 12.

---

## 3. What was learned about running local models here

These are the findings worth keeping even though the answer was no. Three of
the four are harness defects found and fixed during the run, which is the
honest record: the first two rounds of numbers were wrong.

**Use `/api/chat`, never `/api/generate`.** The chat endpoint applies the
model's chat template, which is what makes a reasoning model put its thinking
in `message.thinking` instead of inlining it. Measured on Granite 4.2: through
`/api/generate` its reasoning arrived as unmarked English prose with `thinking`
empty and no JSON anywhere in the reply; through `/api/chat` the same model
separated 15,628 characters of thought cleanly. The completion endpoint scores
every reasoning model on a prompt format it was never trained for.

**The comparison must run at `--batch-size 5`.** That is what the hosted
baseline used (`fix-log.md`, cycle 17), not the script's default of 20. Running
the default produces a number that looks comparable and is not.

**Local models cannot hold five items in one prompt; the hosted one can.**
Granite, Gemma and both Ministrals all failed at batch 5 and passed at batch 2.
Five items times four questions is apparently past what a small quantised model
sustains before it degenerates. Only Qwen3.5 9B managed batch 5. **Any future
local verifier work should start at batch 2**, and note that this doubles the
call count against the hosted lane's.

**Reasoning could not be bounded, only switched off.** ollama's `think: "low"`
and `"medium"` levels are silently ignored by Qwen3.5: all three settings
produced byte-identical 26,892-character thinking. With reasoning left on,
neither Qwen nor Granite ever emitted an answer, at any batch size (1, 2 or 5)
or token cap (2,048 through 32,768). Granite at 32,768 spent 675 seconds and
108,238 characters of thinking and judged nothing. Every number in section 1 is
therefore measured with reasoning off, which understates any model whose
strength is reasoning. Ministral Reasoning ignores the off switch and reasons
anyway, which is why it was the one model that both thought and answered.

**Greedy decoding causes degeneration on this prompt.** Granite at
`temperature=0.0` loops inside a JSON string (`"Die korrekte Lücke wäre
'gekaufte' nicht."` repeated to the cap). Its JSON structure is correct; it
writes ~800-token German essays in each `reason` field and runs out of room. A
`temperature` of 0.3 finished once and truncated on a re-run, so this mitigates
the problem without solving it.

**Model output format varies in ways that are not the model's fault.**
Ministral answers with a bare top-level JSON array where the pass expects
`{"verdicts": [...]}`. The content was correct and complete. Scoring that as a
failure measures the wrapper, so `local_client.normalize_verdict_envelope`
rewraps it, in the transport, changing only the envelope and never a verdict.
The strict parser and the hosted lane are untouched. The three models measured
before this existed all emitted the object form, so it changed none of their
numbers.

**VRAM: the 6 GB cap was met by one model out of four.** Granite peaked at 5248
MiB. Every other model peaked at 7534 to 7585 MiB against the card's 8188, and
their throughput reflects it. Raising `num_ctx` does not raise VRAM usage past
this point; it lowers it, because ollama moves model layers to system RAM to
fit the KV cache. Measured on Qwen3.5 9B with an identical prompt:

| `num_ctx` | tok/s | GPU used |
|---:|---:|---:|
| 8,192 | 45.9 | 7238 MiB |
| 16,384 | 45.8 | 7502 MiB |
| 32,768 | 26.8 | 7436 MiB |
| 65,536 | 16.4 | 7190 MiB |

There is no out-of-memory error at the cliff between 16k and 32k. There is a
42% throughput loss, and then a 64% one, and nothing says so.

---

## 4. Models that were not tested

**GLM-4.7-Flash.** A mixture of experts of about 30B total parameters. Every
expert must be resident even though only about 3B activate, so its smallest
quantisation is 13.78 GB, more than twice the 6 GB budget. GLM 4.6 Flash, as
originally proposed, does not exist as a text model; only GLM-4.6V-Flash, which
is vision.

**Granite 4.2 at any other quantisation.** Q5_K_M at 2.61 GB left the most
headroom of anything tested and still degenerated on 12 of 35 calls. The
failure is not a quantisation artefact.

---

## 5. What was built, and what happens to it

`src/llm/local_client.py` and `scripts/eval_local_verifier.py` are kept. The
client writes a `cost_log` row per call with `lane="local"` and zero cost,
which is the true figure: CLAUDE.md rule 4 is about visibility, and a run
verified locally must not read as a run that did no verification.

Two contract changes were made and are flagged per CLAUDE.md rule 8:

- `verify_items` and `cache_coverage` take a `VerifyingLlmClient` Protocol
  rather than a concrete `GeminiLlmClient`. A widening only; every existing
  caller satisfies it structurally and no behaviour changed.
- `Lane` and `LoggedMode` gained a `"local"` value. Additive; no historical row
  carries it, so nothing reparses differently.

They are worth keeping even though the answer was no, because the next cheap
model release makes this a one-command question instead of a day's work. The
pre-flight probe in particular reduces the cost of a model that cannot answer
from about an hour to a single call.

**This is not a general verdict on local models.** It is a verdict on five
models, at quantisations that fit 6 GB, on one prompt that asks four questions
per item in German and demands strict JSON. A larger VRAM budget, a shorter
prompt, or a model released next month could all change it.
