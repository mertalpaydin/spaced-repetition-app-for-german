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
    """``cards_per_day`` is the day's exercise budget: due units always run,
    new units are introduced until that many answers have been given today.
    ``new_per_day`` is a hard cap on top of it, ``None`` for none. The first
    learner found a cap of ten units repetitive (feedback, 2026-09-11)."""

    cards_per_day: int = 40
    new_per_day: int | None = None
    #: A new unit is drawn at random from the next ``new_pool`` by rank
    #: rather than always the very next one, so a session does not march
    #: down the ranking in lockstep (feedback, 2026-09-19).
    new_pool: int = 50
    learn_ahead: timedelta = timedelta(minutes=20)
    retention: float = 0.9
    #: How many other reviews go by before a unit the learner got wrong comes
    #: back. It replaces the ten-minute learning step as the thing that spaces
    #: a retry, so the clock stops deciding what the session shows
    #: (owner, 2026-09-22).
    #: Raised from 3 to 8 on 2026-10-06. At 3, a unit the learner kept
    #: getting wrong came back every fourth card; with a wrong ending
    #: counting as "again" that was most of a sitting spent on a handful of
    #: units, which is what "I see the same words again and again" was.
    relearn_spacing: int = 8
    #: How many times one unit may be asked in a single sitting before it is
    #: left for tomorrow. Spacing alone cannot stop a loop: a unit the
    #: learner never gets right is always eligible again, so without a cap
    #: the sitting narrows to it. The unit keeps its FSRS state; it is simply
    #: not offered again today (owner, 2026-10-06).
    max_asks_per_session: int = 3
    #: Inactivity that ends a sitting. Only used to waive the spacing above
    #: for a unit still pending from an earlier one.
    session_gap: timedelta = timedelta(minutes=60)
    #: How many other units go by before a word the learner just added is
    #: shown. He looked the word up a moment ago, so asking it at once tests
    #: nothing (owner, 2026-10-07). Counted in reviews logged since the
    #: request, so closing the app does not skip the wait.
    requested_delay: int = 10
    #: Words the learner added from the page are introduced before the due
    #: queue, this many per day. They asked for the word, so waiting behind
    #: a hundred due cards is not an answer; but a learner who adds fifty
    #: words at once should not lose the day's reviews to them either
    #: (owner, 2026-10-06).
    requested_per_day: int = 5


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

    def with_added(self, state: LearnerState) -> Deck:
        """This deck plus whatever the learner added from the page.

        A word the exported deck does not have arrives as a ``RequestEntry``
        carrying its unit and cards, because a static artifact cannot be
        written to from a phone. Folding them in here means the scheduler,
        the grader and the statistics need to know nothing about where a
        unit came from; everything downstream sees one deck.

        The deck wins on a collision: once a word is mined into a real
        build, the reviewed cards replace the written ones, and the
        ``unit_id`` is the same so the learner's history carries over.
        """
        if not state.added_units and not state.added_cards:
            return self
        known = {u.unit_id for u in self.units}
        extra = [u for uid, u in sorted(state.added_units.items()) if uid not in known]
        cards_by_unit = dict(self.cards_by_unit)
        for unit_id, cards in state.added_cards.items():
            if not cards_by_unit.get(unit_id):
                cards_by_unit[unit_id] = list(cards)
        return Deck(
            manifest=self.manifest,
            units=sorted([*self.units, *extra], key=lambda u: u.rank),
            cards_by_unit=cards_by_unit,
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


def reviews_today(state: LearnerState, now: datetime) -> int:
    day = now.astimezone(UTC).date()
    return sum(1 for ts in state.review_times if ts.astimezone(UTC).date() == day)


def budget_spent_today(state: LearnerState, now: datetime) -> int:
    """What the day's budget has actually been charged for.

    Not the same as ``reviews_today``: the first retry of a unit mid step is
    left out, because finishing a step is not new work. The statistics keep
    counting every answer, so the two numbers differ by the number of lapses
    (owner, 2026-09-22).
    """
    day = now.astimezone(UTC).date()
    return sum(1 for ts in state.budget_review_times if ts.astimezone(UTC).date() == day)


def budget_left(state: LearnerState, settings: Settings, now: datetime) -> int:
    return max(settings.cards_per_day - budget_spent_today(state, now), 0)


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


def session_start(review_times: Sequence[datetime], now: datetime, gap: timedelta) -> datetime:
    """When the current sitting began: walk the reviews back while the gaps
    between them stay under ``gap``.

    Derived from the timestamps the log already carries, so no entry type and
    no field is added for it (CLAUDE.md 8). With no reviews, or none recent
    enough, the sitting starts now.
    """
    start = now
    for ts in reversed(review_times):
        if start - ts > gap:
            break
        start = ts
    return start


def eligible_pending_units(
    deck: Deck,
    state: LearnerState,
    engine: FSRSEngine,
    settings: Settings,
    now: datetime,
    *,
    ignore_spacing: bool = False,
) -> list[PhraseUnit]:
    """Units mid learning or relearning step that may be shown again now.

    Their FSRS ``due`` is ten minutes out, and waiting for it is what let the
    day's budget strand a unit the learner had just failed (owner, 2026-09-22).
    Eligibility is spacing instead of the clock: the unit comes back once
    ``relearn_spacing`` other reviews have gone by. A unit left pending from an
    earlier sitting skips the wait, since the reviews that would have spaced it
    happened before the break.

    ``ignore_spacing`` is the last resort in ``next_unit``: when there is
    nothing else at all to show, the unit comes back at once rather than the
    session ending.
    """
    by_id = deck.by_id
    started = session_start(state.review_times, now, settings.session_gap)
    out: list[tuple[float, int, PhraseUnit]] = []
    for unit_id, record in state.records.items():
        unit = by_id.get(unit_id)
        if unit is None or unit_id in state.known or record.state == "review":
            continue
        last = record.last_review
        if not ignore_spacing and last is not None and last >= started:
            since = sum(1 for ts in state.review_times if ts > last)
            if since < settings.relearn_spacing:
                continue
        out.append((engine.get_retrievability(record, now), unit.rank, unit))
    out.sort(key=lambda item: (item[0], item[1]))
    return [unit for _, _, unit in out]


def introduction_order(deck: Deck, state: LearnerState | None = None) -> list[PhraseUnit]:
    """The order new units are introduced in.

    Three tiers. First what the learner added from the page, newest last, in
    the order they pressed the button: they asked for it a moment ago, so it
    is the next thing they should see. Then the units the owner asked for by
    hand in ``requested.yaml``. Then everything else by corpus frequency, as
    before.

    The owner's list is carried on the unit as ``requested_order`` rather
    than by rewriting ``rank``: rank is part of the deck's content hash and
    the review ledger's idea of "the top of the deck", and it promises to
    mean corpus frequency (owner, 2026-09-22). The learner's list is not on
    the unit at all, because it is per learner and lives in the log.

    ``web/lib/session.js`` holds the same sort key and the two are pinned
    against each other by tests in both suites.
    """
    added = {unit_id: position for position, unit_id in enumerate(state.requested)} if state else {}
    return sorted(
        deck.units,
        key=lambda u: (
            u.unit_id not in added,
            added.get(u.unit_id, 0),
            u.requested_order is None,
            u.requested_order or 0,
            u.rank,
        ),
    )


def _not_last(units: list[PhraseUnit], last: str | None) -> list[PhraseUnit]:
    """Never the unit just shown when anything else is available."""
    others = [u for u in units if u.unit_id != last]
    return others or units


def recently_shown(state: LearnerState, settings: Settings, now: datetime) -> set[str]:
    """Units answered within the last ``relearn_spacing`` reviews of this
    sitting. Nothing is offered again until that many other units have gone
    by, in ANY tier.

    The spacing used to live in ``eligible_pending_units`` alone, so it only
    governed the pending tier. A unit answered wrong goes to relearning with
    its FSRS due ten minutes out, so once those ten minutes passed it came
    back through the OVERDUE tier, which had no spacing at all, and the
    learner met it again far sooner than the setting implied (owner,
    2026-10-07).
    """
    gap = settings.relearn_spacing
    if gap <= 0:
        return set()
    started = session_start(state.review_times, now, settings.session_gap)
    in_session = [
        unit_id
        for ts, unit_id in zip(state.review_times, state.review_order, strict=False)
        if ts >= started
    ]
    return set(in_session[-gap:])


def asks_this_session(state: LearnerState, settings: Settings, now: datetime) -> dict[str, int]:
    """How often each unit has already been asked in this sitting."""
    start = session_start(state.review_times, now, settings.session_gap)
    return {
        unit_id: sum(1 for ts in times if ts >= start)
        for unit_id, times in state.review_times_by_unit.items()
    }


def requested_to_introduce(
    deck: Deck, state: LearnerState, settings: Settings, now: datetime
) -> list[PhraseUnit]:
    """Words the learner added from the page that have never been reviewed.

    These jump the due queue. The learner typed the word a moment ago and
    pressed a button that said it would be taught next; with a hundred cards
    due, the new-unit tier meant days of waiting, so the promise was false
    (owner, 2026-10-06). Bounded by ``requested_per_day`` so adding fifty
    words does not cost a day's reviews.
    """
    if not state.requested:
        return []
    started_today = sum(
        1
        for unit_id in state.requested
        if (first := state.first_review.get(unit_id)) is not None and first.date() == now.date()
    )
    budget = settings.requested_per_day - started_today
    if budget <= 0:
        return []
    out: list[PhraseUnit] = []
    by_id = deck.by_id
    for unit_id in state.requested:
        unit = by_id.get(unit_id)
        if unit is None or unit_id in state.records or unit_id in state.known:
            continue
        if not is_learnable(deck, unit, state):
            continue
        # The learner looked this word up a moment ago, so a few other units
        # go by before it is asked. Counted in reviews since the request
        # rather than in time, so closing the app does not skip the wait.
        asked_at = state.requested_at.get(unit_id)
        if asked_at is not None:
            since = sum(1 for ts in state.review_times if ts > asked_at)
            if since < settings.requested_delay:
                continue
        out.append(unit)
        if len(out) >= budget:
            break
    return out


def next_unit(
    deck: Deck,
    state: LearnerState,
    engine: FSRSEngine,
    settings: Settings,
    now: datetime,
    *,
    over_limit: bool = False,
    choose: Callable[[int], int] | None = None,
) -> PhraseUnit | None:
    """A word the learner just added first, then due units, least retrievable
    first; then a new unit while the day's budget lasts (or ``over_limit``
    says go on); then the learn-ahead window. The unit shown last is skipped
    when there is a choice, so a session never shows one unit twice in a row,
    and no unit is asked more than ``max_asks_per_session`` times in a
    sitting.

    The new unit is drawn from the next ``settings.new_pool`` by rank:
    ``choose(n)`` picks an index in ``range(n)`` and the clients pass a
    random one, so two sessions do not introduce the same units in the same
    order. The default picks the first, which keeps the order deterministic
    for the tests and for anyone replaying a build."""
    last = state.last_unit
    asks = asks_this_session(state, settings, now)
    recent = recently_shown(state, settings, now)

    def spaced(units: list[PhraseUnit]) -> list[PhraseUnit]:
        """Drop units answered in the last ``relearn_spacing`` reviews."""
        return [u for u in units if u.unit_id not in recent]

    def not_exhausted(units: list[PhraseUnit]) -> list[PhraseUnit]:
        """Drop units already asked ``max_asks_per_session`` times today.

        Spacing cannot stop a loop on its own: a unit the learner never gets
        right stays eligible for ever, so the sitting narrows onto it. The
        unit keeps its FSRS state and comes back tomorrow.
        """
        cap = settings.max_asks_per_session
        return [u for u in units if cap <= 0 or asks.get(u.unit_id, 0) < cap]

    # A word the learner added comes first, ahead even of the due queue.
    wanted = _not_last(not_exhausted(requested_to_introduce(deck, state, settings, now)), last)
    if wanted:
        return wanted[0]
    overdue = _not_last(
        spaced(not_exhausted(due_units(deck, state, engine, now, timedelta(0)))), last
    )
    if overdue:
        return overdue[0]
    # A unit mid learning step comes back on spacing, not on the clock, and
    # ahead of any new unit: there is no point introducing more while the
    # learner is still getting this one wrong (owner, 2026-09-22).
    pending = [
        u
        for u in spaced(not_exhausted(eligible_pending_units(deck, state, engine, settings, now)))
        if u.unit_id != last
    ]
    if pending:
        return pending[0]
    within_budget = over_limit or budget_left(state, settings, now) > 0
    under_cap = settings.new_per_day is None or (
        new_units_started_today(state, now) < settings.new_per_day
    )
    if within_budget and (under_cap or over_limit):
        pool: list[PhraseUnit] = []
        for unit in introduction_order(deck, state):
            if unit.unit_id in state.records:
                continue
            if is_learnable(deck, unit, state):
                pool.append(unit)
                if len(pool) >= max(settings.new_pool, 1):
                    break
        if pool:
            return pool[(choose(len(pool)) if choose else 0) % len(pool)]
    # Learn-ahead never re-shows the unit just answered: that is the
    # back-to-back repeat the first learner complained about.
    ahead = [
        u
        for u in spaced(not_exhausted(due_units(deck, state, engine, now, settings.learn_ahead)))
        if u.unit_id != last
    ]
    if ahead:
        return ahead[0]
    # Nothing else to interleave with: show the pending unit again rather
    # than stopping. Drilling what the learner just got wrong is the only
    # useful work left, and the ten-minute wait this replaces was the
    # complaint (owner, 2026-09-22).
    looping = not_exhausted(
        eligible_pending_units(deck, state, engine, settings, now, ignore_spacing=True)
    )
    return looping[0] if looping else None


#: Stability bands, in days, for the Einheiten screen. The learner had 110
#: units in one "young" bucket spanning 0 to 21 days and 19 in "mature",
#: which told him nothing about what was actually settling (owner,
#: 2026-10-06). The names are the client's; the boundaries are here so both
#: clients agree.
STABILITY_BANDS: tuple[tuple[str, float], ...] = (
    ("fresh", 7.0),
    ("young", 21.0),
    ("mature", 90.0),
    ("solid", float("inf")),
)


def stability_band(stability: float) -> str:
    """Which band a unit's stability puts it in."""
    for name, upper in STABILITY_BANDS:
        if stability < upper:
            return name
    return STABILITY_BANDS[-1][0]


def units_by_stage(
    deck: Deck, state: LearnerState, now: datetime
) -> dict[str, list[tuple[PhraseUnit, datetime | None]]]:
    """Every unit the log knows, grouped: known, deferred, learning, then one
    group per stability band; each with its next due time (``None`` for known
    and deferred)."""
    by_id = deck.by_id
    groups: dict[str, list[tuple[PhraseUnit, datetime | None]]] = {
        "known": [],
        "deferred": [],
        "learning": [],
    }
    for name, _ in STABILITY_BANDS:
        groups[name] = []
    for unit_id in state.known:
        if unit_id in by_id:
            stage = "deferred" if state.known_source.get(unit_id) == "defer" else "known"
            groups[stage].append((by_id[unit_id], None))
    for unit_id, record in state.records.items():
        unit = by_id.get(unit_id)
        if unit is None or unit_id in state.known:
            continue
        if record.state != "review" or record.stability is None:
            groups["learning"].append((unit, record.due))
        else:
            groups[stability_band(record.stability)].append((unit, record.due))
    for items in groups.values():
        items.sort(key=lambda pair: (pair[1] or now, pair[0].rank))
    return groups


def is_praeteritum(card: PhraseCard) -> bool:
    """A finite past-tense form in the gaps (``form_key`` ``Fin|Past|...``)."""
    return card.form_key.startswith("Fin|Past")


def pick_card(
    deck: Deck, unit: PhraseUnit, state: LearnerState, choose: Callable[[int], int] | None = None
) -> PhraseCard | None:
    """Rotate through the unit's showable cards, never the one shown last.
    ``choose(n)`` picks an index in ``range(n)``; the default is the card
    after the last one, so a session walks the surface forms in order."""
    cards = showable_cards(deck.cards_by_unit.get(unit.unit_id, []))
    # Präteritum is never shown, on the owner's instruction of 2026-10-06.
    # The card stage no longer selects one, so this only matters for a deck
    # built before that; it is kept rather than deleted because a client can
    # be running against a cached older deck for weeks. ``or cards`` is the
    # safety net: a unit whose only cards are Präteritum stays answerable on
    # an old deck rather than silently vanishing.
    cards = [c for c in cards if not is_praeteritum(c)] or cards
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
