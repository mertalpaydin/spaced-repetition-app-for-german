# Cycle 6 audit

Blanking pilot, 300 sentences. **419 accepted, 38 topics, 135 cued.**

## Structural invariants: all clean

| Check | Result |
|---|---|
| `eligible_types` violations | **0** (was 72) |
| Cue equal to the answer | **0** |
| Answer appearing in the prompt | **0** |
| Modal leaking into a lexical verb topic | **0** (was 3) |

## Defect rate

333 of 419 items hand audited in full. **28 defects, 8.4%.**

| | C3 | C4 | C5 | **C6** |
|---|---|---|---|---|
| Accepted | 245 | 510 | 469 | **419** |
| Defects | ~10% | ~22% | ~12% | **8.4%** |

The lexical verb cue worked. All 20 `verb_praesens_regelm` items and 17 of 20
`praeteritum_vollverben` are correct, cued, and solvable. `perfekt_haben` (20),
`kasus_akkusativ_formen` (20), `adjektivdeklination_unbestimmt` (20),
`plusquamperfekt` (6), all four passive topics and both participle topics
audited **completely clean**.

Every remaining defect falls into seven classes, all mechanically fixable.

## A. Mislemmatised cues, visible to the learner (4 items)

    ...dass wir die Reise stornieren ___.   -> mussten   cue: (mussen)
    Obwohl der Hunger groß war, ___ ...     -> musste    cue: (mussen)
    Morgen früh ___ du deine Schultasche... -> packst    cue: (einpacksen)
    ...beim Einkaufen auf ___ zu verzichten -> Plastiktüten  cue: (Plastiktüt)

`mussen`, `einpacksen` and `Plastiktüt` are not German words. This is the worst
class because the learner sees the nonsense directly.

**Fix: validate every cue against a real German dictionary.** We vendored one
last cycle for the vocabulary work: `data/fixtures/corpus/frequency/
de_dictionary_filter.txt`, 37,567 entries, CC0. A cue that is not a real word
is rejected and the item skipped. This closes the class completely rather than
extending a hand-maintained exclusion list one lemma at a time, which is what
previous cycles have been doing.

## B. Swiss orthography in carriers (4 items)

    In der grossen Pause hatte unser Klassenlehrer ...
    Vor der grossen Prüfung hatte mein bester Freund ...

Standard German is `großen`. The existing eszett check is a closed list for
`heißen` only.

**Fix: the same dictionary.** `grossen` is not in it; `großen` is. One check
closes A and B together.

## C. `dass` where `damit`, `wenn` or `weil` belongs (6 items)

    Sie laden das Betriebssystem herunter, dass Ihr Computer wieder sicher ist.   -> damit
    Wir hatten der netten Dame geholfen, dass sie ihren Koffer schnell fand.      -> damit
    Sie kaufen ein neues Smartphone, dass Ihre alte Technik zu langsam ist.       -> weil
    Es wäre sehr hilfreich, dass Sie dem Reiseleiter Ihre Wünsche mitteilen.      -> wenn

The carrier validator's `das`/`dass` check only covers the relative-pronoun
direction against a two-verb list. These are a different error: a purpose or
causal clause written with `dass`.

**Fix: two layers.** Add an explicit instruction to the German generation
prompt about when `dass` is and is not correct, and extend the carrier check
with a purpose-frame matrix-verb list. Note the check must not overreach:
`Sie haben der Reiseleitung eine Nachricht geschickt, dass der Bus pünktlich
angekommen ist` is a content clause and correct.

## D. `verb_praesens_vokalwechsel` items that show no vowel change (7 items)

    Mittags ___ ich meistens ein leckeres Sandwich.   -> esse
    Morgen ___ ich meinem besten Freund beim Umzug.   -> helfe
    ...aber heute ___ ich Ihnen einen Tipp.           -> gebe

The vowel change in German appears only in the 2nd and 3rd person singular:
`du isst`, `er isst`. First person singular is `ich esse`, identical to a
regular verb. Seven of eight items in this topic blank a first-person form, so
they exercise nothing the topic is about. Only `hilft` genuinely tests it.

**Fix: require the blanked form to actually differ from the regular stem.**
Mechanical, and it turns this topic from mostly noise into a real drill.

## E. `adjektivdeklination_nullartikel` firing with an article present (3 items)

    Gemeinsam erreichten sie die Firma, wo schon die erste ___ Besprechung wartete.  -> wichtige
    Ich wünsche mir wirklich eine viel ___ Zukunft für unsere Stadt.        -> umweltfreundlichere
    ... hatten wir das von einem Maler ___ Arbeitszimmer besichtigt         -> gestaltete

**The third is the exact sentence cycle 6 was told to fix and reported as
fixed.** The bounded backward scan handles an intervening prepositional phrase
but not an intervening adjective (`die erste ___`) or quantifier (`eine viel
___`), and it evidently still misses the PP case in the live pool.

**Fix: extend the scan past preceding adjectives and quantifiers, and add
these three sentences as regression tests.** I will verify against the live
output next time rather than accepting a report that it works.

## F. `konjunktiv_ii_irreal_gegenwart` items that are politeness (2 items)

    Wir ___ uns freuen, wenn Sie unsere Fragen beantworten.   -> würden
    Ich ___ mich sehr freuen, wenn Sie mir dabei helfen könnten. -> würde

Both `wenn` clauses are indicative, so these are polite requests, not
counterfactuals. They belong to `konjunktiv_ii_hoeflichkeit`.

**Fix: require the subordinate clause verb to be Konjunktiv II as well.**

## G. A carrier typo (1 item)

    Nach der Arbeit treue ich mich mit Lisa auf einen Kaffee in der Stadt.

`treue` should be `treffe`. Worth noting that carrier validation's
subject-verb agreement check should have caught this, since no finite verb
agrees with `ich`. That it did not is worth investigating rather than papering
over with the dictionary check, because it suggests the agreement check has a
hole.

## Projection

Classes A and B are one dictionary check, worth 8 items. C, D, E, F and G are
five bounded fixes worth 19 more. Closing all seven takes the audited defect
count from 28 to approximately 1, on roughly 400 items.

Volume is not at risk: 479 items were dropped to the topic cap in this run, so
the pool has ample headroom to absorb stricter gating.

## Also carried into the next cycle

The three `artikel_*_nom` topics remain at zero, with the forcing mechanisms
already decided: a consequence clause for `kein`, a uniqueness anchor for the
definite article, and kinship nouns with an explicit first or second person
subject for the possessive. That plus the seven classes above is the next
cycle.
