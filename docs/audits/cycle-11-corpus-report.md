# Cycle 11 audit: the corpus pilot re-run

337 accepted, 44 topics, 480 sampled. All 337 audited by hand.

Verify-only, no sentence generation. The verification pass ran fully: 337
verified, 143 rejected, **0 not run**.

## Headline

| | C10 (first corpus run) | **C11 (this run)** |
|---|---|---|
| Sampled | 480 | 480 |
| Accepted | 366 | **337** |
| Topics with items | 47 of 49 | **44 of 49** |
| Model rejection rate | 24.1% | **29.8%** |
| Wrong-answer defects | 9 of 366 (2.5%) | **1 of 337 (0.3%)** |
| Non-unique answers | not separated | **9 of 337 (2.7%)** |
| Wrong cue | 2 | **8** |
| Bad carrier | 1 | **6** |
| Total defects | 19 (5.2%) | **24 (7.1%)** |

The total went up and that number is misleading on its own, the same way it
was last cycle. **Every one of the five C10 selector defect classes is
gone.** The 24 here are three classes, two of which C10 did not count and
one of which C10 counted as two cosmetic items.

Read the defect rate by severity instead:

| | Count | Share |
|---|---:|---:|
| Learner is taught something false, or marked wrong for a correct answer | **10** | **3.0%** |
| Item is solvable but shows a wrong cue or bad German | 14 | 4.2% |

## What the C10 fixes did

Every C10 class was retested against a fresh sample at the same quota.

| C10 defect class | C10 | C11 |
|---|---:|---:|
| Reflexive case routing | 3 | **1** |
| `zustandspassiv` catching the perfect of a motion verb | 2 | **0** |
| `passiv_praesens` catching Futur I | 2 | **0** |
| Konjunktiv II present catching the past | 3 | **0** |
| `kasus_dativ_formen` accepting three other cases | 3 | **0** |
| `relativsatz_nom_akk` taking a non-relative `die` | 1 | **0** |
| `adjektivdeklination_bestimmt` with no article present | 1 | **0** |
| Cue spelled the Swiss way (`schliessen`) | 2 | **0** |

`kasus_dativ_formen` produced 8 items, all genuinely dative
(`gehören`, `zuschauen`, `folgen`, `schreiben`, `schicken`, and two
recipient datives). `verben_reflexiv_akk` produced 8, all correct.
`konjunktiv_ii_vergangenheit` produced three past-Konjunktiv items,
including the Ersatzinfinitiv shape that failed twice before:

    Wenn ich es dir ___ sagen wollen, hätte ich es dir gesagt.  -> hätte

That was the class `docs/audits/tagger-accuracy-vs-gold.md` predicted would
be worst, on the feature spaCy conflicts with gold most often (Mood,
12.59%). It is clean.

## The 24 defects

### 1. Reflexive case routing, 1 item (was 3)

    Dabei habe es sich um zwei verschiedene Gruppen gehandelt.  -> sich

`es handelt sich um` takes an **accusative** reflexive; this landed in
`verben_reflexiv_dat`.

Diagnosed rather than guessed at. The governing verb `handeln` has no
entry in the corpus lexicon at all, and cannot have one: `sich handeln um`
only ever occurs with the case-ambiguous `sich`, never with the
unambiguous `mich`/`mir` the lexicon is built from. So it falls through to
the structural check, which asks whether a bare accusative object sits in
the clause. It found one: `zwei verschiedene Gruppen`, which is not a bare
object at all, it is the object of the preposition `um`. The
walk-back from `Gruppen` to its preposition stopped dead at the cardinal
`zwei`, because `CARD` was missing from `_NP_INTERNAL_TAGS`.

**Fixed.** `CARD` added, and the addition is measured rather than
asserted: over 12,000 corpus sentences (6,000 each from Tatoeba and
Leipzig, the reader the pilot itself uses), `CARD` walls off **4.82% of
every prepositional phrase seen**, more than twice the next candidate
(`ADV`, 2.57%). A cardinal between a preposition and its noun is always
inside that noun phrase. `ADV` and `ADJD` were deliberately not added: an
adverb can equally well end a phrase, so walking past one would guess.

