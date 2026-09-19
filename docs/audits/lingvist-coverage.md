# Does the deck teach what a real learner saved? (19 September 2026)

The owner kept a list of 1,420 German entries from two years with Lingvist:
the words and phrases he had saved or been taught there. It arrived as a
one-column CSV and is the first outside check on the mining and the ranking,
which until now had only been judged against the corpus that produced them.

The list is not in the repository: it is personal study history, and this
repository is public. The matcher is
`scratchpad/lingvist_match.py` in the session that produced this note; it
writes a ranked copy of the list next to the input.

## What is in the list

Lingvist teaches single words, so most of the list is out of this deck's
scope by design.

| Entry kind | Count | In scope |
|---|---:|---|
| Single nouns and adjectives (`Haushalt`, `verfügbar`) | 897 | no |
| Single verbs (`schicken`, `abfahren`) | 391 | only when separable |
| Multi-word phrases (`sich verlassen auf`, `Alarm schlagen`) | 94 | **yes** |
| Case drills (`hinter dem Baum`, `auf den Tisch`) | 38 | no |

## What the deck covers

The 94 multi-word phrases are the real test.

| | Count |
|---|---:|
| Taught as a unit of its own | 55 |
| Present only inside another unit | 8 |
| No match | 31 |

Of the 31 with no match, roughly a dozen are in the deck under a
near-identical key and the matcher missed them: it could not lemmatise the
first-person forms the owner had saved (`interessiere mich für` is
`sich interessieren für`, rank 177; `um einen Kaffee gebeten` is
`bitten um`, rank 87; `dahinter stecken` is `stecken hinter`, rank 591).
About nine more are not phrases at all but fragments of sentences
(`Soweit ich weiß`, `Bleib dran`, `euch`). That leaves roughly ten genuine
gaps out of about 85 real phrases, so the deck teaches around **four in
five** of the phrases a learner actually collected elsewhere.

Single verbs land where the design says they should: 131 of 391 are units
in their own right (the separable ones), and 185 more appear inside a
collocation the deck does teach. The 897 nouns and adjectives are almost
never units, as intended, but 452 of them appear inside a phrase unit, so
the vocabulary is being met in context rather than in isolation.

## What this says about the ranking

The phrases the owner had saved sit near the top of the deck's frequency
ordering:

| Rank band | Phrases matched |
|---|---:|
| 1-500 | 46 |
| 501-2000 | 8 |
| 2001-5000 | 1 |

Median rank 190, tenth percentile 52, ninetieth 608. A list assembled by a
different tool, from a different corpus, over two years of study, lands in
the first few hundred ranks of ours. That is the strongest evidence so far
that the corpus-frequency ordering, with the everyday-corpus weighting added
on 2026-09-11, is sound.

## The one systematic gap

**Adjective + verb collocations are not a mined kind.** `ernst nehmen`,
`bereit machen`, `bekannt machen`, `fest stehen`: the miner has adjective +
noun and noun + verb, but nothing for an adjective that forms a set phrase
with a verb. Four of the ten genuine gaps are this shape, which makes it the
only pattern in the list that the deck misses for a structural reason rather
than by chance.

The rest of the genuine gaps are ordinary threshold misses, each plausible
on its own: `leben von`, `helfen bei`, `Mut haben`, `Hilfe erhalten`,
`Verhältnis haben`, and `sich begegnen`, which is absent entirely because
`begegnen` takes a dative object and rarely appears reflexive, so it never
reached the reflexive share threshold.

No action is taken on either finding here. A ninth phrase kind is a change
to the mining contract and a re-review of everything it produces; it is
recorded in `TODO.md` for the owner to decide.
