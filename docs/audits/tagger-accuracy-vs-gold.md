# How much of our defect rate is the tagger's fault

TODO 4.2. First measurement of `de_core_news_sm` against hand-annotated
German, so that "spaCy mistagged it" stops being an anecdote.

## Method

3,000 sentences from UD_German-HDT (CC BY-SA 4.0, gold morphology and gold
dependencies, heise.de technology news 1996 to 2001), filtered to 5 to 18
tokens so the length is comparable to our carriers. Each sentence tagged with
`de_core_news_sm` and compared token by token against the gold annotation on
exactly the features our selectors read.

Sentences where spaCy's tokenisation did not align with gold were skipped
rather than guessed at.

## Result

29,719 tokens compared. **80.3 percent of sentences contain at least one
disagreement.**

| Feature | spaCy gives a conflicting value | spaCy gives nothing |
|---|---:|---:|
| Mood | **12.59%** | 3.00% |
| Gender | **7.90%** | 2.43% |
| Lemma | **6.69%** (17.30% before excluding punctuation) | n/a |
| Case | 6.08% | 20.26% |
| STTS tag | 5.67% | n/a |
| Tense | 5.57% | 3.00% |
| Number | 3.37% | 6.35% |
| Person | 0.40% | 7.40% |

spaCy lemmatises every punctuation mark to `--`, which is 3,380 of the 5,141
raw lemma disagreements. That is annotation convention, not error, so the
6.69% figure is the one to use.

The split matters. A **missing** feature is mostly harmless to us: the
selectors already skip a candidate whose paradigm cell cannot be determined,
which is what the `*_uncovered_by_paradigm` skip reasons in every run report
are. A **conflicting** value is what produces defects, because the code
proceeds confidently on a wrong fact.

## What this explains

**Lemma, 6.69 percent, maps directly onto our worst defect class.** Sampling
the disagreements shows exactly the pattern we keep shipping:

    form=lässt    gold=lassen    spacy=lässt      finite form not reduced
    form=muss     gold=müssen    spacy=muss       the "mussen" cue family
    form=erste    gold=erst      spacy=erster     inflected adjective lemma

That third line is the `(letzter)` and `(nächster)` cues I flagged as
cosmetic in the cycle 8 audit. They are not cosmetic; they are this.
`Tablett` for `Tablette`, `einpacksen`, `Plastigtüt`, `schalte` lemmatised to
`schalen` are all the same measurement.

**Gender at 7.9 percent conflicting** explains the wrong paradigm cell class:
`(eine)` for masculine `Orangensaft`, `(die)` for neuter `Zimmer`, and the
two the verifier caught, `Einkaufszettel` and `Akku`.

**Mood at 12.6 percent conflicting** is the highest conflict rate of any
morphological feature, which is a direct warning about the Konjunktiv II
topics now that they finally produce items.

Not every disagreement is spaCy being wrong. `form=bekannt gold=bekennen
spacy=bekannt` is gold being aggressive about a word used adjectivally, and
punctuation lemmatised to `--` is pure convention. The numbers above are
corrected for punctuation; they are not corrected for the handful of cases
where gold is the odd one out, so treat them as an upper bound.

## Caveat on the corpus

HDT is technology journalism: long sentences, heavy nominal style, many
proper nouns and technical terms. That is harder than our carriers, which are
short A1 to A2 sentences about everyday life. The true rate on our own data is
lower than these numbers. What should carry over is the **ordering**: Mood
first, then Gender and lemma, then Case, with Number and Person comparatively
safe.

**Mood having the worst conflict rate of any feature is the finding I did not
expect.** The Konjunktiv II topics produced their first items in cycle 9, so
they have never been audited, and they sit on the one feature spaCy gets
wrong most often.

## What follows from it

1. **A cue derived from a lemma is the least trustworthy thing we produce.**
   The round-trip check added in cycle 8 for plural nouns is the right shape
   and should be extended to every cued slot where a paradigm makes it
   possible: derive the surface form back from the cue and require it to
   equal the answer.
2. **The owner's cue redesign helps more than it looked.** An invariant
   citation form (`der`, `ein`, `mein`) reads no lemma and no gender, so it
   is immune to both of the top two error classes. That was adopted for
   pedagogical reasons and turns out to remove a measured tagger dependency
   as well.
3. **This is a real argument for the corpus path**, beyond sentence supply.
   A gold-annotated treebank has no lemma error rate at all. Where a topic
   depends on a feature spaCy gets wrong 8 to 17 percent of the time, taking
   the sentence from an annotated corpus removes the error rather than
   gating against it.
4. **Watch the Konjunktiv topics.** They produced their first items in cycle
   9 and sit on the feature with the worst conflict rate.

Reproduce with the script recorded alongside this note. HDT is fetched from
`raw.githubusercontent.com/UniversalDependencies/UD_German-HDT`; the corpus
is not vendored because it is roughly 260 MB across six files.
