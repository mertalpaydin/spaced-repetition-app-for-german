"""Unit tests for the centralized LLM client, spend control, cost logging, and AST scanner.

None of these tests make a network call (CLAUDE.md 158). Two layers are faked:

- ``_call_transport`` itself, for tests that only care about cache/cost-log/lane
  bookkeeping and not the real SDK call shape (``_fake_transport_ok`` below).
- ``GeminiLlmClient._get_sdk_client``, substituted with an in-memory fake
  ``google.genai.Client`` (``_FakeSdkClient`` below), for tests that exercise
  the real ``_call_transport`` logic: sync vs. batch routing, 429 translation,
  thinking config, and real token-count extraction from ``usage_metadata``.

Building fake SDK response/error objects requires importing ``google.genai``
here, which is fine: the AST-scanner test below only forbids that import
inside ``src/``, not in ``tests/``.
"""

import ast
import json
import threading
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from src.contracts import (
    MODEL_GENERATE,
    MODEL_VERIFY,
    PURPOSE_SENTENCE_GENERATION,
    THINKING_FLASH_LITE,
    THINKING_VERIFY,
)
from src.llm.client import (
    BatchForbiddenError,
    BudgetExceeded,
    GeminiLlmClient,
    MissingApiKeyError,
    ModelRejectedError,
    PaidLaneForbiddenError,
    QuotaExceededError,
    ServerUnavailableError,
    TokenUsage,
)


def _fake_transport_ok(**kwargs: object) -> tuple[str, TokenUsage]:
    """A deterministic transport double used where the test only cares about
    cache/cost-log/lane bookkeeping, not the real SDK call shape. Mirrors the
    old stub's heuristics so byte-for-byte behaviour of the bookkeeping tests
    is unchanged."""
    prompt = str(kwargs["prompt"])
    purpose = str(kwargs["purpose"])
    lane = str(kwargs["lane"])
    mode = str(kwargs["mode"])
    return (
        f"Fake LLM response for {purpose} (lane={lane}, mode={mode})",
        TokenUsage(prompt_tokens=len(prompt.split()) * 2, completion_tokens=50),
    )


def _fake_generate_content_response(
    text: str,
    prompt_tokens: int,
    candidates_tokens: int,
    thoughts_tokens: int | None = None,
    cached_content_tokens: int | None = None,
    tool_use_prompt_tokens: int | None = None,
    total_tokens: int | None = None,
    model_version: str | None = None,
) -> genai_types.GenerateContentResponse:
    """Build a real ``GenerateContentResponse`` (not a network response) so
    tests exercise the client's actual ``usage_metadata``/``.text`` parsing.

    Every billed-token field Gemini can report is settable, including the
    provider's own ``total_token_count``, so tests can construct both an
    internally consistent response and one whose parts deliberately do not
    add up.
    """
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(parts=[genai_types.Part(text=text)], role="model")
            )
        ],
        model_version=model_version,
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
            thoughts_token_count=thoughts_tokens,
            cached_content_token_count=cached_content_tokens,
            tool_use_prompt_token_count=tool_use_prompt_tokens,
            total_token_count=total_tokens,
        ),
    )


def _fake_batch_job(response: genai_types.GenerateContentResponse) -> genai_types.BatchJob:
    """A ``BatchJob`` that is already terminal (``JOB_STATE_SUCCEEDED``) at
    creation time, so ``_poll_batch_job`` never needs to call ``batches.get``
    (and therefore never needs to sleep)."""
    return genai_types.BatchJob(
        name="batches/fake-job",
        state=genai_types.JobState.JOB_STATE_SUCCEEDED,
        dest=genai_types.BatchJobDestination(
            inlined_responses=[genai_types.InlinedResponse(response=response)]
        ),
    )


def _client_error_429(quota_id: str, retry_delay: str | None = None) -> genai_errors.ClientError:
    """A structured 429 shaped like Google's real error envelope, carrying a
    ``google.rpc.QuotaFailure`` violation with the given ``quotaId`` and,
    optionally, a ``google.rpc.RetryInfo`` detail with the given ``retryDelay``
    (e.g. ``"48s"``, matching the live API's real free-tier response)."""
    details: list[dict[str, Any]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    payload = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Resource has been exhausted.",
            "details": details,
        }
    }
    return genai_errors.ClientError(429, payload, None)


class _FakeModels:
    """Fake ``client.models``: records every ``generate_content`` call and
    returns (or raises) whatever the test configured."""

    def __init__(
        self,
        response: genai_types.GenerateContentResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: object,
        config: genai_types.GenerateContentConfig | None = None,
    ) -> genai_types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeBatches:
    """Fake ``client.batches``: records every ``create`` call. ``get`` raises
    if reached, since every fake job is already terminal at creation."""

    def __init__(
        self,
        job: genai_types.BatchJob | None = None,
        error: Exception | None = None,
    ) -> None:
        self._job = job
        self._error = error
        self.create_calls: list[dict[str, Any]] = []

    def create(self, *, model: str, src: object) -> genai_types.BatchJob:
        self.create_calls.append({"model": model, "src": src})
        if self._error is not None:
            raise self._error
        assert self._job is not None
        return self._job

    def get(self, *, name: str) -> genai_types.BatchJob:
        raise AssertionError("polling should not be needed: the fake batch job is already terminal")


class _FakeSdkClient:
    """Fake ``google.genai.Client``: just the two surfaces ``_call_transport`` uses."""

    def __init__(
        self, models: _FakeModels | None = None, batches: _FakeBatches | None = None
    ) -> None:
        self.models = models or _FakeModels()
        self.batches = batches or _FakeBatches()


def test_llm_client_is_the_only_module_importing_the_sdk(repo_root: Path) -> None:
    """AST-scan src/ for provider SDK imports and assert they appear only in src/llm/client.py."""
    src_dir = repo_root / "src"
    sdk_names = {"google.genai", "google.generativeai", "anthropic", "openai"}

    violating_files: list[str] = []

    for py_file in src_dir.rglob("*.py"):
        if py_file.name == "client.py" and py_file.parent.name == "llm":
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(sdk) for sdk in sdk_names):
                        violating_files.append(str(py_file))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(sdk) for sdk in sdk_names):
                    violating_files.append(str(py_file))

    assert not violating_files, (
        f"Direct SDK imports found outside src/llm/client.py: {violating_files}"
    )


def test_budget_exceeded_raised_when_month_to_date_over_ceiling(tmp_path: Path) -> None:
    """Fake cost_log at ceiling; assert call raises BudgetExceeded before transport is touched."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        spend_ceiling_usd=5.00,
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
    )

    # Pre-populate cost log with $5.50 of spend
    from src.llm.client import CostLogRow

    client._log_cost(
        CostLogRow(
            timestamp=datetime.now(UTC),
            model="gemini-3.5-flash-lite",
            lane="paid",
            prompt_tokens=10_000_000,
            completion_tokens=5_000_000,
            cost_usd=5.50,
        )
    )

    with pytest.raises(BudgetExceeded):
        client.generate("Test prompt exceeding budget")


def test_cost_log_row_written_for_every_call(tmp_path: Path) -> None:
    """Verify that every generation call writes a cost log row with token counts."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        spend_ceiling_usd=10.00,
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
    )
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    client.generate("Hallo Welt, wie geht es dir?", purpose="unit_test", use_cache=False)
    assert log_file.exists()
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 1
    assert "unit_test" in lines[0]


def test_cache_checked_before_transport_and_hit_logs_zero_cost(tmp_path: Path) -> None:
    """Verify local cache hit logs cost_usd=0.0 and lane=cache."""
    log_file = tmp_path / "cost_log.jsonl"
    cache_dir = tmp_path / "cache"
    client = GeminiLlmClient(
        spend_ceiling_usd=10.00,
        cost_log_path=log_file,
        cache_dir=cache_dir,
    )
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    # First call - cache miss
    resp1 = client.generate("Erkläre mir den Dativ.", purpose="explain", use_cache=True)
    # Second identical call - cache hit
    resp2 = client.generate("Erkläre mir den Dativ.", purpose="explain", use_cache=True)

    assert resp1 == resp2
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 2
    assert '"lane":"cache"' in lines[1]
    assert '"cost_usd":0.0' in lines[1]


