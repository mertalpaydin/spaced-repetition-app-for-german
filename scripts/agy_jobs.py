"""Deck jobs run through the Gemini agent (``agy``), not the Gemini API.

Owner's instruction, 2026-09-09: the context sentences for sentence-initial
connectors and the English glosses of the picked card sentences are produced
by the headless Antigravity CLI, which has its own quota and costs nothing
against the project's API budget. Two jobs:

    uv run python scripts/agy_jobs.py contexts
    uv run python scripts/agy_jobs.py glosses --max-batches 40

Both treat the agent's output as untrusted text: every context sentence goes
through the deterministic checks in ``src/phrases/contexts.py`` (including
the carrier validator) before it is stored, and every gloss is checked for
shape (non-empty, a plausible length ratio, not the German echoed back)
before it lands in the translation store with ``source="gemini"``. A batch
whose output file is missing or unparseable is retried once and then the
job stops, which is how a spent quota shows up.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError
from src.contracts import ContextRecord, ContextRequest, ContextResponse, PhraseCard, PhraseUnit
from src.phrases import carrier_validation
from src.phrases import contexts as contexts_module
from src.phrases.cards import GLOSS_LENGTH_RATIO

from scripts.build_translations import (
    DEFAULT_STORE_PATH,
    TranslationRecord,
    _load_store,
    _write_store_atomic,
)

AGY = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
DEFAULT_BUILD_DIR = Path("data/phrases/build")
DEFAULT_MODEL = "gemini-3.8-flash-medium"
CONTEXT_MODEL_LABEL = "agy:" + DEFAULT_MODEL


# -- agent transport -----------------------------------------------------------


class AgentResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str = ""
    response: str = ""
    denied_actions: list[object] = []


def run_agent(
    workspace: Path,
    prompt: str,
    *,
    model: str,
    timeout: str,
    runner: Callable[..., object] | None = None,
) -> AgentResult:
    """One headless ``agy`` call with ``workspace`` as its project. Never
    raises on the agent's own failure: the caller looks at the output file."""
    cmd = [
        str(AGY),
        "-p",
        prompt,
        "--new-project",
        "--add-dir",
        str(workspace.resolve()),
        "--model",
        model,
        "--output-format",
        "json",
        "--print-timeout",
        timeout,
        "--dangerously-skip-permissions",
    ]
    run = runner or subprocess.run
    result = run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
    stdout = getattr(result, "stdout", "") or ""
    try:
        return AgentResult.model_validate(json.loads(stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError, ValidationError):
        return AgentResult(status="NO_JSON", response=stdout[-300:])


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# -- contexts ------------------------------------------------------------------

CONTEXT_PROMPT = (
    "Use exactly two tools: read the file {inp}, then write the file {out}. Do not run shell "
    "commands and do not read any other file. {inp} holds one JSON object per line: card_id, "
    "connector, sentence_de (a German sentence that OPENS with the connector), gloss_en. For "
    "each line write ONE German sentence that comes immediately BEFORE sentence_de, so that "
    "the connector has something to refer to. Rules: A2-B1 German, 5 to 14 words, same "
    "register as sentence_de, no new names, must not start with a connector and must not "
    "contain the connector, ends with a full stop, no quotation marks. Also give a plain "
    "English translation of your sentence. Write {out} as JSON lines with exactly these "
    'fields: {{"card_id": "...", "context_de": "...", "context_en": "..."}}, one line per '
    "input line, nothing else. Then reply with only the number of lines written."
)


def _read_models(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    return [
        model.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def context_requests(
    cards: Iterable[PhraseCard], units: dict[str, PhraseUnit], existing: dict[str, ContextRecord]
) -> list[ContextRequest]:
    todo = [c for c in contexts_module.cards_needing_context(cards) if c.card_id not in existing]
    return [
        ContextRequest(
            card_id=c.card_id,
            unit_id=c.unit_id,
            connector_display=units[c.unit_id].display_de,
            sentence_de=c.sentence_de,
            gloss_en=c.gloss_en or "",
            prompt_version=contexts_module.PROMPT_VERSION,
        )
        for c in todo
    ]


def records_from_replies(
    requests: list[ContextRequest],
    replies: list[dict[str, object]],
    *,
    validate: Callable[[str], bool],
    now: datetime,
    model: str = CONTEXT_MODEL_LABEL,
) -> list[ContextRecord]:
    by_id = {str(r.get("card_id", "")): r for r in replies}
    out: list[ContextRecord] = []
    for request in requests:
        reply = by_id.get(request.card_id)
        response: ContextResponse | None = None
        if reply is not None:
            try:
                response = ContextResponse.model_validate(
                    {
                        "context_de": reply.get("context_de", ""),
                        "context_en": reply.get("context_en", ""),
                    }
                )
            except ValidationError:
                response = None
        if response is None:
            reason: str | None = "unparseable" if reply is not None else "missing"
        else:
            reason = contexts_module.reject_reason(response, request, validate)
        accepted = response is not None and reason is None
        out.append(
            ContextRecord(
                card_id=request.card_id,
                unit_id=request.unit_id,
                sentence_de=request.sentence_de,
                context_de=response.context_de.strip() if accepted and response else None,
                context_en=response.context_en.strip() if accepted and response else None,
                accepted=accepted,
                reject_reason=reason,
                model=model,
                prompt_version=request.prompt_version,
                generated_at=now,
            )
        )
    return out


def job_contexts(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    cards = [
        c for c in _read_models(build_dir / "cards.jsonl", PhraseCard) if isinstance(c, PhraseCard)
    ]
    units = {
        u.unit_id: u
        for u in _read_models(build_dir / "units.jsonl", PhraseUnit)
        if isinstance(u, PhraseUnit)
    }
    existing = contexts_module.load_records(args.contexts)
    requests = context_requests(cards, units, existing)
    print(f"contexts: {len(existing)} stored, {len(requests)} to generate")
    if not requests:
        return 0
    workspace = Path(tempfile.mkdtemp(prefix="agy_contexts_"))
    (workspace / "requests.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "card_id": r.card_id,
                    "connector": r.connector_display,
                    "sentence_de": r.sentence_de,
                    "gloss_en": r.gloss_en,
                },
                ensure_ascii=False,
            )
            + "\n"
            for r in requests
        ),
        encoding="utf-8",
    )
    prompt = CONTEXT_PROMPT.format(inp="requests.jsonl", out="contexts.jsonl")
    result = run_agent(workspace, prompt, model=args.model, timeout=args.timeout)
    print(f"contexts: agent {result.status} {result.response.strip()[:80]!r}")
    replies = read_jsonl(workspace / "contexts.jsonl")

    def validate(text: str) -> bool:
        return carrier_validation.validate_carrier(text).accepted

    produced = records_from_replies(requests, replies, validate=validate, now=datetime.now(UTC))
    merged = {**existing, **{r.card_id: r for r in produced}}
    contexts_module.save_records(merged.values(), args.contexts)
    accepted = sum(1 for r in produced if r.accepted)
    reasons = sorted({r.reject_reason for r in produced if not r.accepted and r.reject_reason})
    print(f"contexts: {len(produced)} generated, {accepted} accepted; rejections: {reasons}")
    return 0


# -- glosses -------------------------------------------------------------------


def write_store_with_retry(
    path: Path,
    store: dict[str, TranslationRecord],
    *,
    attempts: int = 12,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """``os.replace`` fails on Windows while another process has the store
    open for reading (a deck build in progress). Wait and try again rather
    than lose a batch."""
    for attempt in range(attempts):
        try:
            _write_store_atomic(path, store)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(5.0)


GLOSS_PROMPT = (
    "Use exactly two tools: read the file {inp}, then write the file {out}. Do not run shell "
    "commands and do not read any other file. {inp} holds one JSON object per line: id, de (a "
    "German sentence). Translate each German sentence into natural, faithful English, keeping "
    "the sentence structure where English allows it and translating every content word; keep "
    "names and numbers as they are. Write {out} as JSON lines with exactly these fields: "
    '{{"id": "...", "en": "..."}}, one line per input line, nothing else, no commentary. Then '
    "reply with only the number of lines written."
)


def gloss_reject_reason(german: str, english: str) -> str | None:
    en = english.strip()
    if not en:
        return "empty"
    if en.lower() == german.strip().lower():
        return "echo"
    ratio = len(en) / max(len(german), 1)
    low, high = GLOSS_LENGTH_RATIO
    if not (low <= ratio <= high):
        return "length"
    if "\n" in en:
        return "newline"
    return None


def read_wanted(path: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip("\n").split("\t", 2)
        if len(parts) == 3 and parts[2].strip():
            rows.append((parts[0], parts[1], parts[2].strip()))
    return rows


def job_glosses(args: argparse.Namespace) -> int:
    wanted = read_wanted(args.wanted)
    store = _load_store(args.store)
    todo: list[str] = []
    seen: set[str] = set()
    for _, _, text in wanted:
        if text in store or text in seen:
            continue
        seen.add(text)
        todo.append(text)
    print(f"glosses: {len(wanted):,} wanted, {len(todo):,} without a gloss")
    failures = 0
    batches_done = 0
    for start in range(0, len(todo), args.batch_size):
        if batches_done >= args.max_batches:
            break
        batch = todo[start : start + args.batch_size]
        workspace = Path(tempfile.mkdtemp(prefix="agy_gloss_"))
        (workspace / "input.jsonl").write_text(
            "".join(
                json.dumps({"id": str(i), "de": text}, ensure_ascii=False) + "\n"
                for i, text in enumerate(batch)
            ),
            encoding="utf-8",
        )
        prompt = GLOSS_PROMPT.format(inp="input.jsonl", out="output.jsonl")
        result = run_agent(workspace, prompt, model=args.model, timeout=args.timeout)
        replies = read_jsonl(workspace / "output.jsonl")
        by_id = {str(r.get("id", "")): str(r.get("en", "")) for r in replies}
        now = datetime.now(UTC)
        added = 0
        rejected: dict[str, int] = {}
        for i, text in enumerate(batch):
            english = by_id.get(str(i), "")
            reason = gloss_reject_reason(text, english)
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            store[text] = TranslationRecord(
                german=text, english=english.strip(), source="gemini", written_at=now
            )
            added += 1
        if added:
            write_store_with_retry(args.store, store)
        batches_done += 1
        print(
            f"glosses: batch {batches_done} ({len(batch)} sentences) agent {result.status}: "
            f"{added} stored, rejected {rejected}"
        )
        if added == 0:
            failures += 1
            if failures >= args.max_failures:
                print("glosses: stopping, the agent returned nothing usable twice in a row")
                return 1
        else:
            failures = 0
    print(f"glosses: done, {batches_done} batch(es), store now {len(store):,} records")
    return 0


# -- main ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="job", required=True)
    c = sub.add_parser("contexts")
    c.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    c.add_argument("--contexts", type=Path, default=contexts_module.DEFAULT_CONTEXTS_PATH)
    c.add_argument("--model", default=DEFAULT_MODEL)
    c.add_argument("--timeout", default="15m")
    g = sub.add_parser("glosses")
    g.add_argument("--wanted", type=Path, default=DEFAULT_BUILD_DIR / "wanted_carriers.txt")
    g.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    g.add_argument("--model", default=DEFAULT_MODEL)
    g.add_argument("--timeout", default="15m")
    g.add_argument("--batch-size", type=int, default=150)
    g.add_argument("--max-batches", type=int, default=1000)
    g.add_argument("--max-failures", type=int, default=2)
    args = parser.parse_args(argv)
    if not AGY.exists():
        print(f"agy not found at {AGY}")
        return 1
    return job_contexts(args) if args.job == "contexts" else job_glosses(args)


if __name__ == "__main__":
    sys.exit(main())
