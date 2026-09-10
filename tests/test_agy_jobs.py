"""The agent-driven deck jobs: parsing and acceptance, never the agent."""

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import agy_jobs
from src.contracts import ContextRequest


def _request(card_id: str = "c1") -> ContextRequest:
    return ContextRequest(
        card_id=card_id,
        unit_id="cn:trotzdem",
        connector_display="trotzdem",
        sentence_de="Trotzdem kam sie.",
        gloss_en="She came anyway.",
        prompt_version=1,
    )


def test_context_replies_are_checked_and_missing_ones_recorded() -> None:
    replies = [
        {
            "card_id": "c1",
            "context_de": "Es regnete den ganzen Tag.",
            "context_en": "It rained all day.",
        },
        {"card_id": "c2", "context_de": "Trotzdem war es schön.", "context_en": "Still nice."},
    ]
    records = agy_jobs.records_from_replies(
        [_request("c1"), _request("c2"), _request("c3")],
        replies,
        validate=lambda _: True,
        now=datetime(2026, 9, 9, tzinfo=UTC),
    )
    by_id = {r.card_id: r for r in records}
    assert by_id["c1"].accepted and by_id["c1"].context_de == "Es regnete den ganzen Tag."
    assert not by_id["c2"].accepted and by_id["c2"].reject_reason == "contains_connector"
    assert not by_id["c3"].accepted and by_id["c3"].reject_reason == "missing"
    assert by_id["c1"].model.startswith("agy:")


def test_gloss_rejects_empty_echo_and_wrong_length() -> None:
    assert agy_jobs.gloss_reject_reason("Er wartet auf den Bus.", "") == "empty"
    assert (
        agy_jobs.gloss_reject_reason("Er wartet auf den Bus.", "Er wartet auf den Bus.") == "echo"
    )
    assert agy_jobs.gloss_reject_reason("Er wartet auf den Bus.", "He") == "length"
    assert agy_jobs.gloss_reject_reason("Er wartet auf den Bus.", "He waits for the bus.") is None


def test_read_jsonl_skips_junk_lines(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    path.write_text('{"id": "0", "en": "Hi."}\nnot json\n[1,2]\n\n', encoding="utf-8")
    assert agy_jobs.read_jsonl(path) == [{"id": "0", "en": "Hi."}]
    assert agy_jobs.read_jsonl(tmp_path / "missing.jsonl") == []


def test_run_agent_survives_a_non_json_reply() -> None:
    class Result:
        stdout = "something went wrong"

    result = agy_jobs.run_agent(
        Path("."), "p", model="m", timeout="1m", runner=lambda *a, **k: Result()
    )
    assert result.status == "NO_JSON"


def test_store_write_retries_while_the_file_is_locked(monkeypatch, tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky(path: Path, store: dict) -> None:  # type: ignore[type-arg]
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")

    monkeypatch.setattr(agy_jobs, "_write_store_atomic", flaky)
    slept: list[float] = []
    agy_jobs.write_store_with_retry(tmp_path / "s.jsonl", {}, sleep=slept.append)
    assert calls["n"] == 3 and slept == [5.0, 5.0]


def test_tatoeba_only_glosses_are_glossed_again(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from scripts.build_translations import TranslationRecord

    wanted = tmp_path / "wanted.txt"
    lines = ["tatoeba", "1", "Er wartet.", "|", "azure", "2", "Sie geht."]
    wanted.write_text(chr(9).join(lines[:3]) + chr(10) + chr(9).join(lines[4:]) + chr(10), "utf-8")
    now = datetime.now(UTC)
    store = {
        "Er wartet.": TranslationRecord(
            german="Er wartet.", english="He waits.", source="tatoeba", written_at=now
        ),
        "Sie geht.": TranslationRecord(
            german="Sie geht.", english="She goes.", source="azure", written_at=now
        ),
    }
    monkeypatch.setattr(agy_jobs, "_load_store", lambda path: store)
    seen: list[list[str]] = []

    def fake_agent(workspace: Path, prompt: str, **kw: object) -> agy_jobs.AgentResult:
        rows = agy_jobs.read_jsonl(workspace / "input.jsonl")
        seen.append([str(r["de"]) for r in rows])
        out = [{"id": r["id"], "en": "He is waiting."} for r in rows]
        (workspace / "output.jsonl").write_text(
            "".join(json.dumps(o) + chr(10) for o in out), encoding="utf-8"
        )
        return agy_jobs.AgentResult(status="SUCCESS")

    monkeypatch.setattr(agy_jobs, "run_agent", fake_agent)
    monkeypatch.setattr(agy_jobs, "write_store_with_retry", lambda path, store: None)
    args = type("A", (), {})()
    args.wanted, args.store, args.model, args.timeout = wanted, tmp_path / "s.jsonl", "m", "1m"
    args.batch_size, args.max_batches, args.max_failures = 10, 5, 2
    assert agy_jobs.job_glosses(args) == 0
    assert seen == [["Er wartet."]]
    assert store["Er wartet."].source == "gemini"
