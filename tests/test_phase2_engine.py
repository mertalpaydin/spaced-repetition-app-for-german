"""Phase 2: the review log, grading, the scheduler and the terminal client."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.cli.train import Client
from src.contracts import (
    DeckManifest,
    GapSpan,
    MarkEntry,
    PhraseCard,
    PhraseUnit,
    ResetEntry,
    ReviewEntry,
)
from src.engine.fsrs import FSRSEngine
from src.engine.grading import grade_card, render_marked, render_with_gaps
from src.engine.review_log import ReviewLog, derive_state, entry_key, merge_entries, read_entries
from src.engine.session import (
    Deck,
    Settings,
    asks_this_session,
    budget_left,
    budget_spent_today,
    due_units,
    eligible_pending_units,
    introduction_order,
    next_unit,
    pick_card,
    requested_to_introduce,
    reviews_today,
    session_start,
    showable_cards,
    stability_band,
)
from src.engine.stats import compute_stats

T0 = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


def _unit(unit_id: str, rank: int, display: str, *, trivial: bool = False) -> PhraseUnit:
    kind = unit_id.split(":")[0]
    return PhraseUnit(
        unit_id=unit_id,
        kind={"vp": "verb_prep", "cn": "connector", "sv": "separable_verb"}[kind],  # type: ignore[arg-type]
        lemma_key=display.lower(),
        parts=display.split(),
        display_de=display,
        case="Akk" if kind == "vp" else None,
        cefr="A1",
        gloss_en=None,
        sentence_count=10,
        count_by_source={"tatoeba": 10},
        per_million=1.0,
        rank=rank,
        trivial=trivial,
        trivial_reason=None,
        source="mined",
        card_count=1,
        glossed_card_count=1,
    )


def _card(card_id: str, unit_id: str, text: str, answers: list[str], **extra: object) -> PhraseCard:
    gaps = []
    pos = 0
    for i, a in enumerate(answers):
        start = text.index(a, pos)
        gaps.append(GapSpan(start=start, end=start + len(a), answer=a, token_index=i))
        pos = start + len(a)
    fields: dict[str, object] = {
        "card_id": card_id,
        "unit_id": unit_id,
        "kind": "verb_prep" if unit_id.startswith("vp") else "connector",
        "sentence_de": text,
        "gloss_en": "gloss",
        "gloss_source": "azure",
        "gaps": gaps,
        "answers": answers,
        "form_key": "Fin",
        "corpus_source": "tatoeba",
        "corpus_line_id": card_id,
        "needs_context": False,
    }
    fields.update(extra)
    return PhraseCard.model_validate(fields)


def _deck() -> Deck:
    units = [
        _unit("vp:warten_auf", 1, "warten auf"),
        _unit("cn:und", 2, "und", trivial=True),
        _unit("cn:trotzdem", 3, "Trotzdem"),
        _unit("sv:aufstehen", 4, "aufstehen"),
    ]
    cards = {
        "vp:warten_auf": [
            _card("w10000000000", "vp:warten_auf", "Er wartet auf den Bus.", ["wartet", "auf"]),
            _card("w20000000000", "vp:warten_auf", "Wir warten auf dich.", ["warten", "auf"]),
        ],
        "cn:und": [_card("u10000000000", "cn:und", "Tom und Maria.", ["und"])],
        "cn:trotzdem": [
            _card(
                "t10000000000", "cn:trotzdem", "Trotzdem kam sie.", ["Trotzdem"], needs_context=True
            ),
            _card(
                "t20000000000",
                "cn:trotzdem",
                "Trotzdem ging er.",
                ["Trotzdem"],
                needs_context=True,
                context_de="Es regnete.",
                context_en="It rained.",
                context_source="gemini",
            ),
        ],
        "sv:aufstehen": [
            _card(
                "a10000000000",
                "sv:aufstehen",
                "Er steht früh auf.",
                ["steht", "auf"],
                gloss_en=None,
                gloss_source=None,
            )
        ],
    }
    manifest = DeckManifest(
        deck_version="test",
        built_at=T0,
        corpus={},
        unit_count=len(units),
        card_count=6,
        glossed_card_count=5,
        trivial_count=1,
        contexts_generated=1,
        kinds={},
        units_file="units.json",
        shards=[],
    )
    return Deck(manifest=manifest, units=units, cards_by_unit=cards)


# -- grading -------------------------------------------------------------------


def test_grade_card_maps_outcomes_to_ratings() -> None:
    card = _card("w10000000000", "vp:warten_auf", "Er wartet auf den Bus.", ["wartet", "auf"])
    assert grade_card(card, ["wartet", "auf"]).rating == "good"
    assert grade_card(card, ["wartet", "auf"]).outcome == "exact"
    assert grade_card(card, ["wertet", "auf"]).rating == "hard"
    # Until 2026-10-06 a changed ending was "again", which sent the unit back
    # to learning step 0; the owner knew the words and was being shown them
    # every few cards because of it. The right word in the wrong form is now
    # "hard", like a typo: it costs an interval, not a restart.
    assert grade_card(card, ["warte", "auf"]).outcome == "inflection"
    assert grade_card(card, ["warte", "auf"]).rating == "hard"
    # A different word is still "again", which is what keeps the change from
    # being a blanket softening of the grader.
    assert grade_card(card, ["suchen", "auf"]).outcome == "wrong"
    assert grade_card(card, ["suchen", "auf"]).rating == "again"
    assert grade_card(card, [None, "auf"]).outcome == "revealed"
    assert grade_card(card, ["Wartet", "auf"]).outcome == "case"
    with pytest.raises(ValueError):
        grade_card(card, ["wartet"])


def test_sentence_initial_gap_accepts_lower_case_and_umlaut_transliteration() -> None:
    card = _card("t10000000000", "cn:trotzdem", "Trotzdem kam sie.", ["Trotzdem"])
    assert grade_card(card, ["trotzdem"]).rating == "good"
    hoert = _card("h10000000000", "vp:hoeren_auf", "Er hört auf dich.", ["hört", "auf"])
    assert grade_card(hoert, ["hoert", "auf"]).outcome == "translit"


def test_render_gaps_and_marks() -> None:
    card = _card("w10000000000", "vp:warten_auf", "Er wartet auf den Bus.", ["wartet", "auf"])
    assert render_with_gaps(card) == "Er ___ ___ den Bus."
    assert render_marked(card) == "Er [wartet] [auf] den Bus."


# -- review log ----------------------------------------------------------------


def test_review_log_appends_and_replays_deterministically(tmp_path: Path) -> None:
    clock = [T0]
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: clock[0])
    log.record_review(
        unit_id="vp:warten_auf",
        card_id="w10000000000",
        rating="good",
        outcome="exact",
        answers=["wartet", "auf"],
        expected=["wartet", "auf"],
        elapsed_ms=1200,
        deck_version="t",
    )
    clock[0] = T0 + timedelta(days=1)
    log.record_review(
        unit_id="vp:warten_auf",
        card_id="w20000000000",
        rating="again",
        outcome="wrong",
        answers=["x", "auf"],
        expected=["warten", "auf"],
        elapsed_ms=900,
        deck_version="t",
    )
    log.record_mark(unit_id="cn:trotzdem", known=True, source="triage")
    reread = read_entries(tmp_path / "log.jsonl")
    assert [e.seq for e in reread] == [1, 2, 3]
    engine = FSRSEngine()
    a = derive_state(reread, engine)
    b = derive_state(read_entries(tmp_path / "log.jsonl"), engine)
    assert a.records == b.records
    assert a.records["vp:warten_auf"].lapses == 1 and a.records["vp:warten_auf"].reps == 2
    assert a.known == {"cn:trotzdem"} and a.triaged == {"cn:trotzdem"}
    assert a.last_card["vp:warten_auf"] == "w20000000000"


def test_corrupt_lines_are_skipped_and_merge_dedupes(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    entry = MarkEntry(seq=1, ts=T0, unit_id="cn:und", known=True, source="triage")
    path.write_text(entry.model_dump_json() + "\n{broken\n\n", encoding="utf-8")
    entries = read_entries(path)
    assert len(entries) == 1
    other = [
        MarkEntry(seq=1, ts=T0, unit_id="cn:und", known=True, source="triage"),
        MarkEntry(
            seq=2, ts=T0 + timedelta(hours=1), unit_id="cn:und", known=False, source="practice"
        ),
    ]
    merged = merge_entries(entries, other)
    assert [e.seq for e in merged] == [1, 2]
    assert derive_state(merged, FSRSEngine()).known == set()


# -- session -------------------------------------------------------------------


def test_showable_cards_need_a_gloss_and_prefer_context() -> None:
    deck = _deck()
    assert showable_cards(deck.cards_by_unit["sv:aufstehen"]) == []
    assert [c.card_id for c in showable_cards(deck.cards_by_unit["cn:trotzdem"])] == [
        "t20000000000"
    ]


def test_next_unit_prefers_due_then_new_in_rank_order_and_skips_trivial_and_known() -> None:
    deck = _deck()
    engine = FSRSEngine()
    settings = Settings(cards_per_day=40)
    empty = derive_state([], engine)
    assert next_unit(deck, empty, engine, settings, T0).unit_id == "vp:warten_auf"
    entries = [
        ReviewEntry(
            seq=1,
            ts=T0,
            unit_id="vp:warten_auf",
            card_id="w10000000000",
            rating="good",
            outcome="exact",
            answers=["wartet", "auf"],
            expected=["wartet", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
        MarkEntry(seq=2, ts=T0, unit_id="cn:trotzdem", known=True, source="triage"),
    ]
    state = derive_state(entries, engine)
    # a minute later: trotzdem is known, und trivial, aufstehen unglossed, and
    # one "good" graduates the unit past its single learning step, so it is
    # due tomorrow, not within the learn-ahead window
    soon = T0 + timedelta(minutes=1)
    assert state.records["vp:warten_auf"].state == "review"
    assert next_unit(deck, state, engine, settings, soon) is None
    later = state.records["vp:warten_auf"].due + timedelta(minutes=1)
    assert next_unit(deck, state, engine, settings, later).unit_id == "vp:warten_auf"


def test_budget_and_over_limit_and_no_back_to_back_unit() -> None:
    deck = _deck()
    engine = FSRSEngine()
    tight = Settings(cards_per_day=1)
    entries = [
        ReviewEntry(
            seq=1,
            ts=T0,
            unit_id="vp:warten_auf",
            card_id="w10000000000",
            rating="again",
            outcome="wrong",
            answers=["x", "auf"],
            expected=["wartet", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
    ]
    state = derive_state(entries, engine)
    # The budget is spent (1 of 1) and warten is mid step. Until 2026-09-22
    # this asserted that nothing was offered until the ten-minute due time
    # passed, which is the defect: the session stopped and the step never
    # finished. The unit now comes back, because there is nothing else to
    # interleave with and finishing a step is not new work.
    soon = T0 + timedelta(minutes=1)
    assert next_unit(deck, state, engine, tight, soon).unit_id == "vp:warten_auf"
    assert soon < state.records["vp:warten_auf"].due
    # a new unit still waits for the budget, and is offered over the limit
    assert next_unit(deck, state, engine, tight, soon, over_limit=True).unit_id == "cn:trotzdem"
    later = state.records["vp:warten_auf"].due + timedelta(minutes=1)
    assert next_unit(deck, state, engine, tight, later).unit_id == "vp:warten_auf"


def test_a_pending_unit_yields_to_other_work_until_its_spacing_is_met() -> None:
    """With other units to show, the one just failed waits for them rather
    than looping: the loop is only the last resort (owner, 2026-09-22)."""
    deck = _deck()
    engine = FSRSEngine()
    roomy = Settings(cards_per_day=40, relearn_spacing=3)
    entries = [_review(1, T0, "vp:warten_auf", "again")]
    state = derive_state(entries, engine)
    soon = T0 + timedelta(seconds=30)
    # a new unit comes first: warten has had no other reviews to space it
    assert next_unit(deck, state, engine, roomy, soon).unit_id == "cn:trotzdem"

    for i, unit_id in enumerate(("cn:trotzdem", "sv:aufstehen", "cn:trotzdem"), start=2):
        entries.append(_review(i, T0 + timedelta(seconds=i * 10), unit_id, "good"))
    state = derive_state(entries, engine)
    spaced = T0 + timedelta(minutes=1)
    assert next_unit(deck, state, engine, roomy, spaced).unit_id == "vp:warten_auf"
    assert spaced < state.records["vp:warten_auf"].due


def test_pick_card_rotates_and_never_repeats_the_last_one() -> None:
    deck = _deck()
    engine = FSRSEngine()
    state = derive_state([], engine)
    unit = deck.by_id["vp:warten_auf"]
    first = pick_card(deck, unit, state)
    assert first is not None and first.card_id == "w10000000000"
    state.last_card["vp:warten_auf"] = "w10000000000"
    second = pick_card(deck, unit, state)
    assert second is not None and second.card_id == "w20000000000"
    assert pick_card(deck, unit, state, choose=lambda n: 0).card_id == "w20000000000"


# -- client --------------------------------------------------------------------


def _client(tmp_path: Path, answers: list[str], out: list[str]) -> Client:
    queue = list(answers)
    clock = [T0]

    def read(prompt: str) -> str | None:
        out.append(prompt)
        return queue.pop(0) if queue else None

    ticks = iter(range(0, 10_000))
    return Client(
        _deck(),
        ReviewLog(tmp_path / "log.jsonl", now=lambda: clock[0]),
        read=read,
        write=out.append,
        now=lambda: clock[0],
        clock=lambda: float(next(ticks)),
    )


def test_triage_records_marks_and_undo(tmp_path: Path) -> None:
    out: list[str] = []
    client = _client(tmp_path, ["1", "u", "2", "1", "q"], out)
    assert client.triage(batch=50) == 0
    state = client.state()
    assert state.known == {"cn:trotzdem"}
    assert state.triaged == {"vp:warten_auf", "cn:trotzdem"}
    assert "[1] warten auf +Akk" in out[1]


def test_practice_grades_and_logs_then_stats_reflect_it(tmp_path: Path) -> None:
    out: list[str] = []
    client = _client(tmp_path, ["wartet", "auf", "q"], out)
    assert client.practice(limit=5) == 0
    entries = client.log.entries
    reviews = [e for e in entries if isinstance(e, ReviewEntry)]
    assert (
        len(reviews) == 1 and reviews[0].rating == "good" and reviews[0].card_id == "w10000000000"
    )
    text = "".join(out)
    assert "Er ___ ___ den Bus." in text and "Richtig." in text and "warten auf +Akk" in text
    out.clear()
    assert client.stats() == 0
    joined = "".join(out)
    assert "jung 1" in joined and "gesamt 1" in joined and "Serie 1" in joined


def test_known_button_marks_the_unit_and_moves_on(tmp_path: Path) -> None:
    out: list[str] = []
    client = _client(tmp_path, ["!", "q"], out)
    client.practice(limit=3)
    assert client.state().known == {"vp:warten_auf"}


def test_stats_counts_bands_and_retention() -> None:
    deck = _deck()
    engine = FSRSEngine()
    entries = [
        ReviewEntry(
            seq=1,
            ts=T0 - timedelta(days=1),
            unit_id="vp:warten_auf",
            card_id="w10000000000",
            rating="again",
            outcome="wrong",
            answers=["x", "auf"],
            expected=["wartet", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
        ReviewEntry(
            seq=2,
            ts=T0,
            unit_id="vp:warten_auf",
            card_id="w20000000000",
            rating="good",
            outcome="exact",
            answers=["warten", "auf"],
            expected=["warten", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
    ]
    state = derive_state(entries, engine)
    s = compute_stats(deck, state, list(entries), engine, T0)
    assert s.reviews_total == 2 and s.reviews_today == 1 and s.streak_days == 2
    assert s.retention_30d == 0.5
    assert s.coverage[1] == (1, 4)
    assert s.new_remaining == 1  # trotzdem (und trivial, aufstehen unglossed)


def test_merge_appends_only_the_other_devices_new_entries(tmp_path: Path) -> None:
    out: list[str] = []
    client = _client(tmp_path, [], out)
    client.log.record_mark(unit_id="cn:und", known=True, source="triage")
    other = tmp_path / "phone.jsonl"
    phone = ReviewLog(other, now=lambda: T0 + timedelta(hours=2))
    phone.record_mark(unit_id="cn:und", known=True, source="triage")  # different time: kept
    phone.record_review(
        unit_id="vp:warten_auf",
        card_id="w10000000000",
        rating="good",
        outcome="exact",
        answers=["wartet", "auf"],
        expected=["wartet", "auf"],
        elapsed_ms=5,
        deck_version="t",
    )
    assert client.merge(other) == 0
    assert client.merge(other) == 0  # idempotent
    assert [e.seq for e in client.log.entries] == [1, 2, 3]
    assert "2 Einträge" in out[0] and "0 Einträge" in out[1]
    assert client.state().records["vp:warten_auf"].reps == 1


# -- feedback of 2026-09-13 ------------------------------------------------------


def test_pick_card_never_shows_a_praeteritum_card() -> None:
    """Owner's instruction of 2026-10-06. It used to be held back only until
    the unit reached review, so the learner met it eventually; now never.
    The card stage stopped selecting them in the same commit, so this guards
    a client running against a deck built before that, from cache."""
    deck = _deck()
    deck.cards_by_unit["vp:warten_auf"] = [
        _card(
            "p10000000000",
            "vp:warten_auf",
            "Er wartete auf den Bus.",
            ["wartete", "auf"],
            form_key="Fin|Past|3|Sing",
        ),
        _card(
            "w20000000000",
            "vp:warten_auf",
            "Wir warten auf dich.",
            ["warten", "auf"],
            form_key="Fin|Pres|1|Plur",
        ),
    ]
    engine = FSRSEngine()
    unit = deck.by_id["vp:warten_auf"]
    state = derive_state([], engine)
    assert pick_card(deck, unit, state).card_id == "w20000000000"
    state.last_card["vp:warten_auf"] = "w20000000000"
    # Even with the present form just shown, the past form is not offered;
    # repeating a card beats teaching the wrong tense.
    assert pick_card(deck, unit, state).card_id == "w20000000000"
    review = ReviewEntry(
        seq=1,
        ts=T0,
        unit_id="vp:warten_auf",
        card_id="w20000000000",
        rating="good",
        outcome="exact",
        answers=["warten", "auf"],
        expected=["warten", "auf"],
        elapsed_ms=1,
        deck_version="t",
    )
    good_twice = [review, review.model_copy(update={"seq": 2, "ts": T0 + timedelta(minutes=11)})]
    state = derive_state(good_twice, engine)
    assert state.records["vp:warten_auf"].state == "review"
    assert pick_card(deck, unit, state).card_id == "w20000000000"


def test_pick_card_falls_back_when_every_card_is_praeteritum() -> None:
    """A unit on an older deck whose only carriers are past tense stays
    answerable rather than silently vanishing from the session."""
    deck = _deck()
    deck.cards_by_unit["vp:warten_auf"] = [
        _card(
            "p10000000000",
            "vp:warten_auf",
            "Er wartete auf den Bus.",
            ["wartete", "auf"],
            form_key="Fin|Past|3|Sing",
        )
    ]
    state = derive_state([], FSRSEngine())
    assert pick_card(deck, deck.by_id["vp:warten_auf"], state).card_id == "p10000000000"


def test_near_synonyms_are_accepted_in_single_gap_cards_only() -> None:
    card = _card("d10000000000", "cn:deshalb", "Deshalb kam er.", ["Deshalb"])
    for typed in ("deswegen", "Deswegen", "daher"):
        grade = grade_card(card, [typed], alternatives=["deswegen", "daher"])
        assert grade.rating == "good" and grade.gaps[0].accepted, typed
    assert grade_card(card, ["darum"], alternatives=["deswegen"]).rating == "again"
    two = _card("w10000000000", "vp:warten_auf", "Er wartet auf den Bus.", ["wartet", "auf"])
    assert grade_card(two, ["hofft", "auf"], alternatives=["hofft"]).rating == "again"


def test_deferred_units_are_skipped_and_listed_apart_until_relearned() -> None:
    from src.engine.session import units_by_stage

    deck = _deck()
    engine = FSRSEngine()
    defer = MarkEntry(seq=1, ts=T0, unit_id="vp:warten_auf", known=True, source="defer")
    state = derive_state([defer], engine)
    assert next_unit(deck, state, engine, Settings(), T0).unit_id == "cn:trotzdem"
    groups = units_by_stage(deck, state, T0)
    assert [u.unit_id for u, _ in groups["deferred"]] == ["vp:warten_auf"] and groups["known"] == []
    back = MarkEntry(
        seq=2, ts=T0 + timedelta(hours=1), unit_id="vp:warten_auf", known=False, source="practice"
    )
    state = derive_state([defer, back], engine)
    assert next_unit(deck, state, engine, Settings(), T0).unit_id == "vp:warten_auf"
    assert units_by_stage(deck, state, T0)["deferred"] == []


# -- feedback of 2026-09-19 ------------------------------------------------------


def test_a_reset_starts_the_replay_over_but_keeps_the_lines() -> None:
    engine = FSRSEngine()
    history = [
        ReviewEntry(
            seq=1,
            ts=T0,
            unit_id="vp:warten_auf",
            card_id="w10000000000",
            rating="good",
            outcome="exact",
            answers=["wartet", "auf"],
            expected=["wartet", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
        MarkEntry(
            seq=2, ts=T0 + timedelta(minutes=1), unit_id="cn:trotzdem", known=True, source="triage"
        ),
    ]
    before = derive_state(history, engine)
    assert before.records and before.known and before.review_times

    reset = ResetEntry(seq=3, ts=T0 + timedelta(minutes=2), note="restart")
    after = derive_state([*history, reset], engine)
    assert after.records == {} and after.known == set() and after.triaged == set()
    assert after.review_times == [] and after.last_unit is None

    # what comes after the reset counts again
    later = ReviewEntry(
        seq=4,
        ts=T0 + timedelta(minutes=3),
        unit_id="cn:trotzdem",
        card_id="t20000000000",
        rating="good",
        outcome="exact",
        answers=["Trotzdem"],
        expected=["Trotzdem"],
        elapsed_ms=1,
        deck_version="t",
    )
    assert list(derive_state([*history, reset, later], engine).records) == ["cn:trotzdem"]


def test_a_reset_puts_the_counters_back_to_zero() -> None:
    """The numbers come from the log, so the restart has to reach them too:
    a leftover "heute 1" after starting over is what the browser showed."""
    deck = _deck()
    engine = FSRSEngine()
    history = [
        ReviewEntry(
            seq=1,
            ts=T0,
            unit_id="vp:warten_auf",
            card_id="w10000000000",
            rating="good",
            outcome="exact",
            answers=["wartet", "auf"],
            expected=["wartet", "auf"],
            elapsed_ms=1,
            deck_version="t",
        ),
    ]
    before = compute_stats(deck, derive_state(history, engine), history, engine, T0)
    assert (before.reviews_total, before.reviews_today, before.streak_days) == (1, 1, 1)

    entries = [*history, ResetEntry(seq=2, ts=T0 + timedelta(minutes=1), note="restart")]
    after = compute_stats(deck, derive_state(entries, engine), entries, engine, T0)
    assert (after.reviews_total, after.reviews_today, after.streak_days) == (0, 0, 0)
    assert after.retention_30d is None and after.young == 0
    assert after.new_remaining == before.new_remaining + 1


def test_a_reset_round_trips_through_the_log_and_merges_once(tmp_path: Path) -> None:
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    log.record_review(
        unit_id="vp:warten_auf",
        card_id="w10000000000",
        rating="good",
        outcome="exact",
        answers=["wartet", "auf"],
        expected=["wartet", "auf"],
        elapsed_ms=1,
        deck_version="t",
    )
    reset = log.record_reset(note="restart")
    assert reset.type == "reset" and reset.note == "restart"
    reread = read_entries(tmp_path / "log.jsonl")
    assert [e.type for e in reread] == ["review", "reset"]
    assert entry_key(reset) == ("reset", "", T0)
    assert len(merge_entries(reread, reread)) == 2


def test_a_new_unit_is_drawn_from_the_next_pool_by_rank() -> None:
    deck = _deck()
    engine = FSRSEngine()
    state = derive_state([], engine)
    # two units are learnable here: warten auf (rank 1) and trotzdem (rank 3)
    assert next_unit(deck, state, engine, Settings(), T0).unit_id == "vp:warten_auf"
    assert (
        next_unit(deck, state, engine, Settings(), T0, choose=lambda n: 1).unit_id == "cn:trotzdem"
    )
    # the pool bounds the choice: with a pool of one only the next by rank
    tight = Settings(new_pool=1)
    assert next_unit(deck, state, engine, tight, T0, choose=lambda n: 1).unit_id == "vp:warten_auf"


# -- sessions instead of minutes (owner, 2026-09-22) ---------------------------


def _review(seq: int, ts: datetime, unit_id: str, rating: str) -> ReviewEntry:
    return ReviewEntry(
        seq=seq,
        ts=ts,
        unit_id=unit_id,
        card_id=f"{unit_id[:1]}{seq:011d}",
        rating=rating,  # type: ignore[arg-type]
        outcome="exact" if rating != "again" else "wrong",
        answers=["x"],
        expected=["x"],
        elapsed_ms=1,
        deck_version="t",
    )


def test_session_start_walks_back_while_the_gaps_are_small() -> None:
    """The sitting is derived from the timestamps the log already has, so
    nothing is added to the log format for it."""
    gap = timedelta(minutes=60)
    assert session_start([], T0, gap) == T0

    recent = [T0 - timedelta(minutes=30), T0 - timedelta(minutes=20), T0 - timedelta(minutes=5)]
    assert session_start(recent, T0, gap) == recent[0]

    # a long pause ends the sitting: only what follows it counts
    split = [T0 - timedelta(hours=5), *recent]
    assert session_start(split, T0, gap) == recent[0]

    # nothing recent at all: the sitting starts now
    assert session_start([T0 - timedelta(hours=9)], T0, gap) == T0


def test_a_pending_unit_waits_for_other_cards_not_for_the_clock() -> None:
    """A unit the learner got wrong used to wait ten minutes. It now waits
    for `relearn_spacing` other reviews, so the clock stops deciding what the
    session shows (owner, 2026-09-22)."""
    deck = _deck()
    engine = FSRSEngine()
    settings = Settings(relearn_spacing=3)
    entries = [_review(1, T0, "vp:warten_auf", "again")]
    state = derive_state(entries, engine)
    soon = T0 + timedelta(seconds=30)
    assert state.records["vp:warten_auf"].state != "review"
    # nothing else has been answered yet, so it is not offered back at once
    assert [u.unit_id for u in eligible_pending_units(deck, state, engine, settings, soon)] == []

    for i, unit_id in enumerate(("cn:trotzdem", "sv:aufstehen", "cn:trotzdem"), start=2):
        entries.append(_review(i, T0 + timedelta(seconds=i * 10), unit_id, "good"))
    state = derive_state(entries, engine)
    later = T0 + timedelta(minutes=1)
    pending = [u.unit_id for u in eligible_pending_units(deck, state, engine, settings, later)]
    assert "vp:warten_auf" in pending  # three other reviews have gone by
    assert later < state.records["vp:warten_auf"].due  # and its due time has not


def test_a_unit_pending_from_an_earlier_sitting_skips_the_spacing() -> None:
    """The reviews that would have spaced it happened before the break."""
    deck = _deck()
    engine = FSRSEngine()
    settings = Settings(relearn_spacing=3)
    state = derive_state([_review(1, T0, "vp:warten_auf", "again")], engine)
    tomorrow = T0 + timedelta(days=1)
    pending = [u.unit_id for u in eligible_pending_units(deck, state, engine, settings, tomorrow)]
    assert pending == ["vp:warten_auf"]


def test_only_the_first_retry_is_free_of_the_days_budget() -> None:
    """Finishing a step is not new work, so the budget cannot strand a
    learner mid relearning. Failing the same unit again and again does spend
    it, so new units stop being introduced while they struggle
    (owner, 2026-09-22)."""
    engine = FSRSEngine()
    entries = [_review(1, T0, "vp:warten_auf", "again")]  # the lapse itself counts
    state = derive_state(entries, engine)
    assert len(state.budget_review_times) == 1

    entries.append(_review(2, T0 + timedelta(seconds=30), "vp:warten_auf", "again"))
    state = derive_state(entries, engine)
    assert len(state.review_times) == 2  # the statistics count every answer
    assert len(state.budget_review_times) == 1  # the first retry is free

    entries.append(_review(3, T0 + timedelta(seconds=60), "vp:warten_auf", "again"))
    state = derive_state(entries, engine)
    assert len(state.review_times) == 3
    assert len(state.budget_review_times) == 2  # the second retry is not


def test_the_budget_and_the_statistics_count_a_retry_differently() -> None:
    """The learner did the work, so the statistics count it; the budget does
    not charge for the first retry, so it cannot strand them mid step. The
    two numbers differ by the number of lapses (owner, 2026-09-22)."""
    engine = FSRSEngine()
    settings = Settings(cards_per_day=40)
    entries = [
        _review(1, T0, "vp:warten_auf", "again"),
        _review(2, T0 + timedelta(seconds=30), "vp:warten_auf", "again"),
    ]
    state = derive_state(entries, engine)
    assert reviews_today(state, T0) == 2
    assert budget_spent_today(state, T0) == 1
    assert budget_left(state, settings, T0) == 39

    # keep failing and the budget starts charging again
    entries.append(_review(3, T0 + timedelta(seconds=60), "vp:warten_auf", "again"))
    state = derive_state(entries, engine)
    assert reviews_today(state, T0) == 3
    assert budget_spent_today(state, T0) == 2
    assert budget_left(state, settings, T0) == 38


def test_a_requested_unit_is_introduced_before_any_mined_one() -> None:
    """The owner's list comes first, in his order, whatever the ranks say.
    Its twin in tests/js/session.test.mjs pins the same deck and state to
    the same answer, since nothing else guards the two engines from drifting
    (owner, 2026-09-22)."""
    deck = _deck()
    # aufstehen is rank 4 of 4; asking for it puts it first
    deck.units = [
        u.model_copy(update={"requested_order": 1}) if u.unit_id == "sv:aufstehen" else u
        for u in deck.units
    ]
    engine = FSRSEngine()
    state = derive_state([], engine)
    assert [u.unit_id for u in introduction_order(deck)][0] == "sv:aufstehen"
    # sv:aufstehen has no glossed card in the fixture, so the scheduler skips
    # it and takes the next in order; the ordering itself is what is pinned
    assert introduction_order(deck)[1].unit_id == "vp:warten_auf"
    assert next_unit(deck, state, engine, Settings(), T0).unit_id == "vp:warten_auf"

    # and with a glossed card it is what comes first
    deck.cards_by_unit["sv:aufstehen"] = [
        _card("a20000000000", "sv:aufstehen", "Er steht früh auf.", ["steht", "auf"])
    ]
    assert next_unit(deck, state, engine, Settings(), T0).unit_id == "sv:aufstehen"


# ==============================================================================
# Words the learner adds from the page (the request entry)
# ==============================================================================


def _added_unit() -> PhraseUnit:
    return PhraseUnit(
        unit_id="wn:hähnchen",
        kind="noun",
        lemma_key="hähnchen",
        parts=["hähnchen"],
        display_de="das Hähnchen",
        sentence_count=46,
        rank=99_999,
        source="mined",
        card_count=1,
        glossed_card_count=1,
    )


def _added_card(unit: PhraseUnit) -> PhraseCard:
    return _card("cafecafecafe", unit.unit_id, "Das Hähnchen ist fertig.", ["Hähnchen"])


def test_request_entry_round_trips_and_orders_added_units_first(tmp_path: Path) -> None:
    """A word added on the page is taught before anything the corpus ranked,
    and before the owner's own requested.yaml list: the learner asked for it
    a moment ago."""
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    unit = _added_unit()
    log.record_request(
        unit_id=unit.unit_id, origin="generated", unit=unit, cards=[_added_card(unit)]
    )
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())

    assert state.requested == ["wn:hähnchen"]
    assert state.added_units["wn:hähnchen"].display_de == "das Hähnchen"
    deck = _deck().with_added(state)
    assert [u.unit_id for u in introduction_order(deck, state)][0] == "wn:hähnchen"
    # and the card came with it, so the unit is answerable
    assert [c.card_id for c in deck.cards_by_unit["wn:hähnchen"]] == ["cafecafecafe"]


def test_a_reserve_word_carries_no_payload_because_both_devices_fetch_the_same_shard(
    tmp_path: Path,
) -> None:
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    log.record_request(unit_id="wn:bügeln", origin="reserve")
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())

    assert state.requested == ["wn:bügeln"]
    assert not state.added_units and not state.added_cards
    # Nothing is invented for it: the shard is the deck's job, not the log's.
    assert _deck().with_added(state).by_id.get("wn:bügeln") is None


def test_the_deck_wins_when_a_written_word_is_later_mined_for_real(tmp_path: Path) -> None:
    """Once a real build teaches the word, its reviewed cards replace the
    written ones. The unit_id is the same either way, so the learner's
    history carries over rather than forking (rule 5)."""
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    unit = _added_unit().model_copy(update={"unit_id": "vp:warten_auf"})
    log.record_request(
        unit_id="vp:warten_auf",
        origin="generated",
        unit=unit,
        cards=[_card("dddddddddddd", "vp:warten_auf", "Erfundener Satz hier.", ["Satz"])],
    )
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())

    deck = _deck().with_added(state)
    assert deck.by_id["vp:warten_auf"].display_de == "warten auf"  # the deck's, not "das Haehnchen"
    assert [c.card_id for c in deck.cards_by_unit["vp:warten_auf"]] != ["dddddddddddd"]


def test_requesting_the_same_word_twice_does_not_queue_it_twice(tmp_path: Path) -> None:
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    log.record_request(unit_id="wn:bügeln", origin="reserve")
    log.record_request(unit_id="wn:bügeln", origin="reserve")
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())
    assert state.requested == ["wn:bügeln"]


def test_a_reset_forgets_the_added_words_like_everything_else(tmp_path: Path) -> None:
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    unit = _added_unit()
    log.record_request(unit_id=unit.unit_id, origin="generated", unit=unit)
    log.record_reset(note="starting over")
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())
    assert not state.requested and not state.added_units


def test_an_entry_type_this_client_does_not_know_is_skipped_not_fatal(tmp_path: Path) -> None:
    """The log gains entry types over time and the two clients update
    separately, so an older one must read past a newer line rather than
    lose the history after it."""
    path = tmp_path / "log.jsonl"
    log = ReviewLog(path, now=lambda: T0)
    log.record_mark(unit_id="cn:trotzdem", known=True, source="triage")
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"something_new","seq":9,"ts":"2026-09-10T09:00:00Z"}\n')
    log2 = ReviewLog(path, now=lambda: T0 + timedelta(hours=2))
    log2.record_mark(unit_id="sv:aufstehen", known=True, source="triage")

    state = derive_state(read_entries(path), FSRSEngine())
    assert state.known == {"cn:trotzdem", "sv:aufstehen"}


# ==============================================================================
# 2026-10-06: the owner's feedback after three weeks of real use
# ==============================================================================


def test_a_request_entry_survives_being_written_and_read_back(tmp_path: Path) -> None:
    """The JavaScript client dropped every "request" line when parsing, which
    deleted the words the owner had added: sync replaces the local log with
    the merge and pushes the merge back, so one parse on one device loses
    them everywhere. Python never had the bug (``parse_entry`` validates
    against the whole union), and this is here so it cannot acquire one."""
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    unit = _added_unit()
    log.record_request(unit_id=unit.unit_id, origin="generated", unit=unit)
    text = (tmp_path / "log.jsonl").read_text(encoding="utf-8")
    assert '"type":"request"' in text

    back = read_entries(tmp_path / "log.jsonl")
    assert [e.type for e in back] == ["request"]
    assert derive_state(back, FSRSEngine()).requested == [unit.unit_id]


def test_an_added_word_is_offered_before_the_due_queue(tmp_path: Path) -> None:
    """The owner added words and never saw one: with a hundred cards due the
    new-unit tier is days away, so the button's promise was false."""
    deck = _deck()
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    unit = _added_unit()
    log.record_request(
        unit_id=unit.unit_id, origin="generated", unit=unit, cards=[_added_card(unit)]
    )
    log.record_review(
        unit_id="vp:warten_auf",
        card_id="w20000000000",
        rating="again",
        outcome="wrong",
        answers=["x"],
        expected=["warten", "auf"],
        elapsed_ms=1,
        deck_version="t",
    )
    engine = FSRSEngine()
    state = derive_state(read_entries(tmp_path / "log.jsonl"), engine)
    full = deck.with_added(state)
    later = T0 + timedelta(days=2)

    assert any(
        u.unit_id == "vp:warten_auf" for u in due_units(full, state, engine, later, timedelta(0))
    )
    chosen = next_unit(full, state, engine, Settings(), later)
    assert chosen is not None and chosen.unit_id == unit.unit_id


