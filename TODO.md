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
  item.

  **The caveat this item used to carry ("angebraten"/"aufgeladen" still
  produce no `passiv_praesens` item) is now closed, in a later task.** Not a
  `futur_i` fix -- `passiv_praesens`'s own participle search had two
  separate gaps, both fixed:

  1. `de_core_news_sm` sometimes tags a separable-prefix past participle
     (`angebraten`, `aufgeladen`) `VVIZU` (the zu-infinitive tag) instead of
     `VVPP`, in exactly this construction. Fixed with a SHAPE-based
     participle test (`paradigms.is_participle_shape`,
     `paradigms.participle_shape_infinitive`) that does not need the
     tagger's tag to be right: a known separable prefix + `ge` + a
     participle ending is a past participle regardless of tag, and the `zu`
     infix (never `ge`) is the discriminator that keeps a genuine
     zu-infinitive (`anzubraten`) from being wrongly accepted -- confirmed
     necessary, not just theoretical: this exact tagger tags `aufzuladen`
     `VVIZU` too, correctly, in the identical clause shape. Wired in through
     one new helper, `selectors._is_participle` (tag `VVPP`/`VAPP` OR shape
     when the tag is `VVIZU`), which replaced every direct `token.tag ==
     "VVPP"` check the four `_participle_after`/`_before`(`_in_clause`)
     helpers and `futur_i`'s own inline exclusion used. A second helper,
     `selectors._participle_lemma`, reconstructs the participle's own
     infinitive lemma from its spelling when it was found via shape (the
     tagger's `.lemma` is not merely wrong but UNCHANGED surface text for
     both confirmed cases, unlike the "wrong-but-reduced" separable-verb
     lemma gap `paradigms.AUX_SEIN_LEMMAS`'s own comment already documented).
     `paradigms.STRONG_VERBS` gained `braten`/`laden` (real, hand-verified
     entries, not a new mechanism) and `paradigms.TRANSITIVE_LEMMAS` gained
     `anbraten`/`aufladen`/`einpacken` (plus their bare bases) so the two
     reported verbs actually clear the existing transitivity gate once found.

  2. Independently, `passiv_praesens`/`passiv_praeteritum` (`_select_passiv`)
     only ever searched FORWARD, unbounded, for the participle. A
     subordinate clause is verb-final in German (`"..., dass das Fleisch
     scharf angebraten wird, ..."`, the first reported sentence's actual
     shape), which puts the participle BEFORE the clause-final `wird`, the
     reverse of a main clause's order -- the old search found nothing there
     regardless of tag. Fixed by switching to the same clause-bounded,
     BOTH-directions search `_select_plusquamperfekt`/`_select_konjunktiv_
     ii_vergangenheit` already used for their own aux. Extended to
     `_select_zustandspassiv`/`_select_zustandspassiv_zeiten` too, for the
     identical reason.

  Selectors checked against this same mistagging, as asked, and what
  changed in each:

  | selector | changed | why |
  |---|---|---|
  | `passiv_praesens`/`passiv_praeteritum` | yes: shape fix, directionality fix, modal guard | both reported sentences live here |
  | `zustandspassiv`/`zustandspassiv_zeiten` | yes: shape fix, directionality fix, modal guard | identical `sein`+participle shape |
  | `perfekt_haben`/`perfekt_sein` | yes: shape fix only (`_participle_lemma`) | no directionality gap found -- already bidirectional since `_select_plusquamperfekt`'s own fix; `perfekt_sein` additionally needs `AUX_SEIN_LEMMAS` membership, which a shape-recovered separable lemma will still usually miss (documented, pre-existing, unchanged) |
  | `plusquamperfekt` | yes: shape fix only | already bidirectional |
  | `konjunktiv_ii_vergangenheit` (and its shared base, `_select_konjunktiv_ii_base`) | yes: shape fix only | already bidirectional; the shape fix also means a mistagged participle now correctly EXCLUDES a sentence from `konjunktiv_ii_irreal_gegenwart`/`_hoeflichkeit` instead of wrongly admitting it |
  | `futur_i` | yes: shape fix (its exclusion check) | the original TODO 1.2 fix already clause-scoped it; only the tag check itself changed |
  | `futur_ii` | yes: shape fix only (`_participle_lemma` for the sein/haben aux choice) | same participle-lemma unreliability |
  | `passiv_modalverben` | yes, via the shared `_participle_after` helper only | no other change needed -- does not gate on `TRANSITIVE_LEMMAS` at all |
  | `partizip_ii_attributiv`/`partizip_ii_attributiv_erweitert` | **no** | confirmed empirically: an ATTRIBUTIVE separable-prefix participle ("das gebratene Fleisch", "der aufgeladene Akku") is tagged `ADJA` by this tagger, never `VVIZU` -- a different code path, unaffected by this bug |

  **Two new, real defects found and fixed while verifying against corpus
  data, both side effects of the directionality fix above, not the shape
  fix:**

  1. A modal passive's own clause-final `werden` (`"... können keine
     Häuser gebaut werden."`) is sometimes tagged `VAFIN` (finite) by this
     tagger even though it is really the INVARIANT infinitive `können`
     governs. The old forward-only search never reached this shape (the
     participle sits before the clause-final `werden`); the new
     bidirectional search does, and would have blanked it as if it inflected
     for Person/Number -- a broken item, `passiv_modalverben`'s own
     territory, not `passiv_praesens`'s. Fixed with a new guard,
     `selectors._clause_has_other_modal`, applied to `_select_passiv` and
     both Zustandspassiv selectors: skip if a modal verb sits anywhere else
     in the same clause. Regression test:
     `test_passiv_praesens_does_not_claim_a_modal_passives_own_clause_final_werden`.
  2. **Left as a known, documented cost, not fixed:** `vergessen`'s own
     infinitive and past participle are spelled identically (a genuine,
     narrow German syncretism -- `vergessen`, not a code bug), so `"werde
     ... vergessen"` is structurally ambiguous between Futur I ("I will
     forget") and Präsens Passiv ("it is forgotten") with no tag or shape
     fact to decide between them; this ambiguity already existed in MAIN
     clause word order before this task (confirmed via `git stash`: the
     pre-existing, unmodified selector already produces the same wrong
     `passiv_praesens` candidate for `"Ich werde nie vergessen, wie ich ...
     habe."`). The directionality fix above newly exposes the SAME
     ambiguity in verb-final (subordinate/relative-clause) word order too
     (`"... die er nie vergessen wird."`), because that word order is
     exactly what the fix needed to start searching. `vergessen` is the only
     `TRANSITIVE_LEMMAS` member with this exact infinitive/participle
     syncretism; resolving it needs semantic/argument-structure information
     this module does not have. Recommended follow-up: none identified that
     does not require guessing.

  **Verified against real data, not only tests**, per this task's brief:

  - Both exact reported sentences now produce a real `passiv_praesens` item
    end to end through `pipeline.blank_sentences`.
  - `scripts/eval_corpus_coverage.py --limit 30000` (as asked), before vs.
    after, Tatoeba: `passiv_praesens` 7 -> 9, `passiv_praeteritum` 8 -> 10,
    `zustandspassiv` 2 -> 4, all other topics unchanged. Leipzig: every one
    of the 49 topics identical, before and after -- investigated rather than
    assumed benign (CLAUDE.md-style "explain every move"): the only
    Leipzig sentences in this exact 30k sample containing a shape-fixed verb
    (`aufladen`, twice) are grammatically NOT eligible present-tense
    indicative passives either way (one uses `aufgeladen` as a predicate
    adjective with no `werden` at all, the other is Konjunktiv II
    (`würde`), which `passiv_praesens` correctly requires `Mood=Ind` to
    exclude) -- a real absence, not a bug.
  - Because 30k is small enough that these topics' single-digit counts are
    dominated by chance (confirmed above), also measured at
    `--limit 100000` on Tatoeba for a steadier read: `passiv_praesens`
    19 -> 26 (+37%), `passiv_praeteritum` 21 -> 26 (+24%), `zustandspassiv`
    9 -> 16 (+78%). `futur_i` 643 -> 647, `perfekt_haben` 6423 -> 6424,
    `praeteritum_vollverben` 9035 -> 9058, `verb_sein_haben` 4251 -> 4247
    also moved, all by 4 items or fewer -- investigated, not just noted:
    confirmed by direct raw-selector diff (no candidate-set change at all
    for `praeteritum_vollverben`/`verb_sein_haben`/`praeteritum_
    sein_haben_modal`/`plusquamperfekt` before vs. after) that these are
    `blank_sentences`'s own cross-topic-duplicate resolution reacting to
    `zustandspassiv`'s/`passiv_praesens`'s now-larger candidate sets (the
    module's own `_SPECIFICITY_OVERRIDES` table already documents
    `zustandspassiv` beating `verb_sein_haben` on an identical (prompt,
    answer) pair), not a change in any of those four selectors' own logic.
  - Hand-checked the newly admitted `passiv_praesens` candidates: 27 of 29
    raw (pre-carrier-validation) candidates at the 100k scale are genuine
    present passives (subordinate/relative-clause `"..., dass/der/wo ...
    PARTIZIP wird"` shapes); 2 were not, both traced to the modal-passive
    defect above, fixed before this count. The known `vergessen` residual
    (documented above) was not present in this particular 100k sample's
    newly-admitted set, but is real and reproducible on demand.

  **Honest correction to this task's own brief:** it stated "`paradigms.py`
  already holds German morphological tables including separable prefixes."
  It does not -- `paradigms._INSEPARABLE_PREFIXES` is a disjoint, unrelated
  closed class (`be`/`ver`/`ent`/..., prefixes that never detach). The
  actual separable-prefix table lives in `src.lexicon.lemmatizer.
  SEPARABLE_PREFIXES` (already reused once, by `carrier_validation.py`'s own
  `_NON_FINITE_VERB_PREFIXES`, for the identical "strip a separable prefix
  off a fused non-finite verb form" problem). `paradigms.py` now imports and
  reuses that table rather than adding a second, parallel one, which is the
  spirit of what the brief asked for even though its own premise about
  where the data already lived was wrong.

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

- [x] **1.8 Demand is computed once and misallocates the call budget.**
  Cycle 9 spent 3 calls on each of 49 topics. Twenty topics met demand and
  were then capped, throwing away 1,746 items (339 from
  `pronomen_personal_nom` alone), while ten topics ended at zero after three
  attempts each. Generating for one topic produces items for many others.
  Fix: recompute demand after each topic completes, skip topics already
  filled as a side effect, and give the freed calls to the starved topics.

  Provenance of the figures above, since an agent correctly flagged that
  they are not in any committed document: they come from the cycle 9 pilot's
  own console output, which the owner pasted into the session and which was
  never written to a file. That is exactly why TODO 4.6 below exists. The
  console reported 147 calls, 1,746 items dropped to the per-topic cap
  (339 `pronomen_personal_nom`, 205 `adjektivdeklination_bestimmt`, 139
  `nomen_plural`), and ten topics ending with zero items before the model
  verification pass, which then took three more to the thirteen that
  `cycle-09-report.md` counts in the accepted output. The two documents do
  not disagree; they are counting at different points in the pipeline.

  Fixed: `run_demand_driven_generation` in
  `src/generation/blanking/orchestrator.py` now recomputes each remaining
  topic's deficit from a fresh snapshot of all sentences generated so far
  before every topic it serves (`_snapshot_items_by_topic`,
  `_effective_deficit`), so a topic already filled as a side effect of
  another topic's sentences is skipped with zero calls
  (`already_met_by_other_topics`), and after the main pass a redistribution
  pass gives topics that exhausted their retries and are still short
  (`used_redistributed_budget`) a second full retry budget out of the calls
  the skipped topics never spent, up to the same call ceiling as before.

  Verified against the offline mock pool (`sentence_source.
  MockSentenceGenerator`, deterministic, no network), same 49-topic A2 run,
  same call ceiling (147) both before and after:

  | | before (flat 3 calls/topic) | after (demand-driven) |
  |---|---|---|
  | calls made | 147 | 147 (same ceiling, reallocated) |
  | topics already met, 0 calls spent | 0 | 16 |
  | calls saved on those 16 topics | -- | 48 (16 x 3) |
  | topics given a second retry budget | 0 | 16 (each 3 -> 6 calls) |
  | total items produced | 447 | 459 |
  | topics with zero items | 1 (`zustandspassiv`) | 2 (`zustandspassiv`,
  `passiv_modalverben`) |

  16 topics that would have spent 3 calls each under the old flat scheme
  (`adjektivdeklination_bestimmt/nullartikel/unbestimmt`,
  `artikel_possessiv_nom`, `dativ_nach_praeposition`,
  `kasus_akkusativ_formen`, `nomen_plural`, `perfekt_haben`,
  `plusquamperfekt`, `praepositionen_akkusativ`, `praepositionen_dativ`,
  `praeteritum_vollverben`, `pronomen_personal_nom`, `verb_praesens_regelm`,
  `verb_praesens_vokalwechsel`, `verb_sein_haben`) were already filled by
  other topics' sentences and are now skipped entirely; the freed 48 calls
  went to the 16 topics that had exhausted their original retries and were
  still short (`infinitiv_mit_zu`, `modalverben_praesens`,
  `partizip_ii_attributiv_erweitert`, `passiv_modalverben`,
  `passiv_praesens`, `passiv_praeteritum`, `zustandspassiv`,
  `zustandspassiv_zeiten`, `pronomen_personal_akk`, `relativsatz_genitiv`,
  `konjunktiv_ii_hoeflichkeit`, `futur_i`, `futur_ii`, `infinitiv_um_zu`,
  `partizip_i_attributiv`, `praepositionen_genitiv_gehoben`), each getting a
  second full `1 + max_retries_per_topic` attempt budget.

  **Honest caveat:** `passiv_modalverben` went from 3 items (before) to 0
  (after), which is why `topics_with_zero` went up, not down, from 1 to 2.
  This is not a defect in the reallocation logic itself: the deterministic
  `MockSentenceGenerator` selects sentences by a hash of the requested
  theme/person/tense/register/structure combination, and recomputing demand
  changes topic processing order, which changes which combinations get
  requested when, which this mock's hash-based selection is sensitive to.
  `cycle-09-demand-driven-generation.md` documents the same limitation of
  the offline mock pool. Regression tests:
  `test_topic_already_met_by_other_topics_sentences_is_skipped_with_zero_calls`
  and `test_starved_topic_gets_a_second_budget_from_redistribution` in
  `tests/test_blanking_orchestrator.py`.

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

- [x] **3.1 Ask about the cue.** The three questions cover the sentence, the
  answer's uniqueness, and whether every word is real. None mentions the
  parenthesised hint. Both catches were the model volunteering it under
  question two. Add a fourth question: is the hint the correct citation form
  of the answer. Most of what looks like unreliability is the verifier not
  having been asked.

  Fixed: `_INSTRUCTION_DE_LIVE` in `src/generation/blanking/
  model_verification.py` adds question 4, written to match the TODO 2.1-2.3
  invariant-citation-form cue redesign (`der`/`ein`/`kein`/possessive stem,
  never gender- or case-agreed) rather than the pre-redesign contract this
  item was originally written against: it explains that a determiner cue is
  correct whenever it matches its own invariant family regardless of the
  form the gap actually needs, explicitly walks through a worked
  cue-equals-answer example so the model does not reject a correct
  invariant cue for "not agreeing" or "for giving away the answer" (owner's
  cited concern in TODO 2.2), and states the ordinary rule (citation form of
  the source word) for every non-determiner cue. `_parse_batch_response`
  now requires `hinweis_korrekt` as a strict boolean in every verdict;
  missing or wrong-typed degrades that item to `not_run`, never a silent
  `valid: true`, per CLAUDE.md rule 7's own spirit (a verifier gate must
  fail closed, not open). Could not name "Artikel" or "Kasus" directly
  (both in `_FORBIDDEN_GRAMMAR_WORDS`, CLAUDE.md rule 2): the question
  enumerates the literal invariant word forms instead of the grammatical
  category names. Full German text below.

  Verified: `tests/test_blanking_model_verification.py` (54 tests, all
  passing) checks the instruction text asks all four questions, explains
  the invariant citation form without naming a forbidden grammar word,
  gives the worked cue-equals-answer example, still rejects a cue from the
  wrong determiner family (`'die'` cited for a `der`-series answer), and
  that `_parse_batch_response` degrades to `not_run` on a missing
  `hinweis_korrekt` field. `test_forbidden_grammar_words` and
  `test_build_batch_prompt_never_leaks_the_topic_or_rule_hint` (pre-existing,
  CLAUDE.md rule 2 enforcement) still pass against the extended prompt.

  Full text of question 4 (German, as sent to the model, verbatim):

  > 4. NUR falls die Aufgabe einen Hinweis in Klammern hat: Ist dieser
  > Hinweis die richtige Zitierform (Grundform) zu der vorgeschlagenen
  > Antwort? Zeigt der Hinweis eine dieser kurzen, unveränderlichen Formen
  > -- 'der', 'ein', 'kein', 'mein', 'dein', 'sein', 'ihr', 'unser',
  > 'euer', 'Ihr' --, dann ist GENAU DIESE Form für die ganze zugehörige
  > Wortreihe immer richtig, egal welche andere Form davon im Satz an der
  > Lückenstelle tatsächlich gebraucht wird (zum Beispiel 'des', 'dem',
  > 'den', 'die', 'das' gehören alle zur 'der'-Reihe; 'eines', 'einem',
  > 'einen', 'eine' gehören alle zur 'ein'-Reihe; entsprechend für 'kein'
  > und für die Formen auf 'mein'/'dein'/'sein'/'ihr'/'unser'/'euer'/
  > 'Ihr'). Ein solcher Hinweis muss also KEINE Endung der vorgeschlagenen
  > Antwort tragen, und es ist völlig in Ordnung -- kein Fehler --, wenn
  > er zufällig genauso lautet wie die vorgeschlagene Antwort selbst: bei
  > der Aufgabe 'Er hat sich ___ (ein) neues Fahrrad gekauft.' mit Antwort
  > 'ein' ist der Hinweis 'ein' korrekt, obwohl Hinweis und Antwort
  > identisch sind. Beantworte diese Frage bei einem solchen Hinweis nur
  > dann mit Nein, wenn er zu einer ANDEREN der oben genannten Formen
  > gehört als der, die zur Antwort passt -- zum Beispiel wenn statt immer
  > 'der' der Hinweis 'die' oder 'das' steht, obwohl die Antwort zur
  > 'der'-Reihe gehört. Bei jedem anderen Hinweis (zum Beispiel Infinitiv
  > eines Verbs, Singular eines Nomens, Grundform eines Adjektivs) ist die
  > richtige Zitierform die im Deutschen übliche Grundform des Wortes, aus
  > dem die vorgeschlagene Antwort gebildet ist -- lehne den Hinweis hier
  > nur ab, wenn er das falsche Wort nennt (zum Beispiel 'Tennisschlüssel'
  > statt 'Tennisschläger' als Hinweis) oder gar keine echte deutsche
  > Wortform ist, niemals nur deswegen, weil er nicht dieselbe Endung wie
  > die Antwort trägt. Hat die Aufgabe KEINEN Hinweis ('Hinweis: (kein
  > Hinweis)'), ist diese Frage automatisch mit Ja beantwortet.

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

- [x] **3.3 Measure recall instead of guessing it.** Build an adversarial
  eval set from every defect ever hand-confirmed: 14 from cycle 7, 7 from
  cycle 8, 17 from cycle 9, each with its exact sentence, plus a sample of
  hand-confirmed clean items. Run the verifier against it and record two
  numbers: how many known defects it catches, and how many clean items it
  wrongly rejects. Tune the prompt against those numbers. Every future cycle
  adds its defects to the set.

  Until this exists, "the verifier is unreliable" is something we both
  believe and neither of us can act on.

  Done. Two golden fixtures under `data/fixtures/verification/`
  (CLAUDE.md section 7: versioned, `_meta` header records its own future-
  cycle-appends instruction so the rule survives outside this TODO entry
  too):

  - `blanking_model_verifier_adversarial.jsonl`: 38 records, 14 from
    `cycle-07-report.md`, 7 from `cycle-08-report.md`, 17 from
    `cycle-09-report.md`, spread across 21 distinct defect classes (the
    largest: 5 `nonword_in_carrier_wrong_lexeme`, 4
    `swiss_orthography_elsewhere_in_carrier`, 3+3 reflexive-case-routing in
    each direction). Each record's sentence/answer/cue is quoted, or where
    an audit only quoted a fragment, reconstructed from it and flagged
    `topic_inferred: true` rather than presented as if it were a verbatim
    quote.
  - `blanking_model_verifier_known_clean.jsonl`: 31 records. The audits
    only name clean *topics* with counts, never literal clean item text, so
    these were built by running real carrier sentences through the actual
    current `pipeline.blank_sentences()` and hand-reviewing the output,
    not invented. Several attempted topics (`modalverben_praesens`,
    `pronomen_personal_dat`, `pronomen_personal_akk`) were dropped from
    this set because unanchored/unforced carriers were correctly rejected
    by the uniqueness gate -- itself a demonstration the gate works, not a
    fixture defect.

  `scripts/eval_verifier.py` loads both, builds a minimal `BankItem` per
  record, and runs `verify_items` over each set, reporting recall (fraction
  of the adversarial set rejected or downgraded) and false-positive rate
  (fraction of the known-clean set wrongly rejected), plus a per-defect-
  class recall breakdown. `tests/test_eval_verifier.py` (14 tests) covers
  fixture loading/shape and an end-to-end run against a scripted fake
  client (perfect recall, zero FPR, proving the wiring without a network
  call).

  **Could not actually run it against the real model in this container**:
  no outbound network access here, and `src/llm/client.py` is off-limits
  to touch. Confirmed this honestly rather than reporting invented numbers:
  `verify_items` raises `httpx.ProxyError: 403 Forbidden` through the
  configured (but network-blocked) client, which `eval_verifier.main`
  catches and reports as `NOT RUN: a transport error prevented any model
  call from completing`, exit code 1, same honest-degrade shape as the
  already-existing no-API-key case. Whoever runs this next, with real
  network access and a configured key, gets real recall/FPR numbers from
  the same script; this task could not manufacture them.

- [x] **3.4 Test batch size.** Items are verified 20 to a prompt. Attention
  is plausibly not uniform across a 20-item list, so an item late in a batch
  may get less scrutiny. One-line change; the eval set from 3.3 shows whether
  smaller batches raise recall. Drop the theory if it does not.

  Done: `scripts/eval_verifier.py --batch-size N` passes `N` straight
  through to `verify_items`'s own `batch_size` parameter, without touching
  `model_verification.DEFAULT_VERIFICATION_BATCH_SIZE` (still 20). Whoever
  runs 3.3 for real can rerun with `--batch-size 5` (or any other size)
  against the same fixtures and compare recall directly; this task could
  not run that comparison itself for the same no-network reason as 3.3.

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

- [x] HDT has hand-annotated morphology. Run the pipeline over it twice, once
  with spaCy tags and once with the gold tags, and count disagreements on the
  tokens the selectors actually read.

  **Done. See `docs/audits/tagger-accuracy-vs-gold.md`; reproduce with
  `scripts/eval_tagger_vs_gold.py`.** 3,000 HDT sentences, 29,719 tokens.
  80.3 percent of sentences contain at least one disagreement. Conflicting
  values, worst first: Mood 12.59%, Gender 7.90%, lemma 6.69% (excluding a
  punctuation convention difference), Case 6.08%, tag 5.67%, Tense 5.57%,
  Number 3.37%, Person 0.40%. Missing values are reported separately and
  cost coverage rather than correctness.

  Two consequences already actionable: the `(letzter)` and `(nächster)` cues
  written off as cosmetic in the cycle 8 audit are this measurement, not
  cosmetics; and the Konjunktiv II topics, which produced their first items
  in cycle 9 and have never been audited, sit on the single worst feature. `Tablett` versus `Tablette`, `treue`
  tagged as a finite verb, `schalte` lemmatised to `schalen`, `das` tagged
  `PDS` instead of `ART` were all spaCy being wrong, and we have never known
  whether that is 2 percent of items or 20.

### 4.3 Measure corpus coverage per topic

- [x] Run all 49 selectors over Tatoeba. Count candidates per topic and per
  CEFR band.

  **Done, and the answer is yes. See `docs/audits/corpus-coverage.md`;
  raw counts in `docs/audits/data/`.** 120,000 sentences from each corpus:
  Tatoeba yields 175,640 candidates and Leipzig 172,017, and **both cover
  all 49 topics**, against 36 for AI generation in cycle 9. Every one of the
  ten topics generation could not reach is well supplied, several with
  thousands of candidates. Two findings to carry forward: the CEFR
  vocabulary filter is the next measurement and will cut both numbers
  substantially, and `passiv_praesens` at 21 per 120k is not a corpus fact
  but our own participle-tagging bug showing up against a baseline for the
  first time (see the caveat on TODO 1.2). This answers, with a number rather than an opinion, whether the
  corpus revives the ten topics that generation cannot reach. A construction
  occurring in 0.01 percent of sentences still yields 70 hits in 700k, which
  is the whole argument for corpus retrieval over generation for rare
  constructions.

### 4.4 Unresolved references: the SLOT, not the sentence

- [x] Superseded by the owner's correction, recorded verbatim because the
  original framing of this item was wrong:

  > "Er sagte das damals nicht" is a correct german sentence and it can stay.
  > It can be an exercise to if the removed token is sagte or if its negation
  > exercise like where to put nicht. It can even be used in future for vocab
  > exercise for "damals". Student should not care who "er" is. you might
  > need to rethink about unresolved references. lets not throw away
  > perfectly good sentences

  This item previously proposed a "standalone comprehensibility" check that
  would reject a corpus sentence carrying an unresolved reference. That is
  wrong and would have thrown away good carriers at scale. The problem was
  never the sentence. It is only a problem when the BLANK IS the unresolved
  reference:

      Er sagte das damals nicht.   blank "er"     -> unsolvable
      Er sagte das damals nicht.   blank "sagte"  -> fine, cue (sagen)
      Er sagte das damals nicht.   blank "nicht"  -> fine, tests negation position
      Er sagte das damals nicht.   blank "damals" -> fine as a vocabulary item

  The gate for this already exists and is per item, not per sentence:
  `uniqueness.py`'s `personal_pronoun_unanchored` and
  `nominative_pronoun_syncretic`, which fired 85 and 212 times in cycle 9.
  One good sentence yields several good items and at most one bad one, and
  only the bad one is dropped.

  **No new gate. Nothing to build.** The one thing left to check is whether
  those two pronoun gates, which were tuned against model-generated text,
  behave the same on corpus text. That is a question for the corpus audit
  (section 7 below), not a filter to write in advance.

### 4.5 Decide the split

- [~] After 4.2, 4.3 and a fixed AI pilot run, compare: which topics the
  corpus covers, which only AI generation covers, and whether the AI-only
  topics are the error-prone ones. Write the resulting policy back into this
  file as the standing generation rule.

  **Provisional policy, pending the fixed AI pilot run. Evidence in
  `docs/audits/corpus-coverage.md`.** The corpus half is settled: at an A2
  vocabulary ceiling both corpora cover all 49 topics, and at A1 Tatoeba
  covers 48 and Leipzig 49.

  1. **Tatoeba is the primary source.** Best register match, highest yield
     per sentence at every ceiling, CC BY 2.0 FR.
  2. **Leipzig is the supplement**, for constructions Tatoeba is thin on.
     Its news register survives an A1 filter far worse (83 percent dropped
     against Tatoeba's 52) but what survives is usable, and it is an
     independent 1M sentences.
  3. **Generation keeps two jobs**: any topic the corpus cannot reach even
     at full scale, which on current evidence is at most
     `zustandspassiv_zeiten`, and thematic control when the bank needs items
     about a particular subject.
  4. **Never ask generation for a rare construction again.** That single
     decision caused ten empty topics in cycle 9.

  Left open until the AI pilot runs with the section 1 fixes in: whether the
  AI-only topics are also the error-prone ones.

---

## 4.6 The run report must be written to a file

- [x] The per-topic demand report, the rejection breakdown and the model
  verification counts are printed to the console and nowhere else. After the
  cycle 9 run this cost a full round trip: the audit could not distinguish
  "the model never wrote this construction" from "the selector could not see
  it" until the owner pasted the console output by hand, and an agent later
  and correctly flagged figures taken from that paste as unsourced, because
  they exist in no committed file.

  Write the whole report to `data/blank_pilot_report.json` alongside the two
  JSONL files, with the same numbers the console prints. Every future audit
  then starts from a file rather than from a request.

  **Done for both pilots.** `scripts/step6_blank_pilot.py` gained
  `_build_report_dict` and a `--report-file` flag (default
  `data/blank_pilot_report.json`): it mirrors everything
  `_print_demand_run_report` and `_print_verification_report` put on the
  console, calls made/projected, rejected-by-reason, per-topic sentence and
  item counts, cross-topic duplicate drops, uniqueness skips, and the
  verification outcome. `scripts/step7_corpus_pilot.py` (4.7 below) writes
  the same shape of report for corpus-sourced runs, to
  `data/corpus_pilot_report.json`.

### 4.7 Verify-only pilot over corpus-extracted sentences

- [x] `scripts/step7_corpus_pilot.py`: the first pilot that judges
  corpus-derived items instead of only counting them. Reads Tatoeba and
  Leipzig sentences from disk (paths are CLI arguments, defaulting to the
  owner's uploaded extracts), runs them through the existing carrier
  validator and the 49 selectors (`pipeline.blank_sentences`, no generation
  call anywhere in this script), takes a **balanced** per-topic sample
  (`--per-topic-quota`, default 10, deterministic per `--seed`) so all 49
  topics are represented rather than whichever topics a random cut happens
  to favour, applies each item's OWN topic's CEFR vocabulary ceiling (not
  one global ceiling, since the same carrier sentence can be eligible for a
  B1 topic and ineligible for an A1 one) via
  `VocabularyStore.validate_sentence`, then sends the sample through the
  existing model verification backstop
  (`model_verification.verify_items`). Corpus provenance
  (`corpus_source`, `corpus_line_id`) is attached to every item via
  `model_copy(update=...)` without overloading `source_sentence_id`, which
  already means something else. Corpus reading is shared with
  `scripts/eval_corpus_coverage.py` via the new `scripts/corpus_reading.py`
  rather than duplicated. Outputs: `data/corpus_pilot_review.jsonl`,
  `data/corpus_pilot_rejected.jsonl` (every drop, including model
  rejections, with a reason), `data/corpus_pilot_report.json` (4.6).

  Offline run, no API key available in this environment so the model
  verification backstop legitimately did not run and the script exits
  non-zero, per its own "fail loudly" design: at `--limit 40000` (80,000
  corpus lines total, the brief's own suggested ceiling, checked rather than
  assumed), 56,178 of 80,000 lines pass length and carrier validation in
  about 8 minutes end to end. The balanced sample lands at 482 of a
  possible 490 (49 topics x quota 10). Two topics fall short: `futur_ii`
  (8 of 10, only 8 CEFR-eligible candidates existed in the whole corpus
  scanned) and `zustandspassiv_zeiten` (4 of 10, only 4 existed) -- both
  already flagged in 4.5 as the constructions closest to needing generation
  rather than retrieval. Every other one of the 49 topics hit its quota of
  10. **40,000 is far more than the sample needs**: a `--limit` of 3,000
  per source (6,000 lines total) already samples 392, close to cycle 9's
  428, in well under a minute. 40,000 only exists to close the last two
  topics' shortfall as far as the corpus can, and even then does not fully
  close `zustandspassiv_zeiten`.

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

---

## 7. Carrier validation audited against real corpora (2026-08-20)

- [x] `docs/audits/corpus-coverage.md` found carrier validation rejects 30
  percent of Tatoeba and 39 percent of Leipzig, written to judge model
  output where a rejection means the model erred, never checked against
  human-written German at scale. Audited: sampled at least 40 rejections
  per reason across both corpora (440 sentences total), hand-classified
  each, and loosened six of the ten reasons where the false-reject pattern
  could be characterised precisely enough to exclude without admitting
  anything the check exists to catch. Full method, examples and before/
  after numbers in the session report; summary here.

  Loosened, all in `src/generation/blanking/carrier_validation.py`:
  - `no_subject_found`: a bare imperative (verb-initial, no subject,
    non-interrogative) no longer requires a subject to resolve.
  - `subject_verb_disagreement`: a verb ending in the unambiguous
    2nd-singular "-st" suffix, paired with subject "du", is no longer
    rejected for the morphologizer's own mistagged Person feature.
  - `agreement_undecidable`: an all-3rd-person coordinated subject
    ("Polizei und Staatsanwaltschaft") now resolves to 3rd-plural instead
    of blanket-undecidable (and a genuine number defect on one is now
    actively caught, not silently discarded); every subject type is now
    given the same 3rd-person default a noun subject already got; a
    non-finite verb acting as a clausal subject defaults to singular.
  - `multiple_sentences`: a spaCy sentencizer split with no real
    terminal-punctuation token backing it (a false split on a brand name,
    an inverted question, or an ordinal number's period) is merged back
    into one sentence before counting.
  - `adjective_declension_mismatch`: a capitalised, non-sentence-initial,
    "-er"-ending attributive adjective ("Berliner", "Münchner", "Pariser")
    is recognised as the invariant toponymic/decade-adjective class and
    skipped, rather than checked against a declension it never follows.
  - `dangling_fragment`: "..., oder?" (the standard German tag-question
    idiom) is no longer treated as a sentence truncated on a bare
    conjunction; `APPO` (postposition) was removed from the
    continuation-expecting tag set, since a postposition is correctly
    phrase-final by definition, unlike a preposition.

  Left alone, with the false-reject pattern stated rather than guessed
  past: `missing_clause_connector` (dass-less reported speech and
  "und"-coordination are common and legitimate, but parse identically to a
  genuine comma-splice run-on -- verified empirically, no safe
  discriminator found), `no_finite_verb` (V1 imperatives and inverted
  questions are systematically mistagged as NE/NOUN/ADV by
  `de_core_news_sm`, already documented in the module's own docstring;
  fixing it means second-guessing the tagger's POS, which this module
  never does), `content_word_not_a_real_word` and
  `finite_verb_not_a_real_word` (both measured at a near-100 percent
  false-reject rate on natural text, but the cause is the vendored
  37,567-word dictionary being far too small for German's productive
  verb-prefixation -- even common verbs like "erinnern"/"bewundern" are
  missing -- not a logic gap; the only real fix is a much larger
  dictionary, out of scope here the same way `docs/audits/cycle-06-report.md`
  already ruled dictionary re-derivation out of scope for the Swiss-
  spelling check).

  **Recommended follow-up, not implemented here**: expand or replace
  `data/fixtures/corpus/frequency/de_dictionary_filter.txt` with a larger
  German wordlist, ideally covering common productive verb prefixes
  (be-/ver-/ent-/er-/zer-/emp-/miss- and the standard separable set).
  This single change would likely recover most of `content_word_not_a_
  real_word` and `finite_verb_not_a_real_word`'s false rejects without
  touching either check's logic, since the audit found the logic sound
  and the data insufficient.

  The false-negative guard: `data/fixtures/carrier_validation/
  known_bad_carriers.jsonl`, a golden fixture (CLAUDE.md section 7) built
  from every bad carrier `docs/audits/cycle-03-report.md` through
  `cycle-09-report.md` hand-confirmed, asserted by
  `tests/test_blanking_carrier_validation.py`'s
  `test_validate_carrier_known_bad_carriers_regression` before AND after
  the loosening. All nine records (eight named in the task, none dropped)
  pass unchanged: every genuine defect ("kauft ich", "viele nassen",
  "heilgemacht", Swiss "Schliesslich"/"draussen", the "dass"/"das"
  confusion) is still rejected, and the two documented, pre-existing known
  misses ("treue" for "treffe", "Tennisschlüssel" for "Tennisschläger") are
  still accepted, unchanged, exactly as documented.

  Volume, `scripts.eval_corpus_coverage --limit 30000`, same reader, same
  seed, before vs. after: Tatoeba carrier-valid 21,157 -> 22,293 (+1,136,
  rejection rate 29.5% -> 25.7%); Leipzig carrier-valid 18,395 -> 19,896
  (+1,501, rejection rate 38.7% -> 33.7%). Both corpora still cover 49 of
  49 topics after the change. Cost: none measured -- the false-negative
  guard fixture and the full non-live/non-simulation test suite (1,201
  tests) are unchanged in outcome, and mypy --strict / ruff are clean.

---

## 8. Fixes for the 19 corpus-pilot defects (cycle 10)

From `docs/audits/cycle-10-corpus-report.md`, all 366 items audited by hand.
Confidence is stated per item and is not decoration: two of these are not
solved, and saying so up front is the point.

**The pattern across four of the five classes:** a selector trusted a spaCy
tag that `docs/audits/tagger-accuracy-vs-gold.md` measured as unreliable.
Mood is 12.59 percent conflicting, Case 6.08 percent conflicting and a
further 20.26 percent absent. The general remedy is the one that already
worked for participles: never gate on a tag when the fact is derivable from
structure.

- [ ] **8.1 Reflexive case routing, 3 items. Confidence: high, and the fix is
  newly possible.** `sich verbeugen`, `sich überzeugen lassen` and `sich zur
  Wahl stellen` are accusative and landed in `verben_reflexiv_dat`. Cycle 11
  fixed this class with a closed list of five verbs; none of these three is
  on it, and German has hundreds, so the list approach is finished.

  **Build the government lexicon from the corpus instead.** In 1M sentences,
  a reflexive verb appears many times with an UNAMBIGUOUS pronoun: `ich
  verbeuge mich` is accusative, `ich stelle mir vor` is dative. `mich`/`mir`
  and `dich`/`dir` are not syncretic; `sich`, `uns` and `euch` are. Harvest
  the unambiguous occurrences, derive each verb's case, and use that lexicon
  when the pronoun in the item IS syncretic. Fall back to skipping the item
  when the verb is not in the lexicon.

  This was not possible before we had a corpus. It is now, and it is exactly
  the kind of thing the corpus should be paying for.

- [ ] **8.2 `zustandspassiv` taking the perfect of a motion verb, 2 items.
  Confidence: high.** `dass wir hierher gezogen sind` is the perfect of
  `ziehen`, we moved house. The selector sees `sein` plus a participle. The
  discriminator already exists in the codebase: `paradigms.AUX_SEIN_LEMMAS`
  lists the verbs that form their perfect with `sein`. If the participle's
  verb is on that list, `sein` plus participle is a perfect, not a
  Zustandspassiv.

- [ ] **8.3 `passiv_praesens` taking Futur I, 2 items. Confidence: high.**
  `Ich werde nie vergessen, wie ...` reads as a passive only because
  `vergessen`'s infinitive and past participle are spelled identically. This
  was recorded as a known residual when the participle fix landed and is now
  live twice.

  General rule, not a special case for one verb: **a German passive cannot
  take an accusative object.** The `wie` clause here is the object of
  `vergessen`. If the clause has a direct object, it is not a passive. That
  also covers `bekommen`, `erhalten` and every other verb with a syncretic
  infinitive and participle.

- [ ] **8.4 Konjunktiv II present taking the past, 3 items. Confidence:
  medium, and one of the three needs investigating first.**

      Wenn ich ___ gehen wollen, hätte ich's gesagt.                  hätte
      Ich wäre gerne ins Kino gegangen, wenn ich die Zeit gehabt ___. hätte
      ... dass er das Rennen ___ gewinnen können, wenn ...            hätte

  The first and third are the **Ersatzinfinitiv**: `hätte gehen wollen` has
  no participle at all, it is two bare infinitives, so a participle test can
  never catch it. Add the double-infinitive shape as a past marker.

  The second one is the problem. `gehabt ___` is a participle before the
  auxiliary, which is exactly the shape cycle 11's bidirectional search was
  supposed to handle, and it still got through. **Do not write a fix for this
  until you understand why the existing one missed it.** Guessing here is how
  a class comes back for a fourth cycle.

- [ ] **8.5 `kasus_dativ_formen` accepting three other cases, 3 items.
  Confidence: medium, and it will cost volume.** A nominative (`einer nach
  dem anderen`), an accusative (`den Murks gelesen`) and a genitive
  (`Schlagzeuger der Band`) all landed in the dative topic. Corpus syntax is
  far more varied than generated syntax and the selector's case test does not
  survive it.

  The tag alone cannot be trusted here. Require the dative reading to be
  FORCED: a dative-governing preposition, a dative-governing verb, or a
  genuine indirect-object position with a direct object present. Accept that
  this drops items whose case is real but unforced.

- [ ] **8.6 `relativsatz_nom_akk` taking an article inside an infinitive
  clause, 1 item. Confidence: high.** `fordern Experten, ___
  US-Seltene-Erden-Industrie wiederzubeleben` blanks an ordinary accusative
  article. Require the clause the pronoun introduces to contain a FINITE
  verb. An infinitive clause has none.

- [ ] **8.7 `adjektivdeklination_bestimmt` with no article present, 1 item.
  Confidence: high.** `bei den Studentinnen ___ Anklang gefunden` has no
  article on `Anklang`; the `den` belongs to `Studentinnen`. Same family as
  the nullartikel fix: the determiner must be inside the head noun's own
  phrase, not merely earlier in the sentence. Note the answer was still
  right, because strong and weak both give `-en` here, so this is a topic
  attribution defect rather than a wrong answer.

- [ ] **8.8 Two cues spelled the Swiss way, 2 items. Confidence: high.**
  `mit ___ (schliessen) Augen` should cue `schließen`. This is not the
  corpus, it is our own vendored dictionary, which was written through a
  normaliser that turns every `ß` into `ss`, leaking into text the learner
  reads. Fix at the point where a cue lemma is taken from that dictionary.
  Do NOT blanket-convert `ss` to `ß`: that would break `muss`, `Fluss` and
  every legitimately short-vowel word.

- [ ] **8.9 `futur_ii` taking the present passive, 1 item. Confidence:
  high.** Same shape error the `futur_i` fix already closed. Futur II is
  `werden` plus participle plus `haben`/`sein` infinitive. Require the full
  shape.

- [ ] **8.10 `Strässchen` in a carrier, 1 item. NO FIX. This one is honest
  and unsolved.** Standard German is `Sträßchen`. The Swiss rule added in
  cycle 13 covers diphthongs before `ss` plus a closed list, and cannot cover
  this: `ä` before `ss` is Swiss in `Strässchen` (long vowel) but perfectly
  standard in `Fässer`, `Pässe`, `Gässchen` (short vowel). German
  orthography does not mark vowel length reliably enough to decide it.

  Options, none of them clean: add `Strässchen` and its neighbours to the
  closed list, which is the list-maintenance treadmill we just abandoned for
  reflexives; or drop Leipzig sources published in Switzerland, which costs
  real volume for one defect in 366; or accept it and let the verifier catch
  what it catches. **My recommendation is to accept it and say so in the
  audit each time**, rather than pretend a rule exists.

- [ ] **8.11 Lexical monotony. Not a defect, but it makes the item count
  overstate what a learner gets.** Seven of ten `partizip_i_attributiv` items
  use `laufend`; three of nine `verb_praesens_vokalwechsel` items are `gibt`.
  Every one is correct. The sampler should diversify by lemma as well as by
  topic: cap how many items in one topic may share a blanked lemma.

### Scoreboard

17 of the 19 have a fix I would stand behind. One (8.4's middle item) needs
diagnosis before a fix is written. One (8.10) has no clean solution and is
recorded as a known limit rather than papered over.
