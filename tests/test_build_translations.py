"""Tests for scripts/build_translations.py (TODO.md 5.1 step 2): the
resumable English-gloss backfill.

CLAUDE.md section 7: unit tests never touch the network. Every test here
drives ``run_backfill`` directly with an in-memory carrier set and a fake
``Translator`` (the protocol in ``src/llm/translation.py`` makes this
substitution trivial) -- no real Azure or Gemini call is ever made, and no
corpus or Tatoeba pairs file is read from disk. Only the store itself is
real filesystem I/O, via ``tmp_path``, because the store IS the
resumability mechanism under test.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.build_translations import (
    TranslationRecord,
    _default_batch_size,
    _load_store,
    _shortest_translations,
    _write_store_atomic,
    run_backfill,
)
from scripts.corpus_reading import CorpusLine
from scripts.eval_tatoeba_translation_quality import Pair
from src.llm.translation import AZURE_MAX_BATCH, TranslationError, Translator


def _carrier(text: str, line_id: str = "") -> CorpusLine:
    return CorpusLine(line_id=line_id, text=text)


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
    assert report.characters_spent == one_length * 2

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
