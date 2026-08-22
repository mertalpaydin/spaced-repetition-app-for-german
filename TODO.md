# TODO

Open work only. Closed work is in `docs/audits/fix-log.md` with its reasoning
and measurements; git history has the same in commit bodies.

Rule for anyone adding here, human or agent: **an item is a thing that is not
done.** When you close one, delete it and put the writeup in the fix log. This
file was allowed to reach 1,772 lines of finished work and was useless as a
work list. Do not do that again.

---

## 1. Six defects found while hand-checking the 8.1 survivors

Found in `kasus_dativ_formen`, `verben_reflexiv_akk` and `verben_reflexiv_dat`
during the corpus lexicon work. Not caused by that fix, but live, and they
would ship. Each was left out of scope at the time and is now on the list.

Three are our own helpers:

- [ ] **1.1 Object lookup does not skip a temporal phrase.**
  `Er kauft sich jeden Abend eine Flasche Bier ...`
  `_immediately_followed_by_object_np` looks for the object directly after the
  reflexive and finds `jeden Abend` instead of `eine Flasche Bier`. The
  temporal-accusative exclusion already exists elsewhere in the codebase
  (`paradigms.TEMPORAL_ANCHOR_LEMMAS`, used by the reflexive routing fix);
  reuse it rather than writing a second one.

- [ ] **1.2 Preposition walk-back stops at a coordinating conjunction.**
  `Ob es sich um ein und dasselbe Tier handelt ...`
  `_governed_by_adposition` stops at the `und` inside `ein und dasselbe`
  rather than continuing to `um`.

- [ ] **1.3 Preposition walk-back cannot cross a multi-token proper name.**
  `Hier setzt er sich ... gegen Fatih Celiksoy durch.`
  Same helper, different failure. Confirmed present in both the before and
  after samples, so it predates the lexicon work.

Three are the tagger, and the first two matter beyond their own items:

- [ ] **1.4 Second person in an inverted question tagged as first person.**
  `Kannst du mich ...?` -> `Kannst` tagged `Person=1`.

- [ ] **1.5 Same, on a vowel-change verb.**
  `So also vergiltst du mir ...!` -> `vergiltst` tagged `Person=1`.

  **1.4 and 1.5 expose a hole in our own measurement.**
  `docs/audits/tagger-accuracy-vs-gold.md` sampled UD_German-HDT, which is
  news prose and almost entirely declarative. Its 12.59 percent Mood and 6.08
  percent Case conflict rates therefore say nothing about questions,
  imperatives or second person, which is precisely the register a learner app
  is full of. Re-measure against a treebank or corpus slice that actually
  contains questions before trusting those numbers for this pipeline.

- [ ] **1.6 Determiner-less plural dative tagged accusative.**
  `Der Körper passt sich ... Temperaturänderungen an.`

---

## 2. Known limits, recorded rather than solved

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
  lexicon. A real coverage gap, not a threshold artefact.

- **Anything the verifier catches twice becomes a deterministic rule.** A
  standing rule, not a task. The verifier is a discovery instrument and must
  never be the only thing between a known defect class and a learner.

---

## 3. Open work

- [ ] **3.1 Re-run the corpus pilot and audit it.** Confirms the section 8
  fixes against a fresh sample. Command is in the session; outputs are
  `data/corpus_pilot_review.jsonl`, `_rejected.jsonl` and `_report.json`.

- [ ] **3.2 Get real numbers out of `scripts/eval_verifier.py`.** The
  adversarial set (38 confirmed defects, 31 confirmed clean) and the script
  both exist. They have never been run with a key, so the verifier's recall
  and false-positive rate are still unmeasured. Also test `--batch-size 5`
  against the default 20, to settle whether items late in a batch get less
  scrutiny.

- [ ] **3.3 The AI generation pilot, last.** Owner's sequencing: corpus path
  proven first, then generation for what the corpus cannot reach. On current
  evidence that is `futur_i`, `zustandspassiv_zeiten` and `futur_ii`, which
  are too rare even in 80,000 corpus sentences.

- [ ] **3.4 Decide the split and write it down.** After 3.1 and 3.3: which
  topics are corpus-sourced, which are generated, and the rule for choosing.
  That becomes the standing generation policy.

---

## 4. Owner changes that must never be overturned

Pinned by tests. Do not change without the owner saying so explicitly.

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5` in `src/llm/client.py`.
- `SERVER_ERROR_BACKOFF_SECONDS = 15.0`, `SERVER_ERROR_MAX_RETRIES = 5` in
  `src/llm/client.py`. Applied by the owner after a pilot died on a 503.

Three separate cycles reverted an owner edit to this file. Both groups now
have a pinning test that says to ask rather than update the assertion.

---

## 5. Decisions the owner has made, so nobody relitigates them

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
