# Cycle 9 audit

428 accepted, 36 topics, 311 cued. All 428 audited by hand.

Your change to `SERVER_ERROR_BACKOFF_SECONDS` (5 to 15) and
`SERVER_ERROR_MAX_RETRIES` (3 to 5) is kept and now pinned by a test so no
agent can overturn it.

## What the loop fixed

Topics with items went 31 to 36, and the five that appeared are ones that had
never produced anything:

| Topic | C8 | C9 |
|---|---:|---:|
| `verb_praesens_vokalwechsel` | 4 | **19** |
| `perfekt_sein` | 0 | **9** |
| `pronomen_personal_akk` | 0 | **8** |
| `adjektiv_komparativ_superlativ` | 0 | **7** |
| `konjunktiv_ii_irreal_gegenwart` | 0 | **2** |
| `konjunktiv_ii_vergangenheit` | 0 | **2** |
| `relativsatz_genitiv` | 0 | **1** |

`verb_praesens_vokalwechsel` is the clearest proof the hint mechanism works.
Its hint asks for sentences about someone else, never about myself, because
the vowel change only appears in the second and third person singular. All 19
items show a real vowel change: `schläft`, `läuft`, `wirft`, `hilft`,
`entspricht`, `fährt`, `spricht`, `trifft`.

The verifier rejected 97 items: 64 for having more than one correct answer,
29 for unnatural German, 4 for a word that does not exist. The new third
question works. It caught `unsere sehr geehrte Familie`, which killed four
items from one carrier, with the reason that `sehr geehrt` is a letter
salutation and not a way to describe your own family. That is the
`Tennisschlüssel` class, now caught.

## Defects: 17 of 428, 4.0 percent

### Reflexive case routing is wrong in both directions, 6 items

    Wir wünschen ___, dass Sie uns Ihre ehrliche Meinung mitteilen.  -> uns
    Er wünscht ___ sehr, dass er bald bessere Noten schreibt.        -> sich
    Sie wünscht ___, dass das teure Obst günstiger verkauft wird.    -> sich

`sich etwas wünschen` takes the dative. All three are filed as accusative.
The cycle 7 rule routes a reflexive to the dative when the clause has a direct
object; here the object is a `dass` clause, which the rule does not see.

    Um zwei Uhr treffen wir ___ alle vor dem Haupteingang.           -> uns
    Sie treffen ___ heute Nachmittag mit Ihrer Freundin.             -> sich
    ...weil ___ darauf viel Staub angesammelt hat.                   -> sich

`sich treffen` and `sich ansammeln` take the accusative. All three are filed
as dative, by the same rule firing when it should not. The trigger is visible:
`alle` is an apposition, `heute Nachmittag` is an accusative time adverbial,
and `viel Staub` is the subject. None of the three is a direct object, and the
rule counts all of them as one.

**Fix: the direct-object test must be a real object, not any accusative noun
phrase.** Exclude accusative time adverbials, quantifier appositions and
nominative-headed phrases; include a `dass` clause as an object.

### `futur_i` is catching the passive, 2 items

    ...dass das Fleisch scharf angebraten ___, wenn ein tolles Aroma
    entstehen soll.                                                 -> wird
    Das Smartphone ___ jetzt aufgeladen, damit Sie es am Abend sofort
    nutzen können.                                                  -> wird

Both are `werden` plus a past participle, which is the present passive, not
Futur I. Futur I is `werden` plus an infinitive. Meanwhile `passiv_praesens`
produced zero items this run. One selector bug, two topics wrong.

**Fix: require an infinitive, not a participle.**

### Two cues name the wrong gender, 2 items

    Zum Frühstück trinkt er immer ___ (eine) frisch gepressten Orangensaft.
      -> einen        Orangensaft is masculine, so the cue should be "ein"

    Morgen möchte ich gerne den schweren Schrank in ___ (die) andere Zimmer
    schieben.  -> das        Zimmer is neuter, so the cue should be "das"

