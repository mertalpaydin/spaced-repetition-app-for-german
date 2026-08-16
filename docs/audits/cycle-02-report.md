# Cycle 2 report

Old path: 296 candidates, **117 accepted (40%)**, across **69 topics**.
New blanking path: ran, but persists nothing, so nothing could be audited.

## Trend

| | Cycle 0 | Cycle 1 | Cycle 2 |
|---|---|---|---|
| Accepted | 50 of 98 (51%) | 87 of 300 (29%) | **117 of 296 (40%)** |
| Topics covered | 12 | 12 | **69** |
| Defect rate | 34% | 17.2% | **11.1%**, 13 of 117 |

Defect rate has halved twice. It is the first time the pilot has covered most
of the taxonomy, so the 11.1% is a far more honest number than the previous
two.

Still short of both targets: 200 accepted, zero defective.

## The 13 defects

### Two-form answer sets still slipping through (3)

| # | Prompt | Accepted | Fault |
|---|---|---|---|
| 7 | `___ (sein) bitte ruhig!` | Sei, **Seid** | 2nd singular and 2nd plural, nothing chooses |
| 22 | `Gestern ___ wir nach Berlin gefahren.` | sind, **waren** | Perfekt and Plusquamperfekt |
| 117 | `Das Fenster ___ geöffnet.` | ist, **war** | present and past Zustandspassiv |

All three are the copula or an auxiliary. The target-form filter compares
facets, and for `sein`/`werden` forms the derived facet apparently does not
separate these. **Recommendation:** for auxiliary and copula answers,
compare `Tense`, `Mood` and `Person` explicitly rather than relying on the
facet string, which for these topics is often coarse.

### The determiner-type check does not fire where the topic omits `ArtType` (1)

Item 10, `kaufen wir ___ Apfel auf dem Markt`, accepts `einen`, `den`,
`diesen` **and `keinen`**. That spans indefinite, definite, demonstrative and
negative, which is exactly what cycle 2 added a check for.

It did not fire because `kasus_akkusativ_formen` declares no `ArtType` in its
`morph_spec`, so the check has nothing to key on. The check is gated on the
topic declaring the dimension rather than on the answers exhibiting it.

**Recommendation:** derive the determiner type from the answers themselves and
reject a set that spans more than one, regardless of what the topic declares.
Note the legitimate exception: item 11, `Ich helfe ___ Kind`, accepts eight
determiners spanning several types but all `Dat Neut Sing`, and for a
`kasus_*_formen` topic the tested feature is case, so that one is correct.
The rule needs to be "spans more than one type AND the topic is about the
determiner", not a blanket ban.

### Wrong topic (3)

| # | Topic | Prompt | Fault |
|---|---|---|---|
| 28 | `satzbau_frage_inversion` | Weil er morgen eine Prüfung hat, ___ er den ganzen Abend. | not a question |
| 29 | `satzbau_frage_inversion` | Obwohl sie keinen Hunger hat, ___ sie ein frisches Brötchen. | not a question |
| 91 | `pronominaladverbien_da_wo` | ...die neuen Richtlinien, ___ letztlich alle Mitglieder zustimmten. | answer `denen` is a relative pronoun, not a pronominal adverb |

`satzbau_frage_inversion` is generating declaratives, twice out of two. It is a
`structural` topic and cycle 4 covers it, but the fault is visible now.

### Semantic (2)

- Item 5: `Obwohl das Wetter heute schön ist, scheint dort draußen keine Sonne.`
  Nice weather and no sun is a contradiction.
- Item 73: `Je schneller ich laufe, desto weniger Zeit brauche ich nach Hause.`
  The carrier is incomplete German; `brauchen` needs an infinitive or a
  prepositional phrase here.

### Others (4)

- Item 104, `Die Antwort ___ diese Frage`, accepts `auf` and `für`. Only `auf`
  is the fixed preposition. This is `lexical_table` awaiting cycle 4.
- Item 27, `___ ist mein guter Freund`, accepts `Er`, `Das`, `Dieser`, `Der`
  and **`Wer`**. The last turns the sentence into a question.
- Item 108 uses **four** underscores, `____`, not three. It passed because
  `count("___")` on `____` returns 1. **Normalise runs of three or more
  underscores to exactly three before any check.**
- Items 112 and 113 are near-duplicates: `Ungeachtet ___ schlechten Wetters`
  twice with different tails.

## What is now reliably good

104 of 117 are clean and the strength is broad rather than concentrated. All
six `nomen_plural` items are correct including every umlaut plural. All four
`verb_praesens_vokalwechsel` items are correct. Every relative-clause item (92
to 96) is right, including `von dem / vom / über das` correctly treated as one
acceptable class. The connector topics that I had written off as unfixable
produced clean items here: item 79 accepts seven consequential connectors and
no adversative one, which is exactly the behaviour the semantic class needs.

The genitive, Konjunktiv, passive and Partizip I sets are all clean.

## Blocking gap: the new path produces nothing auditable

`scripts/step6_blank_pilot.py` prints a report and writes no file, so the
generate-tag-select-blank pipeline could not be audited this cycle at all. That
was my omission in the cycle 2 work order.

## Cycle 3 scope

1. **Turn the batch API off for pilots.** Owner's instruction: batch turnaround
   is too slow for a development loop. Route pilot generation through the sync
   lane, keep batch available behind a flag for real stock runs.
2. **Persist the blanking output** to JSONL in the same shape as
   `pilot_review.jsonl`, so both paths are auditable and directly comparable.
3. **Wire blanking into the verification chain, carefully.** Blanked items
   currently carry only `proposed_answer`. If they enter the chain and hit
   `expand_answers`, the computed accepted set is replaced by the old widening
   behaviour and the entire benefit is lost. The computed set must be carried
   on the item and treated as authoritative.
4. Determiner-type spanning derived from the answers, not the topic's declared
   `morph_spec`, with the `kasus_*_formen` exception above.
5. Explicit `Tense`/`Mood`/`Person` comparison for auxiliary and copula answers.
6. Normalise `____` to `___`.
7. Near-duplicate detection that catches shared carrier stems.
