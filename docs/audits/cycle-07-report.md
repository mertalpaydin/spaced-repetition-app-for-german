# Cycle 7 audit

Blanking pilot, 300 sentences. **370 accepted, 36 topics, 120 cued.**
Every one of the 370 items was audited by hand. No sampling this time.

## Headline

Two findings, and the second is bigger than anything in the previous six
reports.

1. **The model verification backstop did not run.** Zero of 370 items were
   verified. The pass degraded silently and the run reported success.
2. **167 of the 370 items cannot be solved by typing.** They are correct
   German and they are in the right topic. They have more than one right
   answer, and the app grades by string comparison against exactly one.

Hand-audited grammar defects, the number previous cycles have been tracking,
are down to **14 of 370, 3.8%**. That number is real but it is no longer the
binding constraint.

## 1. The verification pass never executed

Evidence, independent of the console output: `.cache/cost_log.jsonl` contains
**zero rows with `purpose="item_verification"`**. The wrapper logs every call
including cache hits, so zero rows means zero calls. The run made exactly 50
free-lane `sentence_generation` calls, which is 300 sentences at the batch
size of 6, and then stopped calling.

The mechanism is in the code and is deterministic, not a fluke:

- Sentence generation runs first and consumes the free lane.
- `verify_items` runs last, after every item exists.
- By then `free_lane_open` is `False`, so `_determine_lane` returns `"paid"`.
- The pilot sets `forbid_paid_lane=True`, correctly, per the no-batch rule.
- `PaidLaneForbiddenError` is in `_DEGRADE_EXCEPTIONS`, so all 370 items
  become `not_run` and the script exits 0.

So the backstop is architecturally unable to run in the same process that
generates the sentences, on the free lane, in one pass. It will fail this way
on every run. The report prints an honest `not_run` count, which is what it
was built to do, but the run still exits successfully, which is what lets it
pass unnoticed.

This means the `treue` class of defect, a non-word that spaCy tags as a
perfectly agreeing finite verb, is still uncaught by anything.

## 2. Correct is not the same as answerable

`web/index.html` shows a text input by default. `web/app.js` reveals the
distractors only at hint level 2, and `HintPolicy.is_unhinted_pass` treats any
hinted pass as not a real pass. `TypoGrader.grade` compares the typed string
against `accepted_answers`, which for a blanked item is the single token that
was removed.

So the learner types, and one string is right.

Under that rule, three families of item have more than one right answer.

### Determiner slots, 100 items

    Nach ___ Arbeit treffe ich oft meine Nachbarin im Park.     -> der
      also correct: meiner, dieser, jeder, unserer

    Abends sehe ich oft einen schönen Film auf ___ Sofa an.     -> dem
      also correct: meinem, diesem, einem

The case is forced. The lexeme is not. Affected: `kasus_akkusativ_formen`
(20), `praepositionen_dativ` (20), `dativ_nach_praeposition` (20),
`akkusativ_nach_praeposition` (14), `kasus_dativ_formen` (9),
`artikel_bestimmt_nom` (8), `praepositionen_akkusativ` (6),
`kasus_genitiv_formen` (2), `praepositionen_genitiv` (1).

`artikel_unbestimmt_kein_nom` is NOT affected: the causal clause forces
negation, so `kein` is the only fit. That is the forcing anchor built last
cycle, and it works.

### Adjective slots, 58 items

    Mein ___ Freund Timo hat mir gestern ein sehr gutes Buch geschenkt.
      -> bester        also correct: guter, alter, netter, langjähriger

    Auf dem Tisch steht eine ___ Tasse Tee, die ich mir gekocht habe.
      -> warme         also correct: große, volle, heiße, frische

The ending is forced by the determiner. The adjective itself is open class.
Affected: all three `adjektivdeklination_*` topics, 20 items each.

The two participle topics are NOT affected, because they are already cued
with the infinitive. That is the proof the mechanism works: same slot shape,
cue present, item solvable.

### Nominative pronoun syncretism, 9 items

    ___ haben im letzten Jahr vielen Touristen geholfen, weil die Gäste
    den Weg nicht gefunden haben.                              -> Sie
      also correct: Wir, Sie (3rd plural)

`wir`, `sie` and `Sie` share one verb form in every tense. Seven items blank
into that syncretism with no discourse anchor. Two more (`arbeitete ___`,
`rief ___`) sit in the 1st/3rd singular preterite syncretism.

The uniqueness gate covers five classes: plural nouns, modals, personal
pronouns in the oblique cases, lexical verbs, auxiliary tense. It fired 139
times this run and every skip was correct. It does not cover determiners,
attributive adjectives, or nominative pronouns, and those are 45 percent of
the accepted output.

**This is my error, not the pipeline's.** Cycles 4 through 6 checked whether
an item was *correct*. For determiner and adjective slots I never applied the
solvability test that cycle 4 itself defined. That is why the defect rate kept
falling while the product did not get better.

## 3. The 14 grammar defects

