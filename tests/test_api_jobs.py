"""The API deck jobs: batching, reply parsing, shape checks, findings scoped
to the batch, approval before any call."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts import api_jobs


class FakeClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[dict[str, Any]] = []

    def generate_many(self, prompts: list[str], **kwargs: Any) -> list[str]:
        self.calls.append({"prompts": prompts, **kwargs})
        return self.replies[: len(prompts)]


def test_gloss_batches_store_good_replies_and_reject_the_rest() -> None:
    client = FakeClient(
        [
            '{"id": "0", "en": "I am waiting for the bus."}\n{"id": "1", "en": "Ich warte."}\n',
            '```json\n[{"id": "0", "en": "She came anyway."}]\n```',
        ]
    )
    added, rejected = api_jobs.gloss_sentences(
        ["Ich warte auf den Bus.", "Ich warte.", "Sie kam trotzdem.", "Noch ein Satz hier."],
        client=client,
        batch_size=2,
        max_calls=5,
        now=datetime(2026, 9, 12, tzinfo=UTC),
    )
    assert {k: v.english for k, v in added.items()} == {
        "Ich warte auf den Bus.": "I am waiting for the bus.",
        "Sie kam trotzdem.": "She came anyway.",
    }
    assert all(v.source == "gemini" for v in added.values())
    assert rejected == {"echo": 1, "missing": 1}
    assert client.calls[0]["purpose"] == "sentence_gloss"
    assert client.calls[0]["cache_namespace"] == "sentence_gloss_v1"
    assert len(client.calls[0]["prompts"]) == 2


def test_max_calls_bounds_the_gloss_run() -> None:
    client = FakeClient(['{"id": "0", "en": "One."}'] * 3)
    added, _ = api_jobs.gloss_sentences(
        ["Eins.", "Zwei.", "Drei."], client=client, batch_size=1, max_calls=1, now=datetime.now(UTC)
    )
    assert list(added) == ["Eins."]


def test_review_writes_findings_scoped_to_the_batch(tmp_path: Path) -> None:
    (tmp_path / "cards_000.txt").write_text(
        "aaaaaaaaaaaa\tverb_prep\twarten auf +Akk\tIch [warte] [auf] den Bus.\tI wait.\n"
        "bbbbbbbbbbbb\tidiom\tnach Hause\tEr geht [nach] [Hause].\tHe goes home.\n",
        encoding="utf-8",
    )
    (tmp_path / "cards_001.txt").write_text("cccccccccccc\tidiom\tx\t[x]\tx\n", encoding="utf-8")
    (tmp_path / "deck_version.txt").write_text("v\n", encoding="utf-8")
    client = FakeClient(
        [
            '{"card_id": "bbbbbbbbbbbb", "unit": "nach Hause", "category": "BAD_GLOSS", '
            '"severity": "low", "note": "n", "action": "note"}\n'
            '{"card_id": "zzzzzzzzzzzz", "unit": "x", "category": "BAD_GLOSS", '
            '"severity": "low", "note": "hallucinated id", "action": "drop_card"}\n'
            '{"card_id": "aaaaaaaaaaaa", "unit": "x", "category": "MADE_UP", '
            '"severity": "low", "note": "n", "action": "note"}\n',
            "NONE",
        ]
    )
    counts = api_jobs.review_batches(tmp_path, client=client, max_calls=10)
    assert counts == {"cards_000.txt": 1, "cards_001.txt": 0}
    rows = (tmp_path / "findings" / "cards_000.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and '"bbbbbbbbbbbb"' in rows[0]
    assert (tmp_path / "findings" / "cards_001.jsonl").read_text(encoding="utf-8") == ""
    assert client.calls[0]["purpose"] == "deck_review"
    # A second run finds nothing left to ask.
    assert api_jobs.review_batches(tmp_path, client=FakeClient([]), max_calls=10) == {}


def test_jobs_refuse_without_approval(tmp_path: Path, capsys: Any) -> None:
    wanted = tmp_path / "wanted.txt"
    wanted.write_text("tatoeba\t1\tEin Satz.\n", encoding="utf-8")
    store = tmp_path / "store.jsonl"
    code = api_jobs.main(["glosses", "--wanted", str(wanted), "--store", str(store)])
    assert code == 2 and "REFUSED" in capsys.readouterr().out
    (tmp_path / "cards_000.txt").write_text("a\tb\tc\td\te\n", encoding="utf-8")
    code = api_jobs.main(["review", str(tmp_path)])
    assert code == 2 and "REFUSED" in capsys.readouterr().out
