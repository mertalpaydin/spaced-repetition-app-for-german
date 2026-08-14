"""Unit tests for the centralized LLM client, spend control, cost logging, and AST scanner."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from src.llm.client import BudgetExceeded, GeminiLlmClient, QuotaExceededError


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

    original_transport = client._call_transport
    call_count = {"n": 0}

    def flaky_transport(**kwargs: object) -> tuple[str, int, int]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise QuotaExceededError("rpm")
        return original_transport(**kwargs)  # type: ignore[arg-type]

    client._call_transport = flaky_transport  # type: ignore[method-assign]

    response = client.generate("Hallo Welt", purpose="unit_test", use_cache=False)

    assert response
    assert call_count["n"] == 2, "transport must be retried once after the RPM 429"
    assert sleeps == [GeminiLlmClient.RPM_BACKOFF_SECONDS]
    assert client.free_lane_open is True
    assert client.free_lane_closed_until is None

    lines = [
        line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 1
    assert '"lane":"free"' in lines[0]


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

    original_transport = client._call_transport
    raised = {"once": False}

    def flaky_transport(**kwargs: object) -> tuple[str, int, int]:
        if not raised["once"]:
            raised["once"] = True
            raise QuotaExceededError("rpd")
        return original_transport(**kwargs)  # type: ignore[arg-type]

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
    client._call_transport = original_transport  # type: ignore[method-assign]
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


def test_free_lane_never_uses_batch_and_paid_lane_always_does(tmp_path: Path) -> None:
    """The free lane is always a synchronous call; the paid lane is always batch."""
    log_file = tmp_path / "cost_log.jsonl"
    client = GeminiLlmClient(cost_log_path=log_file, cache_dir=tmp_path / "cache")

    seen_modes: list[str] = []
    original_transport = client._call_transport

    def recording_transport(**kwargs: object) -> tuple[str, int, int]:
        seen_modes.append(str(kwargs["mode"]))
        return original_transport(**kwargs)  # type: ignore[arg-type]

    client._call_transport = recording_transport  # type: ignore[method-assign]

    client.generate("Freie Anfrage", purpose="unit_test", use_cache=False)
    assert seen_modes[-1] == "sync"

    client.free_lane_open = False
    client.generate("Bezahlte Anfrage", purpose="unit_test", use_cache=False)
    assert seen_modes[-1] == "batch"


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

    assert not log_file.parent.exists()
    assert not cache_dir.exists()

    client.generate("Hallo Welt", purpose="unit_test", use_cache=True)

    assert log_file.parent.exists()
    assert cache_dir.exists()
