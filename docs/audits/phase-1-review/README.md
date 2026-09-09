# Phase 1 card review (8 to 9 September 2026)

The owner's zero-defect policy: every exercise is reviewed before a learner
sees it. This directory is the record of the first review of the phrase deck.
It is a dated record, not instructions; the current state is in
`docs/project-state.md`.

## What was reviewed

Deck version `eb2427b97088`: 5,416 units and 28,270 cards, built from the
whole corpus (`data/deck/` at commit `94efd3f`). Every card was read by a
Claude Opus agent in batches of 600 (48 batches), and every unit in batches
of 700 (8 batches, of which 6 completed before the account's session limit;
batches 02 and 06, units ranked 1,401 to 2,100 and 4,201 to 4,900, are still
open). `findings-round-1.jsonl` holds every finding verbatim;
`reviewed-card-ids-round-1.txt` lists every card id that was read.

The reviewers judged six card categories (wrong unit, wrong gaps, bad
sentence, bad gloss, wrong case, bad unit) and six unit categories (not a
phrase, bad citation form, wrong case, wrong CEFR, duplicate, wrong gloss).
Same-vendor caveat: one Claude reviewing an output of a deterministic
pipeline, not of another model, so the CLAUDE.md section 10 cross-vendor
rule does not apply, but a reviewer's blind spots are still one reviewer's.

## What was found

| | Findings | High severity |
|---|---:|---:|
| Cards read | 28,270 | |
| Card findings | 1,619 (5.7%) | 723 |
| Units read | 4,199 of 5,416 | |
| Unit findings | 1,078 | 390 |

Card findings by category: bad sentence 816, bad unit 361, wrong unit 316,
bad gloss 97, wrong case 23, wrong gaps 6. Bad sentences were mostly news
fragments, truncated source lines, headline style without articles, stray
quotation marks, Swiss spelling and reported-speech Konjunktiv I.

Unit findings by category: not a phrase 519, bad citation form 285, wrong
CEFR 179, duplicate 62, wrong case 33.

## What changed because of it

Systematic causes were fixed in code, with tests, so they cannot recur:

- A pronominal adverb used as an object (`darum bitten`, `dagegen sein`) or
  a separable prefix (`daher|kommen`) is no longer a connector.
- A verb used reflexively in a sentence is owned by the reflexive detector;
  `sich setzen auf` no longer also counts as `setzen auf`.
- `als` carries no case (`gelten als`, `bezeichnen als`).
- A capitalised adjective inside a sentence is a name (`Vereinigten
  Staaten`), not a collocation; adjective-noun pairs need count 8 and lift 8.
- A verb lemma the tagger garbled (`benimmsen`, `erinnerstn`) is not a word
  in the dictionary and yields no unit.
- A fused separable participle in predicate position (`ist ausgezeichnet`)
  is an adjective; `hinzukommen` in a zu-infinitive of `hinkommen` abstains.
- Sentences with reported-speech Konjunktiv I, an answer token repeated
  outside the gaps, or an unbalanced quotation mark never become cards.
- Time, measure and place frames (`am Samstag`, `an der Unfallstelle`) are
  not verb complements.
- Two-part connectors accept only their canonical second part (`zwar …
  aber`, not `zwar … doch`), so the displayed unit never contradicts the gap.

Everything else went into the curated lists, each entry with the reviewer's
reason: 834 units into `data/phrases/exclude.yaml`, 1,085 cards into
`data/phrases/excluded_cards.yaml`, and 345 case, citation-form and CEFR
corrections into `data/phrases/unit_overrides.yaml`.

## Rounds 2 to 8

Each later round read only what the previous round had not: the cards and
units new since the last reviewed deck version.

| Round | Deck | Cards read | Card findings | Units read | Unit findings |
|---|---|---:|---:|---:|---:|
| 2 | `c9e740cf8713` | 1,931 | 117 | 1,111 | 347 |
| 3 | `0494e734f279` | 550 | 52 | 959 | 72 |
| 4 | `3b48362f0ed8` | 57 | 11 | 7 | 3 |
| 5 | `a6bfeddcf4b1` | 9 | 4 | 0 | 0 |
| 6 | `dc5c95711d0d` | 56 | 2 | 0 | 0 |
| 7 | `04b08c6a02f5` | 2 | 1 | 0 | 0 |
| 8 | `54a2856481dd` | 1 | 0 | 0 | 0 |

