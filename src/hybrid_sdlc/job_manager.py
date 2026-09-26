"""Persisted asynchronous job state and legal lifecycle transitions.

Failed jobs always carry a machine-readable ``failure_reason``. The initial
reason vocabulary is ``abandoned_process``, ``timeout``, ``test_failure``,
``security_policy``, ``launch_failure``, and ``internal_error``; callers may
use another lowercase snake-case reason without changing the schema.
"""

from __future__ import annotations

import math
import re
import secrets
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hybrid_sdlc.artifacts import atomic_save_json
from hybrid_sdlc.errors import PathTraversalError
from hybrid_sdlc.models import SCHEMA_VERSION, AttemptRecord, RunResult
from hybrid_sdlc.processes import ProcessIdentity, get_process_identity

_JOB_ID_PATTERN = re.compile(r"^job_\d{8}T\d{12}Z_[0-9a-f]{8}$")
_FAILURE_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class JobStatus(StrEnum):
    """Persisted lifecycle states; terminal states have no outgoing edges."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
LEGAL_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RUNNING: TERMINAL_STATUSES,
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class InvalidJobTransitionError(ValueError):
    """A requested status change is not a legal edge in the job state machine."""


def validate_job_id(job_id: str) -> str:
    """Reject unsafe or malformed IDs before any job-path lookup."""

    if not isinstance(job_id, str) or _JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise ValueError("Invalid job ID")
    return job_id


def new_job_id() -> str:
    """Combine a UTC microsecond timestamp and 32 random bits."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"job_{timestamp}_{secrets.token_hex(4)}"


