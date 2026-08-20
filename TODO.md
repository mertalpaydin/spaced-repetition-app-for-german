# TODO

Standing work list. Owner-approved. Read this before starting any generation
work, and update it in the same commit that closes an item.

Status key: `[ ]` not started, `[~]` in progress, `[x]` done and verified
against real pipeline output (not just a passing test).

---

## 1. Generation fixes before the next pilot

All from `docs/audits/cycle-09-report.md`, which audited all 428 accepted
items by hand. Every item here must be verified against real output before it
is ticked. A passing unit test is not sufficient evidence: three separate
cycles have shipped a "fixed" defect that was still live in the next run.

- [x] **1.1 Reflexive case routing, both directions.** 6 items. `sich
  wünschen` is dative and landed in `verben_reflexiv_akk`; `sich treffen` and
  `sich ansammeln` are accusative and landed in `verben_reflexiv_dat`. One
  rule causes both: it treats any accusative noun phrase as a direct object,
  so `heute Nachmittag` (accusative time adverbial), `alle` (apposition) and
  even a nominative subject trigger it, while a `dass` clause does not.
  Fix: the direct-object test must identify a real object.

  Fixed: a real object now requires excluding `paradigms.TEMPORAL_ANCHOR_
  LEMMAS`-derived time nouns and a small apposition-quantifier list
  (`alle`/`beide`), a `dass`-clause is now recognised as an object in its
  own right, and a closed `ACCUSATIVE_ONLY_REFLEXIVE_VERBS` list (`treffen`/
  `ansammeln`/`freuen`/`ändern`/`beeilen`) bypasses the object scan entirely
  for verbs a mistagged Nominative subject ("viel Staub") could otherwise
  fool. Verified against all 6 exact reported sentences plus the pipeline;
  regression tests in `tests/test_blanking_selectors.py`.

- [x] **1.2 `futur_i` selector matches the passive.** 2 items. It accepts
  `werden` plus a past participle, which is the present passive. Futur I is
  `werden` plus an infinitive. This is also why `passiv_praesens` produced
  zero items: the `futur_i` selector took its sentences. One fix, two topics.

  Fixed: `futur_i` now requires a genuine infinitive in its own clause and
  rejects outright if any participle shares the clause. Verified: `futur_i`
  no longer wrongly matches either reported sentence, and a general
  transitive-verb passive (`"Die Suppe wird jeden Tag frisch gekocht."`) now
  correctly produces both a `futur_i` rejection and a `passiv_praesens`
  item. **Caveat, reported rather than silently left:** the two exact
  reported sentences ("angebraten", "aufgeladen") still produce no
  `passiv_praesens` item either, because `de_core_news_sm` independently
  mistags both separable-prefix participles `VVIZU` instead of `VVPP` in
  this context -- a different, pre-existing tagger gap in
  `passiv_praesens`'s own participle search, outside this task's scope.

- [x] **1.3 Cue gender read off the wrong token.** At least 4 occurrences, 2
  in accepted items (`(eine)` for masculine `Orangensaft`, `(die)` for neuter
  `Zimmer`) and 2 caught by the verifier (`Einkaufszettel`, `Akku`). The cue
  must be derived from the head noun's gender. Test per gender.

  Closed structurally, not by correcting the lookup: under 2.1 below, a
  determiner cue no longer reads any noun's gender at all (head or
  otherwise), so this defect class cannot recur. Verified against both
  exact reported sentences plus one direct case per gender (masc/fem/neut/
  plural) in `tests/test_blanking_selectors.py`.

- [x] **1.4 Swiss orthography.** 4 items: `Schliesslich`, `draussen`. The
  vendored dictionary cannot catch this because it was written through a
  normaliser that converts every `ß` to `ss`. The existing check is a closed
  list covering `heißen` and `groß` only, and it fired exactly once in the
  whole run. Fix with a general rule: `ss` after a long vowel or a diphthong
  is Swiss. That catches `draussen`, `schliesslich`, `heisst`, `gross`,
  `weiss`, and leaves `muss`, `musste`, `Fluss`, `Kuss` alone.

  Fixed with a general rule for the DIPHTHONG half only (`ei`/`eu`/`äu`/
  `ie` immediately before `ss`, verified against the vendored dictionary:
  236 matches, only one false positive, "diesseits", excluded by name).
  `au` and the long-vowel-single-letter words (`groß`/`Straße`/`Fuß`/`Maß`/
  `Spaß`) stay closed lists on purpose: the same dictionary check found a
  general `au` rule would reject `aussteigen`, `ausschließlich` and dozens
  more standard `aus`-prefix compounds. Catches all 5 named words plus
  `Strasse`/`Fuss`/`Mass`/`Spass`; verified `muss`/`musste`/`Fluss`/`Kuss`/
  `wissen`/`essen`/`lassen` untouched. Known false-negative cost recorded in
  `carrier_validation.py`'s own module docstring section 11: `Masse`/
  `Massen` (a real, unrelated standard word) and `außen`/`außer` compounds
  beyond the literal `draussen` are not caught.

