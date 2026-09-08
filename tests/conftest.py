"""Pytest fixtures and configuration."""

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def data_fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "fixtures"


@pytest.fixture(autouse=True)
def isolate_batch_job_store(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every test's batch-job bookkeeping out of the real store.

    ``GeminiLlmClient`` records a submitted batch job on disk the instant
    Google accepts it, because a job that is accepted is billable and a job
    nobody wrote down is a job nobody can collect. That recording is
    unconditional by design, which means a unit test submitting a *fake* batch
    job also writes a record -- and without this fixture it writes it into the
    operator's real ``.cache/pending_batch_jobs.json``.

    Not hypothetical. Two entries with ``purpose="unit_test"`` and job names
    ``batches/fake-job`` were found sitting in the real store, where the
    scheduled collector would have gone looking for them at Google.

    Autouse rather than opt-in: the tests that submit batch jobs are not
    obviously the ones that need it, and a test added later would silently
    reintroduce the pollution.
    """
    monkeypatch.setattr(
        "src.llm.client.DEFAULT_BATCH_JOB_STORE",
        tmp_path_factory.mktemp("batch_jobs") / "pending_batch_jobs.json",
    )


@pytest.fixture(autouse=True)
def isolate_azure_ledger(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep every test off the operator's real Azure F0 ledger.

    ``scripts/monthly_translation_topup.py`` reads ``--ledger`` (default: the real
    ``data/fixtures/translations/azure_f0_ledger.json``) before it decides
    whether to translate. Found 2026-09-08: the suite's phase B tests printed
    "the Azure ledger says month 2026-09 was refused on quota" because they
    were reading the owner's file. A test must never depend on, or write to,
    the month's real accounting.
    """
    import scripts.monthly_translation_topup as step7

    ledger_dir = tmp_path_factory.mktemp("azure-ledger")
    monkeypatch.setattr(step7, "DEFAULT_LEDGER_PATH", ledger_dir / "azure_f0_ledger.json")
    monkeypatch.setattr(
        step7,
        "load_ledger",
        lambda path: (
            step7.TranslationLedger()
            if str(path).endswith("azure_f0_ledger.json") and "fixtures" in str(path)
            else _real_load_ledger(path)
        ),
    )


from src.llm.translation_ledger import load_ledger as _real_load_ledger  # noqa: E402
