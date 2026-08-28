# The audit record

Everything in this directory is a dated record of work already done, and none
of it is an instruction. Each file was accurate on the day it was written and
several are now wrong on purpose.

**For the current state, read `docs/project-state.md`, `TODO.md` and
`docs/known-defects.md` instead.** Where a file here disagrees with `CLAUDE.md`,
`CLAUDE.md` wins.

Cycles 13 to 18 have no report of their own. They are in `fix-log.md`, which is
the running archive of closed work and the only file here that is still being
added to.

**Four files read as live work and are not.** They are superseded and kept only
as record:

- `stage-04-pilot-2026-08-15-implementation-note.md` opens "for planner review"
  and "Not yet committed", and names a branch that no longer exists.
- `generation-track-plan.md` plans the AI-generation track that the corpus
  pipeline replaced. It also proposes relaxing `CLAUDE.md` rule 2. That
  proposal was never adopted.
- `stage-04-recovery-plan.md` is a plan for a chain that has since been rebuilt.
- `PLAN_VS_CODE.md` carries its own header saying the same thing.

Dates below are the date a file records for itself, or its first commit where it
records none.

| File | Date | What it measured or decided |
|---|---|---|
| `stage-00-quota.md` | 13 Aug, revised 27 Aug | Gemini pricing, model routing and free-tier limits, read from Google. |
| `stage-04-2026-08-13.md` | 13 Aug | First run of the verification chain against the adversarial and known-good fixtures. |
| `stage-04-pilot-2026-08-14.md` | 14 Aug | Kill gate not passed: 13 of 46 accepted items defective. |
| `PLAN_VS_CODE.md` | 14 Aug | **Superseded.** Gap analysis of plan against code, filed unchanged. |
| `stage-04-pilot-2026-08-15.md` | 15 Aug | Second kill-gate pilot, 51.5% defective, traced to the expander widening instead of rejecting. |
| `stage-04-pilot-2026-08-15b.md` | 15 Aug | The controlled re-run after fixes 1 to 4, same candidates. |
| `stage-04-pilot-2026-08-15-implementation-note.md` | 15 Aug | **Superseded.** Status note on decisions D1 to D7. |
| `stage-04-recovery-plan.md` | 15 Aug | **Superseded.** Found 65 of 84 rejections were chain defects, not model defects. |
| `stage-04-a2-pilot-audit.md` | 15 Aug | The A2 kill-gate pilot: 17 of 50 defective, one dominant cause. |
| `generation-track-plan.md` | 16 Aug | **Superseded.** Plan for the AI-generation track. |
| `cycle-01-report.md` | 16 Aug | 87 of 300 accepted, 17.2% defect rate. |
| `cycle-02-report.md` | 16 Aug | 117 of 296 accepted over 69 topics; the new blanking path persisted nothing. |
| `cycle-03-report.md` | 17 Aug | The blanking path's first real run: 245 items, 25 topics. |
| `cycle-04-report.md` | 18 Aug | 510 items, 42 topics, defect rate up to 20 to 25%. |
| `cycle-05-report.md` | 18 Aug | 469 items; citation cues added; defect rate back to about 12%. |
| `cycle-06-modal-leak.md` | 18 Aug | 72 items violated their own topic's `eligible_types`, plus a modal lemma leaking into lexical verb topics. |
| `cycle-06-report.md` | 19 Aug | 419 items, 38 topics, structural invariants all clean. |
| `cycle-07-report.md` | 19 Aug | All 370 items hand audited; the model backstop had silently not run. |
| `cycle-08-report.md` | 19 Aug | 286 items, all hand audited; the verifier ran and rejected 16 with reasons. |
| `cycle-09-demand-driven-generation.md` | 20 Aug | Why generation was rebuilt to be driven by topic demand. |
| `cycle-09-report.md` | 20 Aug | 428 items, 36 topics; the retry constants pinned by a test. |
| `corpus-coverage.md` | 20 Aug | Both corpora produce candidates for all 49 topics; AI generation reached 36. |
| `cycle-10-corpus-report.md` | 21 Aug | The first corpus pilot: 366 items, 47 topics, no sentence generation. |
| `tagger-accuracy-vs-gold.md` | 22 Aug | `de_core_news_sm` against 3,000 gold-annotated UD sentences. |
| `cycle-11-corpus-report.md` | 23 Aug | Corpus pilot re-run: 337 items, 44 topics. |
| `cycle-12-corpus-report.md` | 23 Aug | Corpus pilot after the four decisions: 396 items, 48 topics. |
| `local-verifier-eval.md` | 28 Aug | Five local models measured as the verification backstop; none adopted. Records the harness defects that invalidated the first two attempts. |
| `fix-log.md` | running, to 28 Aug | Closed work, with what was measured. Holds cycles 13 to 18. |
| `data/` | 20 Aug | The JSON counts behind `corpus-coverage.md`. |
