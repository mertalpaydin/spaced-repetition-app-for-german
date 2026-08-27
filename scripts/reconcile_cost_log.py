"""Compare `cost_log.jsonl` against Google's own per-day billing export.

## Why this script exists

Neither of the two cost bugs found in August was found by reading code.

The `_estimate_cost` bug -- Google's 0.5x batch discount applied
unconditionally, including to the paid-lane synchronous calls a
`forbid_batch=True` pilot makes -- had been sitting in a function whose own
comment asserted the opposite of what the code did, and had been read past
repeatedly. The unlogged-attempt bug was invisible by construction: the log
recorded successes, so a log full of successes looked correct.

Both were found by putting the log next to the bill. And that only happened
because the project owner pushed back on a number I had given him. He had a
$5.04 charge from Google and a cost log claiming $1.99, and he did not accept
the log. The number I was defending was wrong, and the only thing that showed
it was the external record.

So the honest argument for this script is not that it is clever. It is that
the code cannot audit itself: the log is written by the same code whose
correctness is in question, so agreement between the code and its own log is
worth nothing. Google's export is the only independent record, and comparing
against it was a one-off manual reconciliation done under pressure. This is
that comparison, made repeatable, so the next discrepancy is found by running
a script rather than by the owner noticing.

## What it prints

Per day, per model, per transport mode: tokens this log recorded against
tokens Google billed, the difference, and what the difference cost.

Plus, per day, the attempt counts. That column is the explanation for the
first one. A day whose calls were retried is a day where attempts reached
Google, may have been billed, and returned no usage metadata for the log to
record -- see `GeminiLlmClient._log_failed_attempt`. Attempt counts only
exist in rows written after that change; a historical row parses as
`attempt=1, outcome="ok"`, which is what it was, and such days will simply
report no retries rather than a wrong number.

## What is compared against what

**Only paid-lane rows are compared against the bill.** The free lane is an
unbilled Google Cloud project and never appears on the export at all, and a
cache hit reached no transport and carries word-count estimates rather than
real token counts. Including either would inflate the "logged" side against a
bill that does not contain them. Both are counted and reported separately, so
nothing is silently dropped.

Rows written before `mode` existed cannot be matched to a SKU, because
Google's export prices `gemini 3.7 flash text` and `gemini 3.7 flash text
batch` as separate SKUs at different rates. They are grouped under mode
`unknown` and shown as their own line. `scripts/repair_cost_log.py` is what
resolves them.

## Usage

    uv run python -m scripts.reconcile_cost_log \\
        --billing-csv path/to/google_daily_august.csv \\
        --cost-log .cache/cost_log.jsonl

The billing export is a Google Cloud billing CSV. The columns this reads are
`Date`, `SKU description`, `Usage amount` and `Unrounded subtotal ($)`; every
other column is ignored, and rows with no `Date` (the trailing Subtotal / Tax
/ Filtered total lines) are skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from src.llm.client import DEFAULT_COST_LOG_PATH, CostLogRow

#: Mode as this report groups it. ``"unknown"`` is a historical row with no
#: ``mode``; ``"adjustment"`` is a reconciliation row appended by
#: ``scripts/repair_cost_log.py``, which is money without tokens.
ReportMode = Literal["sync", "batch", "unknown", "adjustment"]

#: The SKU description shape Google uses for Gemini text generation, e.g.
#: ``Generate content output token count gemini 3.7 flash text batch``.
_SKU_PREFIX = "Generate content "
_SKU_MIDDLE = " token count "


class BilledUsage(BaseModel):
    """Google's own figures for one (day, model, mode), summed over the input
    and output SKUs that make it up."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())
    day: date
    model: str
    mode: Literal["sync", "batch"]
    input_tokens: int
    output_tokens: int
    cost_usd: float


class BillingExport(BaseModel):
    """A parsed billing CSV, plus every SKU line it could not read.

    ``unparsed_skus`` is not decoration. A SKU shape this parser does not
    recognise is billed money the comparison would otherwise drop on the
    floor, which is the exact failure this whole script exists to catch, so it
    is surfaced rather than skipped.
    """

    model_config = ConfigDict(frozen=True)
    usage: list[BilledUsage]
    unparsed_skus: list[str]
    unparsed_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return round(sum(u.cost_usd for u in self.usage) + self.unparsed_cost_usd, 6)


