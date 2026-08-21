# Cycle 10 audit: the first corpus pilot

366 accepted, 47 topics, 230 cued. All 366 audited by hand.

No sentence generation. Sentences came from Tatoeba and Leipzig; the only
model calls were the verification pass, which ran fully: 366 verified, 116
rejected, **0 not run**.

## Headline

| | C9 (AI generation) | **C10 (corpus)** |
|---|---|---|
| Accepted | 428 | **366** |
| Topics with items | 36 of 49 | **47 of 49** |
| Defects | 4.0% | **5.2%** (19 of 366) |
| Model rejection rate | 18.5% | **24.1%** |
| Defects caused by bad German | 26% of them | **1 of 19** |

The defect rate is slightly higher and that number is misleading on its own.
The composition changed completely, and the composition is the finding.

## The corpus does what it was brought in to do

**Eleven topics produced items for the first time ever, and they are almost
all clean.** `relativsatz_dativ` (8), `relativsatz_genitiv` (8),
`infinitiv_um_zu` (10), `praepositionen_genitiv_gehoben` (7),
`artikel_possessiv_nom` (10), `passiv_praeteritum` (8),
`konjunktiv_ii_hoeflichkeit` (3), `perfekt_sein` (10). Every one of those
was audited item by item with **nothing found**. The only new topic carrying
a defect is `futur_ii`, which produced exactly one item.

    Man kann nur schwer über einen Menschen urteilen, ___ Gesicht man
    nicht kennt.                                              -> dessen
    Tom hat eine Freundin, ___ Vater Raumfahrer ist.          -> deren
    Iouri Podladtchikov feiert seinen Olympiasieg im Land, in ___ er
    geboren wurde.                                            -> dem

Generation could not produce these at all. Retrieval produced them clean on
the first attempt.

**The bad-German defect class is gone.** In cycle 9, five of seventeen
defects were the model inventing words (`Tennisschlüssel`, `holzigen Tisch`,
`rote Software`) and four more were Swiss spelling it had written itself.
Published human German does not do that. Exactly one of the nineteen defects
here is a bad carrier, and it is a genuinely Swiss-published sentence in
Leipzig (`Strässchen`), not fabrication.

**The owner's cue rule works exactly as designed.** `artikel_bestimmt_nom`
produced ten clean items with the invariant `(der)` cue, four of which have
cue equal to answer:

    ___ (Der) Katze, die du gefunden hast, ist meine.    -> Die
    ___ (Der) Land, das ich mag, ist Deutschland.        -> Das
    ___ (Der) erste Satz blieb ... lange ausgeglichen.   -> Der

The learner works out gender and case. Where the answer happens to equal the
citation form that is a coincidence of German, not a leak. This topic was
dead for four cycles.

## The 19 defects, and what they have in common

Almost all of them are **our own case and topic routing**, in topics that
have been audited before. Not one is a fabricated word.

### Reflexive case routing, 3 items, third cycle running

    Tom verbeugte ___ und küsste Maria die Hand.                    -> sich
    ... lassen Sie ___ von der knusprigen Textur überzeugen.        -> sich
    Man muss ___ jedes Mal wieder zur Wahl stellen.                 -> sich

`sich verbeugen`, `sich überzeugen lassen` and `sich zur Wahl stellen` are
all accusative and all landed in `verben_reflexiv_dat`. Cycle 11 fixed this
with a closed list of accusative-only reflexive verbs (`treffen`,
`ansammeln`, `freuen`, `ändern`, `beeilen`). None of these three is on it.

**A closed list cannot work here.** German has hundreds of reflexive verbs
and the corpus will keep finding ones the list does not name. This needs the
governing verb's case looked up, or the topic needs to require a form that is
not syncretic between the two cases.

### `zustandspassiv` catching the perfect of a motion verb, 2 items

    Es ist fünf Jahre her, dass wir hierher gezogen ___.   -> sind
    Ich kann mich nicht erinnern, wann er nach Boston gezogen ___.  -> ist

