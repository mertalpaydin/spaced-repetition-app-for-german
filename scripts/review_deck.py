"""Review the deck under the zero-defect policy: write batches, run a reviewer,
apply findings.

Three subcommands, all deterministic except the reviewer itself:

    batches   write plain-text review batches of the cards and units not yet
              in docs/audits/phase-1-review/reviewed-*-round-*.txt
    gemini    run the Gemini reviewer (the `agy` CLI, gemini-executor skill)
              over every batch in a directory that has no findings file yet
    apply     turn a findings directory into curated-list entries with the
              reviewer's reason, and record the round in docs/audits/

The Claude reviewer is driven from the agent session itself (one agent per
batch); it reads the same batch files and writes the same findings shape.
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.contracts import PhraseCard, PhraseUnit
from src.phrases.export import DEFAULT_DECK_DIR, load_deck

AUDIT_DIR = Path("docs/audits/phase-1-review")
PHRASES_DIR = Path("data/phrases")
AGY = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"

CARD_PROMPT = (
    "You are reviewing German phrase-learning exercises for a zero-defect policy. Use exactly "
    "two tools: read the file {batch}, then write the file {out}. Do not run shell commands, do "
    "not list directories, do not read any other file. The file {batch} is tab-separated, one "
    "card per line: card_id, kind, unit "
    "display with optional case, German sentence with the unit's tokens in [brackets] = the "
    "gaps the learner must type, English gloss or '(no gloss yet)'). A learner sees the "
    "sentence with the bracketed tokens blanked plus the English gloss, types the missing "
    "tokens, then sees the unit display (e.g. 'warten auf +Akk'). Judge every card. Report a "
    "finding ONLY when one of these holds: WRONG_UNIT (the bracketed tokens do not "
    "instantiate the displayed unit in this sentence, e.g. a temporal 'an' bracketed as a "
    "verb complement, a separable prefix that is really a preposition, a noun that is not "
    "the verb's object, a reflexive pronoun that is a plain object, a different verb such as "
    "'zugrunde richten' bracketed as 'sich richten'); WRONG_GAPS (a unit token is missing "
    "from or extra in the brackets); BAD_SENTENCE (not a self-contained, grammatical, "
    "natural German sentence: fragment, typo, obsolete spelling in a gap, junk, Swiss "
    "spelling, missing context, offensive); BAD_GLOSS (the English does not translate the "
    "sentence or misleads about the gaps; skip '(no gloss yet)' cards); WRONG_CASE "
    "(+Akk/+Dat/+Gen wrong for this verb and meaning); BAD_UNIT (the unit is not a real, "
    "useful German phrase for an A1-B2 learner; report once per unit). Be strict but do not "
    "invent problems: a plain, correct card gets no finding. Collect all findings first, "
    "then write them as JSON lines to {out} in this workspace with exactly these fields per "
    'line: {{"card_id": "...", "unit": "...", "category": '
    '"WRONG_UNIT|WRONG_GAPS|BAD_SENTENCE|BAD_GLOSS|WRONG_CASE|BAD_UNIT", "severity": '
    '"high|low", "note": "one short sentence", "action": '
    '"drop_card|drop_unit|fix_case:Akk|fix_case:Dat|fix_case:Gen|note"}}. Write an empty '
    "file if there are no findings. Then reply with only the number of cards read and the "
    "number of findings."
)

UNIT_PROMPT = (
    "You are reviewing the phrase inventory of a German phrase-learning deck (A1-B2 "
    "learners) for a zero-defect policy. Use exactly two tools: read the file {batch}, then "
    "write the file {out}. Do not run shell commands, do not list directories, do not read any "
    "other file. Read {batch} in this workspace (tab-separated, one "
    "unit per line: unit_id, kind, rank=, n=corpus sentence count, display form with "
    "optional +case, cefr=, trivial=, gloss=). Kinds: verb_prep, reflexive_verb, "
    "separable_verb, noun_verb (noun + verb collocation or a Funktionsverbgefuege), "
    "adj_noun, connector, two_part_connector, idiom. Judge every unit. Report a finding ONLY "
    "when: NOT_A_PHRASE (not a real German multiword unit worth teaching: a free "
    "combination, a parser artefact, a name, news-only jargon); BAD_CITATION (the display "
    "form is wrong or ungrammatical as a citation form); WRONG_CASE (the +case is wrong for "
    "the usual meaning, or missing where the verb fixes it); WRONG_CEFR (off by two levels "
    "or more, or '-' for a very common A1/A2 phrase); DUPLICATE (duplicates another unit in "
    "the file; name it); WRONG_GLOSS. Do not flag a unit merely for being rare or slightly "
    "formal. Collect all findings first, then write them as JSON lines to {out} in this "
    'workspace with exactly these fields per line: {{"unit_id": "...", "unit": '
    '"<display>", "category": '
    '"NOT_A_PHRASE|BAD_CITATION|WRONG_CASE|WRONG_CEFR|DUPLICATE|WRONG_GLOSS", "severity": '
    '"high|low", "note": "one short sentence", "action": '
    '"drop_unit|fix_case:Akk|fix_case:Dat|fix_case:Gen|fix_display:<new display>|'
    'fix_cefr:<A1|A2|B1|B2>|note"}}. Write an empty file if there are no findings. Then '
    "reply with only the number of units read and the number of findings."
)


# -- batches -------------------------------------------------------------------


def _reviewed_ids(prefix: str) -> set[str]:
    ids: set[str] = set()
    for path in AUDIT_DIR.glob(f"reviewed-{prefix}-ids-round-*.txt"):
        ids |= set(path.read_text(encoding="utf-8").split())
    return ids


def render_gaps(card: PhraseCard) -> str:
    text = card.sentence_de
    out, last = "", 0
    for gap in card.gaps:
        out += text[last : gap.start] + "[" + gap.answer + "]"
        last = gap.end
    return out + text[last:]


def card_line(card: PhraseCard, unit: PhraseUnit) -> str:
    case = f" +{unit.case}" if unit.case else ""
    return (
        f"{card.card_id}\t{unit.kind}\t{unit.display_de}{case}\t{render_gaps(card)}"
        f"\t{card.gloss_en or '(no gloss yet)'}"
    )


def unit_line(unit: PhraseUnit) -> str:
    case = f" +{unit.case}" if unit.case else ""
    return (
        f"{unit.unit_id}\t{unit.kind}\trank={unit.rank}\tn={unit.sentence_count}"
        f"\t{unit.display_de}{case}\tcefr={unit.cefr or '-'}\ttrivial={int(unit.trivial)}"
        f"\tgloss={unit.gloss_en or '-'}"
    )


def write_batches(
    out_dir: Path,
    *,
    deck_dir: Path,
    everything: bool,
    card_batch: int,
    unit_batch: int,
    gloss_source: str | None = None,
    max_rank: int | None = None,
) -> dict[str, int]:
    """``gloss_source`` selects the gloss review: every card whose gloss came
    from that source, whatever the reviewed-id lists say (those record that
    the sentence was read, not that its English was), and no units.

    ``max_rank`` stops at a rank, so a deck far larger than a year of
    learning is reviewed from the top down in steps rather than in one pass
    (owner, 2026-09-21)."""
    manifest, units, cards = load_deck(deck_dir)
    by_unit = {u.unit_id: u for u in units}
    reviewed_cards = set() if everything else _reviewed_ids("card")
    reviewed_units = set() if everything else _reviewed_ids("unit")
    if gloss_source is not None:
        chosen = (c for c in cards if c.gloss_source == gloss_source)
        reviewed_units = {u.unit_id for u in units}
    else:
        chosen = (c for c in cards if c.card_id not in reviewed_cards)
    if max_rank is not None:
        chosen = (c for c in chosen if by_unit[c.unit_id].rank <= max_rank)
    new_cards = sorted(chosen, key=lambda c: (by_unit[c.unit_id].rank, c.card_id))
    new_units = [u for u in units if u.unit_id not in reviewed_units]
    if max_rank is not None:
        new_units = [u for u in new_units if u.rank <= max_rank]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "findings").mkdir(exist_ok=True)
    for stale in out_dir.glob("*.txt"):
        stale.unlink()
    lines = [card_line(c, by_unit[c.unit_id]) for c in new_cards]
    for i in range(0, len(lines), card_batch):
        (out_dir / f"cards_{i // card_batch:03d}.txt").write_text(
            "\n".join(lines[i : i + card_batch]) + "\n", encoding="utf-8"
        )
    ulines = [unit_line(u) for u in new_units]
    for i in range(0, len(ulines), unit_batch):
        (out_dir / f"units_{i // unit_batch:02d}.txt").write_text(
            "\n".join(ulines[i : i + unit_batch]) + "\n", encoding="utf-8"
        )
    (out_dir / "deck_version.txt").write_text(manifest.deck_version + "\n", encoding="utf-8")
    return {
        "deck_version_len": len(manifest.deck_version),
        "cards": len(new_cards),
        "card_batches": (len(lines) + card_batch - 1) // card_batch,
        "units": len(new_units),
        "unit_batches": (len(ulines) + unit_batch - 1) // unit_batch,
    }


# -- gemini ----------------------------------------------------------------------


def run_gemini_batch(batch_dir: Path, batch: Path, *, model: str, timeout: str) -> str:
    out = f"findings/{batch.stem}.jsonl"
    prompt = (CARD_PROMPT if batch.name.startswith("cards") else UNIT_PROMPT).format(
        batch=batch.name, out=out
    )
    cmd = [
        str(AGY),
        "-p",
        prompt,
        "--new-project",
        "--add-dir",
        str(batch_dir.resolve()),
        "--model",
        model,
        "--output-format",
        "json",
        "--print-timeout",
        timeout,
        "--dangerously-skip-permissions",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return f"{batch.name}: no JSON from agy ({result.stdout[-200:]!r})"
    denied = payload.get("denied_actions") or []
    written = (batch_dir / out).exists()
    return (
        f"{batch.name}: {payload.get('status')} {payload.get('response', '').strip()!r}"
        f"{' DENIED ' + str(denied) if denied else ''}{'' if written else ' (no findings file)'}"
    )


def run_gemini(batch_dir: Path, *, model: str, timeout: str, workers: int) -> None:
    findings = batch_dir / "findings"
    findings.mkdir(exist_ok=True)
    todo = [
        b
        for b in sorted(batch_dir.glob("*.txt"))
        if b.name != "deck_version.txt" and not (findings / f"{b.stem}.jsonl").exists()
    ]
    print(f"gemini: {len(todo)} batch(es) to review with {model}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for line in pool.map(
            lambda b: run_gemini_batch(batch_dir, b, model=model, timeout=timeout), todo
        ):
            print("  ", line, flush=True)


# -- apply -----------------------------------------------------------------------


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ids_in_batches(batch_dir: Path, pattern: str) -> set[str]:
    """The ids a reviewer was actually shown: the first column of every batch
    file the round was written from."""
    return {
        line.split("\t")[0]
        for path in batch_dir.glob(pattern)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def apply_findings(findings_dir: Path, *, round_label: str, deck_dir: Path) -> dict[str, int]:
    manifest, units, cards = load_deck(deck_dir)
    by_id = {u.unit_id: u for u in units}
    card_by_id = {c.card_id: c for c in cards}
    display_to_keys: dict[str, set[str]] = collections.defaultdict(set)
    for u in units:
        display_to_keys[u.display_de].add(u.lemma_key)
        if u.case:
            display_to_keys[f"{u.display_de} +{u.case}"].add(u.lemma_key)

    def keys_for(display: str) -> set[str]:
        display = display.strip()
        if display in display_to_keys:
            return display_to_keys[display]
        return display_to_keys.get(re.sub(r"\s*\+\s*(Akk|Dat|Gen)$", "", display), set())

    card_rows: list[dict[str, str]] = []
    unit_rows: list[dict[str, str]] = []
    for path in sorted(findings_dir.glob("*.jsonl")):
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        (card_rows if path.name.startswith("cards") else unit_rows).extend(rows)

    drop_units: dict[str, str] = {}
    drop_cards: dict[str, str] = {}
    overrides: dict[str, dict[str, str]] = collections.defaultdict(dict)
    unresolved = 0
    for r in card_rows:
        card = card_by_id.get(r["card_id"])
        unit_keys = {by_id[card.unit_id].lemma_key} if card else keys_for(r["unit"])
        reason = f"{r['category']}: {r['note']}"
        if r["category"] == "BAD_UNIT" or r["action"] == "drop_unit":
            unresolved += not unit_keys
            for k in unit_keys:
                drop_units.setdefault(k, reason)
        elif r["action"].startswith("fix_case:"):
            for k in unit_keys:
                overrides[k]["case"] = r["action"].split(":", 1)[1]
        elif r["category"] == "WRONG_CASE" and r["action"] == "note":
            continue
        else:
            drop_cards.setdefault(r["card_id"], reason)
    for r in unit_rows:
        unit = by_id.get(r["unit_id"])
        if unit is None:
            unresolved += 1
            continue
        k, act = unit.lemma_key, r["action"]
        reason = f"{r['category']}: {r['note']}"
        if act == "drop_unit" or r["category"] in {"NOT_A_PHRASE", "DUPLICATE"}:
            drop_units.setdefault(k, reason)
        elif act.startswith(("fix_case:", "fix_display:", "fix_cefr:")):
            field, _, value = act.partition(":")
            overrides[k][field[4:]] = value.strip()
    for k in list(overrides):
        if k in drop_units:
            del overrides[k]

    exclude = PHRASES_DIR / "exclude.yaml"
    existing = {
        line[2:].split("  #")[0].strip().strip('"')
        for line in exclude.read_text(encoding="utf-8").splitlines()
        if line.startswith("- ")
    }
    new_drops = {k: v for k, v in drop_units.items() if k not in existing}
    with exclude.open("a", encoding="utf-8") as h:
        h.write(f"# Review round {round_label}: units the reviewer rejected.\n")
        for k, why in sorted(new_drops.items()):
            h.write(f"- {_yaml_str(k)}  # {why[:110]}\n")
    with (PHRASES_DIR / "excluded_cards.yaml").open("a", encoding="utf-8") as h:
        h.write(f"# Review round {round_label}.\n")
        for cid, why in sorted(drop_cards.items()):
            card = card_by_id.get(cid)
            if card and by_id[card.unit_id].lemma_key in drop_units:
                continue
            h.write(f"- {cid}  # {why[:110]}\n")

    ov = PHRASES_DIR / "unit_overrides.yaml"
    key_re = re.compile(r'- \{key: ("(?:[^"\\]|\\.)*")')
    merged: dict[str, str] = {}
    if ov.exists():
        for line in ov.read_text(encoding="utf-8").splitlines():
            m = key_re.match(line)
            if m:
                merged[m.group(1)] = line
    for k, fields in overrides.items():
        current = merged.get(_yaml_str(k))
        if current:
            for field, value in re.findall(
                r'(case|display|cefr|gloss_en): ("(?:[^"\\]|\\.)*")', current
            ):
                fields.setdefault(field, json.loads(value))
        parts = ", ".join(f"{a}: {_yaml_str(b)}" for a, b in sorted(fields.items()))
        merged[_yaml_str(k)] = f"- {{key: {_yaml_str(k)}, {parts}}}"
    ov.write_text(
        "\n".join(
            [
                "# Reviewer corrections to units: case, citation form, CEFR level. Applied",
                "# after the unit decision; the key is the miner's lowercase lemma key.",
            ]
            + [merged[k] for k in sorted(merged)]
        )
        + "\n",
        encoding="utf-8",
    )

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with (AUDIT_DIR / f"findings-round-{round_label}.jsonl").open("w", encoding="utf-8") as h:
        for r in card_rows + unit_rows:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")
    # The ledger records what a reviewer actually read, which is the batch
    # files, not the whole deck. Writing every card id was harmless while
    # every round covered everything; with --max-rank a round covers the top
    # of the deck only, and claiming the tail was reviewed both hides the
    # unreviewed cards and empties the next round's batches.
    reviewed_cards = _ids_in_batches(findings_dir.parent, "cards_*.txt")
    if reviewed_cards:
        (AUDIT_DIR / f"reviewed-card-ids-round-{round_label}.txt").write_text(
            "".join(c + "\n" for c in sorted(reviewed_cards)), encoding="utf-8"
        )
    reviewed_units = _ids_in_batches(findings_dir.parent, "units_*.txt")
    if reviewed_units:
        (AUDIT_DIR / f"reviewed-unit-ids-round-{round_label}.txt").write_text(
            "".join(u + "\n" for u in sorted(reviewed_units)), encoding="utf-8"
        )
    return {
        "card_findings": len(card_rows),
        "unit_findings": len(unit_rows),
        "drop_units": len(new_drops),
        "drop_cards": len(drop_cards),
        "overrides": len(overrides),
        "unresolved": unresolved,
    }


# -- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("batches")
    b.add_argument("out_dir", type=Path)
    b.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    b.add_argument("--everything", action="store_true", help="ignore the reviewed-id lists")
    b.add_argument(
        "--gloss-source",
        default=None,
        help="gloss review: every card glossed by this source (e.g. gemini), no units",
    )
    b.add_argument("--card-batch", type=int, default=600)
    b.add_argument("--max-rank", type=int, default=None, help="only units at or above this rank")
    b.add_argument("--unit-batch", type=int, default=700)
    g = sub.add_parser("gemini")
    g.add_argument("batch_dir", type=Path)
    g.add_argument("--model", default="gemini-3.1-pro-high")
    g.add_argument("--timeout", default="15m")
    g.add_argument("--workers", type=int, default=3)
    a = sub.add_parser("apply")
    a.add_argument("findings_dir", type=Path)
    a.add_argument("--round", required=True, help='label, e.g. "9-gemini"')
    a.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    args = parser.parse_args(argv)
    if args.command == "batches":
        stats = write_batches(
            args.out_dir,
            deck_dir=args.deck,
            everything=args.everything,
            gloss_source=args.gloss_source,
            max_rank=args.max_rank,
            card_batch=args.card_batch,
            unit_batch=args.unit_batch,
        )
    elif args.command == "gemini":
        if not AGY.exists():
            print(f"agy not found at {AGY}")
            return 1
        run_gemini(args.batch_dir, model=args.model, timeout=args.timeout, workers=args.workers)
        return 0
    else:
        stats = apply_findings(args.findings_dir, round_label=args.round, deck_dir=args.deck)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