- [x] **1.5 `adjektivdeklination_nullartikel` fires with a determiner
  present.** 1 item: `diese innovative ___ Lösung`. Fourth cycle for this
  class. Previous fixes handled an intervening prepositional phrase and an
  intervening adverbial; this time an intervening adjective defeated it.
  Fix by walking the noun phrase from the head noun, not by extending a
  backward scan again.

  Fixed by walking backward from the adjective and rejecting if ANY
  determiner exists at any distance, rather than extending the bounded scan
  again. All 4 previously-reported sentences (cycle 5/6/6/9) pinned as
  regression tests in `tests/test_blanking_selectors.py`, including the
  new one.

- [x] **1.6 Auxiliary tense ambiguity.** The `auxiliary_tense_unanchored`
  gate fired 44 times and still let this class through; roughly 15 of the
  verifier's 97 rejections are `hatte` versus `habe`, `war` versus `bin`,
  `haben` versus `hatten`. The verifier is doing the gate's job. Tighten the
  gate against the specific cases the verifier caught.

  Fixed: a Plusquamperfekt-cell candidate (`haben`/`sein` + participle,
  Past-tense aux) is no longer treated as forced by construction shape
  alone, and no longer falls back to the broad `TEMPORAL_ANCHOR_LEMMAS`
  list (which included exactly the vague adverbs the verifier caught:
  "zuvor"/"davor"/"vorher"). It now needs a narrower anchor: an explicit
  `bevor`/`nachdem` clause, or a genuine second past-tense event in the
  other clause. Verified against the staged pilot review/rejected data
  (`/mnt/user-data/uploads/Language_Learning_App/data/`): all traced
  rejected sentences now correctly flagged, all traced accepted sentences
  still pass. Regression tests in `tests/test_blanking_uniqueness.py`.

- [x] **1.7 Two topic misfilings.** `Später trinken wir dann gemeinsam eine
  Tasse Kaffee` is filed under `adjektiv_komparativ_superlativ` but contains
  no comparison. `Wenn ich nur etwas früher auf meine Ernährung geachtet
  ___, wäre ich jetzt bestimmt fitter` is filed under
  `konjunktiv_ii_irreal_gegenwart` but its condition is in the past.

  Both fixed. The comparative selector now requires "als" to be tagged
  `KOKOM` (comparative), not merely present as text -- "als kleines
  Dankeschön" tags `APPR` and no longer fires. The Konjunktiv II base
  selector now checks for a participle in BOTH directions (before as well
  as after the aux, clause-bounded), closing the same word-order gap
  `_select_plusquamperfekt` already handled -- "geachtet hätte" (participle
  before the aux, verb-final subordinate clause) now correctly routes to
  `konjunktiv_ii_vergangenheit` instead of `_irreal_gegenwart`. Verified
  against both exact reported sentences.

- [ ] **1.8 Demand is computed once and misallocates the call budget.**
  Cycle 9 spent 3 calls on each of 49 topics. Twenty topics met demand and
  were then capped, throwing away 1,746 items (339 from
  `pronomen_personal_nom` alone), while ten topics ended at zero after three
  attempts each. Generating for one topic produces items for many others.
  Fix: recompute demand after each topic completes, skip topics already
  filled as a side effect, and give the freed calls to the starved topics.

---

## 2. The cue rule (owner's decision)

- [x] **2.1 Cue with the invariant citation form of the determiner family,
  not the gender-agreed form.** Today the cue is the determiner's nominative
  form agreed to the head noun (`Nach ___ (die) Arbeit` -> `der`), which
  hands the learner the gender for free and leaves only the case to work out.

  The rule is: always `der` for the definite article, always `ein` for the
  indefinite, `mein` / `ihr` / `Ihr` for possessives. The learner works out
  gender and case, which is the whole skill.

  Done. `selectors._determiner_cue` rewritten: always `der`/`ein`/`kein`,
  or the possessive's own uninflected stem read directly off the blanked
  token's surface text (`paradigms.match_ein_word`), never off any noun.
  Case-matched to the answer's own capitalisation
  (`_cue_case_matched_to_answer`, reused from its existing modal/verb-cue
  use) so the formal `Ihr`-family still capitalises correctly.

