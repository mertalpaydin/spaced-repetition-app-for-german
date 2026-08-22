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

## Addendum (2026-08-22): the interrogative/2nd-person blind spot, measured

TODO.md 1.4 and 1.5 found `de_core_news_sm` tagging a genuine 2nd-person-
singular `-st` finite verb (`Kannst du mich ...?`, `vergiltst du mir ...!`)
as `Person=1` -- in an inverted question and an inverted exclamation, both
word orders essentially absent from HDT's news prose. The Person figure
above (0.40% conflicting) is the safest-looking row in the whole table,
which made it easy to trust for a register HDT does not actually sample.
That trust was misplaced for exactly the register this app is built on.

**Method.** No gold-annotated corpus with the right register was on hand
(the honest gap this addendum exists to name), so this does the cheaper,
still-decisive thing: German grammar makes the `-st` present/preterite/
Konjunktiv-II ending unambiguously 2nd person -- no `(Person, Number)` cell
of any German verb, modal or lexical, regular or irregular, is ever spelled
with a final `-st` for 1st person. A finite verb (`VVFIN`/`VAFIN`/`VMFIN`)
whose surface text ends in `-st` and is tagged `Person=1` is therefore a
tagger error by the paradigm alone, no gold annotation needed.
20,000 length-plausible sentences each from Tatoeba and Leipzig (the same
corpora and the same `scripts/corpus_reading.py` reader `step7_corpus_
pilot.py` uses), tagged and scanned for every finite verb ending in `-st`.

## Result

| Corpus | Finite verbs | Ending in `-st` | Tagged `Person=1` (wrong) |
|---|---:|---:|---:|
| Tatoeba (dialogic) | 25,643 | 5,008 | 74 (**1.48%**) |
| Leipzig (news/web prose) | 25,452 | 2,524 | 3 (**0.12%**) |
| Combined | 51,095 | 7,532 | 77 (**1.02%**) |

The gap between the two corpora is the finding, not the combined number:
Tatoeba, full of questions and direct address ("Kannst du ...?"), shows the
error at **more than ten times** Leipzig's rate. HDT sits even further from
Tatoeba's register than Leipzig does (HDT is technology journalism, the
same declarative-prose shape as Leipzig, with markedly fewer second-person
constructions still), so the true rate on HDT is likely lower than even
Leipzig's 0.12% -- which is exactly why the original 0.40% Person figure
looked safe and was not.

**1.02% understates the error on the forms that actually matter.** The
`-st` bucket above is dominated by `ist` ("sein", 3rd singular) -- 
unambiguous by spelling (2nd singular is the unrelated form `bist`), never
at risk of this confusion, and never observed mistagged in the sample.
Diluted by that, and by genuinely syncretic sibilant-stem forms ("isst",
"reist", "lässt" -- 2nd and 3rd singular share one spelling by a real
orthographic rule, so a `Person=3` tag on one of those is not necessarily
wrong either, see `selectors._finite_verb_person`'s own module comment),
the 1.02% figure is a floor, not a ceiling: almost every sampled mistag is
the modal `kannst`/`Kannst`, the single most common 2nd-person question
form in conversational German, in exactly the "Kannst du ...?" shape this
task's own reported sentence used.

## What follows from it

1. **The original Person row (0.40%) is not wrong, it is incomplete.**
   HDT's declarative-prose sample essentially cannot surface this defect;
   a learner app that is full of questions and imperatives runs headlong
   into it. Treat every feature row in the table above as measured on
   declarative registers only, not as a bound on interrogative/2nd-person
   accuracy for any feature, not only Person.
2. **The fix applied (TODO.md 1.4/1.5, `selectors._finite_verb_person`)
   corrects `Person=1` unconditionally but deliberately leaves `Person=3`
   alone** -- confirmed while building it that blindly correcting `Person=3`
   too breaks a genuinely-3rd-person sibilant-stem form ("Der Körper
   passt ... an.", `passt` correctly `Person=3`), which the module comment
   documents as a found asymmetry, not an assumption.
3. **Re-running this same measurement is the honest way to watch the fix's
   own limits**: it catches the mistagging, not the sentences it appears
   in -- both reported sentences additionally carry an unreduced lemma
   (`Kannst`/`vergiltst` instead of `können`/`vergelten`) that keeps them
   from reaching a shipped item through the full generation pipeline
   regardless of this fix. See `docs/audits/fix-log.md` for the full
   accounting.

Reproduced with a one-off script (not vendored) that reuses `scripts/
corpus_reading.py` and `src.generation.blanking.sentence_tagger.tag_sentence`
exactly as `step7_corpus_pilot.py` does; seed and limit match that script's
own defaults for the corpus read.