def test_user_content_routed_to_paid_lane_when_restriction_enabled(tmp_path: Path) -> None:
    """When privacy restriction enabled, user content routes to paid lane."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        spend_ceiling_usd=10.00,
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        restrict_user_content_to_paid_lane=True,
    )
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    client.generate(
        "User generated essay", purpose="production_grading", is_user_content=True, use_cache=False
    )
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 1
    assert '"lane":"paid"' in lines[0]


def test_rpm_429_backs_off_and_stays_on_free_lane(tmp_path: Path) -> None:
    """RPM (rate) 429s are transient: back off and retry the same free lane."""
    log_file = tmp_path / "cost_log.jsonl"
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )

    call_count = {"n": 0}

    def flaky_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise QuotaExceededError("rpm")
        return _fake_transport_ok(**kwargs)

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    response = client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert response
    assert call_count["n"] == 2, "transport must be retried once after the RPM 429"
    assert sleeps == [GeminiLlmClient.RPM_BACKOFF_SECONDS]
    assert client.free_lane_open is True
    assert client.free_lane_closed_until is None

    # Two rows, not one. This assertion used to read ``== 1`` and is
    # deliberately strengthened, not weakened: the retried attempt now writes
    # its own row. Logging only the successful attempt is the defect being
    # fixed here, so a test pinning the old count would be pinning the bug.
    rows = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2
    assert [row["outcome"] for row in rows] == ["quota", "ok"]
    assert [row["attempt"] for row in rows] == [1, 2]
    assert rows[0]["call_id"] == rows[1]["call_id"], "one retried call, not two calls"
    assert all(row["lane"] == "free" for row in rows)


def test_rpm_429_honors_google_suggested_retry_delay(tmp_path: Path) -> None:
    """The live free tier's per-minute window resets in tens of seconds, not
    one: a fixed 1s backoff retries into the same still-exhausted window and
    fails the call outright (confirmed against the real API, which reports
    ``retryDelay: "48s"`` on this quota). The backoff must honor Google's
    suggested delay, from the structured ``RetryInfo`` error detail, over the
    hardcoded fallback."""
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )

    call_count = {"n": 0}

    def flaky_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise QuotaExceededError("rpm", retry_delay_seconds=48.0)
        return _fake_transport_ok(**kwargs)

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    response = client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert response
    assert sleeps == [48.0]


def test_extract_retry_delay_seconds_reads_structured_retry_info(tmp_path: Path) -> None:
    """``retryDelay`` is read from the structured ``google.rpc.RetryInfo``
    detail, and absence of that detail (an unclassifiable or differently
    shaped 429) falls back to ``None`` rather than raising."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    with_retry_info = _client_error_429(
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", retry_delay="48s"
    )
    without_retry_info = _client_error_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")

    assert client._extract_retry_delay_seconds(with_retry_info) == 48.0
    assert client._extract_retry_delay_seconds(without_retry_info) is None


def test_rpd_429_closes_free_lane_until_pacific_midnight(tmp_path: Path) -> None:
    """RPD (daily-quota) 429s close the free lane until the next Pacific midnight
    and move the work to the paid lane. The clock is injected: no real time
    dependency."""
    log_file = tmp_path / "cost_log.jsonl"
    fixed_now = datetime(2026, 6, 15, 10, 30, tzinfo=UTC)
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        clock=lambda: fixed_now,
    )

    raised = {"once": False}

    def flaky_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        if not raised["once"]:
            raised["once"] = True
            raise QuotaExceededError("rpd")
        return _fake_transport_ok(**kwargs)

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert client.free_lane_open is False
    assert client.free_lane_closed_until is not None

    pacific = ZoneInfo("America/Los_Angeles")
    local_date = fixed_now.astimezone(pacific).date()
    expected_local_midnight = datetime.combine(
        local_date + timedelta(days=1), datetime.min.time(), tzinfo=pacific
    )
    assert client.free_lane_closed_until == expected_local_midnight.astimezone(UTC)

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"paid"' in lines[-1], "the call that hit the RPD 429 must land on the paid lane"

    # Restore the non-raising transport: a further call before the reset must not
    # even attempt the free lane again.
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]
    client.generate("Noch eine Anfrage", purpose="unit_test", use_cache=False)
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"paid"' in lines[-1]

    # After the Pacific-midnight reset, the free lane reopens.
    after_reset = client.free_lane_closed_until
    client.generate("Nach dem Reset", purpose="unit_test", use_cache=False, now=after_reset)
    assert client.free_lane_open is True
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"free"' in lines[-1]


def test_forbid_paid_lane_raises_instead_of_silently_routing_to_paid(tmp_path: Path) -> None:
    """This is the real production bug: last cycle's "sync by default, --batch
    to force it" fix only ever controlled *forcing* the paid lane -- nothing
    ever *forbade* it. Once the free lane's daily quota tripped, every
    subsequent call silently fell through to the paid batch lane (the cost
    log showed 337 of 390 lifetime API calls on the paid lane). This test
    exercises ``_determine_lane``'s real, unmocked routing decision -- the
    exact code path that was silently returning "paid" -- and proves that
    with ``forbid_paid_lane=True`` it raises instead of ever reaching the
    transport."""
    log_file = tmp_path / "cost_log.jsonl"
    fixed_now = datetime(2026, 6, 15, 10, 30, tzinfo=UTC)
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        clock=lambda: fixed_now,
        forbid_paid_lane=True,
    )
    # Simulate a free lane already closed by an earlier RPD 429 in this
    # process, exactly as ``_close_free_lane_until_pacific_midnight`` leaves
    # it -- this is state, not mocking of ``_determine_lane`` itself.
    client._close_free_lane_until_pacific_midnight(fixed_now)
    assert client.free_lane_open is False

    calls: list[str] = []

    def recording_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        calls.append(str(kwargs["lane"]))
        return _fake_transport_ok(**kwargs)

    client._call_transport = recording_transport  # type: ignore[method-assign]

    with pytest.raises(PaidLaneForbiddenError) as exc_info:
        client.generate("Anfrage nach RPD-Schluss", purpose="unit_test", use_cache=False)

    assert calls == [], "must never reach the transport at all -- no free call, no paid call"

    message = str(exc_info.value)
    assert "free lane is closed" in message
    assert "--batch" in message
    assert client.free_lane_closed_until is not None
    assert client.free_lane_closed_until.isoformat() in message, (
        "the refusal must say when the free lane reopens"
    )

    # No cost-log row was written: refusing is not the same as spending.
    assert not log_file.exists() or log_file.read_text(encoding="utf-8").strip() == ""


def test_forbid_paid_lane_raises_in_generate_many_via_determine_lane(tmp_path: Path) -> None:
    """The pilot's real call path is ``generate_many`` (via
    ``GeminiBatchClient.submit``), not ``generate`` -- confirm the same
    ``_determine_lane`` refusal applies there too, with no ``force_lane``
    override in play (``force_lane=None`` is what a non-``--batch`` pilot run
    actually passes)."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", forbid_paid_lane=True
    )
    client.free_lane_open = False  # would normally auto-route to "paid"

    def fail_if_called(**kwargs: object) -> tuple[str, TokenUsage]:
        pytest.fail("generate_many must never reach the transport when paid is forbidden")

    client._call_transport = fail_if_called  # type: ignore[method-assign]

    with pytest.raises(PaidLaneForbiddenError):
        client.generate_many(["eins"], purpose="unit_test", use_cache=False)


def test_forbid_paid_lane_raises_mid_call_on_rpd_429_instead_of_falling_through(
    tmp_path: Path,
) -> None:
    """Separate from ``_determine_lane``'s routing decision for a fresh call,
    ``_call_transport_with_lane_handling`` has its own free-to-paid fallback
    for an RPD 429 discovered mid-call. That fallback must also respect
    ``forbid_paid_lane`` -- otherwise the very first call that trips RPD
    would still slip onto paid before the client ever had a closed free lane
    to refuse on."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", forbid_paid_lane=True
    )

    def rpd_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        assert kwargs["lane"] == "free", "must not have already switched to paid"
        raise QuotaExceededError("rpd")

    client._call_transport = rpd_transport  # type: ignore[method-assign]

    with pytest.raises(PaidLaneForbiddenError):
        client.generate("Erste Anfrage", purpose="unit_test", use_cache=False)

    # The free lane is still correctly recorded as closed for the day, even
    # though the call itself refused rather than spending on paid.
    assert client.free_lane_open is False


def test_forbid_paid_lane_default_false_preserves_existing_auto_routing(tmp_path: Path) -> None:
    """``forbid_paid_lane`` defaults to ``False``: a client built without it
    keeps today's auto-routing behaviour (falls through to paid), so nothing
    that does not opt in is affected by this mode."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(cost_log_path=log_file, cache_dir=tmp_path / "cache")
    assert client.forbid_paid_lane is False

    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]
    client.free_lane_open = False

    response = client.generate("Bezahlte Anfrage", purpose="unit_test", use_cache=False)
    assert "lane=paid" in response


def test_server_overload_backs_off_and_retries_same_lane(tmp_path: Path) -> None:
    """A 5xx server-overload error (confirmed live: 503 UNAVAILABLE, 'This
    model is currently experiencing high demand') is transient and carries no
    quota signal: back off and retry the same (free) lane, exactly like RPM,
    never the RPD lane-closing path."""
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )

    call_count = {"n": 0}

    def flaky_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ServerUnavailableError("503 UNAVAILABLE")
        return _fake_transport_ok(**kwargs)

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    response = client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert response
    assert call_count["n"] == 2, "transport must be retried once after the 503"
    # The first wait is the first step of the escalating schedule, not the
    # scalar constant: that constant is now only the fallback for an attempt
    # index past the end of the schedule.
    assert sleeps == [GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE[0]]
    assert client.free_lane_open is True
    assert client.free_lane_closed_until is None


def test_server_overload_gives_up_after_max_retries(tmp_path: Path) -> None:
    """Persistent server overload must eventually propagate, not retry
    forever and hang a pilot run."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=lambda _seconds: None,
    )

    def always_overloaded(**kwargs: object) -> tuple[str, TokenUsage]:
        raise ServerUnavailableError("503 UNAVAILABLE")

    client._call_transport = always_overloaded  # type: ignore[method-assign]

    with pytest.raises(ServerUnavailableError):
        client.generate("Hallo Welt", purpose="unit_test", use_cache=False)


