# Known and accepted defects

Every defect class this pipeline is known to let through, with real examples,
why no rule catches it, and what would have to exist before one could. Nothing
here is a task. `TODO.md` section 1 is the work list; this file is the
explanation, written so it can be read in five minutes.

Classes 2.10 to 2.14 are the one exception to "known to let through", added in
cycle 17 and marked as such where they start: each of those already has a
deterministic rule and does not reach a learner today. They are listed with the
rest because the model verification pass catches none of them, so those rules
have nothing standing behind them.

Every example below is a real sentence from a real pilot or a real audit. None
is invented. Where a class has only one recorded example, it says so rather
than padding.

---

## 1. What it costs, in numbers

### The 430-item hand audit

All 430 accepted items from the last corpus pilot were read by hand, one at a
time. **10 defects, 2.3%.**

| | |
|---|---:|
| Items read | 430 |
| Bad German | 4 |
| Correct German, wrong English translation | 4 |
| Wrong English caused by the cross-corpus id collision (since fixed) | 2 |
| **Total** | **10 (2.3%)** |

The two collision defects are the pair recorded in `docs/audits/fix-log.md`
under Cycle 15:

```
Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung
schlimmer ist als gedacht.
    "She crossed the street."

Jetzt gibt sie ein Update zu ihrem Alltag während der Chemotherapie.
    "Bye!"
```

Both German sentences are news prose from Leipzig. Both English sentences came
from Tatoeba, because the two corpora number their lines in the same range and
the store joined them on that number. That cause is fixed.

**This 2.3% describes a configuration that no longer exists, and that is the
honest caveat on it.** It was measured against a pilot whose English glosses
were largely Tatoeba's. As of 2026-08-27 Tatoeba glosses are not used for
exercises at all, so three of the four wrong-English defects and both collision
defects are drawn from a source the pipeline no longer reads. The next audit
will measure a different pipeline. Do not quote 2.3% as the current rate.

### Translation quality: machine against Tatoeba

Two hand checks, 120 pairs each, same size, different sources.

| | Machine translation (2026-08-27) | Tatoeba (earlier) |
|---|---:|---:|
| Sample size | 120 | 120 |
| Clean | 116 | 112 |
| Loose but usable | about 4 | 6 |
| **Outright wrong** | **0** | **2** |
| Mispaired with the wrong German sentence | 0 | not applicable |

The four machine-translation cases are drift, not error. All three of the ones
worth naming:

```
Du darfst gehen.
    "You can go."
        loses the permission sense; "may" is what darfst means

Man stellt diese Kiste aus Holz her.
    "This box is made of wood."
        turned an active sentence into a passive one

Sie können Einer dem Anderen helfen.
    "They can help one to the other."
        should be "help each other"
```

Two conclusions, both load-bearing:

1. **Machine translation is measurably better on the number that matters.**
   Zero outright wrong against two.
2. **Machine translation structurally cannot mispair.** It translates the
   sentence it is handed. A lookup table can hand back the wrong row, and did,
   7,365 times in the owner's own store. That is not a quality difference, it
   is a difference in what can go wrong at all.

---

## 2. The defect classes

### 2.1 Words that do not go together

**What it is.** Every word is a real German word, every ending is right, and a
native speaker would still never put those words side by side.

**Examples**, all four from the last pilot's own output:

```
Sie schnaubten wegen ihres kleinen Gehalts.
    Nobody snorts BECAUSE OF a salary, and a salary is niedrig (low),
    never klein (small).

Sie erreichte einen großen Erfolg in ihrem Geschäft.
    German says einen Erfolg erzielen or Erfolg haben, not erreichen.

... war mein Eindruck über die jeweiligen Landsleute klar.
    The preposition after Eindruck is von, not über.

Darüber hinaus können beigefügte Bilder anhand der Google-Bildersuche
entlarvt werden.
    Images get überprüft (checked). Entlarven is what you do to a liar.
```

**Why no rule catches it.** A rule would have to know which verbs go with
which nouns, and which nouns take which prepositions. That is a collocation
dictionary. This repository has a frequency list, a word list and a
morphological tagger, and none of the three carries that information. Nothing
structural substitutes: all four sentences parse perfectly.

