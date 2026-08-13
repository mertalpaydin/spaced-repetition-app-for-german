"""Automated tests for static web assets, PWA manifest, and offline data."""

import json
from pathlib import Path

from src.bank.exporter import BankExporter


def test_web_assets_exist() -> None:
    """Verify all core static web application files exist."""
    web_dir = Path("web")
    assert (web_dir / "index.html").exists()
    assert (web_dir / "styles.css").exists()
    assert (web_dir / "app.js").exists()
    assert (web_dir / "manifest.json").exists()
    assert (web_dir / "sw.js").exists()


def test_web_manifest_schema() -> None:
    """Verify PWA manifest schema attributes."""
    manifest_path = Path("web/manifest.json")
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["name"] == "DeutschMaster - German Grammar Trainer"
    assert data["display"] == "standalone"
    assert "icons" in data and len(data["icons"]) >= 2


def test_web_exported_data_validates() -> None:
    """Verify exported web data adheres to manifest schema."""
    web_data_dir = Path("web/data")
    assert (web_data_dir / "manifest.json").exists()
    assert (web_data_dir / "all_items.json").exists()

    is_valid = BankExporter.validate_export(web_data_dir)
    assert is_valid is True
