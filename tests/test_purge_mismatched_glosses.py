"""Tests for scripts/purge_mismatched_glosses.py: removing the glosses the
cross-corpus id collision poisoned.

CLAUDE.md section 7: offline, ``tmp_path``, no network. ``purge`` takes the
corpora as plain text sets, so nothing here reads a staged corpus either;
only the store is real filesystem I/O, because rewriting the store correctly
is the thing under test.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.build_translations import TranslationRecord, _load_store, _write_store_atomic
from scripts.purge_mismatched_glosses import find_contradictory, main, purge

# The two real defects, from a hand audit of 430 accepted exercises. Both
# German sentences are Leipzig; both English sentences are obviously Tatoeba.
_GRAZ = (
    "Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung schlimmer ist als gedacht."
)
_CROSSED = "She crossed the street."
_CHEMO = "Jetzt gibt sie ein Update zu ihrem Alltag während der Chemotherapie."
_BYE = "Bye!"

_NOW = datetime(2026, 8, 27, tzinfo=UTC)


def _record(german: str, english: str, source: str) -> TranslationRecord:
    return TranslationRecord(
        german=german,
        english=english,
        source=source,  # type: ignore[arg-type]
        written_at=_NOW,
    )


def _write_store(path: Path, records: list[TranslationRecord]) -> None:
    _write_store_atomic(path, {record.german: record for record in records})


def test_purge_removes_only_the_contradictory_records(tmp_path: Path) -> None:
    """The whole rule, exercised on one store holding every case at once:
    the two real defects, a correctly machine-translated Leipzig carrier, a
    genuine Tatoeba record, and a record for a sentence in neither corpus
    (which is what --carriers-from runs put there)."""
    store_path = tmp_path / "de_en.jsonl"
    tatoeba_carrier = "Sie überquerte die Straße."
    leipzig_machine = "Der Minister hat die Entscheidung am Montag verteidigt."
    unknown_origin = "Ein Satz aus einer Karrierendatei."
    _write_store(
        store_path,
        [
            _record(_GRAZ, _CROSSED, "tatoeba"),
            _record(_CHEMO, _BYE, "tatoeba"),
            _record(leipzig_machine, "The minister defended the decision.", "azure"),
            _record(tatoeba_carrier, _CROSSED, "tatoeba"),
            _record(unknown_origin, "A sentence from a carrier file.", "gemini"),
        ],
    )

    result = purge(
        store_path=store_path,
        leipzig_texts=frozenset({_GRAZ, _CHEMO, leipzig_machine}),
        dry_run=False,
    )

    assert result.removed == 2
    assert result.store_size_before == 5
    assert result.store_size_after == 3
    assert result.leipzig_carriers_in_store == 3

    remaining = _load_store(store_path)
    assert set(remaining) == {leipzig_machine, tatoeba_carrier, unknown_origin}
    # Nothing survived with a rewritten gloss: repair here means removal,
    # because the correct English is not known and inventing one is how this
    # class of bug starts.
    assert remaining[leipzig_machine].english == "The minister defended the decision."


def test_purge_leaves_a_clean_store_completely_untouched(tmp_path: Path) -> None:
    """A store with nothing contradictory in it must not be rewritten at
    all -- not even to the identical bytes, since a needless rewrite of the
    one file the whole backfill resumes from is pure risk for no gain."""
    store_path = tmp_path / "de_en.jsonl"
    leipzig_machine = "Der Minister hat die Entscheidung am Montag verteidigt."
    _write_store(
        store_path,
        [
            _record(leipzig_machine, "The minister defended the decision.", "azure"),
            _record("Sie überquerte die Straße.", _CROSSED, "tatoeba"),
        ],
    )
    before_bytes = store_path.read_bytes()
    before_mtime = store_path.stat().st_mtime_ns

    result = purge(
        store_path=store_path,
        leipzig_texts=frozenset({leipzig_machine}),
        dry_run=False,
    )

    assert result.removed == 0
    assert result.store_size_after == 2
    assert store_path.read_bytes() == before_bytes
    assert store_path.stat().st_mtime_ns == before_mtime


def test_purge_dry_run_writes_nothing_but_reports_what_it_would_remove(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    _write_store(
        store_path,
        [_record(_GRAZ, _CROSSED, "tatoeba"), _record(_CHEMO, _BYE, "tatoeba")],
    )
    before_bytes = store_path.read_bytes()

    result = purge(
        store_path=store_path,
        leipzig_texts=frozenset({_GRAZ, _CHEMO}),
        dry_run=True,
    )

    assert result.removed == 2
    # The counts a dry run reports describe the store as it stands, not as it
    # would stand: nothing was removed, so nothing is "after".
    assert result.store_size_before == 2
    assert result.store_size_after == 2
    assert store_path.read_bytes() == before_bytes
    assert len(_load_store(store_path)) == 2
    # And no stray temp file from the atomic-write path either.
    assert [p.name for p in tmp_path.iterdir()] == ["de_en.jsonl"]


def test_purge_dry_run_examples_carry_the_german_and_the_wrong_english(tmp_path: Path) -> None:
    """The examples are the point of a dry run: a count says how much is
    wrong, an example says whether the rule is selecting the right thing."""
    store_path = tmp_path / "de_en.jsonl"
    _write_store(store_path, [_record(_GRAZ, _CROSSED, "tatoeba")])

    result = purge(store_path=store_path, leipzig_texts=frozenset({_GRAZ}), dry_run=True)

    assert result.examples == [(_GRAZ, _CROSSED)]


def test_purge_example_limit_bounds_the_output_not_the_removal(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    texts = [f"Der Minister hat am {i}. Montag eine Entscheidung verteidigt." for i in range(20)]
    _write_store(store_path, [_record(text, _BYE, "tatoeba") for text in texts])

    result = purge(
        store_path=store_path,
        leipzig_texts=frozenset(texts),
        dry_run=False,
        example_limit=3,
    )

    assert result.removed == 20
    assert len(result.examples) == 3
    assert _load_store(store_path) == {}


def test_purge_spares_a_leipzig_carrier_whose_text_is_genuinely_in_tatoeba(
    tmp_path: Path,
) -> None:
    """The one honest exception. A sentence present in both corpora really
    can carry a Tatoeba gloss, because the fixed join still matches it by
    text -- so removing it would only make the next backfill re-add the
    identical record, for ever."""
    store_path = tmp_path / "de_en.jsonl"
    shared = "Der Hund läuft schnell durch den grünen Park."
    _write_store(
        store_path,
        [_record(_GRAZ, _CROSSED, "tatoeba"), _record(shared, "The dog runs.", "tatoeba")],
    )

    result = purge(
        store_path=store_path,
        leipzig_texts=frozenset({_GRAZ, shared}),
        tatoeba_texts=frozenset({shared}),
        dry_run=False,
    )

    assert result.removed == 1
    assert result.spared_text_matches == 1
    assert set(_load_store(store_path)) == {shared}


def test_find_contradictory_ignores_machine_translated_leipzig_records() -> None:
    """A Leipzig carrier labelled azure or gemini is a correct machine
    translation, not a collision. Only ``source="tatoeba"`` is the
    contradiction."""
    store = {
        _GRAZ: _record(_GRAZ, _CROSSED, "tatoeba"),
        _CHEMO: _record(_CHEMO, "Now she gives an update.", "azure"),
    }

    assert find_contradictory(store, frozenset({_GRAZ, _CHEMO}), frozenset()) == [_GRAZ]


def test_main_missing_store_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purge_mismatched_glosses.py",
            "--store",
            str(tmp_path / "nope.jsonl"),
            "--leipzig",
            str(tmp_path / "leipzig.txt"),
        ],
    )
    assert main() == 1


def test_main_missing_leipzig_corpus_refuses_rather_than_reporting_a_clean_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the Leipzig corpus nothing can be identified as
    contradictory, and "removed 0" would read as "the store is clean". Those
    are different facts, so this exits nonzero and says which one it is."""
    store_path = tmp_path / "de_en.jsonl"
    _write_store(store_path, [_record(_GRAZ, _CROSSED, "tatoeba")])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purge_mismatched_glosses.py",
            "--store",
            str(store_path),
            "--leipzig",
            str(tmp_path / "absent.txt"),
        ],
    )

    assert main() == 1
    assert "no Leipzig carriers" in capsys.readouterr().out
    assert len(_load_store(store_path)) == 1


