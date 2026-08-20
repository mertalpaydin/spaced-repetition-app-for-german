# Does a corpus cover the topics generation cannot reach

TODO 4.3. Reproduce with `scripts/eval_corpus_coverage.py`.

## Answer

Yes, decisively. **Both corpora produce candidates for all 49 topics.** The AI
pipeline reached 36 in cycle 9, with ten topics at zero and three more killed
by verification.

| | Tatoeba | Leipzig news 2025 | AI generation, cycle 9 |
|---|---:|---:|---:|
| Sentences in | 120,000 | 120,000 | 882 |
| Carrier-valid | 84,546 (70%) | 73,375 (61%) | 712 (81%) |
| Candidates | 175,640 | 172,017 | 525 |
| **Topics with items** | **49 of 49** | **49 of 49** | **36 of 49** |

Both runs sample from a length-filtered pool (25 to 160 characters, 5 to 18
words, terminal punctuation) so the sentences are comparable to the carriers
the generator writes. Caps are disabled: this measures what the corpus
contains, and a balance cap would hide exactly the number being measured.

## The ten topics generation could not reach

Every one of them is well supplied. Counts are per 120,000 sentences.

| Topic | Tatoeba | Leipzig | AI |
|---|---:|---:|---:|
| `artikel_possessiv_nom` | 3,643 | 1,159 | 0 |
| `infinitiv_um_zu` | 1,012 | 557 | 0 |
| `artikel_unbestimmt_kein_nom` | 579 | 461 | 0 |
| `artikel_bestimmt_nom` | 432 | 775 | 0 |
| `relativsatz_dativ` | 334 | 409 | 0 |
| `konjunktiv_ii_hoeflichkeit` | 123 | 19 | 0 |
| `passiv_praeteritum` | 26 | 43 | 0 |
| `passiv_praesens` | 21 | 26 | 0 |
| `praepositionen_genitiv_gehoben` | 18 | 35 | 0 |
| `zustandspassiv_zeiten` | 1 | 5 | 0 |

The three that verification killed are also covered: `zustandspassiv` 11 and
20, `partizip_i_attributiv` 26 and 92, `relativsatz_genitiv` 65 and 77.

This is the argument for retrieval over generation, confirmed with numbers. A
construction the model will not write on request is simply *found* by looking
at enough natural sentences.

## What is still thin, and one number that looks wrong

Six topics stay under 50 per 120k in at least one corpus:
`zustandspassiv_zeiten`, `futur_ii`, `zustandspassiv`,
`praepositionen_genitiv_gehoben`, `passiv_praesens`, `passiv_praeteritum`.

Scaling matters here. These are counts from 120k sentences; full Tatoeba is
roughly 700k and the Leipzig corpus is 1M, so multiply by six to eight.
`futur_ii` at 20 becomes about 120. Only `zustandspassiv_zeiten`, at 1 per
120k in Tatoeba, stays genuinely marginal.

**`passiv_praesens` at 21 and 26 looked like a bug, and was partly one.**
Followed up and resolved, with the original diagnosis only half right.

There were two real selector bugs, both now fixed: separable-prefix
participles mistagged `VVIZU` instead of `VVPP`, and a participle search that
only looked forward and so missed verb-final subordinate clauses. Together
they were worth about a third: `passiv_praesens` went 19 to 26 per 100,000
Tatoeba sentences, `zustandspassiv` 9 to 16.

The remaining gap is not a bug. Taking 4,000 Leipzig sentences containing
`wird` or `werden` and instrumenting the pipeline: 1,643 die in carrier
validation, the selector finds roughly 44 passive candidates in the 2,357
that survive, and **33 of those 44 are then killed by the uniqueness gate for
`auxiliary_tense_unanchored`**, leaving 11.

That gate is right. `passiv_praesens` blanks the auxiliary, because the
auxiliary is what makes the sentence passive, and `Die Währung ___ auch in
Bulgarien eingeführt` admits `wird` and `wurde` equally unless the sentence
carries a time anchor. Three quarters of natural present passives do not
carry one.

So the topic is intrinsically expensive, not broken. It needs either a
temporal anchor requirement of its own, the way `plusquamperfekt` requires
`bevor`, or acceptance that its yield is roughly one usable item per 200
passive sentences. At corpus scale that is still enough.

The honest lesson: a number that looks impossible is worth chasing, and the
answer was two real bugs plus a gate working correctly. Without the corpus
baseline neither the bugs nor the gate's true cost would have been visible.

## Carrier validation on natural text

Carrier validation rejects 30 percent of Tatoeba and 39 percent of Leipzig.
Both dominated by the same two reasons:

| Reason | Tatoeba | Leipzig |
|---|---:|---:|
| `missing_clause_connector` | 10,057 | 13,462 |
| `no_subject_found` | 6,618 | 8,849 |
| `multiple_sentences` | 5,105 | 4,969 |
| `no_finite_verb` | 4,754 | 4,713 |
| `subject_verb_disagreement` | 3,316 | 3,714 |
| `agreement_undecidable` | 3,291 | 5,505 |
| `content_word_not_a_real_word` | 1,093 | 2,077 |
| `swiss_spelling` | 37 | 738 |

These were written to judge model output, where a rejection means the model
made a mistake. Against a corpus of published human German the same rejection
usually means our checker cannot parse the sentence, not that a professional
editor wrote it wrong. `subject_verb_disagreement` firing 3,316 times on
Tatoeba is our false-positive rate showing up at scale for the first time.

