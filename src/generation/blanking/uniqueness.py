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
  excluded from this rule: unlike the modals, an auxiliary's own identity is
  fixed by the construction itself (Perfekt/Plusquamperfekt choose
  sein-vs-haben per verb, Passiv always takes werden), so there is no other
  auxiliary that could stand in the same slot at all -- the ambiguity a
  modal has does not exist for these, and ``selectors.py`` never sets a cue
  on one of them either (see ``_irregular_finite_selector``: the cue is
  gated on the lemma being a modal in the first place).

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

* ``personal_pronoun`` -- **ambiguous unless the carrier itself supplies an
  anchor.** A NOMINATIVE personal pronoun is the one case this module trusts
  unconditionally: the sentence's own finite verb form is left untouched by
  blanking, and German subject-verb agreement means only the pronoun(s)
  matching that verb's own (person, number) can stand in the subject slot at
  all -- the verb itself is already the disambiguator, no search needed. An
  ACCUSATIVE or DATIVE personal pronoun has no such built-in anchor: the verb
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

* Every other kind (``determiner``, ``adjective``, ``degree``,
  ``reflexive_pronoun``, ``relative_pronoun``, ``verb_form``,
  ``irregular_aux`` for sein/haben/werden, ``fixed_particle``) -- **passes
  unaffected.** Their own paradigm cell (Case/Gender/Number, or Person/Number
  for a reflexive/relative pronoun) is forced by agreement with a governing
  noun, preposition, or antecedent that this module does not re-derive: a
  DIFFERENT cell's form would not agree with that governor and so would not
  be grammatical in the slot at all, which is exactly the asymmetry the task
  that specified this module called out (a determiner slot whose case and
  gender are forced by a preposition has only one grammatical filler, unlike
  a modal slot where essentially every member fits). Nothing observed in the
  cycle-4 audit contradicts this for these kinds; if a future audit finds
  otherwise for one of them, this policy table is where that finding lands,
  not a silent guess bolted onto ``blanker.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.generation.blanking import paradigms
from src.generation.blanking.selectors import Candidate
from src.generation.blanking.sentence_tagger import TaggedSentence

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

    if candidate.kind == "personal_pronoun":
        assert candidate.token_index < len(sentence.tokens)
        token = sentence.tokens[candidate.token_index]
        if token.morph.get("Case") == "Nom":
            # The sentence's own (unchanged) finite verb already forces this
            # -- see module docstring.
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

    return UniquenessOutcome(True, None)