class ReconciliationRow(BaseModel):
    """One (day, model, mode) line of the comparison."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())
    day: date
    model: str
    mode: ReportMode
    logged_input: int
    logged_output: int
    logged_cost_usd: float
    billed_input: int
    billed_output: int
    billed_cost_usd: float

    @property
    def diff_input(self) -> int:
        return self.billed_input - self.logged_input

    @property
    def diff_output(self) -> int:
        return self.billed_output - self.logged_output

    @property
    def diff_cost_usd(self) -> float:
        """What the gap cost, taken from Google's own dollars.

        Deliberately the bill's number minus the log's number, never a
        re-pricing of the token difference: the bill is the authority here,
        and re-deriving the dollars from a price table would put this script's
        own arithmetic back in the loop it exists to check.
        """
        return round(self.billed_cost_usd - self.logged_cost_usd, 6)

    @property
    def matches(self) -> bool:
        return self.diff_input == 0 and self.diff_output == 0


class DayAttempts(BaseModel):
    """Attempt bookkeeping for one day: the explanation column.

    ``calls`` counts attempts that succeeded. ``retried_calls`` counts those
    that needed more than one attempt to do it. ``failed_calls`` counts call
    ids that have failure rows and no success at all, which is work that
    reached Google and produced nothing -- the shape most likely to have been
    billed and never recorded.
    """

    model_config = ConfigDict(frozen=True)
    day: date
    calls: int
    attempts: int
    retried_calls: int
    failed_attempts: int
    failed_calls: int


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    rows: list[ReconciliationRow]
    attempts: list[DayAttempts]
    unparsed_skus: list[str]
    #: Rows excluded from the token comparison because the bill cannot contain
    #: them: the free lane is an unbilled project, a cache hit reached no
    #: transport. Reported so their exclusion is visible rather than assumed.
    free_lane_rows: int
    cache_rows: int
    logged_total_usd: float
    billed_total_usd: float

    @property
    def difference_usd(self) -> float:
        return round(self.billed_total_usd - self.logged_total_usd, 6)


def parse_sku_description(sku: str) -> tuple[str, Literal["input", "output"], str] | None:
    """Split a Gemini SKU description into ``(model, direction, mode)``.

    ``Generate content output token count gemini 3.7 flash text batch``
    becomes ``("gemini-3.7-flash", "output", "batch")``; the same line without
    the trailing ``batch`` becomes mode ``"sync"``. Returns ``None`` for a SKU
    shape this does not recognise, so the caller can report it rather than
    guess at it.
    """
    text = sku.strip()
    if not text.startswith(_SKU_PREFIX):
        return None
    rest = text[len(_SKU_PREFIX) :]
    direction, separator, remainder = rest.partition(_SKU_MIDDLE)
    if not separator or direction not in ("input", "output"):
        return None
    mode = "sync"
    if remainder.endswith(" batch"):
        mode = "batch"
        remainder = remainder[: -len(" batch")]
    if remainder.endswith(" text"):
        remainder = remainder[: -len(" text")]
    model = remainder.strip().replace(" ", "-")
    if not model:
        return None
    typed_direction: Literal["input", "output"] = "input" if direction == "input" else "output"
    return model, typed_direction, mode


def _parse_amount(raw: str) -> float:
    """Read a billing-CSV number: thousands separators, blanks, empty cells."""
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return 0.0
    return float(cleaned)


def load_billing_csv(path: Path) -> BillingExport:
    """Read Google's per-day billing export into per-(day, model, mode) totals."""
    totals: dict[tuple[date, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    unparsed: dict[str, float] = defaultdict(float)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            day_text = (record.get("Date") or "").strip()
            if not day_text:
                # The trailing Subtotal / Tax / Filtered total lines carry no
                # date. They are a restatement of the rows above, not extra
                # usage, so adding them would double-count the month.
                continue
            day = datetime.strptime(day_text, "%Y-%m-%d").date()
            sku = (record.get("SKU description") or "").strip()
            cost = _parse_amount(record.get("Unrounded subtotal ($)") or "")
            parsed = parse_sku_description(sku)
            if parsed is None:
                unparsed[sku] += cost
                continue
            model, direction, mode = parsed
            amount = int(_parse_amount(record.get("Usage amount") or ""))
            bucket = totals[(day, model, mode)]
            if direction == "input":
                bucket[0] += amount
            else:
                bucket[1] += amount
            bucket[2] += cost

    usage = [
        BilledUsage(
            day=day,
            model=model,
            mode="batch" if mode == "batch" else "sync",
            input_tokens=int(bucket[0]),
            output_tokens=int(bucket[1]),
            cost_usd=round(bucket[2], 6),
        )
        for (day, model, mode), bucket in sorted(totals.items(), key=lambda kv: str(kv[0]))
    ]
    return BillingExport(
        usage=usage,
        unparsed_skus=sorted(unparsed),
        unparsed_cost_usd=round(sum(unparsed.values()), 6),
    )


def load_cost_log(path: Path) -> list[CostLogRow]:
    """Read a cost log, skipping and reporting rows that will not parse.

    A malformed row is printed to stderr rather than raising: a reconciliation
    that refuses to run because one line is broken tells you nothing, while
    one that runs and says which line it dropped tells you both.
    """
    rows: list[CostLogRow] = []
    if not path.exists():
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(CostLogRow.model_validate_json(line))
        except ValidationError as exc:
            print(f"skipping unparseable cost_log row {path}:{number}: {exc}", file=sys.stderr)
    return rows


def _report_mode(row: CostLogRow) -> ReportMode:
    if row.outcome == "adjustment":
        return "adjustment"
    if row.mode is None:
        return "unknown"
    if row.mode == "batch":
        return "batch"
    return "sync"


def summarise_attempts(rows: list[CostLogRow]) -> list[DayAttempts]:
    """Per-day attempt counts over every lane, not only the paid one.

    Lane does not matter here: a free-lane call that 503d four times before
    landing is the same evidence of trouble as a paid one, and the point of
    this column is to say how much of the day's work had to be retried. Cache
    hits are excluded because they reached no transport and cannot be
    retried. Adjustment rows are excluded because they are not calls.
    """
    per_day: dict[date, list[CostLogRow]] = defaultdict(list)
    for row in rows:
        if row.mode == "cache" or row.outcome == "adjustment":
            continue
        per_day[row.timestamp.date()].append(row)

    summaries: list[DayAttempts] = []
    for day in sorted(per_day):
        day_rows = per_day[day]
        by_call: dict[str, list[CostLogRow]] = defaultdict(list)
        for row in day_rows:
            if row.call_id is not None:
                by_call[row.call_id].append(row)
        summaries.append(
            DayAttempts(
                day=day,
                calls=sum(1 for row in day_rows if row.outcome == "ok"),
                attempts=len(day_rows),
                retried_calls=sum(1 for row in day_rows if row.outcome == "ok" and row.attempt > 1),
                failed_attempts=sum(1 for row in day_rows if row.outcome != "ok"),
                failed_calls=sum(
                    1
                    for attempts in by_call.values()
                    if all(row.outcome != "ok" for row in attempts)
                ),
            )
        )
    return summaries


def reconcile(billing: BillingExport, rows: list[CostLogRow]) -> ReconciliationReport:
    """Put the log next to the bill, per day, per model, per mode."""
    logged: dict[tuple[date, str, ReportMode], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    free_lane_rows = 0
    cache_rows = 0
    for row in rows:
        if row.lane == "cache" or row.mode == "cache":
            cache_rows += 1
            continue
        if row.lane == "free":
            # An unbilled Google Cloud project. It is genuinely absent from
            # the export, so counting it as "logged" would manufacture a
            # difference that is not real.
            free_lane_rows += 1
            continue
        bucket = logged[(row.timestamp.date(), row.model, _report_mode(row))]
        bucket[0] += row.prompt_tokens
        bucket[1] += row.completion_tokens
        bucket[2] += row.cost_usd

    # Keyed by the report's own wider mode type, so a billed (day, model,
    # mode) with no logged counterpart and a logged one with no billed
    # counterpart both survive into the table instead of one silently
    # dropping the other.
    billed: dict[tuple[date, str, ReportMode], BilledUsage] = {
        (u.day, u.model, u.mode): u for u in billing.usage
    }
    keys = set(logged) | set(billed)

    report_rows: list[ReconciliationRow] = []
    for key in sorted(keys, key=lambda k: (k[0], k[1], k[2])):
        day, model, mode = key
        bucket = logged.get(key, [0.0, 0.0, 0.0])
        billed_usage = billed.get(key)
        report_rows.append(
            ReconciliationRow(
                day=day,
                model=model,
                mode=mode,
                logged_input=int(bucket[0]),
                logged_output=int(bucket[1]),
                logged_cost_usd=round(bucket[2], 6),
                billed_input=billed_usage.input_tokens if billed_usage else 0,
                billed_output=billed_usage.output_tokens if billed_usage else 0,
                billed_cost_usd=billed_usage.cost_usd if billed_usage else 0.0,
            )
        )

    return ReconciliationReport(
        rows=report_rows,
        attempts=summarise_attempts(rows),
        unparsed_skus=billing.unparsed_skus,
        free_lane_rows=free_lane_rows,
        cache_rows=cache_rows,
        logged_total_usd=round(sum(r.logged_cost_usd for r in report_rows), 6),
        billed_total_usd=billing.total_cost_usd,
    )


def format_report(report: ReconciliationReport) -> str:
    """Render the report as plain text. Separate from ``reconcile`` so the
    numbers can be asserted in tests without parsing a table."""
    lines: list[str] = []
    header = (
        f"{'day':<12}{'model':<24}{'mode':<11}"
        f"{'logged in/out':>22}{'billed in/out':>22}{'diff in/out':>22}{'diff $':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in report.rows:
        marker = "" if row.matches else "  <-"
        lines.append(
            f"{row.day.isoformat():<12}{row.model:<24}{row.mode:<11}"
            f"{f'{row.logged_input:,} / {row.logged_output:,}':>22}"
            f"{f'{row.billed_input:,} / {row.billed_output:,}':>22}"
            f"{f'{row.diff_input:+,} / {row.diff_output:+,}':>22}"
            f"{row.diff_cost_usd:>10.4f}{marker}"
        )

    lines.append("")
    lines.append("Attempts per day (the explanation column):")
    attempt_header = (
        f"{'day':<12}{'calls':>8}{'attempts':>10}{'retried':>9}"
        f"{'failed attempts':>17}{'failed calls':>14}"
    )
    lines.append(attempt_header)
    lines.append("-" * len(attempt_header))
    for day_attempts in report.attempts:
        lines.append(
            f"{day_attempts.day.isoformat():<12}{day_attempts.calls:>8}"
            f"{day_attempts.attempts:>10}{day_attempts.retried_calls:>9}"
            f"{day_attempts.failed_attempts:>17}{day_attempts.failed_calls:>14}"
        )

    lines.append("")
    lines.append(f"logged (paid lane):  ${report.logged_total_usd:.6f}")
    lines.append(f"billed by Google:    ${report.billed_total_usd:.6f}")
    lines.append(f"difference:          ${report.difference_usd:.6f}")
    lines.append("")
    lines.append(
        f"excluded from the token comparison: {report.free_lane_rows} free-lane rows "
        f"(unbilled project), {report.cache_rows} cache rows (no transport)."
    )
    if report.unparsed_skus:
        lines.append("")
        lines.append("SKU descriptions this parser did not recognise (billed, uncompared):")
        for sku in report.unparsed_skus:
            lines.append(f"  {sku}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile cost_log.jsonl against a Google billing export."
    )
    parser.add_argument(
        "--billing-csv",
        type=Path,
        required=True,
        help="Google Cloud per-day billing export (CSV).",
    )
    parser.add_argument(
        "--cost-log",
        type=Path,
        default=DEFAULT_COST_LOG_PATH,
        help=f"Cost log to reconcile (default: {DEFAULT_COST_LOG_PATH}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of a table.",
    )
    args = parser.parse_args(argv)

    if not args.billing_csv.exists():
        parser.error(f"billing export not found: {args.billing_csv}")

    report = reconcile(load_billing_csv(args.billing_csv), load_cost_log(args.cost_log))
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
