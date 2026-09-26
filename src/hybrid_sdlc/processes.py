"""Bounded subprocess execution with OS-level process tree termination."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hybrid_sdlc.errors import ProcessExecutionError

# Windows Win32 Job Object constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


@dataclass
class SubprocessResult:
    """Captured result of a bounded subprocess execution."""

    exit_code: int
    stdout: str
    stderr: str
    is_truncated: bool
    duration_seconds: float
    timed_out: bool
    cancelled: bool = False


@dataclass(frozen=True)
class ProcessIdentity:
    """Best-effort process state and creation identity from the operating system."""

    state: str
    created_at: datetime | None = None
    start_token: str | None = None
    precision: str | None = None


def get_process_identity(pid: int) -> ProcessIdentity:
    """Probe liveness and creation identity without treating access errors as death."""

    if pid <= 0:
        raise ValueError("PID must be positive")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = int(kernel32.GetLastError())
            if error in {87, 1168}:  # ERROR_INVALID_PARAMETER / ERROR_NOT_FOUND
                return ProcessIdentity("dead")
            return ProcessIdentity("unknown")
        try:
            creation = FILETIME()
            exit_time = FILETIME()
            kernel_time = FILETIME()
            user_time = FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return ProcessIdentity("unknown")
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            created_at = datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
            return ProcessIdentity("alive", created_at, f"windows:{ticks}", "100ns")
        finally:
            kernel32.CloseHandle(handle)

    if sys.platform == "linux":
        try:
            stat_fields = (
                Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
            )
            if stat_fields[0] in {"Z", "X"}:
                return ProcessIdentity("dead")
            start_ticks = int(stat_fields[19])
        except FileNotFoundError:
            # hidepid=2 and similarly restricted procfs mounts can hide an
            # existing process. Confirm ESRCH independently before calling it dead.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return ProcessIdentity("dead")
            except OSError:
                return ProcessIdentity("unknown")
            return ProcessIdentity("unknown")
        except (OSError, ValueError, IndexError):
            return ProcessIdentity("unknown")
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
            try:
                boot_time = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME)
            except AttributeError:
                boot_time = float(
                    next(
                        int(line.split()[1])
                        for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
                        if line.startswith("btime ")
                    )
                )
        except (OSError, ValueError, StopIteration):
            return ProcessIdentity("unknown")
        created_at = datetime.fromtimestamp(boot_time + start_ticks / ticks_per_second, tz=UTC)
        return ProcessIdentity(
            "alive", created_at, f"linux:{boot_id}:{start_ticks}", f"{ticks_per_second}hz"
        )

    # ps exposes only second-resolution start times on macOS and BSD.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProcessIdentity("dead")
    except PermissionError:
        return ProcessIdentity("unknown")
    except OSError:
        return ProcessIdentity("unknown")
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
            env=env,
        )
        created_at = datetime.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y")
    except (OSError, subprocess.SubprocessError, ValueError):
        return ProcessIdentity("unknown")
    created_at = created_at.replace(tzinfo=UTC)
    return ProcessIdentity("alive", created_at, f"ps:{created_at.isoformat()}", "1s")


class _WindowsJobObject:
    """Manages a Win32 Job Object to guarantee descendant process termination."""

    def __init__(self) -> None:
        self.handle: Any = None
        if os.name != "nt":
            return

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        self.handle = kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            return

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        res = kernel32.SetInformationJobObject(
            self.handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not res:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def assign_process(self, process_handle: int) -> bool:
        if not self.handle or os.name != "nt":
            return False
        import ctypes

        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(self.handle, process_handle))  # type: ignore[attr-defined]

    def terminate(self) -> None:
        if not self.handle or os.name != "nt":
            return
        import ctypes

        ctypes.windll.kernel32.TerminateJobObject(self.handle, 1)  # type: ignore[attr-defined]

    def close(self) -> None:
        if not self.handle or os.name != "nt":
            return
        import ctypes

        ctypes.windll.kernel32.CloseHandle(self.handle)  # type: ignore[attr-defined]
        self.handle = None


def process_is_alive(pid: int) -> bool:
    """Return false only when the OS positively confirms that the PID is gone."""
    return get_process_identity(pid).state != "dead"


def get_process_creation_time(pid: int) -> datetime:
    """Return the OS-reported UTC creation time for a live process."""
    identity = get_process_identity(pid)
    if identity.state == "dead":
        raise ProcessLookupError(pid)
    if identity.state != "alive" or identity.created_at is None:
        raise OSError(f"Could not determine creation time for process {pid}")
    return identity.created_at


def kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Forcefully terminate a process and all its descendants across OS platforms."""
    pid = proc.pid
    if os.name == "nt":
        try:
            # taskkill /F /T kills process and all child trees
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
        try:
            proc.kill()
        except OSError:
            pass
    else:
        try:
            getpgid = os.getpgid  # type: ignore[attr-defined]
            killpg = os.killpg  # type: ignore[attr-defined]
            pgid = getpgid(pid)
            killpg(pgid, signal.SIGTERM)
            time.sleep(0.1)
            killpg(pgid, signal.SIGKILL)  # type: ignore[attr-defined]
        except (ProcessLookupError, OSError):
            try:
                proc.kill()
            except OSError:
                pass


