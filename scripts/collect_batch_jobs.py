"""Collect finished Gemini batch jobs into the local cache. TODO.md item 1.

## What this is for

A client built with ``detach_batch=True`` submits a real Batch API job, records
it in ``.cache/pending_batch_jobs.json``, and exits without waiting. This is
what comes back for the results.

It is built to be run by a scheduled task, repeatedly, on a machine that may
have been switched off since the job was submitted:

- It takes **one look** at each outstanding job and returns. It never waits.
- A job still running is left alone and looked at again next time.
- A job that has finished has its responses written into the content-addressed
  cache under the same ``(model, prompt)`` keys a synchronous call would have
  used, so the next pipeline run finds the work already done and pays nothing.
- A job that failed, was cancelled or expired is dropped, because re-polling
  something Google has finished with forever is a slow way of never finishing.

Exit codes are for the scheduler, not for a person: ``0`` if the sweep ran,
whatever it found, and ``1`` only if it could not run at all. "Nothing was
ready" is a normal, successful outcome and must not look like a failure, or a
scheduled task's history becomes a wall of red that nobody reads.

## Usage

    uv run python -m scripts.collect_batch_jobs
    uv run python -m scripts.collect_batch_jobs --status   # look, change nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.llm.batch_jobs import DEFAULT_BATCH_JOB_STORE, BatchJobStore
from src.llm.client import GeminiLlmClient, MissingApiKeyError
from src.llm.env import load_env_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect finished Gemini batch jobs into the local LLM cache."
    )
    parser.add_argument(
        "--store",
        type=str,
        default=str(DEFAULT_BATCH_JOB_STORE),
        help="Where submitted-but-uncollected jobs are recorded.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report what is outstanding and exit without contacting Google.",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Poll a running job until it finishes instead of leaving it for "
            "next time. For a supervised run; a scheduled one should not wait."
        ),
    )
    args = parser.parse_args()

    store_path = Path(args.store)
    store = BatchJobStore.load(store_path)

    print("=" * 60)
    print("  Pending Gemini batch jobs")
    print("=" * 60)
    print(f"  Store: {store_path}")
    print(f"  {store.describe()}")
    for job in store.jobs:
        print(
            f"    - {job.job_name}  model={job.model}  purpose={job.purpose}  "
            f"{job.size} prompt(s)  submitted {job.submitted_at.isoformat(timespec='seconds')}"
        )

    if args.status:
        return 0
    if not store.jobs:
        # Not a failure. This is what most scheduled runs will find.
        print("\n  Nothing to collect.")
        return 0

    load_env_file()
    try:
        client = GeminiLlmClient(batch_job_store_path=store_path)
        report = client.collect_pending_batches(only_finished=not args.wait)
    except MissingApiKeyError as exc:
        print(f"\n  FAILING: {exc}")
        print("  The jobs are still recorded and can be collected once a key is configured.")
        return 1

    print()
    print(f"  {report.describe()}")
    for failure in report.failed:
        print(f"    FAILED:  {failure}")
    for error in report.errors:
        print(f"    ERROR:   {error}")
    if report.cached_responses:
        print(
            f"\n  {report.cached_responses} response(s) are now in the local cache. "
            "The next pipeline run over the same prompts will find them and spend nothing."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
