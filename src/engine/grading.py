"""Grading a card's gaps and turning the result into an FSRS rating.

The learner never self-rates. Every gap exact or a transliteration: good.
Any gap a scoped typo, or the right word in the wrong form ("starken" for
"stark"): hard. Any gap wrong, wrongly capitalised, or revealed: again.

The typo grader is the phrase-agnostic ``ScopedTypoGrader``: the tested
morpheme is the whole token, so an edit in the last two letters is never
tolerated as a typo. That is what makes the ending its own outcome rather
than something the typo rule could absorb.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.contracts import PhraseCard, ReviewOutcome, ReviewRating
from src.engine.typo_grader import ScopedTypoGrader


@dataclass(frozen=True)
class GapGrade:
    expected: str
    typed: str
    outcome: ReviewOutcome
    accepted: bool


@dataclass(frozen=True)
class CardGrade:
    gaps: list[GapGrade]
    outcome: ReviewOutcome
    rating: ReviewRating

    @property
    def correct(self) -> bool:
        return self.rating != "again"


#: Worst outcome first, for a card: any of these on one gap decides the card.
#: "inflection" sits with "typo": both are the right word, so both cost the
#: learner a harder rating and not a restart.
_SEVERITY: dict[ReviewOutcome, int] = {
    "revealed": 6,
    "wrong": 5,
    "case": 4,
    "inflection": 3,
    "typo": 2,
    "translit": 1,
    "exact": 0,
}

#: The endings a German noun, adjective or determiner takes. The owner knows
#: "stark" and still types "starken", because the ending is agreement with a
#: noun phrase and not part of the vocabulary item; marking that "again" sent
#: the unit back to learning step 0 and it returned every few cards, which is
#: what "I see the same words again and again" was (owner, 2026-10-06).
#:
#: Deliberately not a morphology engine. Both forms must share a stem of at
#: least ``_MIN_STEM`` characters and differ ONLY by these endings, so
#: "starken"/"stark" matches while "Hand"/"Hund" and "geben"/"gegeben" do
#: not. The verb endings are here too ("-t", "-te", "-st"), but only after
#: the stem test, which a different lemma cannot pass.
_INFLECTION_ENDINGS: frozenset[str] = frozenset(
    {"", "e", "en", "em", "er", "es", "s", "n", "ern", "t", "te", "st", "ten", "et"}
)
#: Three, not four: "gut"/"gute" and "alt"/"alten" are the commonest
#: case of all, and a four-character stem misses every short adjective.
_MIN_STEM = 3


def _strip_ending(word: str) -> list[tuple[str, str]]:
    """Every (stem, ending) split of ``word`` the ending list allows."""
    out: list[tuple[str, str]] = []
    for ending in _INFLECTION_ENDINGS:
        if not ending:
            out.append((word, ""))
        elif word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM:
            out.append((word[: -len(ending)], ending))
    return out


def is_inflection_of(typed: str, expected: str) -> bool:
    """Whether ``typed`` is the same word as ``expected`` in another form.

    Case-insensitive, because a wrong capital is already its own outcome and
    judging both at once would hide one behind the other.
    """
    a, b = typed.strip().lower(), expected.strip().lower()
    if not a or a == b:
        return False
    stems_a = {stem for stem, _ in _strip_ending(a)}
    stems_b = {stem for stem, _ in _strip_ending(b)}
    # A shared stem is not enough on its own: "stark" and "starken" share
    # "stark", but so would "stark" and "starkenzzz", which never reaches
    # here because every candidate stem came off one of the known endings.
    return bool(stems_a & stems_b)


def accepted_forms(card: PhraseCard, gap_index: int, alternatives: Sequence[str] = ()) -> list[str]:
    """The gap's answer, plus its lower-cased form when the gap opens the
    sentence: a connector typed as "trotzdem" for "Trotzdem" is right, the
    capital belongs to the sentence, not the word. ``alternatives`` are the
    unit's accepted near-synonyms ("deswegen" for "deshalb"); they apply to
    single-gap cards only, capitalised like the answer."""
    gap = card.gaps[gap_index]
    forms = [gap.answer]
    initial = gap.start == 0 and gap.answer[:1].isupper() and not gap.answer[1:2].isupper()
    if initial:
        forms.append(gap.answer[:1].lower() + gap.answer[1:])
    if len(card.gaps) == 1:
        for alt in alternatives:
            if alt.lower() == gap.answer.lower():
                continue
            forms.append(alt)
            if initial:
                forms.append(alt[:1].upper() + alt[1:])
    return forms


def grade_gap(
    card: PhraseCard, gap_index: int, typed: str | None, alternatives: Sequence[str] = ()
) -> GapGrade:
    expected = card.gaps[gap_index].answer
    if typed is None:
        return GapGrade(expected=expected, typed="", outcome="revealed", accepted=False)
    result = ScopedTypoGrader.grade(
        typed, accepted_forms(card, gap_index, alternatives), topic_id=None
    )
    if result.is_correct and result.is_exact:
        outcome: ReviewOutcome = "exact"
    elif result.is_correct and result.is_transliteration:
        outcome = "translit"
    elif result.is_correct and result.is_scoped_typo:
        outcome = "typo"
    elif result.is_capitalization_error:
        outcome = "case"
    elif any(
        is_inflection_of(typed, form) for form in accepted_forms(card, gap_index, alternatives)
    ):
        outcome = "inflection"
    else:
        outcome = "wrong"
    return GapGrade(expected=expected, typed=typed, outcome=outcome, accepted=result.is_correct)


def grade_card(
    card: PhraseCard, typed: list[str | None], alternatives: Sequence[str] = ()
) -> CardGrade:
    """``typed`` has one entry per gap; ``None`` means the learner revealed it."""
    if len(typed) != len(card.gaps):
        raise ValueError(f"{len(card.gaps)} gaps, {len(typed)} answers")
    gaps = [grade_gap(card, i, t, alternatives) for i, t in enumerate(typed)]
    worst = max(gaps, key=lambda g: _SEVERITY[g.outcome])
    if worst.outcome in {"exact", "translit"}:
        rating: ReviewRating = "good"
    elif worst.outcome in {"typo", "inflection"}:
        rating = "hard"
    else:
        rating = "again"
    return CardGrade(gaps=gaps, outcome=worst.outcome, rating=rating)


def render_with_gaps(card: PhraseCard, placeholder: str = "___") -> str:
    """The sentence with every gap blanked, for the prompt."""
    out: list[str] = []
    last = 0
    for gap in card.gaps:
        out.append(card.sentence_de[last : gap.start])
        out.append(placeholder)
        last = gap.end
    out.append(card.sentence_de[last:])
    return "".join(out)


def render_marked(card: PhraseCard, left: str = "[", right: str = "]") -> str:
    """The sentence with the phrase's tokens marked, for the feedback."""
    out: list[str] = []
    last = 0
    for gap in card.gaps:
        out.append(card.sentence_de[last : gap.start])
        out.append(f"{left}{card.sentence_de[gap.start : gap.end]}{right}")
        last = gap.end
    out.append(card.sentence_de[last:])
    return "".join(out)