Round 2 exposed a second layer of systematic causes, now rules with tests:
existential `es gibt` is not `eine Möglichkeit geben`; a particle inside
`ab und zu` or `hin und her`, a second particle on one verb, the
correlative `da` and a postpositional `aus` are not prefixes; a participle
used adverbially is an adjective; spelled-out clock times are frames; `ob`
inside `als ob` belongs to `als ob`; a reflexive pronoun on the auxiliary
still marks the verb reflexive; reciprocal `gegenseitig` and `einander`
sentences teach no reflexive unit. Rounds 3 and 4 found no new cause; round
5 found two more (a pronominal adverb of a seeded verb read as a connector
whatever the parser's label, and a subordinate-clause fragment the validator
accepts), both rules now. Each rebuild replaces dropped cards with new ones,
so the rounds shrink until the replacements are clean.

## Step 1 of the stepped review on the wide corpus (9 September 2026)

The corpus was widened to six sources (Tatoeba, three Leipzig 1M packages,
the full 2025 news file, an OpenSubtitles sample), which rebuilt the deck as
`c925dce41c96`: 9,192 units and 50,111 cards. Reading all of it again would
have spent reviewer time on cards that a rule fix would remove anyway, so
the owner asked for a stepped review: read the top of the ranking, fix the
systematic causes, rebuild, then read the next band of what remains.

Step 1 read the first 2,400 cards and the first 700 units by rank, and for
the first time by two vendors: Claude Opus agents (four 600-card batches, one
700-unit batch) and Gemini Flash through the `gemini-executor` skill (twelve
200-card batches, two 350-unit batches). `findings-round-step1.jsonl` holds
both sets, each row tagged with its `reviewer`.

| | Claude | Gemini | Both flagged | Claude only | Gemini only |
|---|---:|---:|---:|---:|---:|
| Card findings (2,400 cards) | 194 | 191 | 145 | 49 | 46 |
| Unit findings (700 units) | 138 | 185 | 59 | 79 | 126 |

On the 145 cards both flagged, the category agreed 135 times. Gemini's
extra unit findings were mostly citation forms (oblique-case adjective
displays such as `guten Zweck`, `+Akk` shown on plain reflexive verbs) and
CEFR labels; Claude's were mostly free news combinations (`verletzt Mann`,
`Leiche entdecken`) and passive-agent `durch` units.

Disagreements were surfaced, not reconciled (CLAUDE.md section 10):

- Six Gemini findings said a collocation card's gaps miss the complement
  (`Wert legen auf`: the gaps blank `Wert` and `legen`, not `auf`; `einen
  Fehler machen`: not `einen`). The gaps blank the unit's own tokens by
  design and the display carries the complement; these were not applied.
  They are in `disagreements-round-step1.jsonl`.
- Eleven overrides where both reviewers corrected the same unit differently.
  Ten are the same correction in a different shape (`der falsche Weg` against
  `falscher Weg`); Claude's form was kept. One is a real difference:
  `sich wirken auf`, where Gemini's `sich auswirken auf +Akk` is right and
  was taken.
- 23 units both reviewers dropped, 39 only Claude dropped, 7 only Gemini
  dropped. All were dropped; the reviewer is named in the reason.

Systematic causes found in step 1, now rules with tests:

- A passive agent is not a complement: `durch` on a participle (`wird
  durch ... geregelt`) yields no verb-preposition unit; `ohne` never does.
- A participle the tagger left as its own lemma (`gelitten unter`,
  `abgezogen von`, `sich eingeschlichen`) is cited by its infinitive through
  the paradigm tables, with separable and inseparable prefixes resolved
  (`unterzogen` to `unterziehen`); one that cannot be resolved yields no
  unit. A zu-infinitive left as lemma (`sich einzubringen`) loses its `zu`.
- A separable "verb" the parser invented from an adverb (`dabeihelfen`,
  `wiedergehen`, `weiterheißen`) or from a preposition heading a phrase
  (`finden Sie unter http://...`) is not a unit. Applied both in the parse
  layer and on the stored parse, so no re-parse was needed.
- A noun-verb collocation is cited with the noun as the corpus writes it:
  `Angaben machen`, `Vokabeln lernen`, `Vertrauen gewinnen` (capital kept).
- An adjective-noun collocation is cited in the nominative with its article
  (`ein guter Zweck`), not in the commonest oblique form.
- A plain reflexive verb shows no `+Akk`: the accusative is the pronoun's
  own case, not an object. A dative pronoun (`sich etwas vorstellen`) stays.
- A mined unit's CEFR is that of its hardest content word, and B2 when the
  word lists do not know one (`nachweisen` is no longer A1 because `weisen`
  is). A register clause (B2 when the everyday corpora rarely show the unit)
  was tried and dropped: on this corpus mix it moved `stattfinden` to B2.
  Formal Funktionsverbgefuege such as `zur Kenntnis nehmen` keep the
  reviewers' B2 as overrides.
