"""Grading a card's gaps and turning the result into an FSRS rating.

The learner never self-rates. Every gap exact or a transliteration: good.
Any gap a scoped typo: hard. Any gap wrong, wrongly capitalised, or
revealed: again. The grader is the phrase-agnostic ``ScopedTypoGrader``:
the tested morpheme is the whole token, so an edit in the last two letters
(the ending) is never tolerated.
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
_SEVERITY: dict[ReviewOutcome, int] = {
    "revealed": 5,
    "wrong": 4,
    "case": 3,
    "typo": 2,
    "translit": 1,
    "exact": 0,
}


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
    elif worst.outcome == "typo":
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
