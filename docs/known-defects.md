# Known and accepted defects

Every defect class this pipeline is known to let through, with real examples,
why no rule catches it, and what would have to exist before one could. Nothing
here is a task. `TODO.md` section 1 is the work list; this file is the
explanation, written so it can be read in five minutes.

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
still unknown is how many passes are worth paying for, and there is a related
unknown underneath it: the verifier now reads the English translation and
relaxes its judgment against it, and **nobody has yet measured whether it
notices a translation that is wrong.** The fixture and the harness for that
measurement exist (`data/fixtures/adversarial/wrong_glosses.jsonl`,
`scripts/eval_gloss_adversarial.py`); they need a key and a run.

---

## 3. What this adds up to

Of the nine classes above, four (2.1, 2.2, 2.6, 2.7) have no rule and are held
by the verifier alone, and the verifier is the class in 2.9. Two (2.4, 2.5) are
handled by dropping items rather than risking them, which costs coverage and
never costs correctness. One (2.3) is reported in every audit instead of fixed.
One (2.8) is a filter nobody has asked for yet.

The measured cost of all of it together was 10 bad items in 430, and half of
those were translation problems whose source has since been removed from the
pipeline entirely.
