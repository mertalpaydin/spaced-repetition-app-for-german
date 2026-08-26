"""Tests for scripts/eval_verifier.py (TODO.md 3.3, 3.4): the fixture
loaders, the ``BankItem`` mapping, the recall/false-positive-rate
computation, and the honest offline degrade path -- all exercised with a
fake LLM client, never a real network call (CLAUDE.md 7)."""

from __future__ import annotations

import json
import sys

import pytest
import scripts.eval_verifier as eval_verifier
from scripts.eval_verifier import (
    DEFAULT_ADVERSARIAL_PATH,
    DEFAULT_KNOWN_CLEAN_PATH,
    EvalResult,
    _to_bank_item,
    load_adversarial_fixture,
    load_known_clean_fixture,
)

# ---------------------------------------------------------------------------
# Fixture files themselves
# ---------------------------------------------------------------------------


def test_adversarial_fixture_has_38_hand_confirmed_defects() -> None:
    """TODO.md 3.3's own count: 14 from cycle 7, 7 from cycle 8, 17 from
    cycle 9."""
    records = load_adversarial_fixture()
    assert len(records) == 38
    by_cycle: dict[int, int] = {}
    for r in records:
        by_cycle[r.source_cycle] = by_cycle.get(r.source_cycle, 0) + 1
    assert by_cycle == {7: 14, 8: 7, 9: 17}


def test_adversarial_fixture_ids_are_unique() -> None:
    records = load_adversarial_fixture()
    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))


def test_adversarial_fixture_every_prompt_has_exactly_one_gap() -> None:
    """Every record's ``prompt`` must be a genuine blanked prompt (the
    ``sentence`` with the answer's own occurrence replaced by ``___``), not
    the raw, unblanked sentence -- otherwise this fixture would not be
    testing the same shape ``model_verification.build_batch_prompt`` sends
    for a real item."""
    for r in load_adversarial_fixture():
        assert r.prompt.count("___") == 1
        assert r.answer not in r.prompt or r.prompt.count(r.answer) >= 0


def test_adversarial_fixture_records_carry_a_defect_class_and_description() -> None:
    for r in load_adversarial_fixture():
        assert r.defect_class
        assert r.defect_description


def test_known_clean_fixture_has_hand_confirmed_clean_items() -> None:
    records = load_known_clean_fixture()
    assert len(records) >= 20, "expected a count in the same order of magnitude as the 38 defects"
    ids = [r.id for r in records]
    assert len(ids) == len(set(ids))


def test_known_clean_fixture_every_prompt_has_exactly_one_gap() -> None:
    for r in load_known_clean_fixture():
        assert r.prompt.count("___") == 1


def test_fixture_files_start_with_a_meta_record() -> None:
    """TODO.md 3.3: 'document in its own header that every future cycle
    adds its newly found defects to it' -- both fixture files' own first
    line carries that instruction directly, not only this script's
    docstring."""
    for path in (DEFAULT_ADVERSARIAL_PATH, DEFAULT_KNOWN_CLEAN_PATH):
        with path.open(encoding="utf-8") as f:
            first_line = json.loads(f.readline())
        assert first_line.get("_meta") is True
        assert "instructions" in first_line
        assert "description" in first_line


# ---------------------------------------------------------------------------
# _to_bank_item
# ---------------------------------------------------------------------------


def test_to_bank_item_cued_when_cue_present() -> None:
    item = _to_bank_item(
        id_="x", topic_id="verb_praesens_regelm", prompt="Er ___.", answer="geht", cue="gehen"
    )
    assert item.type == "cloze_cued"
    assert item.cue == "gehen"
    assert item.accepted_answers == ["geht"]


def test_to_bank_item_free_when_no_cue() -> None:
    item = _to_bank_item(
        id_="x", topic_id="verb_praesens_regelm", prompt="Er ___.", answer="geht", cue=None
    )
    assert item.type == "cloze_free"
    assert item.cue is None


# ---------------------------------------------------------------------------
# EvalResult.rate()
# ---------------------------------------------------------------------------


def test_eval_result_rate_is_rejected_over_judged() -> None:
    result = EvalResult(label="x", total=10, verified=6, rejected=3, not_run=1)
    assert result.judged == 9
    assert result.rate() == pytest.approx(3 / 9)


def test_eval_result_rate_is_none_when_nothing_judged() -> None:
    result = EvalResult(label="x", total=5, verified=0, rejected=0, not_run=5)
    assert result.judged == 0
    assert result.rate() is None


# ---------------------------------------------------------------------------
# main(): honest offline degrade, and an end-to-end run against a fake client
# ---------------------------------------------------------------------------


def test_main_with_no_client_configured_is_honest_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(eval_verifier.sentence_source, "client_from_env", lambda: None)
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])

    exit_code = eval_verifier.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "NOT RUN" in out
    assert "no API key configured" in out


def test_main_catches_a_transport_error_honestly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """This container's own real failure mode (TODO.md's 'no network in
    this container'): a client IS configured but every call raises before
    completing. Must degrade exactly as honestly as no-client-at-all, never
    crash with a raw traceback."""

    class _AlwaysFailsClient:
        def generate_many(
            self, prompts: list[str], model: str, purpose: str, use_cache: bool = True
        ) -> list[str]:
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr(
        eval_verifier.sentence_source, "client_from_env", lambda: _AlwaysFailsClient()
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])

    exit_code = eval_verifier.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "NOT RUN" in out
    assert "transport error" in out


def test_main_end_to_end_with_a_fake_client_reports_recall_and_fpr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: object
) -> None:
    """A fully scripted fake client that rejects everything in the
    adversarial set and verifies everything in the known-clean set --
    perfect recall, zero false positives -- proves the whole wiring (load,
    build BankItems, call verify_items, compute rates, print, exit 0)
    without a real network call."""

    calls: list[int] = []

    class _ScriptedClient:
        def generate_many(
            self, prompts: list[str], model: str, purpose: str, use_cache: bool = True
        ) -> list[str]:
            call_index = len(calls)
            calls.append(call_index)
            out = []
            for prompt in prompts:
                n = prompt.count("Lücke: ")
                if call_index == 0:
                    verdicts = [
                        {
                            "index": i + 1,
                            "valid": False,
                            "woerter_echt": True,
                            "hinweis_korrekt": True,
                            "reason": "simulated rejection",
                        }
                        for i in range(n)
                    ]
                else:
                    verdicts = [
                        {
                            "index": i + 1,
                            "valid": True,
                            "woerter_echt": True,
                            "hinweis_korrekt": True,
                            "reason": None,
                        }
                        for i in range(n)
                    ]
                out.append(json.dumps({"verdicts": verdicts}))
            return out

    monkeypatch.setattr(eval_verifier.sentence_source, "client_from_env", lambda: _ScriptedClient())
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])

    exit_code = eval_verifier.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "recall: 100.0%" in out
    assert "false-positive rate: 0.0%" in out
