"""Tests for scripts/build_translations.py (TODO.md 5.1 step 2): the
resumable English-gloss backfill.

CLAUDE.md section 7: unit tests never touch the network. Every test here
drives ``run_backfill`` directly with an in-memory carrier set and a fake
``Translator`` (the protocol in ``src/llm/translation.py`` makes this
substitution trivial) -- no real Azure or Gemini call is ever made, and no
corpus or Tatoeba pairs file is read from disk. Only the store itself is
real filesystem I/O, via ``tmp_path``, because the store IS the
resumability mechanism under test.

Every test that exercises the Tatoeba join passes ``trust_tatoeba=True``
explicitly. That is not boilerplate: the join is OFF by default as of the
owner's 2026-08-27 decision (a hand audit of 430 accepted exercises found 4
wrong glosses and 3 of the 4 were Tatoeba's own human translations), so a
test that wants the join has to ask for it. The cross-corpus id-collision
tests further down are the ones that most need it -- without the flag they
would pass for the wrong reason, by the join never running at all, and the
fence they exist to guard would go unexercised.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts import build_translations
from scripts.build_translations import (
    BatchOutcome,
    CrossCorpusGlossError,
    TranslationBackfillReport,
    TranslationRecord,
    _default_batch_size,
    _fill_from_tatoeba,
    _load_store,
    _read_carriers_from_file,
    _shortest_translations,
    _write_store_atomic,
    main,
    reject_cross_corpus_gloss,
    run_backfill,
    tatoeba_policy_sentence,
)
from scripts.corpus_reading import SOURCE_LEIPZIG, SOURCE_TATOEBA, CorpusLine
from scripts.eval_tatoeba_translation_quality import Pair
from src.llm.translation import (
    AZURE_MAX_BATCH,
    FallbackTranslator,
    TranslationError,
    Translator,
)


def _carrier(text: str, line_id: str = "", source: str = "") -> CorpusLine:
    return CorpusLine(line_id=line_id, text=text, source=source)


@dataclass
class FakeTranslator:
    """Records every batch it is asked to translate, for assertions, and
    never touches the network. Satisfies the ``Translator`` protocol
    structurally (no inheritance needed)."""

    calls: list[list[str]] = field(default_factory=list)
    prefix: str = "EN: "

    def translate(self, sentences: Sequence[str]) -> list[str]:
        self.calls.append(list(sentences))
        return [f"{self.prefix}{s}" for s in sentences]


@dataclass
class FailingTranslator:
    """Always refuses -- stands in for "both providers down"."""

    def translate(self, sentences: Sequence[str]) -> list[str]:
        raise TranslationError("simulated provider failure")


@dataclass
class TransportBoomTranslator:
    """Raises a plain, non-``TranslationError`` exception -- reproduces a
    real failure seen driving this script live in this sandbox: a Gemini key
    IS configured, so ``GeminiTranslator`` genuinely attempts a call, but the
    outbound request hits this sandbox's own proxy with a 403
    (``httpx``/``httpcore`` ``ProxyError``), which neither ``AzureTranslator``
    nor ``GeminiTranslator`` wraps as ``TranslationError`` since it never
    reaches the provider at all. Regression test for that crash."""

    def translate(self, sentences: Sequence[str]) -> list[str]:
        raise ConnectionError("simulated transport failure (e.g. a blocked proxy)")


def _assert_is_translator(_: Translator) -> None:
    """Compile-time-ish sanity check that the fakes above really do satisfy
    the protocol; exercised by every test that constructs one."""


# ==============================================================================
# Resumability
# ==============================================================================


def test_run_backfill_rerun_carriers_already_in_store_translates_nothing_new(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {
        "Der Hund läuft schnell.": _carrier("Der Hund läuft schnell.", "1"),
        "Die Katze schläft.": _carrier("Die Katze schläft.", "2"),
    }
    translator = FakeTranslator()
    _assert_is_translator(translator)

    first = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )
    assert first.machine_translated == 2
    assert first.already_in_store == 0
    assert len(translator.calls) == 1

    second = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )
    assert second.machine_translated == 0
    assert second.already_in_store == 2
    # No new translate() calls at all -- the second run never asked the
    # translator for anything, not just "asked and got a cached answer".
    assert len(translator.calls) == 1

    store = _load_store(store_path)
    assert len(store) == 2


# ==============================================================================
# Shortest translation wins
# ==============================================================================


def test_shortest_translations_german_sentence_with_multiple_translations_keeps_shortest() -> None:
    pairs = [
        Pair(german_id="1", german="Ich bin müde.", english="I am so very tired today."),
        Pair(german_id="1", german="Ich bin müde.", english="I am tired."),
        Pair(german_id="1", german="Ich bin müde.", english="I am exhausted right now."),
    ]
    by_id, by_text = _shortest_translations(pairs)
    assert by_id["1"] == "I am tired."
    assert by_text["Ich bin müde."] == "I am tired."


def test_run_backfill_tatoeba_pairs_multiple_translations_stores_shortest(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {"Ich bin müde.": _carrier("Ich bin müde.", "1")}
    pairs = [
        Pair(german_id="1", german="Ich bin müde.", english="I am so very tired today."),
        Pair(german_id="1", german="Ich bin müde.", english="I am tired."),
    ]

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=None,
        translator_mode="none",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=1000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )
    assert report.from_tatoeba == 1
    assert report.machine_translated == 0

    store = _load_store(store_path)
    assert store["Ich bin müde."].english == "I am tired."
    assert store["Ich bin müde."].source == "tatoeba"


# ==============================================================================
# Budget stop
# ==============================================================================


def test_run_backfill_max_characters_exceeded_stops_cleanly_and_reports_skipped(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    texts = [f"Satz Nummer {i} ist hier." for i in range(5)]
    carriers = {text: _carrier(text, str(i)) for i, text in enumerate(texts)}
    one_length = len(texts[0])
    assert all(len(t) == one_length for t in texts)  # keeps the budget math exact
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=one_length * 2,  # room for exactly 2 of the 5
        batch_size=1,  # fine-grained, so the stop is exact rather than batch-lumpy
        seed=7,
        limit_per_source=1000,
    )

    assert report.machine_translated == 2
    assert report.skipped_for_budget == 3
    assert report.failed == 0
    # The stop itself is unchanged, but the accounting behind it is now split:
    # this run is gemini_only, so Azure translated nothing and characters_spent
    # (which exists to protect the F0 monthly allowance) must stay at zero,
    # while the characters really translated show up as Gemini's. The budget
    # still stops the run on the two counters combined, which is why 2 of 5
    # carriers land here exactly as before.
    assert report.characters_spent == 0
    assert report.gemini_fallback_characters == one_length * 2

    store = _load_store(store_path)
    assert len(store) == 2

    # Rerun with a fresh (larger) budget: the 2 already stored are skipped,
    # the remaining 3 get picked up -- the run never lost track of where it
    # left off, because the store itself is the position.
    second = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=one_length * 10,
        batch_size=1,
        seed=7,
        limit_per_source=1000,
    )
    assert second.already_in_store == 2
    assert second.machine_translated == 3
    assert len(_load_store(store_path)) == 5


# ==============================================================================
# Atomic write
# ==============================================================================


def test_write_store_atomic_replace_fails_leaves_original_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    original_content = (
        '{"german": "a", "english": "b", "source": "tatoeba", '
        '"written_at": "2026-01-01T00:00:00+00:00"}\n'
    )
    store_path.write_text(original_content, encoding="utf-8")
    original_mtime = store_path.stat().st_mtime_ns

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _boom)

    new_store = {
        "c": TranslationRecord(
            german="c", english="d", source="gemini", written_at=datetime.now(UTC)
        )
    }
    with pytest.raises(OSError):
        _write_store_atomic(store_path, new_store)

    assert store_path.stat().st_mtime_ns == original_mtime
    assert store_path.read_text(encoding="utf-8") == original_content


def test_write_store_atomic_replace_fails_leaves_no_stray_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_path = tmp_path / "de_en.jsonl"

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", _boom)

    new_store = {
        "c": TranslationRecord(
            german="c", english="d", source="gemini", written_at=datetime.now(UTC)
        )
    }
    with pytest.raises(OSError):
        _write_store_atomic(store_path, new_store)

    assert not store_path.exists()
    assert list(tmp_path.iterdir()) == []


# ==============================================================================
# Gemini-only mode
# ==============================================================================


def test_run_backfill_no_azure_key_gemini_only_mode_warns_loudly(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {"Guten Morgen.": _carrier("Guten Morgen.", "1")}
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.translator_mode == "gemini_only"
    assert any("GEMINI" in w.upper() and "AZURE" in w.upper() for w in report.warnings)

    store = _load_store(store_path)
    assert store["Guten Morgen."].source == "gemini"


def test_run_backfill_no_translator_at_all_skips_machine_translation(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {"Guten Morgen.": _carrier("Guten Morgen.", "1")}

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=None,
        translator_mode="none",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.machine_translated == 0
    assert report.skipped_for_budget == 1
    assert any("NO TRANSLATOR" in w.upper() for w in report.warnings)


# ==============================================================================
# Failures do not count as spent budget and are left retriable
# ==============================================================================


def test_run_backfill_translator_raises_records_failure_not_success(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {"Hallo Welt.": _carrier("Hallo Welt.", "1")}

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FailingTranslator(),
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.failed == 1
    assert report.machine_translated == 0
    assert report.characters_spent == 0
    assert len(report.failure_examples) == 1


def test_run_backfill_translator_raises_transport_error_does_not_crash_the_run(
    tmp_path: Path,
) -> None:
    """Regression test: a live run in this sandbox crashed with an uncaught
    ``httpx.ProxyError`` from inside ``GeminiTranslator.translate`` -- a
    transport failure, not a ``TranslationError``. The whole backfill must
    degrade the same way it does for a documented ``TranslationError``, not
    crash before the report or store is ever written."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {
        "Hallo Welt.": _carrier("Hallo Welt.", "1"),
        "Guten Tag.": _carrier("Guten Tag.", "2"),
    }

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=TransportBoomTranslator(),
        translator_mode="gemini_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.failed == 2
    assert report.machine_translated == 0
    assert report.characters_spent == 0
    assert report.failure_examples
    assert "ConnectionError" in report.failure_examples[0]

    store = _load_store(store_path)
    assert "Hallo Welt." not in store