**What would have to exist.** A German collocation lexicon, either licensed or
built from a very large corpus by counting which words co-occur far more often
than chance would predict. That is a project in itself, not a rule.

Recorded so the standing "anything the verifier catches twice becomes a
deterministic rule" policy is not read as applying here. It cannot.

### 2.2 Word order

**What it is.** German puts time before manner before place. English does not,
so an English sentence translated word by word lands its time phrase in the
wrong spot.

**Examples**, two:

```
Komisch, ich träumte zweimal den selben Traum letzte Nacht.
    "letzte Nacht" is stuck on the end the way English does it; German
    wants it early. Separately, "den selben" is one word, "denselben".
    This one was ACCEPTED.

Ich arbeite mit einem Computer den ganzen Tag.
    Time ("den ganzen Tag") has to come before manner ("am Computer").
    This one the verifier REJECTED, in its own words: "im Deutschen steht
    die Zeitangabe 'den ganzen Tag' vor der Angabe der Art und Weise".
```

**Why no rule catches it.** Word order in German is genuinely free enough that
almost any ordering is grammatical. What is wrong here is that it sounds
translated, which is a judgment about naturalness, not about grammar. A rule
that enforced time before manner would reject correct sentences that front a
phrase for emphasis, which German does constantly.

**What would have to exist.** A model of how natural a word order sounds, which
is what the verifier already is. The two examples above are the same defect and
it caught one of them. See 2.9.

### 2.3 Swiss spelling with the letter ä

**What it is.** Switzerland does not use ß, so Swiss German writes `ss` where
standard German writes `ß`. Leipzig contains Swiss publications, so Swiss
spellings reach carriers.

**Examples:**

```
Strässchen     Swiss. Standard German is Sträßchen.
Fässer         standard German, correct as written.
Pässe          standard German, correct as written.
Gässchen       standard German, correct as written.
```

**Why no rule catches it.** The shipped rule handles the cases it can decide
(diphthongs before `ss`, plus a closed list). It cannot decide `ä`, because the
answer depends on whether the vowel is long or short, and German spelling does
not mark vowel length reliably enough to read it off the string. `Strässchen`
has a long `ä` and is Swiss; `Fässer`, `Pässe` and `Gässchen` have a short `ä`
and are perfectly standard. The four look identical to any rule.

**What would have to exist.** A pronunciation dictionary giving vowel length
per word, or a list of every German word in this shape. Recommendation already
given and accepted: report it in each audit instead of pretending a rule
exists.

### 2.4 Datives with no verb to look up

**What it is.** The pipeline decides which case a phrase should be in by
looking up the governing verb in a lexicon built from corpus evidence. Some
datives have no governing verb to look up.

**Examples**, two:

```
Ich wasche mir die Hände.
    "mir" is there to say the hands are mine. Waschen does not demand it.

Meiner Schwester ist es kalt.
    The dative belongs to the adjective "kalt", not to "sein".
```

**Why no rule catches it.** The lexicon is keyed on verbs. Neither sentence has
a verb that governs the dative, so there is nothing to look up. These items are
dropped rather than risked.

**What would have to exist.** A second lexicon keyed on adjectives, plus an
explicit account of free datives, which are a construction rather than a word
and so cannot be listed at all.

### 2.5 He, she or it

**What it is.** For the topic that blanks a nominative personal pronoun
(`pronomen_personal_nom`), third person singular is dropped entirely.

**Example.** There is no useful single example here and inventing one would
misrepresent the class, so here is the mechanism instead. Blank the subject of
`Er sagte das damals nicht` and the remaining sentence is `___ sagte das damals
nicht`. `Er`, `sie` and `es` all fit. The verb form `sagte` is identical for all
three, so the sentence cannot settle it.

**Why no rule catches it.** No German verb form anywhere distinguishes `er`
from `sie` from `es`. This is a fact about the language, not a gap in the code.

**What would have to exist.** Coreference resolution: working out from the
surrounding text who is being talked about. This package does not have it, and
carriers are single sentences, so often the surrounding text does not exist
either. The cell is dropped on purpose.

### 2.6 Caption bits in brackets

**What it is.** Scraped news text carries photo-caption furniture in brackets.
It looks exactly like ordinary journalistic apposition, which is correct German.

