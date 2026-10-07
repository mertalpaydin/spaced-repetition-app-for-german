"""The finishing audit over the top of the deck.

Four build stages have to have run for the same unit before a learner meets a
complete card, and until 2026-10-07 nothing checked that they all had.
"""

from pathlib import Path

from scripts.audit_top_units import reviewed_ids, unfinished
from src.contracts import GapSpan, PhraseCard, PhraseUnit


def _unit(
    unit_id: str, rank: int, *, gloss: str | None = "to do", trivial: bool = False
) -> PhraseUnit:
    return PhraseUnit(
        unit_id=unit_id,
        kind="verb",
        lemma_key=unit_id.split(":")[-1],
        parts=[unit_id.split(":")[-1]],
        display_de=unit_id.split(":")[-1],
        gloss_en=gloss,
        sentence_count=100,
        rank=rank,
        trivial=trivial,
        source="mined",
        card_count=1,
    )


def _card(card_id: str, unit_id: str, *, gloss: str | None = "English") -> PhraseCard:
    return PhraseCard(
        card_id=card_id,
        unit_id=unit_id,
        kind="verb",
        sentence_de="Hier steht ein Satz.",
        gloss_en=gloss,
        gloss_source="azure" if gloss else None,
        gaps=[GapSpan(start=0, end=4, answer="Hier", token_index=0)],
        answers=["Hier"],
        form_key="",
        corpus_source="tatoeba",
        corpus_line_id="1",
    )


def test_each_missing_finishing_is_reported_separately() -> None:
    units = [
        _unit("vb:done", 1),
        _unit("vb:nocard", 2),
        _unit("vb:nosentence", 3),
        _unit("vb:nophrase", 4, gloss=None),
        _unit("vb:unread", 5),
    ]
    cards = {
        "vb:done": [_card("a" * 12, "vb:done")],
        "vb:nosentence": [_card("b" * 12, "vb:nosentence", gloss=None)],
        "vb:nophrase": [_card("c" * 12, "vb:nophrase")],
        "vb:unread": [_card("d" * 12, "vb:unread")],
    }
    reviewed_cards = {"a" * 12, "c" * 12}
    reviewed_units = {u.unit_id for u in units}

    found = unfinished(units, cards, reviewed_cards, reviewed_units)
    assert [u.unit_id for u in found["no_card"]] == ["vb:nocard"]
    assert [u.unit_id for u in found["no_sentence_translation"]] == ["vb:nosentence"]
    assert [u.unit_id for u in found["no_phrase_translation"]] == ["vb:nophrase"]
    assert [u.unit_id for u in found["unreviewed_cards"]] == ["vb:unread"]
    assert found["unreviewed_unit"] == []


def test_a_unit_with_no_card_is_not_also_reported_as_unreviewed() -> None:
    """One problem per unit at the top: a card-less unit has nothing to
    review, and listing it under both would double-count the work."""
    units = [_unit("vb:nocard", 1)]
    found = unfinished(units, {}, set(), set())
    assert [u.unit_id for u in found["no_card"]] == ["vb:nocard"]
    assert found["unreviewed_cards"] == []
    assert found["no_phrase_translation"] == []


def test_trivial_units_are_not_counted() -> None:
    """The scheduler never shows them, so they need no finishing."""
    units = [_unit("vb:und", 1, gloss=None, trivial=True)]
    found = unfinished(units, {}, set(), set())
    assert all(not v for v in found.values())


def test_reviewed_ids_reads_every_round_ledger(tmp_path: Path) -> None:
    (tmp_path / "reviewed-card-ids-round-one.txt").write_text("aaa\nbbb\n", encoding="utf-8")
    (tmp_path / "reviewed-card-ids-round-two.txt").write_text("ccc\n\n", encoding="utf-8")
    (tmp_path / "reviewed-unit-ids-round-one.txt").write_text("vb:x\n", encoding="utf-8")

    assert reviewed_ids("card", tmp_path) == {"aaa", "bbb", "ccc"}
    assert reviewed_ids("unit", tmp_path) == {"vb:x"}
    assert reviewed_ids("card", tmp_path / "absent") == set()
