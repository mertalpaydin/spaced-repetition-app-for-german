# Stage 0: Gemini API Pricing & Quota Audit

## 1. Overview & Verification

This audit records the active pricing tiers, models, and rate limits for the Google Gemini API used across the development and operation of the Interleaved German Grammar Trainer.

Date of Audit: August 2026

---

## 2. Model Routing Matrix

**Corrected 2026-08-18.** The table below previously showed thinking "OFF" for every `gemini-3.5-flash-lite` workload and listed a separate "Topic-Leak Checking" LLM row. Both were wrong against the code as of this correction: `src/contracts.py` and `GeminiLlmClient._thinking_config_for` (`src/llm/client.py`) key thinking on the *model*, not the calling purpose, and there is no LLM call anywhere in `src/verification/layer_topic_leak.py` at all (it is a deterministic blocklist/compound-term match; it never touches `cost_log`). This table now matches the code; see CLAUDE.md section 9 for the same routing stated as a contract.

| Workload / Stage | Model | Lane policy | Thinking | Rationale |
|---|---|---|---|---|
| **Item Generation (Stage 3, 10)** | `gemini-3.5-flash-lite` | Pilot: free/sync by default, `--batch` opt-in for `step5_pilot_generation.py` only. Nightly top-up and cold-start: batch permitted. | `low` | Shares `THINKING_FLASH_LITE` with every other Flash-Lite workload (see below); no longer thinking-OFF. |
| **Topic-Leak Checking (Stage 4)** | *(none)* | n/a, code-only | n/a | Not an LLM call. Deterministic blocklist/compound-term match (`src/verification/layer_topic_leak.py`), free, no `cost_log` row. Removed from this table's earlier "batch, OFF" listing, which described a call that does not exist. |
| **Answer-Set Expansion / verification layer 5 (Stage 4)** | `gemini-3.7-flash` | Inherits the calling run's lane (pilot vs. nightly); not independently batch or sync | `medium` (fixed, `THINKING_VERIFY`) | The verification chain's one model-backed layer, optional per run, invoked on every candidate surviving layers 1-4. Raised from `low`. This table's earlier separate "Override Verification" row was the same call, not a second one. |
| **Live Explanations (Stage 11b)** | `gemini-3.5-flash-lite` | On-demand only (sync); a learner is waiting | `low` | Shares `THINKING_FLASH_LITE`; no longer thinking-OFF. |
| **Production Grading (Stage 11a)** | `gemini-3.5-flash-lite` | On-demand only (sync); a learner is waiting | `low` | Shares `THINKING_FLASH_LITE`; no longer thinking-OFF. |
| **Minimal-Pair Generation** | `gemini-3.5-flash-lite` | On-demand only (sync) | `low` | Shares `THINKING_FLASH_LITE`; not in the original table at all. |
| **Weekly Report (Stage 11)** | `gemini-3.5-flash-lite` | Dual: manual button press is on-demand; activity-triggered auto-fire may use batch (`src/llm/weekly_report.py`, `maybe_generate_report`) | `low` | Shares `THINKING_FLASH_LITE`; no longer thinking-OFF, and not batch-only as the original "Billed Sync" entry implied. |

`MODEL_LIVE` and `MODEL_GENERATE` are the same string, `"gemini-3.5-flash-lite"`; every row above sharing that model shares its one thinking constant, `THINKING_FLASH_LITE`, by construction. There is no per-workload thinking override on this model.

---

## 3. Quota & Cost Guardrails

1. **Two-Lane Strategy**:
   - **Free Project (Synchronous)**: Used for rapid development iterations, pilot runs (by default), and all four on-demand live workloads.
   - **Billed Project (Batch API)**: Used for nightly runs, cold-start bank generation, weekly-report auto-fire, and any pilot run given `--batch` explicitly, at 50% discount.