**Examples:**

```
Masi Pfand (am Ball) befindet sich aktuell in einer sehr guten Form.
    "(am Ball)" marks where a player is in a photo. Caption junk.

Bundeskanzler Friedrich Merz (CDU) hat am Sonntag mit dem israelischen
Ministerpräsidenten Benjamin Netanjahu telefoniert.
    "(CDU)" is a party affiliation. Correct German, printed daily.
```

**Why no rule catches it.** The two have the identical parse: a short
verbless bracketed insert between a proper-noun subject and its finite verb.
The structural rule was built and measured over 40,000 Leipzig lines. Of the
150 hits the pipeline otherwise accepts, **23 were position markers and 127
were legitimate**: party affiliations, ages, abbreviation glosses, goal
minutes. That is five and a half correct sentences thrown away per piece of
junk caught. The rule was measured and dropped.

**What would have to exist.** Nothing structural can do it, because the
structures are the same. What separates the two groups is the vocabulary
inside the brackets, so the only safe direction is widening a closed list of
position words (`links`, `rechts`, `Mitte`, `vorn`, `am Ball`) one entry at a
time, and only for a phrase a real pilot actually turns up.

### 2.7 A capitalised verb

**What it is.** German capitalises nouns, and any verb can be turned into a
noun, so a capitalised verb is usually a real word. Occasionally it is a typo
in the source.

**Example.** Only one is recorded, and this class is here on the strength of
that one:

```
Es gibt in der Nähe auch ein Seniorenheim, dessen Bewohner im Wasserwald
Spazieren gehen.
```

`spazieren gehen` is lowercase. `das Spazieren` is a real German noun, so no
dictionary can call the string `Spazieren` wrong. Only its position next to
`gehen` makes it wrong here.

**Why no rule catches it.** The word list cannot help, because the word is
real. The tagger cannot help either, and its failure is circular: it labels
`Spazieren` a noun largely because it is capitalised.

**What would have to exist.** A rule about the specific pattern "capitalised
infinitive immediately followed by a motion verb". That is a rule written on a
single example, which this project has already learned costs more than it
saves. Left to the verifier, which did catch this one, in its own words:
"'Spazieren gehen' ist orthografisch fehlerhaft".

### 2.8 Leipzig has no content filter

**What it is.** Leipzig is scraped news. Nothing screens carriers for subject
matter.

**Examples.** Two occurrences are recorded, both described rather than quoted
in the audits, so they are described here too rather than reconstructed:

- Cycle 11 accepted a carrier containing a quote about genocide.
- Cycle 12 accepted a `partizip_i_attributiv` item drawn from a news report of
  a woman attacking her sleeping husband with a sledgehammer.

**Why no rule catches it.** Nobody has written one. Neither sentence is a
grammar defect, and every grammar check in the pipeline passed them correctly.

**What would have to exist.** A word blocklist applied to Leipzig carriers
only. It is cheap and it has not been built because it has not been asked for.
Say the word.

### 2.9 The verifier does not agree with itself

**What it is.** The model verification pass is the only thing standing between
several of the classes above and a learner. Asked the same question about the
same item twice, it gives the same answer about **92%** of the time.

**Example.** Cycle 14 re-ran the identical 475 candidates from cycle 13. 64
items were newly accepted. 56 of those are explained: the verifier had gained
access to the English translation, which settles the tense ambiguity that
caused most of the earlier rejections. **The other 8 were rejected the previous
run for bad German, which a translation says nothing about, and accepted this
run.** Every one of the 8 was a correct rejection the first time:

```
Masi Pfand (am Ball) befindet sich ...              caption residue (2.6)
Sie erreichte einen großen Erfolg ...               collocation (2.1)
Ja", gesteht Norris, ...                            opens mid-quotation
Erst am 6. November 2021 wurde damals ...           date plus "damals"
... war mein Eindruck über die jeweiligen ...       collocation (2.1)
Sie schnaubten wegen ihres kleinen Gehalts.         collocation (2.1)
... Bilder anhand der Google-Bildersuche entlarvt   collocation (2.1)
Ein Film, der die Frage aufwirft, ...               no main clause
```

Two of those now have deterministic rules. Five are collocation errors no rule
here can reach. One is too narrow to rule.