class JobRecord(BaseModel):
    """Versioned on-disk state for a single asynchronous job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION
    job_id: str
    spec_path: str
    task_id: str
    test_profile: str | None = None
    host_url: str | None = None
    model: str | None = None
    max_retries: int | None = Field(default=None, ge=1, le=10)
    status: JobStatus = JobStatus.QUEUED
    failure_reason: str | None = None
    failure_metadata: dict[str, str | int | float | bool | None] | None = None
    worker_pid: int | None = Field(default=None, gt=0)
    worker_created_at: datetime | None = None
    worker_process_identity: str | None = None
    worker_start_precision: str | None = None
    child_pid: int | None = Field(default=None, gt=0)
    child_created_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    run_result: RunResult | None = None
    final_diff_patch: str | None = None
    log_file: str | None = None

    @model_validator(mode="after")
    def validate_record(self) -> JobRecord:
        validate_job_id(self.job_id)
        if self.status is JobStatus.FAILED:
            if (
                self.failure_reason is None
                or _FAILURE_REASON_PATTERN.fullmatch(self.failure_reason) is None
            ):
                raise ValueError("Failed jobs require a lowercase snake-case failure_reason")
        elif self.failure_reason is not None:
            raise ValueError("Only failed jobs may have a failure_reason")
        if self.status is not JobStatus.FAILED and self.failure_metadata is not None:
            raise ValueError("Only failed jobs may have failure_metadata")
        if self.status in TERMINAL_STATUSES and self.finished_at is None:
            raise ValueError("Terminal jobs require finished_at")
        if self.status not in TERMINAL_STATUSES and self.finished_at is not None:
            raise ValueError("Nonterminal jobs cannot have finished_at")
        return self


class JobManager:
    """Create, read, and atomically transition job records under one repo."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def _job_path(self, job_id: str) -> Path:
        validate_job_id(job_id)
        artifacts_root = self.repo_root / ".hybrid_sdlc"
        jobs_root = artifacts_root / "jobs"
        candidate = jobs_root / f"{job_id}.json"
        if (
            artifacts_root.resolve() != artifacts_root
            or jobs_root.resolve() != jobs_root
            or candidate.resolve() != candidate
        ):
            raise PathTraversalError("Job path escapes the repository artifacts directory")
        return candidate

    def create(
        self,
        spec_path: str,
        task_id: str,
        *,
        test_profile: str | None = None,
        host_url: str | None = None,
        model: str | None = None,
        max_retries: int | None = None,
    ) -> JobRecord:
        """Persist a queued job, retrying if an ID already exists."""

        for _ in range(10):
            job_id = new_job_id()
            path = self._job_path(job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(path) + ".transition.lock"):
                if path.exists():
                    continue
                now = datetime.now(UTC)
                record = JobRecord(
                    job_id=job_id,
                    spec_path=spec_path,
                    task_id=task_id,
                    test_profile=test_profile,
                    host_url=host_url,
                    model=model,
                    max_retries=max_retries,
                    created_at=now,
                    updated_at=now,
                )
                atomic_save_json(path, record, self.repo_root)
                return record
        raise RuntimeError("Could not allocate a unique job ID")

    def claim(
        self,
        job_id: str,
        worker_pid: int,
        worker_created_at: datetime,
        worker_start_token: str | None = None,
        worker_start_precision: str | None = None,
    ) -> JobRecord:
        """Atomically claim a queued job for exactly one worker process."""

        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        with FileLock(str(path) + ".transition.lock"):
            current = self.get(job_id)
            if current.status is not JobStatus.QUEUED:
                raise InvalidJobTransitionError(f"Cannot claim job in {current.status} state")
            now = datetime.now(UTC)
            claimed = JobRecord.model_validate(
                {
                    **current.model_dump(),
                    "status": JobStatus.RUNNING,
                    "worker_pid": worker_pid,
                    "worker_created_at": worker_created_at,
                    "worker_process_identity": worker_start_token,
                    "worker_start_precision": worker_start_precision,
                    "started_at": now,
                    "heartbeat_at": now,
                    "updated_at": now,
                }
            )
            atomic_save_json(path, claimed, self.repo_root)
            return claimed

    def heartbeat(self, job_id: str, worker_pid: int, worker_created_at: datetime) -> JobRecord:
        """Atomically refresh a running job heartbeat owned by this worker."""

        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        with FileLock(str(path) + ".transition.lock"):
            current = self.get(job_id)
            if (
                current.status is not JobStatus.RUNNING
                or current.worker_pid != worker_pid
                or current.worker_created_at != worker_created_at
            ):
                raise InvalidJobTransitionError("Heartbeat is not owned by this worker")
            now = datetime.now(UTC)
            refreshed = current.model_copy(update={"heartbeat_at": now, "updated_at": now})
            atomic_save_json(path, refreshed, self.repo_root)
            return refreshed

    def set_child_process(
        self,
        job_id: str,
        worker_pid: int,
        worker_created_at: datetime,
        child_pid: int,
        child_created_at: datetime | None,
    ) -> JobRecord:
        """Persist or clear the active child identity under the owning worker."""

        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        with FileLock(str(path) + ".transition.lock"):
            current = self.get(job_id)
            if (
                current.status is not JobStatus.RUNNING
                or current.worker_pid != worker_pid
                or current.worker_created_at != worker_created_at
            ):
                raise InvalidJobTransitionError("Child process is not owned by this worker")
            if child_created_at is None and current.child_pid != child_pid:
                return current
            updated = current.model_copy(
                update={
                    "child_pid": child_pid if child_created_at is not None else None,
                    "child_created_at": child_created_at,
                    "updated_at": datetime.now(UTC),
                }
            )
            atomic_save_json(path, updated, self.repo_root)
            return updated

    def get(self, job_id: str) -> JobRecord:
        """Load a complete persisted record or raise FileNotFoundError."""

        path = self._job_path(job_id)
        record = JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.job_id != job_id:
            raise ValueError("Persisted job ID does not match requested ID")
        return record

    def status(
        self,
        job_id: str,
        *,
        wait_timeout_seconds: float = 0.0,
        poll_interval_seconds: float = 0.25,
    ) -> JobRecord:
        """Look up a job, optionally waiting a bounded time for a terminal state."""

        if (
            not math.isfinite(wait_timeout_seconds)
            or wait_timeout_seconds < 0
            or wait_timeout_seconds > 60
        ):
            raise ValueError("wait_timeout_seconds must be finite and between 0 and 60")
        if (
            not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds < 0.01
            or poll_interval_seconds > 5
        ):
            raise ValueError("poll_interval_seconds must be finite and between 0.01 and 5")
        deadline = time.monotonic() + wait_timeout_seconds
        while True:
            record = self.recover_job(job_id)
            if record.status in TERMINAL_STATUSES or time.monotonic() >= deadline:
                return record
            time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))

    def recover_job(self, job_id: str) -> JobRecord:
        """Fail a confirmed abandoned worker; unknown OS probes leave state untouched."""

        snapshot = self.get(job_id)
        if snapshot.status is not JobStatus.RUNNING:
            return snapshot
        identity = (
            get_process_identity(snapshot.worker_pid)
            if snapshot.worker_pid is not None
            else ProcessIdentity("dead")
        )
        diagnostic = self._abandonment_diagnostic(snapshot, identity)
        if diagnostic is None:
            return snapshot
        path = self._job_path(job_id)
        with FileLock(str(path) + ".transition.lock"):
            current = self.get(job_id)
            if (
                current.status is not JobStatus.RUNNING
                or current.worker_pid != snapshot.worker_pid
                or current.worker_created_at != snapshot.worker_created_at
                or current.worker_process_identity != snapshot.worker_process_identity
            ):
                return current
            now = datetime.now(UTC)
            diagnostic["detected_at"] = now.isoformat()
            failed = JobRecord.model_validate(
                {
                    **current.model_dump(),
                    "status": JobStatus.FAILED,
                    "failure_reason": "abandoned_process",
                    "failure_metadata": diagnostic,
                    "updated_at": now,
                    "finished_at": now,
                    "duration_seconds": max(0.0, (now - current.created_at).total_seconds()),
                }
            )
            atomic_save_json(path, failed, self.repo_root)
            return failed

    def recover_running_jobs(self) -> list[JobRecord]:
        """Explicit startup recovery over validated direct job records only.

        Hosts should call this when their CLI or MCP process starts. The directory
        scan is intentionally bounded to direct ``*.json`` files under the
        repository's validated jobs directory.
        """

        artifacts_root = self.repo_root / ".hybrid_sdlc"
        jobs_root = artifacts_root / "jobs"
        if not jobs_root.exists():
            return []
        if artifacts_root.resolve() != artifacts_root or jobs_root.resolve() != jobs_root:
            raise PathTraversalError("Jobs directory escapes the repository artifacts directory")
        recovered: list[JobRecord] = []
        for candidate in jobs_root.iterdir():
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix != ".json":
                continue
            try:
                job_id = validate_job_id(candidate.stem)
                record = self.get(job_id)
            except (ValueError, OSError):
                continue
            if record.status is JobStatus.RUNNING:
                recovered.append(self.recover_job(job_id))
        return recovered

    @staticmethod
    def _abandonment_diagnostic(
        record: JobRecord, identity: ProcessIdentity
    ) -> dict[str, str | int | float | bool | None] | None:
        if record.worker_pid is None or identity.state == "dead":
            return {
                "cause": "worker_process_missing",
                "worker_pid": record.worker_pid,
                "probe_state": identity.state,
            }
        if identity.state != "alive":
            return None
        if record.worker_process_identity is not None:
            if identity.start_token is None:
                return None
            if record.worker_process_identity != identity.start_token:
                return {
                    "cause": "worker_pid_reused",
                    "worker_pid": record.worker_pid,
                    "expected_process_identity": record.worker_process_identity,
                    "observed_process_identity": identity.start_token,
                    "identity_precision": identity.precision,
                }
            return None
        # Compatibility for pre-token records. Linux reconstructed creation
        # times may drift after suspend/clock changes; ps is only second precise.
        if record.worker_created_at is None or identity.created_at is None:
            return None
        tolerance = 1.1 if identity.precision == "1s" else 2.5
        delta = abs((record.worker_created_at - identity.created_at).total_seconds())
        if delta > tolerance:
            return {
                "cause": "worker_pid_reused_legacy_identity",
                "worker_pid": record.worker_pid,
                "expected_created_at": record.worker_created_at.isoformat(),
                "observed_created_at": identity.created_at.isoformat(),
                "identity_precision": identity.precision,
                "comparison_tolerance_seconds": tolerance,
            }
        return None

    def transition(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failure_reason: str | None = None,
        failure_metadata: dict[str, str | int | float | bool | None] | None = None,
        run_result: RunResult | None = None,
    ) -> JobRecord:
        """Apply one legal transition under a per-job lock and atomic replacement."""

        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        with FileLock(str(path) + ".transition.lock"):
            current = self.get(job_id)
            status = JobStatus(status)
            if status not in LEGAL_TRANSITIONS[current.status]:
                raise InvalidJobTransitionError(f"Cannot transition {current.status} to {status}")
            now = datetime.now(UTC)
            updates: dict[str, Any] = {
                "status": status,
                "failure_reason": failure_reason,
                "failure_metadata": failure_metadata,
                "updated_at": now,
                "run_result": run_result,
            }
            if status is JobStatus.RUNNING:
                updates["started_at"] = now
            if status in TERMINAL_STATUSES:
                updates["finished_at"] = now
                updates["duration_seconds"] = max(0.0, (now - current.created_at).total_seconds())
            record = JobRecord.model_validate({**current.model_dump(), **updates})
            atomic_save_json(path, record, self.repo_root)
            return record
