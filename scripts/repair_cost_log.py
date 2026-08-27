"""Repair an existing `cost_log.jsonl` against Google's per-day billing export.

The owner's August log is wrong on disk and he asked for it corrected. It is
wrong in two independent ways, and they need two independent corrections
because only one of them can actually be recovered.

## 1. Repricing (recoverable)

Every row written before `mode` existed was priced by an `_estimate_cost`
that applied Google's 0.5x batch discount unconditionally, including to the
paid-lane synchronous calls a `forbid_batch=True` pilot makes. Roughly $1.90
of a $5.04 bill logged as $1.99 is that halving.

The rows themselves carry no `mode`, so the mode has to come from outside.
The billing export supplies it: Google prices `gemini 3.7 flash text` and
`gemini 3.7 flash text batch` as separate SKUs, so a day whose bill shows
only non-batch SKUs was a sync day, a day whose bill shows only batch SKUs
was a batch day, and a day showing both cannot be resolved this way at all.

That last case is left alone, with `mode` still `None`, and counted in the
report. A day the evidence cannot decide is not a day to guess about; a
guessed mode is a 2x error in either direction, which is the size of the bug
being repaired.

Two rows are decided without consulting the bill, because the client
guarantees them: a free-lane row is always synchronous (`_call_transport`
rejects any other mode on that lane) and a cache row reached no transport at
all. Neither costs anything, so neither moves the total; setting their mode
just stops them showing up as unresolved.

## 2. Reconciliation (not recoverable, so it is labelled)

Repricing still does not reach $5.04, and it never can. Roughly $1.58 is
attempts that billed and were never logged: retried calls wrote no row until
`GeminiLlmClient._log_failed_attempt` existed, and a failed batch job that
Google had already partly billed wrote nothing at all -- three days in the
August export have real batch charges against days with zero rows in the log.
The tokens for those attempts are not in the log, not in the exception that
killed them, and not derivable from anything this repo holds.

So the difference is appended as one explicit adjustment row per day, with
`purpose="billing_reconciliation"`, `outcome="adjustment"` and a
`billing-reconciliation` model marker. It carries dollars and zero tokens,
which is exactly the shape of what is known: Google's charge is a fact, the
token split behind it is not. After this the month-to-date total the spend
ceiling reads equals the real bill, and no row in the file can be mistaken
for a call that did not happen.

## Safety

- `--dry-run` prints the full report and writes nothing.
- The real write goes to a temp file in the log's own directory, is fsynced,
  and is swapped in with a single `os.replace` (the pattern in
  `scripts/build_translations.py`), so the log is either wholly the old
  content or wholly the new one at every instant.
- Running twice is refused: an adjustment row already present means the file
  has been repaired, and a second pass would reprice nothing but would append
  a second set of adjustments on top of the first, doubling them.

## Usage

    uv run python -m scripts.repair_cost_log \\
        --billing-csv path/to/google_daily_august.csv \\
        --cost-log .cache/cost_log.jsonl --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from src.llm.client import DEFAULT_COST_LOG_PATH, CostLogRow, GeminiLlmClient

from scripts.reconcile_cost_log import BillingExport, load_billing_csv, load_cost_log

#: What an appended reconciliation row carries, so it is unmistakably an
#: adjustment and not a call. All three markers are set together on purpose:
#: ``outcome`` is what the idempotency check reads, ``purpose`` is what a
#: human scanning the file sees first, and the model marker makes the row
#: fail any lookup that expects a real Gemini model id.
ADJUSTMENT_PURPOSE = "billing_reconciliation"
ADJUSTMENT_MODEL = "billing-reconciliation"

#: Below this, a day's difference is rounding in Google's own export (it
#: reports six decimal places) rather than a real gap, and appending a row for
#: it would be noise.
ADJUSTMENT_EPSILON_USD = 0.000_001

DayVerdict = Literal["sync", "batch", "ambiguous"]


class DayClassification(BaseModel):
    """What the bill says a day's transport mode was."""

    model_config = ConfigDict(frozen=True)
    day: date
    verdict: DayVerdict
    billed_cost_usd: float


