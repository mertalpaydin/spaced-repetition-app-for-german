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
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.contracts import CandidateItem, Difficulty, Distractor
from src.generation.blanking import paradigms
from src.generation.blanking.selectors import Candidate
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
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
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
    assert candidate.declension is not None and candidate.cell is not None
    token = sentence.tokens[candidate.token_index]
    ending = paradigms.adjective_ending(candidate.declension, candidate.cell)
    if ending is None:
        return BlankOutcome(None, "adjective_cell_uncovered_by_paradigm")
    lower = token.text.lower()
    if not lower.endswith(ending) or len(token.text) <= len(ending):
        return BlankOutcome(None, "adjective_ending_mismatch")
    stem = token.text[: len(token.text) - len(ending)]

    family = paradigms.adjective_family_forms(candidate.declension, stem)
    distractor_forms = sorted(
        {form for cell, form in family.items() if cell != candidate.cell and form != token.text}
    )[:MAX_DISTRACTORS]
    distractors = [Distractor(text=form) for form in distractor_forms]

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
    decision spelled out."""
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
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
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
            type="cloze_free",
            difficulty=difficulty,
            prompt=_render_prompt(sentence, candidate.token_index),
            proposed_answer=token.text,
            distractors=distractors,
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
