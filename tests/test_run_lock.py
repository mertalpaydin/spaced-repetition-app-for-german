"""Unit tests for the scheduled-job lock. TODO.md item 1.

Liveness and the clock are injected, so nothing here spawns a process or
sleeps. The two recovery paths (holder gone, lock too old) are exactly the ones
that decide whether a crashed run wedges the schedule forever, so both are
tested directly rather than through the context manager alone.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.run_lock import LockHeld, LockInfo, run_lock

_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _at(moment: datetime) -> object:
    return lambda: moment


def _write_lock(path: Path, *, pid: int, host: str, acquired_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        LockInfo(pid=pid, host=host, acquired_at=acquired_at).to_json(), encoding="utf-8"
    )


def _this_host() -> str:
    import socket

    return socket.gethostname()


def test_lock_is_created_while_held_and_removed_after(tmp_path: Path) -> None:
    path = tmp_path / "tick.lock"
    with run_lock(path, now=_at(_NOW)):  # type: ignore[arg-type]
        assert path.exists()
    assert not path.exists()


def test_lock_records_who_holds_it(tmp_path: Path) -> None:
    path = tmp_path / "tick.lock"
    with run_lock(path, label="pilot_tick", now=_at(_NOW)) as info:  # type: ignore[arg-type]
        assert info.pid == os.getpid()
        assert info.label == "pilot_tick"
        on_disk = LockInfo.from_json(path.read_text(encoding="utf-8"))
        assert on_disk is not None and on_disk.pid == os.getpid()


def test_a_live_holder_blocks_a_second_run(tmp_path: Path) -> None:
    """The case the lock exists for: two concurrent phase-B runs would both
    submit the same prompts as batch jobs and pay for them twice."""
    path = tmp_path / "tick.lock"
    _write_lock(path, pid=4321, host=_this_host(), acquired_at=_NOW - timedelta(minutes=5))

    with pytest.raises(LockHeld) as caught:
        with run_lock(path, now=_at(_NOW), is_alive=lambda _pid: True):  # type: ignore[arg-type]
            pass
    assert caught.value.holder.pid == 4321


def test_a_dead_holder_is_taken_over(tmp_path: Path) -> None:
    """A reboot mid-run must not wedge the schedule until the age backstop."""
    path = tmp_path / "tick.lock"
    _write_lock(path, pid=4321, host=_this_host(), acquired_at=_NOW - timedelta(minutes=5))

    with run_lock(path, now=_at(_NOW), is_alive=lambda _pid: False) as info:  # type: ignore[arg-type]
        assert info.pid == os.getpid()


def test_an_old_lock_is_taken_over_even_if_the_pid_looks_alive(tmp_path: Path) -> None:
    """The backstop for a recycled PID, which liveness cannot see through."""
    path = tmp_path / "tick.lock"
    _write_lock(path, pid=4321, host=_this_host(), acquired_at=_NOW - timedelta(hours=7))

    with run_lock(path, now=_at(_NOW), is_alive=lambda _pid: True) as info:  # type: ignore[arg-type]
        assert info.pid == os.getpid()


def test_a_lock_just_under_the_age_limit_is_still_respected(tmp_path: Path) -> None:
    path = tmp_path / "tick.lock"
    _write_lock(path, pid=4321, host=_this_host(), acquired_at=_NOW - timedelta(hours=5))

    with pytest.raises(LockHeld):
        with run_lock(path, now=_at(_NOW), is_alive=lambda _pid: True):  # type: ignore[arg-type]
            pass


def test_a_pid_from_another_host_is_not_asked_about(tmp_path: Path) -> None:
    """A PID from another machine says nothing here, and checking it would be
    asking about an unrelated local process that happens to share the number."""
    path = tmp_path / "tick.lock"
    _write_lock(path, pid=4321, host="some-other-machine", acquired_at=_NOW - timedelta(minutes=5))
    asked: list[int] = []

    def spy(pid: int) -> bool:
        asked.append(pid)
        return False

    with pytest.raises(LockHeld):
        with run_lock(path, now=_at(_NOW), is_alive=spy):  # type: ignore[arg-type]
            pass
    assert asked == []


def test_an_unparseable_lock_file_is_taken_over(tmp_path: Path) -> None:
    """Unreadable means unowned. Treating it as permanently held would wedge
    the job forever on a corrupt file."""
    path = tmp_path / "tick.lock"
    path.write_text("{not json", encoding="utf-8")

    with run_lock(path, now=_at(_NOW)) as info:  # type: ignore[arg-type]
        assert info.pid == os.getpid()


def test_the_lock_is_released_even_when_the_body_raises(tmp_path: Path) -> None:
    """A crashed tick must not block the next one."""
    path = tmp_path / "tick.lock"
    with pytest.raises(ValueError):
        with run_lock(path, now=_at(_NOW)):  # type: ignore[arg-type]
            raise ValueError("phase B blew up")
    assert not path.exists()


def test_a_lock_taken_over_by_someone_else_is_not_deleted_on_exit(tmp_path: Path) -> None:
    """If another run took this lock over as stale while we worked, deleting it
    on the way out would leave that run unprotected."""
    path = tmp_path / "tick.lock"
    with run_lock(path, now=_at(_NOW)):  # type: ignore[arg-type]
        _write_lock(path, pid=999999, host=_this_host(), acquired_at=_NOW)
    assert path.exists()
    holder = LockInfo.from_json(path.read_text(encoding="utf-8"))
    assert holder is not None and holder.pid == 999999


def test_lock_info_round_trips(tmp_path: Path) -> None:
    original = LockInfo(pid=7, host="h", acquired_at=_NOW, label="pilot_tick")
    assert LockInfo.from_json(original.to_json()) == original


@pytest.mark.parametrize("raw", ["", "[]", "null", '{"pid": "not a number"}', "{}"])
def test_lock_info_rejects_anything_that_is_not_a_lock(raw: str) -> None:
    assert LockInfo.from_json(raw) is None


def test_parent_directories_are_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "tick.lock"
    with run_lock(path, now=_at(_NOW)):  # type: ignore[arg-type]
        assert path.exists()


def test_process_is_alive_says_yes_about_this_process() -> None:
    """The one liveness assertion that needs no fake: we are, definitionally,
    running. Also proves the Windows path does not kill its own caller, which
    os.kill(pid, 0) would."""
    from src.run_lock import process_is_alive

    assert process_is_alive(os.getpid()) is True


def test_process_is_alive_says_no_about_an_impossible_pid() -> None:
    from src.run_lock import process_is_alive

    assert process_is_alive(-1) is False
    assert process_is_alive(0) is False
