"""Words added from the page are imported into requested.yaml for review."""

import json
from pathlib import Path

from scripts.import_requests import known_units, main, requested_words


def _deck(tmp_path: Path) -> Path:
    deck = tmp_path / "deck"
    (deck / "reserve").mkdir(parents=True)
    (deck / "units.json").write_text(
        json.dumps({"units": [{"unit_id": "nn:hand", "lemma_key": "hand", "kind": "noun"}]}),
        encoding="utf-8",
    )
    (deck / "reserve" / "l.json").write_text(
        json.dumps({"units": [{"unit_id": "aj:ledig", "lemma_key": "ledig", "kind": "adjective"}]}),
        encoding="utf-8",
    )
    return deck


def _request(seq: int, unit_id: str, unit: dict[str, str] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "type": "request",
        "seq": seq,
        "ts": f"2026-10-2{seq}T08:00:00Z",
        "unit_id": unit_id,
        "origin": "reserve",
    }
    if unit is not None:
        row["unit"] = unit
    return row


def test_requested_words_resolves_reserve_words_and_written_ones(tmp_path: Path) -> None:
    deck = _deck(tmp_path)
    log = "\n".join(
        json.dumps(r)
        for r in [
            _request(1, "aj:ledig"),
            _request(2, "vb:brummen", {"lemma_key": "brummen", "kind": "verb"}),
            _request(3, "aj:ledig"),  # pressed twice, imported once
            _request(4, "nn:unbekannt"),  # in neither: reported, skipped
        ]
    )
    found = requested_words(log, known_units(deck))
    assert found == [("ledig", "adjective", "aj:ledig"), ("brummen", "verb", "vb:brummen")]


def test_import_appends_only_new_words_and_only_with_write(tmp_path: Path) -> None:
    """The owner: reserve words "should be reviewed if only added"
    (2026-10-22). requested.yaml is what makes a word part of every review
    band, so getting it there is the whole job."""
    deck = _deck(tmp_path)
    phrases = tmp_path / "phrases"
    phrases.mkdir()
    for name in (
        "connectors.yaml",
        "idioms.yaml",
        "verb_prep_seed.yaml",
        "collocation_seed.yaml",
        "trivial_stoplist.yaml",
        "exclude.yaml",
        "excluded_cards.yaml",
        "unit_overrides.yaml",
    ):
        (phrases / name).write_text("", encoding="utf-8")
    (phrases / "requested.yaml").write_text(
        '- {key: "hand", kind: noun, note: "already asked for"}\n', encoding="utf-8"
    )
    log = tmp_path / "log.jsonl"
    log.write_text(
        "\n".join(json.dumps(r) for r in [_request(1, "aj:ledig"), _request(2, "nn:hand")]),
        encoding="utf-8",
    )
    args = [str(log), "--deck", str(deck), "--phrases-dir", str(phrases)]

    assert main(args) == 0
    assert "ledig" not in (phrases / "requested.yaml").read_text(encoding="utf-8"), "dry run"

    assert main([*args, "--write"]) == 0
    text = (phrases / "requested.yaml").read_text(encoding="utf-8")
    assert '- {key: "ledig", kind: adjective' in text
    assert text.count('key: "hand"') == 1, "a word already requested is not added again"
