# Stage 4 kill gate: fixes 1-4 verification, 15 August 2026 (second pass)

## What this document is

`stage-04-pilot-2026-08-15.md` found the expander widening instead of
rejecting, and proposed fixes 1-4 (ambiguity threshold, constrained
expansion, post-expansion distractor re-check, real-word validation) plus
fix 5 (taxonomy `eligible_types` redesign, scoped to its own branch). This
document is the promised re-run after implementing fixes 1-4: same audit
process, same candidate batch, so the before/after comparison is a
controlled measurement, not two different samples.

**Auditor: Claude (Sonnet), single pass.** CLAUDE.md section 10 requires a
second, cross-vendor pass before a number like this is treated as final; none
was performed here. Read the verdict below as strong, self-consistent
evidence that fixes 1-4 work as specified, not as the gate's official
sign-off.

## Controlled comparison

All three runs used **`batch_311b96edf8f8`**, the same 100 candidates the
15 August audit evaluated (generation is deterministic and content-cached,
so re-submitting the same 12-topic sample reproduces the same batch id).
Every difference below is attributable to the verification chain, not to
different content.

| | 15 Aug (original) | 15 Aug (fixes 1-4, first pass) | 15 Aug (fixes 1-4, final) |
|---|---|---|---|
| Accepted | 33 | 30 | **18** |
| `structural_malformation` | ~32 | 48 | 46 |
| `vocabulary_ceiling_violation` | 0 (never ran) | 0 (never ran) | **23** |
| `ambiguity` | 0 | 12 | 9 |
| `pedagogical_flaw` | ~11 | 6 | 3 |
| `topic_leak` | 3 | 3 | 1 |

Two columns exist between "original" and "final" because implementing fixes
1-4 correctly, and then actually trusting the result, surfaced three bugs
unrelated to the four fixes' own logic -- caught by re-auditing the
*accepted* set after each pass rather than stopping once rejection counts
looked plausible. Each is described below.

## Fixes 1-4 confirmed working, item by item

Every specific defect the 15 August audit named is gone from the accepted
set in the final run:

| Audit finding | Status |
|---|---|
| Item 1: `artikel_bestimmt_nom` accepting Der/Ein/Mein/Dein/Sein/Ihr/Unser/Euer/Dieser/Kein | Fixed. Determiner-type consistency (fix 1) rejects a mixed set; fix 2 drops mismatched alternatives before they arrive |
| Item 13: `futur_i` accepting werden/wollen/möchten/können/würden | Fixed. Verb-lemma consistency rejects the mix; `würde`-forms are additionally distinguished from indicative `werden` (Konjunktiv II vs Indikativ) even though both share the lemma |
| Items 21, 26, 27, 29: distractor collisions after expansion | Fixed. The distractor check now re-runs against the final expanded set (fix 3) |
| Item 20: hallucinated "paratstünden" / "daheimhätten" | Fixed -- but only after closing a wiring gap, see below |
| Item 15: `kasus_genitiv_formen` accepting 10 genitive determiners of different subtypes | Still correctly accepted. No `ArtType` tag on this topic, so fix 1 does not apply the determiner check to it -- confirms the gating logic (not "reject on any spread") is working as designed |

## Three bugs found by re-auditing the accepted set, not by the fixes' own logic

**1. `VocabularyStore` was never constructed with real data anywhere in
production.** `_verify_and_insert_candidates` built `VerificationPipeline`
without a `vocab_store` argument. This meant layer 1's vocabulary-ceiling
check -- one of the seven stages `02-content-pipeline.md` specifies -- had
never actually run in any real pilot or nightly run, and fix 4's real-word
validation of expander alternatives was validating alternatives against
*nothing*. This is why "paratstünden" and "daheimhätten" survived the first
fixes-1-4 pass despite fix 4 supposedly checking for exactly that. Fixed by
loading `data/fixtures/corpus/vocab_levels.json` (11,633 CEFR-banded
lemmas -- despite the path, this is real production data, not a test
double) into a real `VocabularyStore` and passing it through. Confirmed
live: the hallucinated forms are gone from the final run's accepted set,
and the reactivated vocabulary check now legitimately rejects 23 items on
its own.

