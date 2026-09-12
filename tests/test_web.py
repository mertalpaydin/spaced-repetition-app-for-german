"""The PWA (phase 3b): assets, the service worker's precache list, the
JavaScript test suite, and the generated files the JavaScript side depends
on (grader tables, grader parity cases, the FSRS replay fixture).

The three generated files are golden: this module regenerates them from the
Python engine and compares. Run with ``--regen-web`` to rewrite them.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.contracts import MarkEntry, ReviewEntry
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.review_log import derive_state
from src.engine.typo_grader import SUFFIX_TOLERANCE_WINDOW, ScopedTypoGrader

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FIXTURES = ROOT / "data" / "fixtures"
TABLES = WEB / "lib" / "grader-tables.js"
PARITY = FIXTURES / "grading" / "grader_parity.json"
REPLAY = FIXTURES / "fsrs" / "replay_fixture.json"


# -- assets ------------------------------------------------------------------


def test_web_assets_exist_and_reference_each_other() -> None:
    index = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css", "manifest.json"):
        assert (WEB / name).exists(), name
        assert name in index
    assert "https://" not in index  # the shell loads nothing external


def test_manifest_is_the_phrase_trainer() -> None:
    manifest = json.loads((WEB / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Phrasen"
    assert all((WEB / icon["src"]).exists() for icon in manifest["icons"])


def test_only_the_sync_module_talks_to_the_network() -> None:
    for path in sorted(WEB.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        hosts = set(re.findall(r"https?://([\w.-]+)", text))
        if path.name == "sync.js":
            assert hosts == {"api.github.com"}, hosts
        else:
            assert not hosts, (path, hosts)


def test_service_worker_precaches_every_shell_file() -> None:
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'"\./([^"]+)"', sw.split("let deckCache")[0]))
    shipped = {
        str(p.relative_to(WEB)).replace("\\", "/")
        for p in WEB.rglob("*")
        if p.is_file() and "data" not in p.parts and p.name != "sw.js"
    }
    assert listed - {""} == shipped, listed ^ shipped


def test_deck_lives_under_web() -> None:
    manifest = json.loads((WEB / "data" / "deck" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert (WEB / "data" / "deck" / manifest["units_file"]).exists()
    assert all((WEB / "data" / "deck" / s["file"]).exists() for s in manifest["shards"])


# -- the JavaScript suite ----------------------------------------------------


def test_javascript_suite_passes() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    run = subprocess.run([node, "--test", *tests], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stdout + run.stderr


# -- generated files ---------------------------------------------------------


def render_grader_tables() -> str:
    morphemes = json.dumps(sorted(ScopedTypoGrader.GRAMMATICAL_MORPHEMES), ensure_ascii=False)
    pairs = json.dumps(
        sorted(f"{a}|{b}" for a, b in ScopedTypoGrader.CRITICAL_MINIMAL_PAIRS), ensure_ascii=False
    )
    return (
        "// Generated from src/engine/typo_grader.py by tests/test_web.py's generator; "
        "do not edit by hand.\n"
        f"export const SUFFIX_TOLERANCE_WINDOW = {SUFFIX_TOLERANCE_WINDOW};\n"
        f"export const GRAMMATICAL_MORPHEMES = new Set({morphemes});\n"
        f"export const CRITICAL_MINIMAL_PAIRS = new Set({pairs});\n"
    )


PARITY_CASES: list[tuple[str, list[str]]] = [
    ("wartet", ["wartet"]),
    ("Wartet", ["wartet"]),
    ("wertet", ["wartet"]),
    ("warte", ["wartet"]),
    ("wartte", ["wartet"]),
    ("hoert", ["hört"]),
    ("gross", ["groß"]),
    ("trotzdem", ["Trotzdem", "trotzdem"]),
    ("dem", ["den"]),
    ("einen", ["einem"]),
    ("auf", ["auf"]),
    ("aus", ["auf"]),
    ("an", ["auf"]),
    ("Entfernung", ["Entfernung"]),
    ("entfernung", ["Entfernung"]),
    ("Entfernunk", ["Entfernung"]),
    ("Entfernug", ["Entfernung"]),
    ("Enfernung", ["Entfernung"]),
    ("zu", ["zu"]),
    ("zur", ["zu"]),
    ("sich", ["sich"]),
    ("sih", ["sich"]),
    ("aufgestanden", ["aufgestanden"]),
    ("aufgestandne", ["aufgestanden"]),
    ("aufgstanden", ["aufgestanden"]),
    ("hätte", ["hatte"]),
    ("Strasse", ["Straße"]),
    ("", ["auf"]),
    ("  wartet ", ["wartet"]),
]


def render_parity_cases() -> str:
    out = []
    for typed, accepted in PARITY_CASES:
        r = ScopedTypoGrader.grade(typed, accepted)
        out.append(
            {
                "typed": typed,
                "accepted": accepted,
                "is_correct": r.is_correct,
                "is_exact": r.is_exact,
                "is_scoped_typo": r.is_scoped_typo,
                "is_transliteration": r.is_transliteration,
                "is_capitalization_error": r.is_capitalization_error,
            }
        )
    return json.dumps(out, indent=1, ensure_ascii=False) + "\n"


def render_replay_fixture() -> str:
    """A simulated learner over 40 days (seeded), replayed by the Python engine.

    The JavaScript engine must reach the same records; tests/js/replay.test.mjs
    checks it. Learning-step re-shows within a session are included so the
    same-day (short-term) branch of the scheduler is exercised.
    """
    rng = random.Random(11)
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    engine = FSRSEngine()
    recs: dict[str, FSRSRecord] = {}
    entries: list[ReviewEntry | MarkEntry] = []
    seq = 1
    introduced = 0
    for day in range(40):
        start = t0 + timedelta(days=day)
        session = [u for u, r in recs.items() if r.due <= start + timedelta(hours=8)]
        session.sort(key=lambda u: engine.get_retrievability(recs[u], start))
        for _ in range(3):
            session.append(f"u{introduced}")
            introduced += 1
        t = start
        queue = list(session)
        while queue:
            u = queue.pop(0)
            t += timedelta(minutes=rng.randint(1, 3))
            r = recs.get(u) or FSRSRecord(card_id=u, due=t)
            rating = rng.choices(["good", "again", "hard"], [7, 2, 1])[0]
            r = engine.schedule_review(r, rating, now=t)
            recs[u] = r
            entries.append(
                ReviewEntry(
                    seq=seq,
                    ts=t,
                    unit_id=u,
                    card_id="c" * 12,
                    rating=rating,
                    outcome="exact" if rating == "good" else "wrong",
                    answers=["x"],
                    expected=["x"],
                    elapsed_ms=1,
                    deck_version="fx",
                )
            )
            seq += 1
            if r.state != "review" and r.due <= start + timedelta(hours=8) and u not in queue:
                queue.append(u)
    entries.append(
        MarkEntry(seq=seq, ts=t0 + timedelta(days=41), unit_id="u0", known=True, source="triage")
    )
    state = derive_state(entries, engine)
    now = t0 + timedelta(days=42)
    fixture = {
        "log": [json.loads(e.model_dump_json()) for e in entries],
        "now": now.isoformat(),
        "expected": {
            uid: {
                "state": r.state,
                "due": r.due.isoformat(),
                # Rounded: libm's exp and pow differ in the last bit between
                # Windows and Linux, and the JavaScript side compares at 1e-6.
                "stability": round(r.stability or 0.0, 9),
                "difficulty": round(r.difficulty or 0.0, 9),
                "reps": r.reps,
                "lapses": r.lapses,
                "retrievability": engine.get_retrievability(r, now),
            }
            for uid, r in sorted(state.records.items())
        },
        "known": sorted(state.known),
    }
    return json.dumps(fixture, indent=1, sort_keys=True) + "\n"


GENERATED = {
    TABLES: render_grader_tables,
    PARITY: render_parity_cases,
    REPLAY: render_replay_fixture,
}


@pytest.mark.golden
@pytest.mark.parametrize("path", sorted(GENERATED, key=str), ids=lambda p: p.name)
def test_generated_web_file_is_current(path: Path, request: pytest.FixtureRequest) -> None:
    rendered = GENERATED[path]()
    if request.config.getoption("--regen-web"):
        path.write_text(rendered, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == rendered, (
        f"{path.name} is stale; run `uv run pytest tests/test_web.py --regen-web`"
    )
