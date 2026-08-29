"""One wake-up of the scheduled pilot. TODO.md item 1.

## What one tick does

Three things, in this order, under a lock:

1. **Collect** any batch jobs that have finished since last time, writing their
   responses into the local cache.
2. **Run phase B** over the candidate pool. Everything the collector just
   cached is a cache hit and costs nothing; the rest spends free-lane quota
   until that is gone, then queues as a real batch job and exits.
3. **Report** what is still outstanding.

Then it exits. It never waits for a batch job, because the next tick is thirty
minutes away and waiting is what the whole design exists to avoid.

## Why it exits 0 when it did nothing

Three of the four outcomes here are healthy and must not look like failures:
the lock was held by a still-running tick, the pool was already fully verified,
or work was queued and is not back yet. A scheduled task whose history is a
wall of red is a history nobody reads, and then the one real failure goes
unnoticed. Only a genuine problem -- no pool, no key, a crash -- exits nonzero.

## Why phase A is not here

Phase A is the ~2.5 hours of spaCy over the corpus. It is run once, by hand,
and its output is the pool this reads. Putting it in a thirty-minute tick would
mean every tick restarted it and no tick ever finished it, which is precisely
the failure the phase split was built to remove.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.generation.candidate_pool import DEFAULT_POOL_PATH
from src.llm.batch_jobs import DEFAULT_BATCH_JOB_STORE, BatchJobStore
from src.run_lock import LockHeld, run_lock

DEFAULT_LOCK_PATH = Path(".cache/pilot_tick.lock")


def _run(command: list[str], *, label: str) -> int:
    """Run a child command, streaming its output, and return its exit code.

    A child process rather than an import so that one step crashing cannot take
    the tick's own reporting down with it, and so each step's output is
    attributable in the log.
    """
    print(f"\n{'=' * 60}\n  {label}\n{'=' * 60}", flush=True)
    completed = subprocess.run(command, check=False)
    print(f"  [{label}] exit {completed.returncode}", flush=True)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="One wake-up of the scheduled corpus pilot.")
    parser.add_argument("--pool-file", type=str, default=str(DEFAULT_POOL_PATH))
    parser.add_argument("--lock-file", type=str, default=str(DEFAULT_LOCK_PATH))
    parser.add_argument("--batch-store", type=str, default=str(DEFAULT_BATCH_JOB_STORE))
    parser.add_argument("--verification-passes", type=int, default=2)
    parser.add_argument("--verification-batch-size", type=int, default=5)
    parser.add_argument("--max-translation-characters", type=int, default=120000)
    parser.add_argument(
        "--write-bank",
        type=str,
        default=None,
        help="Passed through to phase B. Omit to verify without banking.",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Collect finished batch jobs and stop, without running phase B.",
    )
    args = parser.parse_args()

    started = datetime.now(UTC)
    print(f"Pilot tick at {started.isoformat(timespec='seconds')}")

    pool_path = Path(args.pool_file)
    if not pool_path.exists():
        print(f"\n  FAILING: no candidate pool at {pool_path}.")
        print("  Phase A has to run once, by hand, before any tick can do anything:")
        print(f"    uv run python -m scripts.step7_corpus_pilot --phase a --pool-file {pool_path}")
        return 1

    try:
        with run_lock(args.lock_file, label="pilot_tick"):
            collect = _run(
                [
                    sys.executable,
                    "-m",
                    "scripts.collect_batch_jobs",
                    "--store",
                    args.batch_store,
                ],
                label="collect finished batch jobs",
            )
            if collect != 0:
                # Collection failing is not fatal to the tick: phase B can
                # still spend free-lane quota on prompts nothing has queued.
                print("  Collection reported a problem; continuing to phase B anyway.")

            if args.collect_only:
                print("\n  --collect-only: stopping before phase B.")
                return 0

            phase_b = [
                sys.executable,
                "-m",
                "scripts.step7_corpus_pilot",
                "--phase",
                "b",
                "--batch",
                "--pool-file",
                str(pool_path),
                "--verification-passes",
                str(args.verification_passes),
                "--verification-batch-size",
                str(args.verification_batch_size),
                "--max-translation-characters",
                str(args.max_translation_characters),
            ]
            if args.write_bank:
                phase_b += ["--write-bank", args.write_bank]
            _run(phase_b, label="phase B: translate, verify, bank")
    except LockHeld as exc:
        # The normal state of a job that runs more often than it finishes.
        print(f"\n  Another tick is still running: {exc}")
        print("  Nothing to do. Exiting 0 so this does not read as a failure.")
        return 0

    outstanding = BatchJobStore.load(Path(args.batch_store))
    elapsed = (datetime.now(UTC) - started).total_seconds()
    print(f"\n  Tick finished in {elapsed:.0f}s. {outstanding.describe()}.")
    if outstanding.jobs:
        print("  The next tick collects them. Nothing to do by hand.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
