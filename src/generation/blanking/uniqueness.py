"""The uniqueness gate: whether the blanked token is the ONLY member of its
own closed grammatical class that would also be grammatical in the same
slot -- a different question from whether the token itself is correct
there, which ``blanker.py`` already guarantees by construction (the token
came from a real, carrier-validated German sentence).

docs/audits/cycle-04-report.md's finding: removing a token proves the
removed token was right, it proves nothing about whether some OTHER member
of the same closed class would have been equally right. Where the blanked
slot belongs to a small closed class (a modal verb, a personal pronoun)
several members of which are near-universally interchangeable there, the
item is correct but not solvable -- the learner has no way to reason to the
one accepted answer among several equally grammatical ones.

## Policy per ``Candidate.kind``, and why

* ``irregular_aux`` whose lemma is one of the five modals (or ``möchten``,
  the frozen sixth) -- **ambiguous, UNLESS the candidate carries a cue, in
  which case it passes.** German modal choice is a free lexical choice
  almost everywhere a modal appears; there is no realistic syntactic signal
  in a plain sentence that rules out every other modal at the same (tense,
  person, number) cell (the cycle-4 report's own examples:
  "kann"/"muss"/"soll"/"sollte"/"darf" all fit "Das Fleisch ___ scharf
  angebraten werden ..." equally well). ``sein``/``haben``/``werden`` are
  excluded from THIS rule specifically -- unlike the modals, an auxiliary's
  own IDENTITY is fixed by the construction itself (Perfekt/Plusquamperfekt
  choose sein-vs-haben per verb, Passiv always takes werden), so there is no
  OTHER auxiliary LEXEME that could stand in the same slot at all, and
  ``selectors.py`` never sets a cue on one of them either (see
  ``_irregular_finite_selector``: the cue is gated on the lemma being a
  modal in the first place). That is a narrower claim than "sein/haben/
  werden are never ambiguous", though -- see the dedicated bullet below for
  the TENSE question this one leaves open.

  ``Candidate.cue`` (docs/audits/cycle-04-report.md recommendation 5) is a
  bracketed citation form -- here, the modal's own infinitive -- that gives
  the learner exactly the one fact a free lexical choice among modals is
  missing: WHICH modal. "___ (müssen) er noch arbeiten." no longer accepts
  "kann"/"soll"/"darf" alongside "muss" the way a bare "___ er noch
  arbeiten." slot would, because the cue names the lexeme directly. A cue
  is only trusted when the selector actually supplied one (``None`` still
  falls through to the always-ambiguous skip below) -- see
  ``selectors._citation_cue`` for the one case a modal candidate goes
  without a cue: a present-tense 1st/3rd-plural form is spelled identically
  to its own infinitive ("wir/sie müssen" == "müssen"), so a cue there would
  hand over the answer verbatim and is withheld instead, leaving that one
  cell genuinely unrescuable and still skipped.

* ``irregular_aux`` whose lemma is ``sein``, ``haben`` or ``werden`` --
  **ambiguous, UNLESS the carrier itself anchors the TENSE.** The previous
  bullet's claim (no rival auxiliary LEXEME) says nothing about which CELL
  of that one lexeme's own paradigm is meant: "ist"/"war" (Zustandspassiv,
  plain copula), "hat"/"hatte" and "wird"/"wurde" (a bare Präsens/Präteritum
  aux, plain Passiv) are each, by default, equally grammatical in the same
  slot with nothing else to decide between them -- docs/audits/
  cycle-06-report.md's task 1, the biggest untreated defect class the
  report found: "Das Buffet ___ bereits gut geplant, aber die Getränke
  müssen wir noch einkaufen." accepts "ist" and "war" alike.

  This is the SAME asymmetry the ``personal_pronoun`` bullet below
  describes for a different part of speech -- the ambiguity is over which
  CELL, never which lexeme -- so ``Candidate.cue`` does not rescue it
  either, for the identical reason a cue cannot rescue a pronoun (see that
  bullet's own closing paragraph); this branch never inspects
  ``candidate.cue`` at all. What rescues it instead is one of three
  anchors, checked in this order by ``_auxiliary_tense_anchor_present``:

  1. **The construction's own shape already fixes it**
     (``_tense_forced_by_construction``) -- Konjunktiv II (no rival
     indicative-shaped form of the same construction exists at all), Futur
     I/II (``werden`` plus a clause-final infinitive -- no ordinary German
     construction pairs a trailing infinitive with "wurde" the same way),
     and Perfekt/Plusquamperfekt (``haben``/``sein`` plus a Partizip II in
     the same clause, aux choice and Zustandspassiv-vs-Perfekt both already
     decided by the participle's own lemma against
     ``paradigms.AUX_SEIN_LEMMAS``/``TRANSITIVE_LEMMAS``, the same disjoint
     pair ``selectors.py`` itself uses). Zustandspassiv is deliberately NOT
     in this list -- it is the report's own worked example, and a
     participle alone does not fix its tense (a Zustandspassiv participle's
     lemma is never in ``AUX_SEIN_LEMMAS``, so it falls through to anchor 2
     or 3 like any other bare aux).
  2. **An explicit time expression** (``_temporal_expression_anchor``) --
     ``paradigms.TEMPORAL_ANCHOR_LEMMAS``, the same closed list
     ``scripts/check_gold_examples.py`` already uses for its own
     ``temporal_anchor`` forcing-element check, reused here rather than
     redeclared.
  3. **A genuinely subordinated sibling clause with matching tense**
     (``_sibling_clause_tense_anchor``) -- the report's own "Obwohl die
     neuen Vorschriften sehr streng ___, bitten wir um Ihr Verständnis."
     example: the present-tense matrix clause ("bitten wir") forces "sind",
     not "waren", because the "obwohl" clause describes the SAME state.
     Narrowly scoped (see that function's own docstring) specifically so
     the report's OTHER example does not turn into a false anchor: "aber"
     COORDINATES two independent clauses with no tense relationship at all,
     which a naive "some other finite verb in the sentence has a matching
     Tense" check would wrongly accept.

  No anchor of any of the three kinds: the candidate is skipped
  (``auxiliary_tense_unanchored``), exactly the standing standard's "no
  anchor, no item".

* ``personal_pronoun`` -- **ambiguous unless the carrier itself supplies an
  anchor.** A NOMINATIVE personal pronoun is trusted on the strength of the
  sentence's own finite verb form alone -- UNLESS that verb form's own
  (person, number) is itself SYNCRETIC across more than one pronoun, the
  sixth reason this module tracks (docs/audits/cycle-07-report.md section C,
  added alongside the determiner/adjective fix below):

  1. **1st and 3rd person PLURAL share one finite form in every German
     tense** ("wir machen"/"sie machen"/"Sie machen",
     "wir machten"/"sie machten"/"Sie machten", ...) -- a closed
     conjugation fact true of every verb, not a per-lemma lookup. "wir",
     "sie" (3rd plural) and the formal "Sie" are the resulting three-way
     ambiguity; this tagger cannot tell "sie" and "Sie" apart at all (it
     tags both ``Person=3``, ``Number=Plur`` regardless of capitalisation),
     so the ambiguity is real even where German orthography alone would
     resolve it for a human reader.
  2. **1st and 3rd person SINGULAR share one finite form in the Präteritum
     and Konjunktiv II** ("ich machte"/"er machte", "ich hätte"/"er
     hätte") -- both surface as ``Tense=Past`` in this tagger's own
     morphology (see the Konjunktiv II selectors elsewhere in this
     package) -- but NOT in the Präsens, where the endings genuinely
     differ ("ich mache"/"er macht").

  See ``_nominative_pronoun_syncretic`` for exactly which (person, number,
  tense) combinations this covers. Where the verb form IS syncretic, this
  module falls back to the same carrier-supplied anchor the oblique cases
  already use (``_person_number_anchor_present``, reused rather than
  re-derived -- a second pronoun or unambiguous possessive elsewhere in the
  sentence sharing the identical (person, number) is what turns "sie/Sie/
  wir all fit" back into "only this one does", exactly the same mechanism
  that already rescues an oblique pronoun): "Sie haben ... geschickt, obwohl
  Sie ... waren." is rescued by its own second "Sie" (same (3, Plur) cell);
  "Sie haben ... geholfen, weil die Gäste ... nicht gefunden haben." has no
  second matching pronoun anywhere and stays flagged.

  An ACCUSATIVE or DATIVE personal pronoun has no such built-in anchor: the verb
  does not care which person its object refers to, so every other pronoun of
  the same case is, by default, equally grammatical (the report's own
  "schmeckte ___ ausgezeichnet" example: mir/ihm/ihr/uns/ihnen all fit).
  Before skipping, this module looks for a CARRIER-SUPPLIED anchor -- another
  token elsewhere in the sentence (a personal pronoun, or an unambiguous
  possessive determiner) carrying the identical (Person, Number) -- because
  that is what turns a free choice back into a forced one (the report's own
  solvable counter-examples: "Sie ... verstehen müssen" forces "Ihnen"; "...
  für meinen Aufsatz ..." forces "mir"). See ``_person_number_anchor_present``
  for exactly what counts as an anchor and why the possessive stems it
  accepts are deliberately a narrow subset.

  A cue does NOT rescue a pronoun candidate the way it rescues a modal or a
  plural noun, and ``selectors.py`` never puts one on a ``personal_pronoun``
  candidate in the first place -- deliberately, not by omission. A cue
  supplies the LEXEME the gap tests; a personal pronoun's whole difficulty
  is never which lexeme (there is exactly one pronoun paradigm), it is which
  CELL of that one paradigm (which person, which number) the gap wants, and
  a "citation form" for a pronoun is not a coherent idea the way an
  infinitive or a nominative singular is. A bracketed cue here would either
  restate the missing referent directly (indistinguishable from just
  answering the gap) or be a meaningless aside -- there is no third option
  where it narrows the choice without giving it away. Do not extend the cue
  rescue to this kind on the strength of the modal/plural-noun precedent
  alone; the ambiguity a cue resolves for those two is not the same
  ambiguity this kind has.

* ``plural_noun`` -- **ambiguous, UNLESS the candidate carries a cue, in
  which case it passes.** Not one of the closed classes this module
  otherwise reuses from ``paradigms.py`` (a plural noun is an open class,
  not a small enumerable paradigm), but structurally the same defect as the
  unrescued modal case above: with no cue, a blanked plural noun slot admits
  any plural noun that satisfies the governing determiner's case/gender/
  number, which is nearly always more than one real noun ("meine ___" fits
  "Zähne", "Hände", "Schuhe", "Haare" alike).

  ``Candidate.cue`` (docs/audits/cycle-04-report.md recommendation 5) is the
  derived singular citation form ("meine ___ (Zahn)." -> "Zähne"), supplying
  the one fact "meine ___" alone does not: which noun. As with the modal
  case, only an actually-supplied cue rescues the candidate (``None`` still
  falls through to the always-ambiguous skip) -- see
  ``selectors._plural_noun_cue`` for the cases the tagger's own lemma cannot
  be trusted for (an invariant plural, a Dative plural, or any lemma that
  did not reduce to a real citation form), which still leave a
  ``nomen_plural`` candidate genuinely unrescuable and skipped exactly as
  before this cue mechanism existed.

* ``verb_form`` -- **ambiguous, UNLESS the candidate carries a cue, in
  which case it passes.** docs/audits/cycle-05-report.md: exactly the
  ``plural_noun`` defect one part of speech over, and strictly worse -- a
  full lexical verb (``verb_praesens_regelm``, ``verb_praesens_
  vokalwechsel``, ``verben_trennbar_praesens``, ``praeteritum_vollverben``;
  every OTHER topic that resolves through a finite verb -- Perfekt, Passiv,
  Futur, Konjunktiv II -- blanks the closed-class auxiliary/modal instead,
  which is ``irregular_aux``, not this kind) is an OPEN class: with no cue,
  an ordinary sentence's finite-verb slot is nearly always satisfied by more
  than one semantically distinct verb ("Unser Chef ___ ein großes
  Sommerfest" -- plant/organisiert/veranstaltet/feiert all fit equally; the
  report's own contrast in the same pilot run: ``verb_sein_haben``,
  ``praeteritum_sein_haben_modal`` and ``perfekt_haben`` were clean at 20/20
  each because the construction itself forces sein/haben/werden, while
  ``verb_praesens_regelm`` and ``praeteritum_vollverben`` were defective at
  20/20 each because any verb fits).

  ``Candidate.cue`` is the derived infinitive citation form ("___ (gehen)."
  -> "ging"), supplying the one fact the bare slot does not: which verb. As
  with the modal/plural-noun cases, only an actually-supplied cue rescues
  the candidate (``None`` still falls through to the always-ambiguous skip)
  -- see ``selectors._lexical_verb_lemma_trustworthy`` for the cases the
  tagger's own lemma cannot be trusted for (not infinitive-shaped, or a
  lemma confirmed mislemmatised for this exact model -- "schalen" for
  "schalte", where even the ROUND-TRIP reconstruction check earlier in
  ``blanker.py`` cannot catch the error because it regenerates the same
  wrong answer the tagger already committed to), which still leave a
  ``verb_form`` candidate genuinely unrescuable and skipped exactly as
  before this cue mechanism existed. Not extended to ``personal_pronoun``
  above on the strength of this precedent: a cue supplies the LEXEME a free
  choice among verbs (or among plural nouns, or among modals) is missing,
  and a personal pronoun's ambiguity is never over which lexeme -- see that
  kind's own paragraph above for why a cue is not a coherent idea there at
  all, not merely withheld.

* ``determiner`` -- **ambiguous, UNLESS the candidate carries a cue or the
  selector already established an unconditional lexeme anchor, in which
  case it passes.** docs/audits/cycle-07-report.md section A: cycle 4's own
  claim for this kind (see the superseded paragraph this replaces, kept
  below for the record) proved half right -- a determiner slot's CASE and
  GENDER genuinely are forced by agreement with a governing noun or
  preposition, exactly as claimed, but the FAMILY (definite article vs.
  possessive vs. demonstrative vs. an indefinite-family word) is a free
  lexical choice almost everywhere one of these topics blanks one ("Nach
  der Arbeit" also admits "meiner"/"dieser"/"jeder" -- same case, same
  gender, different lexeme entirely). ``Candidate.cue``
  (``selectors._determiner_cue``) is the determiner's own Nominative
  citation form, agreeing with the HEAD NOUN's gender/number -- the fact
  "der Arbeit" alone does not supply: which FAMILY the word belongs to,
  leaving only the case-form inflection for the learner to work out.
  ``Candidate.lexeme_anchored`` is the narrower, unconditional escape hatch
  for the two selectors whose own forcing anchor already rules out every
  rival family, not merely the blanked cell (``_select_artikel_unbestimmt_
  kein_nom``'s causal ``weil``-clause negation, and ``_select_artikel_
  possessiv_nom``'s possessive-person anchor, out of this report's own
  scope and left exactly as it already behaved) -- see ``Candidate.
  lexeme_anchored``'s own docstring for why ``artikel_bestimmt_nom`` is
  deliberately NOT in that set even though it also has a forcing anchor:
  its own anchor only rules out the indefinite family, not a possessive or
  demonstrative one, so it still needs (and, because its own blanked cell
  is already Nominative, can never receive) a cue -- see
  ``selectors._determiner_cue``'s own docstring for why that is the correct
  behaviour, not a bug, and docs/audits/cycle-07-report.md's own count of
  how many items this costs that topic.

* ``adjective`` -- **ambiguous, UNLESS the candidate carries a cue, in
  which case it passes.** docs/audits/cycle-07-report.md section B: the
  ENDING an attributive adjective takes is forced by the governing
  determiner's own declension (weak/mixed/strong), exactly as cycle 4
  claimed, but the ADJECTIVE ITSELF is open-class ("eine ___ Tasse Tee"
  admits warme/große/volle/heiße/frische alike) -- structurally the same
  defect ``plural_noun``/``verb_form`` already have, one part of speech
  over. ``Candidate.cue`` (``selectors._adjective_selector``'s own cue
  computation) is the adjective's uninflected positive base form -- its own
  lemma, never a separate lookup, since an ``ADJA`` token's lemma is
  already that base form. A comparative or superlative attributive
  adjective is excluded from candidacy entirely rather than cued (its
  lemma is shared with the positive -- "bester" and "guter" both lemmatise
  to "gut" -- so a cue would not distinguish them; that ambiguity belongs to
  ``adjektiv_komparativ_superlativ`` instead). The two attributive-
  participle topics (``partizip_i_attributiv``/``partizip_ii_attributiv_
  erweitert``) already cued with the underlying infinitive before this
  report and are unaffected by this bullet's own addition -- they were
  cycle 4's own proof this mechanism works, reused here for the ordinary
  adjective case instead of a participle.

* Every remaining kind (``degree``, ``reflexive_pronoun``,
  ``relative_pronoun``, ``fixed_particle``) -- **passes unaffected.** Their
  own paradigm cell (Case/Gender/Number, or Person/Number for a reflexive/
  relative pronoun, or the comparative/superlative alternation itself for
  ``degree``) is forced by agreement with a governing noun, preposition, or
  antecedent that this module does not re-derive, or (``degree``) already
  gated by its own selector's forcing element (an explicit "als", or the
  fused "am" particle) and its own cue where the tagger's lemma can be
  trusted. Nothing observed in the cycle-4 or cycle-7 audits contradicts
  this for these kinds; if a future audit finds otherwise for one of them,
  this policy table is where that finding lands, not a silent guess bolted
  onto ``blanker.py``.

  **Superseded claim, kept for the historical record cycle-7 itself asks
  for (its own section 2's closing paragraph: "this is my error, not the
  pipeline's"):** cycle 4 originally placed ``determiner`` and ``adjective``
  in this same "passes unaffected" bucket, reasoning that agreement with a
  governing noun/preposition leaves only one grammatical filler. That
  reasoning is correct for CASE and GENDER, which is all cycle 4's own audit
  had tested for -- it does not extend to LEXEME choice within a family that
  shares the same case and gender, which is what cycle 7's full hand-audit
  (370 of 370 items, not a sample) found instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.generation.blanking import paradigms
from src.generation.blanking.selectors import _MODAL_INFINITIVE_TAGS, Candidate, _clause_span
from src.generation.blanking.sentence_tagger import TaggedSentence, Token

# Possessive-determiner stems (paradigms.match_ein_word's own stem list)
# whose Person/Number an anchor check can trust WITHOUT ambiguity. "sein"
# (his/its) and "ihr" (her/their) are deliberately excluded even though they
# are grammatically 3rd person: both are genuinely syncretic across more than
# one (Person, Number) reading, and a wrong anchor here would let a real
# duplicate answer through -- the one failure mode this whole module exists
# to prevent. Losing a handful of otherwise-anchorable 3rd-person items to
# this caution is the cheap failure (module docstring: a skip costs nothing);
# a false anchor is not.
_UNAMBIGUOUS_POSSESSIVE_STEM_PERSON_NUMBER: dict[str, tuple[str, str]] = {
    "mein": ("1", "Sing"),
    "dein": ("2", "Sing"),
    "unser": ("1", "Plur"),
    "euer": ("2", "Plur"),
    "eur": ("2", "Plur"),
}

# The five modals plus the frozen sixth ("möchten") -- exactly
# ``paradigms.MODAL_LEMMAS | {"möchten"}``, the same set
# ``selectors.SELECTORS["modalverben_praesens"]`` matches against, reused
# here rather than redeclared (module docstring: no new linguistic facts).
_MODAL_LEMMAS: frozenset[str] = paradigms.MODAL_LEMMAS | frozenset({"möchten"})


@dataclass(frozen=True)
class UniquenessOutcome:
    """Whether ``candidate`` is the unique grammatical filler of its own
    slot -- ``unique=False`` always carries a ``reason``, ``unique=True``
    never does, mirroring ``blanker.BlankOutcome``'s own either/or shape."""

    unique: bool
    reason: str | None