class RepairReport(BaseModel):
    """Everything the repair did or declined to do.

    Built whether or not the file is written, so ``--dry-run`` and a real run
    report identically and the only difference between them is the write.
    """

    model_config = ConfigDict(frozen=True)
    classifications: list[DayClassification]
    repriced_rows: int
    repriced_cost_delta_usd: float
    #: Rows left with ``mode=None`` because nothing could decide them: the
    #: bill showed both SKU families for that day, or the day is not in the
    #: bill at all.
    unresolved_rows: int
    unresolved_days: list[date]
    #: Rows whose mode needed no evidence: the free lane is always sync, a
    #: cache hit reached no transport.
    invariant_rows: int
    adjustments: list[CostLogRow]
    total_before_usd: float
    total_after_usd: float
    billed_total_usd: float
    #: Days present in the log but absent from the bill. Nothing can be
    #: adjusted for them, so they are reported instead of quietly skipped.
    days_missing_from_bill: list[date]


class AlreadyRepairedError(RuntimeError):
    """Raised when the log already contains reconciliation adjustment rows."""


def classify_days(billing: BillingExport) -> dict[date, DayClassification]:
    """Decide each billed day's transport mode from the SKUs it contains."""
    modes: dict[date, set[str]] = defaultdict(set)
    cost: dict[date, float] = defaultdict(float)
    for usage in billing.usage:
        modes[usage.day].add(usage.mode)
        cost[usage.day] += usage.cost_usd

    classified: dict[date, DayClassification] = {}
    for day, day_modes in modes.items():
        if day_modes == {"sync"}:
            verdict: DayVerdict = "sync"
        elif day_modes == {"batch"}:
            verdict = "batch"
        else:
            verdict = "ambiguous"
        classified[day] = DayClassification(
            day=day, verdict=verdict, billed_cost_usd=round(cost[day], 6)
        )
    return classified


def _repriced(row: CostLogRow, mode: Literal["sync", "batch", "cache"]) -> CostLogRow:
    """Return ``row`` with its mode set and its cost recomputed.

    Pricing goes through ``GeminiLlmClient._estimate_cost``, the client's own
    implementation, rather than a copy of the price table. Two pricing
    implementations is exactly how the 0.5 batch discount came to be applied
    to calls that never went through batch, and a repair script that repeats
    that mistake in reverse would be worse than no repair.
    """
    return row.model_copy(
        update={
            "mode": mode,
            "cost_usd": GeminiLlmClient._estimate_cost(
                row.model, row.prompt_tokens, row.completion_tokens, row.lane, mode
            ),
        }
    )


def _adjustment_row(day: date, difference_usd: float) -> CostLogRow:
    """One day's reconciliation row.

    Timestamped at midnight UTC on the billing day so it lands in the same
    calendar month as the charge it represents, which is the granularity
    ``GeminiLlmClient.get_month_to_date_spend`` and the spend ceiling actually
    use. Zero tokens, deliberately: Google's dollar charge is a fact, the
    token split behind it is not, and this file has already been damaged once
    by a number that was inferred rather than measured.
    """
    return CostLogRow(
        timestamp=datetime.combine(day, time(0, 0), tzinfo=UTC),
        model=ADJUSTMENT_MODEL,
        lane="paid",
        mode=None,
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=round(difference_usd, 6),
        purpose=ADJUSTMENT_PURPOSE,
        outcome="adjustment",
        attempt=1,
        call_id=None,
    )


def already_repaired(rows: list[CostLogRow]) -> bool:
    """True if this log already carries reconciliation adjustments."""
    return any(row.outcome == "adjustment" or row.purpose == ADJUSTMENT_PURPOSE for row in rows)