def run_bounded_subprocess(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    buffer_cap_bytes: int = 500 * 1024,
    cancel_event: threading.Event | None = None,
    process_observer: Callable[[int, datetime | None], None] | None = None,
) -> SubprocessResult:
    """Run a subprocess strictly via argv without shell, bounding time and log buffer.

    Terminates all child processes on timeout or exception.
    """
    if not argv:
        raise ProcessExecutionError("Cannot execute empty argv array")

    cwd = cwd.resolve()
    start_time = time.perf_counter()

    job_obj = _WindowsJobObject() if os.name == "nt" else None
    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }

    if os.name == "nt":
        # CREATE_SUSPENDED (0x4) or standard creation
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except Exception as e:
        if job_obj:
            job_obj.close()
        raise ProcessExecutionError(
            f"Failed to spawn process '{argv[0]}': {e}",
            details={"argv": argv, "cwd": str(cwd), "error": str(e)},
        ) from e

    if job_obj and job_obj.handle:
        # Assign process to job object
        import ctypes

        process_handle = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, proc.pid)  # type: ignore[attr-defined]
        if process_handle:
            job_obj.assign_process(process_handle)
            ctypes.windll.kernel32.CloseHandle(process_handle)  # type: ignore[attr-defined]

    try:
        child_created_at = get_process_creation_time(proc.pid)
    except ProcessLookupError:
        # Very short-lived commands can exit before their identity is persisted.
        child_created_at = None
    except Exception as e:
        if job_obj:
            job_obj.terminate()
        kill_process_tree(proc)
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        if job_obj:
            job_obj.close()
        raise ProcessExecutionError(
            f"Could not read child process creation time: {e}",
            details={"pid": proc.pid, "error": str(e)},
        ) from e
    if process_observer is not None:
        if child_created_at is not None:
            try:
                process_observer(proc.pid, child_created_at)
            except Exception as e:
                if job_obj:
                    job_obj.terminate()
                kill_process_tree(proc)
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
                if job_obj:
                    job_obj.close()
                raise ProcessExecutionError(
                    f"Could not persist child process identity: {e}",
                    details={"pid": proc.pid, "error": str(e)},
                ) from e

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_bytes = 0
    stderr_bytes = 0
    is_truncated = False
    timed_out = False
    cancelled = False

    def reader(stream: Any, chunk_list: list[bytes], is_out: bool) -> None:
        nonlocal is_truncated, stdout_bytes, stderr_bytes
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                current_total = (stdout_bytes + stderr_bytes) + len(chunk)
                if current_total > buffer_cap_bytes:
                    allowed = max(0, buffer_cap_bytes - (stdout_bytes + stderr_bytes))
                    if allowed > 0:
                        chunk_list.append(chunk[:allowed])
                    is_truncated = True
                    # Discard rest of stream
                    while stream.read(4096):
                        pass
                    break
                chunk_list.append(chunk)
                if is_out:
                    stdout_bytes += len(chunk)
                else:
                    stderr_bytes += len(chunk)
        except Exception:
            pass

    t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_chunks, True))
    t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_chunks, False))
    t_out.daemon = True
    t_err.daemon = True
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if proc.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        cancelled = True
    finally:
        if timed_out or cancelled:
            if job_obj:
                job_obj.terminate()
            kill_process_tree(proc)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        if job_obj:
            job_obj.close()

    if timed_out and proc.poll() is None:
        if job_obj:
            job_obj.terminate()
        kill_process_tree(proc)
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    elapsed = time.perf_counter() - start_time
    stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    if process_observer is not None and child_created_at is not None:
        process_observer(proc.pid, None)

    return SubprocessResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_text,
        stderr=stderr_text,
        is_truncated=is_truncated,
        duration_seconds=elapsed,
        timed_out=timed_out,
        cancelled=cancelled,
    )
