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
from src.contracts import (
    ContextRecord,
    ContextRequest,
    ContextResponse,
    PhraseCard,
    PhraseUnit,
    UnitGlossRecord,
)
from src.phrases import carrier_validation
from src.phrases import contexts as contexts_module
from src.phrases import unit_glosses as glosses_module
from src.phrases import written_carriers as written_module
from src.phrases.cards import GLOSS_LENGTH_RATIO
from src.phrases.mining import WordGate, detect_words
from src.phrases.occurrences import Occurrence, read_occurrences, write_occurrences
from src.phrases.parse import ParsedSentence, parse_many

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
        # A Tatoeba gloss does not count (rule 9: it is never shown), so the
        # sentence is glossed again and the agent's record replaces it.
        if (text in store and store[text].source != "tatoeba") or text in seen:
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


# -- unit glosses --------------------------------------------------------------

UNIT_GLOSS_PROMPT = (
    "Use exactly two tools: read the file {inp}, then write the file {out}. Do not run shell "
    "commands and do not read any other file. {inp} holds one JSON object per line: id, de (a "
    "German word or phrase as a learner would look it up), kind, example (a sentence using "
    "it, or empty). For each line give the English a dictionary would give for de, shortest "
    "common rendering first, at most three renderings. Rules: lower case unless it is a "
    "proper noun; a verb as a bare infinitive without 'to'; no German in the English at all, "
    "not even in brackets, because the learner sees this English BEFORE answering and any "
    "German would give the answer away; no commentary. Write {out} as JSON lines with exactly "
    'these fields: {{"id": "...", "en": ["...", "..."]}}, one line per input line, nothing '
    "else. Then reply with only the number of lines written."
)


