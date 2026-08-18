# Cycle 4 audit

Blanking pilot, 300 sentences requested.

## Numbers

| | Cycle 3 | Cycle 4 |
|---|---|---|
| Accepted items | 245 | **510** |
| Topics with items | 25 | **42 of 49** |
| Dropped to topic cap | n/a | 637 |
| Defect rate (sample of 93 hand audited) | about 10% | **about 20 to 25%** |

Volume and coverage are solved. **510 items is 2.5 times the 200 target**, and 637
more were dropped only by the balance cap, so the ceiling is higher still. The
construction hints worked: `relativsatz_nom_akk` went from 0 to 20 items, and
every passive, participle, Konjunktiv and Plusquamperfekt topic now produces
content.

The defect rate went UP, and the reason is a single architectural gap that this
cycle's volume increase exposed rather than caused.

## The finding: blanking proves the answer is CORRECT, not that it is UNIQUE

This is the flaw in my own design and it is now the whole problem.

Removing a token guarantees the removed token was right, because a competent
German sentence contained it. It guarantees nothing about whether some OTHER
token would also have been right. Where the blanked slot belongs to a closed
class with several grammatical members, the learner cannot reason to the answer.

    Das Fleisch ___ scharf angebraten werden, wenn ein kräftiger Geschmack gewünscht wird.
      answer: kann        also fine: muss, soll, sollte, darf

    Nach dem Frühstück putze ich gründlich meine ___.
      answer: Zähne       also fine: Hände, Schuhe, Haare

    Das Restaurant hatte einen neuen Koch eingestellt, und das Essen schmeckte ___ ausgezeichnet.
      answer: Ihnen       also fine: mir, ihm, ihr, uns, ihnen

Every one of these items is *correct*. None is *solvable*.

Affected systematically:

| Topic | Items in run | Problem |
|---|---:|---|
| `nomen_plural` | 20 | no cue, so any plural noun fits the slot |
| `modalverben_praesens` | 14 | any modal is grammatical |
| `passiv_modalverben` | 5 | same |
| `pronomen_personal_dat` | 20 | person is free unless the carrier establishes it |

Note the contrast that proves the point. Two `pronomen_personal_dat` items in
the same run ARE solvable, because the carrier fixes the person:

    Wir erklären ___ den Fehler, weil Sie das System besser verstehen müssen.  -> Ihnen
    Gegen Mittag hatte unsere Schulleiterin ___ den ersten Preis für meinen Aufsatz überreicht.  -> mir

`Sie` and `meinen` do the forcing. So the topic is fine; the item selection is
what needs a uniqueness test.

**The fix already exists and is not wired up.** Cycle 3 built `gloss_en` with
mechanical tense and person verification. An English gloss resolves exactly
these cases: "The meat CAN be seared", "I brush my TEETH", "the food tasted
excellent TO THEM". The gloss is currently applied only on the old
generate-with-target path.

Recommended: after blanking, test whether another member of the blanked token's
closed class also fits. If it does, either require a gloss for that item or
skip it. The closed classes are small and already enumerated in
`paradigms.py`.

## Second finding: `verben_reflexiv_dat` is mostly accusative

Three of four sampled items are accusative reflexives misfiled as dative:

    Wir würden ___ freuen, wenn Sie unsere Fragen beantworten.     sich freuen is ACCUSATIVE
    Wir treffen ___ jeden Samstag im Park.                          sich treffen is ACCUSATIVE
    ...weil ___ die gesetzlichen Bestimmungen geändert haben.       sich ändern is ACCUSATIVE

Only `Wir helfen ___ gegenseitig` is genuinely dative, since `helfen` governs
the dative.

Cause: `uns` and `sich` are syncretic between accusative and dative, so the
surface form cannot decide it. **The governing verb decides it.** Cycle 4 fixed
the prepositional-object case for this topic but not this one. A dative-reflexive
verb list is the fix, and it is a closed list.

## Third finding: carrier validation misses two error classes

The validator catches subject-verb agreement, which was its job and it works.
Two errors got through:

    Auf dem Boden lagen viele nassen Blätter, ...      should be "viele nasse Blätter"
    Sie haben Ihren Mitreisenden ein Souvenir geschenkt, dass Sie ... gekauft hatten.
                                                       should be "das", a relative pronoun

The first is adjective declension after `viele`, which takes strong endings.
That bad carrier produced two accepted items in different topics. The second is
the `das`/`dass` confusion, which is mechanically checkable: `dass` introduces
a clause, `das` refers back to a noun.

Also seen once: `heisse` for `heiße`. Swiss spelling, not standard German.

## What is genuinely excellent

All 20 `relativsatz_nom_akk` items I checked are correct, including case
distinctions:

    ... den Tennisschläger haben, ___ ich gestern in der Halle vergessen habe?   -> den (accusative)
    ... den schweren Basketball geben, ___ dort in der Ecke liegt?                -> der (nominative)

All seven `plusquamperfekt` items carry a `bevor` anchor, so the tense is forced
every time. All four `infinitiv_mit_zu` items are unambiguous because the
construction itself forces `zu`. `perfekt_haben`, `praeteritum_vollverben` and
`praeteritum_sein_haben_modal` were clean across the sample.

The construction-aware hints did what they were meant to do.

## Recommended next cycle

1. **Uniqueness test after blanking**, against the blanked token's closed class.
   This is the gate. It converts the ~40 unsolvable items into either
   glossed items or skips.
2. **Wire `gloss_en` into the blanking path**, so an item that survives the
   uniqueness test with a gloss is still usable rather than discarded.
3. Dative-reflexive verb list for `verben_reflexiv_dat`.
4. Extend carrier validation: adjective declension after quantifiers, and
   `das` versus `dass`.
5. A cue mechanism for `nomen_plural`, which needs the singular supplied the way
   the old path did with `(Apfel)`.

Items 1 and 2 are the same piece of work and address roughly 60 of the 510.
