"""The agent-driven deck jobs: parsing and acceptance, never the agent."""

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
