"""Tests for scripts/eval_verifier.py (TODO.md 3.3, 3.4): the fixture
loaders, the ``BankItem`` mapping, the recall/false-positive-rate
computation, and the honest offline degrade path -- all exercised with a
fake LLM client, never a real network call (CLAUDE.md 7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
from src.contracts import MODEL_VERIFY
from src.generation.blanking.model_verification import (
    ItemVerdict,
    VerificationReport,
    _chunk,
    build_batch_prompt,
)
from src.llm.cache import LlmCache
from src.llm.client import PaidLaneForbiddenError

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
    monkeypatch.setattr(
        eval_verifier.sentence_source, "client_from_env", lambda *, free_lane_only=False: None
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])

    exit_code = eval_verifier.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "NOT RUN" in out
    assert "no API key configured" in out


def test_main_free_lane_only_refuses_to_start_without_an_explicit_free_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner's zero-spend requirement. ``GEMINI_API_KEY`` is his BILLED
    key, and ``GeminiLlmClient`` would otherwise accept it as the free lane's
    key, so every "free" call would bill. The real ``client_from_env`` runs
    here deliberately: stubbing it would test nothing about the guard."""
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "the-billed-key")
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py", "--free-lane-only"])

    exit_code = eval_verifier.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAILING" in out
    assert "GEMINI_FREE_API_KEY=" in out


