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

**`passiv_praesens` at 21 and 26 is not credible as a corpus fact.** The
present passive is one of the commonest constructions in German news writing,
and Leipzig is German news. Something in our selector is rejecting it, which
matches the caveat recorded against TODO 1.2: `de_core_news_sm` tags some
separable-prefix participles `VVIZU` instead of `VVPP`, and the passive
selector's participle search does not survive that. The same suspicion
applies to `passiv_praeteritum`.

Treat those two numbers as a bug report about our own code, not a measurement
of the corpus. That is worth catching: without a corpus baseline there was
nothing to notice it against.

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
