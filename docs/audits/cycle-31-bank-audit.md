# Cycle 31 bank audit, 8 September 2026

Eight Claude agents (one per shard of ~128 items) read every one of the
1,021 items banked on 2026-09-08 by the two-pass phase B, with the brief in
the fix-log. Claude only, so this is a same-vendor check of a Gemini
verifier; CLAUDE.md section 10 still wants a Gemini-side pass before the
number is treated as ground truth. Defects are what an agent was confident
about; "minor" notes (register, idiom in the gloss, punctuation) are not
counted.

## Result

| | count | rate |
|---|---|---|
| audited | 1,021 | |
| defective | 38 | 3.7% |
| of which HIGH (learner taught something wrong, or marked wrong for a correct answer) | 5 | 0.5% |
| minor notes | 36 | |

By category:

| category | count | what it means for the learner |
|---|---|---|
| 5 wrong topic (gap does not test the topic_id) | 29 | The form is right and the learner is graded right, but FSRS credits the wrong topic. Most are case syncretism (a feminine or plural form tagged nominative when the gap is accusative or dative), Futur I tagged as passive or Futur II, plain "war" tagged Zustandspassiv, Konjunktiv II irreal tagged as politeness, "mit"/"weh"/"außer" particles tagged infinitiv_mit_zu. |
| 2 missing answer | 5 | A correct fill is marked wrong: "unseren" after trotz, "war" for "bin" in a past narrative, "sei" after als, "soll" for "sollte", "etwas" for "weh". |
| 4 wrong gloss | 2 | "ins Auge gehen" glossed as eye-catching; "den ersten Zug nehmen" glossed as a puff. |
| 1, 3, 6 | 0 | No wrong accepted answer, no topic leak, no unanswerable item. |

**Zero defects is not reached.** Rule 2 makes the model verifier blind to the
topic attribution on purpose, so 29 of the 38 are exactly the class it cannot
see: the tagger's, not the verifier's. The verifier's own classes (wrong
answer, missing answer, wrong gloss) came out at 7 of 1,021, 0.7%.

## HIGH

- corpus_453c651ba76be410: "Tom würde dir niemals ___ tun." accepts only "weh"; "etwas" fits the gloss "hurt you" equally, and nothing here is infinitiv_mit_zu.
- corpus_3b59d27cb6516843: "Gemäß ___ KorrAI-Vereinbarung" is dative (gemäß + Dativ) tagged praepositionen_genitiv_gehoben; "der" is right only because feminine dative and genitive coincide, so the item teaches a wrong case rule.
- corpus_f557805d398a9646: "Ich ___ meine Augen geschlossen halten" is Futur I tagged passiv_praesens; no passive exists in the sentence.
- corpus_322e960355290731: "Tun wir so, als ___ das nicht passiert!" accepts only "wäre"; "sei" is fully correct and common.
- corpus_9579b421e9a966d1: "Parallel dazu ___ die technische Vorbereitung erfolgen." accepts only "sollte"; the gloss "should take place" invites "soll".

## MEDIUM, by id

Wrong topic: corpus_2b7c07d07e6882de, corpus_a5a1b610af6a6d2c, corpus_d7baebd5db417cce, corpus_679e5ad20044d3a1, corpus_c1e44ead69605125, corpus_8a130b1d160f0775, corpus_258b798fe9d0436f, corpus_65499cdcd38a3545, corpus_4ca84c5b942abb42, corpus_abcb713f99847fb7, corpus_f402bef649f35370, corpus_f2677ac606b30b4c, corpus_4583d21fcbc1814f, corpus_6980c689a6db79e5, corpus_8864cc3f8a54805c, corpus_64a4ff94b6f618f7, corpus_58d5318d3dbff724, corpus_0026445633994e9e, corpus_252dd757dc1b7ab0, corpus_4def8a3857a67216, corpus_13b3bce1fe9ee85f, corpus_ce6609f53d97bc59, corpus_1f97c90647f4a253, corpus_85e666dcb95c3d1d, corpus_bca21a447f3ae325, corpus_1eb9f0c25fa927da, corpus_274a7f8f9d1c64a8, corpus_3a838e27c67fe861, corpus_6902477b95bb3f38.

Missing answer: corpus_f11c13f7c05434d7 (unseren), corpus_91cbe363dc911b1b (war).

Wrong gloss: corpus_9e9bc22628a226ae, corpus_3c09b59f3c9a2b7c.

## What to do with it

Nothing has been removed from the bank; that is the owner's call. The three
levers, in order of yield: (1) a deterministic tag check for the recurring
syncretism cases (a feminine or plural determiner tagged nominative when the
verb governs another case; "werden + Infinitiv" is never passive; "war"
without a Partizip II is never Zustandspassiv), which would have caught most
of the 29; (2) answer-set expansion for the five missing answers, all of
which are classic alternations (trotz + Dativ, als + Konjunktiv I,
soll/sollte, Perfekt/Plusquamperfekt); (3) a Gemini-side repeat of this audit
before the rate is quoted as measured.