def test_main_dry_run_end_to_end_reads_a_real_leipzig_file_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one test that goes through the corpus reader, so the length filter
    that decides "this is a Leipzig carrier" is the same one that decided it
    when the record was written."""
    store_path = tmp_path / "de_en.jsonl"
    _write_store(store_path, [_record(_GRAZ, _CROSSED, "tatoeba")])
    leipzig = tmp_path / "leipzig.txt"
    leipzig.write_text(f"541845\t{_GRAZ}\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "purge_mismatched_glosses.py",
            "--store",
            str(store_path),
            "--leipzig",
            str(leipzig),
            "--dry-run",
        ],
    )

    assert main() == 0
    out = capsys.readouterr().out
    assert "Would remove:" in out
    assert "DRY RUN" in out
    assert _CROSSED in out
    assert len(_load_store(store_path)) == 1


def test_main_applies_the_purge_and_prints_removed_and_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    keeper = "Der Minister hat die Entscheidung am Montag deutlich verteidigt."
    _write_store(
        store_path,
        [
            _record(_GRAZ, _CROSSED, "tatoeba"),
            _record(keeper, "The minister defended the decision.", "azure"),
        ],
    )
    leipzig = tmp_path / "leipzig.txt"
    leipzig.write_text(f"541845\t{_GRAZ}\n900001\t{keeper}\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["purge_mismatched_glosses.py", "--store", str(store_path), "--leipzig", str(leipzig)],
    )

    assert main() == 0
    out = capsys.readouterr().out
    assert "Removed:" in out
    assert "Records remaining:" in out
    assert set(_load_store(store_path)) == {keeper}