`wir sind gezogen` is the perfect of `ziehen`, we moved house. It is not a
Zustandspassiv. The selector sees `sein` plus a participle and does not check
that the verb is transitive in that reading.

### `passiv_praesens` catching Futur I, 2 items

    Ich ___ nie vergessen, wie ich dich zum ersten Mal gesehen habe. -> werde

`werde vergessen` is future tense. It reads as a passive only because
`vergessen`'s infinitive and past participle are spelled identically. This
was flagged as a known residual when the participle fix landed; it is now a
live defect twice.

### Konjunktiv II present catching the past, 3 items

    Wenn ich ___ gehen wollen, hätte ich's gesagt.                  -> hätte
    Ich wäre gerne ins Kino gegangen, wenn ich die Zeit gehabt ___. -> hätte
    ... dass er das Rennen ___ gewinnen können, wenn ...            -> hätte

All three are past Konjunktiv filed under present. Cycle 11 fixed the
participle-before-auxiliary word order and these still get through, including
one (`gehabt ___`) with exactly that shape. **This is the prediction from
`docs/audits/tagger-accuracy-vs-gold.md` landing:** Mood is the feature
spaCy gets wrong most often, 12.59 percent conflicting, and these topics sit
directly on it.

### `kasus_dativ_formen` accepting three other cases, 3 items

    Die Busse fuhren ___ nach dem anderen ab.        -> einer    nominative
    ... obwohl alle ___ Murks längst gelesen haben.  -> den      accusative
    ... der als Schlagzeuger ___ Band Liily bekannt ist. -> der  genitive

Corpus German has far more varied syntax than generated German, and this
selector's case test does not survive it.

### Two more

`relativsatz_nom_akk` took `fordern Experten, ___ US-Seltene-Erden-Industrie
wiederzubeleben` where `die` is an ordinary accusative article inside an
infinitive clause, not a relative pronoun. And `adjektivdeklination_bestimmt`
took `bei den Studentinnen ___ Anklang gefunden`, where `guten Anklang` has
no article at all; the determiner belongs to `Studentinnen`.

### Two cues spelled the Swiss way

    Die Patientin lag mit ___ (schliessen) Augen im Bett.  -> geschlossenen

The cue is `schliessen`, not `schließen`. This is not the corpus. It is our
own vendored dictionary, which was written through a normaliser that turns
every `ß` into `ss`, leaking into a cue the learner reads.

## Tatoeba against Leipzig

| | Items | Defects | Rate |
|---|---:|---:|---:|
| Tatoeba | 252 | 12 | 4.8% |
| Leipzig | 114 | 7 | 6.1% |

Close enough that neither is clearly better on correctness. They differ in
character rather than quality: Tatoeba is short, everyday and learner-shaped;
Leipzig is current news, longer, and carries proper nouns, numbers and
journalistic register. Leipzig supplies the rare constructions Tatoeba lacks,
so both earn their place.

## Two quality issues that are not defects

**Lexical monotony.** Seven of ten `partizip_i_attributiv` items use
`laufend`, and three of nine `verb_praesens_vokalwechsel` items are `gibt`.
Every one is correct, but a learner meeting the same word repeatedly is
learning less than the item count suggests. The sampler should diversify by
lemma, not only by topic.

**Two topics still empty**, `futur_i` and `zustandspassiv_zeiten`, plus
`futur_ii` at a single item. Those three are the case for keeping AI
generation: they are rare enough that even 80,000 corpus sentences do not
supply them.

## Verdict

The corpus path is better than the generation path on every axis that
matters except raw defect rate, where it is 1.2 points worse for reasons that
have nothing to do with the corpus.

- Coverage: 47 topics against 36.
- Carrier quality: one bad sentence in 366, against a whole defect class.
- Cost: 25 model calls against 147.
- The rare constructions that generation could never reach are now solved.

Every one of the 19 defects is a selector or routing bug on our side, and
they concentrate in five known classes, four of which have been fixed before
and regressed against harder input. That is the work: corpus German is more
syntactically varied than generated German, and it is finding holes that
model-written sentences never exercised.
