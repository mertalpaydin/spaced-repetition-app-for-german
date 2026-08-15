# Stage 0: Gemini API Pricing & Quota Audit

## 1. Overview & Verification

This audit records the active pricing tiers, models, and rate limits for the Google Gemini API used across the development and operation of the Interleaved German Grammar Trainer.

Date of Audit: August 2026

---

## 2. Model Routing Matrix

| Workload / Stage | Model | Lane / Project | Mode | Thinking | Rationale |
|---|---|---|---|---|---|
| **Item Generation (Stage 3, 10)** | `gemini-3.5-flash-lite` | Free Sync / Billed Batch | Batch | **OFF** | High volume, low stakes, cheapest token cost. Thinking OFF avoids ballooning output tokens. |
| **Topic-Leak Checking (Stage 4)** | `gemini-3.5-flash-lite` | Free Sync / Billed Batch | Batch | **OFF** | Fast term/context classifier. |
| **Answer-Set Expansion (Stage 4)** | `gemini-3.7-flash` | Billed Project | Batch | **Low / Medium** | High-precision morphological & syntactic expansion. Accuracy-critical to avoid false negatives. |
| **Live Explanations (Stage 11b)** | `gemini-3.5-flash-lite` | Free / Billed Sync | Sync | **OFF** | On-demand user diagnostic feedback with local `(item_id, user_answer)` caching. |
| **Production Grading (Stage 11a)** | `gemini-3.5-flash-lite` | Free / Billed Sync | Sync | **OFF** | 3-rubric grading (target structure, accuracy, naturalness). |
| **Weekly Report (Stage 11)** | `gemini-3.5-flash-lite` | Billed Sync | Sync | **OFF** | Rolling activity-triggered narrative scorecard. |

---

## 3. Quota & Cost Guardrails

1. **Two-Lane Strategy**:
   - **Free Project (Synchronous)**: Used for rapid development iterations and test runs within 15 RPM / 1,000 RPD limits.
   - **Billed Project (Batch API)**: Used for Stage C nightly runs and cold-start bank generation at 50% discount.
2. **Thinking Token Budgeting**:
   - Thinking tokens bill as output tokens. Thinking is strictly disabled (`off`) for bulk generation.
   - Verifier thinking is kept to `low` or `medium` and tuned against empirical recall on `adversarial.jsonl`.
3. **Hard Caps**:
   - Nightly generation item cap: **120 items maximum**.
   - Monthly spend hard ceiling: **5.00 EUR**. On reaching the ceiling, system gracefully degrades to least-recently-seen bank review.

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
