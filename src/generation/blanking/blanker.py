"""Turn one selected ``Candidate`` into a ``CandidateItem``: remove the
token, compute its accepted-answer set and distractors from the closed
paradigms in ``paradigms.py``, or reject the sentence for this topic if the
paradigm cannot resolve it cleanly.

The accepted answer is never asked of a model and never guessed: it is the
literal text of the token that was removed, cross-checked by reconstructing
the same surface form from the paradigm table for the token's own
(art_type/declension, cell) and requiring an exact (case-insensitive) match.
A mismatch (an irregular form, a paradigm cell the reused tables do not
cover -- see ``paradigms.py``'s docstring on the ein-word plural gap) is a
reject, not a lower-confidence accept.

## Carrying a selector's cue through

``Candidate.cue`` (a bracketed citation-form cue -- see that field's own
docstring) is set by the selector, not derived here, but it has to reach the
``CandidateItem`` for ``uniqueness.py``'s cue rescue
(docs/audits/cycle-04-report.md recommendation 5, extended by
docs/audits/cycle-05-report.md to every open-class lexical-verb topic, and
again by docs/audits/cycle-06-modal-leak.md to the comparative/superlative
degree topic and both attributive-participle topics, and again by
docs/audits/cycle-07-report.md to every determiner topic and the three
plain adjective-declension topics) to mean anything: a cue that is computed
and then discarded rescues nothing. The six outcome builders that can
receive one (``_irregular_aux_outcome`` for a modal or sein/haben,
``_plural_noun_outcome``, ``_verb_form_outcome`` for a lexical verb,
``_degree_outcome`` for a comparative/superlative, ``_adjective_outcome``
for every attributive-adjective topic -- the two participle topics and now
the three plain declension topics alike -- and ``_determiner_outcome``)
copy it onto ``CandidateItem.cue`` unchanged via ``_cued_item_type``, which
resolves
to ``type="cloze_cued"`` instead of the ``"cloze_free"`` every cue-less
outcome builder still hard-codes -- ``data/taxonomy.yaml``'s
``eligible_types`` for ``nomen_plural``, ``verb_praesens_regelm``/
``verb_praesens_vokalwechsel``, ``verb_sein_haben`` and
``adjektiv_komparativ_superlativ`` is ``[cloze_cued]`` alone, and for
``modalverben_praesens``/``verben_trennbar_praesens``/``praeteritum_
vollverben``/``praeteritum_sein_haben_modal``/``partizip_i_attributiv``/
``partizip_ii_attributiv_erweitert`` is ``[cloze_cued, transformation]`` --
``cloze_free`` is not in ANY of these ten topics' own declared types, so an
item built from any of them without this would not even be a valid item
type, regardless of the uniqueness question. (Before docs/audits/
cycle-05-report.md's fix, ``_verb_form_outcome`` hard-coded
``type="cloze_free"`` unconditionally -- confirmed a live contract
violation for the four lexical-verb topics, independent of and in addition
to the uniqueness defect the cue itself fixes; docs/audits/
cycle-06-modal-leak.md found the same live violation, unfixed, on eight
more topics including these five.) ``_cue_equals_answer`` is a
second, independent check of the same invariant ``selectors._citation_cue``
already enforces at derivation time (a cue must never equal the answer it
cues) -- reject the item outright if it is ever true, rather than trust a
single guard for something this severe.

## ``artikel_bestimmt_nom`` and its two siblings, revisited

``artikel_bestimmt_nom``, ``artikel_unbestimmt_kein_nom`` and
``artikel_possessiv_nom`` used to declare ``eligible_types: [paragraph_cloze]``
in ``data/taxonomy.yaml`` alone, and this module's own ``_determiner_outcome``
unconditionally built ``type="cloze_free"`` for every one of them -- never
eligible, so ``pipeline.py``'s eligible-types assertion skipped every item
these three topics' selectors ever found, and they reported zero. That was a
deliberate architectural choice, not an oversight: definiteness, negation and
possession are discourse properties (docs/audits/stage-04-pilot-2026-08-15.md's
bucket 1), and a bare, unanchored sentence never forces one determiner family
over the other three ("Der/Ein/Kein/Mein Hund schläft im Garten" are all
grammatical) -- a citation cue is no fix here either, the way it is for a
lexeme choice, because there is no WORD that forces "the" over "a".

A later cycle (feat/generate-then-blank) narrowed the fix to what the
restriction was actually protecting against, rather than removing it: each
of the three topics gets a selector in ``selectors.py`` that fires ONLY when
a real, structural forcing anchor is present in the sentence (a uniqueness-
making relative clause, superlative, or ordinal for the definite article; a
causal ``weil`` clause for the negative article; a kinship noun plus an
explicit 1st-/2nd-person reference for the possessive) -- see that module's
own section for the three of them. With the anchor required, the item this
outcome builder still unconditionally types ``cloze_free`` genuinely is
solvable, so ``data/taxonomy.yaml`` now lists ``cloze_free`` alongside
``paragraph_cloze`` in all three topics' own ``eligible_types``, and
``pipeline.py``'s assertion passes these items through instead of skipping
them. An UNANCHORED sentence for one of these three topics still yields no
candidate at all -- that judgment did not change, only the topics' own
selectors got strict enough to make it safe to stop blocking every item
regardless of anchor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.contracts import CandidateItem, Difficulty, Distractor, ItemType
from src.generation.blanking import paradigms
from src.generation.blanking.selectors import Candidate, _cue_is_real_word
from src.generation.blanking.sentence_tagger import TaggedSentence

MAX_DISTRACTORS = 3


@dataclass(frozen=True)
class BlankOutcome:
    """Either a finished item, or a skip reason -- never both, never
    neither. ``pipeline.py`` counts skips by ``skip_reason``."""

    item: CandidateItem | None
    skip_reason: str | None


def _match_case(reference: str, form: str) -> str:
    """Capitalise ``form`` like ``reference`` if ``reference`` was itself
    capitalised (i.e. the blanked token opened its sentence) -- the paradigm
    tables are all lowercase, so every reconstructed and distractor form
    needs this before it can stand in the same slot the original occupied."""
    if reference[:1].isupper():
        return form[:1].upper() + form[1:]
    return form


def _render_prompt(sentence: TaggedSentence, blank_index: int) -> str:
    """Reconstruct ``sentence`` with the token at ``blank_index`` replaced by
    ``___``, using each token's own trailing whitespace so spacing and
    punctuation attachment are exactly what spaCy parsed, not re-guessed."""
    parts: list[str] = []
    for token in sentence.tokens:
        parts.append("___" if token.i == blank_index else token.text)
        parts.append(token.whitespace)
    return "".join(parts)


def _source_sentence_id(sentence: TaggedSentence) -> str:
    digest = hashlib.sha256(sentence.text.encode("utf-8")).hexdigest()[:16]
    return f"sent_{digest}"


def _cue_equals_answer(cue: str | None, answer: str) -> bool:
    """Whether ``cue`` is letter-for-letter identical to ``answer``
    (case-insensitively) -- a total giveaway, never acceptable.

    ``selectors._citation_cue`` already withholds a cue in exactly this
    situation at derivation time (docs/audits/cycle-04-report.md
    recommendation 5), so this should never fire in practice. It is kept
    here anyway as a second, independent backstop at the point the item is
    actually built and handed to a learner -- a bug in the selector's own
    guard should not alone be enough to leak an answer, and "reject rather
    than guess" (this module's own docstring) applies just as much to a
    defect in this pipeline's own earlier stage as to an unresolved
    paradigm cell."""
    return cue is not None and cue.lower() == answer.lower()


def _cued_item_type(cue: str | None) -> ItemType:
    """``cloze_cued`` whenever a cue is present, ``cloze_free`` otherwise --
    matching how every other ``cloze_cued`` item in this bank is shaped (a
    bracketed citation form paired with a ``cue`` field the learner sees;
    see ``CandidateItem.cue``'s own docstring) and, concretely, how
    ``data/taxonomy.yaml`` declares ``nomen_plural``'s and
    ``modalverben_praesens``'s own ``eligible_types`` (``cloze_cued`` only,
    no bare ``cloze_free``): a cue-rescued item from either topic would not
    even be a valid item type without this."""
    return "cloze_cued" if cue else "cloze_free"


def _determiner_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    assert candidate.art_type is not None and candidate.cell is not None
    token = sentence.tokens[candidate.token_index]
    family = paradigms.family_forms(candidate.art_type, token.text)
    if family is None:
        return BlankOutcome(None, "determiner_family_unrecognised")
    reconstructed = family.get(candidate.cell)
    if reconstructed is None:
        return BlankOutcome(None, "determiner_cell_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "determiner_paradigm_mismatch")

    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")

    distractor_forms = sorted(
        {
            form
            for cell, form in family.items()
            if cell != candidate.cell and form.lower() != token.text.lower()
        }
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _adjective_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """Shared by the three plain adjective-declension topics (no cue,
    ``candidate.cue`` is always ``None`` for those) and by
    ``partizip_i_attributiv``/``partizip_ii_attributiv_erweitert`` (cued with
    the underlying verb's own infinitive -- docs/audits/
    cycle-06-modal-leak.md's cue extension, see ``selectors.py``'s own two
    participle selectors for where the cue is derived). ``_cued_item_type``
    and the ``_cue_equals_answer`` guard are exactly the same treatment
    ``_verb_form_outcome``/``_irregular_aux_outcome`` already give a cue --
    ``data/taxonomy.yaml``'s ``eligible_types`` for both participle topics is
    ``[cloze_cued, transformation]``, so an item built from either without
    this would not even be a valid item type."""
    assert candidate.declension is not None and candidate.cell is not None
    token = sentence.tokens[candidate.token_index]
    ending = paradigms.adjective_ending(candidate.declension, candidate.cell)
    if ending is None:
        return BlankOutcome(None, "adjective_cell_uncovered_by_paradigm")
    lower = token.text.lower()
    if not lower.endswith(ending) or len(token.text) <= len(ending):
        return BlankOutcome(None, "adjective_ending_mismatch")
    stem = token.text[: len(token.text) - len(ending)]
    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")

    family = paradigms.adjective_family_forms(candidate.declension, stem)
    distractor_forms = sorted(
        {form for cell, form in family.items() if cell != candidate.cell and form != token.text}
    )
    # docs/audits/cycle-07-report.md defect 2: an adjective distractor is
    # synthesised by concatenating a STEM (read off the blanked token's own
    # surface text, not looked up in any paradigm) with each OTHER cell's
    # ending -- unlike every other outcome builder's distractors, which are
    # real closed-class paradigm forms by construction, this one is only as
    # good as the stem. A wrongly-selected candidate (e.g. "euer" mistagged
    # as an attributive adjective) yields a stem that is not a real
    # adjective stem at all, and every "distractor" built from it is
    # nonsense ("eue", "euem", "euen") -- checked here, once, against the
    # same vendored dictionary the cue check uses, independently of whether
    # the selector-level fix above also closed this specific case, per the
    # module's own "reject rather than guess" posture: a non-word distractor
    # is shown to the learner at hint level 2, so this is not merely
    # cosmetic. A distractor that fails the check is dropped; if that
    # leaves fewer than ``MAX_DISTRACTORS``, the item ships with the
    # distractors it has, exactly like every other reject-partial posture in
    # this pipeline.
    distractor_forms = [form for form in distractor_forms if _cue_is_real_word(form)][
        :MAX_DISTRACTORS
    ]
    distractors = [Distractor(text=form) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _degree_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """Comparative/superlative has no safe paradigm to derive a near-miss
    distractor from: the positive/comparative/superlative alternation is
    frequently irregular (gut/besser/best-, viel/mehr/meist-) and this cycle
    has no comparison table to reuse or compute it from, so distractors are
    left empty rather than guessed. See the top-level report for this
    decision spelled out.

    ``cue`` is the positive-form citation cue (docs/audits/
    cycle-06-modal-leak.md's cue extension; see ``selectors._degree_cue``):
    without one, any comparative/superlative fits the slot, which is exactly
    why ``adjektiv_komparativ_superlativ``'s own ``eligible_types`` in
    ``data/taxonomy.yaml`` is ``[cloze_cued]`` and never ``cloze_free``."""
    token = sentence.tokens[candidate.token_index]
    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")
    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=[],
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _personal_pronoun_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    assert candidate.person is not None and candidate.number is not None
    token = sentence.tokens[candidate.token_index]
    gender = candidate.gender or "Unk"
    # The candidate's own Case is not carried on Candidate for this kind (it
    # is implied by which topic's selector produced it, and is fixed per
    # topic), so re-read it from the token itself -- the same source the
    # selector used to select it in the first place.
    case = token.morph.get("Case")
    if case is None:
        return BlankOutcome(None, "pronoun_case_unresolved")
    reconstructed = paradigms.personal_pronoun_form(
        case, candidate.person, candidate.number, gender
    )
    if reconstructed is None:
        return BlankOutcome(None, "personal_pronoun_cell_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "personal_pronoun_paradigm_mismatch")

    family = paradigms.personal_pronoun_family_forms(candidate.person, candidate.number, gender)
    distractor_forms = sorted(
        {form for c, form in family.items() if c != case and form.lower() != token.text.lower()}
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _reflexive_pronoun_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    assert candidate.person is not None and candidate.number is not None
    token = sentence.tokens[candidate.token_index]
    case = token.morph.get("Case")
    if case is None:
        return BlankOutcome(None, "reflexive_case_unresolved")
    reconstructed = paradigms.reflexive_form(case, candidate.person, candidate.number)
    if reconstructed is None:
        return BlankOutcome(None, "reflexive_cell_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "reflexive_paradigm_mismatch")

    family = paradigms.reflexive_family_forms(candidate.person, candidate.number)
    distractor_forms = sorted(
        {form for c, form in family.items() if c != case and form.lower() != token.text.lower()}
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _relative_pronoun_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    assert candidate.cell is not None
    case, gender, number = candidate.cell
    token = sentence.tokens[candidate.token_index]
    reconstructed = paradigms.relative_pronoun_form(case, gender, number)
    if reconstructed is None:
        return BlankOutcome(None, "relative_pronoun_cell_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "relative_pronoun_paradigm_mismatch")

    family = paradigms.relative_pronoun_family_forms(gender, number)
    distractor_forms = sorted(
        {form for c, form in family.items() if c != case and form.lower() != token.text.lower()}
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _verb_form_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """Regular/vowel-change/strong/mixed finite verb forms -- reconstructed
    by rule or by the closed strong/mixed stem tables in ``paradigms.py``,
    cross-checked against the actual token exactly like every other outcome
    builder in this module."""
    assert (
        candidate.lemma is not None
        and candidate.person is not None
        and candidate.number is not None
        and candidate.verb_family is not None
    )
    token = sentence.tokens[candidate.token_index]
    reconstructed = paradigms.verb_family_form(
        candidate.verb_family, candidate.lemma, candidate.person, candidate.number
    )
    if reconstructed is None:
        return BlankOutcome(None, "verb_form_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "verb_form_paradigm_mismatch")
    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")

    family = paradigms.verb_family_forms(candidate.verb_family, candidate.lemma)
    distractor_forms = sorted(
        {
            form
            for cell, form in family.items()
            if cell != (candidate.person, candidate.number) and form.lower() != token.text.lower()
        }
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _irregular_aux_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """sein/haben/werden/modal finite forms, in whichever (Tense, Mood)
    combination the selector already decided (``candidate.tense_mood``) --
    Präsens, Präteritum, or Konjunktiv II. Covers perfekt/plusquamperfekt/
    passive/Futur/Konjunktiv-II, all of which blank an auxiliary or modal
    rather than the lexical verb itself."""
    assert (
        candidate.lemma is not None
        and candidate.person is not None
        and candidate.number is not None
        and candidate.tense_mood is not None
    )
    token = sentence.tokens[candidate.token_index]
    family = paradigms.irregular_finite_family_forms(candidate.lemma, candidate.tense_mood)
    if family is None:
        return BlankOutcome(None, "irregular_aux_lemma_unrecognised")
    reconstructed = family.get((candidate.person, candidate.number))
    if reconstructed is None:
        return BlankOutcome(None, "irregular_aux_cell_uncovered_by_paradigm")
    if reconstructed.lower() != token.text.lower():
        return BlankOutcome(None, "irregular_aux_paradigm_mismatch")
    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")

    distractor_forms = sorted(
        {
            form
            for cell, form in family.items()
            if cell != (candidate.person, candidate.number) and form.lower() != token.text.lower()
        }
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=_match_case(token.text, form)) for form in distractor_forms]

    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _fixed_particle_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """A single, invariant closed-class word (``zu``, ``gewesen``): the
    selector has already confirmed it structurally, so there is nothing left
    to reconstruct -- trusted like the token itself, same posture as
    ``_degree_outcome`` above, no distractors because there is no
    alternation to draw one from."""
    token = sentence.tokens[candidate.token_index]
    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=[],
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def _plural_noun_outcome(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty,
) -> BlankOutcome:
    """A plural noun, trusted as its own correct answer -- German plural
    formation has no single computable rule (see ``selectors.py``'s own
    docstring on this topic), so distractors are left empty rather than
    guessed, matching ``_degree_outcome``'s precedent for the same reason."""
    token = sentence.tokens[candidate.token_index]
    if _cue_equals_answer(candidate.cue, token.text):
        return BlankOutcome(None, "cue_equals_answer")
    return BlankOutcome(
        CandidateItem(
            topic_id=topic_id,
            type=_cued_item_type(candidate.cue),
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=[],
            cue=candidate.cue,
            source_sentence_id=_source_sentence_id(sentence),
        ),
        None,
    )


def blank_candidate(
    topic_id: str,
    sentence: TaggedSentence,
    candidate: Candidate,
    difficulty: Difficulty = 1,
) -> BlankOutcome:
    """Dispatch on ``candidate.kind`` to build the ``CandidateItem``, or
    return a ``BlankOutcome`` carrying only a skip reason."""
    if candidate.kind == "determiner":
        return _determiner_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "adjective":
        return _adjective_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "degree":
        return _degree_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "personal_pronoun":
        return _personal_pronoun_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "reflexive_pronoun":
        return _reflexive_pronoun_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "relative_pronoun":
        return _relative_pronoun_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "verb_form":
        return _verb_form_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "irregular_aux":
        return _irregular_aux_outcome(topic_id, sentence, candidate, difficulty)
    if candidate.kind == "plural_noun":
        return _plural_noun_outcome(topic_id, sentence, candidate, difficulty)
    return _fixed_particle_outcome(topic_id, sentence, candidate, difficulty)