def _is_modal_lemma(lemma: str | None) -> bool:
    return lemma is not None and lemma in _MODAL_LEMMAS


def _clause_participle(sentence: TaggedSentence, candidate: Candidate) -> Token | None:
    """The Partizip II (``VVPP``) sharing ``candidate``'s own clause, if any
    -- scanned in BOTH directions, since Plusquamperfekt's subordinate-clause
    word order puts it BEFORE the auxiliary ("nachdem er gegessen HATTE"),
    the reverse of every other compound-tense/passive shape this module
    covers. Clause-bounded (``selectors._clause_span``, the same comma-proxy
    boundary every other clause-scoped check in this package already uses),
    not sentence-wide, so a participle belonging to a DIFFERENT clause is
    never mistaken for this candidate's own."""
    start, end = _clause_span(sentence, candidate.token_index)
    for token in sentence.tokens[start:end]:
        if token.tag == "VVPP":
            return token
    return None


def _clause_infinitive_after(sentence: TaggedSentence, candidate: Candidate) -> Token | None:
    """A bare infinitive (``VVINF``/``VAINF``) later in ``candidate``'s own
    clause -- the Futur I/II shape ("wird ... lesen", "wird ... gelesen
    haben"), clause-bounded for the same reason ``_clause_participle`` is:
    an infinitive belonging to a different, unrelated clause must never
    license this candidate the way its own clause's infinitive does."""
    start, end = _clause_span(sentence, candidate.token_index)
    for token in sentence.tokens[candidate.token_index + 1 : end]:
        if token.tag in _MODAL_INFINITIVE_TAGS:
            return token
    return None


