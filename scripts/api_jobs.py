"""Deck jobs through the Gemini API (``src/llm/client.py``), the counterpart
of ``agy_jobs.py`` for when the agent quota is spent.

    uv run python scripts/api_jobs.py glosses --approved-by-owner
    uv run python scripts/api_jobs.py review <batch-dir> --approved-by-owner

``glosses`` translates the sentences the deck still wants
(``build/wanted_carriers.txt``) 50 a call on ``MODEL_GENERATE`` and stores
them with ``source="gemini"`` after the same shape checks as the agent job.
``review`` reads the card batches ``review_deck.py batches`` wrote, asks
``MODEL_VERIFY`` for findings in the same JSON-lines shape the agent
reviewer writes, and stores them as ``findings/<batch>.jsonl`` so
``review_deck.py apply`` works unchanged.

Both refuse without ``--approved-by-owner``; the owner approves each run in
chat on top. The free lane is used first, the paid lane takes the overflow,
and every call is a cost-log row.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.contracts import MODEL_GENERATE, MODEL_VERIFY, PURPOSE_DECK_REVIEW, PURPOSE_SENTENCE_GLOSS
from src.llm.env import client_from_env, load_env_file

from scripts.agy_jobs import (
    DEFAULT_BUILD_DIR,
    gloss_reject_reason,
    read_wanted,
    write_store_with_retry,
)
from scripts.build_translations import DEFAULT_STORE_PATH, TranslationRecord, _load_store

GLOSS_PROMPT_VERSION = 1
REVIEW_PROMPT_VERSION = 1
FINDING_CATEGORIES = {
    "WRONG_UNIT",
    "WRONG_GAPS",
    "BAD_SENTENCE",
    "BAD_GLOSS",
    "WRONG_CASE",
    "BAD_UNIT",
}


class ManyClient(Protocol):
    def generate_many(
        self,
        prompts: list[str],
        model: str = ...,
        purpose: str = ...,
        *,
        cache_namespace: str | None = ...,
    ) -> list[str]: ...


# -- glosses -------------------------------------------------------------------


def gloss_prompt(batch: list[str]) -> str:
    lines = "\n".join(
        json.dumps({"id": str(i), "de": t}, ensure_ascii=False) for i, t in enumerate(batch)
    )
    return (
        "Translate each German sentence below into natural, faithful English, keeping the "
        "sentence structure where English allows it and translating every content word; keep "
        "names and numbers as they are. Answer with JSON lines only, one per input line, same "
        'ids, exactly these fields: {"id": "...", "en": "..."}. No commentary.\n\n' + lines
    )


def parse_jsonl_objects(raw: str) -> list[dict[str, object]]:
    """Every JSON object in the reply, one per line or inside a JSON array;
    junk lines are skipped, never fatal."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    rows: list[dict[str, object]] = []
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def gloss_sentences(
    todo: list[str],
    *,
    client: ManyClient,
    batch_size: int,
    max_calls: int,
    now: datetime,
    model: str = MODEL_GENERATE,
) -> tuple[dict[str, TranslationRecord], dict[str, int]]:
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)][:max_calls]
    replies = client.generate_many(
        [gloss_prompt(b) for b in batches],
        model=model,
        purpose=PURPOSE_SENTENCE_GLOSS,
        cache_namespace=f"sentence_gloss_v{GLOSS_PROMPT_VERSION}",
    )
    added: dict[str, TranslationRecord] = {}
    rejected: dict[str, int] = {}
    for batch, raw in zip(batches, replies, strict=True):
        by_id = {str(r.get("id", "")): str(r.get("en", "")) for r in parse_jsonl_objects(raw)}
        for i, text in enumerate(batch):
            english = by_id.get(str(i), "")
            reason = gloss_reject_reason(text, english) if english else "missing"
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            added[text] = TranslationRecord(
                german=text, english=english.strip(), source="gemini", written_at=now
            )
    return added, rejected


