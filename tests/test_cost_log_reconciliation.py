"""Tests for scripts/reconcile_cost_log.py and scripts/repair_cost_log.py.

CLAUDE.md section 7: unit tests never touch the network. Nothing here does --
both scripts are pure file readers plus arithmetic, and every file they read
is written into ``tmp_path`` by the fixtures below.

The billing CSV here is **synthetic**. The owner's real export is his billing
data and is deliberately not committed; the fixture reproduces its column
names and SKU-description grammar exactly, which is all either script parses,
and adds one shape the real August export happens not to contain: a day whose
bill shows both batch and non-batch SKUs, which the repair must refuse to
classify rather than guess at.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from scripts.reconcile_cost_log import (
    load_billing_csv,
    load_cost_log,
    parse_sku_description,
    reconcile,
)
from scripts.repair_cost_log import (
    ADJUSTMENT_PURPOSE,
    AlreadyRepairedError,
    classify_days,
    repair,
)
from scripts.repair_cost_log import main as repair_main
from src.contracts import MODEL_VERIFY
from src.llm.client import CostLogRow, LoggedMode, RowOutcome

_BILLING_HEADER = (
    "Date,Service description,Service ID,SKU description,SKU ID,Usage amount,"
    "Usage unit,List cost ($),Negotiated savings ($),Savings programmes ($),"
    "Other savings ($),Unrounded subtotal ($),Subtotal ($)"
)


def _billing_line(day: str, sku: str, amount: int, unrounded: float) -> str:
    return (
        f'{day},Gemini API,AEFD-7695-64FA,{sku},2156-A528-227F,"{amount:,}",count,'
        f"{unrounded:.2f},0.00,0.00,0.00,{unrounded:.6f},{unrounded:.2f}"
    )


#: gemini-3.7-flash is priced at 0.75/3.75 USD per million (input/output),
#: halved for a real Batch API submission. Every figure below is that table
#: applied to the token counts in the same row, so the fixture's dollars and
#: its tokens cannot drift apart.
_FIXTURE_DAYS = [
    # A sync-only day. The log under-reports its tokens.
    ("2026-07-02", "gemini 3.7 flash text", 100_000, 200_000, 0.075, 0.750),
    # A batch-only day the log happens to have exactly right.
    ("2026-07-03", "gemini 3.7 flash text batch", 100_000, 200_000, 0.0375, 0.375),
    # A day with no log rows at all -- present in the bill, absent from the log.
    ("2026-07-05", "gemini 3.7 flash text", 50_000, 50_000, 0.0375, 0.1875),
]


def write_billing_csv(path: Path) -> Path:
    """A synthetic Google billing export covering four days.

    2026-07-04 is the interesting one: it carries both a non-batch and a batch
    SKU, so nothing in the bill can say which mode that day's log rows used.
    """
    lines = [_BILLING_HEADER]
    for day, sku, input_tokens, output_tokens, input_cost, output_cost in _FIXTURE_DAYS:
        lines.append(
            _billing_line(
                day, f"Generate content input token count {sku}", input_tokens, input_cost
            )
        )
        lines.append(
            _billing_line(
                day, f"Generate content output token count {sku}", output_tokens, output_cost
            )
        )
    # 2026-07-04: both SKU families on one day.
    lines.append(
        _billing_line(
            "2026-07-04", "Generate content input token count gemini 3.7 flash text", 10_000, 0.0075
        )
    )
    lines.append(
        _billing_line(
            "2026-07-04",
            "Generate content output token count gemini 3.7 flash text",
            10_000,
            0.0375,
        )
    )
    lines.append(
        _billing_line(
            "2026-07-04",
            "Generate content input token count gemini 3.7 flash text batch",
            10_000,
            0.00375,
        )
    )
    lines.append(
        _billing_line(
            "2026-07-04",
            "Generate content output token count gemini 3.7 flash text batch",
            10_000,
            0.01875,
        )
    )
    # The trailing summary rows the real export carries: no Date, and a
    # restatement of the rows above rather than extra usage.
    lines.append(",,,,,,,,,Subtotal,1.530000,1.53")
    lines.append(",,,,,,,,,Tax,0.000000,0.00")
    lines.append(",,,,,,,,,Filtered total,1.530000,1.53")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _row(
    day: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    mode: LoggedMode | None = None,
    lane: str = "paid",
    outcome: RowOutcome = "ok",
    attempt: int = 1,
    call_id: str | None = None,
) -> CostLogRow:
    return CostLogRow(
        timestamp=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
        model=MODEL_VERIFY,
        lane=lane,  # type: ignore[arg-type]
        mode=mode,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        purpose="unit_test",
        outcome=outcome,
        attempt=attempt,
        call_id=call_id,
    )


def write_cost_log(path: Path, rows: list[CostLogRow]) -> Path:
    path.write_text(
        "".join(row.model_dump_json() + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _unrepaired_rows() -> list[CostLogRow]:
    """A log in the state the owner's August log is actually in: no ``mode``
    on any row, so every one of them was priced with the unconditional 0.5
    batch discount."""
    return [
        # Sync day, under-reported tokens, priced at half.
        _row("2026-07-02", prompt_tokens=80_000, completion_tokens=150_000, cost_usd=0.31125),
        # Batch day, exact tokens, and the half price happens to be correct.
        _row("2026-07-03", prompt_tokens=100_000, completion_tokens=200_000, cost_usd=0.4125),
        # The undecidable day.
        _row("2026-07-04", prompt_tokens=5_000, completion_tokens=5_000, cost_usd=0.01125),
    ]


# ---------------------------------------------------------------------------
# reconcile_cost_log.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sku", "expected"),
    [
        (
            "Generate content output token count gemini 3.7 flash text",
            ("gemini-3.7-flash", "output", "sync"),
        ),
        (
            "Generate content output token count gemini 3.7 flash text batch",
            ("gemini-3.7-flash", "output", "batch"),
        ),
        (
            "Generate content input token count gemini 3.5 flash lite text batch",
            ("gemini-3.5-flash-lite", "input", "batch"),
        ),
    ],
)
def test_parse_sku_description_reads_model_direction_and_mode(
    sku: str, expected: tuple[str, str, str]
) -> None:
    """Google's SKU description is the only place the export says which
    transport mode a charge was for, and it says it by appending one word."""
    assert parse_sku_description(sku) == expected


def test_parse_sku_description_returns_none_for_an_unrecognised_shape() -> None:
    """An unknown SKU is reported, never guessed at: it is billed money the
    comparison would otherwise drop, which is the failure this script exists
    to catch."""
    assert parse_sku_description("Cloud Storage standard class A operations") is None


def test_reconcile_reports_the_per_day_difference_including_a_day_absent_from_the_log(
    tmp_path: Path,
) -> None:
    """The core comparison: logged tokens against billed tokens, per day, per
    model, per mode -- including a day Google billed for and the log has no
    row for at all, which is the shape of the owner's Aug 14, 15 and 17."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    rows = [
        _row(
            "2026-07-02",
            prompt_tokens=80_000,
            completion_tokens=150_000,
            cost_usd=0.6225,
            mode="sync",
        ),
        _row(
            "2026-07-03",
            prompt_tokens=100_000,
            completion_tokens=200_000,
            cost_usd=0.4125,
            mode="batch",
        ),
    ]
    report = reconcile(billing, rows)
    by_day = {(r.day, r.mode): r for r in report.rows}

    short_day = by_day[(date(2026, 7, 2), "sync")]
    assert (short_day.logged_input, short_day.logged_output) == (80_000, 150_000)
    assert (short_day.billed_input, short_day.billed_output) == (100_000, 200_000)
    assert (short_day.diff_input, short_day.diff_output) == (20_000, 50_000)
    assert short_day.diff_cost_usd == pytest.approx(0.2025)

    exact_day = by_day[(date(2026, 7, 3), "batch")]
    assert exact_day.matches
    assert exact_day.diff_cost_usd == pytest.approx(0.0)

    missing_day = by_day[(date(2026, 7, 5), "sync")]
    assert (missing_day.logged_input, missing_day.logged_output) == (0, 0)
    assert (missing_day.billed_input, missing_day.billed_output) == (50_000, 50_000)
    assert missing_day.diff_cost_usd == pytest.approx(0.225)

    assert report.billed_total_usd == pytest.approx(1.53)