A separate experiment changed how many items go into one prompt, 20 against 5,
on the same 475 candidates. 9 items flipped one way and 3 the other, and all 12
were read by hand, and **all 12 are genuinely bad items**. So batch size is not
the lever. About 2.5% of items are decided by which run you happen to look at.

**Why no rule catches it.** It is not a rule's job. It is the instrument being
unsteady.

**What would have to exist.** Running the pass more than once and rejecting
anything any pass rejects. That is built:
`step7_corpus_pilot.py --verification-passes N`, default 1. The union of two
passes caught all 12 of the items above; either single pass did not. What is
still unknown is how many passes are worth paying for.

The related unknown underneath it is no longer unknown. The verifier reads the
English translation and relaxes its judgment against it, and on **2026-08-28**
`scripts/eval_gloss_adversarial.py` was run against
`data/fixtures/adversarial/wrong_glosses.jsonl` for the first time: 36 matched
pairs, each item verified once with a deliberately wrong translation and once
with its real one, in separate calls. It catches **22 of 36** wrong
translations, with **0 false positives** on the 36 correct ones. The zero is the
good half: the pass does not invent translation defects, so nothing here argues
for loosening it. The 22 splits very unevenly by kind, and one kind scores zero.
That kind is 2.15.

---

**The five classes below are a different kind of entry, and the difference is
the point.** Every one of them already has a deterministic rule, shipped and
checked against these exact sentences. They are here because the model
verification pass misses all five, every time, and it will keep missing them:
its prompt is deliberately given the gap, the cue, the English translation and
the stated answer and nothing else (CLAUDE.md rule 2, so the model judges the
item the way a learner does), and four of the five are defects you cannot see
without knowing which topic the item was filed under. So these are not classes
that get through today. They are classes with exactly one thing standing
between them and a learner, and no second opinion behind it. Measured in
`docs/audits/fix-log.md` cycle 17.

### 2.10 A reflexive pronoun filed under the wrong case

**What it is.** German reflexive verbs take either the accusative or the
dative, and the pipeline has a topic for each. An item can be correct German
with exactly one right answer and still be filed under the wrong one of the
two, which teaches the learner the opposite of the truth.

**Examples**, three of the six from cycle 9:

```
Wir wünschen ___, dass Sie uns Ihre ehrliche Meinung mitteilen.   -> uns
    Filed as accusative. "sich etwas wünschen" is dative, and the
    object it wants is the whole dass clause.

Um zwei Uhr treffen wir ___ alle vor dem Haupteingang.            -> uns
    Filed as dative. "sich treffen" is accusative; "alle" is an
    apposition to the subject, not a direct object.

...weil ___ darauf viel Staub angesammelt hat.                    -> sich
    Filed as dative. "viel Staub" is the nominative subject.
```

**What already catches it.** `selectors._reflexive_case`, which asks the
corpus-built verb government lexicon (`verb_government.reflexive_verdict`)
first and falls back to a structural object test. The structural half is what
these six needed: `_followed_by_dass_clause_object` for the first shape, and
`_has_bare_accusative_object` excluding time adverbials, quantifier
appositions and nominative-headed phrases for the second. Run against all six
sentences today, every one routes to the right topic.

**Why the verifier never will.** The item is good German with one right
answer. The only thing wrong with it is the topic, and the verifier is never
told the topic. It caught 0 of 6.

### 2.11 A cue in the wrong case

**What it is.** The bracketed cue is meant to be the answer's citation form. A
gap at the very start of a sentence capitalises its answer for a reason that
has nothing to do with the word, and `TypoGrader` strict-fails a capitalisation
mismatch by design, so a learner who types exactly what the cue shows is marked
wrong.

**Examples**, both from cycle 8:

```
___ Batterien können im Supermarkt abgegeben werden.   -> Alte    cue: alt
___ Woche hatte ich plötzlich fiese Bauchschmerzen.    -> Letzte  cue: letzter
```

**What already catches it.** `selectors._cue_case_matched_to_answer`, one line,
applied at `_citation_cue`, which is the single choke point every cue in that
module passes through. It returns `Alt` and `Letzter` for those two.

