# Cycle 12 audit: the corpus pilot after the four decisions

396 accepted, 48 topics, 479 sampled. All 396 audited by hand.

Verify-only. The verification pass ran fully: 396 verified, 83 rejected,
**0 not run**.

## Headline

| | C11 | **C12** |
|---|---|---|
| Sampled | 480 | 479 |
| Accepted | 337 | **396** |
| Topics with items | 44 of 49 | **48 of 49** |
| Model rejection rate | 29.8% | **17.3%** |
| **Wrong answers shipped** | 1 | **0** |
| Wrong topic or wrong case | 5 | **6** |
| Wrong cue | 8 | **0** |
| Bad carrier | 6 | **3** |
| **Total defects** | 24 (7.1%) | **9 (2.3%)** |

The defect rate fell by two thirds and the item count went **up**, not
down. I predicted 236 items and was wrong by 160: the projection assumed
the same sample surviving new filters, but the filters changed which
candidates were drawn, and the verifier then rejected far fewer of them.

**Not one of the 396 answers is wrong German.** Every blanked word is the
correct word for its sentence. All nine defects are about which topic an
item was filed under, or about the quality of the carrier text.

| Severity | Count | Share |
|---|---:|---:|
| Wrong answer shipped | **0** | **0%** |
| Correct answer, wrong grammar topic or case | 6 | 1.5% |
| Correct item, flawed carrier text | 3 | 0.8% |

## What the four decisions did

**D1, the auxiliary cue, is the whole story of the coverage jump.** All
five topics that produced nothing in cycle 11 came back, and the ones that
were barely alive filled up:

| Topic | C11 | C12 |
|---|---:|---:|
| `futur_i` | 0 | **9** |
| `futur_ii` | 0 | **4** |
| `perfekt_sein` | 0 | **10** |
| `passiv_modalverben` | 0 | **1** |
| `zustandspassiv_zeiten` | 0 | **4** |
| `zustandspassiv` | 1 | **9** |
| `perfekt_haben` | 2 | **10** |
| `konjunktiv_ii_irreal_gegenwart` | 2 | **10** |
| `konjunktiv_ii_vergangenheit` | 3 | **6** |
| `passiv_praeteritum` | 7 | **10** |

That is 15 items becoming 73, on the same corpus, from printing three
words in brackets.

**D2's Nominative anchor gate is exact.** The topic produced 9 items, and
all 9 are settled by their own verb: `wohne`, `werde`, `gebe` for "ich";
`hast`, `bist` for "du"; `habt`, `seid`, `könnt` for "ihr". Not one third
person, which is the cell the gate permanently removes. Every one has a
single correct answer.

**D2's oblique cue works and the cue is right in all 18 cases.** `(er)` for
`ihn`, `(ich)` for `mich`, `(wir)` for `uns`, `(Sie)` for `Ihnen`. The last
one also settles capitalisation, which was an open ambiguity before.

**D3 rejected 75,764 candidates on vocabulary level**, and the accepted
items read like beginner material where they are labelled A1. The
compound-noun leak is gone.

**The junk-text filter rejected 373 carriers**, and the four caption and
heading shapes from cycle 11 do not appear.

**The cycle 11 fixes all held**: no wrong adjective cue in 396 items (8 in
337 before), no `handeln` misrouting, no colon fragments.

## The 9 defects

### 1. `passiv_praesens` catching Futur I, 3 items

    Die Ausstellung ___ noch einen weiteren Monat geöffnet bleiben. -> wird
    Die Tür des Hauses ___ geschlossen sein.                        -> wird
    Ich sage, dass das Buch von Paul gelesen werden ___.            -> wird

All three are future tense. `wird ... bleiben` and `wird ... sein` are
Futur I of `bleiben` and `sein`; `gelesen werden wird` is future passive,
where the blanked `wird` is the future auxiliary and the passive one is the
infinitive `werden`. This is cycle 10's defect class returning after cycle
11 cleared it, on a shape cycle 11 did not contain.

**Fixed.** A finite `wird`/`wurde` in the same clause as an infinitive of
`sein`, `bleiben` or `werden` is a future auxiliary, not a passive one.
Clause scoping is what makes this safe rather than blunt: "Alles, was zu
dumm ist, um gesprochen zu werden, wird gesungen." contains an infinitive
`werden` in its own `um ... zu` clause and is a genuine passive. Measured
over this sample the check rejects exactly the three defects and none of
the five correct items in the same topic.