Both directions are pinned by tests, so a future widening cannot buy
precision by trading it for recall: `sich handeln um` must route
accusative, and `Er hat sich drei neue Bücher gekauft` must still route
dative.

### 2. Non-unique answers, 9 items, all in the two pronoun topics

This class is new. It is the largest single group and the most serious,
because the learner types a correct German word and is marked wrong.

`pronomen_personal_nom`, 4 of 8:

    ___ muss Tom fragen, wie ich zu seinem Haus komme.   -> Ich   (Er, Sie)
    ___ können nicht viel tun, bis wir Toms Erlaubnis haben. -> Wir (Sie)
    Tom hat mir die Bilder gezeigt, die ___ ... gemacht hat. -> er  (sie)
    ___ hat ihren Pullover angezogen.                    -> Sie   (Er)

`pronomen_personal_akk`, 4 of 9:

    ... brauchen wir jemanden, um ___ mit ihm zu teilen. -> es    (ihn)
    Er war ins Lesen vertieft, als ich ___ besuchte.     -> ihn   (sie)
    Wenn du laut sprichst, kann ich ___ hören.           -> dich  (es, ihn, sie)
    Die Nachrichten ... erfüllten ___ mehr und mehr mit Sorge. -> mich (uns, ihn)

Plus one weaker case in the same shape.

`pronomen_personal_dat` produced 8 items and **all 8 are unique**, which is
the diagnosis: every dative item happened to carry an anchor elsewhere in
the sentence that forces the person (`Es kommt mir so vor, als ob **ich**
...`, `Wenn du nicht mit uns bist, dann bist du gegen **uns**`, `Sie hat
ihm einen Kuchen für **sein** Fest gemacht`). Where the anchor is present
the item is sound; there is simply no gate requiring one.

The four nominative items that ARE unique are unique for exactly one
reason: the finite verb's form is non-syncretic (`sage`, `werde`,
`brauchst`, `Träumst`). The four that are not have a syncretic verb
(`muss`, `können`, `hat`).

The same anchor requirement already exists in this codebase, for the
article topics, and was added for the same reason: a bare nominative with
no anchor is unsolvable. It was never extended to the pronoun topics.

**Not fixed; two options, and this needs a decision.**

* **A. Anchor gate for `pronomen_personal_nom`.** Accept only when the
  clause's finite verb form is non-syncretic for the answer's
  (Person, Number), which is a deterministic paradigm lookup against
  tables this module already has. Nothing else changes. Measured cost on
  this sample: 4 of 8 items, so roughly half the topic's supply. It is
  pedagogically the right test anyway, since pronoun-verb agreement is
  what the topic teaches.
* **B. Nominative-citation cue for `pronomen_personal_akk` and `_dat`,
  exactly the device that resurrected `artikel_bestimmt_nom`.** `(er)` for
  `ihn`, `(ich)` for `mich`, `(sie)` for `ihr`. The learner still has to
  produce the case form, which is the whole topic, and the answer becomes
  unique. Cue equal to answer never arises, because a nominative cue and
  an accusative or dative answer are different words. Costs no coverage.

A and B are complementary, not alternatives: B cannot help the nominative
topic (the cue would be the answer, with nothing left to work out), and A
would cost the accusative and dative topics coverage that B gets for free.
Recommendation is to do both.

### 3. Wrong cue, 8 items

    Ich habe die ___ halbe Stunde des Films verpasst.  -> erste   cue (erster)
    Bitte geben Sie Ihre Hausarbeit bis zum ___ Tag ab. -> letzten cue (letzter)
    Haben Sie noch ___ Hobbys?                          -> andere  cue (anderer)
    Sie können heute ___ Leistungen bringen.            -> besondere cue (besonderer)

