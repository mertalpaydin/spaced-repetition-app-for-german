# Cycle 6 audit: eligible_types enforcement and the modal-lemma leak

Blanking pilot, 300 sentences requested, 174 accepted into the pool (mock
generator, offline, `--cefr A2`, matching `scripts/step6_blank_pilot.py`'s
own defaults). Two findings, reported separately below, both now fixed and
verified against the same pool.

## Finding 1: 72 of 354 items violated their own topic's `eligible_types`

A previous cycle (docs/audits/cycle-05-report.md) added citation cues for
four verb topics and, in doing so, fixed an `eligible_types` violation for
those four as a side effect -- but never checked whether the same class of
bug existed elsewhere. It did, on eight more topics, none of them cued:

| Topic | Items | Emitted | Topic allows |
|---|---:|---|---|
| `artikel_bestimmt_nom` | 20 | `cloze_free` | `paragraph_cloze` |
| `verb_sein_haben` | 16 | `cloze_free` | `cloze_cued` |
| `praeteritum_sein_haben_modal` | 8 | `cloze_free` | `cloze_cued`, `transformation` |
| `artikel_unbestimmt_kein_nom` | 7 | `cloze_free` | `paragraph_cloze` |
| `artikel_possessiv_nom` | 7 | `cloze_free` | `paragraph_cloze` |
| `adjektiv_komparativ_superlativ` | 6 | `cloze_free` | `cloze_cued` |
| `partizip_i_attributiv` | 4 | `cloze_free` | `cloze_cued`, `transformation` |
| `partizip_ii_attributiv_erweitert` | 4 | `cloze_free` | `cloze_cued` |

`72 / 354 = 20%` of the run. Nothing in `src/generation/blanking` ever
checked `item.type in topic.eligible_types` at all; `src.verification.pipeline`
enforces the identical invariant for the other (LLM-direct) generation path,
so this pipeline was the one place the constraint was not honoured.

### The fix has two parts, because the eight topics split into two genuinely
### different situations

**(a) Five topics wanted a citation cue, and the mechanism already existed.**
`adjektiv_komparativ_superlativ`, `partizip_i_attributiv`,
`partizip_ii_attributiv_erweitert`, `verb_sein_haben` and
`praeteritum_sein_haben_modal` are now cued:

- `verb_sein_haben` / `praeteritum_sein_haben_modal`: both share
  `selectors._irregular_finite_selector` with `modalverben_praesens`. The
  cue used to be gated on `lemma in _MODAL_LEMMAS_FOR_CUE` (modals only);
  since every candidate this factory produces has, by construction, already
  matched `lemma in lemmas` (the selector's own closed target set), the
  citation form is exactly as trustworthy for `sein`/`haben` as for a modal.
  The gate is now unconditional (`_MODAL_LEMMAS_FOR_CUE` removed).
- `adjektiv_komparativ_superlativ`: cued with the positive-form lemma
  (`___ (groß)` -> `größer`). spaCy's own lemmatiser resolves even irregular
  comparatives correctly (`gut`->`besser`, `hoch`->`höher`, `nah`->`näher`),
  confirmed by direct testing across ~25 common A1-B2 comparative/
  superlative adjectives. One confirmed, real failure: `gern - lieber - am
  liebsten` is a suppletive comparison, and the lemmatiser resolves the
  comparative/superlative surface to the unrelated, real adjective `lieb`
  ("dear") instead of `gern`. A handful of umlaut-stem comparatives
  (`kälter`, `wärmste`, `jüngste`) also mislemmatise to non-words. Both
  classes are held in a small, confirmed-bad exclusion list
  (`selectors._MISLEMMATIZED_ADJEKTIV_DEGREE_LEMMAS`), the same posture as
  the existing `_MISLEMMATIZED_VERB_LEMMAS` list for lexical verbs -- when
  the cue cannot be trusted, it is withheld, and the item is honestly
  reported as a type-ineligibility skip rather than shown with a misleading
  cue. This adjustment is also a genuine correctness fix independent of the
  type violation: without a cue, any comparative fits the slot equally well.
- `partizip_i_attributiv`: cued with the underlying verb's own infinitive.
  The participle's own lemma minus its trailing "d" (`schlafend` ->
  `schlafen`) is unconditionally trustworthy here, because it is only ever
  reached after already matching the closed `_PARTIZIP_I_VERBS` allowlist.
