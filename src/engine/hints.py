"""Hint ladder: 4 static levels plus the FSRS-rating degradation mapping.

Levels 1 through 3 are deliberately free at runtime: level 1 derives word shape
from the accepted answer already on the item, level 2 reuses the item's
precomputed ``distractors``, and level 3 reads the item's (or, failing that,
the topic's) static ``rule_hint``. None of the three makes a network call or
an LLM call. Level 4 reveals the accepted answer outright.
"""

import random

from src.contracts import BankItem, FsrsRating, HintLevel, Topic

# Level 2 shows the accepted answer plus up to this many precomputed
# distractors, so a learner sees at most a 4-way multiple choice.
MAX_DISTRACTOR_OPTIONS = 3


class HintPolicy:
    """Encapsulates the 4-tier hint ladder and translates hint usage into FSRS ratings."""

    @staticmethod
    def is_unhinted_pass(hint_level: HintLevel, is_correct: bool) -> bool:
        """Only Hint 0 (pure recall) without any clues counts toward promotion."""
        return hint_level == 0 and is_correct

    @classmethod
    def evaluate_attempt(
        cls,
        hint_level: HintLevel,
        is_correct: bool,
        is_fast: bool = False,
    ) -> FsrsRating:
        """Map user attempt accuracy and hint level to an FSRS rating."""
        if not is_correct or hint_level >= 3:
            # Failure, rule revealed, or answer given -> Again
            return "again"

        if hint_level in (1, 2):
            # Cued / Multiple-choice options selected -> Hard
            return "hard"

        # Hint 0 (Unhinted success)
        if is_fast:
            return "easy"
        return "good"

    @staticmethod
    def _target_answer(item: BankItem) -> str:
        """First accepted answer, the canonical target the ladder hints toward."""
        return item.accepted_answers[0] if item.accepted_answers else ""

    @classmethod
    def shape_hint(cls, item: BankItem) -> str:
        """Level 1: word length and first letter of the target answer.

        Free at runtime -- both facts are derived from ``accepted_answers``,
        already in memory on the item. No lookup, no LLM call.
        """
        target = cls._target_answer(item)
        if not target:
            return "Kein Hinweis verfügbar."
        first_word = target.split(" ", 1)[0]
        return f"{len(target)} Zeichen, beginnt mit '{first_word[0]}'."

    @classmethod
    def option_hint(
        cls,
        item: BankItem,
        rng: random.Random | None = None,
    ) -> tuple[str, list[str]]:
        """Level 2: the accepted answer plus up to 3 of the item's precomputed distractors.

        Free at runtime -- distractors are stored on the item at generation
        time, so this is a pure in-memory selection, never a lookup or an LLM
        call. Returns both a display message and the raw option list (for a
        client that wants to render buttons instead of text).
        """
        target = cls._target_answer(item)
        distractor_texts = [d.text for d in item.distractors[:MAX_DISTRACTOR_OPTIONS]]

        options = [target, *distractor_texts] if target else list(distractor_texts)
        seen: set[str] = set()
        deduped: list[str] = []
        for opt in options:
            if opt and opt not in seen:
                seen.add(opt)
                deduped.append(opt)

        ordered = list(deduped)
        if rng is not None:
            rng.shuffle(ordered)

        message = "Optionen: " + " / ".join(ordered) if ordered else "Keine Optionen verfügbar."
        return message, ordered

    @classmethod
    def rule_hint_text(cls, item: BankItem, topic: Topic | None = None) -> str:
        """Level 3: the topic's static rule_hint.

        Free at runtime -- `rule_hint` is static text, either already copied
        onto the item at generation time or read off the (already in-memory)
        topic. Never generated on demand.
        """
        if item.rule_hint:
            return item.rule_hint
        if topic is not None and topic.rule_hint:
            return topic.rule_hint
        return "Grammatikregel beachten."

    @classmethod
    def reveal(cls, item: BankItem) -> str:
        """Level 4: reveal the accepted answer outright."""
        target = cls._target_answer(item)
        return f"Lösung: {target}"

    @classmethod
    def get_hint(
        cls,
        item: BankItem,
        hint_level: HintLevel,
        topic: Topic | None = None,
        rng: random.Random | None = None,
    ) -> str:
        """Render the hint text for the requested ladder level.

        Level 0 has no hint (empty string); levels 1-4 escalate from a shape
        clue through options and the stated rule to an outright reveal.
        """
        if hint_level == 1:
            return cls.shape_hint(item)
        if hint_level == 2:
            message, _ = cls.option_hint(item, rng=rng)
            return message
        if hint_level == 3:
            return cls.rule_hint_text(item, topic=topic)
        if hint_level == 4:
            return cls.reveal(item)
        return ""