| # | Topic | Item | Fault |
|---|---|---|---|
| 1 | `nomen_plural` | `die neuen ___` cue `(Tablett)` -> `Tabletten` | cue is the wrong lexeme. `Tablett` pluralises to `Tabletts`; `Tabletten` is the plural of `Tablette`. The dictionary check passes it because `Tablett` is a real word |
| 2 | `adjektivdeklination_nullartikel` | `Gegen ___ Unwohlsein` -> `euer` | a possessive determiner, not an adjective. Distractors `eue`, `euem`, `euen` are not German words |
| 3 | `adjektivdeklination_nullartikel` | `mit dem vor wenigen Wochen ___ Ball` -> `gekauften` | `dem` is present, so this is weak declension. The backward scan is still defeated by an intervening adverbial. Third cycle for this one |
| 4 | `artikel_bestimmt_nom` | `hat ___ schwerste Kiste getragen` -> `die` | accusative object under a nominative topic |
| 5 | `zustandspassiv` | `Es ___ sehr wichtig, dass...` -> `ist` | copula plus adjective, no passive at all |
| 6 | `zustandspassiv` | `...weil der Hausmeister ihn gestern schnell heilgemacht hat` | `heilgemacht` is not standard German |
| 7 | `zustandspassiv` | `dass ein wichtiges Ereignis angekündigt worden ___` -> `ist` | Vorgangspassiv in the Perfekt, not Zustandspassiv |
| 8 | `verben_reflexiv_akk` | `einen Kaffee, den er ___ frisch gekocht hatte` -> `sich` | benefactive dative, wrong topic |
| 9 | `verben_reflexiv_dat` | `dem ___ oft helfenden Trainer` -> `mir` | not reflexive at all; `mir` is the dative object of the participle |
| 10 | `pronomen_personal_dat` | `Mein bester Freund Timo hat ___ gestern ein Buch geschenkt` -> `mir` | unanchored. `dir`, `ihm`, `uns`, `Ihnen` all fit |
| 11 | `pronomen_personal_dat` | `Mein Kollege hat ___ heute einen Apfelkuchen mitgebracht` -> `mir` | same |
| 12 | `futur_i` | `Obwohl die Bearbeitungszeit kurz ist, ___ Sie den Termin einhalten` -> `werden` | `können`, `müssen`, `wollen` all fit. The modal gate does not fire because the answer is `werden` |
| 13 | `verb_praesens_regelm` | `...dass Stefan bald den Tennisschlüssel findet` | `Tennisschlüssel` is not a thing. Should be `Tennisschläger` |
| 14 | `relativsatz_nom_akk` | same carrier | one bad sentence, two items |

Defects 10 and 11 share a root cause worth naming: the possessive-person
anchor fires on a possessive anywhere in the sentence. `Mein` in the SUBJECT
noun phrase does not force the person of the DATIVE OBJECT.

## 4. What is clean

These topics were audited item by item with nothing found:

| Topic | Items |
|---|---:|
| `relativsatz_nom_akk` | 20 (one shared carrier defect aside) |
| `praeteritum_vollverben` | 20 |
| `praeteritum_sein_haben_modal` | 20 |
| `adjektivdeklination_bestimmt` | 20 endings, all correct |
| `perfekt_haben` | 16 |
| `verb_sein_haben` | 14 |
| `verben_trennbar_praesens` | 12 |
| `infinitiv_mit_zu` | 6 |
| `praepositionen_akkusativ` | 6 |
| `partizip_i_attributiv` | 4 |
| `verb_praesens_vokalwechsel` | 4 |
| `artikel_unbestimmt_kein_nom` | 3 |
| `modalverben_praesens`, `passiv_modalverben`, `partizip_ii_attributiv_erweitert` | 2 each |

`verb_praesens_vokalwechsel` deserves a specific note: all four items now show
a real vowel change (`hilft`, `läuft`, `erhält`, `wächst`). Cycle 6 had seven
of eight items blanking a first-person form that changes nothing. That fix
worked completely.

The three revived article topics produce items and the forcing anchors hold.
The cue mechanism is correct on all 120 cued items except the one `Tablett`.

## 5. Trend

| | C3 | C4 | C5 | C6 | **C7** |
|---|---|---|---|---|---|
| Accepted | 245 | 510 | 469 | 419 | **370** |
| Topics | 25 | 42 | 42 | 38 | **36** |
| Audited | sample | sample | sample | 333 of 419 | **370 of 370** |
| Grammar defects | ~10% | ~22% | ~12% | 8.4% | **3.8%** |
| Unsolvable by typing | not measured | not measured | not measured | not measured | **45%** |

## 6. What would close it

**A. Cue the determiner and adjective slots**, exactly as nouns, verbs,
modals and participles are already cued. This is textbook practice and it is
the same code path:

    Nach ___ (die) Arbeit treffe ich oft meine Nachbarin.       -> der
    Mein ___ (gut) Freund Timo hat mir ein Buch geschenkt.      -> guter

The cue supplies the lemma; the learner supplies the inflection, which is the
only thing the topic tests. Closes 158 of the 167.

**B. Extend the uniqueness gate to nominative pronouns** with the same
anchor logic already used for the oblique cases. Closes the remaining 9.

**C. Six bounded fixes** for the 14 grammar defects: cue lexeme round-trip
(`Tablett` -> `Tabletts` != `Tabletten` rejects it), restrict the nullartikel
selector to slots with no determiner anywhere in the noun phrase, require a
nominative slot for `artikel_*_nom`, require a genuine participle for
`zustandspassiv`, scope the possessive anchor to the object noun phrase, and
add `werden` to the interchangeable-auxiliary check.

**D. Make the verification pass able to run.** Two options that respect the
no-batch rule: reserve free-lane quota by budgeting the sentence pool against
the verification call count, or split the pilot into generate and verify as
two commands so verification starts on a fresh daily quota. Also: exit
non-zero when the pass reports `not_run`, so a silent skip cannot be mistaken
for a pass again.