and four more of the same shape. Every ordinary adjective in these topics
is cued with its bare base form (`(gut)`, `(klein)`, `(öffentlich)`); the
determiner-like ones are cued with an inflected masculine nominative,
because that is what this tagger gives as their lemma. That is
`docs/audits/tagger-accuracy-vs-gold.md`'s measured 6.69% lemma-conflict
rate reaching a learner verbatim, in the one place a wrong lemma is shown
rather than merely costing a candidate. Cycle 8 called this cosmetic. It
is not: it is inconsistent, and `(erster)` is simply not the citation form
of `erste`.

**Fixed**, using that document's own recommendation 1: derive the surface
form back from the cue and require it to equal the answer. The cue is kept
only if declining it at the item's own (declension, cell) reproduces the
answer. Two real German stem changes are handled so the gate does not buy
precision with false negatives: `hoch` to `hoh`, and the `-el`/`-er`
e-elision (`dunkel` to `dunkle`, `teuer` to `teure`), the latter computed
from the rule rather than enumerated, and restricted to `-er` after a
vowel so `sauber` to `saubere` still round-trips.

No cue is repaired by guessing a stem. `anderer` reduces to `ander` and
`besonderer` to `besonder`, neither a German word, so there is no correct
cue to fall back to and the cue is dropped. The item then usually fails
the uniqueness gate and is dropped too: measured cost is those 8 items,
2.4% of the sample, against 21 correctly cued adjective items kept.

One existing test asserted `cue == "Letzter"` for the answer `Letzte`. Its
subject was capitalisation matching, which is right and is still covered
by the `Alte`/`Alt` test beside it, but the value it froze was this defect.
It is inverted rather than deleted, with the reason recorded in the test,
per CLAUDE.md rule 7.

### 4. Bad carrier, 6 items, all Leipzig

Four are scraped-web artefacts sharing one shape, a colon joining a
fragment that is not a sentence:

    Ein ___ Mädchen auf einem Sofa (Symbolbild): Die Möbelhauskette
      Møbelkompagniet eröffnet ihre erste Filiale in Deutschland.
    Mobilitätslösungen: Während ___ Werkstattaufenthalts stellen wir ...
    Der letzte Eintrag ___ in der vergangenen Saison gemacht: „2025 “.
    Auch beim Fahrrad ist der Bremsweg ___ als gedacht: Reaktionstest
      bei der Verkehrswacht.

A caption glued to a headline, a page heading glued to body text, a
trailing quoted stub with a stray space, and a headline glued to its
subheading. Each is answerable and each answer is correct, so none is a
wrong-answer defect; they are text a learner should not be shown as a
model sentence. Carrier validation's `multiple_sentences` check looks for
sentence-final punctuation and does not see a colon.

One is orthography from the source:

    Es gibt ... ein Seniorenheim, ___ Bewohner im Wasserwald Spazieren gehen.

`spazieren gehen` is lowercase. This is a Leipzig typo, not ours.

One is the recorded Swiss-orthography limit, in a carrier rather than a
cue this time:

    Alle sechs Jahre ___ es eine grössere Prozession ...   -> gab

`grössere` is `größere` in standard German. Swiss publications are in
Leipzig and there is no reliable way to tell Swiss `ss` from standard `ss`
by vowel length, which is TODO section 1's first entry.

**Not fixed; needs a decision.** The colon shape is the only one worth a
rule, and the obvious rule has a false negative: rejecting every carrier
containing a colon costs 5 of 337 (1.5%), of which one is the **only**
`zustandspassiv` item in the run (`Aber: Das Thema ist damit nicht beendet
...`), a perfectly good sentence. Options:

* **A. Reject any carrier containing a colon.** Simplest, deterministic,
  costs 1.5% including the one good sentence above.
* **B. Reject only when a colon-delimited segment of three or more tokens
  has no finite verb.** Keeps the `Aber:` sentence. Catches the caption
  and the subheading; does not catch the one-word heading prefix
  (`Mobilitätslösungen:`) or the quoted stub. So 2 of 4.