- [x] **2.2 Drop the cue-equals-answer rejection for determiner slots.**
  Owner's reasoning, recorded verbatim: "cue being the answer is not a
  problem if the problem still requires student to identify case, declension
  etc. so 'ein' can still be both clue and an answer if the exercise requires
  identify whether its ein, eine, einen etc."

  When the required form happens to equal the citation form, that is a
  coincidence of German morphology, not a leak, because the learner cannot
  know it in advance.

  Done. `blanker._determiner_outcome` no longer calls `_cue_equals_answer`
  at all -- the one outcome builder exempt from it; every other cued
  outcome builder keeps the check unchanged (their own cue really is
  derived from the answer's own inflected form, so a match there really is
  a leak). Existing tests pinning the old rejection behaviour for
  determiner slots updated to the new, owner-approved behaviour, per
  CLAUDE.md rule 7 (explained inline in each updated test).

- [x] **2.3 Revive the topics this unlocks.** `artikel_bestimmt_nom` and
  `artikel_possessiv_nom` were dead only because of the equals-answer rule.
  `artikel_unbestimmt_kein_nom` can be cued instead of relying on a causal
  anchor. The nine accusative feminine and neuter items killed in cycle 8
  come back for the same reason.

  Known cost, recorded rather than discovered later: for roughly a fifth of
  definite-article items the required form is `der`, so cue and answer match
  and a learner who copies the cue scores correct. That is a measurement
  dilution, not a wrong item. If it matters later, the fix belongs in the
  scheduler (do not count those as evidence of acquisition), not in throwing
  the items away.

  Done, and confirmed producing items end to end through the full
  pipeline. `artikel_bestimmt_nom` keeps its own forcing anchor as a
  required gate (a bare sentence still never forces "der" over "ein"/
  "mein"/"kein"); `artikel_unbestimmt_kein_nom`'s causal-clause anchor and
  `artikel_possessiv_nom`'s kinship-plus-person anchor are both kept,
  un-deleted, and still recorded on `lexeme_anchored`, but no longer gate
  candidacy -- extended one step beyond this item's literal wording,
  because the same invariant-cue argument that revives `artikel_bestimmt_
  nom`'s siblings applies to their own anchors too, and TODO.md 2.3's own
  text already says as much for `artikel_unbestimmt_kein_nom`. A real
  interaction this task found and fixed: every determiner candidate now
  always carrying a cue means `_cued_item_type` types every item from
  these three topics `cloze_cued`, not `cloze_free` -- `data/taxonomy.yaml`
  now lists `cloze_cued` in all three topics' `eligible_types` alongside
  `paragraph_cloze`/`cloze_free`, or the pipeline silently skipped every
  item as type-ineligible.

---

## 3. The verifier

The verifier caught the wrong-gender cue twice and missed it twice in the
same run. Four responses, in order of how much they matter.

- [ ] **3.1 Ask about the cue.** The three questions cover the sentence, the
  answer's uniqueness, and whether every word is real. None mentions the
  parenthesised hint. Both catches were the model volunteering it under
  question two. Add a fourth question: is the hint the correct citation form
  of the answer. Most of what looks like unreliability is the verifier not
  having been asked.

- [ ] **3.2 Anything the verifier catches twice becomes a deterministic
  rule.** This is the structural answer. The verifier is a discovery
  instrument, not a gate, and must never be the only thing between a known
  defect class and the learner. Wrong-gender cue is not a judgement call: the
  head noun's gender is a fact the tagger gives us, so it is a code check
  that is right every time rather than half the time. Every class in the
  cycle 9 audit gets a rule. The verifier only has to catch a new class once
  in its life, after which it is converted and never relied on again.

  Precedent: `Tennisschlüssel` was a verifier miss in cycle 8, became
  question three, and in cycle 9 caught `holzigen Tisch`, `rote Software`,
  `unsere sehr geehrte Familie` and `festhoffen`.

- [ ] **3.3 Measure recall instead of guessing it.** Build an adversarial
  eval set from every defect ever hand-confirmed: 14 from cycle 7, 7 from
  cycle 8, 17 from cycle 9, each with its exact sentence, plus a sample of
  hand-confirmed clean items. Run the verifier against it and record two
  numbers: how many known defects it catches, and how many clean items it
  wrongly rejects. Tune the prompt against those numbers. Every future cycle
  adds its defects to the set.

  Until this exists, "the verifier is unreliable" is something we both
  believe and neither of us can act on.

