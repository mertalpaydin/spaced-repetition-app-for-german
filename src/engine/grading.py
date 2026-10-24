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

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from src.contracts import PhraseCard, PhraseUnit, ReviewOutcome, ReviewRating
from src.engine.typo_grader import ScopedTypoGrader


@dataclass(frozen=True)
class GapGrade:
    expected: str
    typed: str
    outcome: ReviewOutcome
    accepted: bool
    #: The right word in a form the grader will not pass on its own: a verb
    #: in the wrong tense or person, a wrong capital, a missed "zu". The
    #: learner may count it as right with "Als richtig werten".
    near: bool = False


@dataclass(frozen=True)
class CardGrade:
    gaps: list[GapGrade]
    outcome: ReviewOutcome
    rating: ReviewRating

    @property
    def correct(self) -> bool:
        return self.rating != "again"

    @property
    def near_miss(self) -> bool:
        """Failed, but only on gaps where the learner had the right word.

        This is when "Als richtig werten" is offered (owner, 2026-10-22: "I
        miss the zu, or the sentence is in Perfekt, or a capital on a name;
        I should be able to click if I recognised the word"). Never for a
        wrong word and never for a revealed card, so the button cannot turn
        a guess into a pass.
        """
        if self.correct:
            return False
        failed = [g for g in self.gaps if not g.accepted and g.outcome != "inflection"]
        return bool(failed) and all(g.near for g in failed)


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
    # Two words that differ ONLY by an umlaut are different words, not two
    # forms of one: "schon"/"schön", "hatte"/"hätte", "konnte"/"könnte".
    if _fold_umlauts(a) == _fold_umlauts(b):
        return False
    # Umlauts are folded inside the stem, so a plural that umlauts its stem
    # is still the same word: "Hände"/"Hand", "Häuser"/"Haus". Until
    # 2026-10-22 they were compared as written and "Hände" was graded wrong.
    stems_a = {_fold_umlauts(stem) for stem, _ in _strip_ending(a)}
    stems_b = {_fold_umlauts(stem) for stem, _ in _strip_ending(b)}
    # A shared stem is not enough on its own: "stark" and "starken" share
    # "stark", but so would "stark" and "starkenzzz", which never reaches
    # here because every candidate stem came off one of the known endings.
    return bool(stems_a & stems_b)


def _fold_umlauts(word: str) -> str:
    return word.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")


#: Separable particles that sit in front of the "ge" of a participle
#: ("aufgemacht") or the "zu" of an infinitive ("anzurufen").
_PARTICLES = (
    "zurück",
    "zusammen",
    "weiter",
    "vorbei",
    "heraus",
    "herein",
    "hinaus",
    "hinein",
    "nach",
    "fort",
    "weg",
    "los",
    "auf",
    "aus",
    "ein",
    "mit",
    "vor",
    "zu",
    "ab",
    "an",
    "um",
    "hin",
    "her",
    "da",
    "dar",
    "durch",
)


def _without_infix(word: str, infix: str) -> str | None:
    """``word`` with a participle "ge" or an infinitive "zu" taken out, at
    the start or right after a separable particle; None if it has none."""
    if word.startswith(infix) and len(word) > len(infix) + 2:
        return word[len(infix) :]
    for particle in _PARTICLES:
        rest = word[len(particle) :]
        if word.startswith(particle) and rest.startswith(infix) and len(rest) > len(infix) + 2:
            return particle + rest[len(infix) :]
    return None


def is_verb_form_of(typed: str, expected: str) -> bool:
    """Whether ``typed`` is the same verb as ``expected`` in another form.

    The ending test above, plus the two things a verb adds: the "ge" of the
    participle, so "macht" is the verb of "gemacht" (the owner: "I realise
    the sentence is in Perfekt"), and the "zu" of the infinitive, so
    "anrufen" is the verb of "anzurufen" ("I miss the zu"). Strong verbs
    that change their stem ("ging", "gegangen") are beyond a test without a
    lexicon and are left to the grader's plain "wrong".
    """
    a, b = typed.strip().lower(), expected.strip().lower()
    if not a or a == b:
        return False
    if is_inflection_of(a, b):
        return True
    for infix in ("ge", "zu"):
        for x, y in ((a, b), (b, a)):
            stripped = _without_infix(y, infix)
            if stripped is not None and (stripped == x or is_inflection_of(x, stripped)):
                return True
    return False


#: A preposition fused with its article, to the bare preposition. "zum" for
#: "zu" and "vom" for "von" are the right word with the article folded in,
#: which the owner reported twice in two days (2026-10-23/24).
_FUSED_PREPOSITIONS: dict[str, str] = {
    "zum": "zu",
    "zur": "zu",
    "vom": "von",
    "am": "an",
    "ans": "an",
    "im": "in",
    "ins": "in",
    "beim": "bei",
    "aufs": "auf",
    "ums": "um",
    "durchs": "durch",
    "fürs": "für",
    "übers": "über",
    "unters": "unter",
    "unterm": "unter",
    "vors": "vor",
    "vorm": "vor",
    "hinters": "hinter",
    "hinterm": "hinter",
}


