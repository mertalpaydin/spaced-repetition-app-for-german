"""The exported deck: round trip, bands, determinism, the pinned schema."""

import json
from datetime import UTC, datetime
from pathlib import Path

from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.export import DEFAULT_SCHEMA_PATH, check_deck, deck_schema, export_deck, load_deck

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _unit(rank: int) -> PhraseUnit:
    return PhraseUnit(
        unit_id=f"vp:unit_{rank}",
        kind="verb_prep",
        lemma_key=f"unit {rank}",
        parts=["unit", str(rank)],
        display_de=f"unit {rank}",
        sentence_count=100 - rank,
        rank=rank,
        source="mined",
        card_count=1,
    )


def _card(unit: PhraseUnit) -> PhraseCard:
    text = f"Satz {unit.rank} hier."
    return PhraseCard(
        card_id=f"{unit.rank:012d}",
        unit_id=unit.unit_id,
        kind="verb_prep",
        sentence_de=text,
        gloss_en="gloss",
        gloss_source="azure",
        gaps=[GapSpan(start=0, end=4, answer="Satz", token_index=0)],
        answers=["Satz"],
        form_key="",
        corpus_source="tatoeba",
        corpus_line_id=str(unit.rank),
    )


def test_export_round_trips_and_bands_by_rank(tmp_path: Path) -> None:
    units = [_unit(r) for r in range(1, 6)]
    cards = [_card(u) for u in units]
    manifest = export_deck(units, cards, tmp_path, shard_size=2, now=lambda: NOW)
    assert [s.rank_from for s in manifest.shards] == [1, 3, 5]
    loaded_manifest, loaded_units, loaded_cards = load_deck(tmp_path)
    assert loaded_manifest == manifest
    assert loaded_units == units and sorted(loaded_cards, key=lambda c: c.card_id) == cards
    assert check_deck(tmp_path) == []


def test_export_is_byte_deterministic(tmp_path: Path) -> None:
    units = [_unit(r) for r in range(1, 4)]
    cards = [_card(u) for u in units]
    export_deck(units, cards, tmp_path / "a", now=lambda: NOW)
    export_deck(list(reversed(units)), list(reversed(cards)), tmp_path / "b", now=lambda: NOW)
    for file in ("manifest.json", "units.json", "shards/band_000.json"):
        assert (tmp_path / "a" / file).read_bytes() == (tmp_path / "b" / file).read_bytes()


def test_deck_version_changes_with_content(tmp_path: Path) -> None:
    units = [_unit(1)]
    v1 = export_deck(units, [_card(units[0])], tmp_path / "a", now=lambda: NOW).deck_version
    v2 = export_deck(units, [], tmp_path / "b", now=lambda: NOW).deck_version
    assert v1 != v2


def test_orphan_card_is_refused(tmp_path: Path) -> None:
    orphan = _card(_unit(9))
    try:
        export_deck([_unit(1)], [orphan], tmp_path, now=lambda: NOW)
    except ValueError as exc:
        assert "no unit" in str(exc)
    else:
        raise AssertionError("an orphan card must be refused")


def test_stale_shards_are_removed_on_rebuild(tmp_path: Path) -> None:
    units = [_unit(r) for r in range(1, 6)]
    export_deck(units, [_card(u) for u in units], tmp_path, shard_size=1, now=lambda: NOW)
    export_deck(units[:2], [_card(u) for u in units[:2]], tmp_path, shard_size=1, now=lambda: NOW)
    assert sorted(p.name for p in (tmp_path / "shards").glob("*.json")) == [
        "band_000.json",
        "band_001.json",
    ]


def test_committed_schema_matches_the_models() -> None:
    committed = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == deck_schema(), (
        "the deck models changed; regenerate with scripts/build_phrase_deck.py --stage export "
        "and explain the format change in the commit"
    )
