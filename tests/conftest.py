"""Pytest fixtures and configuration."""

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def data_fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "fixtures"
