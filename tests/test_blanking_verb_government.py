"""Unit tests for src/generation/blanking/verb_government.py (TODO 8.1)."""

import json
from pathlib import Path

from src.generation.blanking import verb_government


def _write_fixture(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": True, "version": 1}) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_reflexive_verdict_reads_corpus_final_verdict_from_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "lex.jsonl"
    _write_fixture(
        fixture,
        [
            {
                "verb": "verbeugen",
                "final": {"reflexive": "acc", "object": None},
            }
        ],
    )
    assert verb_government.reflexive_verdict("verbeugen", path=fixture) == "Acc"


def test_reflexive_verdict_falls_back_to_hand_list_when_fixture_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jsonl"
    assert verb_government.reflexive_verdict("helfen", path=missing) == "Dat"
    assert verb_government.reflexive_verdict("freuen", path=missing) == "Acc"
    assert verb_government.reflexive_verdict("verbeugen", path=missing) is None


def test_reflexive_verdict_falls_back_to_hand_list_when_verb_absent_from_fixture(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "lex.jsonl"
    _write_fixture(fixture, [{"verb": "kaufen", "final": {"reflexive": None, "object": None}}])
    assert verb_government.reflexive_verdict("helfen", path=fixture) == "Dat"
    assert verb_government.reflexive_verdict("freuen", path=fixture) == "Acc"


def test_reflexive_verdict_returns_none_for_a_mixed_or_unknown_verb(tmp_path: Path) -> None:
    fixture = tmp_path / "lex.jsonl"
    _write_fixture(
        fixture,
        [{"verb": "vorstellen", "final": {"reflexive": None, "object": None}}],
    )
    assert verb_government.reflexive_verdict("vorstellen", path=fixture) is None
    assert verb_government.reflexive_verdict("nichtvorhanden", path=fixture) is None


def test_object_verdict_reads_corpus_final_verdict_from_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "lex.jsonl"
    _write_fixture(
        fixture,
        [{"verb": "glauben", "final": {"reflexive": None, "object": "dat"}}],
    )
    assert verb_government.object_verdict("glauben", path=fixture) == "Dat"


def test_object_verdict_falls_back_to_dative_only_hand_list(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jsonl"
    assert verb_government.object_verdict("helfen", path=missing) == "Dat"
    assert verb_government.object_verdict("geben", path=missing) is None


def test_object_verdict_does_not_treat_ditransitive_hand_list_as_forcing(tmp_path: Path) -> None:
    """``DITRANSITIVE_DATIVE_VERBS`` (e.g. "geben") is deliberately not read
    by ``_hand_object_verdict`` -- membership there was never sufficient
    evidence on its own; the selector's own accompanying-object check
    handles those, unchanged, outside this module."""
    missing = tmp_path / "does_not_exist.jsonl"
    assert verb_government.object_verdict("geben", path=missing) is None
    assert verb_government.object_verdict("zeigen", path=missing) is None


def test_load_degrades_cleanly_on_a_corrupt_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "lex.jsonl"
    fixture.write_text("not json at all\n{broken\n", encoding="utf-8")
    assert verb_government.reflexive_verdict("helfen", path=fixture) == "Dat"
    assert verb_government.object_verdict("irgendein_unbekanntes_verb", path=fixture) is None


def test_committed_fixture_loads_and_produces_the_documented_hand_list_verdicts() -> None:
    """Regression pin against the real, committed fixture (not a fake one):
    every verb on the two simple hand lists must still resolve to its own
    hand-listed verdict through the real lexicon file, confirming the
    merge in ``build_records`` never lost a hand-listed verb."""
    assert verb_government.reflexive_verdict("helfen") == "Dat"
    assert verb_government.reflexive_verdict("freuen") == "Acc"
    assert verb_government.reflexive_verdict("treffen") == "Acc"
    assert verb_government.reflexive_verdict("ändern") == "Acc"
    assert verb_government.reflexive_verdict("ansammeln") == "Acc"
    assert verb_government.reflexive_verdict("beeilen") == "Acc"
    assert verb_government.object_verdict("helfen") == "Dat"
    assert verb_government.object_verdict("gehören") == "Dat"