That is a cost worth paying at this volume, because 70 percent of 700k
sentences is still far more than we need. But it is also free evidence about
carrier validation's own accuracy, which we have never measured, and it
should be sampled before anyone tightens those checks further.

The Swiss spelling numbers are a useful sanity check on TODO 1.4's new rule:
738 hits in German news, 37 in Tatoeba. Swiss German media is exactly where
you would expect that, and the near-absence in Tatoeba is what a
learner-written corpus should look like.

## What this settles

1. **Corpus first for the rare constructions.** All ten dead topics are
   covered, several by thousands of candidates. Generation should not be
   asked to produce a Futur II again.
2. **Generation still owns level control.** These counts say nothing about
   whether a sentence is A1-appropriate. Leipzig is 2025 news: politics,
   finance, proper nouns. Tatoeba is far closer to the register we want and
   should be the primary source, with Leipzig for constructions Tatoeba is
   thin on. The CEFR vocabulary filter is the next measurement and it will
   cut both numbers substantially.
3. **A corpus baseline catches our own bugs.** `passiv_praesens` at 21 per
   120k is the first evidence of the participle-tagging gap outside a
   hand audit.

## The level filter

TODO 4.3, second half. Same runs, with `VocabularyStore.check_ceiling_budget`
applied before tagging, which is the budgeted rule the generation pipeline
already uses: content words two bands above the ceiling are always violations,
words one band above are tolerated up to a small budget.

Per 120,000 input sentences:

| | Tatoeba A1 | Tatoeba A2 | Tatoeba B1 | Leipzig A1 | Leipzig A2 | Leipzig B1 |
|---|---:|---:|---:|---:|---:|---:|
| Dropped above ceiling | 62,172 (52%) | 34,972 (29%) | 1,234 (1%) | 99,518 (83%) | 67,289 (56%) | 5,778 (5%) |
| Carrier-valid | 41,285 | 60,659 | 83,828 | 12,974 | 33,068 | 70,067 |
| Candidates | 81,100 | 122,521 | 173,678 | 25,299 | 70,524 | 162,418 |
| **Topics covered** | **48/49** | **49/49** | **49/49** | **49/49** | **49/49** | **49/49** |

The filter cuts hard, exactly as expected, and coverage barely moves. At an A1
vocabulary ceiling Tatoeba still yields 81,100 candidates across 48 of the 49
topics, missing only `zustandspassiv_zeiten`. Leipzig loses 83 percent of its
sentences and still covers all 49.

### Correction: one global ceiling is the wrong measurement

The table above applies a single vocabulary ceiling to all 49 topics. **That is
not what the product does and was never a requirement.** The owner's
correction, and he is right: a B1 grammar topic should not be restricted to A1
words. A learner's vocabulary is expected to grow alongside their grammar.

`data/taxonomy.yaml` already assigns every topic its own level (18 A1, 13 A2,
12 B1, 6 B2), and `scripts/step6_blank_pilot.py` already stamps each item with
`cefr=topic.cefr`. So the filter that matters is **per topic, at that topic's
own level**: an A1 topic's carriers filtered at A1, a B2 topic's at B2.

Recomposed from the same runs on that rule, per 120,000 sentences per corpus:

| | Tatoeba | Leipzig | Combined |
|---|---:|---:|---:|
| Candidates | 102,984 | 60,764 | **163,748** |
| Topics covered | | | **49 of 49** |
| Topics under 20 candidates | | | **1** |

The only topic under 20 is `zustandspassiv_zeiten` (B2) at 6 combined, which
becomes roughly 40 at full corpus scale. Every other topic is comfortably
supplied at its own level.

This is the number to quote. The single-ceiling table is kept above only
because it is what was actually run, and because it shows the filter's cost
per band.

### What is thin at A1 and A2, and whether it matters

Under 20 candidates per 120k at an A2 ceiling: `zustandspassiv_zeiten` (1 in
Tatoeba, 4 in Leipzig), `praepositionen_genitiv_gehoben` (6, 13), `futur_ii`
(15, 3), `zustandspassiv` (11, 18), `relativsatz_genitiv` (13, 11),
`passiv_praesens` (18, 14), `passiv_praeteritum` (17), the two participle
topics, and `konjunktiv_ii_hoeflichkeit` (13 in Leipzig).

Scale settles most of it. Full Tatoeba is roughly 5.8 times this sample and
the full Leipzig corpus 8.3 times, and the two are independent sources that
add. `zustandspassiv_zeiten`, the worst case, goes from 1 and 4 to roughly 6
and 33. `futur_ii` goes to about 110 combined.

Two caveats on those numbers. `passiv_praesens` and `passiv_praeteritum` are
still suppressed by our own participle-tagging bug, so their true counts are
higher. And `praepositionen_genitiv_gehoben` is elevated register by
definition, so a low count under an A2 ceiling is the corpus being honest
rather than thin.

## Recommended split (TODO 4.5)

1. **Tatoeba is the primary source.** Best register match, highest yield per
   sentence at every ceiling, CC BY 2.0 FR.
2. **Leipzig is the supplement**, for constructions Tatoeba is thin on. Its
   news register survives an A1 filter far worse, but what survives is usable
   and it is an independent 1M sentences.
3. **Generation keeps two jobs only**: topics the corpus cannot reach even at
   full scale, which on this evidence is at most `zustandspassiv_zeiten`, and
   thematic control when the bank needs items about a particular subject.
4. **Never ask generation for a rare construction again.** That was the cause
   of ten empty topics in cycle 9 and the corpus answers it outright.
