"""Single-job worker entry point for persisted asynchronous jobs."""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import load_config
from hybrid_sdlc.errors import HybridSDLCError
from hybrid_sdlc.job_manager import JobManager, JobRecord, JobStatus
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.processes import get_process_identity
from hybrid_sdlc.runtime_strata import release_async_task
from hybrid_sdlc.security import verify_repo_root
from hybrid_sdlc.server_probe import select_active_endpoint


class WorkerClaimError(RuntimeError):
    """The job is missing or is not available to this worker."""


def _failure_reason(code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_")
    if "timeout" in normalized:
        return "timeout"
    if "test" in normalized or "baseline" in normalized:
        return "test_failure"
    return normalized or "internal_error"


def _terminalize_error(manager: JobManager, job_id: str, error: Exception) -> JobRecord:
    if isinstance(error, HybridSDLCError):
        reason = _failure_reason(error.code)
    elif isinstance(error, ValueError):
        reason = "launch_failure"
    else:
        reason = "internal_error"
    return manager.transition(job_id, JobStatus.FAILED, failure_reason=reason)


def _execute(
    manager: JobManager,
    job: JobRecord,
    cancel_event: threading.Event,
    process_observer: Callable[[int, datetime | None], None],
) -> RunResult:
    if job.test_profile is None:
        raise ValueError("Job does not contain a test_profile")

    config = load_config(repo_root=manager.repo_root)
    profile = config.command_profiles.get(job.test_profile)
    if profile is None:
        raise ValueError(f"Test profile '{job.test_profile}' is not configured")
    executable = resolve_profile_executable(profile, manager.repo_root)
    model = job.model or config.selected_model
    candidate, _ = select_active_endpoint(
        candidates=config.server_candidates,
        explicit_url=job.host_url,
        required_model=model,
    )
    return run_bounded_loop(
        repo_root=manager.repo_root,
        spec_path=job.spec_path,
        task_id=job.task_id,
        profile=profile,
        endpoint_url=candidate.url,
        model_name=model,
        max_retries=job.max_retries or config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        buffer_cap_bytes=config.log_buffer_cap_bytes,
        resolved_test_executable=executable,
        cancel_event=cancel_event,
        process_observer=process_observer,
        repo_map_tokens=config.aider_repo_map_tokens,
        target_files=[Path(path) for path in config.aider_edit_files],
    )


def run_worker(
    job_id: str,
    repo_root: Path | str | None = None,
    *,
    heartbeat_interval_seconds: float = 5.0,
) -> JobRecord:
    """Claim and execute one queued job, persisting heartbeat and terminal state."""

    if heartbeat_interval_seconds <= 0:
        raise ValueError("heartbeat_interval_seconds must be positive")
    verified_root = verify_repo_root(repo_root)
    manager = JobManager(verified_root)
    identity = get_process_identity(os.getpid())
    if identity.state != "alive" or identity.created_at is None:
        raise RuntimeError("Could not determine this worker's process identity")
    worker_created_at = identity.created_at
    claimed = manager.claim(
        job_id,
        os.getpid(),
        worker_created_at,
        identity.start_token,
        identity.precision,
    )

    stop_heartbeat = threading.Event()
    stop_execution = threading.Event()
    heartbeat_errors: list[Exception] = []

    def observe_child(child_pid: int, child_created_at: datetime | None) -> None:
        manager.set_child_process(
            job_id,
            os.getpid(),
            worker_created_at,
            child_pid,
            child_created_at,
        )

    def refresh_heartbeat() -> None:
        while not stop_heartbeat.wait(heartbeat_interval_seconds):
            try:
                record = manager.heartbeat(job_id, os.getpid(), worker_created_at)
                if record.cancellation_requested:
                    stop_execution.set()
                    return
            except Exception as exc:  # persisted worker state must remain observable
                heartbeat_errors.append(exc)
                stop_execution.set()
                return

    heartbeat_thread = threading.Thread(target=refresh_heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        result = _execute(manager, claimed, stop_execution, observe_child)
        if heartbeat_errors:
            raise RuntimeError(f"Worker heartbeat failed: {heartbeat_errors[0]}")
    except Exception as exc:
        stop_heartbeat.set()
        heartbeat_thread.join()
        try:
            terminal = _terminalize_error(manager, job_id, exc)
            release_async_task(verified_root, job_id, claimed.host_url)
            return terminal
        except Exception as persist_error:
            raise RuntimeError(
                "Could not persist the worker's terminal failure state"
            ) from persist_error
    stop_heartbeat.set()
    heartbeat_thread.join()

    if result.status is RunStatus.SUCCESS:
        terminal = manager.transition(job_id, JobStatus.COMPLETED, run_result=result)
        release_async_task(verified_root, job_id, claimed.host_url)
        return terminal
    if result.status is RunStatus.CANCELLED:
        terminal = manager.transition(job_id, JobStatus.CANCELLED, run_result=result)
        release_async_task(verified_root, job_id, claimed.host_url)
        return terminal
    reason = _failure_reason(result.failure.code if result.failure else "task_failure")
    terminal = manager.transition(
        job_id, JobStatus.FAILED, failure_reason=reason, run_result=result
    )
    release_async_task(verified_root, job_id, claimed.host_url)
    return terminal