def job_glosses(args: argparse.Namespace) -> int:
    wanted = read_wanted(args.wanted)
    store = _load_store(args.store)
    todo: list[str] = []
    seen: set[str] = set()
    for _, _, text in wanted:
        if (text in store and store[text].source != "tatoeba") or text in seen:
            continue
        seen.add(text)
        todo.append(text)
    calls = min(-(-len(todo) // args.batch_size), args.max_calls)
    print(f"glosses: {len(wanted):,} wanted, {len(todo):,} without a gloss, {calls} call(s)")
    if not todo:
        return 0
    if not args.approved_by_owner:
        print(f"glosses: REFUSED. {calls} call(s) on {MODEL_GENERATE} need --approved-by-owner.")
        return 2
    load_env_file()
    client = client_from_env()
    if client is None:
        print("glosses: no Gemini key configured")
        return 2
    added, rejected = gloss_sentences(
        todo,
        client=client,
        batch_size=args.batch_size,
        max_calls=args.max_calls,
        now=datetime.now(UTC),
    )
    if added:
        store.update(added)
        write_store_with_retry(args.store, store)
    print(f"glosses: {len(added)} stored, rejected {rejected}, store now {len(store):,} records")
    return 0


# -- review --------------------------------------------------------------------

REVIEW_PROMPT = (
    "You are reviewing German phrase-learning exercises for a zero-defect policy. Below is a "
    "tab-separated list, one card per line: card_id, kind, unit display with optional case, "
    "German sentence with the unit's tokens in [brackets] = the gaps the learner must type, "
    "English gloss or '(no gloss yet)'. A learner sees the sentence with the bracketed tokens "
    "blanked plus the English gloss, types the missing tokens, then sees the unit display "
    "(e.g. 'warten auf +Akk'). Judge every card. Report a finding ONLY when one of these "
    "holds: WRONG_UNIT (the bracketed tokens do not instantiate the displayed unit in this "
    "sentence, e.g. a temporal 'an' bracketed as a verb complement, a separable prefix that is "
    "really a preposition, a noun that is not the verb's object, a reflexive pronoun that is a "
    "plain object, a different verb such as 'zugrunde richten' bracketed as 'sich richten'); "
    "WRONG_GAPS (a unit token is missing from or extra in the brackets); BAD_SENTENCE (not a "
    "self-contained, grammatical, natural German sentence: fragment, typo, obsolete spelling "
    "in a gap, junk, Swiss spelling, missing context, offensive); BAD_GLOSS (the English does "
    "not translate the sentence or misleads about the gaps; skip '(no gloss yet)' cards); "
    "WRONG_CASE (+Akk/+Dat/+Gen wrong for this verb and meaning); BAD_UNIT (the unit is not a "
    "real, useful German phrase for an A1-B2 learner; report once per unit). Be strict but do "
    "not invent problems: a plain, correct card gets no finding. Answer with JSON lines only, "
    'one finding per line, exactly these fields: {"card_id": "...", "unit": "...", '
    '"category": "WRONG_UNIT|WRONG_GAPS|BAD_SENTENCE|BAD_GLOSS|WRONG_CASE|BAD_UNIT", '
    '"severity": "high|low", "note": "one short sentence", "action": '
    '"drop_card|drop_unit|fix_case:Akk|fix_case:Dat|fix_case:Gen|note"}. Answer with the '
    "single word NONE if there are no findings.\n\n"
)


def review_findings(raw: str, card_ids: set[str]) -> list[dict[str, object]]:
    """The reply's findings restricted to cards of the batch and known
    categories, so a hallucinated id never reaches the apply step."""
    out = []
    for row in parse_jsonl_objects(raw):
        if row.get("card_id") in card_ids and row.get("category") in FINDING_CATEGORIES:
            out.append(row)
    return out


def review_batches(
    batch_dir: Path,
    *,
    client: ManyClient,
    max_calls: int,
    model: str = MODEL_VERIFY,
) -> dict[str, int]:
    findings_dir = batch_dir / "findings"
    findings_dir.mkdir(exist_ok=True)
    todo = [
        b
        for b in sorted(batch_dir.glob("cards_*.txt"))
        if not (findings_dir / f"{b.stem}.jsonl").exists()
    ][:max_calls]
    if not todo:
        return {}
    texts = [b.read_text(encoding="utf-8") for b in todo]
    replies = client.generate_many(
        [REVIEW_PROMPT + t for t in texts],
        model=model,
        purpose=PURPOSE_DECK_REVIEW,
        cache_namespace=f"deck_review_v{REVIEW_PROMPT_VERSION}",
    )
    counts: dict[str, int] = {}
    for batch, text, raw in zip(todo, texts, replies, strict=True):
        ids = {line.split("\t", 1)[0] for line in text.splitlines() if line.strip()}
        rows = review_findings(raw, ids)
        (findings_dir / f"{batch.stem}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
        counts[batch.name] = len(rows)
    return counts


def job_review(args: argparse.Namespace) -> int:
    batch_dir: Path = args.batch_dir
    pending = [
        b
        for b in sorted(batch_dir.glob("cards_*.txt"))
        if not (batch_dir / "findings" / f"{b.stem}.jsonl").exists()
    ]
    calls = min(len(pending), args.max_calls)
    print(f"review: {len(pending)} card batch(es) without findings, {calls} call(s)")
    if not pending:
        return 0
    if not args.approved_by_owner:
        print(f"review: REFUSED. {calls} call(s) on {MODEL_VERIFY} need --approved-by-owner.")
        return 2
    load_env_file()
    client = client_from_env()
    if client is None:
        print("review: no Gemini key configured")
        return 2
    counts = review_batches(batch_dir, client=client, max_calls=args.max_calls)
    for name, n in counts.items():
        print(f"  {name}: {n} finding(s)")
    print(f"review: {len(counts)} batch(es), {sum(counts.values())} finding(s)")
    return 0


# -- main ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="job", required=True)
    g = sub.add_parser("glosses")
    g.add_argument("--wanted", type=Path, default=DEFAULT_BUILD_DIR / "wanted_carriers.txt")
    g.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    g.add_argument("--batch-size", type=int, default=50)
    g.add_argument("--max-calls", type=int, default=200)
    g.add_argument("--approved-by-owner", action="store_true")
    r = sub.add_parser("review")
    r.add_argument("batch_dir", type=Path)
    r.add_argument("--max-calls", type=int, default=200)
    r.add_argument("--approved-by-owner", action="store_true")
    args = parser.parse_args(argv)
    return job_glosses(args) if args.job == "glosses" else job_review(args)


if __name__ == "__main__":
    sys.exit(main())