**Why the verifier misses it anyway.** This one it can see: its fourth question
asks whether the cue is the answer's correct citation form. It answered yes to
both. This is the only one of the five that is a plain miss on a question the
pass is actually asked, rather than a defect outside its view.

### 2.12 Futur I that is really the present passive

**What it is.** `werden` plus an infinitive is Futur I. `werden` plus a past
participle is the present passive. Both put the same finite `werden` in the
gap, so an item can be filed under `futur_i` while testing the passive.

**Examples**, both from cycle 9:

```
Das Smartphone ___ jetzt aufgeladen, damit Sie es am Abend sofort
nutzen können.                                                    -> wird

...dass das Fleisch scharf angebraten ___, wenn ein tolles Aroma
entstehen soll.                                                   -> wird
```

**What already catches it.** `_select_futur_i` requires a bare infinitive in
the finite verb's own clause and excludes any participle in it. The obvious
version of this rule does not work: `de_core_news_sm` tags `angebraten` and
`aufgeladen` as `VVIZU`, not `VVPP`, so the check uses `_is_participle` (tag or
spelling shape) rather than the tag alone. Run today, both sentences produce no
`futur_i` candidate at all.

**Why the verifier never will.** Same as 2.10. Both sentences are correct
German with one right answer; only the topic is wrong, and the verifier does
not see the topic. It caught 0 of 2.

### 2.13 A comparative that is not comparing, and a Konjunktiv in the wrong tense

**What it is.** Two more topic-attribution errors of the same family, kept
together because they share a cause: a surface form that belongs to one topic
in one reading and to another in a second.

**Examples**, both from cycle 9:

```
___ trinken wir dann gemeinsam eine Tasse Kaffee als kleines
Dankeschön.                                          -> Später   cue: Spät
    Filed as comparative. "Später" here means "afterwards", and
    "als kleines Dankeschön" means "as a small thank-you". Nothing
    in the sentence is being compared to anything.

Wenn ich nur etwas früher auf meine Ernährung geachtet ___, wäre ich
jetzt bestimmt fitter.                               -> hätte
    Filed as present irrealis. The condition is in the past, so the
    blanked form belongs to konjunktiv_ii_vergangenheit.
```