def test_free_lane_never_uses_batch_and_paid_lane_always_does(tmp_path: Path) -> None:
    """The free lane is always a synchronous call; the paid lane is always batch."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(cost_log_path=log_file, cache_dir=tmp_path / "cache")

    seen_modes: list[str] = []

    def recording_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        seen_modes.append(str(kwargs["mode"]))
        return _fake_transport_ok(**kwargs)

    client._call_transport = recording_transport  # type: ignore[method-assign]

    client.generate("Freie Anfrage", purpose="unit_test", use_cache=False)
    assert seen_modes[-1] == "sync"

    client.free_lane_open = False
    client.generate("Bezahlte Anfrage", purpose="unit_test", use_cache=False)
    assert seen_modes[-1] == "batch"


def test_generate_many_force_lane_paid_bypasses_auto_routing(tmp_path: Path) -> None:
    """``force_lane="paid"`` pins ``generate_many`` to the paid lane's real
    Batch API even though the free lane is open and would otherwise be
    picked -- the seam ``scripts/step5_pilot_generation.py --batch`` uses to
    deliberately opt a real stock run into the paid lane."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
        paid_api_key="fake-paid-key",
    )
    assert client.free_lane_open is True  # the default routing would pick "free"

    fake_batches = _FakeBatches(job=_fake_batch_job_many(["Antwort"]))
    fake_sdk = _FakeSdkClient(batches=fake_batches)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    responses = client.generate_many(
        ["eins"], purpose="unit_test", use_cache=False, force_lane="paid"
    )

    assert responses == ["Antwort"]
    assert len(fake_batches.create_calls) == 1

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"paid"' in lines[-1]


def test_generate_many_force_lane_free_overrides_closed_free_lane(tmp_path: Path) -> None:
    """``force_lane="free"`` still picks the free lane even after RPD closed
    it, confirming ``force_lane`` genuinely bypasses ``_determine_lane``
    rather than merely nudging its default."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", free_api_key="fake-free-key"
    )
    client.free_lane_open = False  # would normally force "paid"

    fake_models = _FakeModels(
        response=_fake_generate_content_response("Antwort", prompt_tokens=4, candidates_tokens=2)
    )
    fake_sdk = _FakeSdkClient(models=fake_models)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    responses = client.generate_many(
        ["eins"], purpose="unit_test", use_cache=False, force_lane="free"
    )

    assert responses == ["Antwort"]
    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"free"' in lines[-1]


def test_generate_many_no_force_lane_keeps_auto_routing(tmp_path: Path) -> None:
    """Omitting ``force_lane`` (the default, ``None``) keeps the normal
    auto-routed policy: free lane open picks free."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", free_api_key="fake-free-key"
    )
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    client.generate_many(["eins"], purpose="unit_test", use_cache=False)

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"free"' in lines[-1]


def test_generate_many_force_lane_paid_without_paid_key_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forcing the paid lane with no ``GEMINI_PAID_API_KEY`` configured must
    fail loudly and name the missing key, not silently fall back to free or
    to the offline mock -- the same actionable error an auto-routed paid call
    already raises."""
    # Defensive against a real key leaking into the environment from an
    # earlier test in the same session (e.g. one that calls load_env_file()),
    # exactly like test_missing_paid_api_key_raises_clear_actionable_error
    # already guards against.
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )

    with pytest.raises(MissingApiKeyError, match="paid"):
        client.generate_many(["eins"], purpose="unit_test", use_cache=False, force_lane="paid")


def test_restriction_flag_is_read_from_config_not_hardcoded(tmp_path: Path) -> None:
    """The privacy restriction flag comes from config.yaml, not a hardcoded default."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "privacy:\n  restrict_user_content_to_paid_lane: true\n", encoding="utf-8"
    )

    client_from_config = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log_true.jsonl",
        cache_dir=tmp_path / "cache_true",
        config_path=config_path,
    )
    assert client_from_config.restrict_user_content_to_paid_lane is True

    missing_config_path = tmp_path / "does_not_exist.yaml"
    client_missing_config = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log_default.jsonl",
        cache_dir=tmp_path / "cache_default",
        config_path=missing_config_path,
    )
    assert client_missing_config.restrict_user_content_to_paid_lane is False

    # An explicit constructor argument still overrides the config file.
    client_override = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log_override.jsonl",
        cache_dir=tmp_path / "cache_override",
        config_path=config_path,
        restrict_user_content_to_paid_lane=False,
    )
    assert client_override.restrict_user_content_to_paid_lane is False


def test_directories_not_created_until_first_write(tmp_path: Path) -> None:
    """Instantiating the client or its cache must not touch the filesystem yet."""
    log_file = tmp_path / "nested" / "cost_log.jsonl"
    cache_dir = tmp_path / "nested_cache" / "llm"
    client = GeminiLlmClient(cost_log_path=log_file, cache_dir=cache_dir)
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    assert not log_file.parent.exists()
    assert not cache_dir.exists()

    client.generate("Hallo Welt", purpose="unit_test", use_cache=True)

    assert log_file.parent.exists()
    assert cache_dir.exists()


# ----------------------------------------------------------------------
# Real transport: sync path, batch path, thinking config, token counts,
# 429 translation, missing-key errors. These exercise the real
# ``_call_transport`` logic by faking ``_get_sdk_client`` instead of
# ``_call_transport`` itself, so the routing/thinking/token-extraction code
# is actually under test -- never the network.
# ----------------------------------------------------------------------


def test_sync_generation_call_on_free_lane_uses_generate_content(tmp_path: Path) -> None:
    """The free lane calls ``client.models.generate_content`` once, synchronously,
    and returns the SDK response's text."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", free_api_key="fake-free-key"
    )
    fake_models = _FakeModels(
        response=_fake_generate_content_response("Guten Tag!", prompt_tokens=8, candidates_tokens=4)
    )
    fake_sdk = _FakeSdkClient(models=fake_models)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    result = client.generate("Sag Guten Tag auf Deutsch", purpose="unit_test", use_cache=False)

    assert result == "Guten Tag!"
    assert len(fake_models.calls) == 1
    assert fake_models.calls[0]["model"] == MODEL_GENERATE
    assert fake_sdk.batches.create_calls == []

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"free"' in lines[-1]


def test_batch_call_on_paid_lane_uses_batches_api(tmp_path: Path) -> None:
    """The paid lane submits an inlined batch job via ``client.batches.create``
    and returns the (already-terminal) job's response text."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", paid_api_key="fake-paid-key"
    )
    client.free_lane_open = False  # force the paid lane

    fake_batches = _FakeBatches(
        job=_fake_batch_job(
            _fake_generate_content_response("Batch Antwort", prompt_tokens=20, candidates_tokens=9)
        )
    )
    fake_sdk = _FakeSdkClient(batches=fake_batches)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    result = client.generate("Bezahlte Anfrage", purpose="unit_test", use_cache=False)

    assert result == "Batch Antwort"
    assert len(fake_batches.create_calls) == 1

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert '"lane":"paid"' in lines[-1]


def test_generate_many_returns_empty_list_for_empty_input(tmp_path: Path) -> None:
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    assert client.generate_many([], purpose="unit_test") == []


def test_sliding_window_rate_limiter_paces_bursts_instead_of_letting_them_through(
    tmp_path: Path,
) -> None:
    """Regression test for a live crash: concurrent free-lane dispatch could
    burst past the real RPM ceiling, and when several of those burst items
    then retried at roughly the same moment too, one could exhaust its
    bounded retry budget and raise -- crashing the entire ``generate_many``
    group via ``future.result()``, losing every other item's completed work.
    The limiter must block the 3rd call within a 2-call window rather than
    letting all 3 through immediately."""
    from src.llm.client import _SlidingWindowRateLimiter

    sleeps: list[float] = []
    limiter = _SlidingWindowRateLimiter(max_calls=2, period_seconds=0.05, sleep_fn=sleeps.append)

    limiter.acquire()
    limiter.acquire()
    limiter.acquire()

    assert sleeps, "the 3rd acquire within the window must have waited, not passed straight through"


