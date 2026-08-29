"""A single-holder lock for scheduled jobs. TODO.md item 1.

## Why this exists

A Windows scheduled task with a 30-minute repetition does not know or care
whether the previous firing is still running. It starts another copy. For the
pilot's phase B that is not merely wasteful: two concurrent runs would both
read the same candidate pool, both find the same prompts uncached, and both
submit them as batch jobs. The work is paid for twice and the second job's
results are thrown away.

So every wake-up takes this lock first, and a wake-up that cannot take it exits
0 immediately. Exiting 0 is deliberate: "the previous run is still going" is
the normal, healthy state of a job that runs more often than it finishes, and a
scheduled task whose history is a wall of red is a history nobody reads.

## Why not just create the file exclusively

Exclusive creation (``open(path, "x")``) is a correct mutex and a bad lock on
its own, because a holder that crashes leaves the file behind and nothing ever
runs again. Something has to decide when a lock is abandoned.

Two independent recoveries, because either alone has a hole:

- **The holder's process is gone.** Checked directly. This is the common case
  (a reboot, a kill) and recovers in seconds rather than hours.
- **The lock is older than ``max_age``.** The backstop for the case process
  liveness cannot see: a PID that has been recycled by a different program, or
  a lock file copied from another machine.

## Checking liveness without killing anything

``os.kill(pid, 0)`` is the usual POSIX idiom and is **actively dangerous on
Windows**, where CPython implements ``os.kill`` with ``TerminateProcess`` for
any signal that is not ``CTRL_C_EVENT`` or ``CTRL_BREAK_EVENT``. Asking "is
this process alive" that way would kill it. Windows therefore goes through
``OpenProcess``/``GetExitCodeProcess`` instead, and POSIX keeps the signal-0
idiom.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: How long a lock may sit before it is treated as abandoned regardless of what
#: process liveness says. Generously longer than any run this guards: phase B
#: over a whole pool is minutes to hours, and taking a lock away from a run
#: that is genuinely still working is worse than waiting another half hour.
DEFAULT_MAX_AGE = timedelta(hours=6)

#: Windows constants. 0x1000 is PROCESS_QUERY_LIMITED_INFORMATION, which is the
#: least privilege that can read an exit code and works across sessions.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


class LockHeld(RuntimeError):
    """Raised when the lock is held by a live holder.

    Carries the holder so a caller can say who, rather than only that.
    """

    def __init__(self, holder: LockInfo, message: str) -> None:
        super().__init__(message)
        self.holder = holder


@dataclass(frozen=True)
class LockInfo:
    """Who holds a lock, as recorded in the file."""

    pid: int
    host: str
    acquired_at: datetime
    label: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "pid": self.pid,
                "host": self.host,
                "acquired_at": self.acquired_at.isoformat(),
                "label": self.label,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> LockInfo | None:
        """Parse a lock file, or ``None`` if it is not one.

        Unreadable means unowned. A lock file nothing can parse cannot tell us
        who holds it, and treating it as permanently held would wedge the job
        forever on a corrupt file.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                pid=int(data["pid"]),
                host=str(data["host"]),
                acquired_at=datetime.fromisoformat(str(data["acquired_at"])),
                label=str(data.get("label", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def process_is_alive(pid: int) -> bool:
    """Whether ``pid`` is a running process on this machine.

    **Never uses ``os.kill``**: on Windows CPython implements it with
    ``TerminateProcess`` for ordinary signals, so the usual ``os.kill(pid, 0)``
    liveness idiom would kill the process it is asking about.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                # Cannot tell. Say alive: refusing to run is recoverable by the
                # next wake-up, whereas two concurrent runs double-submit.
                return True
            return bool(code.value == _STILL_ACTIVE)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists; it just is not ours.
        return True
    return True


@contextmanager
def run_lock(
    path: Path | str,
    *,
    label: str = "",
    max_age: timedelta = DEFAULT_MAX_AGE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    is_alive: Callable[[int], bool] = process_is_alive,
) -> Iterator[LockInfo]:
    """Hold an exclusive lock at ``path`` for the duration of the block.

    Raises ``LockHeld`` if a live holder has it. Takes it over if the recorded
    holder is gone or the lock is older than ``max_age``.

    ``now`` and ``is_alive`` are injected so the staleness and liveness
    branches are testable without sleeping or spawning anything (CLAUDE.md
    section 8).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    moment = now()
    mine = LockInfo(pid=os.getpid(), host=_hostname(), acquired_at=moment, label=label)

    existing = _read_holder(target)
    if existing is not None:
        age = moment - existing.acquired_at
        stale_by_age = age >= max_age
        # Only trust the PID on the machine that recorded it. A PID from
        # another host means nothing here, and asking about it would be asking
        # about an unrelated local process that happens to share the number.
        same_host = existing.host == mine.host
        holder_gone = same_host and not is_alive(existing.pid)
        if not (stale_by_age or holder_gone):
            raise LockHeld(
                existing,
                f"{target} is held by pid {existing.pid} on {existing.host} "
                f"since {existing.acquired_at.isoformat(timespec='seconds')} "
                f"({int(age.total_seconds())}s ago).",
            )

    target.write_text(mine.to_json(), encoding="utf-8")
    try:
        yield mine
    finally:
        # Only remove the lock if it is still ours. Another process may have
        # taken it over as stale while this one was working, and deleting its
        # lock would leave that run unprotected.
        current = _read_holder(target)
        if current is not None and current.pid == mine.pid and current.host == mine.host:
            target.unlink(missing_ok=True)


def _read_holder(path: Path) -> LockInfo | None:
    try:
        return LockInfo.from_json(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _hostname() -> str:
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return "unknown"