def _tense_forced_by_construction(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether ``candidate``'s own tense is already fixed by the SHAPE of
    the construction it belongs to, independent of anything else in the
    carrier -- the third anchor kind docs/audits/cycle-06-report.md's task 1
    names ("a perfect or pluperfect construction that fixes it"), generalised
    to every shape this package selects that has the same property: no
    alternate-tense reading of the SAME construction is itself grammatical,
    so there is nothing for a carrier-level anchor to rule out in the first
    place.

    * Konjunktiv II (``tense_mood == "SubjII"``, all three of
      ``konjunktiv_ii_hoeflichkeit``/``_irreal_gegenwart``/``_vergangenheit``):
      German's synthetic/periphrastic Konjunktiv II ("hätte"/"wäre"/
      "könnte"/"würde", with or without a following participle) has no
      separate present-vs-past INDICATIVE-shaped rival at all -- the mood
      itself, not a carrier fact, is what is fixed here, so it needs no
      anchor of the kind this module can check.
    * ``werden`` followed by a bare infinitive later in its own clause
      (``_clause_infinitive_after``): Futur I ("wird ... lesen") or Futur II
      ("wird ... gelesen haben") -- German has no ordinary indicative
      construction built from "wurde" (Präteritum) plus a trailing
      infinitive with the same meaning, so there is no rival tense reading
      of this exact shape to rule out.
    * ``sein``/``haben`` with a Partizip II sharing the same clause
      (``_clause_participle``): Perfekt or Plusquamperfekt. ``haben`` is
      never a Zustandspassiv auxiliary (only ``sein`` is), so any
      haben+participle candidate is unambiguously this shape. For ``sein``,
      the participle's own lemma decides which of the two disjoint-by-
      construction sets (``paradigms.AUX_SEIN_LEMMAS`` for Perfekt-mit-sein/
      Plusquamperfekt-mit-sein, ``paradigms.TRANSITIVE_LEMMAS`` for
      Zustandspassiv) it belongs to -- see paradigms.py's own module-level
      comment for why the two are disjoint by construction, not merely by
      observation. Zustandspassiv itself is NOT exempted here: it is
      exactly docs/audits/cycle-06-report.md's task 1 worked example
      ("Das Buffet ist/war bereits gut geplant" -- both indicative tenses
      genuinely fit), so it must still clear the general anchor check below.

    A bare ``sein``/``haben``/``werden`` finite form with none of these
    shapes (``verb_sein_haben``, ``praeteritum_sein_haben_modal``'s own
    sein/haben half, plain Passiv, plain Zustandspassiv) returns ``False``
    -- exactly the population this task exists to close."""
    if candidate.tense_mood == "SubjII":
        return True
    if candidate.lemma == "werden":
        return _clause_infinitive_after(sentence, candidate) is not None
    if candidate.lemma in ("sein", "haben"):
        participle = _clause_participle(sentence, candidate)
        if participle is None:
            return False
        if candidate.lemma == "haben":
            return True
        part_lemma = participle.lemma.lower()
        return part_lemma in paradigms.AUX_SEIN_LEMMAS
    return False


def _sibling_clause_tense_anchor(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether another clause's own finite verb forces ``candidate``'s
    tense through subordination -- docs/audits/cycle-06-report.md task 1's
    "another finite verb in the sentence whose tense the gap must agree
    with" anchor, and the mechanism behind its own worked example: "Obwohl
    die neuen Vorschriften sehr streng ___, bitten wir um Ihr Verständnis."
    forces "sind" because the "obwohl" clause describes the SAME present
    state the present-tense matrix clause is talking about.

    Deliberately narrow, on both sides, to avoid the report's OTHER worked
    example turning into a false anchor: "Das Buffet ___ bereits gut
    geplant, aber die Getränke müssen wir noch einkaufen." has a present-
    tense finite verb ("müssen") in its other clause too, yet "war" fits
    there just as well as "ist" -- because "aber" COORDINATES two
    independent main clauses with no tense relationship between them at
    all, unlike a genuine subordinate clause.

    * The sentence must contain no coordinating conjunction (``KON``) at
      all -- ruling out exactly the "aber" shape above, and any other
      sentence mixing coordination with subordination this module cannot
      safely reason about.
    * The sentence must contain a subordinating conjunction from
      ``paradigms.TENSE_CONCORDANT_SUBORDINATORS`` -- the narrow, checked
      subset of German subordinators whose own clause conventionally
      shares the matrix clause's tense (see that constant's own comment for
      why "nachdem"/"bevor"/"als"/"wenn" are deliberately excluded).
    * The OTHER clause (everything outside ``candidate``'s own
      ``_clause_span``) must contain EXACTLY ONE finite verb, with a
      resolvable ``Tense`` equal to ``candidate``'s own -- more than one
      finite verb there means which one would govern is not determined,
      and "reject rather than guess" applies exactly as it does everywhere
      else in this module."""
    if candidate.tense_mood not in ("Pres", "Past"):
        return False
    if any(t.pos == "KON" for t in sentence.tokens):
        return False
    if not any(t.pos == "SCONJ" for t in sentence.tokens):
        return False
    start, end = _clause_span(sentence, candidate.token_index)
    other = sentence.tokens[:start] + sentence.tokens[end:]
    finite = [t for t in other if t.morph.get("VerbForm") == "Fin"]
    if len(finite) != 1:
        return False
    return finite[0].morph.get("Tense") == candidate.tense_mood