# ==============================================================================
# Small pure-function coverage
# ==============================================================================


def test_default_batch_size_gemini_only_mode_returns_one() -> None:
    assert _default_batch_size("gemini_only") == 1


def test_default_batch_size_fallback_mode_returns_azure_max_batch() -> None:
    assert _default_batch_size("fallback") == AZURE_MAX_BATCH
    assert _default_batch_size("azure_only") == AZURE_MAX_BATCH


# ==============================================================================
# Azure characters and Gemini fallback characters are never conflated
#
# The per-run budget exists to protect Azure's monthly F0 allowance, so a
# batch Azure refused and Gemini translated spent none of it. The script used
# to charge those characters to characters_spent anyway, contradicting its own
# module docstring, which already reasons correctly about the identical case
# for a failed batch.
# ==============================================================================


def test_run_backfill_fallback_batch_counts_gemini_characters_not_azure_ones(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    text = "Hallo Welt."
    carriers = {text: _carrier(text, "1")}
    translator = FallbackTranslator(
        primary=FailingTranslator(), fallback=FakeTranslator(prefix="GEM: ")
    )

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.machine_translated == 1
    assert report.characters_spent == 0
    assert report.gemini_fallback_characters == len(text)
    assert report.azure_fallback_events == 1
    assert _load_store(store_path)[text].source == "gemini"


def test_run_backfill_azure_batch_counts_azure_characters_not_gemini_ones(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    text = "Hallo Welt."
    carriers = {text: _carrier(text, "1")}
    translator = FallbackTranslator(
        primary=FakeTranslator(prefix="AZ: "), fallback=FakeTranslator(prefix="GEM: ")
    )

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.characters_spent == len(text)
    assert report.gemini_fallback_characters == 0
    assert _load_store(store_path)[text].source == "azure"


def test_run_backfill_report_json_carries_both_character_counters(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    text = "Hallo Welt."
    translator = FallbackTranslator(
        primary=FailingTranslator(), fallback=FakeTranslator(prefix="GEM: ")
    )

    report = run_backfill(
        carriers={text: _carrier(text, "1")},
        store_path=store_path,
        translator=translator,
        translator_mode="fallback",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )
    written = report.to_dict()

    assert written["characters_spent"] == 0
    assert written["gemini_fallback_characters"] == len(text)


# ==============================================================================
# --carriers-from: the per-carrier mode
#
# The whole-corpus backfill is the wrong shape for the item pipeline: 248,935
# carriers still need machine translation at roughly 17,000,000 characters,
# about eight and a half months of the F0 monthly allowance, while one 396-item
# pilot needs 166 new translations and about 11,000 characters.
# ==============================================================================


def test_read_carriers_from_file_plain_sentences_reads_every_line(tmp_path: Path) -> None:
    path = tmp_path / "carriers.txt"
    path.write_text("Der Hund läuft.\nDie Katze schläft.\n", encoding="utf-8")

    carriers, _ = _read_carriers_from_file(path)

    assert set(carriers) == {"Der Hund läuft.", "Die Katze schläft."}
    # No corpus id exists in this format; _fill_from_tatoeba falls back to an
    # exact-text match when line_id is empty, which is what makes that safe.
    assert all(line.line_id == "" for line in carriers.values())


def test_read_carriers_from_file_jsonl_takes_the_german_key(tmp_path: Path) -> None:
    path = tmp_path / "items.jsonl"
    path.write_text(
        '{"item_id": "a1", "german": "Der Hund läuft.", "tag_id": "x"}\n'
        '{"item_id": "a2", "german": "Die Katze schläft."}\n',
        encoding="utf-8",
    )

    carriers, _ = _read_carriers_from_file(path)

    assert set(carriers) == {"Der Hund läuft.", "Die Katze schläft."}


def test_read_carriers_from_file_json_object_without_german_key_is_skipped(
    tmp_path: Path,
) -> None:
    """Skipped rather than taken literally: storing the raw JSON text as a
    German sentence would put an untranslatable line in the store and spend
    real characters on it."""
    path = tmp_path / "items.jsonl"
    path.write_text(
        '{"item_id": "a1", "prompt": "no german key here"}\n{"german": "Der Hund läuft."}\n',
        encoding="utf-8",
    )

    carriers, _ = _read_carriers_from_file(path)

    assert set(carriers) == {"Der Hund läuft."}


def test_read_carriers_from_file_blank_lines_and_duplicates_collapse(tmp_path: Path) -> None:
    path = tmp_path / "carriers.txt"
    path.write_text(
        "\n  Der Hund läuft.  \n\nDie Katze schläft.\nDer Hund läuft.\n   \n",
        encoding="utf-8",
    )

    carriers, _ = _read_carriers_from_file(path)

    assert set(carriers) == {"Der Hund läuft.", "Die Katze schläft."}
    assert len(carriers) == 2


def test_read_carriers_from_file_non_json_line_with_a_brace_is_taken_literally(
    tmp_path: Path,
) -> None:
    """Only a line that really parses as a JSON OBJECT gets the ``german``-key
    treatment. German text that merely opens with a brace is a sentence."""
    path = tmp_path / "carriers.txt"
    path.write_text("{das ist kein JSON}\n", encoding="utf-8")

    carriers, _ = _read_carriers_from_file(path)

    assert set(carriers) == {"{das ist kein JSON}"}


def test_run_backfill_carrier_list_with_empty_line_ids_still_joins_tatoeba_by_text(
    tmp_path: Path,
) -> None:
    """``--carriers-from`` produces carriers with no ``line_id`` at all, so
    the Tatoeba join has to fall back to an exact-text match. Verified here
    rather than assumed, because the per-carrier mode relies on it for the
    free half of its glosses."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {"Ich bin müde.": _carrier("Ich bin müde.", "")}
    pairs = [Pair(german_id="1", german="Ich bin müde.", english="I am tired.")]

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=None,
        translator_mode="none",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=1000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
        carriers_source="carriers.txt",
    )

    assert report.from_tatoeba == 1
    assert report.carriers_source == "carriers.txt"
    assert _load_store(store_path)["Ich bin müde."].english == "I am tired."


def test_main_carriers_from_with_skip_tatoeba_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The combination is meaningless: ``--carriers-from`` already means
    neither corpus is read, so a ``--skip`` flag beside it can only mean the
    caller thinks corpora are still in play. argparse's own error path exits
    2 before anything reads the environment or builds a client."""
    path = tmp_path / "carriers.txt"
    path.write_text("Der Hund läuft.\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["build_translations.py", "--carriers-from", str(path), "--skip-tatoeba"]
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_main_carriers_from_with_skip_leipzig_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "carriers.txt"
    path.write_text("Der Hund läuft.\n", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["build_translations.py", "--carriers-from", str(path), "--skip-leipzig"]
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_main_carriers_from_missing_file_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly given path that does not exist is a mistake worth
    stopping for, unlike a missing staged corpus (which only warns): a silent
    zero-carrier run would look like a completed build."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_translations.py", "--carriers-from", str(tmp_path / "nope.txt")],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_main_carriers_from_jsonl_with_no_german_key_anywhere_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The realistic way to misuse this flag, and the one that would otherwise
    pass silently. This project's own item dump
    (``data/corpus_pilot_review.jsonl``) is keyed on ``prompt`` and
    ``accepted_answers``, not ``german``, so pointing ``--carriers-from`` at it
    skips every line. Without this check the run would write an empty report
    and exit 0, which reads as "the build has all its glosses" when in fact it
    translated nothing at all."""
    path = tmp_path / "items.jsonl"
    path.write_text(
        '{"id": "a1", "prompt": "Der Hund ___ schnell.", "accepted_answers": ["läuft"]}\n'
        '{"id": "a2", "prompt": "Die Katze ___.", "accepted_answers": ["schläft"]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["build_translations.py", "--carriers-from", str(path)])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_read_carriers_from_file_reports_how_many_json_objects_it_skipped(
    tmp_path: Path,
) -> None:
    """The count is what makes a PARTIAL mismatch visible. A file that is
    half plain sentences and half foreign JSON still produces carriers, so it
    never trips the usage error above; the caller prints this number instead,
    and a nonzero one says the file is not the shape its author thought."""
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "Der Hund läuft.\n"
        '{"prompt": "no german key"}\n'
        '{"german": "Die Katze schläft."}\n'
        '{"item_id": "x"}\n',
        encoding="utf-8",
    )

    carriers, skipped = _read_carriers_from_file(path)

    assert set(carriers) == {"Der Hund läuft.", "Die Katze schläft."}
    assert skipped == 2


def test_load_store_malformed_line_skipped_not_fatal(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    store_path.write_text(
        '{"german": "a", "english": "b", "source": "tatoeba", '
        '"written_at": "2026-01-01T00:00:00+00:00"}\n'
        "not even json\n"
        '{"german": "c", "english": "d", "source": "azure", '
        '"written_at": "2026-01-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    store = _load_store(store_path)
    assert set(store) == {"a", "c"}


# ==============================================================================
# The cross-corpus id collision
#
# ``shortest_by_id`` is keyed on TATOEBA sentence ids. ``CorpusLine.line_id``
# is whatever id the line's own corpus gave it. Leipzig line ids and Tatoeba
# sentence ids are both bare integers in the same numeric range, so an
# unfenced id join reads one as the other. Measured against the owner's real
# store: 7,365 of the 7,499 Leipzig carriers in it (98.2%) were labelled
# source="tatoeba" and therefore wrong, and two of them reached the last
# pilot's 430 accepted items.
#
# The numbers below are the real ones from that bug, not invented.
# ==============================================================================

_GRAZ = (
    "Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung schlimmer ist als gedacht."
)
_CROSSED = "She crossed the street."


def test_run_backfill_leipzig_carrier_colliding_with_a_tatoeba_id_gets_no_tatoeba_gloss(
    tmp_path: Path,
) -> None:
    """The exact reported defect. Leipzig line 541845 and Tatoeba sentence
    541845 are unrelated sentences that share a number; the Leipzig carrier
    must not receive the Tatoeba English, and must instead fall through to
    the machine-translation queue like any other unglossed carrier."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {_GRAZ: _carrier(_GRAZ, "541845", SOURCE_LEIPZIG)}
    pairs = [Pair(german_id="541845", german="Sie überquerte die Straße.", english=_CROSSED)]
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.from_tatoeba == 0
    # Not merely "no wrong gloss": it went to the translator, which is where
    # a carrier with no free English is supposed to go.
    assert report.machine_translated == 1
    assert translator.calls == [[_GRAZ]]

    store = _load_store(store_path)
    assert store[_GRAZ].english != _CROSSED
    assert store[_GRAZ].source == "gemini"


def test_run_backfill_leipzig_carrier_colliding_with_a_tatoeba_id_and_no_translator_stays_absent(
    tmp_path: Path,
) -> None:
    """With no translator configured the collision must leave the carrier
    with NO record at all. An absent gloss is a state the pipeline handles;
    a wrong one is read by the verifier as if it were true."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {_GRAZ: _carrier(_GRAZ, "541845", SOURCE_LEIPZIG)}
    pairs = [Pair(german_id="541845", german="Sie überquerte die Straße.", english=_CROSSED)]

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=None,
        translator_mode="none",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.from_tatoeba == 0
    assert report.skipped_for_budget == 1
    assert _load_store(store_path) == {}


def test_run_backfill_tatoeba_carrier_still_gets_its_gloss_by_id(tmp_path: Path) -> None:
    """The fix must not cost the coverage that justified the feature. The id
    join is most of Tatoeba's own measured 61.7% coverage, so a Tatoeba
    carrier whose TEXT differs from the pairs export (a re-encoded quote
    character, here) must still match on its id and cost nothing."""
    store_path = tmp_path / "de_en.jsonl"
    corpus_text = "Er sagte «Hallo» und ging dann wieder weg."
    export_text = 'Er sagte "Hallo" und ging dann wieder weg.'
    carriers = {corpus_text: _carrier(corpus_text, "541845", SOURCE_TATOEBA)}
    pairs = [Pair(german_id="541845", german=export_text, english="He said hello and left.")]
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.from_tatoeba == 1
    assert report.machine_translated == 0
    assert translator.calls == []
    assert _load_store(store_path)[corpus_text].english == "He said hello and left."


def test_run_backfill_leipzig_carrier_matching_tatoeba_by_text_still_gets_that_gloss(
    tmp_path: Path,
) -> None:
    """Text matching stays open to every carrier regardless of source. The
    German string is its own proof: a sentence that genuinely exists in
    Tatoeba genuinely has that English, whichever corpus this particular copy
    was read from. Only the ID join is namespaced."""
    store_path = tmp_path / "de_en.jsonl"
    shared = "Der Hund läuft schnell durch den grünen Park."
    carriers = {shared: _carrier(shared, "541845", SOURCE_LEIPZIG)}
    pairs = [
        Pair(german_id="9999", german=shared, english="The dog runs fast through the green park."),
        Pair(german_id="541845", german="Sie überquerte die Straße.", english=_CROSSED),
    ]
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="gemini_only",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.from_tatoeba == 1
    assert translator.calls == []
    record = _load_store(store_path)[shared]
    assert record.english == "The dog runs fast through the green park."
    assert record.source == "tatoeba"


def test_reject_cross_corpus_gloss_leipzig_carrier_without_a_text_match_raises() -> None:
    """The tripwire itself. ``_fill_from_tatoeba``'s source fence means this
    state cannot arise through the fixed code path, which is exactly why the
    check is tested directly rather than through a branch the fix makes
    unreachable: it exists to fire if the fence is ever removed again, and a
    guard nobody has ever seen raise is a guard nobody knows works."""
    with pytest.raises(CrossCorpusGlossError) as exc_info:
        reject_cross_corpus_gloss(_carrier(_GRAZ, "541845", SOURCE_LEIPZIG), _CROSSED, {})

    message = str(exc_info.value)
    assert "541845" in message
    assert SOURCE_LEIPZIG in message
    assert _CROSSED in message


def test_reject_cross_corpus_gloss_leipzig_carrier_with_a_text_match_is_allowed() -> None:
    """A text match is self-verifying, so it is legitimate from any corpus."""
    shared = "Der Hund läuft schnell durch den grünen Park."
    reject_cross_corpus_gloss(
        _carrier(shared, "541845", SOURCE_LEIPZIG), "The dog runs.", {shared: "The dog runs."}
    )


def test_reject_cross_corpus_gloss_tatoeba_carrier_is_allowed() -> None:
    """An id match on a Tatoeba carrier is the join working as intended."""
    reject_cross_corpus_gloss(
        _carrier("Sie überquerte die Straße.", "541845", SOURCE_TATOEBA), _CROSSED, {}
    )


def test_reject_cross_corpus_gloss_unknown_source_is_allowed() -> None:
    """``--carriers-from`` produces carriers with no source at all. Those can
    only ever have matched by text, because the id join refuses a carrier
    whose corpus is unconfirmed, so they are not the contradiction this
    guard is looking for."""
    reject_cross_corpus_gloss(_carrier("Ich bin müde.", ""), "I am tired.", {})


def test_fill_from_tatoeba_runs_the_guard_on_every_stored_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is only worth anything if it actually sits on the write
    path. Pinned here so a future refactor cannot quietly drop the call and
    still pass every other test in this file."""
    seen: list[CorpusLine] = []

    def _spy(line: CorpusLine, english: str, shortest_by_text: dict[str, str]) -> None:
        seen.append(line)

    monkeypatch.setattr(build_translations, "reject_cross_corpus_gloss", _spy)
    report = TranslationBackfillReport(
        seed=0, limit_per_source=0, max_characters=0, batch_size=1, store_path=str(tmp_path)
    )
    line = _carrier("Ich bin müde.", "1", SOURCE_TATOEBA)

    _fill_from_tatoeba(
        [line],
        {"1": "I am tired."},
        {},
        {},
        report,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert seen == [line]


# ==============================================================================
# --trust-tatoeba: the join is opt-in, not default (owner's decision,
# 2026-08-27)
#
# A hand audit of all 430 accepted exercises found ten defects, four of them
# items whose German is correct and whose English gloss is wrong. THREE OF
# THE FOUR came from Tatoeba's own human translations, not from machine
# translation:
#
#   "Wenn ich im Lotto gewaenne, wuerde ich mir ein neues Auto kaufen."
#     -> "If I won the lottery, I'd buy you a new car."   (mir is himself)
#   "Ich habe eine Freundin, die sich selbst die Haare schneidet."
#     -> "I have a friend who cuts his own hair."         (Freundin is female)
#   "Das Haus, in dem man lacht, wird vom Glueck bedacht."
#     -> "The house in which one laughs is considered by luck."  (meaningless)
#
# A separate hand check of 120 Tatoeba pairs found 2 outright wrong and 6
# loose. So the join is off unless asked for -- and it is KEPT, because
# feature 5.3 is fed entirely by those records and needs breadth over
# precision.
# ==============================================================================


_TIRED = "Ich bin müde."


def test_run_backfill_without_trust_tatoeba_sends_a_pair_matched_carrier_to_the_translator(
    tmp_path: Path,
) -> None:
    """THE test for this change. The carrier has a perfectly good Tatoeba
    pair, matching by BOTH id and exact text, so every route into the join is
    open -- and none of them may be taken. The carrier must reach the machine
    translator instead, and the stored record must say so."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {_TIRED: _carrier(_TIRED, "1", SOURCE_TATOEBA)}
    pairs = [Pair(german_id="1", german=_TIRED, english="I am tired.")]
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="azure_only",
        tatoeba_pairs=pairs,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.trust_tatoeba is False
    assert report.from_tatoeba == 0
    assert report.machine_translated == 1
    assert translator.calls == [[_TIRED]]

    stored = _load_store(store_path)[_TIRED]
    assert stored.english == f"EN: {_TIRED}"
    assert stored.source == "azure"


def test_run_backfill_with_trust_tatoeba_still_fills_from_the_pairs(tmp_path: Path) -> None:
    """Feature 5.3's own path, intact. Rebuilding the corpus store from
    Tatoeba's 200,555 free glosses is the whole reason the join is kept
    rather than deleted; machine translating them instead would be about
    13,000,000 characters, roughly half a year of Azure F0."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {_TIRED: _carrier(_TIRED, "1", SOURCE_TATOEBA)}
    pairs = [Pair(german_id="1", german=_TIRED, english="I am tired.")]
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="azure_only",
        tatoeba_pairs=pairs,
        trust_tatoeba=True,
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    assert report.trust_tatoeba is True
    assert report.from_tatoeba == 1
    assert report.machine_translated == 0
    assert translator.calls == [], "a Tatoeba gloss costs nothing; nothing may be translated"
    assert _load_store(store_path)[_TIRED].source == "tatoeba"


def test_run_backfill_without_trust_tatoeba_leaves_existing_tatoeba_records_untouched(
    tmp_path: Path,
) -> None:
    """Distrusted, not deleted. The Tatoeba records already in the store are
    the only thing feeding feature 5.3, so a run under the new default must
    not remove or rewrite them; it simply stops ADDING more."""
    store_path = tmp_path / "de_en.jsonl"
    store_path.write_text(
        TranslationRecord(
            german=_TIRED,
            english="I am tired.",
            source="tatoeba",
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    new_carrier = "Die Katze schläft auf dem Sofa."
    carriers = {new_carrier: _carrier(new_carrier, "2", SOURCE_TATOEBA)}

    run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[Pair(german_id="2", german=new_carrier, english="The cat sleeps.")],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )

    store = _load_store(store_path)
    assert store[_TIRED].english == "I am tired."
    assert store[_TIRED].source == "tatoeba"


@pytest.mark.parametrize("trust_tatoeba", [False, True])
def test_run_backfill_counts_add_up_to_carriers_seen_in_both_tatoeba_modes(
    tmp_path: Path, trust_tatoeba: bool
) -> None:
    """The report's disjoint-counts discipline, held across the new flag.
    Every carrier lands in exactly one of five buckets, whichever way the
    join is set, so a mode that silently dropped carriers on the floor would
    fail here rather than in a hand count months later."""
    store_path = tmp_path / "de_en.jsonl"
    already = "Der Hund läuft schnell durch den Park im Sommer."
    store_path.write_text(
        TranslationRecord(
            german=already,
            english="The dog runs.",
            source="azure",
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    paired = "Die Katze schläft auf dem warmen Sofa im Wohnzimmer."
    fresh = "Ein Mann steht vor der Tür und wartet auf den Bus."
    beyond_budget = "Noch ein Satz, der diesmal nicht mehr in das Zeichenbudget passt."
    carriers = {
        already: _carrier(already, "1", SOURCE_TATOEBA),
        paired: _carrier(paired, "2", SOURCE_TATOEBA),
        fresh: _carrier(fresh, "3", SOURCE_TATOEBA),
        beyond_budget: _carrier(beyond_budget, "4", SOURCE_TATOEBA),
    }
    pairs = [Pair(german_id="2", german=paired, english="The cat sleeps.")]

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=pairs,
        trust_tatoeba=trust_tatoeba,
        # Room for one or two carriers, never all of them, so
        # skipped_for_budget is genuinely exercised in both modes.
        max_characters=len(fresh) + 5,
        batch_size=1,
        seed=7,
        limit_per_source=1000,
    )

    assert report.carriers_seen == 4
    assert report.already_in_store == 1
    assert (
        report.already_in_store
        + report.from_tatoeba
        + report.machine_translated
        + report.failed
        + report.skipped_for_budget
        == report.carriers_seen
    )
    assert report.from_tatoeba == (1 if trust_tatoeba else 0)


def test_report_json_carries_trust_tatoeba_and_says_what_it_means(tmp_path: Path) -> None:
    """A store holds records from many runs, so the report has to say which
    policy produced this one -- and say it in words, not only as a boolean
    nobody can interpret two months later."""
    store_path = tmp_path / "de_en.jsonl"
    report = run_backfill(
        carriers={_TIRED: _carrier(_TIRED, "1", SOURCE_TATOEBA)},
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=100,
        seed=7,
        limit_per_source=1000,
    )
    payload = report.to_dict()
    run_block = payload["run"]
    assert isinstance(run_block, dict)
    assert run_block["trust_tatoeba"] is False
    policy = payload["tatoeba_policy"]
    assert isinstance(policy, str)
    assert "not used at all" in policy
    assert "5.3" in policy

    assert "used as" in tatoeba_policy_sentence(True)


def test_main_pairs_without_trust_tatoeba_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contradictory, not merely redundant: the pair files exist only to feed
    a join that is now off by default, so naming them without the flag means
    the caller believes Tatoeba glosses are about to be used. Reading them and
    quietly ignoring them would leave a caller certain a gloss came from
    Tatoeba when it came from Azure."""
    carriers_path = tmp_path / "carriers.txt"
    carriers_path.write_text("Der Hund läuft.\n", encoding="utf-8")
    pairs_path = tmp_path / "pairs.tsv"
    pairs_path.write_text("1\tDer Hund läuft.\tThe dog runs.\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_translations.py",
            "--carriers-from",
            str(carriers_path),
            "--pairs",
            str(pairs_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


# ==============================================================================
# The additive hooks the standing monthly job needs
#
# ``scripts/monthly_translation_topup.py`` is the caller. These three parameters
# exist so that job can prioritise, account per batch and checkpoint without a
# second copy of this file's batch loop, which is the thing that would actually
# drift. All three default to today's behaviour, so the nightly path is
# unchanged.
# ==============================================================================


def test_run_backfill_distrusted_store_source_is_retranslated_and_overwritten(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    _write_store_atomic(
        store_path,
        {
            "Der Hund läuft schnell.": TranslationRecord(
                german="Der Hund läuft schnell.",
                english="The dog is quick.",
                source="tatoeba",
                written_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "Die Katze schläft.": TranslationRecord(
                german="Die Katze schläft.",
                english="The cat sleeps.",
                source="azure",
                written_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        },
    )
    carriers = {
        "Der Hund läuft schnell.": _carrier("Der Hund läuft schnell."),
        "Die Katze schläft.": _carrier("Die Katze schläft."),
    }
    translator = FakeTranslator()

    report = run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=translator,
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=AZURE_MAX_BATCH,
        seed=7,
        limit_per_source=100,
        distrust_stored_sources=frozenset({"tatoeba"}),
    )

    assert report.replacing_distrusted == 1
    assert report.already_in_store == 1
    assert translator.calls == [["Der Hund läuft schnell."]]
    stored = _load_store(store_path)
    assert stored["Der Hund läuft schnell."].source == "azure"
    assert stored["Der Hund läuft schnell."].english == "EN: Der Hund läuft schnell."
    # The trusted record was never touched.
    assert stored["Die Katze schläft."].english == "The cat sleeps."


def test_run_backfill_distrust_defaults_to_empty_so_the_nightly_path_is_unchanged(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "de_en.jsonl"
    _write_store_atomic(
        store_path,
        {
            "Der Hund läuft schnell.": TranslationRecord(
                german="Der Hund läuft schnell.",
                english="The dog is quick.",
                source="tatoeba",
                written_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        },
    )
    translator = FakeTranslator()
    report = run_backfill(
        carriers={"Der Hund läuft schnell.": _carrier("Der Hund läuft schnell.")},
        store_path=store_path,
        translator=translator,
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=AZURE_MAX_BATCH,
        seed=7,
        limit_per_source=100,
    )
    assert translator.calls == []
    assert report.already_in_store == 1
    assert report.replacing_distrusted == 0


def test_run_backfill_trusting_and_distrusting_tatoeba_at_once_is_refused(
    tmp_path: Path,
) -> None:
    """The join would refill the very records the distrust just queued for
    machine translation. Contradictory, so it raises rather than silently
    picking one of the two."""
    with pytest.raises(ValueError, match="contradictory"):
        run_backfill(
            carriers={},
            store_path=tmp_path / "de_en.jsonl",
            translator=None,
            translator_mode="none",
            tatoeba_pairs=[],
            max_characters=0,
            batch_size=1,
            seed=7,
            limit_per_source=100,
            trust_tatoeba=True,
            distrust_stored_sources=frozenset({"tatoeba"}),
        )


def test_run_backfill_on_batch_fires_once_per_successful_batch_with_its_texts(
    tmp_path: Path,
) -> None:
    carriers = {
        f"Der Satz Nummer {i:03d} hier.": _carrier(f"Der Satz Nummer {i:03d} hier.")
        for i in range(4)
    }
    seen: list[BatchOutcome] = []
    run_backfill(
        carriers=carriers,
        store_path=tmp_path / "de_en.jsonl",
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=2,
        seed=7,
        limit_per_source=100,
        on_batch=seen.append,
    )

    assert len(seen) == 2
    assert [outcome.carriers for outcome in seen] == [2, 2]
    assert all(outcome.source == "azure" for outcome in seen)
    assert all(outcome.gemini_characters == 0 for outcome in seen)
    assert sum(outcome.azure_characters for outcome in seen) == sum(len(t) for t in carriers)
    assert sum(outcome.characters for outcome in seen) == sum(len(t) for t in carriers)
    # The texts are carried because counts alone cannot say WHICH carriers a
    # batch covered once a batch in front of it has failed.
    assert {text for outcome in seen for text in outcome.texts} == set(carriers)


def test_run_backfill_on_batch_does_not_fire_for_a_failed_batch(tmp_path: Path) -> None:
    seen: list[BatchOutcome] = []
    report = run_backfill(
        carriers={"Der Hund läuft schnell.": _carrier("Der Hund läuft schnell.")},
        store_path=tmp_path / "de_en.jsonl",
        translator=FailingTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=1,
        seed=7,
        limit_per_source=100,
        on_batch=seen.append,
    )
    assert report.failed == 1
    assert seen == []


def test_run_backfill_checkpoint_every_flushes_the_store_mid_run(tmp_path: Path) -> None:
    """A killed run must not lose glosses the ledger has already charged the
    month for. The checkpoint runs before ``on_batch``, so anything accounted
    for is already durable."""
    store_path = tmp_path / "de_en.jsonl"
    carriers = {
        f"Der Satz Nummer {i:03d} hier.": _carrier(f"Der Satz Nummer {i:03d} hier.")
        for i in range(4)
    }
    sizes: list[int] = []

    def record_size(_: BatchOutcome) -> None:
        sizes.append(len(_load_store(store_path)))

    run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=1,
        seed=7,
        limit_per_source=100,
        on_batch=record_size,
        checkpoint_every=1,
    )
    assert sizes == [1, 2, 3, 4]


def test_run_backfill_checkpoint_every_zero_writes_only_at_the_end(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {
        f"Der Satz Nummer {i:03d} hier.": _carrier(f"Der Satz Nummer {i:03d} hier.")
        for i in range(3)
    }
    sizes: list[int] = []

    run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=1,
        seed=7,
        limit_per_source=100,
        on_batch=lambda _: sizes.append(len(_load_store(store_path))),
        checkpoint_every=0,
    )
    assert sizes == [0, 0, 0]
    assert len(_load_store(store_path)) == 3


def test_run_backfill_checkpoint_every_n_flushes_every_nth_batch(tmp_path: Path) -> None:
    store_path = tmp_path / "de_en.jsonl"
    carriers = {
        f"Der Satz Nummer {i:03d} hier.": _carrier(f"Der Satz Nummer {i:03d} hier.")
        for i in range(6)
    }
    sizes: list[int] = []

    run_backfill(
        carriers=carriers,
        store_path=store_path,
        translator=FakeTranslator(),
        translator_mode="azure_only",
        tatoeba_pairs=[],
        max_characters=10_000,
        batch_size=1,
        seed=7,
        limit_per_source=100,
        on_batch=lambda _: sizes.append(len(_load_store(store_path))),
        checkpoint_every=2,
    )
    assert sizes == [0, 2, 2, 4, 4, 6]
