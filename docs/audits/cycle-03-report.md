# Cycle 3 audit and cycle 4 work

## Results

| | Old path (generate-with-target) | New path (generate-then-blank) |
|---|---|---|
| Accepted | 113 of 297, 38% | **245 items** |
| Topics covered | 69 | 25 of 49 |
| Source sentences | n/a | **about 60** |

The blanking path cleared the 200-item bar on its first real run. But two
structural faults matter more than the count.

## Fault 1: nothing checked that the generated sentence was correct German

The architecture's claim was that blanking makes hallucinated answers
impossible, because the answer is the token we removed rather than a model
assertion. **That is true of the ANSWER and false of the CARRIER.**

The model generated:

    Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch ein.

`kauft ich` is wrong; with `ich` it is `kaufe`. That one bad sentence produced
two accepted items, one of which blanks the very verb that is wrong. A learner
would have been taught the error.

This was my blind spot in the design, not an implementation slip.

## Fault 2: the pool was tiny, uniform, and first-person present

About 60 sentences, evidently one daily-routine narrative, produced all 245
items. The consequences were severe and predictable in hindsight:

- `pronomen_personal_nom` got **44** items and `verb_praesens_regelm` **38**,
  because `ich` and a present-tense verb occur in nearly every sentence of such
  a text.
- `kasus_genitiv_formen`, `modalverben_praesens` and
  `adjektiv_komparativ_superlativ` got **1 each**.
- Every past-tense, passive, Konjunktiv and participle topic was structurally
  starved: a present-tense pool cannot exercise them at all.

**8 prompt-and-answer pairs were accepted under two topics at once**, for
example `Ich ___ jeden Morgen um sieben Uhr auf.` with answer `stehe`, filed
under both `verb_praesens_regelm` and `verben_trennbar_praesens`. Since FSRS
schedules per topic, one item under two topics updates two topics' state from a
single piece of evidence and corrupts both.

## Selector defects found by hand

| Topic | Item | Fault |
|---|---|---|
| `nomen_plural` | `...meistens eine ___ Kaffee...` = `Tasse` | singular noun under a pluralisation topic |
| `praeteritum_vollverben` | `Im Büro angekommen ___ ich...` = `schalte` | present tense under a preterite topic |
| `verben_trennbar_praesens` | `Um zehn Uhr müde ___ ich ins Schlafzimmer und schlafe schnell ein.` = `gehe` | the particle `ein` belongs to `einschlafen` in the next clause |
| `verben_reflexiv_dat` | `Ich lade meine Freunde zu ___ nach Hause ein.` = `mir` | `zu mir` is a prepositional phrase, not a reflexive argument |
| `verb_praesens_regelm` | `Um acht Uhr ___ ich das Haus...` = `verlasse` | `verlassen` is strong, so it belongs to the vowel-change topic |

Roughly 9 defects in a 90-item sample, about 10 percent.

## Cycle 4: what was done

**Carrier validation, new module.** Subject-verb agreement read off the
dependency arc rather than word order, so it holds in verb-final clauses;
exactly one finite verb per clause; sentence completeness. Sentences failing
validation never reach a selector. Every rejection is counted by reason so the
pool's losses are visible.

Building it surfaced four German syncretisms that had to be handled to avoid
rejecting correct sentences: 1st and 3rd plural in all tenses, 1st and 3rd
singular in preterite and Konjunktiv II, 2nd plural and 3rd singular in the
present, and 2nd and 3rd singular for sibilant stems. Bare informal imperatives
are discarded rather than judged, because the tagger mistags them.

**Pool widened** from 60 to a default of 300, varied across 16 themes and
across person, tense, register and structure. Measured distribution moved from
roughly 90 percent first-person present to person `{er/sie/es 71, ich 32, wir
26, ihr 19, du 16, sie/Sie 11}` and tense `{present 113, Perfekt 20, Konjunktiv
II 17, Plusquamperfekt 15, Futur I 7, Präteritum 5}`.

**All five selector defects fixed**, each pinned with its real sentence. Two
findings worth recording:

- The tagger tags `Tasse` as `Number=Plur` in that sentence even though `eine`
  is correctly `Number=Sing`. The fix reads the governing determiner instead of
  trusting the noun's own morphology.
- `de_core_news_sm` mislemmatises `schalte` to a different verb, `schalen`, and
  mistags it `Tense=Past`. Reconstructing a weak preterite from `schalen`
  regenerates `schalte` exactly, so the existing reconstruction cross-check
  cannot catch it: it is self-consistent nonsense. Handled as a named exclusion
  for that one lemma.
- `verlassen` is now classed correctly by a general rule: an inseparably
  prefixed verb inherits its base verb's vowel-change class.

**Cross-topic dedup** on (prompt, answer), resolved by a curated specificity
table derived from which selector's conditions are a strict superset of
another's. `verben_trennbar_praesens` beats `verb_praesens_regelm`; `futur_ii`
beats `futur_i`; the participle topics beat the adjective-declension ones.

**Caps**: 20 items per topic, 4 per source sentence, both reported separately
from quality skips and labelled as balance rather than quality.

## What cycle 4 did NOT fix, and what it means for cycle 5

**16 of 49 topics still receive zero items.** The agent verified this by
re-running with caps disabled, so it is a pool coverage gap and not an artifact
of capping. The starved set is specific: all three `relativsatz_*`, every
passive and `zustandspassiv` topic, both `infinitiv_*`, both `partizip_*`,
`kasus_genitiv_formen`, `praepositionen_genitiv_gehoben`,
`konjunktiv_ii_hoeflichkeit`, `futur_ii`.

These are exactly the constructions a general-purpose sentence pool rarely
contains. Widening the pool further will not reliably fix it, because the
problem is not volume but the absence of the target construction.

**Cycle 5 should therefore make generation construction-aware**: prompt for
sentences that contain the needed structure, described as meaning rather than
grammar ("write a sentence that describes a thing by what someone did to it"
yields a relative clause; "describe something that has already been completed
before another past event" yields Plusquamperfekt). That is the same
prompt-the-relation-not-the-word technique already planned for the semantic
topics, applied to structure.

Three cycles remain after this one.
