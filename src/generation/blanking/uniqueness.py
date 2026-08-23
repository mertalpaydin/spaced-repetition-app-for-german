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
     and Perfekt (``haben``/``sein`` plus a Partizip II in the same clause,
     with the candidate itself in the PRESENT-tense aux cell -- aux choice
     and Zustandspassiv-vs-Perfekt both already decided by the participle's
     own lemma against ``paradigms.AUX_SEIN_LEMMAS``/``TRANSITIVE_LEMMAS``,
     the same disjoint pair ``selectors.py`` itself uses). Zustandspassiv is
     deliberately NOT in this list -- it is the report's own worked
     example, and a participle alone does not fix its tense (a
     Zustandspassiv participle's lemma is never in ``AUX_SEIN_LEMMAS``, so
     it falls through to anchor 2 or 3 like any other bare aux).

     Plusquamperfekt (the SAME shape, candidate in the PAST-tense aux cell
     -- "hatte"/"hatten"/"war"/"waren") is deliberately EXCLUDED from this
     anchor as of docs/audits/cycle-09-report.md 1.6: a participle in the
     clause proves the construction is Perfekt-or-Plusquamperfekt, not
     which of the two, and "er hatte gegessen" reads exactly as naturally
     as "er hat gegessen" with nothing else in the sentence to decide
     between them -- the pilot's own verifier caught this confusion 15
     times ("hatte" vs "habe", "war" vs "bin", "hatten" vs "haben"). This
     population is routed to its own narrower anchor instead; see the
     dedicated bullet below.
  2. **An explicit time expression** (``_temporal_expression_anchor``) --
     ``paradigms.TEMPORAL_ANCHOR_LEMMAS``, the same closed list
     ``scripts/check_gold_examples.py`` already uses for its own
     ``temporal_anchor`` forcing-element check, reused here rather than
     redeclared. Never reached by a Plusquamperfekt candidate (see below).
  3. **A genuinely subordinated sibling clause with matching tense**
     (``_sibling_clause_tense_anchor``) -- the report's own "Obwohl die
     neuen Vorschriften sehr streng ___, bitten wir um Ihr Verständnis."
     example: the present-tense matrix clause ("bitten wir") forces "sind",
     not "waren", because the "obwohl" clause describes the SAME state.
     Narrowly scoped (see that function's own docstring) specifically so
     the report's OTHER example does not turn into a false anchor: "aber"
     COORDINATES two independent clauses with no tense relationship at all,
     which a naive "some other finite verb in the sentence has a matching
     Tense" check would wrongly accept. Never reached by a Plusquamperfekt
     candidate either.

  No anchor of any of the three kinds: the candidate is skipped
  (``auxiliary_tense_unanchored``), exactly the standing standard's "no
  anchor, no item".

* **Plusquamperfekt specifically** (``_is_plusquamperfekt_shape`` /
  ``_has_anteriority_marker``, docs/audits/cycle-09-report.md 1.6) --
  excluded from anchor 1 above for the reason just given, and deliberately
  NOT routed to anchor 2 either: that list's "zuvor"/"davor"/"damals"/
  "vorher"/"schließlich" entries are bare adverbs that state WHEN, not that
  one event precedes another, and the verifier caught exactly those
  standing in for a real anchor ("Kurz zuvor ___ ich ... gelaufen." and
  "Davor ___ ich ... gehängt." were both rejected as equally natural in
  Perfekt). The real anchor is narrower: an explicit ``bevor``/``nachdem``
  clause naming the ordering directly, or a genuine second past-tense
  event in another clause (``_has_anteriority_marker``'s own docstring has
  both worked examples from the accepted pilot set). No anchor of either
  kind: skipped, same as any other unanchored aux.

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

  Those two closed facts were the whole of this check until cycle 11
  measured what they miss: modals ("ich muss"/"er muss", identical in the
  PRESENT, which a tense-based test cannot see) and gender ("er"/"sie"/
  "es", which no German verb form distinguishes in any tense). See
  ``_nominative_pronoun_settled``, which replaced them with a direct
  paradigm lookup. Where the verb form does NOT settle the pronoun, this
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
  (``selectors._determiner_cue``) is, as of the owner's TODO.md 2.1
  decision, the determiner FAMILY's own invariant citation form (always
  "der"/"ein"/"kein", or the possessive's own uninflected stem) -- the fact
  "der Arbeit" alone does not supply: which FAMILY the word belongs to,
  leaving case AND gender for the learner to work out from the sentence
  itself (superseding an earlier design that agreed the cue to the head
  noun's own gender, handing that fact over for free -- see ``selectors.
  _determiner_cue``'s own docstring for why, and for the confirmed defect
  that design had independently of the owner's decision to replace it).
  Present for essentially every candidate of this kind now, definite/
  indefinite/negative always and possessive whenever the token matches the
  ein-word paradigm at all (see that function's own ``None`` case).
  ``Candidate.lexeme_anchored`` is the narrower, unconditional escape hatch
  this module trusted before a cue existed for these candidates at all
  (``_select_artikel_unbestimmt_kein_nom``'s causal ``weil``-clause
  negation, and ``_select_artikel_possessiv_nom``'s possessive-person
  anchor) -- both selectors still compute and record it, but a cue alone
  already passes this gate now, so it is no longer load-bearing for either.
  ``artikel_bestimmt_nom`` is deliberately NOT one of the two selectors
  that sets it: its own anchor (``_definite_uniqueness_anchor``) only rules
  out the indefinite family, not a possessive or demonstrative one, and
  remains a REQUIRED gate at the selector level, unaffected by the cue rule
  change -- see that selector's own comment.

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
from src.generation.blanking.selectors import (
    _MODAL_INFINITIVE_TAGS,
    Candidate,
    _clause_span,
    _determiner_head_noun,
    _finite_verb_cell_is_unambiguous,
)
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
    -- exactly the population this task exists to close.

    docs/audits/cycle-09-report.md 1.6's own correction to this function:
    a Partizip II sharing the clause fixes the CONSTRUCTION (Perfekt-or-
    Plusquamperfekt, as opposed to Zustandspassiv/plain Passiv/bare copula),
    but it does NOT by itself decide which of THOSE TWO the candidate is --
    "er hatte gegessen" and "er hat gegessen" are both, in isolation, an
    equally natural way to say the same thing, and the pilot's own verifier
    caught this exact confusion 15 separate times ("hatte" vs "habe", "war"
    vs "bin", "hatten" vs "haben"). So this function now only claims the
    tense is forced by the shape ALONE when the candidate's own cell is
    Perfekt (``tense_mood != "Past"``, i.e. a present-tense aux) -- German's
    default, unmarked way to narrate a finished action, needing nothing
    else to license it. A Plusquamperfekt cell (``tense_mood == "Past"``,
    "hatte"/"hatten"/"war"/"waren") is never forced by the shape alone;
    ``_auxiliary_tense_anchor_present`` routes it to ``_has_anteriority_marker``
    instead, the narrower check this task adds."""
    if candidate.tense_mood == "SubjII":
        return True
    if candidate.lemma == "werden":
        return _clause_infinitive_after(sentence, candidate) is not None
    if candidate.lemma in ("sein", "haben"):
        participle = _clause_participle(sentence, candidate)
        if participle is None:
            return False
        if candidate.lemma == "haben":
            return candidate.tense_mood != "Past"
        part_lemma = participle.lemma.lower()
        if part_lemma not in paradigms.AUX_SEIN_LEMMAS:
            return False
        return candidate.tense_mood != "Past"
    return False


# The two German subordinators that introduce an explicit ANTERIORITY
# relation ("this event finished before that one") rather than a merely
# concurrent one -- the same pair ``paradigms.TENSE_CONCORDANT_SUBORDINATORS``'s
# own comment names and deliberately excludes for the opposite reason (it
# wants SAME-tense concordance, this wants a genuine tense DIFFERENCE).
# Deliberately narrower than ``paradigms.TEMPORAL_ANCHOR_LEMMAS``, whose
# broad list ("zuvor", "davor", "damals", "vorher", "schließlich"/
# "schliesslich", "sofort", "gleich" -- all bare adverbs with no clause of
# their own) is exactly what docs/audits/cycle-09-report.md 1.6 found the
# verifier catching as an insufficient anchor for the Perfekt-vs-
# Plusquamperfekt question specifically: "Kurz zuvor ___ ich ... gelaufen."
# and "Davor ___ ich ... gehängt." were both rejected with "'war'/'hatte' is
# no better than 'bin'/'habe' here" even though ``_temporal_expression_anchor``
# already treated "zuvor"/"davor" as sufficient. A bare adverb states WHEN,
# not that one event precedes another; only a subordinator that names the
# ordering, or a second clause whose own verb is independently in the past,
# does that.
_ANTERIORITY_SUBORDINATORS: frozenset[str] = frozenset({"bevor", "nachdem"})


def _is_plusquamperfekt_shape(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether ``candidate`` is exactly the population
    ``_tense_forced_by_construction`` now declines to anchor by shape alone:
    a ``sein``/``haben`` finite form in the Past cell (Plusquamperfekt),
    sharing its clause with a Partizip II that is not, for ``sein``, a
    Zustandspassiv participle (``paradigms.AUX_SEIN_LEMMAS`` decides that the
    same way ``_tense_forced_by_construction`` does). Used by
    ``_auxiliary_tense_anchor_present`` to route this specific population to
    ``_has_anteriority_marker`` instead of the generic, too-broad
    ``_temporal_expression_anchor`` fallback."""
    if candidate.lemma not in ("sein", "haben") or candidate.tense_mood != "Past":
        return False
    participle = _clause_participle(sentence, candidate)
    if participle is None:
        return False
    if candidate.lemma == "sein" and participle.lemma.lower() not in paradigms.AUX_SEIN_LEMMAS:
        return False
    return True


def _has_anteriority_marker(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """The narrow anchor a Plusquamperfekt candidate actually needs
    (docs/audits/cycle-09-report.md 1.6): either an explicit ``bevor``/
    ``nachdem`` clause naming the ordering directly, or a genuine second
    past-tense event -- another clause, outside ``candidate``'s own
    (``_clause_span``), with its own finite verb tagged ``Tense=Past``. Both
    of ``_tense_forced_by_construction``'s Past-tense examples in the
    accepted pilot set are the ``bevor`` shape ("Bevor der Unterricht ...
    anfing, ___ ich meinen ... Rucksack ... gestellt."); the narrative-past
    shape ("In der Hand hielt sie das Portemonnaie, das ihr Onkel ...
    geschenkt ___.") is the second clause with its own past-tense verb.
    Deliberately does NOT fall back to ``paradigms.TEMPORAL_ANCHOR_LEMMAS``
    -- see ``_ANTERIORITY_SUBORDINATORS``'s own comment for why that list is
    exactly what this task tightens against."""
    if any(t.text.lower() in _ANTERIORITY_SUBORDINATORS for t in sentence.tokens):
        return True
    start, end = _clause_span(sentence, candidate.token_index)
    other = sentence.tokens[:start] + sentence.tokens[end:]
    return any(t.morph.get("VerbForm") == "Fin" and t.morph.get("Tense") == "Past" for t in other)


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
    item", otherwise.

    A Plusquamperfekt candidate (``_is_plusquamperfekt_shape``) is routed to
    ``_has_anteriority_marker`` ALONE, never to the two generic fallbacks
    below it -- docs/audits/cycle-09-report.md 1.6's finding was precisely
    that ``_temporal_expression_anchor``'s broad adverb list was standing in
    for that narrower check and passing sentences the verifier then caught,
    so letting this population fall through to it again would silently
    undo the fix."""
    if _tense_forced_by_construction(sentence, candidate):
        return True
    if _is_plusquamperfekt_shape(sentence, candidate):
        return _has_anteriority_marker(sentence, candidate)
    if _temporal_expression_anchor(sentence):
        return True
    return _sibling_clause_tense_anchor(sentence, candidate)


def _futur_i_shape(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether ``candidate`` (an ``irregular_aux`` candidate whose lemma is
    ``werden``) is genuinely Futur I: a bare infinitive later in its own
    clause, with no participle -- Futur II's own shape, checked and
    excluded first so this never doubles up with that construction.

    Scoped to ``tense_mood == "Pres"`` (indicative present "werden"), not
    merely to the surface shape "werden-family form + bare infinitive
    later in the clause": Konjunktiv II politeness ("Würden Sie mir bitte
    das Fenster öffnen?", ``tense_mood == "SubjII"``) has the identical
    surface shape but is not interchangeable with a modal at all -- "Würden
    Sie...?" is a fixed, conventionalised polite-request formula with no
    modal-verb paraphrase that keeps the same register, unlike Futur I's
    genuine free choice against the whole modal set. Regression found by
    running this check against the real pipeline: without this restriction
    it also rejected the konjunktiv_ii_hoeflichkeit item built from that
    exact sentence."""
    if candidate.lemma != "werden" or candidate.tense_mood != "Pres":
        return False
    if _clause_participle(sentence, candidate) is not None:
        return False
    return _clause_infinitive_after(sentence, candidate) is not None


def _futur_i_modal_interchangeable(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """docs/audits/cycle-07-report.md defect 12: "werden" plus a bare
    infinitive (Futur I) is not merely a tense choice the way a bare "ist"/
    "war" is (the previous bullet's own question) -- it is ALSO, separately,
    a free LEXICAL choice against the entire modal set. "Obwohl die
    Bearbeitungszeit kurz ist, ___ Sie den Termin einhalten." accepts
    "können"/"müssen"/"wollen"/"sollen" exactly as well as "werden": nothing
    about the construction's own shape rules a modal out the way it does
    for "wurde + infinitive" against Futur's OWN Präteritum reading
    (``_tense_forced_by_construction``'s own claim, which is real but
    narrower than "no rival LEXEME exists at all"). The modal-
    interchangeability check (the very first branch of
    ``check_uniqueness``) never fires here because the answer itself is
    "werden", not a modal -- this is the same ambiguity one lexeme over.

    Rescued exactly the way the modal branch rescues itself: an explicit
    FUTURE time anchor (``paradigms.TEMPORAL_ANCHOR_LEMMAS``, the same
    closed list ``_temporal_expression_anchor`` already uses) makes "werden"
    the only construction that actually commits to that future reading --
    "Ich werde morgen kommen." forces Futur specifically, because a modal
    alone ("Ich kann morgen kommen.") states ability/permission, not a
    scheduled future event, which is a real, if softer, semantic
    difference "morgen" resolves. With no such anchor, "werden" and the
    modal set are interchangeable and neither the module's cue mechanism
    (``selectors.py`` never puts one on this kind, see the ``irregular_aux``
    bullets above) nor anything else in this pipeline rescues it."""
    return _futur_i_shape(sentence, candidate) and not _temporal_expression_anchor(sentence)


def _possessive_governs_a_subject(sentence: TaggedSentence, token: Token) -> bool:
    """Whether the possessive determiner ``token`` sits inside the SUBJECT
    noun phrase of its own clause -- i.e. its head noun (``selectors.
    _determiner_head_noun``, the same walk that already derives a
    determiner's cue from its head noun elsewhere in this package) is itself
    tagged ``Case=Nom``.

    docs/audits/cycle-07-report.md defects 10 and 11: "Mein bester Freund
    Timo hat ___ gestern ein sehr gutes Buch geschenkt." wrongly anchors
    "mir" because "Mein" (1st-singular-possessor) sits somewhere in the
    sentence with the right (Person, Number) -- but "Mein" possesses the
    SUBJECT ("mein Freund", who does the giving), which says nothing at all
    about the person of the DATIVE OBJECT (the recipient: "dir"/"ihm"/"ihr"/
    "uns"/"euch"/"Ihnen" all fit exactly as well as "mir" does here). A
    possessive only forces the referent of a DIFFERENT argument when it
    itself belongs to that argument's own noun phrase (module docstring's
    own working example, "für meinen Aufsatz", where "meinen" possesses the
    accusative object itself, not the subject) or a phrase coreferring with
    it -- never when it sits in the subject, which is a structurally
    unrelated argument of the very same clause. Conservative on an
    unresolved head noun (``None``, or a noun whose own Case did not tag):
    treated as "might be the subject", not as "safe to trust", per this
    module's own standing bias against a false anchor over a lost one."""
    head = _determiner_head_noun(sentence, token.i)
    if head is None:
        return True
    case = head.morph.get("Case")
    return case is None or case == "Nom"


def _person_number_anchor_present(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether some token OTHER than the blanked one itself carries the
    identical (Person, Number) as ``candidate`` -- another personal pronoun,
    or a possessive determiner from the narrow unambiguous-stem set above.
    That match is what lets the carrier itself fix who is meant (module
    docstring's "Sie" -> "Ihnen", "meinen Aufsatz" -> "mir" examples)
    instead of leaving the referent free among every other member of the
    pronoun paradigm. Reflexive pronouns are not treated as an anchor: "sich"
    corefers with its own clause's subject, which is a different fact than
    "some other pronoun in this sentence has the same person as the blank".

    A possessive determiner is trusted only when it does NOT govern the
    clause's own SUBJECT (``_possessive_governs_a_subject``) -- see that
    function's own docstring for docs/audits/cycle-07-report.md defects 10
    and 11, the possessive-in-the-subject-noun-phrase false anchor."""
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
            if _UNAMBIGUOUS_POSSESSIVE_STEM_PERSON_NUMBER.get(stem) != target:
                continue
            if _possessive_governs_a_subject(sentence, token):
                continue
            return True
    return False


def _nominative_pronoun_settled(sentence: TaggedSentence, candidate: Candidate) -> bool:
    """Whether the sentence itself determines WHICH subject pronoun was
    removed from a blanked Nominative slot.

    This replaces ``_nominative_pronoun_syncretic``, which asked the
    narrower question "is this a known syncretic cell" from two closed
    facts: (1|3, Plur) is always three-way ambiguous, and (1|3, Sing) is
    ambiguous in the Past. Everything else it trusted unconditionally.
    docs/audits/cycle-11-corpus-report.md found four of the eight items
    that topic shipped had more than one correct answer, and all four came
    through the gap in exactly that "everything else":

        ___ muss Tom fragen, wie ich zu seinem Haus komme.   (1, Sing), Present
        ___ hat ihren Pullover angezogen.                    (3, Sing), Present

    "muss" is spelled the same at 1st and 3rd singular, which the
    tense-based test cannot see because it is a fact about modals, not
    about the Präteritum. And no German verb form of any tense distinguishes
    "er" from "sie" from "es", so a 3rd-singular Nominative blank is never
    settled by its verb at all.

    So the question is asked directly instead. Two conditions, both
    required.

    **The clause's own finite verb must belong to this cell and no other.**
    ``selectors._finite_verb_cell_is_unambiguous`` reconstructs the verb's
    whole paradigm and looks the surface form up in it, which gets modals
    ("muss" 1st and 3rd singular), the plural syncretism ("können" 1st and
    3rd plural) and the Präteritum syncretism ("sagte") from one test
    rather than three special cases. Scoped to the pronoun's own clause:
    without that, "___ muss Tom fragen, wie ich zu seinem Haus komme."
    passes on the SUBORDINATE clause's "komme", which is uniquely 1st
    singular and says nothing about the main clause's subject.

    **The pronoun must not be 3rd person singular.** Recovering "er" from
    "sie" would need coreference over the antecedent, which this package
    does not have and will not guess at. This cell is therefore
    permanently unavailable to the topic, which is a real coverage price
    and is recorded as such rather than worked around.

    ``_person_number_anchor_present`` remains the rescue for a cell the
    verb does not settle, exactly as before: another pronoun or an
    unambiguous possessive elsewhere in the carrier can still fix who is
    meant."""
    assert candidate.person is not None and candidate.number is not None
    if candidate.number == "Sing" and candidate.person == "3":
        return False
    start, end = _clause_span(sentence, candidate.token_index)
    for token in sentence.tokens[start:end]:
        if token.morph.get("VerbForm") != "Fin":
            continue
        if _finite_verb_cell_is_unambiguous(token, candidate.person, candidate.number):
            return True
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
        # Cycle 11, owner decision D1: these topics now cue the auxiliary's
        # own citation form ("(werden)", "(sein)", "(haben)"). A candidate
        # that reaches here with NO cue is one whose citation form is
        # spelled the same as the answer, which is exactly the 1st/3rd
        # plural cell ("wir werden", "sie haben") -- so the cue was
        # withheld to avoid handing the answer over, and the slot is left
        # open to every modal instead: "Wir ___ es genauso machen wie
        # letztes Mal." takes "werden", "wollen", "können" and "müssen"
        # alike. Found in a dry run of the changed pipeline, not reasoned
        # about. Unlike the modal branch above, where the cue is a bonus,
        # here its absence is itself the defect.
        if not candidate.cue:
            return UniquenessOutcome(False, "auxiliary_lexeme_uncued")
        if not _auxiliary_tense_anchor_present(sentence, candidate):
            return UniquenessOutcome(False, "auxiliary_tense_unanchored")
        # docs/audits/cycle-07-report.md defect 12: a SEPARATE, lexical
        # ambiguity from the tense question just above -- "werden" plus a
        # bare infinitive is also interchangeable with the whole modal set
        # unless a future time anchor is present. See
        # ``_futur_i_modal_interchangeable``'s own docstring.
        if _futur_i_modal_interchangeable(sentence, candidate):
            return UniquenessOutcome(False, "futur_i_modal_interchangeable")
        return UniquenessOutcome(True, None)

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
            # No anchor rescue here any more, unlike the oblique branch
            # below. ``_person_number_anchor_present`` scans the whole
            # sentence for another pronoun or possessive of the same person
            # and number, and for a SUBJECT slot that is not evidence:
            # "___ haben den Urlaubern eine Nachricht geschickt, obwohl Sie
            # damals selbst sehr müde waren." was rescued by the second
            # "Sie" and accepts "Wir" just as happily, and "___ habe meinen
            # Aufsatz vergessen." would be rescued by "meinen" even though
            # the essay's owner and the sentence's subject need not be the
            # same person. Coreference is what the rescue silently assumed
            # and neither shape supplies it. The verb is the only witness
            # that actually constrains a subject, so it is now the only one
            # consulted. The price is that 1st and 3rd person plural become
            # unusable for this topic, which is a fact about German rather
            # than about this code: nothing in "wir/sie/Sie haben"
            # distinguishes them.
            if _nominative_pronoun_settled(sentence, candidate):
                return UniquenessOutcome(True, None)
            return UniquenessOutcome(False, "nominative_pronoun_syncretic")
        # An oblique pronoun's cue is the NOMINATIVE form of the same
        # pronoun ("(er)" -> "ihn", "(Sie)" -> "Ihnen"), added for the
        # owner's decision D2 (docs/audits/cycle-11-corpus-report.md). This
        # module used to say, for the auxiliary and modal branches above,
        # that a cue cannot rescue a personal pronoun because "the
        # ambiguity is over which CELL of one paradigm, not which lexeme".
        # That reasoning was written when no pronoun cue existed and it does
        # not survive one: a Nominative citation form names the person, the
        # number AND the gender, which is the whole of the cell except the
        # case, and the case is exactly what the topic asks the learner to
        # supply. Flagged here rather than silently changed, per CLAUDE.md
        # rule 8, because it reverses a stated position in this module's own
        # docstring. It does NOT extend to the Nominative branch above,
        # where a cue really would be the answer.
        if candidate.cue:
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