def test_generate_many_free_lane_dispatches_concurrently_in_order(tmp_path: Path) -> None:
    """Every prompt is a separate free-lane call (mirroring ``generate()``'s
    existing per-item transport seam, so RPM/RPD/503 handling is unchanged),
    but they run concurrently rather than one after another, and results
    come back in the same order as the input prompts regardless of which
    finishes first."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )

    call_order: list[str] = []
    lock = threading.Lock()

    def fake_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        prompt = str(kwargs["prompt"])
        with lock:
            call_order.append(prompt)
        # Deliberately reverse-order the "slowness" so the fastest-to-finish
        # is NOT the first prompt -- a real regression here (returning
        # completion order instead of input order) would fail this test.
        if prompt == "eins":
            time.sleep(0.05)
        return (
            f"Antwort auf {prompt}",
            TokenUsage(prompt_tokens=1, completion_tokens=1),
        )

    client._call_transport = fake_transport  # type: ignore[method-assign]

    responses = client.generate_many(["eins", "zwei", "drei"], purpose="unit_test", use_cache=False)

    assert responses == ["Antwort auf eins", "Antwort auf zwei", "Antwort auf drei"]
    assert sorted(call_order) == ["drei", "eins", "zwei"]

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 3


def test_generate_many_skips_cached_prompts_and_only_calls_transport_for_misses(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    client.cache.set(model=MODEL_GENERATE, prompt="cached", response="Aus dem Cache")

    called: list[str] = []

    def fake_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        prompt = str(kwargs["prompt"])
        called.append(prompt)
        return (
            f"Antwort auf {prompt}",
            TokenUsage(prompt_tokens=1, completion_tokens=1),
        )

    client._call_transport = fake_transport  # type: ignore[method-assign]

    responses = client.generate_many(["cached", "neu"], purpose="unit_test")

    assert responses == ["Aus dem Cache", "Antwort auf neu"]
    assert called == ["neu"]


def _fake_batch_job_many(texts: list[str]) -> genai_types.BatchJob:
    """A terminal ``BatchJob`` carrying one inlined response per ``texts`` entry."""
    return genai_types.BatchJob(
        name="batches/fake-job-many",
        state=genai_types.JobState.JOB_STATE_SUCCEEDED,
        dest=genai_types.BatchJobDestination(
            inlined_responses=[
                genai_types.InlinedResponse(
                    response=_fake_generate_content_response(
                        t, prompt_tokens=5, candidates_tokens=5
                    )
                )
                for t in texts
            ]
        ),
    )


def test_generate_many_paid_lane_submits_one_job_for_the_whole_group(tmp_path: Path) -> None:
    """The whole point of this seam: N prompts on the paid lane must become
    ONE ``batches.create`` call carrying N inlined requests, not N separate
    single-item jobs -- the previous implementation's batch-scheduling
    overhead was paid once per item; this pays it once per group."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", paid_api_key="fake-paid-key"
    )
    client.free_lane_open = False  # force the paid lane

    fake_batches = _FakeBatches(job=_fake_batch_job_many(["Antwort A", "Antwort B", "Antwort C"]))
    fake_sdk = _FakeSdkClient(batches=fake_batches)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    responses = client.generate_many(["a", "b", "c"], purpose="unit_test", use_cache=False)

    assert responses == ["Antwort A", "Antwort B", "Antwort C"]
    assert len(fake_batches.create_calls) == 1, "must be exactly one batch job for the group"
    assert len(fake_batches.create_calls[0]["src"]) == 3

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 3
    assert all('"lane":"paid"' in line for line in lines)


def test_generate_many_paid_lane_threads_model_into_thinking_config(tmp_path: Path) -> None:
    """``generate_many``'s paid-lane branch builds its ``GenerateContentConfig``
    once for the whole group, so it must also pass ``model`` through to
    ``_thinking_config_for`` -- otherwise a ``gemini-3.5-flash-lite`` batch
    that fell over to the paid lane would silently lose the low-thinking
    config that the free lane grants it. ``purpose`` no longer gates this
    (``_thinking_config_for`` keys on model alone), so any purpose exercises
    the same path; ``PURPOSE_SENTENCE_GENERATION`` is used here only because
    it is a real, already-defined purpose string."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", paid_api_key="fake-paid-key"
    )
    client.free_lane_open = False  # force the paid lane

    fake_batches = _FakeBatches(job=_fake_batch_job_many(["Antwort A"]))
    fake_sdk = _FakeSdkClient(batches=fake_batches)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    client.generate_many(
        ["a"], model=MODEL_GENERATE, purpose=PURPOSE_SENTENCE_GENERATION, use_cache=False
    )

    sent_request = fake_batches.create_calls[0]["src"][0]
    assert sent_request.config.thinking_config.thinking_level == genai_types.ThinkingLevel(
        THINKING_FLASH_LITE
    )


def test_chunk_indices_for_inline_batch_respects_byte_cap(tmp_path: Path) -> None:
    """Pure unit test of the chunking algorithm: Google documents inline
    batch submission as suitable for keeping total request size under
    ~20MB (ai.google.dev/gemini-api/docs/batch-mode); this client had no
    awareness of that limit at all before -- it just dumped the whole
    prompt list into one ``src=[...]`` call regardless of size."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    prompts = ["a" * 5, "b" * 5, "c" * 5, "d" * 5]
    client.BATCH_INLINE_MAX_BYTES = 12  # fits two 5-byte prompts (10) but not three (15)

    chunks = client._chunk_indices_for_inline_batch([0, 1, 2, 3], prompts)

    assert chunks == [[0, 1], [2, 3]]


def test_generate_many_paid_lane_splits_into_multiple_jobs_when_oversized(
    tmp_path: Path,
) -> None:
    """A group whose total size exceeds ``BATCH_INLINE_MAX_BYTES`` must
    submit more than one real batch job, not silently risk an oversized
    inline request Google's own guidance warns against."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", paid_api_key="fake-paid-key"
    )
    client.free_lane_open = False  # force the paid lane
    client.BATCH_INLINE_MAX_BYTES = 1  # tiny cap: forces one item per job

    class _MultiJobFakeBatches:
        def __init__(self) -> None:
            self.create_calls: list[dict[str, object]] = []

        def create(self, *, model: str, src: list[object]) -> genai_types.BatchJob:
            self.create_calls.append({"model": model, "src": src})
            return _fake_batch_job_many([f"Antwort {len(self.create_calls)}"])

        def get(self, *, name: str) -> genai_types.BatchJob:
            raise AssertionError("polling should not be needed: fake jobs are already terminal")

    fake_batches = _MultiJobFakeBatches()
    fake_sdk = _FakeSdkClient(batches=fake_batches)  # type: ignore[arg-type]
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    responses = client.generate_many(["a", "bb", "ccc"], purpose="unit_test", use_cache=False)

    assert len(fake_batches.create_calls) == 3, "tiny byte cap must force one job per item"
    assert responses == ["Antwort 1", "Antwort 2", "Antwort 3"]


def test_generate_many_raises_budget_exceeded_before_any_call(tmp_path: Path) -> None:
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        spend_ceiling_usd=0.0,
    )

    def must_not_be_called(**kwargs: object) -> tuple[str, TokenUsage]:
        raise AssertionError("transport must never be called once the ceiling is reached")

    client._call_transport = must_not_be_called  # type: ignore[method-assign]

    with pytest.raises(BudgetExceeded):
        client.generate_many(["a", "b"], purpose="unit_test")


def test_paid_lane_non_batch_call_is_rejected_as_a_defect(tmp_path: Path) -> None:
    """Pinning UPDATED (was unconditional; now explicitly conditional on
    ``forbid_batch``, per the project owner's "no batch api ... for pilot
    go to paid on demand api" instruction, which requires a paid-lane sync
    call to be legitimate under ``forbid_batch=True`` -- see
    ``test_paid_lane_sync_call_permitted_under_forbid_batch`` below for that
    case). Without ``forbid_batch`` (the default, exercised here), a
    non-batch call on the paid lane remains a defect, not something
    ``_call_transport`` silently coerces into batch mode."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
    )
    assert client.forbid_batch is False

    with pytest.raises(ValueError, match="batch-only"):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="paid", mode="sync", purpose="unit_test"
        )


def test_free_lane_batch_mode_call_is_also_rejected_as_a_defect(tmp_path: Path) -> None:
    """Symmetrically, the free lane is always synchronous."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )

    with pytest.raises(ValueError, match="synchronous"):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="free", mode="batch", purpose="unit_test"
        )


def test_paid_lane_sync_call_permitted_under_forbid_batch(tmp_path: Path) -> None:
    """The owner's actual instruction: 'no batch api ... for pilot go to
    paid on demand api, if free lane is already expired.' A paid-lane
    synchronous call is legitimate, not a defect, when the client was built
    with ``forbid_batch=True`` -- ``_call_transport`` must reach the real
    sync transport instead of raising."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
        forbid_batch=True,
    )

    fake_models = _FakeModels(
        response=_fake_generate_content_response("Antwort", prompt_tokens=4, candidates_tokens=2)
    )
    fake_sdk = _FakeSdkClient(models=fake_models)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    text, usage = client._call_transport(
        model=MODEL_GENERATE, prompt="x", lane="paid", mode="sync", purpose="unit_test"
    )

    assert text == "Antwort"
    assert (usage.prompt_tokens, usage.completion_tokens) == (4, 2)
    assert len(fake_models.calls) == 1, "must have reached the real sync transport, not batch"


