"""Tests for scripts/eval_gloss_adversarial.py and its fixture (TODO.md 2.2):
the fixture's own shape, the ``BankItem`` mapping (the gloss must actually
reach the prompt), the paired recall arithmetic, and the honest offline
degrade path -- all with a fake LLM client, never a real network call
(CLAUDE.md section 7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import scripts.eval_gloss_adversarial as evg
from scripts.eval_gloss_adversarial import (
    DEFAULT_FIXTURE_PATH,
    DEFECT_KINDS,
    ArmResult,
    GlossRecord,
    correct_rows,
    kind_results,
    load_fixture,
    to_bank_item,
    wrong_rows,
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
# The fixture file itself
# ---------------------------------------------------------------------------


def test_fixture_has_equal_wrong_and_correct_arms() -> None:
    """The false-positive rate is only meaningful against as many correct
    glosses as wrong ones, which is why the fixture carries both arms in
    equal number rather than only the defects."""
    records = load_fixture()
    assert len(wrong_rows(records)) == len(correct_rows(records))
    assert len(records) == 72


def test_fixture_covers_every_defect_kind_with_enough_rows_to_mean_something() -> None:
    counts = dict.fromkeys(DEFECT_KINDS, 0)
    for record in wrong_rows(load_fixture()):
        assert record.defect_kind is not None
        counts[record.defect_kind] += 1
    assert all(n >= 6 for n in counts.values()), counts


def test_fixture_every_pair_has_exactly_one_wrong_and_one_correct_row() -> None:
    by_pair: dict[str, list[str]] = {}
    for record in load_fixture():
        by_pair.setdefault(record.pair_id, []).append(record.arm)
    assert by_pair
    for pair_id, arms in by_pair.items():
        assert sorted(arms) == ["correct", "wrong"], pair_id


def test_fixture_paired_rows_differ_only_in_the_gloss() -> None:
    """The matched design is the whole point: if a pair's two rows differed
    in the German as well, a rejection could not be attributed to the
    gloss."""
    by_pair: dict[str, dict[str, GlossRecord]] = {}
    for record in load_fixture():
        by_pair.setdefault(record.pair_id, {})[record.arm] = record
    for pair_id, arms in by_pair.items():
        wrong, correct = arms["wrong"], arms["correct"]
        assert wrong.prompt == correct.prompt, pair_id
        assert wrong.answer == correct.answer, pair_id
        assert wrong.cue == correct.cue, pair_id
        assert wrong.source_item_id == correct.source_item_id, pair_id
        assert wrong.gloss_en != correct.gloss_en, pair_id


def test_fixture_every_prompt_has_exactly_one_gap() -> None:
    for record in load_fixture():
        assert record.prompt.count("___") == 1


def test_fixture_every_wrong_row_says_what_the_defect_is() -> None:
    for record in wrong_rows(load_fixture()):
        assert record.defect_kind in DEFECT_KINDS
        assert record.defect_description


def test_fixture_correct_rows_carry_no_defect_label() -> None:
    for record in correct_rows(load_fixture()):
        assert record.defect_kind is None
        assert record.defect_description is None


def test_fixture_ids_are_unique() -> None:
    ids = [r.id for r in load_fixture()]
    assert len(ids) == len(set(ids))


def test_fixture_starts_with_a_meta_record_carrying_its_own_instructions() -> None:
    with DEFAULT_FIXTURE_PATH.open(encoding="utf-8") as f:
        first_line = json.loads(f.readline())
    assert first_line.get("_meta") is True
    assert "description" in first_line
    assert "instructions" in first_line


# ---------------------------------------------------------------------------
# to_bank_item: the gloss has to actually reach the prompt
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> GlossRecord:
    base: dict[str, object] = {
        "id": "pair_99_wrong",
        "pair_id": "pair_99",
        "source_item_id": "corpus_x",
        "topic_id": "verb_praesens_regelm",
        "prompt": "Er ___ nach Hause.",
        "answer": "geht",
        "cue": "gehen",
        "arm": "wrong",
        "gloss_en": "He went home.",
        "defect_kind": "wrong_tense",
        "defect_description": "present moved to past",
    }
    base.update(overrides)
    return GlossRecord(**base)  # type: ignore[arg-type]


def test_to_bank_item_carries_the_gloss_into_the_verification_prompt() -> None:
    """Without this the whole eval would measure nothing: the gloss is the
    only thing the two arms differ in."""
    item = to_bank_item(_record())
    prompt = build_batch_prompt([item])
    assert "He went home." in prompt
    assert "Englische Übersetzung: He went home." in prompt


def test_to_bank_item_is_cued_when_the_record_has_a_cue() -> None:
    item = to_bank_item(_record())
    assert item.type == "cloze_cued"
    assert item.cue == "gehen"


def test_to_bank_item_is_free_when_the_record_has_no_cue() -> None:
    item = to_bank_item(_record(cue=None))
    assert item.type == "cloze_free"
    assert item.cue is None


# ---------------------------------------------------------------------------
# ArmResult.rate()
# ---------------------------------------------------------------------------


def test_arm_result_rate_is_rejected_over_judged() -> None:
    result = ArmResult(label="x", total=10, verified=6, rejected=3, not_run=1)
    assert result.judged == 9
    assert result.rate() == pytest.approx(3 / 9)


def test_arm_result_rate_is_none_when_nothing_was_judged() -> None:
    result = ArmResult(label="x", total=4, verified=0, rejected=0, not_run=4)
    assert result.rate() is None


# ---------------------------------------------------------------------------
# kind_results: a rejection that happens in BOTH arms is not a gloss catch
# ---------------------------------------------------------------------------


def _report(outcomes: list[str]) -> VerificationReport:
    return VerificationReport(
        attempted=True,
        verdicts=[
            ItemVerdict(outcome=o, reason=None if o == "verified" else "r")  # type: ignore[arg-type]
            for o in outcomes
        ],
    )


def _two_pairs() -> list[GlossRecord]:
    return [
        _record(id="pair_01_wrong", pair_id="pair_01", arm="wrong", defect_kind="wrong_tense"),
        _record(
            id="pair_01_correct",
            pair_id="pair_01",
            arm="correct",
            gloss_en="He goes home.",
            defect_kind=None,
            defect_description=None,
        ),
        _record(id="pair_02_wrong", pair_id="pair_02", arm="wrong", defect_kind="wrong_tense"),
        _record(
            id="pair_02_correct",
            pair_id="pair_02",
            arm="correct",
            gloss_en="He goes home.",
            defect_kind=None,
            defect_description=None,
        ),
    ]


def test_kind_results_counts_a_gloss_only_catch_as_attributable() -> None:
    records = _two_pairs()
    results = {
        r.kind: r
        for r in kind_results(
            records, _report(["rejected", "verified"]), _report(["verified", "verified"])
        )
    }
    tense = results["wrong_tense"]
    assert tense.total == 2
    assert tense.attributable == 1
    assert tense.missed == 1
    assert tense.rejected_both == 0


def test_kind_results_does_not_credit_a_rejection_that_happens_in_both_arms() -> None:
    """An item the verifier dislikes with its REAL gloss was not caught by
    the gloss, and counting it as recall would overstate the safety net."""
    records = _two_pairs()
    results = {
        r.kind: r
        for r in kind_results(
            records, _report(["rejected", "rejected"]), _report(["rejected", "verified"])
        )
    }
    tense = results["wrong_tense"]
    assert tense.attributable == 1
    assert tense.rejected_both == 1
    assert tense.missed == 0


def test_kind_results_marks_a_pair_unresolved_when_either_arm_did_not_run() -> None:
    records = _two_pairs()
    results = {
        r.kind: r
        for r in kind_results(
            records, _report(["rejected", "verified"]), _report(["not_run", "verified"])
        )
    }
    tense = results["wrong_tense"]
    assert tense.unresolved == 1
    assert tense.attributable == 0
    assert tense.missed == 1


def test_kind_results_reports_every_kind_even_when_a_kind_has_no_rows() -> None:
    records = _two_pairs()
    results = kind_results(
        records, _report(["verified", "verified"]), _report(["verified", "verified"])
    )
    assert [r.kind for r in results] == list(DEFECT_KINDS)


# ---------------------------------------------------------------------------
# main(): refuses to invent numbers, and runs end to end against a fake client
# ---------------------------------------------------------------------------


def test_main_with_no_client_configured_reports_not_run_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner's explicit requirement: report 'not run', never a zero."""
    monkeypatch.setattr(
        evg.sentence_source, "client_from_env", lambda *, free_lane_only=False: None
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py"])

    exit_code = evg.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "NOT RUN" in out
    assert "no API key configured" in out
    assert "0.0%" not in out


def test_main_free_lane_only_refuses_to_start_without_an_explicit_free_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero paid spend is a hard requirement for this run, and
    ``GEMINI_API_KEY`` is the owner's BILLED key. The real ``client_from_env``
    runs here deliberately: stubbing it would test nothing about the guard."""
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "the-billed-key")
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py", "--free-lane-only"])

    exit_code = evg.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAILING" in out
    assert "GEMINI_FREE_API_KEY=" in out


def test_main_catches_a_transport_error_honestly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _AlwaysFailsClient:
        def generate_many(
            self,
            prompts: list[str],
            model: str,
            purpose: str,
            use_cache: bool = True,
            cache_namespace: str | None = None,
        ) -> list[str]:
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr(
        evg.sentence_source, "client_from_env", lambda *, free_lane_only=False: _AlwaysFailsClient()
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py"])

    exit_code = evg.main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "NOT RUN" in out
    assert "transport error" in out


class _ScriptedClient:
    """Rejects every item in the first call (the wrong arm) and verifies
    every item in every later call (the correct arm) -- perfect attributable
    recall, zero false positives, with no network."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_many(
        self,
        prompts: list[str],
        model: str,
        purpose: str,
        use_cache: bool = True,
        cache_namespace: str | None = None,
    ) -> list[str]:
        reject = self.calls == 0
        self.calls += 1
        out: list[str] = []
        for prompt in prompts:
            n = prompt.count("Lücke: ")
            verdicts = [
                {
                    "index": i + 1,
                    "valid": not reject,
                    "woerter_echt": True,
                    "hinweis_korrekt": True,
                    "reason": "simulated rejection" if reject else None,
                }
                for i in range(n)
            ]
            out.append(json.dumps({"verdicts": verdicts}))
        return out