def test_adding_many_words_does_not_cost_the_days_reviews(tmp_path: Path) -> None:
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    for index in range(9):
        unit = _added_unit().model_copy(
            update={
                "unit_id": f"nn:wort{index}",
                "lemma_key": f"wort{index}",
                "rank": 90_000 + index,
            }
        )
        log.record_request(
            unit_id=unit.unit_id, origin="generated", unit=unit, cards=[_added_card(unit)]
        )
    state = derive_state(read_entries(tmp_path / "log.jsonl"), FSRSEngine())
    full = _deck().with_added(state)
    settings = Settings()
    assert len(requested_to_introduce(full, state, settings, T0)) == settings.requested_per_day


def test_one_unit_is_not_asked_for_ever_in_a_single_sitting(tmp_path: Path) -> None:
    """Spacing alone cannot stop the loop: a unit the learner never gets
    right stays eligible, so the sitting narrows onto it. The cap is per
    sitting, so tomorrow it comes back."""
    deck = _deck()
    log = ReviewLog(tmp_path / "log.jsonl", now=lambda: T0)
    clock = [T0]
    log.now = lambda: clock[0]
    for minute in (0, 2, 4):
        clock[0] = T0 + timedelta(minutes=minute)
        log.record_review(
            unit_id="vp:warten_auf",
            card_id="w20000000000",
            rating="again",
            outcome="wrong",
            answers=["x", "auf"],
            expected=["warten", "auf"],
            elapsed_ms=1,
            deck_version="t",
        )
    engine = FSRSEngine()
    state = derive_state(read_entries(tmp_path / "log.jsonl"), engine)
    settings = Settings()
    now = T0 + timedelta(minutes=6)

    assert asks_this_session(state, settings, now)["vp:warten_auf"] == 3
    chosen = next_unit(deck, state, engine, settings, now)
    assert chosen is None or chosen.unit_id != "vp:warten_auf"

    tomorrow = T0 + timedelta(days=1)
    again = next_unit(deck, state, engine, settings, tomorrow)
    assert again is not None and again.unit_id == "vp:warten_auf"


def test_stability_lands_a_unit_in_the_right_band() -> None:
    assert stability_band(3.0) == "fresh"
    assert stability_band(7.0) == "young"
    assert stability_band(20.9) == "young"
    assert stability_band(21.0) == "mature"
    assert stability_band(89.0) == "mature"
    assert stability_band(400.0) == "solid"