def job_unit_glosses(args: argparse.Namespace) -> int:
    """The phrase's own English, the "Gesucht:" line the learner sees before
    answering. Same gates as the API path: ``unit_glosses.reject_reason``,
    which includes the German-leak check of rule 2."""
    units = [
        u
        for u in _read_models(args.build_dir / "units.jsonl", PhraseUnit)
        if isinstance(u, PhraseUnit)
    ]
    cards = [
        c
        for c in (
            _read_models(args.build_dir / "cards.jsonl", PhraseCard)
            if (args.build_dir / "cards.jsonl").exists()
            else []
        )
        if isinstance(c, PhraseCard)
    ]
    existing = glosses_module.load_records(args.unit_glosses)
    todo = [u for u in glosses_module.units_needing_gloss(units) if u.unit_id not in existing]
    if args.only_requested:
        # A requested word keeps its CORPUS rank, which for a rare word is in
        # the thousands even though the scheduler teaches it first. So
        # --max-rank is the wrong filter for them and excluded exactly the
        # words the owner asked for (2026-10-07).
        todo = [u for u in todo if u.requested_order is not None]
    elif args.max_rank:
        todo = [u for u in todo if u.rank <= args.max_rank]
    examples = glosses_module.example_sentences(cards)
    print(f"unit-glosses: {len(existing)} stored, {len(todo)} still needed")
    produced: dict[str, UnitGlossRecord] = {}
    failures = 0
    batches_done = 0
    try:
        for start in range(0, len(todo), args.batch_size):
            if batches_done >= args.max_batches:
                break
            batch = todo[start : start + args.batch_size]
            workspace = Path(tempfile.mkdtemp(prefix="agy_unitgloss_"))
            (workspace / "input.jsonl").write_text(
                "".join(
                    json.dumps(
                        {
                            "id": u.unit_id,
                            "de": u.display_de,
                            "kind": u.kind,
                            "example": examples.get(u.unit_id, ""),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for u in batch
                ),
                encoding="utf-8",
            )
            prompt = UNIT_GLOSS_PROMPT.format(inp="input.jsonl", out="output.jsonl")
            result = run_agent(workspace, prompt, model=args.model, timeout=args.timeout)
            rows = read_jsonl(workspace / "output.jsonl")
            if not rows:
                failures += 1
                print(f"  batch {batches_done}: no output ({result.status}); {failures} failure(s)")
                if failures >= args.max_failures:
                    print("unit-glosses: stopping, the agent produced nothing twice")
                    break
                continue
            by_id = {u.unit_id: u for u in batch}
            kept = 0
            rejected: dict[str, int] = {}
            for row in rows:
                unit = by_id.get(str(row.get("id", "")))
                raw = row.get("en")
                glosses = [str(g).strip() for g in raw] if isinstance(raw, list) else []
                if unit is None or not glosses:
                    rejected["unparseable"] = rejected.get("unparseable", 0) + 1
                    continue
                reason = glosses_module.reject_reason(unit, glosses)
                if reason is not None:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                produced[unit.unit_id] = UnitGlossRecord(
                    unit_id=unit.unit_id,
                    display_de=unit.display_de,
                    glosses=glosses,
                    accepted=True,
                    reject_reason=None,
                    model=args.model,
                    prompt_version=glosses_module.PROMPT_VERSION,
                    generated_at=datetime.now(UTC),
                )
                kept += 1
            batches_done += 1
            print(f"  batch {batches_done}: {kept} kept, rejected {rejected}")
    finally:
        if produced:
            glosses_module.save_records({**existing, **produced}.values(), args.unit_glosses)
    print(f"unit-glosses: {len(produced)} new record(s) -> {args.unit_glosses}")
    return 0


# -- written carriers ----------------------------------------------------------

WRITE_CARDS_PROMPT = (
    "Use exactly two tools: read the file {inp}, then write the file {out}. Do not run shell "
    "commands and do not read any other file. {inp} holds one JSON object per line: lemma (a "
    "German word), kind, count. For each line write `count` short German sentences that use "
    "lemma. Rules: everyday register, 5 to 14 words, one or two clauses, ending in . ! or ?; "
    "a different grammatical form of lemma in each sentence; nothing offensive, no proper "
    "names, no brand names. Give a plain English translation of each sentence, and the exact "
    "surface form of lemma as it appears in that sentence. The English must not contain lemma "
    "or any other German word. Write {out} as JSON lines with exactly these fields: "
    '{{"lemma": "...", "de": "...", "en": "...", "surface": "..."}}, one line per sentence, '
    "nothing else. Then reply with only the number of lines written."
)


def job_write_cards(args: argparse.Namespace) -> int:
    """Sentences for a requested word the corpus does not use at all.

    Same gates as the API path (``written_carriers.reject_reason``): the
    surface form must occur in the sentence, the English must not quote the
    German, the gloss must be plausible, and the sentence must pass the
    carrier validator.
    """
    # --only names the words directly, as "lemma:kind". The report is written
    # by the mine stage, so without this a corrected key (2026-10-07:
    # "angestellte" is lemmatised to "angestellter", which is what the miner
    # aggregates on) could not be retried without a two-hour mine first.
    if args.only:
        named: list[tuple[str, str]] = []
        for spec in args.only:
            key, _, kind = spec.partition(":")
            if not key or not kind:
                print(f"--only expects lemma:kind, got {spec!r}")
                return 1
            named.append((key.strip().lower(), kind.strip()))
        return _write_cards_for(args, named)
    report_path = args.build_dir / "report.json"
    if not report_path.exists():
        print("write-cards: no report.json; run --stage mine first")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    missing = [str(e) for e in (report.get("requested_missing") or [])]
    excluded = {str(e) for e in (report.get("requested_but_excluded") or [])}
    found: set[str] = set()
    occ_path = args.build_dir / "requested_occurrences.jsonl"
    if occ_path.exists():
        found = {occ.unit_key for occ in read_occurrences(occ_path)}
    wanted: list[tuple[str, str]] = []
    for entry in missing:
        if entry in excluded:
            continue
        kind, _, key = entry.partition(":")
        if kind and key and key not in found:
            wanted.append((key, kind))
    if not wanted:
        print("write-cards: every requested word has a sentence; nothing to write")
        return 0
    return _write_cards_for(args, wanted)


def _write_cards_for(args: argparse.Namespace, wanted: list[tuple[str, str]]) -> int:
    print(f"write-cards: {len(wanted)} word(s): {[k for k, _ in wanted]}")
    if not carrier_validation.analysis_available():
        print("write-cards: spaCy is unavailable, so nothing can be validated")
        return 1

    workspace = Path(tempfile.mkdtemp(prefix="agy_writecards_"))
    (workspace / "input.jsonl").write_text(
        "".join(
            json.dumps({"lemma": key, "kind": kind, "count": args.count}, ensure_ascii=False) + "\n"
            for key, kind in wanted
        ),
        encoding="utf-8",
    )
    prompt = WRITE_CARDS_PROMPT.format(inp="input.jsonl", out="output.jsonl")
    result = run_agent(workspace, prompt, model=args.model, timeout=args.timeout)
    rows = read_jsonl(workspace / "output.jsonl")
    if not rows:
        print(f"write-cards: the agent produced nothing ({result.status})")
        return 1

    def validate(text: str) -> bool:
        return carrier_validation.validate_carrier(text).accepted

    kept: dict[str, str] = {}
    rows_kept: list[tuple[str, str, written_module.WrittenSentence]] = []
    per_word: dict[str, int] = {}
    rejected: dict[str, int] = {}
    for row in rows:
        lemma = str(row.get("lemma", "")).strip().lower()
        sentence = written_module.WrittenSentence(
            german=str(row.get("de", "")).strip(),
            english=str(row.get("en", "")).strip(),
            surface=str(row.get("surface", "")).strip(),
        )
        if sentence.german in kept:
            continue
        reason = written_module.reject_reason(sentence, lemma, validate)
        if reason is not None:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        kept[sentence.german] = sentence.english
        kinds = dict(wanted)
        rows_kept.append((lemma, kinds.get(lemma, "adjective"), sentence))
        per_word[lemma] = per_word.get(lemma, 0) + 1
    print(f"write-cards: {len(kept)} sentence(s) kept, rejected {rejected}")
    for key, _ in wanted:
        print(f"  {key}: {per_word.get(key, 0)} kept")
    if not kept:
        return 1

    store = _load_store(args.store)
    now = datetime.now(UTC)
    for german, english in kept.items():
        store[german] = TranslationRecord(
            german=german, english=english, source="gemini", written_at=now
        )
    write_store_with_retry(args.store, store)
    print(f"write-cards: store now {len(store):,} records")

    gate = WordGate(
        lemmas=frozenset(key for key, _ in wanted),
        glossed=frozenset(kept),
        dictionary=carrier_validation._load_dictionary(),  # noqa: SLF001
        cap=args.count * 4,
    )
    ordered = sorted(rows_kept, key=lambda row: row[2].german)
    texts = [row[2].german for row in ordered]
    emitted: list[Occurrence] = []
    forced = 0
    parsed_all = list(parse_many(texts))
    for index, ((lemma, kind, sentence), parsed) in enumerate(
        zip(ordered, parsed_all, strict=True)
    ):
        line_id = f"w{index:04d}"
        found = [
            occ
            for occ in detect_words(parsed, gate, source="written", line_id=line_id)
            if occ.unit_key == lemma
        ]
        if found:
            emitted.extend(found)
            continue
        # Rule 1: the owner asked for this word by name. The detector only
        # emits NOUN, VERB, ADJ and ADV and drops the adverb stoplist, so
        # "solche" (DET), "aufgrund" (ADP) and "dazu" (stoplisted) never
        # reached it, and he was told they could not be taught. They can:
        # the sentence and the surface form are all a card needs. The gaps
        # still have to line up (rule 6), which is what _forced_occurrence
        # checks, and the gloss has already passed rule 2 above.
        occ = _forced_occurrence(parsed, sentence, lemma, kind, line_id)
        if occ is not None:
            emitted.append(occ)
            forced += 1
    # Merge rather than overwrite: a run for one corrected word must not
    # throw away the occurrences of every word written before it, which is
    # the obvious way to lose work when retrying a single key (2026-10-07).
    out_path = args.build_dir / "written_occurrences.jsonl"
    rewritten = {key for key, _ in wanted}
    if out_path.exists():
        kept_before = [occ for occ in read_occurrences(out_path) if occ.unit_key not in rewritten]
        if kept_before:
            print(f"write-cards: keeping {len(kept_before)} occurrence(s) for other words")
            emitted = kept_before + emitted
    write_occurrences(out_path, iter(emitted))
    by_key: dict[str, int] = {}
    for occ in emitted:
        by_key[occ.unit_key] = by_key.get(occ.unit_key, 0) + 1
    for key, _ in wanted:
        print(f"  {key}: {by_key.get(key, 0)} occurrence(s)")
    print(
        f"write-cards: {len(emitted)} occurrence(s) written "
        f"({forced} past the part-of-speech gates); re-run --stage mine"
    )
    return 0


#: The kind the DETECTOR would emit for a requested kind. ADJ and ADV tokens
#: are both filed as "adjective" at parse time and units._decide_word settles
#: which a lemma mostly is, so a forced occurrence has to use the provisional
#: kind or its stats land where nothing looks. Mirrors _REQUEST_STAT_KIND in
#: src/phrases/units.py.
_PROVISIONAL_KIND: dict[str, str] = {"adverb": "adjective"}


def _forced_occurrence(
    parsed: ParsedSentence,
    sentence: written_module.WrittenSentence,
    lemma: str,
    kind: str,
    line_id: str,
) -> Occurrence | None:
    """An occurrence for a word the detector refuses to emit.

    ``mining.words._word_of`` only ever emits NOUN, VERB, ADJ and ADV, and
    drops anything on the adverb stoplist, so a determiner, a preposition or
    a stoplisted adverb can never become a unit however good its sentence
    is. Rule 1 says that is not a reason to refuse the owner a word he asked
    for, so this builds the occurrence from the sentence and the surface
    form the model gave, which is everything a card needs.

    The one thing that cannot be fudged is the gap: the surface form has to
    be a whole token of the parse, so ``answers`` slices back out of
    ``sentence_de`` exactly (rule 6). Returns ``None`` when it does not,
    rather than producing a card whose blank is in the wrong place.
    """
    surface = sentence.surface
    for index, token in enumerate(parsed.tokens):
        if token.text != surface:
            continue
        start = token.idx
        end = start + len(token.text)
        if sentence.german[start:end] != surface:
            continue
        return Occurrence(
            # The PROVISIONAL kind, the one the detector would have used.
            # ADJ and ADV are both emitted as "adjective" and
            # units._decide_word settles which a lemma mostly is, so an
            # occurrence emitted as "adverb" lands in a stats bucket nothing
            # reads and the word stays missing however many sentences it has
            # (2026-10-07: wodurch, dazu, aufgrund and wieso all did).
            kind=_PROVISIONAL_KIND.get(kind, kind),  # type: ignore[arg-type]
            unit_key=lemma,
            parts=[lemma],
            token_indices=[index],
            spans=[(start, end)],
            surfaces=[surface],
            corpus_source="written",
            line_id=line_id,
            text=sentence.german,
            # Distinct per surface form, so the card stage still covers
            # different realisations of the word rather than one twice.
            form_key=f"written|{surface.lower()}",
            evidence={"surface": surface, "forced": "requested"},
        )
    return None


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
    u = sub.add_parser("unit-glosses")
    u.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    u.add_argument("--unit-glosses", type=Path, default=glosses_module.DEFAULT_UNIT_GLOSSES_PATH)
    u.add_argument("--model", default=DEFAULT_MODEL)
    u.add_argument("--timeout", default="15m")
    u.add_argument("--batch-size", type=int, default=40)
    u.add_argument("--max-batches", type=int, default=1000)
    u.add_argument("--max-failures", type=int, default=2)
    u.add_argument("--max-rank", type=int, default=0, help="0 means every unit")
    u.add_argument(
        "--only-requested",
        action="store_true",
        help="only units the owner asked for by hand, whatever their rank",
    )
    w = sub.add_parser("write-cards")
    w.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    w.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    w.add_argument("--model", default=DEFAULT_MODEL)
    w.add_argument("--timeout", default="15m")
    w.add_argument("--count", type=int, default=8, help="sentences to ask for per word")
    w.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="LEMMA:KIND",
        help="write for these words instead of reading the mine report; repeatable",
    )
    args = parser.parse_args(argv)
    if not AGY.exists():
        print(f"agy not found at {AGY}")
        return 1
    jobs = {
        "contexts": job_contexts,
        "glosses": job_glosses,
        "unit-glosses": job_unit_glosses,
        "write-cards": job_write_cards,
    }
    return jobs[args.job](args)


if __name__ == "__main__":
    sys.exit(main())