* **C. Do it at the corpus reader instead of the carrier validator**, and
  drop Leipzig lines whose text before a colon is shorter than about six
  tokens and has no finite verb. Catches all four, keeps `Aber:` only if
  the one-token threshold is set below it. Needs measuring against the
  full corpus before I would trust the numbers.

Also worth one line, not a defect: one accepted `partizip_i_attributiv`
item is a news quote about a `"laufenden Völkermord"`. There is no content
filter on Leipzig carriers at all. If that matters for a learner app, a
small blocklist applied to Leipzig only is cheap.

## Tatoeba against Leipzig

| | Items | Defects | Rate |
|---|---:|---:|---:|
| Tatoeba | 232 | 12 | 5.2% |
| Leipzig | 105 | 12 | 11.4% |

The gap widened from C10 (4.8% vs 6.1%) and it is entirely composition.
Every one of the six carrier defects is Leipzig, because Leipzig is
scraped news and web text and Tatoeba is hand-written learner sentences.
Strip the carrier class out and the two are 5.2% and 5.7%, which is the
C10 result again. Leipzig still earns its place: it supplies the news
register and the rare constructions Tatoeba lacks, and every one of its
carrier problems is a filtering job on our side, not a quality problem
with the corpus.

## The 143 model rejections, and the strongest finding in this run

The rejections are not spread evenly. They concentrate almost entirely in
the topics where the blank is a tense-carrying or mood-carrying auxiliary,
and they wiped five topics out completely:

    10  futur_i        10  passiv_modalverben  10  perfekt_sein
     5  futur_ii        5  zustandspassiv_zeiten

with `zustandspassiv` at 1 accepted of 10, `perfekt_haben` at 2,
`konjunktiv_ii_irreal_gegenwart` at 2, and `passiv_praesens` at 3.

The verifier's reasons are all one reason:

    Neben 'ist' ist auch 'war' (Plusquamperfekt) eine ebenso richtige Lösung
    Neben 'will' ist auch 'wollte' (Präteritum)
    Neben 'kann' ist auch 'könnte'
    Ohne Hinweis sind neben 'werde' auch Modalverben wie 'kann', 'will'
      oder 'muss' ebenso passend

Note `Ohne Hinweis`: without a cue. That is the verifier naming the fix.

Splitting the whole run by whether the topic gives a cue at all:

| | Topics | Sampled | Accepted | Rate |
|---|---:|---:|---:|---:|
| Cued | 26 | 260 | 221 | **85%** |
| Uncued | 23 | 220 | 116 | **53%** |

**Every topic below 60% acceptance is uncued. All twelve of them.** Every
cued topic is at 60% or above, and thirteen are at 100%.

The uncued topics that do fine are the ones where the answer is forced by
structure and needs no cue: relative pronouns, `zu`, prepositional case.
The uncued topics that collapse are exactly the ones where the blank is an
auxiliary, and there `wird`/`will`/`kann`/`soll`/`muss`/`würde` are all
grammatical in the gap.

This is not the verifier being harsh. It is correct every time: those items
really do have several right answers. And the fix has a precedent the
owner already approved and that this same run vindicates, the invariant
citation cue that brought `artikel_bestimmt_nom` back from four dead
cycles to 10 of 10 here.

**Proposal: extend the cue to the auxiliary topics.** `(werden)` for
`futur_i`, `futur_ii`, `passiv_praesens`, `passiv_praeteritum`,
`passiv_modalverben`; `(sein)` for `perfekt_sein`, `zustandspassiv`,
`zustandspassiv_zeiten`; `(haben)` for `perfekt_haben`. The learner still
has to produce person, number and tense, which is what each topic tests,
and `wollen`/`können` stop being admissible. `modalverben_praesens`,
`praeteritum_vollverben` and `verb_praesens_*` already work exactly this
way and sit at 60 to 100%.

