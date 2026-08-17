"""Step 5: stage 4 kill-gate pilot generation run.

docs/00-index.md:32 makes stage 4 a kill gate: audit ~100 accepted items by
hand and compute the post-verifier error rate; stop above roughly 15%. This
script is the small, cheap, spread-out run that produces that audit sample --
deliberately NOT the full ~1000-item top-up docs/02-content-pipeline.md
describes, which must never run before the gate is passed.

Run this once from a terminal for the standard 100-item pilot:

    .venv/bin/python -m scripts.step5_pilot_generation --pilot 100

It generates at most --pilot items (default 100) spread across a sample of
topics and CEFR levels, runs them through the full verification chain,
inserts the accepted items into the bank, and writes the accepted set to a
JSONL file for hand audit. It refuses outright, before any model call, if
--pilot exceeds the nightly item cap, and again mid-run if the monthly spend
ceiling is reached.
"""

import argparse
import sys

from src.contracts import Difficulty, VerificationClass
from src.generation.batch_client import DEFAULT_DB_PATH
from src.generation.deficits import NIGHTLY_ITEM_CAP
from src.generation.pilot import (
    ALL_VERIFICATION_CLASSES,
    DEFAULT_PILOT_CLASSES,
    DEFAULT_PILOT_ITEM_COUNT,
    DEFAULT_REJECTED_PATH,
    DEFAULT_REVIEW_PATH,
    DEFAULT_SYNC_CHUNK_SIZE,
    DEFAULT_TOPICS_PER_CEFR,
    PilotRunReport,
    run_pilot,
)
from src.llm.client import BudgetExceeded, PaidLaneForbiddenError
from src.llm.env import load_env_file


