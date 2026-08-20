"""Carrier soundness validation: is the model-generated CARRIER sentence
itself correct German, before anything is blanked from it.

Every other check in this package (``selectors``, ``blanker``, ``paradigms``)
answers "is the removed token a computable instance of this topic", which
makes the ANSWER hallucination-proof -- it is derived from a closed paradigm,
never asserted by a model. Nothing upstream of this module checked whether
the SENTENCE THE ANSWER WAS TAKEN FROM is itself grammatical. A pilot run
generated "Auf dem Weg kauft ich im Supermarkt frisches Gemüse und Milch
ein." -- "kauft ich" is wrong; "ich" takes "kaufe" -- and that one bad
carrier produced two accepted items, one of which blanked the very verb that
was wrong. This module is the fix: it runs on a plain generated sentence,
before ``sentence_tagger.tag_sentence`` and ``selectors`` ever see it, and
discards anything that is not sound German, counting why.

## What is checked, and how honestly it is checked

Three real, mechanically-checkable properties, each backed by the
dependency parse (not a proxy for anything semantic):

1. **Subject-verb agreement.** A nominative subject (``sb``) or expletive
   subject (``ep`` -- "es regnet") must agree with its finite verb in Person
   and Number. This is read off the dependency arc, not word order, so it is
   correct for both "Ich komme" (SVO) and "Weil ich komme, ..." (subject
   after the conjunction). This is the exact check that catches "kauft ich".

2. **Exactly one finite verb per clause, and at least one finite verb
   present.** A sentence with zero finite verbs is a fragment. A finite verb
   that is not the sentence's ROOT and whose head is *itself* a finite verb
   is only a legitimate second clause (subordinate: "..., dass er kommt") if
   it carries an explicit subordinating conjunction (a ``cp`` child) --
   coordinated main clauses ("... und du kommst") and relative clauses
   ("..., der dort steht") never trigger this path at all, because they
   attach to the coordinating conjunction or the antecedent noun
   respectively, never directly to another finite verb. A finite verb
   attached straight to another finite verb with no subordinator is
   indistinguishable from a run-on / a missing conjunction, so it is
   rejected.

3. **Sentence completeness.** Capitalised start, terminal punctuation, at
   least a handful of tokens, and the sentence does not end on a token whose
   own tag demands a continuation (a bare article, preposition or
   conjunction) -- a dangling fragment a generation call got cut off on.

## What is a real check versus a documented proxy

Every rejection below is conservative by design (CLAUDE.md: discarding a
good sentence is cheap, keeping a bad one is expensive), which means several
checks knowingly reject sentences that are actually fine German, because
this module cannot tell the difference confidently enough to accept them:

* **Coordinated subjects** ("Der Mann und die Frau tanzen") are detected
  (a ``cd`` child on the subject noun) and rejected as
  ``agreement_undecidable`` rather than resolved -- German coordinate-subject
  person resolution ("du und ich" takes "wir"-agreement, "du und er" takes
  "ihr"-agreement) is a real rule this module does not implement.
* **Subjectless dative-experiencer constructions** ("Mir ist kalt.") have no
  ``sb``/``ep`` child at all and are rejected as ``no_subject_found``, even
  though they are perfectly grammatical -- there is nothing to check
  agreement against, and a genuinely subject-dropped error looks identical
  from here.
* **Bare informal imperatives** ("Geh nach Hause!") are rejected, but not by
  design: verified empirically that ``de_core_news_sm`` systematically
  mistags a subjectless second-person imperative as a noun (``NN``/``PROPN``
  ROOT, not ``VVFIN``), so it never reaches the finite-verb check at all and
  is discarded as ``no_finite_verb``. Formal imperatives with an explicit
  "Sie" ("Kommen Sie bitte her.") are unaffected -- they have a real subject
  and tag correctly.
* **Person/Number syncretism tolerance.** German verb morphology has four
  real, categorical surface ambiguities where two different (Person, Number)
  cells always share one form: 1st-plural/3rd-plural in every tense ("wir
  machen"/"sie machen"), 1st-singular/3rd-singular in the preterite and
  Konjunktiv II ("ich sah"/"er sah", "ich hätte"/"er hätte"), 2nd-plural/
  3rd-singular in the present tense ("ihr macht"/"er macht"), and 2nd-/
  3rd-singular present for sibilant-stem verbs ("du vergisst"/"er
  vergisst"). ``_is_syncretism_tolerated`` treats all four as agreement
  rather than disagreement -- verified empirically for the 2nd-plural case
  that ``de_core_news_sm``'s morphologizer resolves the ambiguity toward
  3rd-singular regardless of the actual subject (every "ihr <verb>t"
  sentence tested tags Person=3/Number=Sing; "ihr habt"/"ihr seid", which
  are NOT syncretic, tag correctly as Person=2/Plur), and the other three
  are the same shape of fact even where this module has not separately
  logged a verification run for every lexeme. The known cost: a genuine
  substitution of the wrong cell within a tolerated pair (e.g. a
  3rd-singular ablauted form used where the syncretic-but-different
  2nd-plural form was meant on a strong verb) is surface-identical to the
  tolerated case and will not be caught.

Two things that are checks, not proxies, worth calling out because they
look like they might be gaps: **subject-gapped coordination** ("Ein Mann
steht vor der Tür und wartet." -- one subject, two coordinated verbs) is
resolved, not rejected: the second conjunct's agreement is checked against
the first conjunct's real subject (``_resolve_subject``), not reported as
"no subject found". And **"man"/"jemand"/other indefinite pronoun
subjects** default to 3rd person the same way a noun subject does (they are
3rd person by definition in German, regardless of notional number), so
"Während der Prüfung darf man nicht sprechen." is accepted, not discarded
for lacking a Person feature.

Semantic incoherence (a sentence that parses and agrees but makes no sense)
is explicitly out of scope -- not mechanically checkable, per the task that
commissioned this module.

`docs/audits/cycle-04-report.md` found two error classes the checks above
never looked at, plus one seen once. All three are real, mechanically-checked
properties, not semantic proxies -- but each has an honestly-scoped limit,
recorded here rather than left implicit:

4. **Attributive adjective declension after its determiner.** An attributive
   adjective's ending is fixed by what precedes it: weak after a definite
   article, mixed after an ein-word (indefinite article, negation, or
   possessive), strong after a quantifier such as "viele"/"einige"/
   "mehrere"/"wenige" or after nothing at all. The three ending tables
   (`paradigms.ADJ_ENDING_BY_CELL`) are reused, not duplicated, exactly as
   this package's own convention requires.

   The hard part is Case. "die" is nominative-or-accusative, singular
   feminine or plural-any-gender; "der" is masculine nominative, feminine
   dative-or-genitive, or genitive plural -- and a determiner never carries
   enough information on its own to pick one reading. This check never
   guesses: it reads the head noun's own Gender and Number off spaCy (both
   are lexical facts about the noun, not context-dependent the way Case is,
   so they are trustworthy here the same way Number already is for
   subject-verb agreement), enumerates EVERY Case reading consistent with
   the determiner's own surface form under that Gender/Number, and only
   rejects when the adjective's actual ending matches NONE of them --
   impossible under every reading, never merely improbable under the most
   likely one. Where the determiner's own surface form cannot be classified
   at all (an unrecognised word, or an ein-word ending the paradigm has no
   plural Nominative/Accusative row for -- see `paradigms.py`'s own
   docstring on that exact gap), the check is skipped for that noun phrase
   rather than guessed. Deliberately narrow scope, to keep false positives
   at zero: only `ART` (definite and indefinite article), `PPOSAT`
   (possessive), and `PIAT` (the closed quantifier set, plus `kein`-family
   words, which `de_core_news_sm` also tags `PIAT`) are read as determiners;
   a demonstrative (`dieser`), `jeder`/`manche` (`PIDAT`), or a determiner
   fused into a preposition ("im", "zum") are not recognised at all, so an
   adjective after one of those falls through to the "no determiner found"
   branch, which checks against all four cases and is correspondingly
   looser (it still catches a wrong DECLENSION family, e.g. a weak "-en"
   ending where every strong reading needs "-er"/"-es"/"-em", just not
   every wrong CASE within the strong paradigm). The adjective's own ending
   is read off its literal surface suffix, not off spaCy's morphology:
   verified empirically that the morphologizer assigns a wrong attributive
   adjective ("nassen" where "nasse" was needed) the SAME Case/Gender/Number
   features as the correct form would have gotten, because it infers those
   features from the surrounding noun phrase rather than from the
   adjective's own ending -- exactly the case this check exists to catch,
   so trusting that feature would silently defeat the check.

5. **`dass` written where `das` belongs.** `dass` (`KOUS`, introducing a
   clause attached with a `cp` dependency) can never itself fill a
   grammatical role inside its own clause; every argument slot the clause's
   verb needs must be filled from words already inside the clause. `das`
   used as a relative pronoun is different: it IS one of those arguments,
   referring back to an antecedent outside the clause. The reliable signal
   is a gap: a clause introduced by `dass` whose verb is missing an argument
   it structurally needs is exactly the shape a wrongly-typed `dass` leaves
   behind, because the relative pronoun that should have filled that slot
   is gone.

   **This catches exactly one direction, and only for a closed, two-verb
   list.** Knowing which verbs need which argument (transitivity/valency)
   is not something spaCy's dependency labels give for free, and German
   verbs are unusually promiscuous about dropping objects when the context
   allows it ("Ich lese." is fine; "Ich esse." is fine) -- a general
   "verb X normally takes an object" rule would misfire constantly against
   ordinary, correct German. So this checks only `kaufen` and `schenken`,
   picked because the audit's own example uses one of them and both are
   about as close to obligatorily transitive as German verbs get in
   ordinary written prose. A `dass`-clause whose content verb (walking down
   any auxiliary/modal `oc` chain to find it, so "..., dass sie es gekauft
   hatten" is checked on "gekauft", not "hatten") lemmatises to one of
   those two AND has no accusative object (`oa`/`oa2`) child is rejected.
   A verb carrying its own separable-prefix particle (`svp`, e.g.
   "einkaufen" split as "kauft ... ein") is excluded first: it is a
   different verb with different valency, not a transitivity gap in
   "kaufen" itself. **The reverse error -- `das` written where a real
   `dass`-complement clause was meant -- is NOT caught.** Confirming that
   direction needs knowing which verbs take a sentential complement at all
   (`glauben`, `wissen`, `sagen`, `hoffen`, ... an open, much larger lexical
   class than "which two verbs are obligatorily transitive"), which this
   module does not attempt to enumerate.

6. **Swiss `ss` for standard `ß`.** Seen once (`heisse` for `heiße`). At the
   time this section was first written, the general rule -- `ß` after a
   long vowel or diphthong, `ss` after a short one -- looked like exactly
   the kind of thing this module refuses to guess at without a word list:
   "Fluss", "dass", and "muss" are correct with `ss` precisely because
   their vowel is short, and nothing in the surface spelling of a single
   vowel LETTER reliably says whether German pronounces it long or short
   (contrast "Fluss" short vs. "Fuß" long, spelled with the same single
   letter "u"). **docs/audits/cycle-09-report.md 1.4 revisits this and
   ships the DIPHTHONG half of the rule generally** (see
   `_SWISS_DIPHTHONG_SS_PATTERN`'s own comment for the empirical dictionary
   check behind it): a diphthong ("ei"/"eu"/"äu"/"ie") is two vowel LETTERS
   forming one sound, always long by definition, so the ambiguity that
   blocks a general rule for a single vowel letter does not apply to a
   diphthong at all. The long-vowel-single-LETTER half of the rule
   ("groß", "Straße", "Fuß", "Maß", "Spaß") still has no general answer for
   the reason above, and stays a closed list -- what was originally
   implemented here is that closed list's first, narrowest member: a fixed
   set of literal Swiss-spelled surface forms of exactly one verb, "heißen"
   ("heisse", "heisst", "heissen", "heissend", "hiess", "hiessen",
   "geheissen"), matched on the raw sentence text before spaCy ever sees
   it. "heißen" always takes `ß` in every standard-German form regardless
   of context, so there is no ambiguity to misjudge for this one closed
   list -- section 11 below has the general diphthong rule and the rest of
   the long-vowel closed list this cycle adds.

## Cycle 6 additions, and one investigated but NOT added

`docs/audits/cycle-06-report.md` found three more classes. Two are new
checks (7, 8 below); the third (the `treue`/`treffe` item, report class G)
is a diagnosis with NO new check attached, and section "Cycle 6: the
`treue` hole" below explains why at length rather than shipping something
that either does not close the reported item or breaks the zero-false-
positive regression trying to.

7. **Swiss `ss` for standard `ß`, extended to `groß`.** The report proposed
   validating every `ss` token against the vendored dictionary
   (`data/fixtures/corpus/frequency/de_dictionary_filter.txt`, 37,567
   entries, CC0) instead of hand-listing forms: reject a token whose literal
   text is absent from the dictionary but whose `ß`-substituted spelling is
   present. **Verified against the actual file before relying on it, per
   the task's own instruction, and it cannot support that test.** The file
   was built by writing each retained entry through
   `src.lexicon.lemmatizer.normalise` (see
   `src/lexicon/frequency.py:load_dictionary_filter` and the file's own
   `PROVENANCE.md`), which maps `ß` to `ss` -- so the file was normalised
   not just for the membership TEST used to build it, but in the bytes it
   actually stores. Empirically: the file contains zero `ß` characters in
   37,567 entries despite containing 6,316 entries with umlauts, and
   `gross`/`weiss`/`fuss`/`strasse` are present while `groß`/`weiß`/`fuß`/
   `straße` -- unambiguously real, extremely common German words that a
   genuine 37k-entry wordlist would certainly contain -- are absent. Every
   `ß` the source wordlist ever had was already collapsed to `ss` before
   this file was committed. A lookup against it (or through
   `FrequencyBander.load_dictionary_filter`, which normalises again on
   load, compounding the same loss) cannot tell "grossen" and "großen"
   apart: both normalise to the same stored key, so the proposed test would
   either never fire (checked literally, as specified) or, if implemented
   with case/eszett-blind normalisation on both sides, fire on nothing
   because there is no `ß`-bearing key left anywhere in the file to find.
   **This is a real, reportable data-quality finding: the vendoring for the
   B2-vocabulary task last cycle discarded exactly the information this
   cycle's task needs, since the frequency-banding use case never needed
   orthography preserved.** Fixing it properly means re-deriving the filter
   file from the upstream `enz/german-wordlist` source with normalisation
   applied only to the membership test, not to the stored bytes -- out of
   scope here (this module owns carrier validation, not corpus vendoring),
   and flagged for whoever next touches that fixture.

   The fallback actually shipped is the same closed-list technique already
   proven for `heißen` (section 6 below), extended by ONE lexeme: `groß`,
   the specific word both audited items used. `_SWISS_GROSS_PATTERN`
   matches the six surface forms `gross`/`grosse`/`grossem`/`grossen`/
   `grosser`/`grosses` (predicative plus all four attributive endings).
   Unlike the `heißen` pattern, this one is matched CASE-SENSITIVELY,
   lowercase only, deliberately: "Gross" and "Grosser" are attested German
   surnames, and matching them case-insensitively (as the `heißen` pattern
   does, safely, because "Heißen" is not a surname anyone actually has)
   would risk rejecting a sentence that correctly names someone. Attributive
   and predicative uses of the adjective are always lowercase, so this loses
   only the rare sentence-initial capitalised use ("Grosse Sorgfalt ist
   nötig...") -- an acceptable, deliberate narrowing given CLAUDE.md's own
   cost calculus (discarding a good sentence is cheap; a false accusation
   against a real surname is a different, worse kind of expensive: it is
   not just a lost sentence, it is a wrong answer to a learner if it ever
   reached the cue-validation path instead of only this pre-blank stage).

8. **`dass` where `damit`/`weil`/`wenn` belongs.** A genuine content clause
   ("Er sagt, dass er kommt") is licensed by its MATRIX predicate -- a verb
   of saying, knowing, hoping, or informing. A matrix predicate of physical
   action never licenses one; a `dass`-clause hanging off "kaufen" or
   "helfen" is a purpose/causal clause the model wrote with the wrong
   connective. The check walks from the `dass` token's own clause verb
   (`kous.head`) up at most two hops -- skipping one intervening object noun
   the clause sometimes attaches to instead of the verb directly (a real,
   observed parser quirk: "..., dass X" attaches to the preceding NP for
   "Sie kaufen ein neues Smartphone, dass ... ist" rather than to "kaufen"
   itself) -- to the matrix predicate, and rejects only when that predicate's
   lemma is one of a small closed set confirmed to be obligatorily
   transitive, physical, and non-communicative: `kaufen`, `schenken` (the
   same two already vetted for section 5's check, for the same reason),
   `helfen`, and the separable compound `herunterladen` (`laden` + an
   `svp` child literally "herunter", checked narrowly so plain "laden" or a
   differently-prefixed compound is not touched).

   **Deliberately a closed BLOCKLIST, not a whitelist of licensing verbs,
   and deliberately small -- biased toward missing errors over raising
   false alarms, per the task's own instruction.** A whitelist ("flag any
   matrix verb not in the list of saying/knowing verbs") would need to
   enumerate an open lexical class correctly to avoid false positives on
   every verb an author forgot to list; getting that list wrong in either
   direction is easy. A blocklist can only ever under-fire: an unlisted
   physical-action verb before a wrongly-typed `dass` is simply missed,
   which is the safe failure direction this task asked for, while a
   genuine communication verb is NEVER at risk of being flagged merely for
   being unlisted. Verified against the audit's two good-content-clause
   examples specifically because they use a verb ("schicken") that looks
   superficially similar to the disallowed set (also a transfer-of-object
   verb): "Sie haben der Reiseleitung eine Nachricht geschickt, dass der
   Bus pünktlich angekommen ist" and "Wir schicken dem Hotelier eine
   Nachricht, dass wir am Abend ankommen" both resolve their matrix
   predicate to "schicken", which is not on the blocklist, so both accept
   -- "eine Nachricht schicken" IS a communication act, and this check does
   not need to know that specially; it only needed to not guess wrong about
   a verb it was never told is disallowed.

   **Known gap, left uncaught on purpose:** the report's fourth example,
   "Es wäre sehr hilfreich, dass Sie dem Reiseleiter Ihre Wünsche
   mitteilen" (should be "wenn"), has a different shape entirely -- an
   extraposed "es" subject with a predicate ADJECTIVE ("hilfreich"), not a
   matrix VERB, and the wrongness is a Konjunktiv-II-versus-indicative mood
   mismatch between the two clauses, not a transitivity gap. The head-walk
   here reaches "wäre" (the copula, "sein"), which is not and should not be
   on a physical-action blocklist -- there is no verb here to blocklist at
   all. Catching this shape needs a different check (predicate-adjective
   licensing plus a mood-agreement rule across the clause boundary), which
   this task's matrix-predicate-list design does not cover and this cycle
   does not add.

9. **Cycle 6: the `treue` hole -- diagnosed, not special-cased, and no new
   check follows from it.** The report's own carrier: "Nach der Arbeit
   treue ich mich mit Lisa auf einen Kaffee in der Stadt." ("treffe" was
   meant). The report asked whether the agreement check -- section 1 above,
   the module's strongest -- has a hole that let a subject with no
   agreeing verb through, since "no finite verb agrees with ich" was the
   human read of the sentence.

   **What the tagger actually does, verified empirically (not assumed):**
   `de_core_news_sm` parses "treue" as `VVFIN`, `pos_="VERB"`, `dep_="ROOT"`
   -- a finite verb, present, with `Person=1, Number=Sing` -- which is
   EXACTLY what "ich" needs. The agreement check runs, finds a resolved
   subject ("ich", real Person/Number features, not a default), reads the
   verb's own declared Person/Number, and they match. It is not a bug in
   the comparison: both sides of that comparison are read faithfully off
   spaCy, exactly as documented in section 1, and section 1's own
   comparison logic is correct on the features it was given. **The hole is
   one level down: nothing anywhere validates that "treuen" (the lemma
   spaCy invented for this token) is a real German verb before trusting
   its morphology.** It is not one -- the real verb is "treffen" -- but
   `de_core_news_sm`'s morphologizer, given an out-of-vocabulary token
   shaped like a regular ("weak") conjugation, assigns Person/Number from
   the token's own suffix shape almost independently of whether the lemma
   exists: "treue" (ending "-e") reliably gets `Person=1, Number=Sing`
   regardless of subject (confirmed: the same suffix on "wir treuen" gets
   `Person=1, Number=Plur`, matching "wir" too) BECAUSE "-e" genuinely is
   the correct 1st-singular-present ending for ANY regular verb, real or
   invented -- weak conjugation is fully regular precisely in this cell.
   **Confirmed this is not limited to "ich"/"-e": it is not a universal
   "verb always agrees" hole either.** "du treust" -- also OOV, also
   weak-conjugation-shaped -- gets tagged `Person=1, Number=Sing` (a
   genuine tagger error, since 2nd person "-st" should be `Person=2`), and
   because that does NOT match "du" (`Person=2`), the EXISTING check
   already rejects it as `REASON_SUBJECT_VERB_DISAGREEMENT` with no changes
   needed. The hole specifically, and only, manifests when a fabricated
   verb's ending happens to be the grammatically correct one for its own
   adjacent subject -- which is the likely shape of exactly the kind of
   error a language model makes (it reliably gets conjugational endings
   right; picking the right STEM for a less common verb is where it
   slips), so this is a real, non-trivial gap, not a curiosity.

   **Closing it fully needs knowing whether a verb's LEMMA is real, which
   needs a POS-aware verb lexicon this repository does not have -- but a
   partial, verified-safe mitigation IS shipped (section 10 below), because
   it closes real cases beyond this one even though it does not close this
   exact reported item, and it was measured, not assumed, to add zero
   false positives before being wired in.**

   - **The flat word dictionary cannot arbitrate the reported item, for the
     identical reason it cannot arbitrate section 7's `ss`/`ß` question**:
     it has no part-of-speech information, and it lists inflected surface
     forms broadly, not just lemmas. Checked directly: "treue" AND
     "treuen" are BOTH already present in `de_dictionary_filter.txt` -- as
     real inflections of the adjective "treu" ("die Treue", "meinen
     treuen Freunden"), not as a verb. A bare "is this string a real
     word" lookup on the verb's lemma would accept this exact sentence,
     not reject it: the collision is real, not hypothetical, and verified
     against the actual file rather than assumed. **This is why section
     10's check, below, cannot and does not claim to catch "treue" -- it
     is documented there as a known, permanent gap of that check, not
     silently left implicit.**
   - **Trusting spaCy's own `token.lemma_` for a dictionary check was
     tried and rejected on evidence, not suspicion.** `token.lemma_` comes
     from `EditTreeLemmatizer`, a trained model, not a lookup table
     (confirmed by introspecting the loaded pipe: no exceptions table to
     query for "is this a known verb" either). Tested directly against
     `sentence_source._MOCK_SENTENCE_POOL`: 12 sentences of genuinely
     correct German -- "Du frühstückst normalerweise sehr schnell.", "Du
     vergisst beim Einkaufen ständig deine eigene Tasche.", "Du hilfst
     deinen Freunden immer sehr gern.", and nine more -- got a nonsense
     lemma from the lemmatizer (`vergissen`, `hilfstn`, `rufsen`, ...),
     mostly 2nd-person-singular forms whose stem is genuinely ablauted.
     Gating carrier acceptance on THAT lemma being a dictionary word would
     have rejected all twelve, a direct violation of the zero-false-
     positive regression this module is pinned against, so section 10
     never reads `token.lemma_` at all.

   **What would actually close the reported item specifically:** a
   POS-tagged German verb lexicon (infinitive lemma to part of speech), so
   a finite-verb-lemma check could require the lemma be classified VERB
   specifically, not merely be some real word. No such resource is
   vendored in this repo; building or fetching one is corpus work, out of
   scope for a module that owns carrier validation, not corpus vendoring
   (the same boundary drawn in section 7).

10. **Finite-verb lexical reality, for the two present-tense cells German
    morphology GUARANTEES are never ablauted.** A regular ("weak") verb's
    1st-singular-present is always its stem plus "-e", and its 1st- or
    3rd-plural-present is always the bare infinitive itself, for every
    German verb, strong or weak, without exception -- ablaut (the vowel
    change that makes 2nd/3rd-singular-present unpredictable from the
    infinitive, e.g. "helfen" to "hilfst"/"hilft") never touches these two
    cells. That guarantee is what makes this check safe WITHOUT trusting
    `token.lemma_` (rejected above): for `tag_=="VVFIN"`, `pos_=="VERB"`
    (never `AUX`/`VMFIN` -- the closed-class auxiliaries and modals have
    their own irregular 1st-singular forms, "bin"/"habe"/"kann", not
    covered by this rule), `Mood=="Ind"` (Konjunktiv II is excluded too:
    its stem is not always the plain infinitive stem either, e.g. "wäre"),
    `Tense=="Pres"`, this module derives the candidate infinitive itself
    -- MECHANICALLY, by string surgery, never by reading `token.lemma_` --
    strips a trailing "-e" for `Person=="1", Number=="Sing"`, or takes the
    surface form as-is for `Number=="Plur"` (either person, since the two
    are syncretic here per section on syncretism above) -- and rejects
    only if that candidate (and, for a verb with a separable-prefix `svp`
    child, the candidate with that prefix re-attached, e.g. "auf" +
    "stehe" -> "aufstehen") is absent from the same vendored dictionary
    used in section 7, run through the same `src.lexicon.lemmatizer.
    normalise` the dictionary itself was built with.

    **Measured before shipping, exactly per this task's standing
    instruction:** zero false positives across every 1st-singular- and
    plural-present verb in `_MOCK_SENTENCE_POOL` (22 checked). Catches, by
    construction, a fabricated verb whose derived infinitive is not a real
    word under ANY part of speech (e.g. a "musse"/"mussen"-shaped
    hallucination, absent from the dictionary in every reading, unlike
    "treuen"). **Does NOT catch, and is not claimed to catch, "treue" ->
    "treuen"** (present in the dictionary as a real adjective inflection;
    see section 9 above) or any other case where a fabricated lemma
    happens to collide with a real word under a different part of speech
    -- a POS-tagged verb lexicon would be needed to close that, and this
    repository does not have one.

11. **Swiss `ss` for standard `ß`, generalised: the diphthong half of the
    rule.** docs/audits/cycle-09-report.md 1.4: the closed-list approach
    (sections 6 and 7) caught the class exactly once in a full pilot run
    ("Schliesslich", "draussen" both went uncaught), because it only ever
    covered two lexemes ("heißen", "groß") out of an open class. Section 6
    above explains why the general rule is now safe for HALF of the
    original ss/ß question -- diphthongs ("ei"/"eu"/"äu"/"ie") are always
    long, so `_SWISS_DIPHTHONG_SS_PATTERN` (module level, see its own
    comment) catches any of the four immediately followed by `ss`, verified
    against the vendored dictionary (236 matches, all but one -- "diesseits",
    a genuine compound-boundary `ss` excluded by name -- real ß-words spelled
    Swiss). Catches "schliesslich", "heisst" (already covered by section 6's
    own list too; harmless overlap), and "weiss" (case-sensitive, lowercase
    only, for the same surname reason as "groß" -- "Weiss" is attested too).

    **"au" stays a closed list, not a general rule, and this is a deliberate,
    checked narrowing, not an oversight.** The same dictionary check applied
    to "au" immediately followed by `ss` returns 140 matches, and the large
    majority -- "aussage", "ausschalten", "aussehen", "aussteigen",
    "ausstellen", "voraussetzung", dozens more -- are ordinary "aus-" prefix
    compounds (an extremely productive separable/inseparable verb prefix)
    plus an s-initial stem: standard German, correctly spelled with `ss`
    because the `ss` is two DIFFERENT morphemes' consonants meeting at a
    prefix boundary, never a single-morpheme diphthong-plus-ß the way
    "draußen"/"außen"/"außer" are. A general "au"+`ss` rule would reject
    "aussteigen", "ausschließlich" (itself containing a genuine `ie`+ß
    later in the same word -- the general rule already catches it for that
    reason, without needing "au" at all) and dozens more completely correct
    sentences. `_SWISS_DRAUSSEN_PATTERN` closed-lists exactly the one "au"
    lexeme TODO.md 1.4 names ("draussen") instead.

    **The long-vowel-STEM half of the original rule (a single vowel LETTER,
    not a diphthong) is still a closed list, for the identical reason
    section 6 gives** -- `_SWISS_LONG_VOWEL_STEM_PATTERN` extends it by the
    four lexemes TODO.md 1.4 names beyond "groß": "Straße" -> "strasse",
    "Fuß"/"Füße" -> "fuss"/"füsse", "Maß" -> "mass" (bare word only, see
    below), "Spaß" -> "spass". Matched case-insensitively, unlike "groß"/
    "weiß": all four are common nouns, and German always capitalises a
    common noun in ordinary use, so a lowercase-only match (the right
    choice for an adjective, capitalised only by sentence position) would
    catch almost nothing here.

    **Known cost, recorded rather than discovered later, per this task's
    own instruction:**
    - "Masse"/"Massen" (the Swiss spelling of "Maße", plural of "Maß") is
      NOT caught. "Masse"/"Massen" is itself standard, unrelated German
      ("mass, crowd", genuinely short-vowel), so including it would reject
      correct sentences about crowds or physical mass -- the same false-
      positive-over-false-negative choice section 7 already made for
      "Gross" the surname. Only the bare nominative/genitive "mass"/
      "masses" are caught.
    - "außen"/"außer" and their own compounds ("außerdem",
      "außergewöhnlich", "außenminister", ...) are NOT caught beyond the
      one literal "draussen" TODO.md 1.4 names -- the same "au"-prefix
      ambiguity above applies to them (e.g. "aussenden", "aus" + "senden",
      is a real, unrelated, correctly-`ss`-spelled standard word that
      collides with a naive "aussen"-prefix match), and resolving it
      lexeme-by-lexeme was judged out of this task's scope. A future cycle
      that finds one of these live in a pilot run should extend
      `_SWISS_DRAUSSEN_PATTERN`'s sibling list the same closed-list way,
      not attempt a broader "au" rule.
    - Any OTHER Swiss-spelled long-vowel-single-LETTER word outside this
      closed list ("Maße" itself before the plural collision above, "Soße"
      -> "Sosse", ...) is still uncaught, unchanged from section 7's own
      standing limitation -- this task closed the specific lexemes TODO.md
      1.4 named, not the open class.

## Why this module loads its own spaCy pipeline

``src.taxonomy.tagger`` and ``src.generation.blanking.sentence_tagger`` both
exclude the dependency parser (neither of their callers ever needed it, and
it is the most expensive component to run). Subject-verb agreement is
fundamentally a dependency-tree question -- word order alone cannot answer
it, per this module's own reason for existing -- so this module needs the
parser and cannot reuse either loader. It mirrors both of their fail-safe
contracts exactly: lazy load, ``functools.lru_cache``, never raises, ``None``
whenever spaCy or the model is missing, degrading to a single counted
rejection reason rather than a crash.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from src.generation.blanking.paradigms import (
    ADJ_ENDING_BY_CELL,
    DEFINITE_ARTICLE_BY_CELL,
    EIN_ENDING_BY_CELL,
    Cell,
    Declension,
    adjective_ending,
    match_ein_word,
)
from src.lexicon.lemmatizer import SEPARABLE_PREFIXES, normalise
from src.taxonomy.tagger import MODEL_NAME

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Span as SpacySpan
    from spacy.tokens import Token as SpacyToken

# -- Rejection reasons, named so callers and tests can key off a stable
# string rather than re-deriving prose -----------------------------------
REASON_EMPTY_SENTENCE = "empty_sentence"
REASON_SPACY_UNAVAILABLE = "spacy_unavailable"
REASON_NOT_CAPITALIZED = "not_capitalized"
REASON_NO_TERMINAL_PUNCTUATION = "no_terminal_punctuation"
REASON_FRAGMENT_TOO_SHORT = "fragment_too_short"
REASON_DANGLING_FRAGMENT = "dangling_fragment"
REASON_MULTIPLE_SENTENCES = "multiple_sentences"
REASON_NO_FINITE_VERB = "no_finite_verb"
REASON_MISSING_CLAUSE_CONNECTOR = "missing_clause_connector"
REASON_NO_SUBJECT_FOUND = "no_subject_found"
REASON_AGREEMENT_UNDECIDABLE = "agreement_undecidable"
REASON_SUBJECT_VERB_DISAGREEMENT = "subject_verb_disagreement"
REASON_ADJECTIVE_DECLENSION_MISMATCH = "adjective_declension_mismatch"
REASON_DASS_CLAUSE_MISSING_OBJECT = "dass_clause_missing_object"
REASON_SWISS_SPELLING = "swiss_spelling"
REASON_DASS_AFTER_PHYSICAL_ACTION_VERB = "dass_after_physical_action_verb"
REASON_FINITE_VERB_NOT_A_REAL_WORD = "finite_verb_not_a_real_word"
REASON_CONTENT_WORD_NOT_A_REAL_WORD = "content_word_not_a_real_word"

# STTS fine-grained tags for a finite verb: full verb, auxiliary, modal, and
# their imperative counterparts (imperative is a finite mood, not a
# non-finite form -- see the module docstring's note on why bare informal
# imperatives are rejected anyway, for an unrelated reason).
_FINITE_TAGS: frozenset[str] = frozenset({"VVFIN", "VAFIN", "VMFIN", "VVIMP", "VAIMP"})

# Dependency labels de_core_news_sm uses for a clause's grammatical subject
# ("sb") and an expletive/dummy subject ("ep" -- "es regnet", "es gibt").
_SUBJECT_DEPS: frozenset[str] = frozenset({"sb", "ep"})

# A token bearing one of these fine-grained tags at the very end of a
# sentence (its final non-punctuation token) is still expecting something to
# follow -- a bare article, preposition, or conjunction, the shape a
# truncated generation call leaves behind.
#
# ``APPO`` (postposition: "meiner Meinung nach", "deiner Expertenmeinung
# zufolge") is deliberately NOT in this set, unlike ``APPR``
# (preposition). A preposition always PRECEDES its complement, so one left
# dangling at the very end of a sentence really is missing what should
# follow it -- but a postposition's whole grammatical point is that it
# FOLLOWS its complement, so a sentence correctly ending on one is complete
# by construction, never truncated. Measured on a corpus audit
# (2026-08-20): confirmed against a real Leipzig sentence ending
# "...deiner Expertenmeinung zufolge?", a genuinely complete, correct
# question wrongly rejected before this exclusion.
_CONTINUATION_EXPECTING_TAGS: frozenset[str] = frozenset(
    {
        "ART",
        "PIAT",
        "PPOSAT",
        "APPR",
        "APPRART",
        "KON",
        "KOUS",
        "KOUI",
        "KOKOM",
        "PTKZU",
    }
)

# "..., oder?" is a standard, extremely common German colloquial tag
# question -- short for "oder nicht?" / "oder täusche ich mich?", the exact
# structural equivalent of English "..., or?" / "..., right?" -- not a
# sentence truncated on a bare coordinating conjunction. Bounded narrowly to
# this one word plus a literal question mark (never a bare "oder." or
# "oder!"): a corpus audit (2026-08-20) sampled 44 dangling_fragment
# rejections and found this exact shape in the overwhelming majority,
# always terminated with "?", never with "." or "!" -- consistent with it
# being a real, complete tag-question idiom rather than a truncation.
_TAG_QUESTION_PATTERN = re.compile(r"\boder\s*\?['\"”’)]*\s*$", re.IGNORECASE)

_TERMINAL_PUNCTUATION = re.compile(r"[.!?…]['\"”’)]*\s*$")
_MIN_TOKEN_COUNT = 3

# A fixed, closed list of literal Swiss-spelled surface forms of "heißen" --
# not a general ss/ß rule (see module docstring section 6 for why a general
# rule is not implemented). Word-boundary matched, case-insensitive, on the
# raw text, so no parse is needed to evaluate this one.
_SWISS_HEISSEN_PATTERN = re.compile(
    r"\b(?:heissend|heissen|heisst|heisse|hiessen|hiess|geheissen)\b",
    re.IGNORECASE,
)

# A fixed, closed list of literal Swiss-spelled surface forms of "groß" --
# see module docstring section 7 for why this is a closed-list extension of
# the same technique as _SWISS_HEISSEN_PATTERN, not the dictionary-based
# general rule the task proposed (verified unworkable on the vendored
# data). Matched CASE-SENSITIVELY, lowercase only, unlike the heißen
# pattern: "Gross"/"Grosser" are attested German surnames, and an
# attributive/predicative adjective use is always lowercase anyway, so
# case-sensitivity loses nothing but the rare sentence-initial capitalised
# use while avoiding a false accusation against a real name.
_SWISS_GROSS_PATTERN = re.compile(r"\b(?:gross|grosse|grossem|grossen|grosser|grosses)\b")

# docs/audits/cycle-09-report.md 1.4: a general rule, not another one-word
# closed list. German's own long/short vowel rule for ss-vs-ß ("ß after a
# long vowel or diphthong, ss after a short one") generalises cleanly for
# the DIPHTHONG half specifically: "ei"/"eu"/"äu"/"ie" are long by
# definition (two vowel letters making one sound), so standard German never
# spells one of them followed by "ss" within a single morpheme -- it always
# writes "ß" there instead ("schließen", "heißen", "weiß", "reißen", ...).
# There is no short-vowel reading of a digraph to misjudge the way a single
# vowel LETTER has one (the module docstring's own "Fluss" short vs. "Fuß"
# long example, same "u" letter) -- that asymmetry is exactly why the long
# VOWEL half of the rule still needs a closed list below, while the
# diphthong half does not.
#
# Verified empirically against the vendored dictionary
# (`data/fixtures/corpus/frequency/de_dictionary_filter.txt`, already
# normalised so every real ß-word in it is stored ss-spelled -- see section
# 7): 236 entries match this pattern, and all but one ("diesseits" --
# "dies" + "seits", a genuine compound-boundary "ss", not a ß-word in
# standard spelling either) are real ß-words. "jenseits" does not itself
# match but is the same compound family, so both are excluded on purpose,
# not because either was individually reported.
#
# "au" is deliberately NOT in this general pattern, even though "draußen"
# needs it (handled in the closed list below instead): checked the same
# way against the dictionary, "au" immediately followed by "ss" matches 140
# entries, and the large majority -- "aussage", "ausschalten", "aussehen",
# "aussetzen", "aussteigen", "ausstellen", "voraussetzung", and more -- are
# ordinary "aus-" prefix compounds (a hugely productive separable/
# inseparable verb prefix) plus an s-initial stem, standard German spelled
# with "ss" precisely because the "ss" is two DIFFERENT morphemes'
# consonants meeting, never a single-morpheme diphthong-plus-ß the way
# "draußen"/"außen"/"außer" are. There is no cheap way to tell the two
# apart without a lexicon, so "au" is handled by an explicit closed list
# instead, the same way "groß" already is.
_SWISS_DIPHTHONG_SS_PATTERN = re.compile(r"\b\w*(?:ei|eu|äu|ie)ss\w*\b", re.IGNORECASE)
_SWISS_DIPHTHONG_SS_EXCLUSIONS: frozenset[str] = frozenset({"diesseits", "jenseits"})

# The "au" diphthong, closed-list handled per the comment above.
# Case-insensitive: "draussen" is an adverb with no plausible surname
# collision (unlike "Gross"/"Weiss" below).
_SWISS_DRAUSSEN_PATTERN = re.compile(r"\bdraussen\b", re.IGNORECASE)

# The long-vowel-STEM half of the rule TODO.md 1.4 also names ("Strasse",
# "Fuss", "Mass", "Spass"): a single vowel LETTER carries no long/short
# information this module (or the tagger) can read -- "Fluss" (short "u")
# and "Fuß" (long "u") share the identical letter -- so, exactly as section
# 7 already argues for "groß", these are a closed list of the specific
# lexemes named, not a rule. Matched case-insensitively, unlike "groß"/
# "weiß" below: all four are common nouns, which German always capitalises
# in normal use ("die Straße", "der Fuß", "das Maß", "der Spaß"), so a
# lowercase-only match would catch almost nothing -- the opposite tradeoff
# from an adjective, which is capitalised only by accident of position.
# "mass" is deliberately bounded to the bare word (never "masse"/"massen"):
# "Masse"/"Massen" IS itself standard German ("mass, crowd", genuinely
# short-vowel, unrelated in meaning to "Maß") and would collide directly --
# a real, reported false-negative for the Swiss plural of "Maß" ("Maße" ->
# "Masse"), the identical kind of gap section 7 already accepts for "Gross"
# as a surname.
_SWISS_LONG_VOWEL_STEM_PATTERN = re.compile(
    r"\b(?:strasse\w*|fuss(?:es)?|füsse\w*|mass(?:es)?|spass(?:es)?)\b",
    re.IGNORECASE,
)

# "weiß" -- both the adjective ("weiß"/"weiße"/...) and "wissen"'s own
# "ich weiß"/"du weißt" forms share this spelling -- needs the same
# surname protection "groß" gets ("Weiss" is an attested German surname,
# paired with "Gross" in section 7's own note), so it stays its own
# lowercase-only pattern rather than joining the case-insensitive diphthong
# rule above.
_SWISS_WEISS_PATTERN = re.compile(r"\b(?:weiss|weisse|weissem|weissen|weisser|weisses|weisst)\b")

# Path to the same vendored dictionary used, and found insufficient for the
# ss/ß question, in module docstring section 7 -- reused here for section
# 10's finite-verb lexical-reality check, where it IS sufficient (that
# check only needs "is this a real word", not "is this word spelled with ß
# or ss", which is exactly the distinction the file cannot make -- see
# section 7 and section 9's docstring for why).
_DICTIONARY_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data"
    / "fixtures"
    / "corpus"
    / "frequency"
    / "de_dictionary_filter.txt"
)


@lru_cache(maxsize=1)
def _load_model() -> Language | None:
    """Load ``de_core_news_sm`` WITH the dependency parser, once per process.

    Deliberately its own loader rather than reaching into
    ``src.taxonomy.tagger``'s or ``src.generation.blanking.sentence_tagger``'s
    private ``_load_model`` -- both exclude the parser this module needs.
    Never raises: missing spaCy or a missing model both degrade to ``None``,
    exactly as those two modules do.
    """
    try:
        import spacy
    except ImportError:
        return None
    try:
        # ner is the only pipe this module never reads; tagger, morphologizer
        # and the parser all feed the checks above directly, and the
        # lemmatizer stays in (cheap, and keeps this loader interchangeable
        # with sentence_tagger's if a future check needs a lemma).
        return spacy.load(MODEL_NAME, exclude=["ner"])
    except OSError:
        return None


@lru_cache(maxsize=1)
def _load_dictionary() -> frozenset[str] | None:
    """The vendored real-word list (section 7/10 of the module docstring),
    normalised through the same ``normalise`` the file was built with, so a
    lookup is comparing like with like. Mirrors ``_load_model``'s fail-safe
    contract: never raises, ``None`` if the file is missing, degrading the
    one check that needs it (section 10) to a no-op rather than a crash --
    consistent with every other check in this module being conservative by
    construction, not by exception handling sprinkled at the call site."""
    try:
        with _DICTIONARY_PATH.open("r", encoding="utf-8") as f:
            return frozenset(normalise(line) for line in f if line.strip())
    except OSError:
        return None


def analysis_available() -> bool:
    """Whether this module's own parser-enabled pipeline loaded.

    Not delegated to ``src.taxonomy.tagger.analysis_available()``: that
    checks a *different* loaded pipeline (parser excluded). The two happen to
    fail under the same condition (model not installed at all), but this
    module's own cache is what actually answers whether ITS calls will work,
    so it asks its own loader.
    """
    return _load_model() is not None


@dataclass(frozen=True)
class CarrierValidation:
    """The verdict for one candidate carrier sentence."""

    sentence: str
    accepted: bool
    reason: str | None = None


@dataclass
class CarrierValidationSummary:
    """A batch of carrier validations, aggregated the way a pilot report
    needs: which sentences survived, and a count per rejection reason for
    everything that did not."""

    accepted: list[str] = field(default_factory=list)
    rejected_by_reason: Counter[str] = field(default_factory=Counter)
    total: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def rejected_count(self) -> int:
        return self.total - len(self.accepted)


def _is_finite(token: SpacyToken) -> bool:
    """A token is treated as a finite verb only when its fine-grained tag AND
    its coarse POS agree that it is one. Both are required rather than the
    fine-grained tag alone: the attribute ruler occasionally assigns a
    verb-shaped fine tag (``VVFIN``) to a token whose coarse POS is
    ``ADJ`` -- confirmed on "Er spart bewusst Wasser..." ("bewusst" tags
    ``ADJ``/``VVFIN``, a direct contradiction) -- and a token the pipeline
    itself cannot agree on is exactly the "cannot evaluate confidently"
    case this module is conservative about."""
    return token.tag_ in _FINITE_TAGS and token.pos_ in ("VERB", "AUX")


def _has_swiss_diphthong_spelling(text: str) -> bool:
    """The general diphthong-plus-``ss`` rule (module-level
    ``_SWISS_DIPHTHONG_SS_PATTERN``, see its own comment for the rule and
    the dictionary check behind it), with the two exceptions that pattern
    alone cannot express:

    * ``_SWISS_DIPHTHONG_SS_EXCLUSIONS`` -- "diesseits"/"jenseits", the one
      compound-boundary false positive the dictionary check found.
    * "weiss" and its own inflections are matched CASE-SENSITIVELY here,
      not case-insensitively like the rest of the pattern's matches --
      "Weiss" is an attested German surname (``_SWISS_WEISS_PATTERN``'s own
      comment), the identical protection ``_SWISS_GROSS_PATTERN`` already
      gives "Gross"."""
    for match in _SWISS_DIPHTHONG_SS_PATTERN.finditer(text):
        word = match.group(0)
        lowered = word.lower()
        if lowered in _SWISS_DIPHTHONG_SS_EXCLUSIONS:
            continue
        if lowered.startswith("weiss") and word != lowered:
            continue
        return True
    return False


def _sentence_shape_reason(text: str) -> str | None:
    """Cheap, parser-free structural checks: capitalisation, terminal
    punctuation, and the Swiss-spelling checks (module docstring sections 6
    and 7, and the general diphthong rule docs/audits/cycle-09-report.md
    1.4 adds). Run before spaCy touches the sentence at all, since none of
    these need a parse to decide."""
    stripped = text.strip()
    first_alpha = next((ch for ch in stripped if ch.isalpha()), None)
    if first_alpha is not None and not first_alpha.isupper():
        return REASON_NOT_CAPITALIZED
    if not _TERMINAL_PUNCTUATION.search(stripped):
        return REASON_NO_TERMINAL_PUNCTUATION
    if (
        _SWISS_HEISSEN_PATTERN.search(stripped)
        or _SWISS_GROSS_PATTERN.search(stripped)
        or _SWISS_WEISS_PATTERN.search(stripped)
        or _SWISS_DRAUSSEN_PATTERN.search(stripped)
        or _SWISS_LONG_VOWEL_STEM_PATTERN.search(stripped)
        or _has_swiss_diphthong_spelling(stripped)
    ):
        return REASON_SWISS_SPELLING
    return None


def _resolve_subject(verb: SpacyToken, *, _depth: int = 0) -> SpacyToken | None:
    """The token that governs agreement for ``verb``, or ``None`` if there is
    not exactly one in a checkable way.

    Direct case: a single ``sb``/``ep`` child. The one indirect case handled:
    subject-gapping in coordination -- "Ein Mann steht vor der Tür und
    wartet." is correct German with only ONE subject ("Mann"), shared by
    both coordinated verbs; the second conjunct ("wartet") has no subject
    child of its own at all. When a finite verb is itself a coordinated
    conjunct (``cj``, attached to the coordinating conjunction) and carries
    no subject of its own, this looks at the first conjunct (the
    coordinator's own head) and inherits its subject instead of reporting
    "no subject found" for ordinary, correct coordination. Depth-bounded
    (``_depth``) purely as a defensive guard against a malformed parse
    creating a cycle; real coordination chains never nest more than a
    handful deep."""
    subjects = [c for c in verb.children if c.dep_ in _SUBJECT_DEPS]
    if len(subjects) == 1:
        return subjects[0]
    if len(subjects) == 0 and verb.dep_ == "cj" and _depth < 5:
        coordinator = verb.head
        first_conjunct = coordinator.head
        if _is_finite(first_conjunct) and first_conjunct is not verb:
            return _resolve_subject(first_conjunct, _depth=_depth + 1)
    return None


def _is_bare_imperative(verb: SpacyToken, *, _depth: int = 0) -> bool:
    """Whether ``verb`` is (or is a same-clause coordinate conjunct of) a
    verb-initial main clause with no subject at all and non-interrogative
    terminal punctuation -- the ONE construction where German legitimately
    allows a finite verb to have no subject: the imperative. German main
    clauses are otherwise rigidly verb-SECOND, so a finite ROOT verb in
    absolute first position, with no subject and not itself a question, has
    no other grammatical reading -- this is a categorical word-order fact,
    not a guess. Confirmed on a corpus audit (2026-08-20): "Holt eure
    Bücher raus und schlagt Seite 42 auf.", "Ruf mich morgen früh um sechs
    Uhr an.", "Lasst uns ans Meer fahren." are all correctly-tagged finite
    verbs (``VVFIN``, not the mistagged-as-noun shape the module docstring
    already documents for some bare imperatives) that were nonetheless
    rejected here purely because an imperative structurally has no subject
    to resolve -- the check itself, not the tagger, was wrong to require
    one.

    Scoped narrowly, mirroring ``_resolve_subject``'s own coordination
    fallback rather than a generic upward walk through any dependency type:
    only a verb that IS the sentence's own ``ROOT``, or that reaches it
    through a coordinator (``cj`` -> ``cd`` head -> first conjunct) chain
    exactly like the subject-gapped-coordination case above, can ever
    qualify. An embedded subordinate clause's own verb is never
    dep_-reachable this way (its dep_ is something like "mo"/"oc"/"rc",
    never "ROOT" or a coordination chain rooted at position 0), so this
    cannot accidentally exempt a genuinely subjectless embedded clause
    nested inside an otherwise-imperative sentence."""
    if verb.dep_ == "ROOT":
        candidate = verb
    elif verb.dep_ == "cj" and _depth < 5:
        coordinator = verb.head
        first_conjunct = coordinator.head
        if first_conjunct is verb:
            return False
        return _is_bare_imperative(first_conjunct, _depth=_depth + 1)
    else:
        return False

    if candidate.i != 0:
        return False
    if any(c.dep_ in _SUBJECT_DEPS for c in candidate.children):
        return False
    return not candidate.doc.text.rstrip().endswith("?")


def _default_person(token: SpacyToken) -> str | None:
    """The grammatical Person a subject token resolves to, generalising
    what this check already did for ``NOUN``/``PROPN`` and certain
    ``PRON`` subtypes to every other part of speech. German has exactly
    one class of subject that is ever 1st or 2nd person: a genuine
    personal pronoun ("ich"/"wir"/"du"/"ihr" -- the formal "Sie" already
    resolves to Person 3 in this tagset, confirmed by the existing
    "Kommen Sie bitte her." test). Every other subject -- a common or
    proper noun, a relative/demonstrative/indefinite pronoun, a
    substantivised adjective ("der dritte", "Jung und Alt"), a numeral, a
    determiner used pronominally ("das ... ist"), or a non-finite verb
    acting as a clausal subject -- is grammatically 3rd person BY
    DEFINITION in German: there is no subject type other than a true
    personal pronoun that could ever be notionally "I" or "you".
    Confirmed on a corpus audit (2026-08-20): every ``agreement_undecidable``
    false reject whose only missing feature was Person (Number already
    resolved) was exactly this shape -- an ``ADJ``, ``PROPN``, ``NUM``,
    ``DET``, or non-finite ``VERB`` subject that the pre-audit code simply
    never assigned a Person to."""
    feats = dict(token.morph.to_dict())
    if (
        token.pos_ == "PRON"
        and feats.get("PronType") == "Prs"
        and feats.get("Person")
        in (
            "1",
            "2",
        )
    ):
        return feats["Person"]
    return "3"


def _coordinated_conjuncts(subject: SpacyToken) -> list[SpacyToken]:
    """``subject`` itself plus every other conjunct in its coordinated noun
    phrase ("Polizei und Staatsanwaltschaft" -> both tokens), reached
    through the "cd" coordinator child(ren) already used to detect
    coordination, and that coordinator's own "cj" children -- the same
    dependency shape ``_resolve_subject``'s own coordination fallback
    already navigates for a coordinated VERB, mirrored here for a
    coordinated SUBJECT noun phrase."""
    conjuncts = [subject]
    for cd in (c for c in subject.children if c.dep_ == "cd"):
        conjuncts.extend(c for c in cd.children if c.dep_ == "cj")
    return conjuncts


def _subject_agreement_reason(verb: SpacyToken) -> str | None:
    """Subject-verb agreement for one finite verb, or ``None`` if it agrees
    (subject resolution, including the subject-gapped coordination case, is
    ``_resolve_subject``'s job). See the module docstring for exactly which
    real cases this deliberately treats as undecidable rather than
    guessing."""
    subject = _resolve_subject(verb)
    if subject is None:
        if _is_bare_imperative(verb):
            return None
        return REASON_NO_SUBJECT_FOUND

    # A coordinated subject ("Der Mann und die Frau tanzen") shows up as a
    # "cd" (coordinating conjunction) child on the subject noun itself, not
    # on the verb. German's genuine coordinate-subject person-resolution
    # rule ("du und ich" -> wir-agreement) is real but not implemented here
    # -- but that rule is ONLY needed when a conjunct is a 1st- or
    # 2nd-person pronoun. When every conjunct is grammatically 3rd person
    # (the overwhelming common case in real text: "Polizei und
    # Staatsanwaltschaft", "Tom und Maria", "Knochen und Zähne", ...),
    # German has no genuine ambiguity to resolve at all: two or more
    # distinct 3rd-person entities joined by "und" are always
    # 3rd-person-PLURAL, categorically, so this resolves the whole
    # coordinated subject that way rather than reporting it undecidable.
    # Confirmed on a corpus audit (2026-08-20) this is not merely a
    # loosening but a strict improvement: because the resolved
    # Person/Number still goes through the ordinary comparison below, a
    # genuine number-agreement defect on a coordinated subject (e.g. a
    # singular verb wrongly paired with "der Mann und die Frau") is now
    # actively CAUGHT as REASON_SUBJECT_VERB_DISAGREEMENT rather than
    # silently waved through as undecidable.
    subject_person: str | None
    subject_number: str | None
    if any(grandchild.dep_ == "cd" for grandchild in subject.children):
        conjuncts = _coordinated_conjuncts(subject)
        if all(_default_person(c) == "3" for c in conjuncts):
            subject_person, subject_number = "3", "Plur"
        else:
            return REASON_AGREEMENT_UNDECIDABLE
    else:
        subject_person = _default_person(subject)
        subject_feats = dict(subject.morph.to_dict())
        subject_number = subject_feats.get("Number")
        if subject_number is None and subject.pos_ == "VERB":
            # An infinitive or other non-finite VERB acting as a clausal
            # subject ("Rauchen ist verboten", "Einen Fehler begehen ...
            # bedeutet wirklich fehlen") carries no Number feature in UD,
            # but a clausal subject is always grammatically singular in
            # German -- there is no plural reading of "to do X" as a
            # subject -- so this is a categorical default, not a guess.
            subject_number = "Sing"

    if subject_person is None or subject_number is None:
        return REASON_AGREEMENT_UNDECIDABLE

    verb_feats = dict(verb.morph.to_dict())
    verb_person = verb_feats.get("Person")
    verb_number = verb_feats.get("Number")
    if verb_person is None or verb_number is None:
        return REASON_AGREEMENT_UNDECIDABLE

    if subject_person == verb_person and subject_number == verb_number:
        return None

    if _is_syncretism_tolerated(subject_person, subject_number, verb_person, verb_number, verb):
        return None

    if _is_mistagged_du_st_form(subject_person, subject_number, verb):
        return None

    return REASON_SUBJECT_VERB_DISAGREEMENT


_SIBILANT_STEM_ENDINGS: tuple[str, ...] = ("ss", "s", "ß", "z", "tz", "x")


def _has_sibilant_stem(verb: SpacyToken) -> bool:
    """Whether ``verb``'s infinitive stem ends in a sibilant (s/ss/ß/z/tz/x)
    -- the class of German verbs ("vergessen", "reisen", "sitzen", "heißen")
    whose 2nd- and 3rd-singular present forms genuinely contract to the same
    surface form ("du/er vergisst", "du/er reist"). Reads ``verb.lemma_``,
    which this module's loader keeps enabled specifically for this."""
    lemma = verb.lemma_
    stem = lemma.removesuffix("en").removesuffix("n")
    return stem.endswith(_SIBILANT_STEM_ENDINGS)


def _is_syncretism_tolerated(
    subject_person: str,
    subject_number: str,
    verb_person: str,
    verb_number: str,
    verb: SpacyToken,
) -> bool:
    """Four real, distinct German verb-paradigm syncretisms, each confirmed
    empirically against ``de_core_news_sm`` where noted (see the module
    docstring for the confirmation of 1 and 2):

    1. **1st-plural / 3rd-plural, every tense and every verb, no
       restriction.** German never morphologically distinguishes "wir" from
       "sie" (plural) on the verb -- "wir machen"/"sie machen", "wir
       hatten"/"sie hatten", "wir würden"/"sie würden" are identical in
       every paradigm without exception.
    2. **2nd-plural / 3rd-singular, present tense only, verb form ending in
       "-t".** Restricted to ``Tense=Pres`` because the same two cells are
       NOT syncretic in the preterite ("ihr machtet" / "er machte" differ).
    3. **1st-singular / 3rd-singular, preterite or Konjunktiv II, every
       verb, no restriction.** Another categorical German rule, not a
       present-tense-only one: "ich machte"/"er machte", "ich sah"/"er sah",
       "ich hätte"/"er hätte", "ich wäre"/"er wäre" are always identical.
       Excluded from the present tense on purpose ("ich mache" / "er macht"
       are genuinely distinct there).
    4. **2nd-singular / 3rd-singular, present tense only, and only for a
       sibilant-stem verb** ("vergessen", "reisen", "heißen", "sitzen"):
       German orthography contracts the 2nd-singular "-st" ending against a
       stem-final sibilant, making "du vergisst" and "er vergisst" the same
       word. Checked against the verb's own lemma stem, not assumed."""
    pair = {(subject_person, subject_number), (verb_person, verb_number)}
    tense = dict(verb.morph.to_dict()).get("Tense")
    mood = dict(verb.morph.to_dict()).get("Mood")

    if pair == {("1", "Plur"), ("3", "Plur")}:
        return True
    if pair == {("1", "Sing"), ("3", "Sing")}:
        return tense == "Past" or mood == "Sub"
    if pair == {("2", "Plur"), ("3", "Sing")}:
        return tense == "Pres" and verb.text.endswith(("t", "T"))
    if pair == {("2", "Sing"), ("3", "Sing")}:
        return tense == "Pres" and _has_sibilant_stem(verb)
    return False


def _is_mistagged_du_st_form(subject_person: str, subject_number: str, verb: SpacyToken) -> bool:
    """Not a syncretism (two legitimate readings) -- a straightforward
    ``de_core_news_sm`` morphologizer error, verified empirically on a
    corpus audit (2026-08-20). German's 2nd-singular verb ending, ``-st``,
    is unambiguous: no OTHER (Person, Number) cell of any German verb --
    modal or lexical, regular or irregular -- ever ends in ``-st`` (the
    same categorical fact ``_is_syncretism_tolerated``'s sibilant-stem case
    already relies on for a different ending). The audit found the
    morphologizer nonetheless mistags a genuine ``-st`` form's own Person
    feature as ``1`` or ``3`` (never ``2``) across a range of real subjects
    and verbs -- "Kannst du", "Du kannst", "du bildest", "du gehst",
    "du betrittst" -- regardless of word order (both V2 declarative and V1
    question inversion trigger it). Trusted only when the SUBJECT is
    unambiguously "du" itself (Person 2, Number Sing -- the only nominative
    pronoun that cell can ever resolve to) rather than merely inferred
    2nd-singular some other way, and only when the verb's own surface text
    ends in "st" -- so this can never paper over a genuine Person/Number
    defect on a verb that does not carry the one ending German grammar
    reserves exclusively for "du"."""
    if subject_person != "2" or subject_number != "Sing":
        return False
    return verb.text.strip().lower().endswith("st")


def _clause_connector_reason(verb: SpacyToken) -> str | None:
    """A finite verb that is not the sentence ROOT and whose head is itself
    a finite verb is only a legitimate subordinate clause if it carries an
    explicit subordinating conjunction. Coordinated clauses and relative
    clauses never reach this branch (see module docstring): they attach to
    the conjunction word or the antecedent noun, never straight to another
    finite verb."""
    if verb.dep_ == "ROOT":
        return None
    head = verb.head
    if not _is_finite(head):
        return None
    has_complementizer = any(child.dep_ == "cp" for child in verb.children)
    if has_complementizer:
        return None
    return REASON_MISSING_CLAUSE_CONNECTOR


def _dangling_fragment_reason(tokens: list[SpacyToken]) -> str | None:
    """Sentence completeness beyond capitalisation/terminal punctuation:
    enough tokens to be a sentence, and the last non-punctuation token is not
    something that is still expecting a continuation (a bare article,
    preposition, or conjunction) -- the shape a truncated generation call
    leaves behind."""
    content_tokens = [t for t in tokens if t.pos_ != "PUNCT"]
    if len(content_tokens) < _MIN_TOKEN_COUNT:
        return REASON_FRAGMENT_TOO_SHORT
    if content_tokens[-1].tag_ in _CONTINUATION_EXPECTING_TAGS:
        if content_tokens[-1].tag_ == "KON" and _TAG_QUESTION_PATTERN.search(tokens[0].doc.text):
            return None
        return REASON_DANGLING_FRAGMENT
    return None


def _reverse_form_to_cells(cell_to_form: dict[Cell, str]) -> dict[str, tuple[Cell, ...]]:
    """Invert a ``cell -> surface form`` paradigm dict from ``paradigms.py``
    into ``surface form -> every cell that produces it``, so a determiner's
    OWN surface text can be read back into the set of grammatical cells it
    is consistent with -- the genuine ambiguity (e.g. "der" is Nom Masc Sing
    or Dat/Gen Fem Sing or Gen Plur) that the adjective-declension check
    below must enumerate rather than guess past."""
    reverse: dict[str, list[Cell]] = {}
    for cell, form in cell_to_form.items():
        reverse.setdefault(form, []).append(cell)
    return {form: tuple(cells) for form, cells in reverse.items()}


_DEFINITE_ARTICLE_CELLS_BY_FORM: dict[str, tuple[Cell, ...]] = _reverse_form_to_cells(
    DEFINITE_ARTICLE_BY_CELL
)
_EIN_ENDING_CELLS_BY_ENDING: dict[str, tuple[Cell, ...]] = _reverse_form_to_cells(
    EIN_ENDING_BY_CELL
)


def _strong_plural_ending_to_cases() -> dict[str, frozenset[str]]:
    """Derive ``ending -> {Case, ...}`` for the PLURAL rows of the strong
    adjective paradigm from ``paradigms.ADJ_ENDING_BY_CELL`` -- not a new
    fact, just a regrouping of the same table already imported, used to read
    a plural quantifier's OWN ending (below) back into the Case(s) it is
    consistent with. Strong plural endings do not vary by Gender, so this
    collapses Gender away entirely."""
    reverse: dict[str, set[str]] = {}
    for (case, _gender, number), ending in ADJ_ENDING_BY_CELL["strong"].items():
        if number == "Plur":
            reverse.setdefault(ending, set()).add(case)
    return {ending: frozenset(cases) for ending, cases in reverse.items()}


_STRONG_PLURAL_ENDING_TO_CASES: dict[str, frozenset[str]] = _strong_plural_ending_to_cases()

# The closed set of plural quantifiers that trigger STRONG adjective
# endings (CLAUDE.md task: "viele", "einige", "mehrere", "wenige"). Each
# declines exactly like a strong plural adjective/article on this same
# stem ("viele"/"vielen"/"vieler"), so matching stem + a strong-plural
# ending is sufficient to read off which Case(s) the quantifier's own
# surface form is consistent with.
_STRONG_QUANTIFIER_STEMS: tuple[str, ...] = ("viel", "wenig", "einig", "mehrer")


def _match_strong_quantifier(text: str) -> str | None:
    """The quantifier's own strong-plural ending ("e"/"en"/"er"), or
    ``None`` if ``text`` does not match one of the closed quantifier stems
    with a recognised strong-plural ending."""
    lower = text.strip().lower()
    for stem in _STRONG_QUANTIFIER_STEMS:
        if lower.startswith(stem):
            ending = lower[len(stem) :]
            if ending in _STRONG_PLURAL_ENDING_TO_CASES:
                return ending
    return None


# The only endings any of the three adjective declension paradigms ever
# assign (checked longest-first purely so a 2-letter ending is identified
# over a coincidental trailing "e" -- the sets are otherwise disjoint, since
# none of "em"/"en"/"es"/"er" ends in "e").
_ADJ_SURFACE_ENDINGS: tuple[str, ...] = ("em", "en", "es", "er", "e")


def _adjective_surface_ending(text: str) -> str | None:
    """The attributive adjective's OWN ending, read off its literal surface
    suffix -- never off spaCy's morphology (see module docstring: the
    morphologizer assigns a wrong ending the SAME features the right one
    would have gotten, because it infers Case/Gender/Number from the noun
    phrase's context, not from the adjective's own spelling)."""
    lower = text.strip().lower()
    for ending in _ADJ_SURFACE_ENDINGS:
        if len(lower) > len(ending) and lower.endswith(ending):
            return ending
    return None


_ATTRIBUTIVE_DETERMINER_TAGS: frozenset[str] = frozenset({"ART", "PIAT", "PPOSAT"})


def _declension_for_determiner(
    det: SpacyToken, noun_gender: str, noun_number: str
) -> tuple[Declension, frozenset[str]] | None:
    """The declension family an attributive adjective must follow given its
    determiner ``det``, and every Case that determiner's own surface form is
    consistent with for a head noun of ``noun_gender``/``noun_number`` --
    ``None`` if ``det`` cannot be confidently classified at all (an
    unrecognised surface form, or a paradigm gap such as the ein-word
    family's missing Nominative/Accusative plural row), in which case the
    caller skips the check for this noun phrase rather than guesses."""
    feats = dict(det.morph.to_dict())
    if det.tag_ == "ART" and feats.get("Definite") == "Def":
        cells = _DEFINITE_ARTICLE_CELLS_BY_FORM.get(det.text.strip().lower(), ())
        cases = frozenset(c[0] for c in cells if c[1] == noun_gender and c[2] == noun_number)
        return ("weak", cases) if cases else None

    if det.tag_ == "PIAT":
        quantifier_ending = _match_strong_quantifier(det.text)
        if quantifier_ending is not None:
            return ("strong", _STRONG_PLURAL_ENDING_TO_CASES[quantifier_ending])
        # Falls through to the ein-word family: de_core_news_sm tags
        # "kein"/"keine"/... as PIAT too, not ART, and "kein" takes the
        # same mixed declension as "ein"/"mein"/... .

    if det.tag_ in ("ART", "PPOSAT", "PIAT"):
        match = match_ein_word(det.text)
        if match is None:
            return None
        _stem, ending = match
        cells = _EIN_ENDING_CELLS_BY_ENDING.get(ending, ())
        cases = frozenset(c[0] for c in cells if c[1] == noun_gender and c[2] == noun_number)
        return ("mixed", cases) if cases else None

    return None


def _adjective_declension_reason(tokens: list[SpacyToken]) -> str | None:
    """Attributive adjective declension: an adjective's ending must be a
    member of the declension family its determiner selects (weak/mixed/
    strong), for at least one Case reading consistent with the determiner's
    own surface form and the head noun's real Gender/Number. See the module
    docstring section 4 for the full reasoning and the deliberately narrow
    scope (only ``ART``/``PPOSAT``/``PIAT`` determiners are recognised; a
    determiner-less noun phrase is checked as strong across all four Cases,
    which is looser but never wrong)."""
    for noun in tokens:
        if noun.pos_ not in ("NOUN", "PROPN"):
            continue
        noun_feats = dict(noun.morph.to_dict())
        noun_gender = noun_feats.get("Gender")
        noun_number = noun_feats.get("Number")
        if noun_gender is None or noun_number is None:
            continue

        adjectives = [c for c in noun.children if c.dep_ == "nk" and c.tag_ == "ADJA"]
        if not adjectives:
            continue

        determiners = [
            c for c in noun.children if c.dep_ == "nk" and c.tag_ in _ATTRIBUTIVE_DETERMINER_TAGS
        ]
        if len(determiners) == 1:
            resolved = _declension_for_determiner(determiners[0], noun_gender, noun_number)
            if resolved is None:
                continue
            declension, cases = resolved
        elif len(determiners) == 0:
            declension, cases = "strong", frozenset({"Nom", "Acc", "Dat", "Gen"})
        else:
            # More than one determiner-tagged child of one noun is not a
            # shape this check anticipates; skip rather than guess which one
            # governs the adjective.
            continue

        candidate_endings = {
            ending
            for case in cases
            if (ending := adjective_ending(declension, (case, noun_gender, noun_number)))
            is not None
        }
        if not candidate_endings:
            continue

        for adjective in adjectives:
            if _is_invariant_toponymic_adjective(adjective):
                continue
            actual_ending = _adjective_surface_ending(adjective.text)
            if actual_ending is not None and actual_ending not in candidate_endings:
                return REASON_ADJECTIVE_DECLENSION_MISMATCH
    return None


def _is_invariant_toponymic_adjective(adjective: SpacyToken) -> bool:
    """German toponymic/decade adjectives formed with the invariant ``-er``
    suffix ("Berliner", "Münchner", "Pariser", "Wiener", "Sechziger") never
    decline at all -- "der Berliner Bär", "die Berliner Mauer", "dem
    Berliner Wetter", "die Berliner Kinder" all keep the identical surface
    form regardless of Case, Gender or Number. This module has no gazetteer
    of place names to identify them by lexeme, but German orthography
    already marks this exact class unambiguously: unlike an ordinary
    attributive adjective, which is lowercase in attributive position
    except when it happens to open the sentence, a toponymic ``-er``
    adjective is conventionally capitalised the same way its source proper
    noun is, in EVERY position. So a capitalised, non-sentence-initial
    ``ADJA`` token ending in ``-er`` is this class, not a declension error
    -- confirmed on a corpus audit (2026-08-20): every one of a 44-item
    ``adjective_declension_mismatch`` sample was exactly this shape
    ("Braunschweiger", "Münchner", "Dortmunder", "Pariser", "Wiener",
    "Prager", "Kieler", "Hamburger", "Sechziger", ...), and none was a
    genuine wrongly-declined adjective. Bounded to the ``-er`` ending
    specifically (the only ending this invariant class ever takes) so a
    capitalised word with any OTHER ending -- which this class never
    produces -- still gets the normal declension check, not a blanket
    pass."""
    if adjective.i == 0:
        return False
    text = adjective.text.strip()
    if not text or not text[0].isupper():
        return False
    return _adjective_surface_ending(text) == "er"


# The closed, two-verb list task 5 (module docstring) is scoped to: about as
# close to obligatorily transitive as German verbs get in ordinary written
# prose, so a missing accusative object is a reliable gap signal rather than
# the normal object-dropping German otherwise tolerates freely.
_DASS_CLAUSE_TRANSITIVE_LEMMAS: frozenset[str] = frozenset({"kaufen", "schenken"})


def _content_verb(finite_verb: SpacyToken) -> SpacyToken:
    """Walk down an auxiliary/modal ``oc`` chain to the deepest verb/aux
    carrying the clause's real lexical content (e.g. "gekauft" inside
    "..., dass sie es gekauft hatten"), or ``finite_verb`` itself if there is
    no such chain. Depth-bounded via the visited set purely as a defensive
    guard against a malformed parse creating a cycle."""
    current = finite_verb
    seen = {current.i}
    while True:
        content_children = [
            c for c in current.children if c.dep_ == "oc" and c.pos_ in ("VERB", "AUX")
        ]
        if len(content_children) != 1 or content_children[0].i in seen:
            return current
        current = content_children[0]
        seen.add(current.i)


def _dass_clause_reason(tokens: list[SpacyToken]) -> str | None:
    """``dass`` versus ``das``: see module docstring section 5 for the full
    reasoning. Catches only "dass" written where relative "das" belongs, and
    only when the clause's content verb is one of a closed two-verb list
    that has no accusative object at all -- the gap the missing relative
    pronoun leaves behind. Does not catch the reverse error."""
    for kous in tokens:
        if kous.tag_ != "KOUS" or kous.dep_ != "cp" or kous.text.strip().lower() != "dass":
            continue
        finite_verb = kous.head
        if not _is_finite(finite_verb):
            continue
        content_verb = _content_verb(finite_verb)
        if content_verb.lemma_ not in _DASS_CLAUSE_TRANSITIVE_LEMMAS:
            continue
        if any(c.dep_ == "svp" for c in content_verb.children):
            # A separable-prefix compound (e.g. "kaufen" + "ein" ->
            # "einkaufen") is a different verb with different valency, not a
            # transitivity gap in "kaufen"/"schenken" themselves.
            continue
        has_object = any(c.dep_ in ("oa", "oa2") for c in content_verb.children)
        if not has_object:
            return REASON_DASS_CLAUSE_MISSING_OBJECT
    return None


# The closed BLOCKLIST for module docstring section 8: matrix verbs
# confirmed, by the audit's own examples, to be obligatorily transitive,
# physical, and non-communicative -- never a real "dass"-complement
# licenser. "kaufen"/"schenken" are the same two already vetted for
# section 5's check, for the same reason; "helfen" is the second audited
# item. "laden" is handled separately (below) because only its
# "herunterladen" reading is in scope: plain "laden" or a differently
# prefixed compound is a different verb, not audited, and left alone.
_DASS_MATRIX_PHYSICAL_ACTION_LEMMAS: frozenset[str] = frozenset({"kaufen", "schenken", "helfen"})

# The one separable compound in scope: "herunterladen" ("laden" + the
# literal particle "herunter"), the audit's first example. Plain "laden"
# (no particle) or any other prefixed compound ("einladen", "aufladen", ...)
# is a different verb with different valency/semantics and is not touched.
_DASS_MATRIX_SEPARABLE_LEMMA = "laden"
_DASS_MATRIX_SEPARABLE_PARTICLE = "herunter"


def _is_disallowed_dass_matrix(verb: SpacyToken) -> bool:
    """Whether ``verb`` is on the closed physical-action blocklist (module
    docstring section 8), narrowly enough that a differently-prefixed
    compound of the same stem is never caught by accident."""
    svp_particles = [c.text.strip().lower() for c in verb.children if c.dep_ == "svp"]
    if verb.lemma_ == _DASS_MATRIX_SEPARABLE_LEMMA:
        return svp_particles == [_DASS_MATRIX_SEPARABLE_PARTICLE]
    if verb.lemma_ in _DASS_MATRIX_PHYSICAL_ACTION_LEMMAS:
        # A separable-prefix compound of "kaufen"/"schenken" (there is no
        # common one, but the same precedent as section 5's check applies)
        # would be a different verb; only the bare verb is in scope.
        return not svp_particles
    return False


def _matrix_predicate(kous: SpacyToken) -> SpacyToken | None:
    """The verb that GOVERNS the ``dass``-clause from outside it (module
    docstring section 8), or ``None`` if it cannot be found confidently.

    ``kous.head`` is the clause's OWN finite verb (e.g. "ist" in "..., dass
    ... ist"), not the matrix predicate -- that verb's own ``.head`` is
    usually the matrix verb directly, but a real, observed parser quirk
    sometimes attaches the clause to the preceding OBJECT NOUN instead
    ("Sie kaufen ein neues Smartphone, dass ... ist" attaches "ist" to
    "Smartphone", not to "kaufen"), so exactly one more hop through a
    NOUN/PROPN head is allowed before giving up. Anything else (an
    extraposed "es" placeholder subject, a predicate adjective, ...) is
    deliberately NOT walked past -- see the module docstring's "known gap,
    left uncaught on purpose" for why "Es wäre hilfreich, dass ..." is not
    handled here."""
    clause_verb = kous.head
    if clause_verb.pos_ not in ("VERB", "AUX"):
        return None
    candidate = clause_verb.head
    if candidate is clause_verb:
        return None
    if candidate.pos_ in ("NOUN", "PROPN"):
        candidate = candidate.head
    if candidate is clause_verb or candidate.pos_ not in ("VERB", "AUX"):
        return None
    return candidate


def _dass_matrix_verb_reason(tokens: list[SpacyToken]) -> str | None:
    """``dass`` after a matrix verb of physical action, where "damit"/
    "weil"/"wenn" was meant: see module docstring section 8 for the full
    reasoning, the closed blocklist, and the one construction (predicate
    adjective, e.g. "wäre hilfreich") this deliberately does not cover."""
    for kous in tokens:
        if kous.tag_ != "KOUS" or kous.dep_ != "cp" or kous.text.strip().lower() != "dass":
            continue
        matrix = _matrix_predicate(kous)
        if matrix is None:
            continue
        if _is_disallowed_dass_matrix(matrix):
            return REASON_DASS_AFTER_PHYSICAL_ACTION_VERB
    return None


# The two present-tense (Person, Number) cells German morphology guarantees
# are never ablauted -- see module docstring section 10 for the full
# argument for why this makes a mechanically-derived (never
# ``token.lemma_``-trusting) infinitive candidate safe to check here where
# trusting the lemmatizer was measured and rejected.
_SAFE_LEXICAL_REALITY_NUMBERS: frozenset[str] = frozenset({"Sing", "Plur"})


def _candidate_infinitives(verb: SpacyToken, feats: dict[str, str]) -> frozenset[str] | None:
    """The infinitive form(s) ``verb`` would need to be a real word under,
    derived MECHANICALLY from its own surface text -- never from
    ``token.lemma_`` (module docstring section 10 explains why that is
    untrustworthy for exactly this purpose). ``None`` if ``verb`` is not in
    one of the two safe (Person, Number) cells, or its surface form does
    not fit the expected shape for its cell (defensive: an unexpected
    shape is skipped, not guessed at)."""
    text = verb.text.strip().lower()
    number = feats.get("Number")
    person = feats.get("Person")
    if number == "Sing":
        if person != "1" or not text.endswith("e") or len(text) < 3:
            return None
        base = text[:-1] + "en"
    elif number == "Plur":
        if person not in ("1", "3"):
            return None
        base = text
    else:
        return None
    candidates = {base}
    svp_particles = [c.text.strip().lower() for c in verb.children if c.dep_ == "svp"]
    if len(svp_particles) == 1:
        candidates.add(svp_particles[0] + base)
    return frozenset(candidates)


def _finite_verb_lexical_reality_reason(verb: SpacyToken) -> str | None:
    """Whether ``verb``'s mechanically-derived candidate infinitive(s) --
    never its ``token.lemma_``, see above -- are a real word in the
    vendored dictionary (module docstring section 10). ``None`` (no
    rejection) whenever the dictionary failed to load, the verb is not a
    lexical verb (``AUX``/modal are excluded -- their irregular forms are
    not covered by this rule), its mood is not plain indicative present
    (Konjunktiv II stems are not always the infinitive stem either), or it
    is not in one of the two safe (Person, Number) cells at all --
    conservative by construction, exactly like the rest of this module."""
    if verb.tag_ != "VVFIN" or verb.pos_ != "VERB":
        return None
    feats = dict(verb.morph.to_dict())
    if feats.get("Mood") != "Ind" or feats.get("Tense") != "Pres":
        return None
    if feats.get("Number") not in _SAFE_LEXICAL_REALITY_NUMBERS:
        return None
    dictionary = _load_dictionary()
    if dictionary is None:
        return None
    candidates = _candidate_infinitives(verb, feats)
    if candidates is None:
        return None
    normalised_candidates = {normalise(c) for c in candidates}
    if normalised_candidates & dictionary:
        return None
    return REASON_FINITE_VERB_NOT_A_REAL_WORD


# ==============================================================================
# docs/audits/cycle-07-report.md defect 6, third fix of the zustandspassiv
# group: "Der kaputte Computer ... ist jetzt wieder repariert, weil der
# Hausmeister ihn gestern schnell heilgemacht hat." is a genuinely correct
# Zustandspassiv on its OWN blanked slot -- the fault is elsewhere in the
# same carrier, "heilgemacht", which is not standard German ("heil" +
# "gemacht" fused as if it were one separable-verb participle; the intended
# word is "repariert" or similar). Section 10 above only ever checks a
# FINITE verb in one of the two ablaut-safe cells, so a PARTICIPLE like this
# one is entirely outside its scope; this is the extension for non-finite
# verb forms specifically.
#
# Scoped to non-finite VERB forms only (VVPP/VVINF/VAINF/VMINF: past
# participles and bare infinitives), never to nouns, and checked by DIRECT
# dictionary lookup on the surface form only -- no compound-split fallback.
# This is a deliberate, narrower asymmetry from the cue-reality check
# ``selectors._cue_is_real_word`` uses for a lemma cue: German compounding
# is a genuinely productive NOUN process ("Radweg", absent as a direct
# entry, is still perfectly good German because "Rad"+"Weg" both resolve),
# but there is no equivalent productive process for FUSING an adjective and
# a participle into one new verb form the way "heilgemacht" pretends to --
# and "heil" and "gemacht" are BOTH, individually, real dictionary words, so
# a compound-split fallback applied here would accept exactly the defect
# this check exists to catch. Extending this check to every noun in the
# carrier as well was considered and deliberately left out: a 37,567-entry
# general-purpose list is nowhere near exhaustive for German's productive
# noun compounding, and the false-positive risk of rejecting ordinary,
# correct carriers this way was not one this task's evidence supported
# taking on for every noun. See docs/audits/cycle-07-report.md for that
# tradeoff, since this constant appears there.
_NON_FINITE_VERB_FORM_TAGS: frozenset[str] = frozenset({"VVPP", "VVINF", "VAINF", "VMINF"})

# A separable-prefixed participle fuses its prefix directly onto the "ge-"
# participle as ONE token ("wieder" + "getroffen" -> "wiedergetroffen"),
# unlike a separable-prefixed FINITE verb, which splits into two tokens in
# plain word order (section 10's own ``svp`` dependency-child handling).
# There is no separate token here to read the prefix off, so a direct
# dictionary lookup on the whole fused form fails for a real word ANY time
# the base participle alone was vendored but the prefixed compound was not
# -- confirmed empirically, not assumed: "wiedergetroffen" (a real,
# standard Partizip II of "wiedertreffen") rejected the entire
# ``_MOCK_SENTENCE_POOL`` regression before this list existed.
#
# Reuses ``src.lexicon.lemmatizer.SEPARABLE_PREFIXES`` (this cycle's own "do
# not duplicate paradigm data" standard, extended to prefix data) plus
# "wieder" -- confirmed missing from that list (it is scoped to a narrower,
# unrelated CEFR-lookup purpose that never needed it) but a genuine,
# unambiguous separable prefix for exactly this purpose. Sorted longest
# first so a token is never mis-split on a shorter prefix that is itself a
# substring of a longer, more specific one.
#
# Deliberately a CLOSED, curated list, not a blind "does 'ge' appear
# anywhere in this token" search: "heilgemacht" also contains "ge"
# (position 4, "heil" + "ge" + "macht") and "gemacht" also resolves in the
# dictionary, so an unscoped search would wrongly ACCEPT the very defect
# this check exists to catch. "heil" is not a real German separable-verb
# prefix (it is an adjective), so it is not, and must never be, a member of
# this list -- the curation itself is the check, not merely a convenience.
_NON_FINITE_VERB_PREFIXES: tuple[str, ...] = tuple(
    sorted(SEPARABLE_PREFIXES | frozenset({"wieder"}), key=len, reverse=True)
)


def _participle_candidate_texts(text: str) -> frozenset[str]:
    """``text`` itself, plus -- for a fused separable-prefix participle --
    the bare participle with a recognised prefix from
    ``_NON_FINITE_VERB_PREFIXES`` stripped off, keeping the "ge-" that
    always immediately follows a genuine separable prefix in this shape
    ("wieder" + "getroffen", never "wieder" + "troffen"). A prefix whose
    remainder does not start with "ge" is not tried at all -- this is what
    keeps the check from mechanically splitting on an unrelated internal
    substring."""
    candidates = {text}
    lower = text.lower()
    for prefix in _NON_FINITE_VERB_PREFIXES:
        if lower.startswith(prefix) and lower[len(prefix) :].startswith("ge"):
            candidates.add(text[len(prefix) :])
    return frozenset(candidates)


def _non_finite_verb_lexical_reality_reason(tokens: list[SpacyToken]) -> str | None:
    """Whether every non-finite verb form (participle or bare infinitive) in
    ``tokens`` is a real word under a DIRECT dictionary lookup on its own
    surface text, or on its own text with a recognised separable prefix
    stripped (``_participle_candidate_texts``) -- see this section's own
    module-level comment for why no general compound-split fallback is
    offered here, unlike the noun-cue check this reuses the same vendored
    dictionary from. ``None`` (no rejection) whenever the dictionary failed
    to load, matching every other check in this module's fail-safe
    contract."""
    dictionary = _load_dictionary()
    if dictionary is None:
        return None
    for token in tokens:
        if token.tag_ not in _NON_FINITE_VERB_FORM_TAGS:
            continue
        text = token.text.strip()
        if not text:
            continue
        candidates = {normalise(c) for c in _participle_candidate_texts(text)}
        if candidates & dictionary:
            continue
        return REASON_CONTENT_WORD_NOT_A_REAL_WORD
    return None


def _real_sentence_count(sentence_spans: list[SpacySpan]) -> int:
    """How many GENUINE sentences ``sentence_spans`` (``doc.sents``)
    actually contains, correcting for spaCy's own sentencizer occasionally
    splitting mid-sentence with no real terminal punctuation at all behind
    the split -- confirmed empirically on a corpus audit (2026-08-20)
    against real Leipzig text: a capitalised brand/proper-noun run with no
    period ("Nun wollen die Earfun" | "Free Pro 3 im Test beweisen..."), a
    bare inverted-question opening ("Erteilt" | "Van der Bellen..."), and an
    ordinal number's period, which the tagger correctly does NOT read as
    sentence-final ("Die 112." | "Tour de France...", "112." tagged
    ``ADJA``, not ``$.``) all trip the sentencizer this way. A GENUINE
    second sentence, by contrast, always ends its earlier span on the STTS
    sentence-final punctuation tag ``$.`` -- covering ".", "!", "?", ":",
    ";" here, verified directly against real two-sentence corpus text
    (colon-introduced quotes, question-then-exclamation pairs, ...).

    Only the SENTENCE-COUNTING gate is touched: every other check in this
    module already reads every token in the whole ``doc`` regardless of
    ``.sents`` grouping, so merging a spurious split changes nothing else
    about how the (correctly single) sentence is checked, and a genuine
    run-on with no punctuation anywhere between its two clauses never
    reaches this function with more than one span in the first place --
    that shape is ``missing_clause_connector``'s job, not this one's."""
    if not sentence_spans:
        return 0
    return 1 + sum(1 for span in sentence_spans[:-1] if span[-1].tag_ == "$.")


def validate_carrier(sentence: str) -> CarrierValidation:
    """Validate one plain, generated German sentence as a sound carrier.

    Never raises. Returns ``accepted=False`` with a specific ``reason``
    whenever the sentence is empty, spaCy is unavailable, or any check
    fails or cannot be evaluated confidently -- conservative by design (see
    module docstring): keeping a bad carrier is far more expensive than
    losing a good one.
    """
    stripped = sentence.strip()
    if not stripped:
        return CarrierValidation(sentence, False, REASON_EMPTY_SENTENCE)

    shape_reason = _sentence_shape_reason(stripped)
    if shape_reason is not None:
        return CarrierValidation(sentence, False, shape_reason)

    nlp = _load_model()
    if nlp is None:
        return CarrierValidation(sentence, False, REASON_SPACY_UNAVAILABLE)

    doc = nlp(stripped)
    sentence_spans = list(doc.sents)
    if _real_sentence_count(sentence_spans) > 1:
        return CarrierValidation(sentence, False, REASON_MULTIPLE_SENTENCES)

    tokens = list(doc)
    dangling_reason = _dangling_fragment_reason(tokens)
    if dangling_reason is not None:
        return CarrierValidation(sentence, False, dangling_reason)

    finite_verbs = [t for t in tokens if _is_finite(t)]
    if not finite_verbs:
        return CarrierValidation(sentence, False, REASON_NO_FINITE_VERB)

    for verb in finite_verbs:
        connector_reason = _clause_connector_reason(verb)
        if connector_reason is not None:
            return CarrierValidation(sentence, False, connector_reason)

    for verb in finite_verbs:
        agreement_reason = _subject_agreement_reason(verb)
        if agreement_reason is not None:
            return CarrierValidation(sentence, False, agreement_reason)

    for verb in finite_verbs:
        lexical_reality_reason = _finite_verb_lexical_reality_reason(verb)
        if lexical_reality_reason is not None:
            return CarrierValidation(sentence, False, lexical_reality_reason)

    non_finite_reality_reason = _non_finite_verb_lexical_reality_reason(tokens)
    if non_finite_reality_reason is not None:
        return CarrierValidation(sentence, False, non_finite_reality_reason)

    declension_reason = _adjective_declension_reason(tokens)
    if declension_reason is not None:
        return CarrierValidation(sentence, False, declension_reason)

    dass_reason = _dass_clause_reason(tokens)
    if dass_reason is not None:
        return CarrierValidation(sentence, False, dass_reason)

    dass_matrix_reason = _dass_matrix_verb_reason(tokens)
    if dass_matrix_reason is not None:
        return CarrierValidation(sentence, False, dass_matrix_reason)

    return CarrierValidation(sentence, True, None)


def validate_carriers(sentences: Iterable[str]) -> CarrierValidationSummary:
    """Validate a batch, aggregated for a pilot report: accepted sentences in
    original order, and a count per rejection reason for everything
    discarded. Never raises, matching ``validate_carrier``."""
    summary = CarrierValidationSummary()
    for sentence in sentences:
        summary.total += 1
        result = validate_carrier(sentence)
        if result.accepted:
            summary.accepted.append(sentence)
        else:
            reason = result.reason or "unknown_skip_reason"
            summary.rejected_by_reason[reason] += 1
    return summary