def test_main_end_to_end_reports_per_kind_recall_and_a_false_positive_rate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _ScriptedClient()
    monkeypatch.setattr(
        evg.sentence_source, "client_from_env", lambda *, free_lane_only=False: client
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py", "--batch-size", "50"])

    exit_code = evg.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Recall by defect kind" in out
    for kind in DEFECT_KINDS:
        assert kind in out
    assert "wrong_definiteness   6/6" in out
    assert "No wrong gloss was accepted." in out
    assert "No correct gloss was rejected." in out
    # A complete run's own numbers ARE the result; a progress block under
    # them would only muddy which of the two the reader is meant to take.
    assert "PROGRESS TOWARD A COMPLETE MEASUREMENT" not in out
    assert "FAILING" not in out


def test_main_verifies_the_two_arms_in_separate_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pair's two rows must never sit in one prompt: the model would then
    be comparing two translations of one German sentence instead of judging
    each item on its own. A pair's rows share their German verbatim, so a
    German prompt appearing twice inside one batch prompt is exactly the
    failure this guards against. (Comparing the glosses themselves would not
    work: an ``unrelated`` row's wrong gloss is deliberately another item's
    real gloss, so the same English string legitimately appears in both
    arms.)"""
    seen: list[str] = []

    class _RecordingClient(_ScriptedClient):
        def generate_many(
            self,
            prompts: list[str],
            model: str,
            purpose: str,
            use_cache: bool = True,
            cache_namespace: str | None = None,
        ) -> list[str]:
            seen.extend(prompts)
            return super().generate_many(prompts, model, purpose, use_cache)

    monkeypatch.setattr(
        evg.sentence_source, "client_from_env", lambda *, free_lane_only=False: _RecordingClient()
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py", "--batch-size", "50"])

    assert evg.main() == 0

    assert len(seen) == 2, "one batch prompt per arm at this batch size"
    for record in load_fixture():
        for prompt in seen:
            assert prompt.count(f"Lücke: {record.prompt}") <= 1


# ---------------------------------------------------------------------------
# An incomplete run: progress toward completion, and the REAL cause
#
# Same situation as scripts/eval_verifier.py's own: on ``--free-lane-only``
# the free tier's daily allowance does not cover 72 rows in two arms, so the
# run spans several days and converges only because the local
# content-addressed cache keeps each day's landed batches. That has to be
# visible, or repeated runs read as repeated failures.
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
    """Close enough to ``GeminiLlmClient.generate_many`` for this test: cached
    prompts are replayed for free, and the first uncached one trips the free
    tier's daily allowance, which raises out of the whole call. The run then
    reports every row ``not_run`` even though the cache holds earlier days'
    verdicts -- which is exactly why progress is counted from the cache."""

    def __init__(self, cache: LlmCache) -> None:
        self.cache = cache

    def generate_many(
        self,
        prompts: list[str],
        model: str,
        purpose: str,
        use_cache: bool = True,
        cache_namespace: str | None = None,
    ) -> list[str]:
        cached = [self.cache.get(model=model, prompt=p) for p in prompts]
        if any(text is None for text in cached):
            raise PaidLaneForbiddenError(_RPD_MESSAGE)
        return [text for text in cached if text is not None]


def _seed_days_of_landed_batches(cache: LlmCache, *, batch_size: int, batches: int) -> int:
    """Pretend the first ``batches`` batches of the WRONG arm landed on
    earlier days. Returns how many rows that covers."""
    items = [to_bank_item(r) for r in wrong_rows(load_fixture())]
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
    cache = LlmCache(cache_dir=tmp_path / "llm")
    covered = _seed_days_of_landed_batches(cache, batch_size=5, batches=2)
    assert covered == 10

    monkeypatch.setattr(
        evg.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _QuotaRefusingClient(cache),
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(
        sys, "argv", ["eval_gloss_adversarial.py", "--free-lane-only", "--batch-size", "5"]
    )

    exit_code = evg.main()

    out = capsys.readouterr().out
    assert exit_code == 1, "an incomplete run is not a measurement and must still fail"
    assert "FAILING" in out
    assert "PROGRESS TOWARD A COMPLETE MEASUREMENT" in out
    assert "rows with a cached model verdict: 10 of 72" in out
    assert "rows still missing a verdict:     62" in out
    assert "--batch-size at 5" in out


def test_main_incomplete_run_names_the_quota_refusal_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The old message offered 'malformed response, transport failure, or
    budget ceiling' even when the code knew it was an RPD refusal with a
    known reset time."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    monkeypatch.setattr(
        evg.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _QuotaRefusingClient(cache),
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(
        sys, "argv", ["eval_gloss_adversarial.py", "--free-lane-only", "--batch-size", "5"]
    )

    exit_code = evg.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "cause: paid_lane_forbidden (72 item(s))" in out
    assert "PaidLaneForbiddenError" in out
    assert "DAILY allowance (RPD)" in out
    assert "2026-08-29 09:00 CEST local" in out
    assert "malformed response, transport failure, or budget ceiling" not in out


def test_main_prints_progress_when_the_transport_raises_outright(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    cache = LlmCache(cache_dir=tmp_path / "llm")
    _seed_days_of_landed_batches(cache, batch_size=5, batches=1)

    class _UnreachableClient(_QuotaRefusingClient):
        def generate_many(
            self,
            prompts: list[str],
            model: str,
            purpose: str,
            use_cache: bool = True,
            cache_namespace: str | None = None,
        ) -> list[str]:
            raise RuntimeError("simulated proxy 403")

    monkeypatch.setattr(
        evg.sentence_source,
        "client_from_env",
        lambda *, free_lane_only=False: _UnreachableClient(cache),
    )
    monkeypatch.setattr(evg, "load_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["eval_gloss_adversarial.py", "--batch-size", "5"])

    exit_code = evg.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "NOT RUN" in out
    assert "rows with a cached model verdict: 5 of 72" in out