def _temporal_expression_anchor(sentence: TaggedSentence) -> bool:
    """Whether an unambiguous German time expression
    (``paradigms.TEMPORAL_ANCHOR_LEMMAS``) appears anywhere in the sentence
    -- docs/audits/cycle-06-report.md task 1's first anchor kind ("gestern",
    "jetzt", "letzte Woche", "morgen", ...). Checked by surface text, not
    lemma: every member of that closed list is already an invariant,
    uninflected adverb or a bare nominal used adverbially, so there is no
    inflection for a lemma lookup to normalise away."""
    return any(t.text.lower() in paradigms.TEMPORAL_ANCHOR_LEMMAS for t in sentence.tokens)


def _auxiliary_tense_anchor_present(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """The top-level entry point for the ``irregular_aux`` (non-modal)
    tense-anchor check: ``True`` when the tense is fixed by the
    construction's own shape, or by an explicit time expression, or by a
    genuinely subordinated sibling clause -- ``False``, "no anchor, no
    item", otherwise."""
    if _tense_forced_by_construction(sentence, candidate):
        return True
    if _temporal_expression_anchor(sentence):
        return True
    return _sibling_clause_tense_anchor(sentence, candidate)


def _person_number_anchor_present(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether some token OTHER than the blanked one itself carries the
    identical (Person, Number) as ``candidate`` -- another personal pronoun,
    or a possessive determiner from the narrow unambiguous-stem set above.
    That match is what lets the carrier itself fix who is meant (module
    docstring's "Sie" -> "Ihnen", "meinen Aufsatz" -> "mir" examples)
    instead of leaving the referent free among every other member of the
    pronoun paradigm. Reflexive pronouns are not treated as an anchor: "sich"
    corefers with its own clause's subject, which is a different fact than
    "some other pronoun in this sentence has the same person as the blank"."""
    assert candidate.person is not None and candidate.number is not None
    target = (candidate.person, candidate.number)
    for token in sentence.tokens:
        if token.i == candidate.token_index:
            continue
        if token.pos == "PRON" and token.morph.get("Reflex") != "Yes":
            if (token.morph.get("Person"), token.morph.get("Number")) == target:
                return True
        if token.tag == "PPOSAT" and token.morph.get("Poss") == "Yes":
            match = paradigms.match_ein_word(token.text)
            if match is None:
                continue
            stem, _ending = match
            if _UNAMBIGUOUS_POSSESSIVE_STEM_PERSON_NUMBER.get(stem) == target:
                return True
    return False


def _clause_finite_verb(sentence: TaggedSentence, index: int) -> Token | None:
    """The single finite verb sharing ``index``'s own clause
    (``_clause_span``), or ``None`` if the clause has none or more than one
    -- "reject rather than guess" applied to the same "exactly one finite
    verb decides this" signal ``_governing_verb_lemma``/
    ``_sibling_clause_tense_anchor`` already use elsewhere in this package,
    reused here to find the verb whose ``Tense`` decides whether a
    Nominative pronoun candidate's own (person, number) is syncretic
    (``_nominative_pronoun_syncretic``)."""
    start, end = _clause_span(sentence, index)
    finite = [t for t in sentence.tokens[start:end] if t.morph.get("VerbForm") == "Fin"]
    return finite[0] if len(finite) == 1 else None


def _nominative_pronoun_syncretic(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether ``candidate``'s own (person, number) shares an identical
    finite-verb form with another cell of the German personal-pronoun
    paradigm, docs/audits/cycle-07-report.md section C's sixth uniqueness
    reason -- see the module docstring's ``personal_pronoun`` bullet for the
    two closed conjugation facts this checks:

    * ``(1, Plur)`` or ``(3, Plur)`` -- the wir/sie/Sie three-way ambiguity,
      true in EVERY tense, so no verb lookup is needed to confirm it.
    * ``(1, Sing)`` or ``(3, Sing)`` in the Präteritum or Konjunktiv II
      (both surface as ``Tense=Past`` in this tagger's own morphology) --
      NOT in the Präsens, where "ich mache"/"er macht" genuinely differ, so
      this branch looks up the clause's own finite verb before deciding.
      A clause whose finite verb cannot be resolved to exactly one token
      (``_clause_finite_verb`` returns ``None``) is treated as NOT
      syncretic here -- the pre-existing, unconditional trust this module
      already gave every Nominative pronoun before this reason existed, kept
      as the fallback rather than guessed into a new skip."""
    assert candidate.person is not None and candidate.number is not None
    if candidate.number == "Plur" and candidate.person in ("1", "3"):
        return True
    if candidate.number == "Sing" and candidate.person in ("1", "3"):
        verb = _clause_finite_verb(sentence, candidate.token_index)
        return verb is not None and verb.morph.get("Tense") == "Past"
    return False


def check_uniqueness(sentence: TaggedSentence, candidate: Candidate) -> UniquenessOutcome:
    """Whether ``candidate``'s blanked token is the only member of its own
    closed class that would also be grammatical in this slot -- see the
    module docstring's policy table for the reasoning behind each kind."""
    if candidate.kind == "irregular_aux" and _is_modal_lemma(candidate.lemma):
        # A cue names the lexeme -- exactly the fact a free choice among
        # modals is missing (module docstring). It does NOT apply to
        # personal_pronoun below: that kind's ambiguity is over which CELL
        # of one paradigm, not which lexeme, so a cue would not resolve it.
        if candidate.cue:
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "modal_verb_interchangeable")

    if candidate.kind == "irregular_aux":
        # sein/haben/werden reached here (the modal branch above already
        # returned for a modal lemma) -- docs/audits/cycle-06-report.md task
        # 1: a cue does NOT rescue this the way it rescues a modal, exactly
        # the personal_pronoun argument below applies here too (the
        # ambiguity is over which CELL of the one paradigm, not which
        # lexeme), so this branch never even looks at ``candidate.cue``.
        if _auxiliary_tense_anchor_present(sentence, candidate):
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "auxiliary_tense_unanchored")

    if candidate.kind == "personal_pronoun":
        assert candidate.token_index < len(sentence.tokens)
        token = sentence.tokens[candidate.token_index]
        if token.morph.get("Case") == "Nom":
            # The sentence's own (unchanged) finite verb already forces this
            # -- UNLESS that verb's own (person, number) is itself
            # syncretic across more than one pronoun (docs/audits/
            # cycle-07-report.md section C), in which case the same
            # carrier-supplied anchor the oblique cases already use is
            # required. See module docstring.
            if _nominative_pronoun_syncretic(sentence, candidate):
                if _person_number_anchor_present(sentence, candidate):
                    return UniquenessOutcome(True, None)
                return UniquenessOutcome(False, "nominative_pronoun_syncretic")
            return UniquenessOutcome(True, None)
        if _person_number_anchor_present(sentence, candidate):
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "personal_pronoun_unanchored")

    if candidate.kind == "plural_noun":
        # Same cue rescue as the modal case above, for the same reason: a
        # cue names the lexeme (here, the singular) a free choice among
        # plural nouns is missing (module docstring).
        if candidate.cue:
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "plural_noun_open_class")

    if candidate.kind == "verb_form":
        # docs/audits/cycle-05-report.md: exactly the ``plural_noun`` defect
        # one part of speech over, and strictly worse -- a full lexical verb
        # is an open class too, and an ordinary sentence's finite-verb slot
        # is nearly always satisfied by more than one semantically distinct
        # verb ("Unser Chef ___ ein großes Sommerfest" -- plant/organisiert/
        # veranstaltet/feiert all fit equally). Same cue rescue as the
        # modal/plural-noun cases above and for the same reason: a cue names
        # the lexeme (the infinitive) a free choice among verbs is missing.
        if candidate.cue:
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "verb_lexical_open_class")

    if candidate.kind == "determiner":
        # docs/audits/cycle-07-report.md section A: a determiner's own
        # FAMILY (definite/indefinite/negative/possessive) is a free
        # lexical choice, not forced by case/gender agreement alone -- a
        # cue names the family, and ``lexeme_anchored`` marks the two
        # selectors whose own forcing anchor already rules out every rival
        # family unconditionally. See module docstring.
        if candidate.lexeme_anchored:
            return UniquenessOutcome(True, None)
        if candidate.cue:
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "determiner_family_interchangeable")

    if candidate.kind == "adjective":
        # docs/audits/cycle-07-report.md section B: an attributive
        # adjective's ENDING is forced by the governing determiner's
        # declension, but the adjective itself is open-class -- same cue
        # rescue as ``plural_noun``/``verb_form`` above, for the same
        # reason. The two attributive-participle topics were already cued
        # before this report and pass here exactly as before.
        if candidate.cue:
            return UniquenessOutcome(True, None)
        return UniquenessOutcome(False, "adjective_lexeme_open_class")

    return UniquenessOutcome(True, None)
