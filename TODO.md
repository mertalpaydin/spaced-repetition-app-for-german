# TODO

Open work only. Closed work is in `docs/audits/fix-log.md` with its reasoning
and measurements; git history has the same in commit bodies.

Rule for anyone adding here, human or agent: **an item is a thing that is not
done.** When you close one, delete it and put the writeup in the fix log. This
file was allowed to reach 1,772 lines of finished work and was useless as a
work list. Do not do that again.

---

## 1. Known limits, recorded rather than solved

Not tasks. Do not "fix" these without a decision from the owner.

- **`Strässchen` and Swiss orthography with `ä`.** Standard German is
  `Sträßchen`. The rule added for Swiss spelling covers diphthongs before
  `ss` plus a closed list, and cannot decide `ä`: Swiss in `Strässchen` (long
  vowel), standard in `Fässer`, `Pässe`, `Gässchen` (short vowel). German
  orthography does not mark vowel length reliably enough. Recommendation
  already given and accepted: report it in each audit rather than pretend a
  rule exists.

- **Free datives and adjective-governed datives are out of reach of the verb
  government lexicon.** `Ich wasche mir die Hände` and `Meiner Schwester ist
  es kalt` have no governing verb to look up. They stay dropped.

- **`ausweichen` has no usable corpus evidence** and so is absent from the
  lexicon. A real coverage gap, not a threshold artefact. `handeln` is a
  second, structural instance: `sich handeln um` only ever occurs with the
  ambiguous `sich`, so the lexicon can never have an entry for it. Cycle 11
  fixed that one item's routing structurally instead (`CARD` in the
  preposition walk-back), not by adding evidence that cannot exist.

- **A colon is not itself a defect, so the junk-text filter is narrow on
  purpose.** Requiring every colon-delimited segment to be a clause rejected
  12 Tatoeba lines of which 11 are correct German. The shipped rule is four
  specific shapes instead (`colon_joined_fragment`), and scraped-web junk
  that does not match one of them still gets through. Widening it means
  measuring against those 12 again first.

- **"Spazieren gehen" and a capitalised verb that should be lowercase.**
  A single Leipzig source typo. German nominalisation is fully productive,
  so "das Spazieren" is a real word and no dictionary can call the string
  wrong; only its role beside "gehen" makes it wrong, and neither the
  vendored word list nor the lemmatiser carries that signal. spaCy's own
  tag is circular here (NOUN because of the capitalisation).

- **3rd person singular is out of reach for `pronomen_personal_nom`.** No
  German verb form distinguishes "er" from "sie" from "es", so the verb can
  never settle a blanked 3rd-singular subject, and recovering the intended
  one needs coreference this package does not have. The cell is dropped.

- **The four CEFR frequency-rank boundaries are tunable, not settled.** The
  owner set the shape (A1 against roughly the first 5,000 words, widening by
  level) and said outright the numbers were a guess. They are constants in
  `src/lexicon/vocabulary.py`. Raising A1 and A2 is the first lever if 70%
  retention proves too steep.