2. **Thinking Token Budgeting**:
   - Thinking tokens bill as output tokens.
   - `gemini-3.5-flash-lite` (`THINKING_FLASH_LITE`) runs at `low` on every workload that uses it: item generation, live explanations, production grading, minimal-pair generation, and the weekly report narrative alike. This is a change from the original audit, which had this model at `off` for bulk generation and every live workload; that is no longer the code's behaviour. `low` was first turned on for one workload only (sentence generation, to fix a subject-verb agreement defect); it has since been extended to the whole model line by owner instruction.
   - `gemini-3.7-flash` (`THINKING_VERIFY`) runs at `medium`, raised from `low`. It is a fixed level for this workload, not tuned per call between `low` and `medium` as the original audit stated; "tuned against empirical recall on `adversarial.jsonl`" describes how the value was chosen, not a runtime choice.
   - **Cost consequence, stated plainly:** `minimal` (Flash-Lite's own undeclared default) cost nothing beyond what the model would have spent anyway, so the earlier `THINKING_GENERATE = "minimal"` period bought no real thinking-token spend. Moving the whole model line to `low`, and verification from `low` to `medium`, is therefore the *first* real thinking-token spend this workload has incurred, and it now touches the highest-volume calls in the system (item generation, live explanations) rather than one narrow purpose. No post-change spend has been measured yet; the paragraph above is a directional statement about what changed, not a dollar figure. Watch `cost_log` and this file's section 4 pricing table for the actual delta before treating any number here as settled.
3. **Hard Caps**:
   - Nightly generation item cap: **120 items maximum**.
   - Monthly spend hard ceiling: **5.00 EUR** (`GeminiLlmClient.spend_ceiling_usd` defaults to `5.00`, tracked in USD, not converted from EUR here; that mismatch predates this correction and is out of scope for it). On reaching the ceiling, system gracefully degrades to least-recently-seen bank review.

---

## 4. Pricing correction (verified 2026-08-14)

`src/llm/client.py`'s `PRICING_PER_MILLION` table was re-verified against
ai.google.dev/gemini-api/docs/pricing and cross-checked against a second
independent source. The values in place since this file's original August
2026 audit were stale and **under-estimated real spend by roughly 4-8x**,
silently weakening the $5/month ceiling in section 3 above (an
under-estimate means real Google billing could exceed the ceiling before the
code believes it has been reached).

| Model | Standard, was | Standard, corrected | Batch (50% off, corrected) |
|---|---|---|---|
| `gemini-3.5-flash-lite` | $0.075 / $0.30 | **$0.30 / $2.50** | $0.15 / $1.25 |
| `gemini-3.7-flash` | $0.15 / $0.60 | **$0.75 / $3.75** | $0.375 / $1.875 |

(input / output per 1M tokens)

Both models remain free-tier eligible (Google's free tier still applies to
both), so this correction only changes cost estimates for paid-lane (batch)
calls -- free-lane calls are still logged at $0.00, correctly. `gemini-3.7-flash`
carries 2026 introductory pricing, 50% off its own standard rate through
2026-12-31; standard pricing ($1.50 / $7.50) takes effect 2027-01-01 and will
need a further correction then.

No model swap is recommended: `gemini-3.6-flash`, the model named in
CLAUDE.md's routing table text for the two thinking-enabled workloads, was
found to carry identical current pricing to `gemini-3.7-flash` (both $0.75 /
$3.75 standard, $0.375 / $1.875 batch) -- the code's actual choice of the
newer `gemini-3.7-flash` for `MODEL_VERIFY` costs nothing extra and CLAUDE.md's
prose is simply out of date relative to `src/contracts.py`, not a pricing
regression.

---

## 5. Thinking-token routing change, budget risk (2026-08-18)

`THINKING_FLASH_LITE` moved from a purpose-gated `off`/`minimal` (only
sentence generation paid anything, and `minimal` is the model's own free
default) to a model-keyed `low` applied to every `gemini-3.5-flash-lite`
call, and `THINKING_VERIFY` moved from `low` to `medium`. Both changes are
approved and already in `src/contracts.py`; this section records the cost
reasoning honestly rather than restating the routing table.

- **No post-change spend has been measured.** There is no `cost_log` data
  yet reflecting `low`/`medium` thinking at the new scope. Any dollar figure
  attached to this change here would be presented as a measurement when it
  is actually a guess, which section 3 above and CLAUDE.md section 9 both
  warn against doing. This document does not do that.
- **What is knowable without measurement:** thinking tokens bill as output
  tokens at the corrected per-model output rate in the table above, and the
  `low` level now applies to `gemini-3.5-flash-lite`'s two highest-call-count
  workloads (item generation and live explanations) instead of one narrow
  purpose, while `gemini-3.7-flash`'s single verification workload moved up
  one thinking tier. Both changes can only increase spend relative to the
  prior routing; neither can decrease it.
- **Budget risk.** The monthly ceiling (`GeminiLlmClient.spend_ceiling_usd`,
  $5.00) is enforced in code and raises `BudgetExceeded` before it is
  crossed, so this change cannot silently blow through the ceiling
  mid-month; a caller that hits it degrades rather than overspending. Two
  distinct risks remain even so, and neither is quantified here:
  (1) `_estimate_cost` (`src/llm/client.py`) logs free-lane calls at $0.00
  regardless of thinking tokens, so the direct dollar exposure from the
  `gemini-3.5-flash-lite` change is confined to whatever share of that
  model's calls land on the paid lane (nightly top-up, weekly-report
  auto-fire, overflow, and any pilot run given `--batch`) -- but every
  `gemini-3.7-flash` call (`answer_expansion`) was already paid-lane-only
  before this change, so its `low` to `medium` move raises real dollar spend
  on every call it makes, with no free-lane exemption; and (2) even on the
  free lane, a thinking-enabled call consumes more of the free project's
  daily token quota than the same call did without thinking, which can close
  the free lane sooner and push more work into the paid lane's per-token
  cost than before, an indirect budget effect the code's cost log does not
  itself attribute to "thinking". Whether either effect meaningfully
  shortens the month before the ceiling trips depends on actual call volume
  and thinking-token counts this document does not have. Watch `cost_log`
  after this change ships; do not assume the 5 EUR/month budget is
  unaffected just because no single call can exceed the hard ceiling.
