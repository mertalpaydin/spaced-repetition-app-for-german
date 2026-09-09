"""Which unit next, and which of its cards: the scheduler over the deck.

Due units first, least likely to be remembered first; then new units in
rank order, skipping trivial and known ones, up to ``new_per_day``; then
units due within the learn-ahead window. A card is shown only when it has
a machine gloss (rule 9) and, for a sentence-initial connector, only when it
has its preceding sentence (a bare "Trotzdem kam sie." is a guess, not an
exercise) unless the unit has no other card.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.contracts import DeckManifest, PhraseCard, PhraseUnit
from src.engine.fsrs import FSRSEngine
from src.engine.review_log import LearnerState
from src.phrases.export import DEFAULT_DECK_DIR, load_deck


@dataclass(frozen=True)
class Settings:
    new_per_day: int = 10
    learn_ahead: timedelta = timedelta(minutes=20)
    retention: float = 0.9


@dataclass
class Deck:
    manifest: DeckManifest
    units: list[PhraseUnit]
    cards_by_unit: dict[str, list[PhraseCard]]

    @property
    def by_id(self) -> dict[str, PhraseUnit]:
        return {u.unit_id: u for u in self.units}

    @classmethod
    def load(cls, deck_dir: Path = DEFAULT_DECK_DIR) -> Deck:
        manifest, units, cards = load_deck(deck_dir)
        by_unit: dict[str, list[PhraseCard]] = {}
        for card in cards:
            by_unit.setdefault(card.unit_id, []).append(card)
        return cls(
            manifest=manifest, units=sorted(units, key=lambda u: u.rank), cards_by_unit=by_unit
        )


def showable_cards(cards: Sequence[PhraseCard]) -> list[PhraseCard]:
    glossed = [c for c in cards if c.gloss_en is not None]
    with_context = [c for c in glossed if not c.needs_context or c.context_de is not None]
    return with_context or glossed


def is_learnable(deck: Deck, unit: PhraseUnit, state: LearnerState) -> bool:
    return (
        not unit.trivial
        and unit.unit_id not in state.known
        and bool(showable_cards(deck.cards_by_unit.get(unit.unit_id, [])))
    )


def new_units_started_today(state: LearnerState, now: datetime) -> int:
    day = now.astimezone(UTC).date()
    return sum(1 for ts in state.first_review.values() if ts.astimezone(UTC).date() == day)


def due_units(
    deck: Deck, state: LearnerState, engine: FSRSEngine, now: datetime, horizon: timedelta
) -> list[PhraseUnit]:
    """Reviewed units whose due time is within ``horizon`` of now, least
    retrievable first."""
    by_id = deck.by_id
    due: list[tuple[float, int, PhraseUnit]] = []
    for unit_id, record in state.records.items():
        unit = by_id.get(unit_id)
        if unit is None or unit_id in state.known or record.due > now + horizon:
            continue
        due.append((engine.get_retrievability(record, now), unit.rank, unit))
    due.sort(key=lambda item: (item[0], item[1]))
    return [unit for _, _, unit in due]


def next_unit(
    deck: Deck,
    state: LearnerState,
    engine: FSRSEngine,
    settings: Settings,
    now: datetime,
) -> PhraseUnit | None:
    overdue = due_units(deck, state, engine, now, timedelta(0))
    if overdue:
        return overdue[0]
    if new_units_started_today(state, now) < settings.new_per_day:
        for unit in deck.units:
            if unit.unit_id in state.records:
                continue
            if is_learnable(deck, unit, state):
                return unit
    ahead = due_units(deck, state, engine, now, settings.learn_ahead)
    return ahead[0] if ahead else None


def pick_card(
    deck: Deck, unit: PhraseUnit, state: LearnerState, choose: Callable[[int], int] | None = None
) -> PhraseCard | None:
    """Rotate through the unit's showable cards, never the one shown last.
    ``choose(n)`` picks an index in ``range(n)``; the default is the card
    after the last one, so a session walks the surface forms in order."""
    cards = showable_cards(deck.cards_by_unit.get(unit.unit_id, []))
    if not cards:
        return None
    if len(cards) == 1:
        return cards[0]
    last = state.last_card.get(unit.unit_id)
    ids = [c.card_id for c in cards]
    if choose is not None:
        candidates = [c for c in cards if c.card_id != last]
        return candidates[choose(len(candidates)) % len(candidates)]
    index = (ids.index(last) + 1) % len(cards) if last in ids else 0
    return cards[index]


def untriaged_units(deck: Deck, state: LearnerState) -> list[PhraseUnit]:
    """Rank order, skipping trivial units and units already judged or
    already reviewed."""
    return [
        u
        for u in deck.units
        if not u.trivial and u.unit_id not in state.triaged and u.unit_id not in state.records
    ]