- `partizip_ii_attributiv_erweitert`: cued via a new
  `paradigms.PARTICIPLE_II_TO_INFINITIVE` table (`entwickelt` ->
  `entwickeln`), built by inverting `STRONG_VERBS`/`MIXED_VERBS` for the
  strong/mixed half and hand-verifying the weak half -- its keys are
  confirmed identical to `KNOWN_PARTICIPLE_FORMS` (test:
  `test_participle_ii_to_infinitive_covers_exactly_known_participle_forms`),
  so a selector that already checked `lemma in KNOWN_PARTICIPLE_FORMS` can
  look its infinitive up unconditionally.

**Honest limitation, not fixed: `verb_sein_haben`'s cue supplies the LEXEME,
never the TENSE.** `Obwohl die neuen Vorschriften sehr streng ___ (sein)`
admits both `sind` and `waren` -- the cue rules out every other
auxiliary/modal, but nothing in the carrier forces Präsens over Präteritum.
`uniqueness.py`'s own policy table already documents why `sein`/`haben`
`irregular_aux` candidates are exempt from the modal-interchangeability
check ("the construction itself forces sein/haben/werden... there is no
other auxiliary that could stand in the same slot"), which is true for
WHICH auxiliary but says nothing about WHICH TENSE of it. This is a
genuinely different problem from the one a cue can solve (a cue names a
word, not a grammatical feature), and fixing it would need a carrier-level
tense anchor (an explicit `jetzt`/jetzige vs. `damals`/`früher` adverbial,
or similar) that the selector does not currently require and this cycle
does not add. Left unfixed and reported here rather than silently
shipped or half-fixed under time pressure; a future cycle's own audit
should re-measure how often this actually produces a genuinely ambiguous
item before deciding whether a carrier anchor is worth requiring.

**(b) Three topics (`artikel_bestimmt_nom`, `artikel_unbestimmt_kein_nom`,
`artikel_possessiv_nom`) cannot be honestly tested by a single, standalone
sentence at all**, and are not given a cue mechanism. Definiteness,
indefiniteness and possession are discourse properties (docs/audits/
stage-04-pilot-2026-08-15.md's bucket 1 analysis, reached independently
here): `___ Hund schläft im Garten` admits `Der`/`Ein`/`Mein`/`Kein` alike,
and no bracketed cue can fix that without naming the grammar category
itself (which rule 2 forbids). Their own `eligible_types` is
`[paragraph_cloze]`.

**Decision: skip these three topics, let them report zero.** The
alternative offered was to build genuinely multi-sentence carriers (an
earlier sentence establishes the referent, a later one carries the gap).
This pipeline's whole architecture (`pipeline.py`'s own module docstring,
`sentence_source.py`'s pool) is built on independent, unpaired sentences
with no mechanism to link one sentence's referent to another's gap --
building that is new architecture (discourse-level carrier generation and
validation), not a fix to this cycle's defect, and rushing it risked
exactly the trap `stage-04-pilot-2026-08-15.md` decision D4 already named
and rejected: producing something that LOOKS like it satisfies the type
check without actually being solvable. Skipping and reporting zero is the
honest choice given the real constraint, not a workaround.

**What was NOT done: relabelling.** No item's `type` was ever set to
`paragraph_cloze` (or any other type) to satisfy the check without the
underlying item actually matching that shape. The fix in every case is
either "make the item genuinely of an eligible type" (the five cued topics)
or "do not emit the item at all" (the three `artikel_*` topics).

### The hard assertion

`pipeline.blank_sentences` now checks `outcome.item.type in
topic.eligible_types` for every successfully built item, immediately AFTER
the uniqueness gate (not before -- see `pipeline.py`'s own comment at the
enforcement site for why that order, not the reverse, matters: for every
cue-gated candidate kind, passing uniqueness already implies the cue was
present and the type is `cloze_cued`, so checking type first would mask the
uniqueness gate's specific, diagnostic skip reason -- e.g.
`modal_verb_interchangeable` -- behind a generic "wrong type" one for
exactly the no-cue cases cycles 4 and 5 built dedicated reasons for). A
violation is skipped, never emitted, and counted under its own fourth
bucket: `BlankingReport.skips_by_type_ineligibility` /
`type_ineligibility_skips`, kept distinct from `skips_by_reason` (an
ordinary quality skip), the cap/dedup drop counters (a balance decision),
and `skips_by_uniqueness` (a solvability judgment) -- the same
"never conflate a distinct outcome with an existing bucket" argument
`skips_by_uniqueness` itself already established in cycle 4.

## Finding 2: modal verbs leaking into `verb_praesens_regelm`

Three items, all the same shape:

    Ihr ___ bei diesem Sturm besser zuhause bleiben.    answer: solltet   cue: (sollten)
    Ihr ___ eure Ideen offener im Team teilen.          answer: solltet   cue: (sollten)
    Ihr ___ eure Passwörter regelmäßig ändern.          answer: solltet   cue: (sollten)

Two defects in one. `sollen` is a MODAL and belongs to `modalverben_praesens`,
not `verb_praesens_regelm`. And the cue itself was wrong: the infinitive is
`sollen`, not `sollten` -- `solltet` here is Konjunktiv II ("you really
should..."), and spaCy's lemmatiser resolves it to `sollten`, itself an
INFLECTED form (the Präteritum/Konjunktiv-II plural of `sollen`), not an
infinitive at all. Confirmed directly: `de_core_news_sm` tags `solltet` as
`VVFIN`/`Tense=Pres`/`Mood=Ind` (both wrong) with lemma `sollten` -- a
self-consistently wrong tag, the same class of bug the previous cycle's
`_MISLEMMATIZED_VERB_LEMMAS` list names one confirmed instance of at a time
(`schalen` for `schalte`).

### What distinguishes an infinitive from an inflected form

Not spelling. `sollten` ends in `-en` exactly like a genuine infinitive
does, so the existing shape check in `_lexical_verb_lemma_trustworthy`
(ends in `-en`, or is `tun`, or bare `-n` on an `-el`/`-er` stem) already
correctly classifies it as infinitive-SHAPED, and does not catch it.

What actually distinguishes them is membership in a closed, already-fully-
tabulated set: no genuine German verb's infinitive is spelled
`sollten`/`hatten`/`wären` -- those spellings are already, fully, claimed by
`paradigms.IRREGULAR_FINITE`'s own INFLECTED cells (sein/haben/werden/every
modal, every tense_mood, every person/number). A new set,
`paradigms.IRREGULAR_FINITE_INFLECTED_FORMS`, is computed from that table
directly (every surface form it produces, minus the table's own lemma
keys -- the subtraction matters, since several modals' own 1st/3rd-plural
present tense is spelled identically to their infinitive, e.g. `können`, and
those must not be flagged as "not an infinitive" when they are exactly that
one). Any lexical-verb candidate's lemma found in this set is proof the
tagger mislemmatised an inflected form as if it were a citation form, the
same class of bug the confirmed-lemma list already guards against one
instance at a time, generalised so it does not need a new hand-added entry
per confirmed case.

### The fix, in two places

1. **Selection, not just cueing.** `_IRREGULAR_PRAESENS_LEMMAS` (the
   exclusion set `verb_praesens_regelm` checks) and the equivalent exclusion
   set in `praeteritum_vollverben` both now include
   `paradigms.IRREGULAR_FINITE_INFLECTED_FORMS`, so the candidate is never
   selected for these topics at all -- not merely left without a cue.
   `verb_praesens_vokalwechsel` and `verben_trennbar_praesens` get the same
   defensive check for symmetry and documentation, though neither was
   actually reachable by this bug (their own gating tables/particle
   requirements already exclude a modal-shaped lemma structurally).
2. **`_lexical_verb_lemma_trustworthy` extended.** The cue-reliability guard
   used by all four lexical-verb selectors now also rejects any lemma in
   `paradigms.IRREGULAR_FINITE_INFLECTED_FORMS`, as a second, independent
   backstop -- the same "a bug in one guard should not alone be enough to
   leak a defect" posture the module already applies elsewhere
   (`_cue_equals_answer` in `blanker.py` is the same idea for a different
   invariant).

## Verification

Same 300-sentence-requested / 174-accepted mock pool, before and after both
fixes:

| | Before | After |
|---|---:|---:|
| Accepted items | 354 | 318 |
| `eligible_types` violations | 72 | **0** |
| Modal leaks in any lexical-verb topic | 3 | **0** |
| Uniqueness skips | (not separately re-measured before) | 49 |
| Cross-topic duplicate drops | (not separately re-measured before) | 39 |

Item count dropped from 354 to 318: every item that is gone either (a) never
should have existed as `cloze_free` in the first place and is now an honest
`type_ineligibility` skip or a `uniqueness` skip (the no-cue lexical-verb/
modal/plural-noun cases, and every `artikel_*` item), or (b) was one of the
three confirmed modal-leak items. No genuinely correct, honestly-typed item
was lost.

Cue coverage on every KEPT item in the affected topics is 100% (by
construction: for the four cue-gated candidate kinds -- modal, plural noun,
lexical verb form, and now sein/haben -- the uniqueness gate only lets a
no-cue candidate through when the kind is exempt from it entirely, and every
exempt kind that also needs a cue for type eligibility, i.e. `degree` and
the two participle `adjective` candidates, is caught by the type-
ineligibility check instead when its cue is missing):

| Topic | Items | Cued |
|---|---:|---:|
| `adjektiv_komparativ_superlativ` | 3 | 3/3 |
| `nomen_plural` | 20 | 20/20 |
| `partizip_i_attributiv` | 4 | 4/4 |
| `partizip_ii_attributiv_erweitert` | 4 | 4/4 |
| `praeteritum_sein_haben_modal` | 9 | 9/9 |
| `praeteritum_vollverben` | 7 | 7/7 |
| `verb_praesens_regelm` | 20 | 20/20 |
| `verb_praesens_vokalwechsel` | 11 | 11/11 |
| `verb_sein_haben` | 17 | 17/17 |
| `verben_trennbar_praesens` | 8 | 8/8 |

`artikel_bestimmt_nom` / `artikel_unbestimmt_kein_nom` / `artikel_possessiv_nom`:
0 items, 45 / 7 / 7 type-ineligibility skips respectively -- reporting zero,
exactly as decided above.

No modal lemma (or any `paradigms.IRREGULAR_FINITE_INFLECTED_FORMS` member)
appears as either the proposed answer or the cue of any item under
`verb_praesens_regelm`, `verb_praesens_vokalwechsel`,
`verben_trennbar_praesens` or `praeteritum_vollverben`.

## Is anything in the taxonomy itself wrong

No. `data/taxonomy.yaml`'s `eligible_types` declarations for all eight
affected topics are correct as written -- the bug was entirely that
`src/generation/blanking` never checked its own output against them. The
`stage-04-pilot-2026-08-15.md` bucket analysis that originally justified
these `eligible_types` values (bucket 1: discourse-forced, bucket 2:
lexical-cue-forced) reads, independently, as the right call for every one of
the eight topics audited here too.

## What is not fixed by this cycle, on record

- `verb_sein_haben`'s tense ambiguity (documented above under Finding 1a).
- `adjektiv_komparativ_superlativ`'s coverage is now smaller (6 items before
  cueing existed as a concept, 3 after, since the "gern"/umlaut-stem
  mislemmatisation cases are honestly withheld rather than cued wrongly).
  This is intentional -- fewer, correct items over more, defective ones --
  but a future cycle could recover the "gern" case specifically by hand-
  correcting its cue to "gern" rather than trusting the lemmatiser, the
  same way `paradigms.py`'s other hand-verified tables already do for
  comparably small, closed irregularities.
