"""The hand-written request list: what the build refuses to guess."""

from pathlib import Path

import pytest
import yaml
from src.phrases.curated import RequestedUnit, load_curated


def _write(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    (tmp_path / "requested.yaml").write_text(
        yaml.safe_dump(rows, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


def test_a_request_needs_its_kind_because_the_unit_id_depends_on_it() -> None:
    """unit_id_for(kind, lemma) derives the id, so a guessed kind would give
    one word two ids and fork the review log (owner, 2026-09-22)."""
    with pytest.raises(ValueError):
        RequestedUnit.model_validate({"key": "braten"})
    assert RequestedUnit.model_validate({"key": "braten", "kind": "verb"}).kind == "verb"


def test_a_request_list_loads_in_the_order_it_is_written(tmp_path: Path) -> None:
    load_from = _write(
        tmp_path,
        [
            {"key": "bewölkt", "kind": "adjective", "note": "Lingvist"},
            {"key": "ausdauer", "kind": "noun"},
        ],
    )
    requested = load_curated(load_from).requested
    assert [r.key for r in requested] == ["bewölkt", "ausdauer"]
    assert requested[0].note == "Lingvist"


def test_a_repeated_request_is_refused_however_it_is_spelt(tmp_path: Path) -> None:
    """ "bewoelkt" and "bewölkt" are the same request."""
    with pytest.raises(ValueError, match="repeats"):
        load_curated(
            _write(
                tmp_path,
                [
                    {"key": "bewölkt", "kind": "adjective"},
                    {"key": "bewoelkt", "kind": "adjective"},
                ],
            )
        )
    # the same word under two kinds is a different request, and allowed
    both = load_curated(
        _write(
            tmp_path,
            [{"key": "braten", "kind": "noun"}, {"key": "braten", "kind": "verb"}],
        )
    ).requested
    assert [r.kind for r in both] == ["noun", "verb"]


def test_a_request_must_be_written_as_the_miner_writes_a_lemma(tmp_path: Path) -> None:
    """The miner's keys are lowercase, so "Bewölkt" would silently miss."""
    with pytest.raises(ValueError, match="lowercase"):
        load_curated(_write(tmp_path, [{"key": "Bewölkt", "kind": "adjective"}]))
    with pytest.raises(ValueError, match="blank or padded"):
        load_curated(_write(tmp_path, [{"key": " bewölkt ", "kind": "adjective"}]))


def test_no_request_list_is_not_an_error(tmp_path: Path) -> None:
    assert load_curated(tmp_path).requested == []
