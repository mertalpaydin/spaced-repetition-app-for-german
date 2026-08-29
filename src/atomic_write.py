"""Replacing a file atomically, including on Windows.

## Why this is not just ``os.replace``

``os.replace`` is an atomic rename on POSIX and **is not reliably atomic on
Windows against a reader**. It fails with ``WinError 5`` (access denied) or
``WinError 32`` (sharing violation) whenever any other process holds the
destination open, even only for reading. Anti-virus scanning the file, an
editor with it open, a backup agent, or simply another part of this project
reading the file to report progress will all do it.

This is not theoretical in this repository. It has now happened twice:

- It killed the first real run of the scheduled Azure translation job at 27% of
  the month's allowance, because the ledger was being read to report progress
  while the job wrote it. An F0 allowance not spent inside its calendar month
  expires, so the crash cost a month of a 13.4-month runway rather than a
  retry.
- It then failed a unit test writing the batch-job store into a pytest temp
  directory, which is a plain machine with nothing unusual running on it.

Retrying is the correct fix rather than a workaround, because the condition is
transient by nature: the other process closes the file microseconds later. The
total wait is far longer than any reader holds a small JSON file and far
shorter than the jobs this guards.

## Every atomic write in this project should come through here

Three call sites had the same pattern independently
(``translation_ledger.save_ledger_atomic``, ``batch_jobs.BatchJobStore.save``,
``candidate_pool.CandidatePool.save``) and only one of them had the retry, for
no better reason than that it was the one that had been observed failing. One
implementation is the point.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

#: Windows error codes meaning "somebody else has this file open right now".
#: 5 is ERROR_ACCESS_DENIED, 32 is ERROR_SHARING_VIOLATION. Both are transient:
#: the holder is a reader that closes microseconds later.
WINDOWS_TRANSIENT_REPLACE_ERRNOS = frozenset({5, 32})

#: Total wait is about three seconds. Longer than any reader holds a small JSON
#: file; far shorter than the runs these writes checkpoint.
REPLACE_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def replace_with_retry(
    source: Path,
    destination: Path,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """``os.replace``, retried while Windows says the destination is in use.

    Only the two transient codes are retried. Any other ``OSError`` (a
    read-only directory, a bad path) is a real problem and is raised
    immediately rather than slept over six times first.

    ``sleep_fn`` is injected so the retry path is testable without a test that
    actually waits three seconds (CLAUDE.md section 8).
    """
    for delay in REPLACE_RETRY_DELAYS_SECONDS:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in WINDOWS_TRANSIENT_REPLACE_ERRNOS:
                raise
            sleep_fn(delay)
    # One last unguarded attempt, so a genuinely stuck file raises the real
    # error with the traceback an operator needs, rather than a synthesised one.
    os.replace(source, destination)


def write_text_atomic(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Write ``text`` to ``path`` so the path is never observed half-written.

    Temp file beside the target (same directory, so the replace is a rename
    within one filesystem rather than a copy), then ``replace_with_retry``. The
    temp file is removed if anything goes wrong, so a failure leaves the
    previous content and no litter.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        temporary.write_text(text, encoding=encoding)
        replace_with_retry(temporary, path, sleep_fn=sleep_fn)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