- [ ] **3.4 Test batch size.** Items are verified 20 to a prompt. Attention
  is plausibly not uniform across a 20-item list, so an item late in a batch
  may get less scrutiny. One-line change; the eval set from 3.3 shows whether
  smaller batches raise recall. Drop the theory if it does not.

---

## 4. Corpus-sourced generation

Owner's plan: use corpora for exercise generation at scale, and supplement
with AI generation for the topics and levels the corpus cannot cover, either
because the construction is rare or because the vocabulary is not level
appropriate.

**Order matters.** Section 1 must be done first. Running the corpus through
the current selectors would measure coverage through eight known bugs, and
`futur_i` alone would swallow every passive sentence in the corpus. There
would be no way to tell "the corpus lacks this construction" from "our
selector cannot see it".

### 4.1 Acquire the corpora

- [x] **UD_German-HDT.** 189,928 sentences, CC BY-SA 4.0, gold morphology
  and gold dependency parses. Downloadable from GitHub raw; already fetched.
  Genre is heise.de technology news, 1996 to 2001.

- [ ] **Tatoeba German.** Roughly 700k sentences, CC BY 2.0 FR with some
  sentences also CC0. Short, human-written, much of it written by and for
  learners, so it is by far the best register match. **Blocked from this
  container** (`downloads.tatoeba.org` returns 403 through the proxy). Owner
  to download:
  https://downloads.tatoeba.org/exports/per_language/deu/deu_sentences.tsv.bz2

- [ ] **Leipzig Corpora Collection, German.** Owner has approved its use.
  **Blocked from this container** (the site is behind bot protection). Owner
  to download from https://wortschatz.uni-leipzig.de/en/download/ , choosing
  a German news or mixed corpus at the 1M-sentence size. The download is a
  tar archive whose `*-sentences.txt` file is one sentence per line.

  Loader already exists for Tatoeba at `src/corpus/tatoeba.py`, with CEFR and
  length filters; only an 8-line sample is vendored today.

### 4.2 Measure how much of our defect rate is the tagger's fault

- [ ] HDT has hand-annotated morphology. Run the pipeline over it twice, once
  with spaCy tags and once with the gold tags, and count disagreements on the
  tokens the selectors actually read. `Tablett` versus `Tablette`, `treue`
  tagged as a finite verb, `schalte` lemmatised to `schalen`, `das` tagged
  `PDS` instead of `ART` were all spaCy being wrong, and we have never known
  whether that is 2 percent of items or 20.

### 4.3 Measure corpus coverage per topic

- [ ] Run all 49 selectors over Tatoeba. Count candidates per topic and per
  CEFR band. This answers, with a number rather than an opinion, whether the
  corpus revives the ten topics that generation cannot reach. A construction
  occurring in 0.01 percent of sentences still yields 70 hits in 700k, which
  is the whole argument for corpus retrieval over generation for rare
  constructions.

### 4.4 New gate that only corpus sentences need

- [ ] **Standalone comprehensibility.** A sentence pulled out of a paragraph
  carries unresolved references. `Er sagte das damals nicht.` is perfect
  German and useless as an exercise, because nothing in it fixes who `er` is.
  This is the uniqueness problem arriving from a new direction and the
  existing gate only partly covers it.

### 4.5 Decide the split

- [ ] After 4.2, 4.3 and a fixed AI pilot run, compare: which topics the
  corpus covers, which only AI generation covers, and whether the AI-only
  topics are the error-prone ones. Write the resulting policy back into this
  file as the standing generation rule.

---

## 5. Owner changes that must never be overturned

Three separate times an agent reverted a fix the owner had applied by hand.
Anything in this section is pinned by a test. Do not change these values
without the owner saying so explicitly.

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5` in `src/llm/client.py`.
- `SERVER_ERROR_BACKOFF_SECONDS = 15.0`, `SERVER_ERROR_MAX_RETRIES = 5` in
  `src/llm/client.py`. Applied by the owner after the cycle 9 pilot hit a
  server error mid-run.

---

## 6. Known limits, stated rather than hidden

- Ten topics have never produced an item and so have never been audited. On
  every previous cycle, the defects landed in exactly the topics seeing their
  first output. Expect the same when those ten come alive.
- Every topic that was clean in cycle 8 was still clean in cycle 9. A
  selector that has been audited once and fixed stays fixed. The path to zero
  is bounded: run until all 49 topics have produced items and been audited.
- The verifier reduces defects. Until 3.3 exists, we have no measured recall.
