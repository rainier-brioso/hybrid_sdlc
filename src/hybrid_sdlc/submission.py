"""Policy-checked asynchronous job submission and detached worker startup."""

from __future__ import annotations

import math
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import ServerCandidateConfig, load_config
from hybrid_sdlc.errors import AsyncJobHostUnsupportedError
from hybrid_sdlc.git_tools import validate_committed_path
from hybrid_sdlc.job_manager import (
    InvalidJobTransitionError,
    JobManager,
    JobRecord,
    JobStatus,
)
from hybrid_sdlc.security import (
    build_sanitized_environment,
    resolve_confined_path,
    verify_repo_root,
)
from hybrid_sdlc.server_probe import select_active_endpoint

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.05
_WorkerCommandFactory = Callable[[str, Path], list[str]]


class JobSubmissionError(RuntimeError):
    """Submission failed after a job record was allocated; its ID remains diagnostic."""

    def __init__(self, job_id: str, message: str) -> None:
        super().__init__(message)
        self.job_id = job_id


def _worker_command(job_id: str, repo_root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "hybrid_sdlc",
        "worker",
        job_id,
        "--repo-root",
        str(repo_root),
        "--json",
    ]


def _detached_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        options["start_new_session"] = True
    return options


def _verify_windows_worker_breakaway() -> None:
    """Verify a detached child can leave every Windows job containing this host.

    Job limits may be nested and a query of the current job alone does not
    establish the effective breakaway policy. Launch a tiny child with the same
    creation flags as the worker and ask it whether it belongs to any job.
    """

    if os.name != "nt":
        return

    probe = (
        "import ctypes, sys\n"
        "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel32.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "member = ctypes.c_int()\n"
        "kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, "
        "ctypes.POINTER(ctypes.c_int)]\n"
        "kernel32.IsProcessInJob.restype = ctypes.c_int\n"
        "ok = kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, "
        "ctypes.byref(member))\n"
        "sys.exit(43 if not ok else (42 if member.value else 0))\n"
    )
    options = _detached_options()
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            **{key: value for key, value in options.items() if key != "stdout" and key != "stderr"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AsyncJobHostUnsupportedError(
            "Cannot verify that a Windows background worker can survive MCP shutdown. "
            "This host does not permit a detached worker; use synchronous execution or "
            "a host that allows Windows Job Object breakaway."
        ) from exc

    if result.returncode != 0:
        detail = "the detached process remains inside a Windows Job Object"
        if result.returncode != 42:
            detail = "the Windows process-membership check failed"
        raise AsyncJobHostUnsupportedError(
            "Cannot safely submit a Windows background job because "
            f"{detail}. Use synchronous execution or a host that allows "
            "Windows Job Object breakaway."
        )


def preflight_task_request(
    *,
    repo_root: Path | str | None,
    spec_path: Path | str,
    task_id: str,
    test_profile: str,
    host_url: str | None,
    model: str | None,
    max_retries: int | None,
) -> tuple[Path, str, str, str, int]:
    """Validate every request policy input before writing a queued job."""

    verified_root = verify_repo_root(repo_root)
    spec_file = resolve_confined_path(spec_path, verified_root, must_exist=True)
    if not spec_file.is_file():
        raise ValueError("spec_path must identify an existing regular file")
    relative_spec = spec_file.relative_to(verified_root).as_posix()
    if not isinstance(task_id, str) or _TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("task_id must be 1-128 safe identifier characters")
    if not isinstance(test_profile, str) or not test_profile.strip():
        raise ValueError("test_profile must name a configured command profile")

    config = load_config(repo_root=verified_root)
    profile = config.command_profiles.get(test_profile)
    if profile is None:
        raise ValueError(f"Test profile '{test_profile}' is not configured")
    resolve_profile_executable(profile, verified_root)
    profile_cwd = resolve_confined_path(profile.cwd, verified_root, must_exist=True)
    if not profile_cwd.is_dir():
        raise ValueError(f"Working directory for test profile '{test_profile}' is not a directory")
    validate_committed_path(verified_root, relative_spec)

    retries = max_retries if max_retries is not None else config.max_retries
    if isinstance(retries, bool) or not isinstance(retries, int) or not 1 <= retries <= 10:
        raise ValueError("max_retries must be between 1 and 10")
    target_model = model if model is not None else config.selected_model
    if (
        not isinstance(target_model, str)
        or not target_model.strip()
        or "\n" in target_model
        or "\r" in target_model
    ):
        raise ValueError("model must be a non-empty single-line identifier")
    normalized_host_url: str | None = None
    if host_url is not None:
        normalized_host_url = ServerCandidateConfig(url=host_url).url
        if all(candidate.url != normalized_host_url for candidate in config.server_candidates):
            raise ValueError("host_url must match a configured server candidate")
    selected, _ = select_active_endpoint(
        candidates=config.server_candidates,
        explicit_url=normalized_host_url,
        required_model=target_model,
    )
    return verified_root, relative_spec, selected.url, target_model, retries


def _fail_if_queued(manager: JobManager, job_id: str) -> JobRecord:
    """Persist launch failure only if the worker has not atomically claimed the job."""

    try:
        return manager.transition(job_id, JobStatus.FAILED, failure_reason="launch_failure")
    except InvalidJobTransitionError:
        # A worker that won the claim race is already a successful startup.
        return manager.get(job_id)


def _worker_claimed(record: JobRecord) -> bool:
    """Confirm startup from persisted evidence, including fast terminal workers."""

    return record.worker_pid is not None and record.started_at is not None


def _startup_succeeded(record: JobRecord) -> bool:
    """A claimed task has started; a worker launch failure has not."""

    return _worker_claimed(record) and record.failure_reason != "launch_failure"


def _stop_unclaimed_worker(child: subprocess.Popen[bytes]) -> None:
    """Stop the detached startup process after winning the queued-state race."""

    try:
        if os.name == "nt":
            child.terminate()
        else:
            kill_group = getattr(os, "killpg", None)
            if kill_group is not None:
                kill_group(child.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _launch_worker(
    command: list[str], repo_root: Path, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Spawn the packaged worker detached from the submitting process."""

    return subprocess.Popen(
        command,
        cwd=repo_root,
        env=env,
        shell=False,
        **_detached_options(),
    )


def submit_job(
    *,
    spec_path: Path | str,
    task_id: str,
    test_profile: str,
    repo_root: Path | str | None = None,
    host_url: str | None = None,
    model: str | None = None,
    max_retries: int | None = None,
    startup_timeout_seconds: float = _DEFAULT_STARTUP_TIMEOUT_SECONDS,
    _worker_command_factory: _WorkerCommandFactory | None = None,
) -> JobRecord:
    """Validate and queue one job, returning only after a worker claims it.

    ``_worker_command_factory`` is a private seam for subprocess-level lifecycle
    tests; applications should always use the packaged worker command.
    """

    if (
        isinstance(startup_timeout_seconds, bool)
        or not math.isfinite(startup_timeout_seconds)
        or not 0 < startup_timeout_seconds <= 60
    ):
        raise ValueError("startup_timeout_seconds must be greater than 0 and at most 60")
    verified_root, relative_spec, endpoint_url, target_model, retries = preflight_task_request(
        repo_root=repo_root,
        spec_path=spec_path,
        task_id=task_id,
        test_profile=test_profile,
        host_url=host_url,
        model=model,
        max_retries=max_retries,
    )
    _verify_windows_worker_breakaway()
    manager = JobManager(verified_root)
    job = manager.create(
        relative_spec,
        task_id,
        test_profile=test_profile,
        host_url=endpoint_url,
        model=target_model,
        max_retries=retries,
    )
    source_root = str(Path(__file__).resolve().parent.parent)
    env = build_sanitized_environment()
    env["PYTHONPATH"] = source_root
    command_factory = _worker_command_factory or _worker_command

    try:
        child = _launch_worker(command_factory(job.job_id, verified_root), verified_root, env)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        final = _fail_if_queued(manager, job.job_id)
        if not _startup_succeeded(final):
            raise JobSubmissionError(job.job_id, f"Worker launch failed: {exc}") from exc
        return final

    deadline = time.monotonic() + startup_timeout_seconds
    while time.monotonic() < deadline:
        current = manager.get(job.job_id)
        if current.status is not JobStatus.QUEUED:
            if _startup_succeeded(current):
                return current
            raise JobSubmissionError(
                job.job_id,
                f"Worker failed during startup ({current.failure_reason or current.status.value})",
            )
        exit_code = child.poll()
        if exit_code is not None:
            final = _fail_if_queued(manager, job.job_id)
            if not _startup_succeeded(final):
                raise JobSubmissionError(
                    job.job_id,
                    f"Worker exited with code {exit_code} before claiming the job",
                )
            return final
        time.sleep(_POLL_INTERVAL_SECONDS)

    final = _fail_if_queued(manager, job.job_id)
    if not _startup_succeeded(final):
        if final.status is JobStatus.FAILED and final.worker_pid is None:
            if child.poll() is None:
                _stop_unclaimed_worker(child)
        raise JobSubmissionError(job.job_id, "Worker did not claim the job before startup timeout")
    return final
