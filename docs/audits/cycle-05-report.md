# Cycle 5 audit

Blanking pilot, 300 sentences requested.

## Numbers

| | Cycle 3 | Cycle 4 | Cycle 5 |
|---|---|---|---|
| Accepted items | 245 | 510 | **469** |
| Topics with items | 25 | 42 | **42** |
| Cued items | 0 | 0 | **31** |
| Defect rate (hand audited) | ~10% | ~20 to 25% | **~12%**, 6 of 51 |

The uniqueness gate removed 41 items and fired 66 skips: 30
`plural_noun_open_class`, 24 `personal_pronoun_unanchored`, 12
`modal_verb_interchangeable`. That is the trade it was built to make.

## Every cycle 4 defect class is closed

**Cues rescued the topics the gate correctly killed.** All 20 `nomen_plural`,
4 `modalverben_praesens` and 5 `passiv_modalverben` items now carry a citation
cue and are solvable:

    Nach dem Frühstück putze ich gründlich meine ___.   (Zahn)    -> Zähne
    Das Fleisch ___ scharf angebraten werden, ...       (können)  -> kann

**Pronoun anchoring works precisely.** All 9 surviving `pronomen_personal_dat`
items have a genuine carrier anchor, and the unanchored ones were skipped:

    Wir erklären ___ den Fehler, weil Sie das System besser verstehen müssen.  -> Ihnen
    Letzte Woche haben Sie mir sehr geholfen, aber heute helfe ich ___ gerne.  -> Ihnen
    ... hatte unsere Schulleiterin ___ den ersten Preis für meinen Aufsatz überreicht.  -> mir

The last one is anchored by `meinen` and the second by a contrastive `Sie`.
Compare the cycle 4 defect that is now gone: `das Essen schmeckte ___
ausgezeichnet` had no anchor and is skipped.

**Reflexive case routing is fixed.** `sich freuen`, `sich treffen` and
`sich wenden` now correctly land in `verben_reflexiv_akk`; `sich etwas
wünschen` correctly lands in `verben_reflexiv_dat`.

**Carrier errors are gone.** Zero matches for the `viele nassen`, `heisse` and
`dass`/`das` patterns that produced defects last cycle.

## The one remaining defect class: open-class lexical verbs need a cue

Six defects in a 51 item sample, and five of them are one thing.

    Unser Chef ___ ein großes Sommerfest, das im nächsten Monat stattfindet.
      answer: plant       also fine: organisiert, veranstaltet, feiert

    Als wir nach Hause ___, hatte der Regen schon längst aufgehört.
      answer: gingen      also fine: kamen, fuhren, liefen

    Die Getränke waren kalt gestellt, aber wir ___ noch die passenden Gläser.
      answer: vermissten  also fine: brauchten, suchten, holten

This is exactly the `nomen_plural` problem, one part of speech over. The
uniqueness gate covers plural nouns and modals because those are enumerated
closed classes. **A full lexical verb is an open class, which is strictly
worse, and the gate does not cover it at all.**

The contrast in the same run proves the diagnosis rather than merely
suggesting it:

| Topic | Items | Clean? | Why |
|---|---:|---|---|
| `verb_sein_haben` | 20 | yes | `sein`/`haben` are closed and the construction forces which |
| `praeteritum_sein_haben_modal` | 20 | yes | same |
| `perfekt_haben` | 20 | yes | auxiliary forced by the participle |
| `verb_praesens_regelm` | 20 | **no** | any verb fits |
| `praeteritum_vollverben` | 20 | **no** | any verb fits |

And one open-lexeme item IS clean, which shows the rule is about forcing and
not about the topic:

    Nach dem Frühstück ___ ich gründlich meine Zähne.   -> putze

`Zähne putzen` is a fixed collocation, so the object forces the verb.

**Exposure: 65 of 469 items** across `verb_praesens_regelm` (20),
`praeteritum_vollverben` (20), `verb_praesens_vokalwechsel` (12) and
`verben_trennbar_praesens` (13).

**Fix: cue every lexical verb slot with its infinitive**, the same mechanism
already working for plural nouns and modals. `___ (gehen)` is what the older
generate-with-target path printed and what every textbook prints. The cue
machinery, the answer-leak guard and the `cloze_cued` typing all exist; this is
extending an existing mechanism to one more part of speech, not new
architecture.

## The two other defects

- `adjektivdeklination_nullartikel` fired on `das von einem Maler ___
  Arbeitszimmer` with answer `gestaltete`. There IS an article (`das`), so this
  is weak declension and belongs to `adjektivdeklination_bestimmt`. The
  intervening `von einem Maler` phrase defeated the determiner lookup. A
  previous agent built a bounded backward scan for
  `partizip_ii_attributiv_erweitert` and noted the trap; the nullartikel
  selector needs the same scan.
- `Ihr gebt aber nicht auf und helft ___ gegenseitig` landed in
  `verben_reflexiv_akk` with `euch`. `helfen` governs the dative and is on the
  dative-reflexive list, so this should have routed to
  `verben_reflexiv_dat`. The clause has two finite verbs joined by `und` with
  no comma, which is the case the selector is meant to skip rather than guess.
  It guessed.

## Assessment

The architecture is sound and the remaining work is bounded. Three cycles ago
the defects were scattered across semantics, morphology, topic assignment and
carrier grammar. Now there is one systematic class with a known fix and two
one-off selector bugs.

If the lexical-verb cue lands, the projected defect rate is roughly 1 in 51,
which is about 2 percent, on roughly 400 to 470 items. That is the first time
zero is a realistic target rather than an aspiration.