def test_reconcile_excludes_free_lane_and_cache_rows_from_the_comparison(
    tmp_path: Path,
) -> None:
    """The free lane is an unbilled Google Cloud project and a cache hit
    reached no transport, so neither can appear on the bill. Counting them as
    "logged" would manufacture a difference that is not real -- but dropping
    them silently would hide that a decision was made, so they are counted."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    rows = [
        _row(
            "2026-07-02",
            prompt_tokens=999_999,
            completion_tokens=999_999,
            cost_usd=0.0,
            mode="sync",
            lane="free",
        ),
        _row(
            "2026-07-02",
            prompt_tokens=42,
            completion_tokens=42,
            cost_usd=0.0,
            mode="cache",
            lane="cache",
        ),
    ]
    report = reconcile(billing, rows)
    assert report.free_lane_rows == 1
    assert report.cache_rows == 1
    logged_day = next(r for r in report.rows if r.day == date(2026, 7, 2))
    assert (logged_day.logged_input, logged_day.logged_output) == (0, 0)


def test_reconcile_counts_attempts_per_day_as_the_explanation_column(tmp_path: Path) -> None:
    """The attempt counts are why a day's tokens do not add up. A run that
    reports "3 calls, 2 of them retried" says at a glance that the log and the
    money have parted ways; the same run reported "3 calls" before and looked
    clean."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    rows = [
        _row(
            "2026-07-02",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            mode="sync",
            outcome="server_error",
            attempt=1,
            call_id="call-a",
        ),
        _row(
            "2026-07-02",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=0.01,
            mode="sync",
            outcome="ok",
            attempt=2,
            call_id="call-a",
        ),
        # A call that never landed at all: attempts, no success.
        _row(
            "2026-07-02",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            mode="batch",
            outcome="server_error",
            attempt=1,
            call_id="call-b",
        ),
        _row(
            "2026-07-02",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=0.01,
            mode="sync",
            outcome="ok",
            attempt=1,
            call_id="call-c",
        ),
    ]
    (day_attempts,) = reconcile(billing, rows).attempts
    assert day_attempts.day == date(2026, 7, 2)
    assert day_attempts.calls == 2
    assert day_attempts.attempts == 4
    assert day_attempts.retried_calls == 1
    assert day_attempts.failed_attempts == 2
    assert day_attempts.failed_calls == 1


