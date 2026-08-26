"""Automated tests for static web assets, PWA manifest, and offline data."""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from src.bank.exporter import BankExporter
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, Distractor

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


def test_web_exported_data_validates() -> None:
    """Verify exported web data adheres to manifest schema."""
    web_data_dir = Path("web/data")
    assert (web_data_dir / "manifest.json").exists()
    assert (web_data_dir / "all_items.json").exists()

    is_valid = BankExporter.validate_export(web_data_dir)
    assert is_valid is True


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


# ==============================================================================
# Gloss rendering (TODO.md section 4 / feature 5.1 step 3)
#
# There is still no JS test runner, package.json or npm dependency in this
# repo, and this change does not add one. tests/test_worker_sync.py already
# established the alternative it uses instead: drive the real JS file under
# Node with a hand-written stub of the browser API it needs, exactly as that
# module stubs the D1 binding. The same shape is used here, so these tests
# exercise web/app.js itself rather than a reimplementation of it.
# ==============================================================================

_DOM_STUB_JS = """
const listeners = new Map();
const elements = new Map();

function makeElement(id) {
  const classes = new Set();
  const own = new Map();
  return {
    id,
    textContent: '',
    innerHTML: '',
    className: '',
    value: '',
    disabled: false,
    hidden: false,
    style: {},
    selectionStart: 0,
    selectionEnd: 0,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      value: () => Array.from(classes),
    },
    addEventListener: (type, fn) => {
      if (!own.has(type)) own.set(type, []);
      own.get(type).push(fn);
    },
    appendChild: () => {},
    getAttribute: () => null,
    setSelectionRange: () => {},
    focus: () => {},
    _handlers: own,
  };
}

globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  querySelectorAll: () => [],
  addEventListener: (type, fn) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(fn);
  },
  createElement: () => makeElement('created'),
};
globalThis.window = {};

async function boot(items) {
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ items }) });
  const src = readFileSync(APP_JS_PATH, 'utf8');
  (0, eval)(src);
  const ready = listeners.get('DOMContentLoaded') || [];
  for (const fn of ready) await fn();
  return elements;
}
"""


