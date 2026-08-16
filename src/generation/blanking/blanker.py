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
    return _degree_outcome(topic_id, sentence, candidate, difficulty)
