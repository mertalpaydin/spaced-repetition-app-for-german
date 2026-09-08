"""Static web assets, the PWA manifest, and the JS/Python grader pins.

Phase 0 of the phrase-deck pivot: the grammar export tests are gone with
``src/bank``; phase 3 rewrites this file for the new ``web/``.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

NODE = shutil.which("node")
APP_JS = Path(__file__).resolve().parents[1] / "web" / "app.js"

requires_node = pytest.mark.skipif(NODE is None, reason="node is required to exercise web/app.js")


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
    web_dir = Path("web")
    manifest_path = web_dir / "manifest.json"
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["name"] == "DeutschMaster - German Grammar Trainer"
    assert data["display"] == "standalone"
    assert "icons" in data and len(data["icons"]) >= 2

    # Every icon the manifest references must actually exist on disk. A
    # manifest pointing at missing icon files fails Lighthouse's PWA
    # installability check and silently breaks the home-screen icon.
    for icon in data["icons"]:
        icon_path = web_dir / icon["src"].lstrip("./")
        assert icon_path.exists(), f"manifest icon not found on disk: {icon['src']}"


def _bracketed_segment(content: str, marker: str, open_ch: str, close_ch: str) -> str:
    """Return the text strictly between the first `open_ch`/`close_ch` pair
    that follows `marker`. Assumes no nested `open_ch`/`close_ch` inside the
    segment, which holds for the flat string/tuple literals graded here."""
    start = content.index(marker)
    open_idx = content.index(open_ch, start)
    close_idx = content.index(close_ch, open_idx)
    return content[open_idx + 1 : close_idx]


def _quoted_strings(segment: str) -> set[str]:
    return set(re.findall(r"""['"]([^'"]+)['"]""", segment))


def test_js_and_python_typo_graders_share_grammatical_morphemes() -> None:
    """web/app.js's grader must grade the same morphemes as
    src/engine/typo_grader.py's ScopedTypoGrader.

    Two divergent graders writing to one FSRS history is a data-corruption
    path named explicitly in 04-application.md stage 9. There is no JS test
    runner in this repo, so this test reads both source files directly and
    fails the moment the two morpheme sets drift apart.
    """
    py_src = Path("src/engine/typo_grader.py").read_text(encoding="utf-8")
    js_src = Path("web/app.js").read_text(encoding="utf-8")

    py_segment = _bracketed_segment(py_src, "GRAMMATICAL_MORPHEMES: set[str] = {", "{", "}")
    js_segment = _bracketed_segment(js_src, "const GRAMMATICAL_MORPHEMES = new Set([", "[", "]")

    py_morphemes = _quoted_strings(py_segment)
    js_morphemes = _quoted_strings(js_segment)

    assert py_morphemes == js_morphemes, (
        "GRAMMATICAL_MORPHEMES drifted between the Python and JS graders.\n"
        f"Python only: {sorted(py_morphemes - js_morphemes)}\n"
        f"JS only: {sorted(js_morphemes - py_morphemes)}"
    )


def test_js_and_python_typo_graders_share_critical_minimal_pairs() -> None:
    """web/app.js's CRITICAL_MINIMAL_PAIRS must match
    src/engine/typo_grader.py's ScopedTypoGrader.CRITICAL_MINIMAL_PAIRS
    exactly, so neither grader tolerates an edit the other treats as a hard
    failure.
    """
    py_src = Path("src/engine/typo_grader.py").read_text(encoding="utf-8")
    js_src = Path("web/app.js").read_text(encoding="utf-8")

    py_segment = _bracketed_segment(
        py_src, "CRITICAL_MINIMAL_PAIRS: set[tuple[str, str]] = {", "{", "}"
    )
    js_segment = _bracketed_segment(js_src, "const CRITICAL_MINIMAL_PAIRS = new Set([", "[", "]")

    py_pairs = {
        (a, b)
        for a, b in re.findall(r"""\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""", py_segment)
    }
    js_pairs = {tuple(s.split("|", 1)) for s in _quoted_strings(js_segment)}

    assert py_pairs == js_pairs, (
        "CRITICAL_MINIMAL_PAIRS drifted between the Python and JS graders.\n"
        f"Python only: {sorted(py_pairs - js_pairs)}\n"
        f"JS only: {sorted(js_pairs - py_pairs)}"
    )