# ---------------------------------------------------------------------------
# repair_cost_log.py
# ---------------------------------------------------------------------------


def test_classify_days_reads_the_mode_off_the_bills_own_sku_families(tmp_path: Path) -> None:
    """A day whose bill shows only non-batch SKUs was a sync day; only batch
    SKUs, a batch day; both, undecidable."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    verdicts = {day: c.verdict for day, c in classify_days(billing).items()}
    assert verdicts == {
        date(2026, 7, 2): "sync",
        date(2026, 7, 3): "batch",
        date(2026, 7, 4): "ambiguous",
        date(2026, 7, 5): "sync",
    }


def test_repair_dry_run_writes_nothing(tmp_path: Path) -> None:
    """The log is an audit record. A run that only wants to know what would
    change must not change it."""
    billing_csv = write_billing_csv(tmp_path / "bill.csv")
    log_path = write_cost_log(tmp_path / "cost_log.jsonl", _unrepaired_rows())
    before = log_path.read_bytes()

    assert (
        repair_main(["--billing-csv", str(billing_csv), "--cost-log", str(log_path), "--dry-run"])
        == 0
    )
    assert log_path.read_bytes() == before


def test_repair_makes_the_month_total_equal_the_bill(tmp_path: Path) -> None:
    """Repricing alone cannot reach the bill, because the unlogged attempts
    are unlogged. The per-day adjustment rows carry the remainder, so the
    month-to-date total the spend ceiling reads equals what Google charged."""
    billing_csv = write_billing_csv(tmp_path / "bill.csv")
    log_path = write_cost_log(tmp_path / "cost_log.jsonl", _unrepaired_rows())

    assert repair_main(["--billing-csv", str(billing_csv), "--cost-log", str(log_path)]) == 0

    repaired = load_cost_log(log_path)
    assert round(sum(row.cost_usd for row in repaired), 6) == pytest.approx(1.53)

    by_mode = {row.timestamp.date(): row.mode for row in repaired if row.outcome == "ok"}
    assert by_mode[date(2026, 7, 2)] == "sync"
    assert by_mode[date(2026, 7, 3)] == "batch"

    adjustments = [row for row in repaired if row.outcome == "adjustment"]
    assert {row.timestamp.date() for row in adjustments} == {
        date(2026, 7, 2),
        date(2026, 7, 4),
        date(2026, 7, 5),
    }, "the day whose logged cost already matched the bill needs no adjustment"
    assert all(row.purpose == ADJUSTMENT_PURPOSE for row in adjustments)
    assert all(row.prompt_tokens == 0 and row.completion_tokens == 0 for row in adjustments), (
        "Google's dollar charge is a fact; the token split behind it is not"
    )


def test_repair_leaves_an_undecidable_day_alone_and_reports_it(tmp_path: Path) -> None:
    """A day whose bill shows both SKU families cannot be classified from the
    bill, and a guessed mode is a 2x error in either direction -- the size of
    the bug being repaired. Leave the row's ``mode`` at ``None`` and say how
    many were left alone."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    repaired, report = repair(_unrepaired_rows(), billing)

    assert report.unresolved_rows == 1
    assert report.unresolved_days == [date(2026, 7, 4)]
    untouched = next(row for row in repaired if row.timestamp.date() == date(2026, 7, 4))
    assert untouched.mode is None
    assert untouched.cost_usd == pytest.approx(0.01125), "left alone means not repriced either"