def _print_report(report: PilotRunReport) -> None:
    print("\n=======================================================")
    print("  Stage 4 pilot generation run")
    print("=======================================================")
    live_note = "yes" if report.ran_live else "no (no API key configured; ran the offline mock)"
    print(f"  Live model calls:      {live_note}")
    lane_note = (
        "paid lane, real Batch API (--batch)" if report.used_batch else "free lane, synchronous"
    )
    print(f"  Lane:                  {lane_note}")
    print(f"  Chunks run:            {report.chunks_run}")
    print(f"  Wall clock:            {report.elapsed_seconds:.1f}s")
    topic_preview = ", ".join(report.topics_used[:8])
    if len(report.topics_used) > 8:
        topic_preview += ", ..."
    print(f"  Topics sampled:        {len(report.topics_used)} ({topic_preview})")
    print(f"  Batch id:              {report.batch_id}")
    print(f"  Requested:             {report.requested_item_count}")
    print(f"  Retrieved:             {report.retrieved}")
    print(f"  Accepted (chain):      {report.accepted}")
    print(f"  Rejected (chain):      {report.rejected}")
    for reason, count in sorted(report.rejected_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")
    print(f"  Inserted into bank:    {report.inserted}")
    print(f"  Duplicates (no-op):    {report.duplicates}")
    print(f"  Bank-level rejected:   {report.bank_rejected}")
    print(
        f"  Cost incurred:         ${report.cost_usd_incurred:.4f} "
        "(from the cost log, not estimated)"
    )
    print(f"  Review file:           {report.review_file}")
    print(f"  Rejected file:         {report.rejected_review_file}")
    print()
    print(
        "Next: hand-audit the review file per docs/02-content-pipeline.md stage 4's kill "
        "gate procedure (100 items, per-defect breakdown, stop above ~15% post-verifier "
        "error rate) before running any full top-up."
    )


def _parse_classes(raw: str) -> tuple[VerificationClass, ...]:
    """Parse a comma-separated ``--classes`` argument into a tuple of valid
    verification classes, rejecting anything outside
    ``ALL_VERIFICATION_CLASSES`` loudly rather than passing an unvalidated
    string through to the contract, mirroring ``_parse_difficulties``."""
    values: list[VerificationClass] = []
    for part in raw.split(","):
        value = part.strip()
        if value not in ALL_VERIFICATION_CLASSES:
            raise SystemExit(
                f"Invalid --classes value '{value}': must be one of "
                f"{', '.join(ALL_VERIFICATION_CLASSES)}."
            )
        values.append(value)  # type: ignore[arg-type]
    return tuple(values)


def _parse_difficulties(raw: str) -> tuple[Difficulty, ...]:
    """Parse a comma-separated ``--difficulties`` argument into a tuple of
    valid tiers, rejecting anything outside {1, 2, 3} loudly rather than
    passing an unvalidated int through to the contract."""
    values: list[Difficulty] = []
    for part in raw.split(","):
        n = int(part.strip())
        if n not in (1, 2, 3):
            raise SystemExit(f"Invalid --difficulties value '{n}': must be one of 1, 2, 3.")
        values.append(n)  # type: ignore[arg-type]
    return tuple(values)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 4 kill-gate pilot generation run.")
    parser.add_argument(
        "--pilot",
        "--count",
        dest="count",
        type=int,
        default=DEFAULT_PILOT_ITEM_COUNT,
        help=f"Number of items to generate (default {DEFAULT_PILOT_ITEM_COUNT}).",
    )
    parser.add_argument(
        "--topics-per-cefr",
        type=int,
        default=DEFAULT_TOPICS_PER_CEFR,
        help=f"How many topics to sample from each CEFR band (default {DEFAULT_TOPICS_PER_CEFR}). "
        "Ignored when --all-topics is set.",
    )
    parser.add_argument(
        "--all-topics",
        action="store_true",
        help=(
            "Select EVERY topic passing --classes and --cefr, instead of "
            "sampling --topics-per-cefr topics per band. The default "
            "--topics-per-cefr of 3 means a --pilot 300 run only ever covers "
            "12 topics (3 per band x 4 bands); this widens the pilot's topic "
            "coverage to the whole filtered taxonomy. Composes with --classes "
            "and --cefr exactly like --topics-per-cefr does."
        ),
    )
    parser.add_argument(
        "--cefr",
        type=str,
        default=None,
        choices=["A1", "A2", "B1", "B2"],
        help=(
            "Restrict the run to one CEFR band and use EVERY topic in it, instead "
            "of sampling a few topics from each band. Use this to stock the bank at "
            "the learner's own level, since interleaving is a within-level "
            "technique. Omit it for the taxonomy-wide audit sample."
        ),
    )
    parser.add_argument(
        "--difficulties",
        type=str,
        default="1,2,3",
        help="Comma-separated difficulty tiers to spread across (default 1,2,3).",
    )
    parser.add_argument(
        "--classes",
        type=str,
        default=",".join(DEFAULT_PILOT_CLASSES),
        help=(
            "Comma-separated verification classes to restrict the run to, from "
            f"{', '.join(ALL_VERIFICATION_CLASSES)} (default "
            f"{','.join(DEFAULT_PILOT_CLASSES)}, i.e. 'semantic' excluded because "
            "it has no mechanically checkable answer; see "
            "docs/audits/generation-track-plan.md 'Topic triage'). Composes with "
            "--cefr: both filters apply together."
        ),
    )
    parser.add_argument(
        "--db", type=str, default=str(DEFAULT_DB_PATH), help="Path to the SQLite item bank."
    )
    parser.add_argument(
        "--review-file",
        type=str,
        default=str(DEFAULT_REVIEW_PATH),
        help="Where to write the accepted items for hand audit.",
    )
    parser.add_argument(
        "--rejected-file",
        type=str,
        default=str(DEFAULT_REJECTED_PATH),
        help="Where to write every rejected candidate, with its layer and reason.",
    )
    parser.add_argument(
        "--item-cap",
        type=int,
        default=NIGHTLY_ITEM_CAP,
        help="Hard ceiling this run refuses to exceed (default the nightly item cap).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Route this run through the paid lane's real Batch API instead of "
            "the default synchronous free lane. The project owner's own call: "
            "batch is too slow to iterate against, so it is now opt-in, "
            "reserved for a deliberate real stock run, not a pilot. Requires a "
            "configured GEMINI_PAID_API_KEY; refuses outright without one "
            "rather than silently falling back to the offline mock or the "
            "free lane. Without --batch the paid lane is genuinely forbidden, "
            "not merely deprioritised: if the free lane's daily quota is "
            "exhausted, the run fails loudly instead of silently spending on "
            "the paid lane."
        ),
    )
    parser.add_argument(
        "--sync-chunk-size",
        type=int,
        default=DEFAULT_SYNC_CHUNK_SIZE,
        help=(
            "How many generation requests (topic x difficulty cells, NOT "
            "items) to submit per round-trip, sync or batch. A long run "
            f"reports progress after each chunk (default {DEFAULT_SYNC_CHUNK_SIZE})."
        ),
    )
    args = parser.parse_args()

    # The two lane keys live in a gitignored .env per docs/01-foundation.md, and
    # nothing else in the process reads that file, so a correctly filled .env
    # would otherwise present as a missing key. Shell-exported values still win.
    load_env_file()

    difficulties = _parse_difficulties(args.difficulties)
    classes = _parse_classes(args.classes)

    try:
        report = run_pilot(
            item_count=args.count,
            topics_per_cefr=args.topics_per_cefr,
            cefr=args.cefr,
            classes=classes,
            all_topics=args.all_topics,
            difficulties=difficulties,
            db_path=args.db,
            review_path=args.review_file,
            rejected_path=args.rejected_file,
            item_cap=args.item_cap,
            use_batch=args.batch,
            sync_chunk_size=args.sync_chunk_size,
        )
    except (ValueError, BudgetExceeded, PaidLaneForbiddenError) as exc:
        print(f"Pilot run refused: {exc}")
        return 1

    _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
