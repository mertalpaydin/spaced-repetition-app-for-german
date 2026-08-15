# Fix 5 implementation note, for planner review

All decisions D1-D7 from the "Decisions requested during fix 5 implementation"
section are implemented on `fix/04-eligible-types-redesign`
(branched from `fix/audit-remediation`). Not yet committed.

## What's done

- **D1-D3**: `data/taxonomy.yaml` and `data/specs/*.yaml` updated. 23 topics
  moved into buckets 1-3 (per the original audit), `plusquamperfekt` set to
  `requires_context: false` per D2, `modalpartikeln` moved to bucket 1 per D3.
- **D5**: the 4 stale unit tests fixed. Three follow D5 exactly (retarget to a
  topic that still allows `cloze_free`). **One deviation, flagged for your
  check**: the two layer-5 determiner tests
  (`test_layer5_repairs_item_when_fix2_drops_the_mismatched_alternative`,
  `test_layer5_ambiguity_check_is_a_backstop_when_fix2_cannot_filter`) test
  ArtType-gated determiner logic that only fires for
  `artikel_bestimmt_nom`/`artikel_possessiv_nom` specifically — retargeting
  the topic would have defeated the test's purpose. I changed `type` to
  `error_correction` instead and left `topic_id` alone, which is the opposite
  of D5's literal instruction ("change the topic, not the type") but preserves
  what the test actually verifies. Worth a second look.
- **D6**: `TopicSpec.forcing_element` is now `{kind, note}` (`ForcingElement`
  model, `ForcingElementKind` enum of the 10 kinds from D6's table) instead of
  a free string. `prompt_builder.py` updated accordingly.
- **D7**: `scripts/check_gold_examples.py` now checks any declared
  `forcing_element` against its gold examples. 8 of 10 kinds have a real
  mechanical check (closed lists for `temporal_anchor`,
  `correlative_first_half`, `anteriority_anchor`; a "declared token from the
  note must appear" check for `governing_word`/`governing_preposition`/
  `motion_or_location_verb`; weaker structural proxies for
  `subject_person_marker`/`discourse_referent`). `unambiguous_antecedent` and
  `clause_relation` are always reported as unverifiable, per D6's own
  admission that they have no mechanical check. The summary line prints an
  explicit unverifiable count so a clean pass can't be misread as "every
  check ran." **No topic uses `forcing_element` yet** — this is schema +
  gate only, not content. Authoring `forcing_element` for the ~61 bucket-4
  topics is NOT done and is a separate, larger follow-up.
- **D4**: `known_good.jsonl`'s 19 stale items reshaped, not relabelled, per
  bucket (bucket 1 → genuine two-sentence `paragraph_cloze`, bucket 2 →
  `cloze_cued` + real cue, bucket 3 → `transformation` with a supplied source
  form). Gold examples for all 23 changed spec sheets updated to match:
  bucket-2 topics got a `cue` field added (content otherwise untouched, since
  their existing sentences were already good); bucket-1/3 topics got their
  weakest example(s) reshaped with real two-sentence or source-form context.

## Also found and fixed, not part of the original decision list

89 stale duplicate files (`<name> (1).yaml`/`.md`, dated one day before this
session, untracked) had accumulated in `data/specs/` and elsewhere —
consistent with a cloud-sync conflict-copy artifact. They were being picked
up by `all_specs`'s glob-based test fixture and caused two tests to fail
spuriously. Deleted after confirming with the user; nothing tracked in git
was affected.

## Verification

- `pytest -m "not live and not simulation"`: 336 passed.
- `python -m scripts.check_gold_examples`: 87/87 pass, 0 unverifiable
  (expected — no `forcing_element` authored yet).
- `ruff check .` / `ruff format --check .`: clean.
- `mypy --strict src/`: clean, 63 files.

## Fix-5-specific pilot (run live, after committing cdaf326)

`python -m scripts.step5_pilot_generation --pilot 100`, batch
`batch_51fc18e48f7b`. 100 requested, 100 retrieved, 16 accepted, 84 rejected,
$0.0046. Zero rejections for eligible_types mismatch — the LLM respects the
new `allowed_item_types` cleanly. All 84 rejections are pre-existing generic
quality noise (distractor/answer collisions, non-German characters in
multi-word distractors), not fix-5-related. Files: `data/pilot_review.jsonl`
(accepted), `data/pilot_rejected.jsonl` (rejected).

Hand-audited the 16 accepted items. `artikel_bestimmt_nom` is clean: *"Hier
ist ein Hund. ___ Hund ist klein."* → **Der**, genuinely forced by the prior
mention — the fix works as intended there.

**Two real defects survived, both in `artikel_possessiv_nom`:**

1. *"Da ___ Bruder heute Geburtstag hat, feiern wir im Garten."* → accepted
   all six possessive persons (mein/dein/sein/ihr/unser/euer). This is the
   *original* audit defect, unchanged: the item is typed `paragraph_cloze`
   but is still one bare sentence with no established possessor. Nothing
   checks that a `paragraph_cloze`-typed item actually contains multi-sentence
   forcing content — the type label alone doesn't guarantee it.
2. *"Ich habe einen Hund. ___ Hund ist sehr klein."* → accepted `['Mein',
   'Dieser', 'Unser']`. "Mein" is correctly forced by "Ich habe"; "Unser"
   (our) is wrong, it doesn't match the established first-person-singular
   speaker. Fix 1's determiner-consistency check (`determiner_art_type`)
   only validates paradigm type (Def/Ind/Neg/Poss), not person within the
   possessive paradigm, so a wrong-person alternative isn't caught.

**A third, more structural concern:** `artikel_unbestimmt_kein_nom` produced
*"Auf dem Tisch liegt ein Buch. ___ Buch ist rot."* → accepted `['Das',
'Dieses']`, both definite/demonstrative, not indefinite or negative at all.
This may not be a bug so much as a design tension: forcing-via-prior-mention
naturally produces *definite* answers, which is the opposite of what a topic
testing indefinite/`kein` articles needs to force. The mechanism that fixes
`artikel_bestimmt_nom` may be structurally wrong for its indefinite
counterpart, which needs a "newly introduced, not yet mentioned" framing
instead of a "already mentioned" one.

Recommendation: do not treat fix 5 as closed on this evidence. The
eligible_types mechanism itself is confirmed working, but the sample surfaced
a real gap (nothing verifies `paragraph_cloze` content actually has forcing
context) and a real ambiguity-check gap (person-within-paradigm, not just
paradigm-type). Both look like layer-2/layer-5 scope, closer to fixes 1-4's
territory than to fix 5's taxonomy scope. Planner's call on whether these
become fix 6 or get folded back into this branch before merge.

## Not done (explicitly out of scope for this pass)

1. `forcing_element` content for the ~61 bucket-4 topics (D1's broadened
   scope + the original audit's bucket 4). Schema and gate are ready for it.
2. Fixing the two defects the pilot just surfaced (see above).
3. Committed as `cdaf326` on `fix/04-eligible-types-redesign`. Pilot output
   files (`data/pilot_review.jsonl`, `data/pilot_rejected.jsonl`) and this
   note's pilot section are not yet committed.
