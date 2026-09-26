"""Unit tests for bounded subprocess execution and process tree termination."""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import hybrid_sdlc.processes as process_api
from hybrid_sdlc.processes import get_process_creation_time, run_bounded_subprocess


def _fake_proc_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def read_text(self, encoding: str) -> str:
            if self.value == "/proc/77/stat":
                fields = ["S", *(["0"] * 18), "12345"]
                return f"77 (fake command with spaces) {' '.join(fields)}"
            return "cpu 1 2 3\nbtime 100000\n"

    monkeypatch.setattr(process_api, "Path", FakePath)
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(name="posix", sysconf=lambda name: 100, environ=os.environ),
    )
    monkeypatch.setattr(process_api.sys, "platform", "linux")
    monkeypatch.setattr(process_api.os, "sysconf", lambda name: 100)


@pytest.mark.parametrize("with_boottime", [False, True])
def test_linux_creation_time_uses_proc_start_ticks(
    monkeypatch: pytest.MonkeyPatch, with_boottime: bool
) -> None:
    _fake_proc_paths(monkeypatch)
    fake_time = SimpleNamespace(time=lambda: 100000.0, sysconf=lambda name: 100)
    if with_boottime:
        fake_time.CLOCK_BOOTTIME = 9
        fake_time.clock_gettime = lambda clock_id: 100.0
    monkeypatch.setattr(process_api, "time", fake_time)
    created_at = get_process_creation_time(77)
    expected = 100023.45 if with_boottime else 100123.45
    assert created_at == datetime.fromtimestamp(expected, tz=UTC)


def test_mac_creation_time_uses_locale_independent_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_api.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(name="posix", environ=os.environ),
    )
    calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append({"argv": argv, **kwargs})
        return SimpleNamespace(stdout="Sat Sep 26 17:40:11 2026\n")

    monkeypatch.setattr(process_api.subprocess, "run", fake_run)
    result = get_process_creation_time(77)
    assert result == datetime(2026, 9, 26, 17, 40, 11, tzinfo=UTC)
    assert calls[0]["argv"] == ["ps", "-p", "77", "-o", "lstart="]
    assert calls[0]["env"]["LC_ALL"] == "C"
    assert calls[0]["env"]["TZ"] == "UTC"


def test_creation_time_rejects_invalid_pid() -> None:
    with pytest.raises(ValueError, match="positive"):
        get_process_creation_time(0)


def _is_pid_alive(pid: int) -> bool:
    """Return True if the PID is still running."""
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        result = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(result and code.value == still_active)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it (zombie, different uid)
            return True


def test_run_bounded_subprocess_success(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "print('hello world')"]
    res = run_bounded_subprocess(
        argv=argv,
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_seconds=5.0,
    )
    assert res.exit_code == 0
    assert "hello world" in res.stdout
    assert not res.timed_out
    assert not res.is_truncated


def test_run_bounded_subprocess_reports_child_identity(tmp_path: Path) -> None:
    events: list[tuple[int, datetime | None]] = []
    live_creation_times: list[datetime] = []

    def observe(pid: int, created_at: datetime | None) -> None:
        events.append((pid, created_at))
        if created_at is not None:
            live_creation_times.append(get_process_creation_time(pid))

    res = run_bounded_subprocess(
        argv=[sys.executable, "-c", "import time; time.sleep(0.2)"],
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_seconds=5.0,
        process_observer=observe,
    )
    assert res.exit_code == 0
    assert len(events) == 2
    assert events[0][0] == events[1][0]
    assert events[0][1] is not None
    assert events[1][1] is None
    assert isinstance(events[0][1], datetime)
    assert abs((events[0][1] - live_creation_times[0]).total_seconds()) <= (
        1.1 if sys.platform == "darwin" else 0.1
    )


def test_run_bounded_subprocess_timeout_kills_process(tmp_path: Path) -> None:
    # Spawn a python process that sleeps for 10 seconds, but timeout in 0.5s
    argv = [sys.executable, "-c", "import time; time.sleep(10)"]
    start = time.perf_counter()
    res = run_bounded_subprocess(
        argv=argv,
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_seconds=0.5,
    )
    elapsed = time.perf_counter() - start
    assert res.timed_out
    # Should return promptly after ~0.5s, well before 10s
    assert elapsed < 3.0


def test_run_bounded_subprocess_output_truncation(tmp_path: Path) -> None:
    # Generate 10 KB of output with a 2 KB buffer cap
    argv = [sys.executable, "-c", "print('A' * 10000)"]
    res = run_bounded_subprocess(
        argv=argv,
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_seconds=5.0,
        buffer_cap_bytes=2048,
    )
    assert res.exit_code == 0
    assert res.is_truncated
    assert len(res.stdout.encode("utf-8")) <= 2048 + 4096  # Bounded within 1 chunk buffer


def test_grandchild_does_not_survive_timeout(tmp_path: Path) -> None:
    """Prove that a grandchild spawned by the direct child is terminated on timeout.

    Strategy: the child writes its own PID to a file, then spawns a grandchild
    that sleeps for 30 s and also writes its PID.  We impose a 0.8 s timeout on
    the entire tree.  After run_bounded_subprocess returns we poll both PID files
    and confirm neither process is still running.
    """
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    child_pid_file = pid_dir / "child.pid"
    grand_pid_file = pid_dir / "grand.pid"

    # Write the child script to a .py file to avoid inline escaping hell
    child_script = tmp_path / "child_script.py"
    child_script.write_text(
        f"""\
import os
import subprocess
import sys
import time
import pathlib

# Write child PID
pathlib.Path({str(child_pid_file)!r}).write_text(str(os.getpid()))

# Spawn grandchild that writes its own PID then sleeps
grandchild_code = (
    "import os, pathlib, time\\n"
    "pathlib.Path({str(grand_pid_file)!r}).write_text(str(os.getpid()))\\n"
    "time.sleep(30)\\n"
)
subprocess.Popen([sys.executable, "-c", grandchild_code])

# Give grandchild time to start and write its PID
time.sleep(0.4)

# Sleep indefinitely so the parent can time us out
time.sleep(60)
""",
        encoding="utf-8",
    )

    argv = [sys.executable, str(child_script)]
    start = time.perf_counter()
    res = run_bounded_subprocess(
        argv=argv,
        cwd=tmp_path,
        env=dict(os.environ),
        timeout_seconds=1.0,
    )
    elapsed = time.perf_counter() - start

    assert res.timed_out
    assert elapsed < 6.0, "Should return within 6 s of timeout"

    # Give OS a moment to reap processes
    time.sleep(0.5)

    # Check child PID
    if child_pid_file.exists():
        child_pid = int(child_pid_file.read_text().strip())
        assert not _is_pid_alive(child_pid), f"Child PID {child_pid} still alive after cleanup"

    # Check grandchild PID
    if grand_pid_file.exists():
        grand_pid = int(grand_pid_file.read_text().strip())
        assert not _is_pid_alive(grand_pid), f"Grandchild PID {grand_pid} still alive after cleanup"
