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
    ReviewEntry,
)
from src.engine.fsrs import FSRSEngine
from src.engine.grading import grade_card, render_marked, render_with_gaps
from src.engine.review_log import ReviewLog, derive_state, merge_entries, read_entries
from src.engine.session import Deck, Settings, next_unit, pick_card, showable_cards
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
    assert grade_card(card, ["warte", "auf"]).rating == "again"  # ending changed
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
    # budget spent (1 of 1), warten is due in 10 min: a new unit is not offered
    # within budget, offered over the limit; and warten is not repeated while
    # another unit exists
    soon = T0 + timedelta(minutes=1)
    assert next_unit(deck, state, engine, tight, soon) is None
    assert next_unit(deck, state, engine, tight, soon, over_limit=True).unit_id == "cn:trotzdem"
    later = state.records["vp:warten_auf"].due + timedelta(minutes=1)
    assert next_unit(deck, state, engine, tight, later).unit_id == "vp:warten_auf"


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


def test_pick_card_keeps_praeteritum_until_the_unit_is_in_review() -> None:
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
    assert pick_card(deck, unit, state).card_id == "w20000000000"  # never the past form yet
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
    assert pick_card(deck, unit, state).card_id == "p10000000000"


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