The two Konjunktiv topics need more than a cue: with `(sein)` the learner
could still answer `war` where `wäre` is wanted. Those need the carrier to
contain an irrealis marker (`wenn`, `an deiner Stelle`, a second
Konjunktiv form), which all three accepted items happen to have. That is a
separate, smaller job.

## Two things that are not defects but should be recorded

**`blanked_lemma` was not serialised.** The diversity cap is keyed on it,
but it never reached the review file, so lemma diversity could only be
checked from the report's aggregate counts and never audited item by item.
**Fixed**, one line, no behaviour change.

The cap itself is working where diversity is possible: `nomen_plural` 10
distinct of 10, `adjektivdeklination_bestimmt` 9, `praeteritum_vollverben`
9, `verb_praesens_vokalwechsel` 8, `partizip_i_attributiv` 6 distinct with
no lemma used more than 3 times, against 7 of 10 `laufend` in C10.

**The CEFR ceiling is inert on about a third of what it sees.**
`VocabularyStore._band_distance_over_ceiling` returns 0, meaning "no
distance to charge", for a word it cannot resolve. On corpus German that
is not a rare edge case: **122 of the 337 accepted items (36.2%) contain
at least one content word the store could not resolve at all**, and 11.1%
of all content tokens are unresolvable. `Haupteinnahmequellen`,
`Drahtgitterelementen`, `Charterraten` and `Bewertungsszenarien` all passed
an A1 ceiling for free. The words most likely to be too hard are exactly
the ones the ceiling does not examine.

This is a level-control gap, not a correctness defect, and the fix is
already on disk: `data/fixtures/corpus/frequency/de_opensubtitles2018_top50k.txt`.
Treating an unresolved content word as a hard violation unless it is in
that list, and skipping tokens the tagger calls `PROPN` so news proper
nouns do not trigger it, would have rejected **70 of 337 items, 20.8%**,
spread across every topic. Some of those rejections are clearly right
(`Drahtgitterelementen`, `tiefgekühltes`, `hineinzudenken` at A2) and some
are collateral from proper nouns spaCy did not tag as such (`Gadou`,
`Biel`).

**This is a coverage-versus-level trade and it is the owner's call.**
Options: leave it (36% of items unchecked), apply the frequency fallback
(costs 20.8% of supply), or apply it only to topics at A1 and A2 where a
hard word hurts most (unmeasured, but it would be a smaller cut).

## Coverage notes

Five topics produced nothing: `futur_i`, `futur_ii`, `passiv_modalverben`,
`perfekt_sein`, `zustandspassiv_zeiten`. In C10 `perfekt_sein` produced 10
clean items and `futur_ii` produced 1, so this is the verifier getting
stricter about uniqueness on the same material, not the corpus running dry.
The cue proposal above addresses all five.

`relativsatz_nom_akk` produced 9 items and **all 9 are nominative**. The
topic's accusative half is untested. Not a defect, but the topic is
delivering half of what its name claims.

`futur_i`, `futur_ii` and `zustandspassiv_zeiten` remain the case for AI
generation: too rare even in 80,000 corpus sentences to sample reliably.
That was C10's finding and it stands.

## Verdict

The C10 fix round worked. Every selector class it targeted is clean
against fresh corpus German at the same quota, including the Konjunktiv
and dative classes that had regressed twice before, and the wrong-answer
rate fell from 2.5% to 0.3%.

What the run surfaced instead is one measured design fact: **an uncued
auxiliary blank is not a well-formed exercise**, and the verifier has been
telling us so in German 143 times. 85% acceptance cued against 53% uncued,
with every collapsing topic on the uncued side, is the clearest signal any
cycle has produced. The cue is not a hint, it is what makes the answer
unique, and it is the same fix the owner already made for the article
topics.

Three fixes landed in this cycle (reflexive `CARD` walk-back, adjective cue
round-trip, `blanked_lemma` serialisation). Three decisions are open and
are listed in TODO section 2: the pronoun anchor and cue, the auxiliary
cue, and the CEFR unknown-word policy.
