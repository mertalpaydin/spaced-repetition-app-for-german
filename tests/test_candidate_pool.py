"""Unit tests for the phase A / phase B boundary. TODO.md item 1.

No corpus, no spaCy, no network: the pool is a file format, and what has to be
true of it is that phase B gets back exactly what phase A put in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.contracts import BankItem
from src.generation.batch_client import RejectedCandidateRecord
from src.generation.candidate_pool import (
    POOL_FORMAT_VERSION,
    CandidatePool,
    PooledProvenance,
    PoolFormatError,
    fingerprint_inputs,
)


def _item(item_id: str = "corpus_0001", topic: str = "nomen_plural") -> BankItem:
    return BankItem(
        id=item_id,
        topic_id=topic,
        tag_id=topic,
        type="cloze_cued",
        difficulty=1,
        cefr="A2",
        prompt="Ich habe schöne ___ gesehen.",
        accepted_answers=["Häuser"],
        cue="Haus",
    )


def _pool(**overrides: object) -> CandidatePool:
    defaults: dict[str, object] = {
        "inputs_fingerprint": "abc123",
        "seed": 7,
        "per_topic_quota": 25,
        "items": [_item()],
        "provenance": {"hash1": PooledProvenance(source="tatoeba", line_id="42", text="Ein Satz.")},
        "report_prefix": {"carrier_valid_total": 1120},
    }
    defaults.update(overrides)
    return CandidatePool(**defaults)  # type: ignore[arg-type]


def test_pool_round_trips_through_disk(tmp_path: Path) -> None:
    """The whole point: what phase A wrote is what phase B reads."""
    original = _pool()
    path = tmp_path / "pool.json"
    original.save(path)

    loaded = CandidatePool.load(path)
    assert [i.id for i in loaded.items] == [i.id for i in original.items]
    assert loaded.items[0].accepted_answers == ["Häuser"]
    assert loaded.provenance["hash1"].line_id == "42"
    assert loaded.seed == 7
    assert loaded.report_prefix == {"carrier_valid_total": 1120}


def test_pool_round_trips_german_text_unmangled(tmp_path: Path) -> None:
    """Umlauts and eszett survive the file. The whole corpus is German."""
    path = tmp_path / "pool.json"
    _pool(
        provenance={
            "h": PooledProvenance(source="leipzig", line_id="9", text="Straßen für Fußgänger.")
        }
    ).save(path)
    assert CandidatePool.load(path).provenance["h"].text == "Straßen für Fußgänger."


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "pool.json"
    _pool().save(path)
    assert path.exists()


def test_save_leaves_no_partial_file_behind(tmp_path: Path) -> None:
    """Written whole and moved into place, so an interrupted write cannot leave
    a half-file a later phase B would read as real."""
    path = tmp_path / "pool.json"
    _pool().save(path)
    assert not list(tmp_path.glob("*.partial"))


def test_load_missing_file_says_to_run_phase_a(tmp_path: Path) -> None:
    with pytest.raises(PoolFormatError, match="phase A"):
        CandidatePool.load(tmp_path / "absent.json")


def test_load_rejects_a_future_format_version(tmp_path: Path) -> None:
    """Guessing at a shape this code does not understand is how a silent
    wrong-data run starts."""
    path = tmp_path / "pool.json"
    path.write_text(json.dumps({"version": POOL_FORMAT_VERSION + 1}), encoding="utf-8")
    with pytest.raises(PoolFormatError, match="version"):
        CandidatePool.load(path)


def test_load_rejects_unparseable_json(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PoolFormatError):
        CandidatePool.load(path)


def test_load_rejects_json_that_is_not_an_object(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(PoolFormatError, match="JSON object"):
        CandidatePool.load(path)


def test_pool_carries_phase_a_rejections(tmp_path: Path) -> None:
    """Phase A's own rejections travel with the pool, so the rejected file
    phase B writes covers the whole run rather than only the model's half."""
    record = RejectedCandidateRecord(
        topic_id="nomen_plural",
        type="cloze_cued",
        difficulty=1,
        prompt="Ich habe schöne ___ gesehen.",
        proposed_answer="Häuser",
        layer_failed=None,
        error_type="cefr_filter",
        reason="vocabulary above the topic's own level",
    )
    path = tmp_path / "pool.json"
    _pool(rejected=[record]).save(path)
    loaded = CandidatePool.load(path)
    assert len(loaded.rejected) == 1
    assert loaded.rejected[0].error_type == "cefr_filter"
    assert loaded.rejected[0].proposed_answer == "Häuser"


def test_describe_names_the_counts_an_operator_needs() -> None:
    text = _pool().describe()
    assert "1 items" in text
    assert "1 carriers" in text


def test_fingerprint_is_stable_for_identical_inputs() -> None:
    kwargs = {
        "tatoeba_path": "a.tsv",
        "leipzig_path": "b.txt",
        "limit_per_source": 1000,
        "per_topic_quota": 25,
        "seed": 7,
        "max_items_per_lemma": 3,
    }
    assert fingerprint_inputs(**kwargs) == fingerprint_inputs(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changed",
    [
        {"seed": 8},
        {"per_topic_quota": 30},
        {"limit_per_source": 2000},
        {"tatoeba_path": "other.tsv"},
        {"max_items_per_lemma": 5},
    ],
)
def test_fingerprint_changes_when_a_phase_a_input_changes(changed: dict[str, object]) -> None:
    """A pool built from one corpus must not be silently verified as if it came
    from another."""
    base: dict[str, object] = {
        "tatoeba_path": "a.tsv",
        "leipzig_path": "b.txt",
        "limit_per_source": 1000,
        "per_topic_quota": 25,
        "seed": 7,
        "max_items_per_lemma": 3,
    }
    altered = {**base, **changed}
    assert fingerprint_inputs(**base) != fingerprint_inputs(**altered)  # type: ignore[arg-type]


def test_an_empty_pool_is_a_valid_pool(tmp_path: Path) -> None:
    """Phase A producing nothing is a result, not a corrupt file."""
    path = tmp_path / "pool.json"
    CandidatePool().save(path)
    assert CandidatePool.load(path).items == []