def test_paid_lane_batch_call_raises_batch_forbidden_under_forbid_batch(tmp_path: Path) -> None:
    """The mirror image: with ``forbid_batch=True``, a paid-lane call that
    requests batch mode must raise ``BatchForbiddenError`` -- distinct from
    the ``ValueError`` a non-batch paid-lane call raises without
    ``forbid_batch`` -- and say the run is on-demand only and that batch
    remains available for nightly/initial generation."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
        forbid_batch=True,
    )

    with pytest.raises(BatchForbiddenError) as exc_info:
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="paid", mode="batch", purpose="unit_test"
        )

    message = str(exc_info.value)
    assert "on-demand" in message or "on demand" in message
    assert "nightly" in message.lower()


def test_generate_many_rpd_mid_call_fallback_lands_on_paid_sync_under_forbid_batch(
    tmp_path: Path,
) -> None:
    """The mid-call RPD fallback in ``_call_transport_with_lane_handling``
    is the second seam that decides paid-lane mode (the first is the
    up-front lane decision, exercised by
    ``test_paid_lane_sync_call_permitted_under_forbid_batch`` via
    ``_call_transport`` directly). Both must resolve to sync when
    ``forbid_batch`` is set: an RPD 429 discovered mid-call on the free
    lane must fall through to a real, synchronous paid-lane call, never a
    batch submission, and never a ``BatchForbiddenError`` (only an actual
    attempted batch call raises that)."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        forbid_batch=True,
    )

    call_count = {"n": 0}
    seen: list[tuple[str, str]] = []

    def fallback_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        lane = str(kwargs["lane"])
        mode = str(kwargs["mode"])
        seen.append((lane, mode))
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert lane == "free"
            raise QuotaExceededError("rpd")
        return _fake_transport_ok(**kwargs)

    client._call_transport = fallback_transport  # type: ignore[method-assign]

    response = client.generate("Anfrage nach RPD", purpose="unit_test", use_cache=False)

    assert "lane=paid" in response
    assert "mode=sync" in response
    assert seen == [("free", "sync"), ("paid", "sync")]
    assert client.free_lane_open is False


def test_generate_many_paid_lane_dispatches_sync_per_item_under_forbid_batch(
    tmp_path: Path,
) -> None:
    """``generate_many``'s own paid-lane branch must also resolve to sync,
    per-item dispatch (not the batch-job grouping machinery) when
    ``forbid_batch`` is set -- the third seam the brief calls out
    ("generate_many picks its mode")."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
        forbid_batch=True,
    )
    client.free_lane_open = False  # force the paid lane

    fake_models = _FakeModels(
        response=_fake_generate_content_response("Antwort", prompt_tokens=4, candidates_tokens=2)
    )
    fake_batches = _FakeBatches()
    fake_sdk = _FakeSdkClient(models=fake_models, batches=fake_batches)
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    responses = client.generate_many(["eins", "zwei"], purpose="unit_test", use_cache=False)

    assert responses == ["Antwort", "Antwort"]
    assert len(fake_models.calls) == 2, "must have reached the sync transport, per item"
    assert len(fake_batches.create_calls) == 0, "must never have queued a real batch job"

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 2
    assert all('"lane":"paid"' in line for line in lines)


def test_real_token_counts_from_usage_metadata_flow_into_cost_log(tmp_path: Path) -> None:
    """The cost log must carry the SDK's real ``usage_metadata`` token counts,
    not a ``len(prompt.split())`` heuristic -- the two disagree for this prompt,
    which is exactly the point."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", free_api_key="fake-free-key"
    )
    fake_sdk = _FakeSdkClient(
        models=_FakeModels(
            response=_fake_generate_content_response(
                "kurz", prompt_tokens=123, candidates_tokens=45
            )
        )
    )
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    client.generate(
        "Ein ganz kurzer Prompt", purpose="unit_test", use_cache=False
    )  # word-count heuristic would give a very different number

    row = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert row["prompt_tokens"] == 123
    assert row["completion_tokens"] == 45


def test_thoughts_token_count_added_to_completion_tokens_when_present(tmp_path: Path) -> None:
    """Thinking tokens bill as output (CLAUDE.md 213): when usage_metadata carries
    ``thoughts_token_count``, it must be folded into ``completion_tokens``."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file, cache_dir=tmp_path / "cache", free_api_key="fake-free-key"
    )
    fake_sdk = _FakeSdkClient(
        models=_FakeModels(
            response=_fake_generate_content_response(
                "Antwort", prompt_tokens=10, candidates_tokens=5, thoughts_tokens=30
            )
        )
    )
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    client.generate("Verifiziere dies", model=MODEL_VERIFY, purpose="verify", use_cache=False)

    row = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert row["completion_tokens"] == 35  # 5 candidate tokens + 30 thought tokens


def test_thinking_low_for_generation_model_regardless_of_purpose(tmp_path: Path) -> None:
    """The project owner's instruction ("set thinking level to low for all
    gemini 3.5 flash lite actions") means ``_thinking_config_for`` keys on
    ``model`` alone, not on ``purpose``. This used to be the opposite: thinking
    was gated to the single sentence-generation purpose specifically to keep
    explanations, production grading, minimal-pair generation, and the weekly
    report narrative thinking-OFF, since ``MODEL_LIVE`` and ``MODEL_GENERATE``
    are literally the same model string. The owner has since asked for the
    reverse, so an arbitrary, non-sentence-generation purpose (standing in for
    any of those other four call sites) must now also get
    ``THINKING_FLASH_LITE``, not ``None``.
    """
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    fake_models = _FakeModels(
        response=_fake_generate_content_response("ok", prompt_tokens=1, candidates_tokens=1)
    )
    client._get_sdk_client = lambda lane: _FakeSdkClient(models=fake_models)  # type: ignore[method-assign]

    client.generate("Prompt", model=MODEL_GENERATE, purpose="unit_test", use_cache=False)

    sent_config = fake_models.calls[0]["config"]
    assert sent_config.thinking_config.thinking_level == genai_types.ThinkingLevel(
        THINKING_FLASH_LITE
    )
    assert sent_config.thinking_config.thinking_budget is None


def test_thinking_low_for_sentence_generation_purpose(tmp_path: Path) -> None:
    """``purpose=PURPOSE_SENTENCE_GENERATION`` (what
    ``LiveSentenceGenerator.generate`` sends, in
    ``src.generation.blanking.sentence_source``) runs at
    ``THINKING_FLASH_LITE`` ("low"), added after an accepted carrier sentence
    turned out to carry an A1 subject-verb agreement error the model could
    not have fixed without any planning step at all. "low", not "minimal",
    because the project owner confirmed "minimal" is this model line's own
    default thinking level -- setting it explicitly bought nothing over
    leaving thinking unset. This purpose is no longer special-cased (see
    ``test_thinking_low_for_generation_model_regardless_of_purpose`` above),
    but is kept as its own test since it is the purpose that originally
    motivated turning thinking on for this model at all."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    fake_models = _FakeModels(
        response=_fake_generate_content_response("ok", prompt_tokens=1, candidates_tokens=1)
    )
    client._get_sdk_client = lambda lane: _FakeSdkClient(models=fake_models)  # type: ignore[method-assign]

    client.generate(
        "Prompt", model=MODEL_GENERATE, purpose=PURPOSE_SENTENCE_GENERATION, use_cache=False
    )

    sent_config = fake_models.calls[0]["config"]
    assert sent_config.thinking_config.thinking_level == genai_types.ThinkingLevel(
        THINKING_FLASH_LITE
    )
    assert sent_config.thinking_config.thinking_budget is None


def test_thinking_enabled_at_configured_level_for_verify_model(tmp_path: Path) -> None:
    """Only the verify model (``MODEL_VERIFY``) uses thinking, at ``THINKING_VERIFY``."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    fake_models = _FakeModels(
        response=_fake_generate_content_response("ok", prompt_tokens=1, candidates_tokens=1)
    )
    client._get_sdk_client = lambda lane: _FakeSdkClient(models=fake_models)  # type: ignore[method-assign]

    client.generate("Verifiziere", model=MODEL_VERIFY, purpose="verify", use_cache=False)

    sent_config = fake_models.calls[0]["config"]
    assert sent_config.thinking_config.thinking_level == genai_types.ThinkingLevel(THINKING_VERIFY)
    assert sent_config.thinking_config.thinking_budget is None


def test_missing_free_api_key_raises_clear_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key configured for the lane in use must fail loudly and specifically,
    not with a generic auth error from deep inside the SDK."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    with pytest.raises(MissingApiKeyError, match="free lane"):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="free", mode="sync", purpose="unit_test"
        )


def test_missing_paid_api_key_raises_clear_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    with pytest.raises(MissingApiKeyError, match="paid lane"):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="paid", mode="batch", purpose="unit_test"
        )


def test_classify_quota_error_reads_structured_quota_violation(tmp_path: Path) -> None:
    """RPM vs RPD is read from the structured ``quotaId`` in the error body,
    not guessed from free text."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    rpd_error = _client_error_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    rpm_error = _client_error_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")

    assert client._classify_quota_error(rpd_error) == "rpd"
    assert client._classify_quota_error(rpm_error) == "rpm"


