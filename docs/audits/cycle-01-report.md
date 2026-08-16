# Cycle 1 report

Batch `batch_72e5da7ab41d`. 300 candidates, 87 accepted, 213 rejected.

## Against the exit criteria

| Criterion | Target | Actual | |
|---|---|---|---|
| Accepted | 180 | **87** | missed |
| Defect rate | 8% or under | **17.2%**, 15 of 87 | missed |
| Facet `Unk` rate | reported | **0%**, from 67.9% | met |

Defect rate did improve, from 34% to 17.2%. Acceptance went the wrong way,
and 60 of the 213 rejections are two bugs I can name precisely.

## Two regressions, 60 rejections, both free to recover

### 1. The cue-consistency check fires on an empty cue (36 rejections)

```
Proposed answer 'das' is not a form of the item's own cue '': the item is self-contradictory.
```

The cue is the empty string, not `None`, so the truthiness guard does not
catch it and every uncued item is rejected for failing to match a cue it does
not have. This is a regression introduced by my own cycle 1 work, commit
`057cef0`. Items lost to it were fine:

- `Obwohl der Winter sehr kalt ist, bleibt ___ Fenster im Zimmer meistens offen.` answer `das`
- `Er wohnt hier. ___ Haus ist alt.` answer `Sein`

**Fix:** treat empty string and whitespace-only as absent. Add a test with
`cue=""` explicitly, since `cue=None` is evidently already covered and did not
catch this.

### 2. `error_correction` items are rejected for having no gap (24 rejections)

```
Missing gap placeholder '___' in prompt.
Ich sehe ein Mann auf der Straße. Der Mann liest ein Buch.  -> answer 'Der Mann liest'
```

An error-correction item presents an incorrect sentence for the learner to
fix. **It correctly has no gap.** Layer 1 requires `___` unconditionally, so
24 of the 55 error-correction candidates in this run died on a rule that does
not apply to their type. This one is not new; it has been quietly deleting an
entire item type for the whole project.

**Fix:** skip the gap-presence and gap-count checks when
`item.type == "error_correction"`, and add the check that type actually needs,
namely that the prompt contains a genuine error and the answer is its
correction.

Recovering both takes acceptance from 87 to about 147 of 300, or 49%.

## A handoff error of mine

The run covered **12 topics, not 87**. `--topics-per-cefr` still defaults to 3,
and I gave you `--pilot 300` without raising it, so 300 items spread over 12
topics at roughly 25 each. The `--classes` filter worked correctly; the topic
sample size was never widened to match.

**Fix:** for a 300-item quality pilot, `--topics-per-cefr 25` or a new
`--all-topics` flag. This also means the cycle 1 numbers describe 12 topics and
should not be read as a taxonomy-wide result.

## The 15 defects, by root cause

### Determiner paradigms are being mixed (6 defects, the largest class)

| # | Prompt | Accepted | Fault |
|---|---|---|---|
| 2 | Wir sehen eine Katze. ___ Katze schläft. | Die, **Diese** | demonstrative under a definite-article topic |
| 5 | Er trinkt einen Saft. ___ Saft ist kalt. | Der, **Dieser** | same |
| 7 | Wir besuchen eine Ausstellung, obwohl ___ Eintritt teuer ist. | der, **dieser** | same |
| 13 | Der Tisch ist neu. Dort steht ___ Lampe. | eine, **diese** | indefinite plus demonstrative |
| 17 | Da draußen im Regen steht ___ Mann, der... | **kein, dieser** | negative plus demonstrative, opposite meanings |
| 8 | Anna kocht gern, weil ___ Küche sehr groß ist. | ihre, **diese, unsere, meine** | demonstrative plus three unforced persons |

`determiner_art_type` already checks that accepted answers do not span
`Def`/`Ind`/`Neg`. **Demonstratives are not in that partition**, so `dieser`
passes as compatible with `der`. Item 8 additionally shows the possessive
person problem flagged after the fix-5 pilot and never fixed: nothing forces
first versus third person.

**Fix:** add `Dem` as a distinct determiner type, and for possessive topics
require that person and number match the established possessor.

### `error_correction` is not implemented as a type (4 defects)

Items 16, 36, 61 and 62 are typed `error_correction` but are ordinary gap
fills with no error to correct. Two of them leaked the authoring instruction
into the learner-visible prompt, misspelled:

```
Korrgiere den Fehler: Die Leitung ___ Unternehmens hat beschlossen...
```

Combined with the 24 rejections above, this type is broken end to end: the
generator does not produce it, the verifier does not check it, and layer 1
rejects the few real ones.

**Fix:** either implement it properly (prompt asks for a corrected sentence,
verifier confirms exactly one error exists and the answer fixes it) or remove
it from every `eligible_types` list until it is implemented. I recommend
removing it in cycle 2 and implementing it later, because a half-present type
is producing defects in both directions.

### Degree confusion (1 defect)

Item 39: `das ___ (groß) Fenster` accepts `große` and `größte`. This is the
gap I flagged at the end of cycle 1 and did not close. The cue is the positive
form and the superlative is admitted.

**Fix:** when a cue is present, the accepted set must match the cue's `Degree`.
The tagger now supplies `Degree` reliably, so this is a small check.

### `adjektiv_feste_praepositionen` needs its table (4 defects)

| # | Fault |
|---|---|
| 73 | `müde ___ dem langen Weg` accepts `von` and `nach`. Only `von` is the fixed preposition |
| 75 | `Angst ___ dem Hund`. **Angst is a noun**, so this belongs to `nomen_feste_praepositionen` |
| 76 | `war er zu jeder Diskussion ___` blanks the **adjective**, not the preposition the topic tests |
| 77 | `bekannt ___ ihre Operationen` accepts `wegen`, which governs the genitive; `wegen ihre` is ungrammatical |

This is the `lexical_table` class behaving exactly as predicted without its
table. Cycle 4 addresses it. No action in cycle 2.

## What is genuinely good

72 of 87 are clean, and the cued items are again the strongest. Every
comparative (19 to 23), every null-article adjective (40 to 48), all seven
Konjunktiv II items (63 to 69) and all seven Futur II items (81 to 87) are
correct. The genitive set (53 to 60) is textbook: eight accepted answers per
item, all carrying one form, which is the intended behaviour and not a defect.

The tagger did what it was brought in for. Zero `Unk` facets against 67.9%
before, and no defect in this audit traces to a facet the system could not
derive.

## Cycle 2 scope, revised

Cycle 2 was to build generate, tag, select, blank for article and adjective
declension. That still stands, and the audit adds three items that belong with
it because they are all about the same topics:

1. Empty-cue regression. One line plus a test.
2. `error_correction` gap check, and remove the type from `eligible_types`
   until implemented.
3. `Dem` as a distinct determiner type; possessive person matching.
4. `Degree` consistency against the cue.
5. Widen the pilot topic sample.

Items 1, 2 and 5 are near-free and should land first so the cycle 2 pilot is
not measuring known bugs. Items 3 and 4 become largely moot once blanking
lands for these topics, since the accepted set will then be computed rather
than accepted, but they are cheap and the old path stays live for comparison.