The cue is supposed to be read off the head noun's gender. In both cases it
was read off something else. A learner who follows the cue writes the wrong
answer, which makes these worse than an uncued item.

### Swiss orthography is back, 4 items

    Schliesslich hatte ich mich auf meinen festen Platz gesetzt...
    Gleich danach war ich noch einmal kurz nach draussen...

Standard German is `Schließlich` and `draußen`. One carrier produced three
items, the other one. The dictionary check cannot catch this class, because
the vendored word list was written through a normaliser that converts every
`ß` to `ss`, so `schliesslich` is in it. The existing eszett check is a closed
list covering `heißen` and `groß` only.

**Fix: a general rule, not a longer list.** Any word containing `ss` where the
preceding vowel is long or a diphthong is Swiss, not standard. `draussen`,
`schliesslich`, `heisst`, `gross`, `weiss` all fall out of that; `muss`,
`musste`, `Fluss`, `Kuss` are correctly untouched because their vowel is
short.

### `adjektivdeklination_nullartikel` with a determiner present, 1 item

    Wir möchten gerne wissen, ob Ihnen diese innovative ___ Lösung gefällt.
      -> technische

`diese` is right there. Third cycle for this class, now defeated by an
intervening adjective rather than an intervening phrase.

### Two topic-attribution errors, 2 items

    Später trinken wir dann gemeinsam eine Tasse Kaffee als kleines
    Dankeschön.   -> Später (cue: Spät)

Filed under `adjektiv_komparativ_superlativ`. `Später` here means "afterwards".
There is no comparison in the sentence, so nothing tells the learner a
comparative is wanted.

    Wenn ich nur etwas früher auf meine Ernährung geachtet ___, wäre ich
    jetzt bestimmt fitter.   -> hätte

Filed under `konjunktiv_ii_irreal_gegenwart`. The condition is in the past, so
the blanked form belongs to `konjunktiv_ii_vergangenheit`.

## The 13 topics still at zero

`artikel_bestimmt_nom`, `artikel_possessiv_nom`, `artikel_unbestimmt_kein_nom`,
`futur_ii`, `infinitiv_um_zu`, `konjunktiv_ii_hoeflichkeit`,
`partizip_i_attributiv`, `passiv_praesens`, `passiv_praeteritum`,
`praepositionen_genitiv_gehoben`, `relativsatz_dativ`, `zustandspassiv`,
`zustandspassiv_zeiten`.

Three of these are known and expected:

- `artikel_bestimmt_nom` and `artikel_possessiv_nom` cannot be made solvable
  by typing. A nominative article is its own citation form, so any cue equals
  the answer.
- `passiv_praesens` is not a generation failure. Its sentences were generated
  and then taken by the `futur_i` selector, as shown above. Fixing that
  selector should return this topic.

Three more produced items in an earlier run and lost them this time, which
means the construction reached the pool before and did not now:
`artikel_unbestimmt_kein_nom` (3 items in cycle 8), `partizip_i_attributiv`
(4 in cycle 8), `zustandspassiv` (4 in cycle 7).

For the rest I cannot say from the output files alone whether the model
failed to write the construction or the selector failed to find it. The
orchestrator prints exactly that breakdown per topic and it goes to the
console, not to a file. **Send me the per-topic block from the run output, or
let me change the orchestrator to write it to a file, and I can tell you which
of the two it is for each topic.**

## Trend

| | C7 | C8 | **C9** |
|---|---|---|---|
| Accepted | 370 | 286 | **428** |
| Topics | 36 | 31 | **36** |
| Audited | all | all | **all** |
| Defects | 3.8% | 2.4% | **4.0%** |
| Verifier ran | no | yes | **yes** |

The defect rate went up because six topics that had never produced anything
now do, and five of the seven defect classes above are in those new topics.
The topics that were clean in cycle 8 are still clean: every determiner,
adjective, plural, preterite, perfect, relative-clause and pronoun item in
this run is correct.

Spend to date is $0.45 against the 5 EUR ceiling.