def test_sync_call_translates_429_into_quota_exceeded_error(tmp_path: Path) -> None:
    """A structured 429 from ``generate_content`` becomes a ``QuotaExceededError``
    with the right ``quota_type``, not an uncaught SDK exception."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    error = _client_error_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
    fake_sdk = _FakeSdkClient(models=_FakeModels(error=error))
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    with pytest.raises(QuotaExceededError) as excinfo:
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="free", mode="sync", purpose="unit_test"
        )
    assert excinfo.value.quota_type == "rpm"


def test_sync_call_translates_503_into_server_unavailable_error(tmp_path: Path) -> None:
    """A 5xx ``ServerError`` from ``generate_content`` becomes a
    ``ServerUnavailableError``, not an uncaught SDK exception -- confirmed
    live: this is a distinct exception hierarchy from the 4xx ``ClientError``
    branch above, so it needs its own translation, not just a wider ``except``."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    error = genai_errors.ServerError(
        503, {"error": {"code": 503, "message": "UNAVAILABLE", "status": "UNAVAILABLE"}}, None
    )
    fake_sdk = _FakeSdkClient(models=_FakeModels(error=error))
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    with pytest.raises(ServerUnavailableError):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="free", mode="sync", purpose="unit_test"
        )


def test_sync_call_wraps_non_429_client_error_naming_the_model(tmp_path: Path) -> None:
    """A rejected model id must fail loudly and name the model, not vanish
    into a generic SDK exception or (worse) get silently swallowed."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
    )
    bad_model_error = genai_errors.ClientError(
        404,
        {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}},
        None,
    )
    fake_sdk = _FakeSdkClient(models=_FakeModels(error=bad_model_error))
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    with pytest.raises(ModelRejectedError, match=MODEL_GENERATE):
        client._call_transport(
            model=MODEL_GENERATE, prompt="x", lane="free", mode="sync", purpose="unit_test"
        )


def test_operator_tuned_rate_limit_constants_are_pinned() -> None:
    """``RPM_MAX_RETRIES``, ``FREE_LANE_MAX_CONCURRENCY`` and
    ``FREE_LANE_RATE_LIMIT_PER_MINUTE`` are operator-tuned against the
    project owner's own observed live Gemini rate limiting, and the values
    below are deliberately conservative -- not the theoretical or documented
    ceiling, but what the owner has actually seen work without 429s. They
    have been silently reverted to weaker values (2 / 8 / 14 respectively)
    across three separate cycles, each time costing the owner a live pilot
    run; one cycle even noticed the discrepancy, reported it, and moved on
    anyway. This test exists so that cannot happen quietly again: if it
    fails, that is the signal to go ask the project owner before changing
    anything here, per CLAUDE.md rule 7 (never weaken a failing test to make
    it pass) -- not to update this assertion to match whatever the code now
    says."""
    assert GeminiLlmClient.RPM_MAX_RETRIES == 5
    assert GeminiLlmClient.FREE_LANE_MAX_CONCURRENCY == 4
    assert GeminiLlmClient.FREE_LANE_RATE_LIMIT_PER_MINUTE == 5


def test_operator_tuned_server_error_constants_are_pinned() -> None:
    """The 5xx retry constants are the project owner's own edit, and this test
    pins whatever he last set. Same standing rule as the rate-limit constants
    above: if it fails, ask the owner rather than updating the assertion, per
    CLAUDE.md rule 7 and TODO.md section 3.

    The values below are NOT the ones this test used to assert (15.0 seconds,
    5 retries), and the change is deliberate rather than drift. History, so
    nobody "restores" the old numbers: the owner first raised these by hand
    after the cycle 9 pilot died on a Gemini 503, then raised the retry count
    again to 40 and added a free-to-paid fallback once those retries are
    exhausted. He has since replaced that with the opposite shape, verbatim:
    "instead of making 40 request make it like 4 but with much longer
    intervals". 40 attempts 15 seconds apart is ten minutes of hammering a
    service that is down; four attempts on the escalating schedule below
    cover roughly 25 minutes and then hand the work to the paid lane. The
    batch cap is lower still, and that one is not the owner's instruction but
    this cycle's judgement -- a failed batch job has already been billed for
    the requests it processed before it failed, and a retry resubmits the
    whole job."""
    assert GeminiLlmClient.SERVER_ERROR_MAX_RETRIES == 4
    assert GeminiLlmClient.SERVER_ERROR_BATCH_MAX_RETRIES == 2
    assert GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE == (30.0, 120.0, 480.0, 900.0)
    # Only the fallback for an attempt index past the end of the schedule.
    assert GeminiLlmClient.SERVER_ERROR_BACKOFF_SECONDS == 900.0


# ---------------------------------------------------------------------------
# Pricing by transport mode, checked against the real Google bill.
#
# The reference data below is three days of the owner's own August 2026 usage
# where his cost log and Google's per-day billing export agree token for
# token, so the priced result can be compared against what Google actually
# charged rather than against this code's own arithmetic.
# ---------------------------------------------------------------------------

#: ``(day, prompt_tokens, completion_tokens, charged_input_usd, charged_output_usd)``.
#: All on ``gemini-3.7-flash``, all non-batch (the pilots run
#: ``forbid_batch=True``), at $0.75 input and $3.75 output per million.
_RECONCILED_BILLING_DAYS: list[tuple[str, int, int, float, float]] = [
    ("2026-08-19", 23_746, 53_668, 0.017809, 0.201255),
    ("2026-08-25", 95_101, 140_004, 0.071324, 0.525013),
    ("2026-08-26", 381_284, 263_122, 0.285960, 0.986703),
]

#: Google's export aggregates per-request charges, each rounded on its own,
#: so re-pricing a whole day from its summed tokens cannot land on the exact
#: cent Google printed. The observed gap across these three days is at most
#: 8e-6 USD, i.e. under a hundredth of a cent on a $1.27 day; the half-price
#: bug this test guards against is off by half the bill, roughly 1e5 times
#: larger, so the tolerance costs the test nothing.
_BILLING_TOLERANCE_USD = 1e-5


def test_sync_pricing_reproduces_the_real_google_charges_for_three_reconciled_days(
    tmp_path: Path,
) -> None:
    """Price the three days whose logged tokens match Google's billing export
    exactly, and land on the charges Google actually raised.

    This is the regression test for the measured billing miss: the owner's
    August bill was $5.04 against a cost log claiming $1.99. ``_estimate_cost``
    applied Google's 0.5x batch discount to EVERY paid call, including the
    synchronous on-demand calls a ``forbid_batch=True`` pilot makes, which are
    billed at full price. Each of these three days would come out at exactly
    half the real charge under that bug.
    """
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    for day, prompt_tokens, completion_tokens, charged_in, charged_out in _RECONCILED_BILLING_DAYS:
        priced = client._estimate_cost(
            MODEL_VERIFY, prompt_tokens, completion_tokens, "paid", "sync"
        )
        charged = charged_in + charged_out
        assert abs(priced - charged) <= _BILLING_TOLERANCE_USD, (
            f"{day}: priced ${priced:.6f} against a real Google charge of ${charged:.6f}"
        )

        # The two sides of the bill, separately, so a compensating error in
        # one price cannot hide inside the total.
        priced_input = client._estimate_cost(MODEL_VERIFY, prompt_tokens, 0, "paid", "sync")
        priced_output = client._estimate_cost(MODEL_VERIFY, 0, completion_tokens, "paid", "sync")
        assert abs(priced_input - charged_in) <= _BILLING_TOLERANCE_USD, day
        assert abs(priced_output - charged_out) <= _BILLING_TOLERANCE_USD, day

        # And the bug itself, named: half price is not within a mile of the bill.
        assert abs(priced / 2 - charged) > _BILLING_TOLERANCE_USD, day


def test_batch_mode_gets_the_discount_and_sync_mode_does_not(tmp_path: Path) -> None:
    """Google's 0.5x discount is a property of the transport mode, not the
    lane: a real Batch API submission is half price, a paid-lane synchronous
    on-demand call is not. The lane is the same in both cases, which is why
    ``lane`` alone could never have fixed this."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    sync_cost = client._estimate_cost(MODEL_VERIFY, 1_000_000, 1_000_000, "paid", "sync")
    batch_cost = client._estimate_cost(MODEL_VERIFY, 1_000_000, 1_000_000, "paid", "batch")

    assert sync_cost == pytest.approx(0.75 + 3.75)
    assert batch_cost == pytest.approx(sync_cost / 2)


def test_free_and_cache_lanes_stay_zero_cost_in_every_mode(tmp_path: Path) -> None:
    """The free lane is an unbilled project and a cache hit reaches no
    provider at all, so both stay at zero cost whatever mode is passed.
    Pricing by mode must not have quietly started charging for them."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )

    for lane in ("free", "cache"):
        for mode in ("sync", "batch", "cache"):
            assert client._estimate_cost(MODEL_VERIFY, 500_000, 500_000, lane, mode) == 0.0


def test_cost_log_records_the_mode_each_call_actually_used(tmp_path: Path) -> None:
    """Every row says how the call reached Google, because Google's bill has
    separate SKUs for ``gemini 3.7 flash text`` and ``... text batch`` at
    different rates. Without this field the log cannot be reconciled against
    the bill line for line, which is what made the August miss take two days
    and a billing export to find."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
        paid_api_key="fake-paid-key",
        forbid_batch=True,
    )
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]

    # Free lane, synchronous.
    client.generate("Freie Anfrage", purpose="unit_test", use_cache=True)
    # The same prompt again: a cache hit, which reached no transport at all.
    client.generate("Freie Anfrage", purpose="unit_test", use_cache=True)
    # Paid lane under forbid_batch: on-demand, and therefore full price.
    client.free_lane_open = False
    client.generate("Bezahlte Anfrage", purpose="unit_test", use_cache=False)

    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    assert [(row["lane"], row["mode"]) for row in rows] == [
        ("free", "sync"),
        ("cache", "cache"),
        ("paid", "sync"),
    ]