**2. The determiner-identity check was too coarse to catch a possessive
topic accepting a bare indefinite article.** Fix 1's original
`determiner_definiteness` only classified Def vs Ind, and "ein", "kein",
and every possessive (mein/dein/sein/ihr/unser/euer) all share the same
ein-word ending paradigm -- they all resolve to "Ind" in that scheme. An
`artikel_possessiv_nom` item accepting "Eine" (genuinely indefinite, not
possessive) alongside "Meine"/"Deine" passed the check undetected, found by
re-reading the first fixes-1-4 pass's own accepted set. Fixed with
`facets.determiner_art_type`, which inspects *which* ein-word stem matched
(distinguishing Ind/Neg/Poss, not just Def-vs-everything-else).

**3. Several modal preterite/Konjunktiv II person-forms were missing from
`IRREGULAR_VERB_LEMMA`.** `"solltest"` (2nd-singular) had no entry, so
`futur_i` item 13's "wirst, solltest" pairing was invisible to the
verb-lemma consistency check -- the unresolvable form was silently excluded
from consideration rather than flagged. Added the missing 2nd-singular and
plural forms across all modals (`konntest`, `musstest`, `wolltest`,
`durftest`, `solltest`, `mochtest`, and their plural counterparts).

**A fourth, smaller gap** surfaced while sanity-checking the newly-active
vocabulary check: "Schweden" (Sweden) was rejected as a B1 vocabulary
violation for a `futur_i` travel-topic sentence, because country names were
never in `VocabularyStore.PROPER_NOUNS` (only given names were). Extended
the exemption list with common countries, cities, and continents, on the
same "carries no vocabulary difficulty of its own" reasoning the existing
given-name exemption already uses.

## What remains: `artikel_bestimmt_nom` still fails on solvability, not on anything fixes 1-4 touch

All three accepted `artikel_bestimmt_nom` items in the final run are still
single- or bare-sentence cloze items with nothing in the carrier that
forces a definite article over an indefinite or possessive one
(`"___ Hund schläft im Garten."`, `"___ Katze trinkt Milch."`). The
*answer given* is correct German; the *item* still cannot be solved by
reasoning, only guessed -- exactly the solvability defect
`01-foundation.md`'s stage 1 section describes and the 15 August audit's
fix 5 exists to address (`requires_context: true`, paragraph cloze, so a
prior-mention sentence can actually force definiteness). Fixes 1-4 operate
on the *answer set* a candidate already has; they cannot retroactively give
a single free-standing sentence a discourse context it was never generated
with. This is not a regression and not a new finding -- it is confirmation
that fix 5 is doing real, distinct work fixes 1-4 cannot substitute for,
exactly as the original audit scoped it.

Rough count: 3 of 18 accepted items (all `artikel_bestimmt_nom`) carry this
defect, all traceable to the same root cause. Read this as supporting
evidence for proceeding to fix 5, not as a rejected-gate verdict on fixes
1-4's own scope, which they were not designed to cover.

## Recommendation

Fixes 1-4 are confirmed working against live re-generation, not just unit
tests: every specific defect the 15 August audit named is gone, and three
unrelated wiring/coverage bugs the audit could not have anticipated were
found and closed by insisting on re-auditing results rather than trusting
rejection counts. Proceed to fix 5 (`eligible_types` / `requires_context`
redesign) on its own branch, per the original audit's own recommendation --
it is the one remaining path to the `artikel_bestimmt_nom`-shaped defect
class, and nothing here suggests fixes 1-4 need further work first.

Get a second, cross-vendor audit pass before treating any future number
from this chain as gate-final, per CLAUDE.md section 10 -- not done for
this document.