def is_fused_preposition_of(typed: str, expected: str) -> bool:
    """Whether ``typed`` and ``expected`` are one preposition, with or without
    a fused article: "zum"/"zu", "zum"/"zur", "vom"/"von"."""
    a, b = typed.strip().lower(), expected.strip().lower()
    if not a or a == b:
        return False
    base_a, base_b = _FUSED_PREPOSITIONS.get(a, a), _FUSED_PREPOSITIONS.get(b, b)
    return base_a == base_b and (a in _FUSED_PREPOSITIONS or b in _FUSED_PREPOSITIONS)


def is_sharp_s_slip(typed: str, expected: str) -> bool:
    """Whether the two differ only in how an s is written: "Nachweiß" for
    "Nachweis", "Strasse" for "Straße" already passes as a transliteration.
    The typo rule cannot forgive it, because the edit sits in the last two
    letters, where an ending would be."""
    a, b = typed.strip().lower(), expected.strip().lower()
    if not a or a == b or "ß" not in a + b:
        return False

    def fold(w: str) -> str:
        return w.replace("ß", "s").replace("ss", "s")

    return fold(a) == fold(b)


#: A form key naming a verb form: the card's tested word is a verb.
_VERBAL_FORMS = ("Fin", "Part", "Inf")


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
    forms = accepted_forms(card, gap_index, alternatives)
    result = ScopedTypoGrader.grade(typed, forms, topic_id=None)
    verbal = card.form_key.split("|", 1)[0] in _VERBAL_FORMS
    near = False
    if result.is_correct and result.is_exact:
        outcome: ReviewOutcome = "exact"
    elif result.is_correct and result.is_transliteration:
        outcome = "translit"
    elif result.is_correct and result.is_scoped_typo:
        outcome = "typo"
    elif result.is_capitalization_error:
        outcome, near = "case", True
    elif verbal and any(is_verb_form_of(typed, form) for form in forms):
        # A verb in the wrong tense or person is the learner's call, not an
        # automatic pass: until 2026-10-22 "wartet" for "warten" was accepted
        # as an ending slip, which the owner called too loose. It fails, and
        # "Als richtig werten" is offered.
        outcome, near = "wrong", True
    elif not verbal and any(is_inflection_of(typed, form) for form in forms):
        outcome = "inflection"
    elif expected.lower() == "zu" and typed.strip().lower() in {"", "zu"}:
        outcome, near = "wrong", True
    elif any(is_fused_preposition_of(typed, f) or is_sharp_s_slip(typed, f) for f in forms):
        outcome, near = "wrong", True
    else:
        outcome = "wrong"
    return GapGrade(
        expected=expected, typed=typed, outcome=outcome, accepted=result.is_correct, near=near
    )


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


#: Too short or too common to say which unit the learner meant.
_NOT_A_RIVAL_FORM: frozenset[str] = frozenset(
    {
        "sich", "mich", "dich", "uns", "euch", "mir", "dir", "ihm", "ihr", "es", "der", "die",
        "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "nicht", "und",
        "aber", "oder", "auch", "noch", "schon", "mit", "von", "aus", "auf", "für", "über",
        "unter", "nach", "bei", "vor", "zum", "zur", "vom", "beim",
    }
)  # fmt: skip


def unit_forms(unit: PhraseUnit, cards: Iterable[PhraseCard]) -> frozenset[str]:
    """Every word a learner could type for ``unit``: its cards' answers and
    its citation parts, lower-cased. What "the learner typed this unit"
    means, for ``rival_typed``."""
    words = {w.lower() for w in unit.parts}
    for card in cards:
        words.update(a.lower() for a in card.answers)
    return frozenset(w for w in words if len(w) >= 3 and w not in _NOT_A_RIVAL_FORM)


def rival_typed(
    unit: PhraseUnit, grade: CardGrade, forms_of: Callable[[str], frozenset[str]]
) -> str | None:
    """The rival unit the learner typed into a failed gap, or None.

    A rival shares the unit's English (``PhraseUnit.rivals``), so typing it
    is the right idea and the wrong word: "kriegen" for "bekommen",
    "zurückkommen" for "zurückkehren". The page says another word is wanted
    and lets the learner try again, nothing recorded (owner, 2026-10-24).
    A word that is also one of the unit's own forms is not a rival: that
    is the unit, graded as it is.
    """
    if grade.correct or not unit.rivals:
        return None
    own = forms_of(unit.unit_id)
    for gap in grade.gaps:
        if gap.outcome != "wrong" or gap.near:
            continue
        typed = gap.typed.strip().lower()
        if typed in own or typed in _NOT_A_RIVAL_FORM or len(typed) < 3:
            continue
        for rival in unit.rivals:
            if typed in forms_of(rival):
                return rival
    return None


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