def test_generate_many_batch_rows_are_logged_as_batch_and_discounted(tmp_path: Path) -> None:
    """``generate_many``'s paid-lane batch branch is the one place a real
    Batch API submission happens, so it is the one place the discount is
    correct. The row records ``mode="batch"`` and the cost is half the
    on-demand price for the same tokens."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
    )
    fake_sdk = _FakeSdkClient(batches=_FakeBatches(_fake_batch_job_many(["Antwort"])))
    client._get_sdk_client = lambda lane: fake_sdk  # type: ignore[method-assign]

    client.generate_many(
        ["Ein Prompt"],
        purpose="unit_test",
        use_cache=False,
        force_lane="paid",
        now=datetime.now(UTC),
    )

    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["lane"] == "paid"
    assert rows[0]["mode"] == "batch"
    on_demand = client._estimate_cost(
        rows[0]["model"], rows[0]["prompt_tokens"], rows[0]["completion_tokens"], "paid", "sync"
    )
    assert on_demand > 0.0, "the fixture must bill something for the halving to mean anything"
    assert rows[0]["cost_usd"] == pytest.approx(on_demand / 2)


# ---------------------------------------------------------------------------
# Usage metadata: every billed field, and the checksum over them.
# ---------------------------------------------------------------------------


def test_extract_response_records_every_billed_token_field(tmp_path: Path) -> None:
    """Gemini reports four separately billed token counts and its own total.
    The client used to read two of them and merge one away. All of them are
    now carried through to the cost-log row, with ``completion_tokens``
    unchanged in meaning (candidates plus thoughts) so nothing downstream
    shifts."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    response = _fake_generate_content_response(
        "Antwort",
        prompt_tokens=100,
        candidates_tokens=40,
        thoughts_tokens=25,
        cached_content_tokens=60,
        tool_use_prompt_tokens=7,
        total_tokens=172,  # 100 prompt (60 of it cached) + 40 + 25 + 7
        model_version="gemini-3.7-flash-002",
    )

    text, usage = client._extract_response(response, prompt="egal", purpose="unit_test")

    assert text == "Antwort"
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 65, "candidates plus thoughts, exactly as before"
    assert usage.thoughts_tokens == 25
    assert usage.cached_content_tokens == 60
    assert usage.tool_use_prompt_tokens == 7
    assert usage.total_tokens == 172
    assert usage.model_version == "gemini-3.7-flash-002"


def test_token_checksum_warns_and_does_not_raise_when_the_parts_do_not_sum(
    tmp_path: Path,
) -> None:
    """When Gemini's own total exceeds the fields this client reads, it is
    billing for tokens the log cannot see -- precisely the failure mode that
    hid roughly $1.58 of the August bill. It must warn, name the discrepancy,
    and keep going: this runs inside a six-week unattended job, so crashing
    it over a bookkeeping gap would cost far more than the gap."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    response = _fake_generate_content_response(
        "Antwort",
        prompt_tokens=100,
        candidates_tokens=40,
        thoughts_tokens=10,
        total_tokens=200,  # 50 tokens this client cannot account for
    )

    with pytest.warns(UserWarning, match="does not add up"):
        _text, usage = client._extract_response(response, prompt="egal", purpose="unit_test")

    assert usage.total_tokens == 200, (
        "the provider's own total must be recorded so a reconciliation can see the gap"
    )
    assert usage.prompt_tokens + usage.completion_tokens == 150


def test_token_checksum_is_skipped_when_the_provider_reports_no_total(tmp_path: Path) -> None:
    """An absent ``total_token_count`` is not a total of zero. Treating it as
    zero would make every such response look like a mismatch and bury the real
    ones in noise."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    response = _fake_generate_content_response(
        "Antwort", prompt_tokens=100, candidates_tokens=40, total_tokens=None
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _text, usage = client._extract_response(response, prompt="egal", purpose="unit_test")

    assert usage.total_tokens is None


def test_cost_log_row_written_before_the_new_fields_existed_still_loads(tmp_path: Path) -> None:
    """The new columns are additive with defaults. ``_load_cost_log`` reads
    this file at construction and ``get_month_to_date_spend`` sums it, so a
    historical row that predates them must still parse silently -- a warning
    per row would make every existing log look corrupt."""
    log_file = tmp_path / "cost_log.jsonl"
    old_row = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": MODEL_GENERATE,
        "lane": "paid",
        "prompt_tokens": 1000,
        "completion_tokens": 2000,
        "cost_usd": 0.5,
        "purpose": "generation",
    }
    log_file.write_text(json.dumps(old_row) + "\n", encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        client = GeminiLlmClient(cost_log_path=log_file, cache_dir=tmp_path / "cache")

    assert client.get_month_to_date_spend() == 0.5
    (row,) = client.cost_records
    assert row.mode is None, "an old row's mode is genuinely unknown, not a guess"
    assert row.thoughts_tokens == 0
    assert row.cached_content_tokens == 0
    assert row.tool_use_prompt_tokens == 0
    assert row.total_tokens is None
    assert row.model_version is None
    # A row written before ``outcome`` existed was, by construction, the
    # successful attempt: a success was the only thing that got logged at
    # all. Defaulting it to "ok" on attempt 1 states exactly that, and no
    # more -- ``call_id`` stays None because no such grouping existed.
    assert row.outcome == "ok"
    assert row.attempt == 1
    assert row.call_id is None


# ---------------------------------------------------------------------------
# 5xx retry schedule, its caps, and the owner's free-to-paid fallback.
# ---------------------------------------------------------------------------


def test_server_error_backoff_follows_the_schedule_in_order_and_stops_after_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four attempts on an escalating schedule, not forty flat ones: the
    owner's instruction, "instead of making 40 request make it like 4 but
    with much longer intervals". No paid key is configured here, so the
    free-to-paid fallback cannot fire and the error propagates once the
    schedule is spent.

    The paid key is explicitly removed from the environment rather than
    merely not passed: another test in the same session may have called
    ``load_env_file()``, which writes a real ``.env`` into ``os.environ``
    for the rest of the run."""
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )
    calls = {"n": 0}

    def always_overloaded(**kwargs: object) -> tuple[str, TokenUsage]:
        calls["n"] += 1
        raise ServerUnavailableError("503 UNAVAILABLE")

    client._call_transport = always_overloaded  # type: ignore[method-assign]

    with pytest.raises(ServerUnavailableError):
        client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert sleeps == list(GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE)
    assert calls["n"] == GeminiLlmClient.SERVER_ERROR_MAX_RETRIES + 1


def test_server_error_backoff_falls_back_to_the_scalar_past_the_schedule(
    tmp_path: Path,
) -> None:
    """The scalar constant is the fallback for an attempt index past the end
    of the schedule, so raising a retry cap can never index off it."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl", cache_dir=tmp_path / "cache"
    )
    schedule = GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE

    assert [client._server_error_backoff_seconds(i) for i in range(len(schedule))] == list(schedule)
    assert client._server_error_backoff_seconds(len(schedule)) == (
        GeminiLlmClient.SERVER_ERROR_BACKOFF_SECONDS
    )


def test_batch_server_error_retries_stop_after_two(tmp_path: Path) -> None:
    """The batch path gets a lower cap than the sync path because a batch
    retry is not free: the job that failed had already run for minutes and
    Google had already billed the requests it processed, and this resubmits
    the whole group."""
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=sleeps.append,
    )
    calls = {"n": 0}

    def always_overloaded(*args: object, **kwargs: object) -> list[tuple[str, TokenUsage]]:
        calls["n"] += 1
        raise ServerUnavailableError("503 UNAVAILABLE")

    client._call_batch_many = always_overloaded  # type: ignore[method-assign]

    with pytest.raises(ServerUnavailableError):
        client._call_batch_many_with_retry(
            None,  # type: ignore[arg-type]  -- never reached, the double raises first
            model=MODEL_GENERATE,
            prompts=["eins", "zwei"],
            config=genai_types.GenerateContentConfig(),
            purpose="unit_test",
            ref_time=datetime.now(UTC),
        )

    assert calls["n"] == GeminiLlmClient.SERVER_ERROR_BATCH_MAX_RETRIES + 1
    assert calls["n"] < GeminiLlmClient.SERVER_ERROR_MAX_RETRIES + 1, (
        "the batch cap must stay strictly below the sync cap"
    )
    assert sleeps == list(GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE[:2])


