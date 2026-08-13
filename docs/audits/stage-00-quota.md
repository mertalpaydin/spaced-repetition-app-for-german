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
| **Answer-Set Expansion (Stage 4)** | `gemini-3.6-flash` | Billed Project | Batch | **Low / Medium** | High-precision morphological & syntactic expansion. Accuracy-critical to avoid false negatives. |
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
