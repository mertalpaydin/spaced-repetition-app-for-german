"""Tests for scripts/corpus_reading.py: the corpus-line reader shared by
scripts.eval_corpus_coverage and scripts.step7_corpus_pilot (see that
module's own docstring for why this is one reader, not two)."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import corpus_reading
from scripts.corpus_reading import (
    MAX_CHARS,
    MAX_WORDS,
    MIN_CHARS,
    MIN_WORDS,
    CorpusLine,
    default_corpus_path,
    is_plausible_carrier,
    read_corpus_lines,
    read_sentence_texts,
)

_PLAUSIBLE = "Der Hund läuft schnell durch den grünen Park heute."
assert MIN_CHARS <= len(_PLAUSIBLE) <= MAX_CHARS
assert MIN_WORDS <= len(_PLAUSIBLE.split()) <= MAX_WORDS


def test_is_plausible_carrier_accepts_a_normal_sentence() -> None:
    assert is_plausible_carrier(_PLAUSIBLE)


def test_is_plausible_carrier_rejects_too_short() -> None:
    assert not is_plausible_carrier("Kurz.")


def test_is_plausible_carrier_rejects_too_long() -> None:
    long_sentence = ("Das ist ein sehr langer Satz mit vielen Wörtern " * 5).strip() + "."
    assert not is_plausible_carrier(long_sentence)


def test_is_plausible_carrier_rejects_missing_terminal_punctuation() -> None:
    assert not is_plausible_carrier(_PLAUSIBLE.rstrip("."))


def test_is_plausible_carrier_rejects_markup_and_tabs() -> None:
    assert not is_plausible_carrier(_PLAUSIBLE.replace("Park", "[Park]"))
    assert not is_plausible_carrier(_PLAUSIBLE.replace(" ", "\t", 1))


def test_read_corpus_lines_tatoeba_format_keeps_id_and_text(tmp_path: Path) -> None:
    path = tmp_path / "tatoeba.tsv"
    path.write_text(
        f"77\tdeu\t{_PLAUSIBLE}\n"
        "78\tdeu\tKurz.\n"  # too short, dropped
        f"79\tdeu\t{_PLAUSIBLE}\n",
        encoding="utf-8",
    )
    lines = read_corpus_lines(path, "tatoeba", limit=10, seed=1)

    assert len(lines) == 2
    ids = {line.line_id for line in lines}
    assert ids == {"77", "79"}
    for line in lines:
        assert line.text == _PLAUSIBLE


def test_read_corpus_lines_tatoeba_format_drops_lines_with_too_few_fields(tmp_path: Path) -> None:
    path = tmp_path / "tatoeba.tsv"
    path.write_text(f"1\tdeu\n{_PLAUSIBLE}\n", encoding="utf-8")
    lines = read_corpus_lines(path, "tatoeba", limit=10, seed=1)
    assert lines == []


def test_read_corpus_lines_lines_format_splits_on_first_tab(tmp_path: Path) -> None:
    path = tmp_path / "leipzig.txt"
    path.write_text(f"101\t{_PLAUSIBLE}\n", encoding="utf-8")
    lines = read_corpus_lines(path, "lines", limit=10, seed=1)

    assert len(lines) == 1
    assert lines[0] == CorpusLine("101", _PLAUSIBLE)


def test_read_corpus_lines_lines_format_with_no_tab_has_empty_id(tmp_path: Path) -> None:
    path = tmp_path / "plain.txt"
    path.write_text(f"{_PLAUSIBLE}\n", encoding="utf-8")
    lines = read_corpus_lines(path, "lines", limit=10, seed=1)

    assert len(lines) == 1
    assert lines[0].line_id == ""
    assert lines[0].text == _PLAUSIBLE


def test_read_corpus_lines_respects_limit(tmp_path: Path) -> None:
    path = tmp_path / "many.txt"
    body = "\n".join(f"{i}\t{_PLAUSIBLE}" for i in range(50))
    path.write_text(body, encoding="utf-8")

    lines = read_corpus_lines(path, "lines", limit=5, seed=1)
    assert len(lines) == 5


def test_read_corpus_lines_deterministic_with_same_seed(tmp_path: Path) -> None:
    path = tmp_path / "many.txt"
    # Distinct text per line (append a distinguishing word) so shuffled order
    # is actually observable, not just five identical strings.
    body = "\n".join(f"{i}\tDer Hund läuft heute schnell durch Park Nummer {i}." for i in range(30))
    path.write_text(body, encoding="utf-8")

    first = read_corpus_lines(path, "lines", limit=30, seed=42)
    second = read_corpus_lines(path, "lines", limit=30, seed=42)
    third = read_corpus_lines(path, "lines", limit=30, seed=99)

    assert first == second
    assert first != third, "a different seed should (almost always) shuffle differently"


def test_read_sentence_texts_discards_the_id(tmp_path: Path) -> None:
    path = tmp_path / "tatoeba.tsv"
    path.write_text(f"77\tdeu\t{_PLAUSIBLE}\n", encoding="utf-8")

    texts = read_sentence_texts(path, "tatoeba", limit=10, seed=1)
    assert texts == [_PLAUSIBLE]


# ==============================================================================
# default_corpus_path
#
# Measured need: three scripts hardcoded the sandbox mount these files were
# staged at while they were being built. On the owner's own machine every one
# of them printed "corpus not found" twice, then "nothing to do", then exited
# 0. A default that only works where the code was written is a trap, not a
# default.
# ==============================================================================


def test_default_corpus_path_prefers_the_repository_copy_over_the_sandbox_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repository's own data/raw/_extract is where a checkout actually
    keeps these files, and it is the only root that exists on the owner's
    machine, so it must win whenever it holds the file."""
    repo_root = tmp_path / "repo"
    mount_root = tmp_path / "mount"
    for root in (repo_root, mount_root):
        root.mkdir(parents=True)
        (root / "tatoeba_deu.tsv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(corpus_reading, "_CORPUS_SEARCH_ROOTS", (repo_root, mount_root))

    assert default_corpus_path("tatoeba_deu.tsv") == repo_root / "tatoeba_deu.tsv"


def test_default_corpus_path_falls_through_to_a_later_root_when_the_first_lacks_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    mount_root = tmp_path / "mount"
    repo_root.mkdir(parents=True)
    mount_root.mkdir(parents=True)
    (mount_root / "leipzig_sample.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(corpus_reading, "_CORPUS_SEARCH_ROOTS", (repo_root, mount_root))

    assert default_corpus_path("leipzig_sample.txt") == mount_root / "leipzig_sample.txt"


def test_default_corpus_path_missing_everywhere_names_the_first_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback is the FIRST root, not the last, so the "not found"
    message a caller prints names a path the user can act on (their own
    repository) rather than a sandbox mount that means nothing to them. This
    is the exact failure the owner hit, and the message is the whole fix for
    it."""
    repo_root = tmp_path / "repo"
    mount_root = tmp_path / "mount"
    monkeypatch.setattr(corpus_reading, "_CORPUS_SEARCH_ROOTS", (repo_root, mount_root))

    assert default_corpus_path("tatoeba_deu.tsv") == repo_root / "tatoeba_deu.tsv"