def test_free_to_paid_fallback_fires_once_server_retries_are_exhausted(tmp_path: Path) -> None:
    """The owner's own fallback, kept: when the free project stays down past
    the whole retry schedule, the work moves to the paid project rather than
    being lost. The paid lane is a separate Google Cloud project, so its
    availability is genuinely independent of the free one's."""
    log_file = tmp_path / "cost_log.jsonl"
    sleeps: list[float] = []
    client = GeminiLlmClient(
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
        free_api_key="fake-free-key",
        paid_api_key="fake-paid-key",
        sleep_fn=sleeps.append,
    )
    seen_lanes: list[str] = []

    def free_lane_is_down(**kwargs: object) -> tuple[str, TokenUsage]:
        lane = str(kwargs["lane"])
        seen_lanes.append(lane)
        if lane == "free":
            raise ServerUnavailableError("503 UNAVAILABLE")
        return _fake_transport_ok(**kwargs)

    client._call_transport = free_lane_is_down  # type: ignore[method-assign]

    response = client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert "lane=paid" in response
    assert seen_lanes == ["free"] * (GeminiLlmClient.SERVER_ERROR_MAX_RETRIES + 1) + ["paid"]
    assert sleeps == list(GeminiLlmClient.SERVER_ERROR_BACKOFF_SCHEDULE)
    # A 503 is not a quota signal, so the free lane stays open for later calls.
    assert client.free_lane_open is True
    rows = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    assert rows[-1]["lane"] == "paid"


def test_free_to_paid_fallback_is_not_taken_when_the_paid_lane_is_forbidden(
    tmp_path: Path,
) -> None:
    """``forbid_paid_lane`` means no paid spend at all, and an outage is not
    an exception to that: a learner-facing call must surface the real 503
    rather than quietly spend on the paid project."""
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        paid_api_key="fake-paid-key",
        forbid_paid_lane=True,
        sleep_fn=lambda _seconds: None,
    )
    seen_lanes: list[str] = []

    def always_overloaded(**kwargs: object) -> tuple[str, TokenUsage]:
        seen_lanes.append(str(kwargs["lane"]))
        raise ServerUnavailableError("503 UNAVAILABLE")

    client._call_transport = always_overloaded  # type: ignore[method-assign]

    with pytest.raises(ServerUnavailableError):
        client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert set(seen_lanes) == {"free"}, "must never have touched the paid lane"


# ---------------------------------------------------------------------------
# One row per attempt, not one row per success.
#
# The measurement: the owner's August Gemini bill was $5.04 against a cost log
# claiming $1.99. Roughly $1.90 of that was the batch discount applied to
# non-batch calls (fixed separately). Roughly $1.58 is attempts that billed
# and were never logged, because only the attempt that finally succeeded ever
# wrote a row. These tests pin the fix.
# ---------------------------------------------------------------------------


def test_every_sync_attempt_writes_a_row_and_only_the_success_carries_tokens(
    tmp_path: Path,
) -> None:
    """A retried sync call leaves one row per attempt, sharing one call id.

    Only the attempt that succeeded carries tokens and cost. The failed ones
    carry zeros with a truthful outcome tag: the exception has no usage
    metadata, so what Google billed for those attempts is genuinely unknown,
    and a guess in an audit log is what produced this whole problem.
    """
    client = GeminiLlmClient(
        paid_api_key="paid-key",
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        restrict_user_content_to_paid_lane=True,
        forbid_batch=True,
        sleep_fn=lambda _seconds: None,
    )

    calls = {"n": 0}

    def flaky_transport(**kwargs: object) -> tuple[str, TokenUsage]:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ServerUnavailableError("503 UNAVAILABLE")
        return "gut", TokenUsage(prompt_tokens=1000, completion_tokens=2000)

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    client.generate("Hallo Welt", purpose="unit_test", use_cache=False, is_user_content=True)

    rows = client.cost_records
    assert [row.outcome for row in rows] == ["server_error", "server_error", "ok"]
    assert [row.attempt for row in rows] == [1, 2, 3]
    assert len({row.call_id for row in rows}) == 1, "one retried call, not three calls"
    assert all(row.lane == "paid" and row.mode == "sync" for row in rows)

    failed, succeeded = rows[:2], rows[2]
    assert all(row.prompt_tokens == 0 and row.completion_tokens == 0 for row in failed)
    assert all(row.cost_usd == 0.0 for row in failed), (
        "a failed attempt must not be priced at an invented figure; this change "
        "makes the gap visible, it does not close it"
    )
    assert (succeeded.prompt_tokens, succeeded.completion_tokens) == (1000, 2000)
    assert succeeded.cost_usd > 0.0


def test_every_batch_attempt_writes_a_row_and_only_the_success_carries_tokens(
    tmp_path: Path,
) -> None:
    """The batch path does the same, at job granularity.

    One row per failed JOB, not per prompt: the job is what was submitted and
    what gets resubmitted, and a job that died partway does not say how many
    of its prompts it reached. The successful rows carry the job's own attempt
    index and call id, so the whole group reads as one retried submission.
    """
    client = GeminiLlmClient(
        paid_api_key="paid-key",
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=lambda _seconds: None,
    )
    client._get_sdk_client = lambda lane: None  # type: ignore[assignment,return-value]

    calls = {"n": 0}

    def flaky_batch(*args: object, **kwargs: object) -> list[tuple[str, TokenUsage]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ServerUnavailableError("503 UNAVAILABLE")
        prompts = list(kwargs["prompts"])  # type: ignore[call-overload]
        return [
            (f"antwort {p}", TokenUsage(prompt_tokens=100, completion_tokens=200)) for p in prompts
        ]

    client._call_batch_many = flaky_batch  # type: ignore[method-assign]

    client.generate_many(["eins", "zwei"], purpose="unit_test", use_cache=False, force_lane="paid")

    rows = client.cost_records
    assert [row.outcome for row in rows] == ["server_error", "ok", "ok"]
    assert [row.attempt for row in rows] == [1, 2, 2]
    assert len({row.call_id for row in rows}) == 1, "one retried job, not three calls"
    assert all(row.lane == "paid" and row.mode == "batch" for row in rows)
    assert (rows[0].prompt_tokens, rows[0].completion_tokens, rows[0].cost_usd) == (0, 0, 0.0)
    assert all(row.prompt_tokens == 100 and row.cost_usd > 0.0 for row in rows[1:])


def test_failed_batch_job_that_is_never_retried_still_writes_a_row(tmp_path: Path) -> None:
    """A batch job that Google accepts, runs, and returns as FAILED raises a
    plain ``RuntimeError`` out of ``_call_batch_many`` and is not retried at
    all. Google has already billed whatever the job processed before it died,
    so this is the exact shape of the owner's Aug 14-17 days: real batch
    charges against days with zero rows in the cost log. It must leave a
    row."""
    client = GeminiLlmClient(
        paid_api_key="paid-key",
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        sleep_fn=lambda _seconds: None,
    )
    client._get_sdk_client = lambda lane: None  # type: ignore[assignment,return-value]

    def job_failed(*args: object, **kwargs: object) -> list[tuple[str, TokenUsage]]:
        raise RuntimeError("Gemini batch job 'batches/abc' did not succeed (state=FAILED)")

    client._call_batch_many = job_failed  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        client.generate_many(["eins"], purpose="unit_test", use_cache=False, force_lane="paid")

    (row,) = client.cost_records
    assert row.outcome == "server_error"
    assert row.mode == "batch"
    assert (row.prompt_tokens, row.completion_tokens, row.cost_usd) == (0, 0, 0.0)


def test_failed_attempts_do_not_move_month_to_date_spend_or_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed-attempt rows are priced at zero on purpose, so they cannot move
    the spend ceiling. Making the gap visible must not also make the budget
    gate fire on usage nobody measured."""
    # No paid key, so the owner's free-to-paid 503 fallback cannot fire and
    # the attempt count is exactly the free lane's own retry budget. Without
    # this the count depends on whether the ambient environment happens to
    # carry a paid key, which it does under a full-suite run.
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    client = GeminiLlmClient(
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
        spend_ceiling_usd=0.01,
        sleep_fn=lambda _seconds: None,
    )

    def always_overloaded(**kwargs: object) -> tuple[str, TokenUsage]:
        raise ServerUnavailableError("503 UNAVAILABLE")

    client._call_transport = always_overloaded  # type: ignore[method-assign]
    with pytest.raises(ServerUnavailableError):
        client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert len(client.cost_records) == GeminiLlmClient.SERVER_ERROR_MAX_RETRIES + 1
    assert all(row.outcome == "server_error" for row in client.cost_records)
    assert client.get_month_to_date_spend() == 0.0

    # The ceiling is untouched, so the next call is not refused.
    client._call_transport = _fake_transport_ok  # type: ignore[method-assign]
    assert client.generate("Hallo Welt", purpose="unit_test", use_cache=False)


def test_spend_ceiling_default_is_the_owners_current_figure() -> None:
    """The default ceiling was 5.00 (a 5 EUR/month budget, tracked in USD).
    The owner has raised it to 7.50. Pinned here for the same reason as the
    rate-limit constants: this number is his call, not a tuning knob, and the
    documents that quote it (CLAUDE.md 9, docs/audits/stage-00-quota.md)
    were corrected in the same commit rather than left contradicting the
    code."""
    client = GeminiLlmClient(cost_log_path=Path("/nonexistent/cost_log.jsonl"))
    assert client.spend_ceiling_usd == 7.50
