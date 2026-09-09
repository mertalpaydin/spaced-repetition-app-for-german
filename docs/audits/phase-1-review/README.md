# Phase 1 card review, round 1 (8 to 9 September 2026)

The owner's zero-defect policy: every exercise is reviewed before a learner
sees it. This directory is the record of the first review of the phrase deck.
It is a dated record, not instructions; the current state is in
`docs/project-state.md`.

## What was reviewed

Deck version `eb2427b97088`: 5,416 units and 28,270 cards, built from the
whole corpus (`data/deck/` at commit `94efd3f`). Every card was read by a
Claude Opus agent in batches of 600 (48 batches), and every unit in batches
of 700 (8 batches, of which 6 completed before the account's session limit;
batches 02 and 06, units ranked 1,401 to 2,100 and 4,201 to 4,900, are still
open). `findings-round-1.jsonl` holds every finding verbatim;
`reviewed-card-ids-round-1.txt` lists every card id that was read.

The reviewers judged six card categories (wrong unit, wrong gaps, bad
sentence, bad gloss, wrong case, bad unit) and six unit categories (not a
phrase, bad citation form, wrong case, wrong CEFR, duplicate, wrong gloss).
Same-vendor caveat: one Claude reviewing an output of a deterministic
pipeline, not of another model, so the CLAUDE.md section 10 cross-vendor
rule does not apply, but a reviewer's blind spots are still one reviewer's.

## What was found

| | Findings | High severity |
|---|---:|---:|
| Cards read | 28,270 | |
| Card findings | 1,619 (5.7%) | 723 |
| Units read | 4,199 of 5,416 | |
| Unit findings | 1,078 | 390 |

Card findings by category: bad sentence 816, bad unit 361, wrong unit 316,
bad gloss 97, wrong case 23, wrong gaps 6. Bad sentences were mostly news
fragments, truncated source lines, headline style without articles, stray
quotation marks, Swiss spelling and reported-speech Konjunktiv I.

Unit findings by category: not a phrase 519, bad citation form 285, wrong
CEFR 179, duplicate 62, wrong case 33.

## What changed because of it

Systematic causes were fixed in code, with tests, so they cannot recur:

- A pronominal adverb used as an object (`darum bitten`, `dagegen sein`) or
  a separable prefix (`daher|kommen`) is no longer a connector.
- A verb used reflexively in a sentence is owned by the reflexive detector;
  `sich setzen auf` no longer also counts as `setzen auf`.
- `als` carries no case (`gelten als`, `bezeichnen als`).
- A capitalised adjective inside a sentence is a name (`Vereinigten
  Staaten`), not a collocation; adjective-noun pairs need count 8 and lift 8.
- A verb lemma the tagger garbled (`benimmsen`, `erinnerstn`) is not a word
  in the dictionary and yields no unit.
- A fused separable participle in predicate position (`ist ausgezeichnet`)
  is an adjective; `hinzukommen` in a zu-infinitive of `hinkommen` abstains.
- Sentences with reported-speech Konjunktiv I, an answer token repeated
  outside the gaps, or an unbalanced quotation mark never become cards.
- Time, measure and place frames (`am Samstag`, `an der Unfallstelle`) are
  not verb complements.
- Two-part connectors accept only their canonical second part (`zwar …
  aber`, not `zwar … doch`), so the displayed unit never contradicts the gap.

Everything else went into the curated lists, each entry with the reviewer's
reason: 834 units into `data/phrases/exclude.yaml`, 1,085 cards into
`data/phrases/excluded_cards.yaml`, and 345 case, citation-form and CEFR
corrections into `data/phrases/unit_overrides.yaml`.

## What is still open

- Unit batches 02 and 06 (1,400 units) were not reviewed.
- The rebuilt deck contains cards that did not exist in `eb2427b97088`
  (replacements for dropped cards, and cards changed by the new rules).
  Those are unreviewed until round 2, which reads only card ids absent from
  `reviewed-card-ids-round-1.txt`.
- Reviewer CEFR corrections were applied as given; the reviewers noted that
  the automatic level defaults low for formal B2 phrases.
