"""Unit tests for the centralized LLM client, spend control, cost logging, and AST scanner."""

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from src.llm.client import BudgetExceeded, GeminiLlmClient


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