def repair(rows: list[CostLogRow], billing: BillingExport) -> tuple[list[CostLogRow], RepairReport]:
    """Reprice what the bill can decide, then append one adjustment per day.

    Pure: takes rows, returns rows. Nothing here touches the filesystem, so
    ``--dry-run`` and a real run compute the identical result and differ only
    in whether the caller writes it.
    """
    if already_repaired(rows):
        raise AlreadyRepairedError(
            "This cost log already contains billing_reconciliation adjustment rows, "
            "so it has been repaired before. Repricing again would be a no-op, but "
            "appending a second set of adjustments would double them. Refusing."
        )

    classified = classify_days(billing)
    repaired: list[CostLogRow] = []
    repriced = 0
    invariant = 0
    delta = 0.0
    unresolved = 0
    unresolved_days: set[date] = set()

    for row in rows:
        if row.mode is not None:
            repaired.append(row)
            continue
        if row.lane == "cache":
            # A cache hit reached no transport at all.
            repaired.append(_repriced(row, "cache"))
            invariant += 1
            continue
        if row.lane == "free":
            # CLAUDE.md 9 and ``_call_transport``: the free lane is always
            # synchronous. No billing evidence needed, and no cost either way.
            repaired.append(_repriced(row, "sync"))
            invariant += 1
            continue

        day = row.timestamp.date()
        classification = classified.get(day)
        if classification is None or classification.verdict == "ambiguous":
            repaired.append(row)
            unresolved += 1
            unresolved_days.add(day)
            continue
        new_row = _repriced(row, classification.verdict)
        delta += new_row.cost_usd - row.cost_usd
        repriced += 1
        repaired.append(new_row)

    total_before = round(sum(row.cost_usd for row in rows), 6)
    repriced_total = round(sum(row.cost_usd for row in repaired), 6)

    logged_per_day: dict[date, float] = defaultdict(float)
    for row in repaired:
        logged_per_day[row.timestamp.date()] += row.cost_usd

    adjustments: list[CostLogRow] = []
    for day in sorted(classified):
        difference = classified[day].billed_cost_usd - logged_per_day.get(day, 0.0)
        if abs(difference) < ADJUSTMENT_EPSILON_USD:
            continue
        adjustments.append(_adjustment_row(day, difference))

    logged_days = {row.timestamp.date() for row in rows}
    missing = sorted(logged_days - set(classified))

    report = RepairReport(
        classifications=[classified[day] for day in sorted(classified)],
        repriced_rows=repriced,
        repriced_cost_delta_usd=round(delta, 6),
        unresolved_rows=unresolved,
        unresolved_days=sorted(unresolved_days),
        invariant_rows=invariant,
        adjustments=adjustments,
        total_before_usd=total_before,
        total_after_usd=round(repriced_total + sum(a.cost_usd for a in adjustments), 6),
        billed_total_usd=billing.total_cost_usd,
        days_missing_from_bill=missing,
    )
    return repaired + adjustments, report


def write_cost_log_atomic(path: Path, rows: list[CostLogRow]) -> None:
    """Temp file, fsync, single ``os.replace``.

    Same pattern as ``scripts/build_translations._write_store_atomic``, for
    the same reason: the target path is only ever replaced by a fully written
    file, so a process killed mid-repair leaves the original log untouched
    rather than a half-rewritten audit record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(row.model_dump_json())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def format_report(report: RepairReport, *, dry_run: bool) -> str:
    lines: list[str] = []
    lines.append("DRY RUN -- nothing written." if dry_run else "Repair applied.")
    lines.append("")
    lines.append("Day classification from the billing export:")
    for classification in report.classifications:
        lines.append(
            f"  {classification.day.isoformat()}  {classification.verdict:<10}"
            f"${classification.billed_cost_usd:.6f}"
        )
    lines.append("")
    lines.append(
        f"repriced rows:      {report.repriced_rows} "
        f"(cost change ${report.repriced_cost_delta_usd:+.6f})"
    )
    lines.append(
        f"mode set by invariant: {report.invariant_rows} "
        "(free lane is always sync, cache reached no transport; no cost change)"
    )
    lines.append(f"left alone:         {report.unresolved_rows} rows with mode still unknown")
    if report.unresolved_days:
        lines.append(
            "  undecidable days: "
            + ", ".join(day.isoformat() for day in report.unresolved_days)
            + "  (the bill shows both batch and non-batch SKUs, or the day is not billed)"
        )
    if report.days_missing_from_bill:
        lines.append(
            "  days in the log but not in the bill: "
            + ", ".join(day.isoformat() for day in report.days_missing_from_bill)
        )
    lines.append("")
    lines.append(f"adjustment rows to append: {len(report.adjustments)}")
    for row in report.adjustments:
        lines.append(f"  {row.timestamp.date().isoformat()}  ${row.cost_usd:+.6f}")
    lines.append("")
    lines.append(f"log total before:   ${report.total_before_usd:.6f}")
    lines.append(f"log total after:    ${report.total_after_usd:.6f}")
    lines.append(f"Google's bill:      ${report.billed_total_usd:.6f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reprice and reconcile a cost log against a Google billing export."
    )
    parser.add_argument("--billing-csv", type=Path, required=True)
    parser.add_argument("--cost-log", type=Path, default=DEFAULT_COST_LOG_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change and write nothing.",
    )
    args = parser.parse_args(argv)

    if not args.billing_csv.exists():
        parser.error(f"billing export not found: {args.billing_csv}")
    if not args.cost_log.exists():
        parser.error(f"cost log not found: {args.cost_log}")

    rows = load_cost_log(args.cost_log)
    try:
        repaired, report = repair(rows, load_billing_csv(args.billing_csv))
    except AlreadyRepairedError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(format_report(report, dry_run=args.dry_run))
    if not args.dry_run:
        write_cost_log_atomic(args.cost_log, repaired)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