- **Two cycle 12 carrier defects, one each, no rule written.** A fixed
  construction filed as a tense ("Das Geschäft hat noch bis zum 19. Mai
  geöffnet" is "is open", not the perfect of "öffnen"); and a Tatoeba
  translation with German words in English order. Each needs a different
  rule, each would be written on a single example, and cycle 11's colon
  rule already showed what that costs. Left to the verifier. The third
  defect this item used to carry, the Leipzig headline with no main clause
  ("Ein Film, der die Frage aufwirft, ..."), was caught a second time in
  cycle 13 and is now the deterministic rule `no_main_clause_verb`; see the
  fix log's cycle 13 section for the measurement.

- **Collocation errors are the verifier's job and cannot be a rule here.**
  Cycle 13 found five of them among the eight items the verifier caught in
  one run and missed in the next ("einen Erfolg erreichen", "Eindruck
  über", "schnaubten wegen ihres Gehalts"). Every one needs a collocation
  lexicon (which verbs take which nouns, which nouns take which
  prepositions) that this repository does not have and that no structural
  check substitutes for. Recorded so the standing "caught twice becomes a
  rule" rule is not read as applying to them.

- **Caption residue in parentheses cannot be told from journalistic
  apposition by structure.** "Masi Pfand (am Ball) befindet sich ..." is
  scraped caption furniture; "Friedrich Merz (CDU) hat ... telefoniert." is
  correct German, and the two have the identical parse. The structural rule
  (a short verbless parenthesised insert between a proper-noun subject and
  its finite verb) was measured over 40,000 Leipzig lines in cycle 13: of
  the 150 hits the module otherwise accepts, 23 are position markers and
  127 are party affiliations, ages, abbreviation glosses and goal minutes.
  Widening `_PARENTHESISED_MARKER`'s closed list lexeme by lexeme is the
  only safe direction, and only for a phrase a pilot actually turns up.

- **Leipzig carriers have no content filter.** Cycle 11 turned up a quote
  about genocide, cycle 12 a report of a sledgehammer assault. Not grammar
  defects. A blocklist applied to Leipzig only is cheap if the owner wants
  one.

- **Anything the verifier catches twice becomes a deterministic rule.** A
  standing rule, not a task. The verifier is a discovery instrument and must
  never be the only thing between a known defect class and a learner.

---

## 2. Open work

- [x] **2.1 Tense ambiguity on the modal topics. Settled by the gloss.**
  The cue names the verb, not the tense, so "(können)" left `kann`,
  `konnte` and `könnte` all open. Cycle 13 measured the cost: 44 of 105
  model rejections, 42%, named a tense or time alternative as an equally
  good answer.

  Cycle 14, same 475 candidates, with the verifier now reading the English
  gloss: **tense rejections 44 to 6, total rejections 105 to 42, accepted
  376 to 437.** The six survivors all cite the translation as their
  evidence ("Die englische Übersetzung 'are to' verlangt Präsens", "passend
  zur englischen Übersetzung ('didn't want')"), which is the check working,
  not failing. No time anchor in the carrier is needed. The recommendation
  in `docs/audits/cycle-12-corpus-report.md` is withdrawn.

- [ ] **2.1c The model verifier is not stable run to run, and that is now
  the largest open risk.** Cycle 14 diffed its accepted set against cycle
  13's on the identical 475 candidates. 64 items were newly accepted. 56 of
  those are explained by the gloss (37 were tense rejections, 19 other
  Frage 2 ambiguity). **The other 8 were rejected last run for bad GERMAN,
  which the gloss says nothing about, and accepted this run.** Every one of
  the 8 was a correct rejection last time:

  - `Masi Pfand (am Ball) befindet sich ...` scraped caption residue
  - `Sie erreichte einen großen Erfolg ...` unidiomatic collocation
  - `Ja", gesteht Norris, ...` opens mid-quotation
  - `Erst am 6. November 2021 wurde damals ...` date plus "damals"
  - `... war mein Eindruck über die jeweiligen Landsleute klar.` wrong preposition
  - `Sie schnaubten wegen ihres kleinen Gehalts.` unidiomatic
  - `... Bilder anhand der Google-Bildersuche entlarvt werden.` wrong collocation
  - `Ein Film, der die Frage aufwirft, ...` no main clause

  Two now have deterministic rules (`opens_mid_quotation`,
  `no_main_clause_verb`), five are collocation errors no rule here can
  reach (section 1), one is too narrow to rule. So the verifier remains the
  only thing between roughly six defects a cycle and a learner, and it
  agrees with itself about 92% of the time on this judgment.

  **2.3 is the experiment that bears on this**: batch size 20 versus 5, to
  see whether items late in a batch get less scrutiny. If they do, this is
  a cheap fix. If they do not, the options are asking the naturalness
  question in its own call, or asking it twice and rejecting on either no.

- [ ] **2.1b Decide whether the gloss check enforces.** Wiring done.
  `step7_corpus_pilot.py` fills `gloss_en` from the translation store,
  translating and storing whatever the store lacks. The consistency check
  (a gloss whose tense or person contradicts the answer) **runs and reports
  but does not reject**, by default; `--enforce-gloss-check` makes it real.

  **Cycle 13 measured it and the first reading was damning: 34 flags on
  376 accepted items, of which 33 were the check being wrong and 1 was a
  real defect.** Four bugs, all now fixed: English contractions were not
  read as tense marking, so "I'll be lonely" counted as present; English
  marks the subjunctive with its past forms, which a German conditional
  target treated as a contradiction; the person check demanded an English
  pronoun even where the subject is a noun, and was fooled by German 1st
  and 3rd singular being identical in Konjunktiv II; and German match
  strings were being found inside English text, so "Ministers usually
  fall" leaked the grammar term "Fall" and "the Ukraine war" leaked the
  answer "war". Re-measured after the fix: **1 flag on 376**, and it is
  the real defect.

  **A second pilot with those four fixes in place measured 3 flags on 437
  accepted items, and all 3 were again the check being wrong.** Three more
  bugs, all now fixed: impersonal `man` is grammatically 3rd singular but
  has no English pronoun counterpart (`In der Schule kann man nicht
  rauchen.` / "You can't smoke at school." demanded he/she/it), so a
  nominative `man` now widens the expected pronoun set to you/we/they and
  promotes an oblique object to subject the way impersonal `es` already
  did; an English modal carries no tense feature at all, so a German modal
  target glossed with one (`Schüler sollten nicht arbeiten ...` /
  "Students should not work ...") can be confirmed but never contradicted,
  the same treatment Konjunktiv II already gets; and the English noun
  "will" was read as the future auxiliary (`... nach Gottes Willen ...` /
  "According to God's will, ..."), now settled by asking the English
  tagger this module already loads whether every "will" in the gloss is a
  NOUN. Re-measured after those three: **0 flags on 437, and still exactly
  1 flag on 376, still the real defect.**

  Still measure-only. One flag is not enough evidence to enforce, and the
  surviving catch is lucky rather than designed: the bad Tatoeba pairing
  (`Nachdem der Vorfall an die Öffentlichkeit gekommen war ...` glossed
  `The trouble is that I don't have much money now.`) is caught only
  because the unrelated English happens to be present tense. The signal
  that actually separates it from the 33 is that it shares no proper noun
  or numeral with its German. **A content-overlap check is the one to
  build for bad pairings, and it is a different check from this one.**

  Known limit that remains: the check compares the gloss against the
  **answer's** own tense and person, so it only bites on items whose
  answer carries that morphology. A wrong gloss on an item that blanks a
  determiner or an adjective ending passes untouched. Verified on a real
  example: one carrier with a deliberately wrong past-tense gloss produced
  two items, and only the one answering a verb was caught. This caps its
  recall structurally and bears directly on 2.2.

- [ ] **2.2 Prove the verifier catches a WRONG English translation.** Owner's
  requirement, and the condition the whole gloss plan rests on. The cycle 12
  measurement hand-checked 120 Tatoeba pairs and found 2 wrong: one tense
  (`Das Kind sah aus wie aus dem Ei gepellt.` / "The child looks as neat as
  a pin.") and one determiner (`der Kuchen` / "this cake"). Both land on
  grammar the gloss is supposed to disambiguate, so the verifier reading the
  gloss against its own German sentence is what keeps a bad gloss out of the
  bank. Build the adversarial set with **deliberately wrong translations
  mixed into correct ones** (wrong tense, wrong determiner, wrong person,
  wrong polarity) and measure how many it catches. A gloss check that does
  not catch these is worse than no gloss check, because the verifier would
  then be relaxing its uniqueness judgment on evidence it never validated.

- [ ] **2.3 Get real numbers out of `scripts/eval_verifier.py`.** The
  adversarial set (38 confirmed defects, 31 confirmed clean) and the script
  both exist. They have never been run with a key, so the verifier's recall
  and false-positive rate are still unmeasured. Also test `--batch-size 5`
  against the default 20, to settle whether items late in a batch get less
  scrutiny.

- [ ] **2.4 The AI generation pilot, last.** Owner's sequencing: corpus path
  proven first, then generation for what the corpus cannot reach. On current
  evidence that is `futur_i`, `zustandspassiv_zeiten` and `futur_ii`, which
  are too rare even in 80,000 corpus sentences.

- [ ] **2.5 Decide the split and write it down.** After 2.1 and 2.4: which
  topics are corpus-sourced, which are generated, and the rule for choosing.
  That becomes the standing generation policy.

---

## 3. Owner changes that must never be overturned

Pinned by tests. Do not change without the owner saying so explicitly.

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5` in `src/llm/client.py`.
- `SERVER_ERROR_BACKOFF_SECONDS = 15.0` in `src/llm/client.py`. Applied by
  the owner after a pilot died on a 503.
- `SERVER_ERROR_MAX_RETRIES` in `src/llm/client.py`. The owner first set
  this to 5, then raised it to **40** and added a free-to-paid lane
  fallback once those retries are exhausted (roughly 10 minutes of retrying
  before the fallback fires). Seen uncommitted in his working tree on
  2026-08-23. Whatever value stands in his tree is the correct one. Do not
  restore 5, and do not remove the fallback.

Three separate cycles reverted an owner edit to this file. Both groups now
have a pinning test that says to ask rather than update the assertion.

---

## 4. Decisions the owner has made, so nobody relitigates them

- **Cue with the invariant citation form**, not a gender-agreed one. A cue
  equal to its answer is fine when the learner still had to work out case and
  gender. Verbatim: "cue being the answer is not a problem if the problem
  still requires student to identify case, declension etc."
- **Vocabulary is filtered at each topic's own CEFR level**, never one global
  ceiling. A B1 grammar topic is not restricted to A1 words.
- **Unresolved references are a property of the slot, not the sentence.**
  `Er sagte das damals nicht` is a good carrier; only a blank on `er` is bad.
- **Pilots forbid batch, not the paid lane.** Once free quota is spent they
  continue on paid, on demand.
- **Every exercise shows its English translation, always.** Owner's call,
  made on pedagogical grounds, not as a fallback for ambiguity. It follows
  that the verifier may treat the translation as available to the learner
  when judging whether an answer is unique.
- **Where a German sentence has several English translations, keep the
  shortest.** 13.1% of Tatoeba's German sentences have more than one. A
  short translation is the more literal one, and literal is what maps word
  to word for a learner; a long one paraphrases, and paraphrase is where
  tense and determiners drift.

---

## 5. Planned features, specified but not started

Not defects. Recorded here so the spec is not lost between sessions.

### 5.1 English translation on every exercise

Decided (section 4). Three parts, in order:

1. **Fetch Tatoeba's German-English export** and measure translation
   accuracy on a hand-checked sample. The export does not mark INDIRECT
   translations (German to X to English), which drift, so the sample must
   count those specifically. This number decides whether the rest is worth
   building.
2. **Translate the rest** with a dedicated translation API, not an LLM.
   Built and run once. `scripts/build_translations.py`, backed by
   `src/llm/translation.py` (Azure Translator F0 primary, Gemini fallback),
   every call logged through `src/llm/client.py` per CLAUDE.md rule 4.

   **First run, 2026-08-24, and what it settled.** 450,490 distinct
   carriers were read. Tatoeba's own pairs glossed 200,555 of them at zero
   cost, in one pass, permanently. 1,000 more were machine translated.
   248,935 were left.

   Those 248,935 are about 17,000,000 characters, which is roughly eight
   and a half months of the free tier. That kills the whole-corpus
   backfill: the earlier "six weeks" estimate assumed only the
   carrier-valid subset, and carrier validation only removes about 30%
   (55,939 of 80,000 in cycle 12), so it does not rescue the number.

   **The fix is to translate per build, not per corpus.** Measured against
   cycle 12's own 396 accepted items: 230 of their carriers already have a
   Tatoeba gloss, so a whole pilot needs 166 new translations, about 11,000
   characters, one run. `--carriers-from` on `build_translations.py` is
   that mode. The whole-corpus mode stays for feature 5.3, which does want
   many corpus sentences glossed, and the free Tatoeba 200,555 already
   covers 5.3 without another paid character.

3. **Show the translation in the app.** Done. It renders under the German
   sentence, before the learner answers and still visible after grading,
   and an item without a gloss renders nothing at all.

   Building it found that `gloss_en` was being dropped TWICE on the way to
   the browser: the `items` table had no `gloss_en` column, so any item
   carrying one lost it on insert, and `EXPORTED_BANK_ITEM_FIELDS` is an
   explicit allowlist that filtered it out of the bundle as well. Fixed by
   migration v4 plus the allowlist entry. Nothing in `data/bank.db` has a
   glossed row yet, because the pilot writes items to a review JSONL and
   not to the bank; the export will carry them once a glossed pilot is
   banked.

4. **Give the verifier the gloss.** Done, and licensed by step 3 landing
   first: relaxing against information the learner never gets would
   manufacture the non-unique-answer defect class cycles 11 and 12 closed.
   The item block now carries `Englische Übersetzung:` (or `(keine)`), and
   question 2 says an alternative the translation rules out is not a
   second correct answer.

   The relaxation is bounded in the instruction itself, in as many words:
   a translation settles **tense, person, number and definiteness** only.
   It says nothing about German case, gender, adjective endings, reflexive
   pronouns or preposition government, and must never rule an alternative
   out on those. Those are the majority of the gates and stay untouched.

   Unmeasured until the next pilot. Expected recovery is most of the 44
   tense-ambiguity rejections in item 2.1. If accepted items rise and no
   new wrong-answer defect appears in the audit, this is settled; if a
   wrong answer does appear, the bound above is where to look first.

### 5.2 Vocabulary FSRS

Owner's spec, verbatim in substance:

- **Identical in shape to the grammar trainer.** One or two sentences
  depending on the word, the tracked word is the missing token, no cue,
  just the English translation.
- **Multi-word units are the one extension asked for**: separable verbs,
  reflexive verbs, and verb-plus-preposition phrases (`warten auf`,
  `sich interessieren für`).
- **The unit must be taught, not memorised as a string.** `warten auf` has
  to be recognised and presented as `wartet auf`, `wartete auf`, `warte
  ... auf` and so on. A fixed-string match is explicitly not what is
  wanted. The lemma-plus-preposition pair is the unit; the surface form
  varies.

### 5.3 Click a word to see it in context

Replaces an earlier hover-for-a-word-gloss idea, which the owner withdrew
after it turned out to need a German-English dictionary we do not have.

Clicking a word in an exercise shows **several sentences from our own
corpus containing that word, each with its English translation**. The
learner reads the word in context, works out the meaning, and decides
whether to add it to the vocabulary list.

Needs no dictionary at all. It reuses the corpus and the translations 5.1
already produces, plus the lemmatiser we already have to match inflected
forms back to one word. Blocked on 5.1 only.
