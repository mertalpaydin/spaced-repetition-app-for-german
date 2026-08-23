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

- **Leipzig carries scraped-web artefacts that carrier validation does not
  see**: a caption glued to a headline, a page heading glued to body text.
  Four of 337 in cycle 11, all sharing a colon. Options and their measured
  costs are in `docs/audits/cycle-11-corpus-report.md` section 4; no rule
  is applied yet because the obvious one drops a good sentence.

- **Anything the verifier catches twice becomes a deterministic rule.** A
  standing rule, not a task. The verifier is a discovery instrument and must
  never be the only thing between a known defect class and a learner.

---

## 2. Open work

- [ ] **2.1 Decide the three open questions from the cycle 11 audit.** Each
  has measured options in `docs/audits/cycle-11-corpus-report.md`; none can
  be picked without the owner.
  - **Auxiliary cue.** Cued topics accept at 85%, uncued at 53%, and all
    twelve topics below 60% are uncued. Give `(werden)`/`(sein)`/`(haben)`
    to the auxiliary topics, as `artikel_bestimmt_nom` already has.
  - **Pronoun uniqueness.** 9 of 17 items in the two topics have more than
    one correct answer. Anchor gate for `_nom`, nominative cue for `_akk`
    and `_dat`. Recommendation is both.
  - **CEFR unknown-word policy.** The ceiling does not examine 36% of
    accepted items. The frequency fallback costs 20.8% of supply.

- [ ] **2.2 Get real numbers out of `scripts/eval_verifier.py`.** The
  adversarial set (38 confirmed defects, 31 confirmed clean) and the script
  both exist. They have never been run with a key, so the verifier's recall
  and false-positive rate are still unmeasured. Also test `--batch-size 5`
  against the default 20, to settle whether items late in a batch get less
  scrutiny.

- [ ] **2.3 The AI generation pilot, last.** Owner's sequencing: corpus path
  proven first, then generation for what the corpus cannot reach. On current
  evidence that is `futur_i`, `zustandspassiv_zeiten` and `futur_ii`, which
  are too rare even in 80,000 corpus sentences.

- [ ] **2.4 Decide the split and write it down.** After 2.1 and 2.3: which
  topics are corpus-sourced, which are generated, and the rule for choosing.
  That becomes the standing generation policy.

---

## 3. Owner changes that must never be overturned

Pinned by tests. Do not change without the owner saying so explicitly.

- `RPM_MAX_RETRIES = 5`, `FREE_LANE_MAX_CONCURRENCY = 4`,
  `FREE_LANE_RATE_LIMIT_PER_MINUTE = 5` in `src/llm/client.py`.
- `SERVER_ERROR_BACKOFF_SECONDS = 15.0`, `SERVER_ERROR_MAX_RETRIES = 5` in
  `src/llm/client.py`. Applied by the owner after a pilot died on a 503.

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