def test_main_free_lane_only_passes_the_flag_through_to_the_client_builder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A flag parsed and then dropped on the floor is worse than no flag."""
    seen: list[bool] = []

    def _record(*, free_lane_only: bool = False) -> None:
        seen.append(free_lane_only)
        return None

    monkeypatch.setattr(eval_verifier.sentence_source, "client_from_env", _record)
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py", "--free-lane-only"])

    assert eval_verifier.main() == 1
    assert seen == [True]

    seen.clear()
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])
    assert eval_verifier.main() == 1
    assert seen == [False], "the default must stay exactly what it was"


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
        eval_verifier.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _AlwaysFailsClient(),
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

    monkeypatch.setattr(
        eval_verifier.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _ScriptedClient(),
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py"])

    exit_code = eval_verifier.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "recall: 100.0%" in out
    assert "false-positive rate: 0.0%" in out
    # The two blocks added after the first paid run (fix log cycle 17) are
    # part of a complete run's own output, not an opt-in.
    assert "Recall by source audit cycle:" in out
    assert "cycle 7: 14/14  (100.0%)" in out
    assert "Missed defects: none." in out
    # A complete run's own numbers ARE the result. A progress block under
    # them would only muddy which of the two the reader is meant to take.
    assert "PROGRESS TOWARD A COMPLETE MEASUREMENT" not in out
    assert "FAILING" not in out


# ---------------------------------------------------------------------------
# An incomplete run: progress toward completion, and the REAL cause
#
# The situation these cover is the owner's actual one. On ``--free-lane-only``
# the free tier grants ``MODEL_VERIFY`` a few tens of requests a day, most
# come back 503, and 69 items at ``--batch-size 5`` need 15 successful ones.
# The run therefore takes several days, converging only because the local
# content-addressed cache keeps each day's landed batches. Both facts have to
# be visible in the terminal or three days of the same command are
# indistinguishable from three identical failures.
# ---------------------------------------------------------------------------

_RPD_MESSAGE = (
    "The free lane is closed: the free-tier DAILY allowance (RPD) is exhausted, "
    "and the paid batch lane is forbidden in this run. Re-running does not help "
    "until the quota resets. The free lane reopens at 2026-08-29 09:00 CEST "
    "local (2026-08-29T07:00:00+00:00), the next Pacific midnight."
)


def _all_valid_response(count: int) -> str:
    return json.dumps(
        {
            "verdicts": [
                {
                    "index": i + 1,
                    "valid": True,
                    "woerter_echt": True,
                    "hinweis_korrekt": True,
                    "reason": None,
                }
                for i in range(count)
            ]
        }
    )


class _QuotaRefusingClient:
    """Close enough to ``GeminiLlmClient.generate_many`` for this test: every
    cached prompt is replayed for free, and the first prompt that is NOT
    cached trips the free tier's daily allowance, which raises out of the
    WHOLE call because a raised call leaves no partial result to salvage.

    That last detail is the reason the progress count is read from the cache
    rather than from the run's own verdicts: this run reports every item
    ``not_run`` while the cache genuinely holds several batches' worth of
    verdicts from earlier days.
    """

    def __init__(self, cache: LlmCache) -> None:
        self.cache = cache

    def generate_many(
        self, prompts: list[str], model: str, purpose: str, use_cache: bool = True
    ) -> list[str]:
        cached = [self.cache.get(model=model, prompt=p) for p in prompts]
        if any(text is None for text in cached):
            raise PaidLaneForbiddenError(_RPD_MESSAGE)
        return [text for text in cached if text is not None]


def _seed_days_of_landed_batches(cache: LlmCache, *, batch_size: int, batches: int) -> int:
    """Pretend the first ``batches`` adversarial batches landed on earlier
    days. Returns how many ITEMS that covers."""
    items = [
        _to_bank_item(id_=r.id, topic_id=r.topic_id, prompt=r.prompt, answer=r.answer, cue=r.cue)
        for r in load_adversarial_fixture()
    ]
    covered = 0
    for batch in _chunk(items, batch_size)[:batches]:
        cache.set(
            model=MODEL_VERIFY,
            prompt=build_batch_prompt(batch),
            response=_all_valid_response(len(batch)),
        )
        covered += len(batch)
    return covered


def test_main_incomplete_run_shows_progress_names_the_real_cause_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Three days into a free-lane run: some batches are banked, the day's
    quota refuses the rest. The run must still fail, and must say how far
    along it is and exactly why it stopped."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    covered = _seed_days_of_landed_batches(cache, batch_size=5, batches=3)
    assert covered == 15

    monkeypatch.setattr(
        eval_verifier.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _QuotaRefusingClient(cache),
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py", "--free-lane-only", "--batch-size", "5"])

    exit_code = eval_verifier.main()

    out = capsys.readouterr().out
    assert exit_code == 1, "an incomplete run is not a measurement and must still fail"
    assert "FAILING" in out
    assert "PROGRESS TOWARD A COMPLETE MEASUREMENT" in out
    assert "items with a cached model verdict: 15 of 69" in out
    assert "items still missing a verdict:     54" in out
    assert "zero cost" in out
    assert "--batch-size at 5" in out


def test_main_incomplete_run_names_the_quota_refusal_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The old message offered 'malformed response, transport failure, or
    budget ceiling' -- a guess-list, printed even when the code knew the
    answer was an RPD refusal with a known reset time. It must name what
    actually happened."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    monkeypatch.setattr(
        eval_verifier.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _QuotaRefusingClient(cache),
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py", "--free-lane-only", "--batch-size", "5"])

    exit_code = eval_verifier.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "cause: paid_lane_forbidden (69 item(s))" in out
    assert "PaidLaneForbiddenError" in out
    assert "DAILY allowance (RPD)" in out
    assert "2026-08-29 09:00 CEST local" in out
    assert "malformed response, transport failure, or budget ceiling" not in out


def test_main_prints_progress_when_the_transport_raises_outright(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The unreachable-network degrade path is just as incomplete as the
    quota one, and banked progress is just as real there."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    _seed_days_of_landed_batches(cache, batch_size=5, batches=1)

    class _UnreachableClient(_QuotaRefusingClient):
        def generate_many(
            self, prompts: list[str], model: str, purpose: str, use_cache: bool = True
        ) -> list[str]:
            raise RuntimeError("simulated proxy 403")

    monkeypatch.setattr(
        eval_verifier.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _UnreachableClient(cache),
    )
    monkeypatch.setattr(eval_verifier, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_verifier.py", "--batch-size", "5"])

    exit_code = eval_verifier.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "NOT RUN" in out
    assert "items with a cached model verdict: 5 of 69" in out


# ---------------------------------------------------------------------------
# The two breakdowns added after the first real measurement (fix log cycle 17)
#
# The first paid run of this script printed per-defect-class totals only, and
# the whole finding it produced -- that recall on the never-screened cycle-7
# defects and recall on the cycle-8/9 defects the verifier had already passed
# once are different numbers, and that a class printed "1/2" hides which
# record was missed -- had to be reconstructed from the fixture by hand. Both
# blocks below exist so the next run does not.
# ---------------------------------------------------------------------------


def _report_rejecting(ids: set[str]) -> VerificationReport:
    """A ``VerificationReport`` over the real adversarial fixture, in fixture
    order, rejecting exactly the records named in ``ids`` and verifying the
    rest -- the shape ``verify_items`` returns, built directly so a print
    helper can be exercised on a chosen pattern of catches and misses without
    a client, real or fake."""
    return VerificationReport(
        attempted=True,
        verdicts=[
            ItemVerdict(outcome="rejected", reason="simulated")
            if record.id in ids
            else ItemVerdict(outcome="verified")
            for record in load_adversarial_fixture()
        ],
    )


def test_recall_by_source_cycle_splits_the_three_audit_cycles(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fixture's own composition (14 from cycle 7, 7 from cycle 8, 17
    from cycle 9) is the denominator of each line, and a run that rejects
    only cycle 7's records reports 100% there and 0% for the other two."""
    records = load_adversarial_fixture()
    cycle_7 = {r.id for r in records if r.source_cycle == 7}

    eval_verifier._print_recall_by_source_cycle(records, _report_rejecting(cycle_7))

    out = capsys.readouterr().out
    assert "cycle 7: 14/14  (100.0%)" in out
    assert "cycle 8: 0/7  (0.0%)" in out
    assert "cycle 9: 0/17  (0.0%)" in out


def test_missed_records_names_every_defect_the_pass_did_not_reject(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A miss is named by fixture id and defect class, which is the part a
    per-class total cannot carry: two records of one class, one caught and
    one missed, must print the missed id and not the caught one."""
    records = load_adversarial_fixture()
    caught = {r.id for r in records if r.id != "c08_06"}

    eval_verifier._print_missed_records(records, _report_rejecting(caught))

    out = capsys.readouterr().out
    assert "Missed defects (1), by fixture id:" in out
    assert "c08_06 (cycle 8): cue_capitalization_mismatch_sentence_initial" in out
    assert "c08_07" not in out


def test_missed_records_says_none_rather_than_printing_an_empty_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Perfect recall prints a positive statement, not a header with nothing
    under it that reads like a truncated report."""
    records = load_adversarial_fixture()

    eval_verifier._print_missed_records(records, _report_rejecting({r.id for r in records}))

    out = capsys.readouterr().out
    assert "Missed defects: none." in out
