"""The browser page of phase 3a: the assets the local server serves."""

import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"


def test_web_assets_exist_and_reference_each_other() -> None:
    index = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css", "manifest.json"):
        assert (WEB / name).exists(), name
        assert name in index
    assert "https://" not in index  # nothing external: the page must work on the LAN offline


def test_manifest_is_the_phrase_trainer() -> None:
    manifest = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Phrasen"
    assert all((WEB / icon["src"]).exists() for icon in manifest["icons"])


def test_app_js_uses_only_the_documented_endpoints() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for endpoint in (
        "/api/next",
        "/api/answer",
        "/api/mark",
        "/api/triage",
        "/api/stats",
        "/api/deck",
    ):
        assert endpoint in app