**What already catches it.** For the comparative, a tag distinction spaCy does
make and that is easy to overlook: comparative `als` ("schneller als sein
Bruder") tags `KOKOM`, the unrelated "as"/"in the role of" homograph tags
`APPR`, and `_select_komparativ_superlativ` requires `KOKOM`. For the
Konjunktiv, `_select_konjunktiv_ii_base` excludes a clause-local participle in
both directions, because a verb-final `wenn` clause puts the participle before
its auxiliary. Run today, the first sentence yields no comparative candidate,
and the second offers `hätte` only under `konjunktiv_ii_vergangenheit` while
still correctly offering `wäre` under `konjunktiv_ii_irreal_gegenwart`.

**Why the verifier never will.** Same as 2.10 and 2.12. Correct German, one
right answer, wrong topic. It caught 0 of 2.

### 2.14 Swiss spelling somewhere else in the sentence

**What it is.** 2.3 above is about the one Swiss spelling no rule can decide.
This is about the ones a rule can, sitting in the carrier rather than in the
answer, where they are just as wrong and just as visible to a learner.

**Examples**, the two carriers from cycle 9 that produced four items between
them:

```
Schliesslich ___ ich mich auf meinen festen Platz gesetzt und wartete
auf den Beginn der Vorstellung.                                   -> hatte
    Standard German is "Schließlich". This carrier alone produced
    three items.

Gleich danach ___ ich noch einmal kurz nach draussen gegangen, um
frische Luft zu holen.                                            -> war
    Standard German is "draußen".
```

**What already catches it.** `carrier_validation._sentence_shape_reason`
rejects both, before spaCy is loaded, as `swiss_spelling`. `Schliesslich` falls
to the general diphthong rule (`ie` before `ss`, and a diphthong is always
long), `draussen` to a one-word closed list, because a general `au` plus `ss`
rule would reject `aussteigen`, `Aussage` and dozens of other correct words.
**The `ä` limitation in 2.3 does not apply to any of these four**, which is
worth stating plainly: that limitation is real, and it is not what let these
through.

**Why the verifier misses it anyway.** Like 2.11, this is in view: its third
question asks whether every word in the completed sentence is a real German
word someone would actually use. A Swiss spelling is a real German word, in
Switzerland, which is presumably why it splits: 2 of 4.

---

**The class below is not one of the five above.** It is the other kind: nothing
catches it, deterministic or otherwise. It is last only because it was measured
last.

### 2.15 An English translation with the wrong number

**What it is.** Every exercise shows its English translation, and the learner is
meant to use it. When the translation says one thing and the German says
several, or the other way round, the translation points at the wrong answer and
the learner is marked wrong for reading it.

**Example.** From the 2026-08-28 gloss eval fixture:

```
Ich habe schöne ___ gesehen.                                  -> Häuser
    "I saw a beautiful house."
```

The learner reads the English, answers `Haus`, and is graded incorrect. The
German is fine. The item is fine. Only the English is wrong, and it is the half
the learner was told to trust.

**Why no rule catches it.** No question in the live verification instruction
asks whether the translation is correct. The four questions are about the
German, the answer's uniqueness, whether every word exists, and the cue. The
translation enters question 2 only as a reason to rule an alternative out. So
every catch in the table below is incidental, and this is the one kind where
nothing was caught by accident:

| Wrong-translation kind | Caught |
|---|---:|
| Tense | 6 of 6 |
| Polarity | 5 of 6 |
| Unrelated sentence | 5 of 6 |
| Person | 4 of 6 |
| Definiteness | 2 of 6 |
| **Number, singular against plural** | **0 of 6** |

Number is the worst kind to be blind to, because it is the kind that changes the
answer. A tense slip in the English leaves `Häuser` the only thing that fits the
gap; a number slip does not.

**What would have to exist.** Either a fifth question in the verification
instruction, asking directly whether the English matches the German, or a
deterministic check comparing the answer's number against the number of the
matching English noun phrase. `en_core_web_sm` is already installed and
`src/generation/gloss_validation.py` already uses it. This one is open work
rather than an accepted limit: `TODO.md` item 4.

---

## 3. What this adds up to

The fifteen classes above split into two groups, and they are not the same
kind of problem.

**Ten classes where no rule exists.** Four (2.1, 2.2, 2.6, 2.7) have no rule
and are held by the verifier alone, and the verifier is the class in 2.9. Two
(2.4, 2.5) are handled by dropping items rather than risking them, which costs
coverage and never costs correctness. One (2.3) is reported in every audit
instead of fixed. One (2.8) is a filter nobody has asked for yet. One (2.15) is
held by nothing at all, and is the only entry in this file that is open work.

The measured cost of that group was 10 bad items in 430, and half of those were
translation problems whose source has since been removed from the pipeline
entirely.

**Five classes where a rule exists and is the only thing there.** 2.10 to 2.14
do not get through today. Each has a deterministic rule, shipped and checked
against the exact sentences above. What they have in common is that the model
verification pass catches none of them, so nothing is standing behind those
rules: if one regresses, the next audit finds out by hand, months later. Four
of the five (2.10, 2.12, 2.13, and half of what 2.11 is about) are invisible to
the verifier by construction, because it is never told which topic an item was
filed under and that is exactly what is wrong with them.

The measured cost of that group is zero items today and unbounded on the day a
selector changes. It is a different risk, and it is the reason these are
written down alongside the other nine rather than treated as closed.

**And the verifier's own numbers, measured for the first time in cycle 17**
(`scripts/eval_verifier.py --batch-size 5`, `$0.065604`):

| | |
|---|---:|
| Recall on 38 hand-confirmed defects | 60.5% |
| Recall on the 20 of those it can actually see | 75.0% |
| False-positive rate on 31 hand-confirmed clean items | **12.9%** |

The false-positive rate is the number to sit with. Roughly one good candidate
in eight is discarded by the pass whose job is to be a backstop, and unlike a
miss, a discarded item leaves no trace: it is simply not in the bank. At 1,225
items that is about 181 good candidates thrown away, which is affordable where
the corpus is rich and is not where it is thin. `--verification-passes 2`
(2.9) rejects on any pass's rejection, so it compounds this deliberately:
somewhere between 12.9% and 24.1%, not yet measured.