def test_repair_sets_the_mode_of_free_and_cache_rows_without_consulting_the_bill(
    tmp_path: Path,
) -> None:
    """Two modes need no billing evidence: the free lane is always
    synchronous (``_call_transport`` rejects anything else on it) and a cache
    hit reached no transport at all. Neither costs anything, so resolving them
    moves no money; it just stops them being reported as unknown."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    rows = [
        # 2026-07-04 is the undecidable day, so the bill cannot help here.
        _row("2026-07-04", prompt_tokens=10, completion_tokens=10, cost_usd=0.0, lane="free"),
        _row("2026-07-04", prompt_tokens=10, completion_tokens=10, cost_usd=0.0, lane="cache"),
    ]
    repaired, report = repair(rows, billing)
    assert report.invariant_rows == 2
    assert report.unresolved_rows == 0
    assert [row.mode for row in repaired if row.outcome == "ok"] == ["sync", "cache"]


def test_repair_refuses_to_run_twice(tmp_path: Path) -> None:
    """A second pass would reprice nothing but would append a second set of
    adjustments on top of the first, doubling them. The adjustment rows are
    their own detector."""
    billing_csv = write_billing_csv(tmp_path / "bill.csv")
    log_path = write_cost_log(tmp_path / "cost_log.jsonl", _unrepaired_rows())

    assert repair_main(["--billing-csv", str(billing_csv), "--cost-log", str(log_path)]) == 0
    after_first = log_path.read_bytes()

    assert repair_main(["--billing-csv", str(billing_csv), "--cost-log", str(log_path)]) == 1
    assert log_path.read_bytes() == after_first, "a refused run must not write"

    with pytest.raises(AlreadyRepairedError):
        repair(load_cost_log(log_path), load_billing_csv(billing_csv))


def test_repair_reports_a_logged_day_the_bill_does_not_cover(tmp_path: Path) -> None:
    """Nothing can be adjusted for a day Google's export does not mention, so
    it is reported rather than quietly skipped."""
    billing = load_billing_csv(write_billing_csv(tmp_path / "bill.csv"))
    rows = [
        _row("2026-07-09", prompt_tokens=1_000, completion_tokens=1_000, cost_usd=0.00225),
    ]
    _, report = repair(rows, billing)
    assert report.days_missing_from_bill == [date(2026, 7, 9)]
    assert report.unresolved_rows == 1


def test_repaired_log_still_loads_through_the_client(tmp_path: Path) -> None:
    """The whole point of the repair is that the spend ceiling reads the right
    number afterwards, and the ceiling reads the log through
    ``GeminiLlmClient``. A repaired file that the client cannot parse would be
    a worse audit record than the broken one."""
    from src.llm.client import GeminiLlmClient

    billing_csv = write_billing_csv(tmp_path / "bill.csv")
    log_path = write_cost_log(tmp_path / "cost_log.jsonl", _unrepaired_rows())
    assert repair_main(["--billing-csv", str(billing_csv), "--cost-log", str(log_path)]) == 0

    client = GeminiLlmClient(cost_log_path=log_path, cache_dir=tmp_path / "cache")
    july = datetime(2026, 7, 15, tzinfo=UTC)
    assert client.get_month_to_date_spend(july) == pytest.approx(1.53)
