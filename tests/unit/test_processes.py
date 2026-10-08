"""Unit tests for bounded subprocess execution and process tree termination."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import hybrid_sdlc.processes as process_api
from hybrid_sdlc.processes import (
    get_process_creation_time,
    get_process_identity,
    run_bounded_subprocess,
)


def _fake_proc_paths(monkeypatch: pytest.MonkeyPatch, state: str = "S") -> None:
    class FakePath:
        def __init__(self, value: str) -> None:
            self.value = value

        def read_text(self, encoding: str) -> str:
            if self.value == "/proc/77/stat":
                fields = [state, *(["0"] * 18), "12345"]
                return f"77 (fake command with spaces) {' '.join(fields)}"
            if self.value == "/proc/sys/kernel/random/boot_id":
                return "test-boot-id\n"
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


def test_linux_identity_uses_stable_boot_and_start_tick_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_proc_paths(monkeypatch)
    fake_time = SimpleNamespace(
        time=lambda: 100000.0, CLOCK_BOOTTIME=9, clock_gettime=lambda _: 100.0
    )
    monkeypatch.setattr(process_api, "time", fake_time)

    identity = get_process_identity(77)

    assert identity.state == "alive"
    assert identity.start_token == "linux:test-boot-id:12345"
    assert identity.precision == "100hz"


def test_linux_zombie_is_dead_even_while_proc_entry_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_proc_paths(monkeypatch, state="Z")
    assert get_process_identity(77).state == "dead"


def test_current_process_identity_is_available() -> None:
    identity = get_process_identity(os.getpid())
    assert identity.state == "alive"
    assert identity.created_at is not None
    assert identity.start_token


def test_exited_process_identity_is_dead() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)

    assert get_process_identity(process.pid).state == "dead"


def test_shutdown_prevents_later_subprocess_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "should-not-exist"
    monkeypatch.setattr(process_api, "_shutdown_requested", True)

    with pytest.raises(process_api.ProcessExecutionError, match="during shutdown"):
        run_bounded_subprocess(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            tmp_path,
            dict(os.environ),
            1,
        )

    assert not marker.exists()


def test_shutdown_waits_for_inflight_spawn_then_kills_registered_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered_popen = threading.Event()
    release_popen = threading.Event()
    shutdown_started = threading.Event()
    spawned: list[subprocess.Popen[bytes]] = []
    results: list[object] = []
    original_popen = subprocess.Popen

    def gated_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(process)
        entered_popen.set()
        if not release_popen.wait(timeout=5):
            raise TimeoutError("test did not release the gated Popen")
        return process

    monkeypatch.setattr(subprocess, "Popen", gated_popen)
    monkeypatch.setattr(process_api, "_shutdown_requested", False)

    def run_child() -> None:
        results.append(
            run_bounded_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                tmp_path,
                dict(os.environ),
                10,
            )
        )

    runner = threading.Thread(target=run_child)
    runner.start()
    try:
        assert entered_popen.wait(timeout=5)
        shutdown = threading.Thread(
            target=lambda: (shutdown_started.set(), process_api.terminate_active_processes())
        )
        shutdown.start()
        assert shutdown_started.wait(timeout=5)
        release_popen.set()
        runner.join(timeout=5)
        shutdown.join(timeout=5)
        assert not runner.is_alive()
        assert not shutdown.is_alive()
        assert spawned
        assert get_process_identity(spawned[0].pid).state == "dead"
        assert results
    finally:
        release_popen.set()
        runner.join(timeout=5)
        monkeypatch.setattr(process_api, "_shutdown_requested", False)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object containment regression")
def test_windows_child_stays_suspended_until_job_assignment_and_timeout_kills_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    process_synchronize = 0x00100000
    process_query_limited_information = 0x1000
    wait_object_0 = 0
    wait_timeout = 0x102
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    child_script = tmp_path / "child.py"
    grandchild_code = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(grandchild_pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    child_script.write_text(
        f"import os, pathlib, subprocess, sys, time\n"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(os.getpid()))\n"
        f"code = {grandchild_code!r}\n"
        "subprocess.Popen([sys.executable, '-c', code])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    assignment_started = threading.Event()
    allow_assignment = threading.Event()
    original_assign = process_api._WindowsJobObject.assign_process
    original_terminate = process_api._terminate_owned_tree
    results: list[object] = []
    retained_processes: list[tuple[str, int, int]] = []
    capture_failures: list[str] = []
    capture_attempted = False
    popen_pids: list[int] = []

    def gated_assign(job: process_api._WindowsJobObject, process_handle: object) -> bool:
        assignment_started.set()
        if not allow_assignment.wait(timeout=5):
            raise TimeoutError("test did not release gated Job Object assignment")
        return original_assign(job, process_handle)

    def retain_fixture_processes_before_termination(
        proc: subprocess.Popen[bytes], job: process_api._WindowsJobObject | None
    ) -> bool:
        nonlocal capture_attempted
        if not capture_attempted:
            capture_attempted = True
            popen_pids.append(proc.pid)
            try:
                for label, pid_file in (
                    ("child", child_pid_file),
                    ("grandchild", grandchild_pid_file),
                ):
                    if not pid_file.is_file():
                        raise AssertionError(
                            f"{label} PID file missing before process-tree termination"
                        )
                    pid = int(pid_file.read_text(encoding="utf-8"))
                    handle = kernel32.OpenProcess(
                        process_synchronize | process_query_limited_information, False, pid
                    )
                    if not handle:
                        raise OSError(
                            ctypes.get_last_error(),
                            f"OpenProcess failed for {label} PID {pid}",
                        )
                    retained_processes.append((label, pid, handle))
                    wait_state = kernel32.WaitForSingleObject(handle, 0)
                    exit_code = ctypes.c_uint32()
                    has_exit_code = bool(
                        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                    )
                    if wait_state != wait_timeout:
                        raise AssertionError(
                            f"{label} PID {pid} was not alive immediately before cleanup: "
                            f"wait_state=0x{wait_state:08x}, "
                            f"exit_code={exit_code.value if has_exit_code else 'unavailable'}"
                        )
            except Exception as exc:
                capture_failures.append(f"{type(exc).__name__}: {exc}")
        # Always run the real cleanup, even if a PID file or handle check failed.
        return original_terminate(proc, job)

    monkeypatch.setattr(process_api._WindowsJobObject, "assign_process", gated_assign)
    monkeypatch.setattr(
        process_api, "_terminate_owned_tree", retain_fixture_processes_before_termination
    )
    monkeypatch.setattr(process_api, "_shutdown_requested", False)
    runner = threading.Thread(
        target=lambda: results.append(
            run_bounded_subprocess(
                [sys.executable, str(child_script)],
                tmp_path,
                dict(os.environ),
                timeout_seconds=0.6,
            )
        )
    )
    runner.start()
    try:
        assert assignment_started.wait(timeout=5)
        # If CREATE_SUSPENDED is missing, user code creates both files while
        # the assignment hook is blocked, proving the containment race.
        time.sleep(0.1)
        assert not child_pid_file.exists()
        assert not grandchild_pid_file.exists()

        allow_assignment.set()
        runner.join(timeout=5)
        assert not runner.is_alive()
        assert results
        result = results[0]
        assert isinstance(result, process_api.SubprocessResult)
        assert result.timed_out
        assert not capture_failures, "; ".join(capture_failures)
        assert [label for label, _, _ in retained_processes] == ["child", "grandchild"]
        assert popen_pids
        # Popen can track a venv launcher rather than the fixture interpreter.
        # Its wait and empty job accounting do not synchronize final kernel
        # teardown of that interpreter. Wait on the original process objects,
        # not reusable numeric PIDs, with one bounded deadline and no retries.
        deadline = time.monotonic() + 2.0
        for label, pid, handle in retained_processes:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            wait_state = kernel32.WaitForSingleObject(handle, remaining_ms)
            exit_code = ctypes.c_uint32()
            has_exit_code = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
            assert wait_state == wait_object_0, (
                f"Popen PID {popen_pids[0]}, {label} PID {pid}; "
                "retained process handle was not signaled within 2s after cleanup: "
                f"wait_state=0x{wait_state:08x}, "
                f"exit_code={exit_code.value if has_exit_code else 'unavailable'}"
            )
    finally:
        allow_assignment.set()
        runner.join(timeout=5)
        for _, _, handle in retained_processes:
            kernel32.CloseHandle(handle)
        monkeypatch.setattr(process_api, "_shutdown_requested", False)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object launch failure regression")
def test_windows_assignment_failure_kills_suspended_child_and_closes_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "must-not-run"
    spawned: list[subprocess.Popen[bytes]] = []
    original_popen = subprocess.Popen

    def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture_popen)
    monkeypatch.setattr(
        process_api._WindowsJobObject,
        "assign_process",
        lambda _job, _handle: False,
    )
    monkeypatch.setattr(process_api, "_shutdown_requested", False)

    with pytest.raises(process_api.ProcessExecutionError, match="assign subprocess"):
        run_bounded_subprocess(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            tmp_path,
            dict(os.environ),
            timeout_seconds=5,
        )

    assert not marker.exists()
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    assert spawned[0].stdout is not None and spawned[0].stdout.closed
    assert spawned[0].stderr is not None and spawned[0].stderr.closed


def test_linux_permission_error_is_unknown_not_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(name="posix", sysconf=lambda name: 100, environ=os.environ),
    )
    monkeypatch.setattr(process_api.sys, "platform", "linux")

    class DeniedPath:
        def __init__(self, value: str) -> None:
            self.value = value

        def read_text(self, encoding: str) -> str:
            raise PermissionError("access denied")

    monkeypatch.setattr(process_api, "Path", DeniedPath)
    assert get_process_identity(77).state == "unknown"


def test_mac_creation_time_uses_locale_independent_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_api.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(name="posix", environ=os.environ, kill=lambda pid, signal: None),
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


def test_bounded_subprocess_writes_stdin_without_blocking(tmp_path: Path) -> None:
    result = run_bounded_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        tmp_path,
        dict(os.environ),
        timeout_seconds=3,
        stdin_data=b"probe input",
    )
    assert result.exit_code == 0
    assert result.stdout == "probe input"
    assert not result.timed_out


def test_bounded_subprocess_timeout_covers_blocked_stdin_writer(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_bounded_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        dict(os.environ),
        timeout_seconds=0.2,
        stdin_data=b"x" * 1024 * 1024,
    )
    assert result.timed_out
    assert time.monotonic() - started < 4


def test_subprocess_deadline_uses_one_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Windows Python 3.12 these clocks need not share an epoch. A
    # different monotonic origin must not make a successful child time out.
    monkeypatch.setattr(time, "monotonic", lambda: time.perf_counter() + 60)
    result = run_bounded_subprocess(
        [sys.executable, "-c", "print('done')"],
        tmp_path,
        dict(os.environ),
        timeout_seconds=2,
    )
    assert not result.timed_out
    assert result.exit_code == 0
    assert result.stdout.strip() == "done"


@pytest.mark.parametrize(
    "termination_succeeds,counts,expected",
    [
        (False, [0], False),
        (True, [2, 1, 0], True),
        (True, [None], False),
        (True, [1, None], False),
        (True, [1], False),
    ],
)
def test_windows_job_termination_requires_bounded_confirmed_empty_job(
    monkeypatch: pytest.MonkeyPatch,
    termination_succeeds: bool,
    counts: list[int | None],
    expected: bool,
) -> None:
    import ctypes

    elapsed = 0.0
    queried: list[int | None] = []
    terminated: list[tuple[object, ...]] = []

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds

    def terminate(*args: object) -> bool:
        terminated.append(args)
        return termination_succeeds

    def query(*args: object) -> bool:
        count = counts[min(len(queried), len(counts) - 1)]
        queried.append(count)
        assert args[0] == 123
        assert args[1] == 1  # JobObjectBasicAccountingInformation
        if count is None:
            return False
        args[2]._obj.ActiveProcesses = count
        return True

    monkeypatch.setattr(process_api, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        process_api, "time", SimpleNamespace(perf_counter=lambda: elapsed, sleep=sleep)
    )
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            kernel32=SimpleNamespace(TerminateJobObject=terminate, QueryInformationJobObject=query)
        ),
        raising=False,
    )
    job = process_api._WindowsJobObject.__new__(process_api._WindowsJobObject)
    job.handle = 123
    assert job.terminate() is expected
    assert terminated == [(123, 1)]
    if termination_succeeds:
        assert queried
        assert queried[-1] == (0 if expected else counts[-1])
    else:
        assert not queried
    assert elapsed <= 2.0
    if counts == [1]:
        assert elapsed == 2.0


@pytest.mark.parametrize("confirmed", [False, True, None])
def test_tree_cleanup_falls_back_unless_job_exit_is_confirmed(
    monkeypatch: pytest.MonkeyPatch, confirmed: bool | None
) -> None:
    proc = SimpleNamespace(pid=123)
    fallback: list[object] = []
    job = SimpleNamespace(terminate=lambda: confirmed) if confirmed is not None else None
    monkeypatch.setattr(process_api, "kill_process_tree", lambda child: fallback.append(child))
    assert process_api._terminate_owned_tree(proc, job) is (confirmed is True)
    assert fallback == ([] if confirmed else [proc])


def test_windows_taskkill_fallback_has_deadline_and_still_kills_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[bool] = []
    proc = SimpleNamespace(pid=123, kill=lambda: killed.append(True))

    def timeout(argv: list[str], **kwargs: object) -> None:
        assert argv == ["taskkill", "/F", "/T", "/PID", "123"]
        assert kwargs["timeout"] == 2.0
        raise subprocess.TimeoutExpired(argv, 2.0)

    monkeypatch.setattr(process_api, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(process_api.subprocess, "run", timeout)
    process_api.kill_process_tree(proc)
    assert killed == [True]