def _run_app_render(
    items: list[dict[str, Any]], *, submit_answer: str | None = None
) -> dict[str, Any]:
    """Boot web/app.js under Node against ``items`` and report what the
    exercise card's gloss node looks like after the first render.

    With ``submit_answer`` the harness also types that answer and fires the
    real submit handler, so the reported state is the one the learner sees
    after grading rather than before it.
    """
    assert NODE is not None
    harness = f"""
import {{ readFileSync }} from 'node:fs';
const APP_JS_PATH = {json.dumps(str(APP_JS))};
{_DOM_STUB_JS}

async function run() {{
  const els = await boot({json.dumps(items, ensure_ascii=False)});
  const answer = {json.dumps(submit_answer)};
  if (answer !== null) {{
    els.get('user-answer-input').value = answer;
    for (const fn of els.get('submit-btn')._handlers.get('click') || []) fn();
  }}
  const gloss = els.get('exercise-gloss');
  const prompt = els.get('exercise-prompt');
  console.log(JSON.stringify({{
    glossExists: gloss !== undefined,
    glossText: gloss ? gloss.textContent : null,
    glossHidden: gloss ? gloss.hidden : null,
    glossInnerHtml: gloss ? gloss.innerHTML : null,
    promptHtml: prompt ? prompt.innerHTML : null,
    feedbackShown: els.get('feedback-box').style.display,
  }}));
}}
run();
"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)

    assert proc.returncode == 0, (
        f"app.js harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    result: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


def _gloss_test_item(gloss_en: str | None) -> dict[str, Any]:
    return {
        "id": "gloss_item",
        "topic_id": "dativ_nach_praeposition",
        "type": "cloze_free",
        "difficulty": 1,
        "cefr": "A2",
        "prompt": "Das Buch liegt auf ___ Tisch.",
        "cue": None,
        "accepted_answers": ["dem"],
        "distractors": [{"text": "den"}],
        "rule_hint": "Wechselpräposition auf + Dativ bei Wo?",
        "gloss_en": gloss_en,
    }


@requires_node
def test_app_renders_english_gloss_before_the_learner_answers() -> None:
    """An item carrying ``gloss_en`` shows that translation on the exercise
    card as soon as the item is rendered, with no interaction first.

    TODO.md section 4: "Every exercise shows its English translation, always."
    It has to be on screen while the learner is still answering, because
    TODO.md 5.1 lets the verifier treat the translation as information the
    learner has when it judges whether a gap has a unique answer. A gloss
    revealed only with the feedback would make that assumption false.
    """
    rendered = _run_app_render([_gloss_test_item("The book is lying on the table.")])

    assert rendered["glossExists"] is True
    assert rendered["glossText"] == "The book is lying on the table."
    assert rendered["glossHidden"] is False
    # Set through textContent, never innerHTML: a gloss is generated content
    # and must not be able to inject markup into the exercise card.
    assert rendered["glossInnerHtml"] == ""
    # The German sentence is still rendered exactly as before.
    assert "gap-blank" in rendered["promptHtml"]


@requires_node
def test_app_keeps_the_gloss_visible_after_the_answer_is_graded() -> None:
    """The gloss is not a hint tier and not a reward: it is up before the
    answer and stays up with the feedback, so the learner can read the
    correction against the translation."""
    rendered = _run_app_render(
        [_gloss_test_item("The book is lying on the table.")], submit_answer="dem"
    )

    assert rendered["feedbackShown"] == "block", "expected the answer to have been graded"
    assert rendered["glossText"] == "The book is lying on the table."
    assert rendered["glossHidden"] is False


@requires_node
def test_app_renders_nothing_extra_for_an_item_without_a_gloss() -> None:
    """``gloss_en`` is null on every bank row exported before the translation
    backfill. Such an item must render no empty box, no placeholder and no
    literal "null" -- the node is emptied and hidden outright."""
    rendered = _run_app_render([_gloss_test_item(None)])

    assert rendered["glossExists"] is True
    assert rendered["glossText"] == ""
    assert rendered["glossHidden"] is True
    assert rendered["glossInnerHtml"] == ""
    assert "gap-blank" in rendered["promptHtml"]


@requires_node
def test_app_renders_nothing_extra_for_an_item_whose_gloss_is_blank() -> None:
    """A gloss that is present but whitespace-only is treated as no gloss, not
    as a translation made of spaces. Machine translation can return an empty
    string for an input it failed on, and the store records what it was
    given."""
    rendered = _run_app_render([_gloss_test_item("   ")])

    assert rendered["glossText"] == ""
    assert rendered["glossHidden"] is True


def test_gloss_markup_ships_empty_and_hidden() -> None:
    """The gloss node exists in index.html, ships empty and hidden, and is
    grouped with the German prompt rather than as a loose card child.

    Shipping it pre-filled would flash a translation belonging to the static
    placeholder sentence before the first real item renders; shipping it
    visible-but-empty would reserve vertical space for items that have no
    gloss."""
    markup = (Path("web") / "index.html").read_text(encoding="utf-8")

    match = re.search(r'<p id="exercise-gloss"[^>]*>(.*?)</p>', markup, re.DOTALL)
    assert match is not None, "index.html must contain the #exercise-gloss node"
    assert match.group(1).strip() == "", "#exercise-gloss must ship with no text in it"

    tag = match.group(0)
    assert "hidden" in tag, "#exercise-gloss must ship hidden"
    assert 'lang="en"' in tag, "the gloss is English inside a lang=de document"

    prompt_group = re.search(
        r'<div class="prompt-group">.*?</div>\s*<p id="exercise-gloss"', markup, re.DOTALL
    )
    assert prompt_group is not None, (
        "the gloss must sit next to the German prompt inside .prompt-group, so "
        "the two read as one unit and a hidden gloss changes no other spacing"
    )


def test_gloss_styling_is_subordinate_to_the_german_and_collapses_when_hidden() -> None:
    """The German sentence is the exercise and the English is support, so the
    gloss must be smaller than the prompt and must occupy no space at all when
    hidden (no layout jump between an item with a gloss and one without)."""
    css = (Path("web") / "styles.css").read_text(encoding="utf-8")

    gloss_rule = re.search(r"\.gloss-box\s*\{(.*?)\}", css, re.DOTALL)
    prompt_rule = re.search(r"\.prompt-box\s*\{(.*?)\}", css, re.DOTALL)
    assert gloss_rule is not None and prompt_rule is not None

    def _font_size(block: str) -> float:
        found = re.search(r"font-size:\s*([\d.]+)rem", block)
        assert found is not None
        return float(found.group(1))

    assert _font_size(gloss_rule.group(1)) < _font_size(prompt_rule.group(1))
    assert "var(--text-secondary)" in gloss_rule.group(1), (
        "the gloss must use the secondary text colour, not the primary one"
    )

    hidden_rule = re.search(r"\.gloss-box\[hidden\]\s*\{(.*?)\}", css, re.DOTALL)
    assert hidden_rule is not None, "styles.css must state that a hidden gloss is display:none"
    assert "display: none" in hidden_rule.group(1)


def test_exported_web_data_carries_gloss_en_field(tmp_path: Path) -> None:
    """The JSON file the app actually fetches must carry ``gloss_en``.

    web/app.js reads its items straight out of ``web/data/all_items.json``,
    which BankExporter writes, so this is the trip the field has to survive:
    BankItem -> sqlite -> export -> the file the browser loads. It was being
    dropped twice on that route (no sqlite column, and absent from the export
    allowlist) before TODO.md 5.1 step 3.
    """
    bank = SqliteItemBank(tmp_path / "web_gloss.db")
    bank.insert_item(
        BankItem(
            id="web_gloss_01",
            topic_id="dativ_nach_praeposition",
            type="cloze_free",
            difficulty=1,
            cefr="A2",
            prompt="Das Buch liegt auf ___ Tisch.",
            accepted_answers=["dem"],
            distractors=[Distractor(text="den")],
            gloss_en="The book is lying on the table.",
        )
    )
    web_data = tmp_path / "data"
    BankExporter.export_to_directory(bank, web_data)

    with (web_data / "all_items.json").open("r", encoding="utf-8") as f:
        exported = json.load(f)

    assert exported[0]["gloss_en"] == "The book is lying on the table."
    assert BankExporter.validate_export(web_data) is True