- A separable-verb card whose sentence shows `sich` right after the verb
  (`es stellt sich heraus`) is the reflexive unit's, not this one's.
- Cards from the older or noisier sources are picked last (Tatoeba first,
  then news, web, mixed, subtitles), and sentences with pre-1996 spelling,
  mojibake, broken hyphenation or a dialogue-fragment shape never become
  cards.

Everything else went into the curated lists with the reviewer's reason:
70 units into `exclude.yaml`, 225 cards into `excluded_cards.yaml`, 157
corrections into `unit_overrides.yaml`.

## Step 2 (9 September 2026)

Deck `e9e2d3a05325` after step 1. Step 2 read everything step 1 had not:
5,171 cards (every remaining card with a gloss) and 4,973 units, ranks 701
to 8,885. Claude Opus read nine 600-card and eight 700-unit batches; Gemini
Flash read 26 card and 15 unit batches (three unit batches wrote no file
the first time and were rerun). `findings-round-step2.jsonl` holds both
sets with the `reviewer` tag.

| | Claude | Gemini | Both flagged | Claude only | Gemini only |
|---|---:|---:|---:|---:|---:|
| Card findings (5,171 cards) | 373 | 348 | 216 | 157 | 132 |
| Unit findings (4,973 units) | 913 | 709 | 447 | 466 | 262 |

Category agreement on shared items: 189 of 216 cards, 389 of 447 units.
The finding rate is far higher than in step 1 (18% of units against 20%
of the top 700, but the top 700 had been through eight rounds already):
the tail of the ranking, sentence counts 5 to 8, is where the parser's
inventions live.

Disagreements, surfaced and not reconciled:

- 13 Gemini "gaps miss the preposition" findings on units whose display
  now carries a governing preposition (`auf freiem Fuß`, `ein Auge werfen
  auf`, `zu guter Letzt`). Not applied; see the open item below.
- 62 override conflicts, mostly Claude giving the fuller phrase (`in
  sicherer Entfernung`) where Gemini gave the bare nominative (`sichere
  Entfernung`); Claude's form was kept. Two were substantive and Claude
  was right both times: `aussähen` is `aussehen` (Konjunktiv II), not
  `aussäen`; `stahlen aus` is `stehlen`, not `strahlen`.
- 165 units dropped by both, 288 by Claude only, 242 by Gemini only; all
  dropped, reviewer named in the reason.

Systematic causes from step 2, now rules with tests:

- A separable particle outside the prefix list and outside a curated set
  of adverbial particles (`offen`, `zugute`, `kennen`, `zurecht`, ...)
  whose fusion is not a dictionary word is a parser artefact
  (`wiewissen`, `starkvariieren`, `fürmachen`, `qmbetragen`).
- A preterite left as its own lemma (`ankamen`, `aufwuchsen`, `rochen`,
  `füllten`) is cited by its infinitive through a stem table, and the
  participle mapping now applies whatever VerbForm the tagger claims
  (`hat eingestochen` came back as `Inf`). The Goethe list is the oracle
  for "this string is already an infinitive".
- An adjective-noun unit that never occurs in the nominative is cited with
  the preposition that governs it (`in sicherer Entfernung`, `mit offenen
  Armen`); a sentence-initial capital is dropped (`heftiger Regen`); only
  real articles are carried, not `kein` or `dieser`.
- `ein bisschen` is a quantifier, not an adjective (`bissch Angst`).

Not a rule: 101 of 925 reviewed adjective-noun units were "free
compositional combinations" (`leere Flasche`, `treuer Freund`). Neither
lift, G², count nor everyday share separates them from the accepted ones
(medians identical), and a participle or word-list gate hits nine good
units for each bad one. They stay reviewer judgements in `exclude.yaml`.

Everything else went into the curated lists: 756 units, 401 cards, 451
overrides.

## What is still open

- Steps 1 and 2 have read every glossed card and every unit of the
  wide-corpus deck once, by both vendors. Cards and units new in the next
  rebuild (replacements for dropped ones) are the next step's batches.
- Adjective-noun and collocation displays that carry a governing
  preposition (`auf freiem Fuß`) blank only the adjective and noun. Both
  reviewers keep flagging the unbracketed preposition; blanking it would
  need the miner to record the preposition token.
- The CEFR default now follows the hardest word and the register; the
  reviewers' remaining per-unit CEFR corrections stay as overrides.
