"""Reading "Problem melden" reports out of an exported review log."""

import json
from pathlib import Path

from scripts.read_issues import read_issues


def _issue(seq: int, note: str) -> dict[str, object]:
    return {
        "type": "issue",
        "seq": seq,
        "ts": f"2026-10-21T20:{seq:02d}:00Z",
        "unit_id": "vb:geschehen",
        "card_id": "abcdef012345",
        "note": note,
        "deck_version": "t",
    }


def _review(seq: int) -> dict[str, object]:
    return {
        "type": "review",
        "seq": seq,
        "ts": f"2026-10-21T20:{seq:02d}:30Z",
        "unit_id": "vb:geschehen",
        "card_id": "abcdef012345",
        "rating": "good",
        "outcome": "exact",
        "answers": ["x"],
        "expected": ["x"],
        "elapsed_ms": 1,
        "deck_version": "t",
    }


def test_read_issues_from_a_normal_log_finds_only_the_issues(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    rows = [_review(1), _issue(2, "gap is wrong"), _review(3), _issue(4, "")]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    found = read_issues(log)
    assert [i.seq for i in found] == [2, 4]
    assert found[0].note == "gap is wrong"


def test_read_issues_survives_a_log_pasted_without_newlines_and_cut_short(
    tmp_path: Path,
) -> None:
    """The owner's export of 2026-10-21 came through a phone clipboard: every
    newline gone, and the text cut off in the middle of the last entry. Read
    line by line that is one unparseable line, and the script reported "no
    reports" without a word. Every complete object must still be read."""
    log = tmp_path / "log.txt"
    rows = [_review(1), _issue(2, "first"), _review(3), _issue(4, "second")]
    flattened = " ".join(json.dumps(r) for r in rows) + ' {"type":"review","seq":5'
    log.write_text(flattened, encoding="utf-8")
    assert [i.note for i in read_issues(log)] == ["first", "second"]