### 2. A Dative pronoun filed as Accusative, 1 item

    Ich kann ___ nicht helfen.   -> euch   (filed as pronomen_personal_akk)

`euch` is spelled the same in both cases, so the tagger's guess decided it,
and it guessed Accusative. `helfen` governs the Dative and nothing else.
The item teaches the wrong case under the wrong topic.

**Fixed.** The fact was already in the repository and simply never
consulted from here: `paradigms.DATIVE_ONLY_VERBS`, the same hand list the
reflexive selectors have always used. The reflexive selectors derive case
from the governing verb; the personal-pronoun selectors trusted the tag.
Now both consult it.

Deliberately one-directional. A verb on the Dative-only list rejects an
Accusative claim, because that list is a positive statement. The reverse is
not available: there is no comparable list of verbs that can never take a
Dative, and inferring one from absence would reject every ordinary
transitive verb that also licenses a benefactive Dative ("Ich kaufe dir ein
Buch"). The item is dropped rather than re-routed to the Dative topic,
which costs one item and ships nothing wrong.

### 3. Reflexive case routing, 1 item

    Seit über 117 Jahren engagieren ___ alle Kinderfreunde für Kinder
      und deren Familien.                                          -> sich

`sich engagieren` is Accusative; this went to `verben_reflexiv_dat`.

Diagnosed. `Familien` counted as a bare Accusative object, which forces the
Dative reading. It is not one: it is the second half of "für Kinder und
deren Familien", inside the prepositional phrase. The walk back from
`Familien` reaches `deren`, which this tagger labels `PDS` (a substituting
demonstrative) rather than `PDAT` (an attributive one), stops there, and
never reaches `für`.

**Fixed**, narrowly. Not by widening `_governed_by_adposition`, which is
consulted from several places and would then cross into genuinely separate
noun phrases ("Er sieht für Peter und Maria einen Film" must keep "einen
Film" as a real object). The new check only ever removes an object signal,
and only for a noun standing immediately after "und"/"oder" whose left
neighbour is itself prepositionally governed.

### 4. A Nominative filed as Genitive, 1 item

    Konstantin ___ Große wird in der modernen historischen Forschung
      kontrovers diskutiert.                    -> der   (kasus_genitiv_formen)

The tagger reads "der Große" as `Case=Gen|Gender=Fem`. It is a Nominative
masculine epithet and part of the subject. Nothing about the determiner
itself catches this, because the determiner's own features are the thing
that is wrong.

**Fixed** by the clause instead. If "der Große" were Genitive, this clause
would contain no Nominative at all, and no German clause with a finite verb
looks like that.

Two guards on the rule, both found by measuring it rather than reasoning
about it. It applies only to a Genitive **not** governed by a preposition,
and only to a clause that **has** a finite verb. An earlier version without
them rejected "... sich während eines Konzerts zu unterhalten" (an
infinitive clause has no subject by construction) and "... infolge des
Bürgerkriegs in Syrien sprunghaft anstiegen" (whose clause only looks
subjectless because the tagger labelled its actual subject, "die
Flüchtlingszahlen", `Case=Acc`). Both are good items and both are kept.

### 5 to 7. Carrier text, 3 items

    Ein Film, ___ die Frage aufwirft, wie man sich im Jahr 2025
      eigentlich richtig hassen kann.                              -> der

A Leipzig headline with no main clause. The relative pronoun is right and
the item is answerable, but it is a fragment, not a sentence.

    Das Geschäft am Neuen Wall 17 ___ noch bis zum 19. Mai geöffnet. -> hat

Filed as `perfekt_haben`. "geöffnet haben" here is the fixed construction
meaning "to be open", not the perfect of "öffnen". The answer is right; the
topic is not.

    Es tut mir leid, dass ich nicht hier sein konnte für dich, wie ich
      es ___ sein sollen.                                          -> hätte

A Tatoeba translation with the wrong word order ("nicht für dich hier sein
konnte" is the German order). The blank and its answer are correct.

**None of the three is fixed**, and I am not going to guess at a rule for
them. Each needs a different judgement (a fragment detector, a
construction-vs-perfect distinction, a word-order checker), each would be
the third rule this project has written on a single example, and the colon
rule in cycle 11 already showed what that costs. They are recorded in TODO
section 1.

## What the verifier rejected, and the one thing it says loudest

83 rejections, down from 143. The single largest group is one complaint,
worded 20-odd different ways:

    Neben dem Präsens 'kann' ist auch das Präteritum 'konnte'
      eine ebenso richtige und natürliche Lösung.
    Neben 'Könnten' ist auch 'Können' eine gleichermaßen richtige Lösung.
    Neben 'Hätten' ist auch die Gegenwartsform 'Haben' ebenso richtig.

**The cue names the verb. It does not name the tense.** `(können)` leaves
`kann`, `konnte` and `könnte` all open. D1 fixed the "which verb" half of
the problem and the "which tense" half is now the whole of what is left.

It costs two topics almost everything:

| Topic | Sampled | Accepted |
|---|---:|---:|
| `modalverben_praesens` | 10 | **0** |
| `konjunktiv_ii_hoeflichkeit` | 10 | **3** |

`modalverben_praesens` had 6 accepted in cycle 11 and has 0 here, on the
same cue. That is not a code regression, it is the verifier applying the
same standard to a different draw of sentences, and it is right both times:
"Bis morgen ___ ich nicht warten. (können)" really does accept `kann` and
`konnte` alike.

**This is the next decision and it is yours.** Three options:

* **A. Require a time anchor in the carrier.** Accept a present-tense modal
  only when the sentence contains something that fixes the time ("heute",
  "gerade", "jetzt", "morgen"). Deterministic, no new machinery, and the
  same shape as the `futur_i` time-anchor check that already exists. Costs
  coverage on both topics, amount unmeasured.
* **B. Widen the accepted answers.** Store `kann` and `konnte` both as
  correct where the sentence genuinely allows both. Costs nothing, and the
  topic stops testing tense, which for `modalverben_praesens` is arguably
  the point of the topic.
* **C. Retire `modalverben_praesens` as a corpus topic** and generate its
  items instead, where the sentence can be built with the time anchor
  already in it.

My recommendation is A, because it keeps the topic honest and reuses a
check that exists. But B is defensible and free, and if the answer is "a
learner practising present modals should not be marked wrong for writing
the preterite", then B is simply correct and A is over-engineering.

## Two cue errors the verifier caught before they shipped

    Der Hinweis 'möchten' ist keine korrekte Grundform (Infinitiv),
      da die Grundform des Verbs 'mögen' lautet.        (twice in one run)

    Das zugrundeliegende trennbare Verb lautet 'vortäuschen',
      weshalb der Hinweis '(täuschen)' nicht die korrekte Grundform ist.

Neither reached a learner. The first hit TODO section 1's standing bar (the
verifier catching the same thing twice becomes a deterministic rule) and is
**fixed**: `möchte` is a Konjunktiv II form of `mögen` and is now cued
`(mögen)`. An existing test had frozen `(möchten)` as the expectation; it is
corrected, with the reason in the test.

The second, a separable verb cued with its bare stem, is one occurrence and
is left to the verifier for now.

## Tatoeba against Leipzig

| | Items | Defects | Rate |
|---|---:|---:|---:|
| Tatoeba | 308 | 6 | 1.9% |
| Leipzig | 88 | 3 | 3.4% |

Both improved sharply (5.2% and 11.4% in cycle 11). Leipzig's share of
accepted items fell from 31% to 22%, which is the vocabulary filter and the
junk-text filter both working on the harder source.

## One thing that is not a defect but should be recorded

One accepted `partizip_i_attributiv` item is a news report of a woman
attacking her sleeping husband with a sledgehammer. There is no content
filter on Leipzig carriers at all, and cycle 11 turned up a quote about
genocide. Neither is a grammar problem. If it matters for a learner app, a
small blocklist applied to Leipzig only is cheap; say the word and I will
build it.

## Coverage

48 of 49 topics produced items. `modalverben_praesens` is the one that did
not, for the tense reason above, and it sampled 10.

`futur_ii` (4 of 10) and `zustandspassiv_zeiten` (4 of 5) are still short of
quota. Those two are genuinely rare in 80,000 corpus sentences and remain
the case for AI generation.

## After the fixes

Every one of the 396 accepted items was re-run through the changed
pipeline. **390 survive; the 6 removed are exactly the 6 grammar defects
above, with nothing swept up alongside them.**

## Verdict

The four decisions delivered on every axis at once, which is not what I
expected: more items (396 against 337), more topics (48 against 44), fewer
model rejections (17.3% against 29.8%), and a third of the defects (2.3%
against 7.1%). Zero wrong answers.

What is left is one design question, not a bug: a cue that names the verb
does not name the tense, and two topics cannot survive that on corpus
sentences alone.
