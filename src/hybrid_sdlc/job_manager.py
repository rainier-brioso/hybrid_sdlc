"""Persisted asynchronous job state and legal lifecycle transitions.

Failed jobs always carry a machine-readable ``failure_reason``. The initial
reason vocabulary is ``abandoned_process``, ``timeout``, ``test_failure``,
``security_policy``, ``launch_failure``, and ``internal_error``; callers may
use another lowercase snake-case reason without changing the schema.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hybrid_sdlc.artifacts import atomic_save_json
from hybrid_sdlc.errors import PathTraversalError
from hybrid_sdlc.models import SCHEMA_VERSION, AttemptRecord

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
    status: JobStatus = JobStatus.QUEUED
    failure_reason: str | None = None
    worker_pid: int | None = Field(default=None, gt=0)
    worker_created_at: datetime | None = None
    child_pid: int | None = Field(default=None, gt=0)
    child_created_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    attempts: list[AttemptRecord] = Field(default_factory=list)
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

    def create(self, spec_path: str, task_id: str) -> JobRecord:
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
                    created_at=now,
                    updated_at=now,
                )
                atomic_save_json(path, record, self.repo_root)
                return record
        raise RuntimeError("Could not allocate a unique job ID")

    def get(self, job_id: str) -> JobRecord:
        """Load a complete persisted record or raise FileNotFoundError."""

        path = self._job_path(job_id)
        record = JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.job_id != job_id:
            raise ValueError("Persisted job ID does not match requested ID")
        return record

    def transition(
        self, job_id: str, status: JobStatus, *, failure_reason: str | None = None
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
                "updated_at": now,
            }
            if status is JobStatus.RUNNING:
                updates["started_at"] = now
            if status in TERMINAL_STATUSES:
                updates["finished_at"] = now
                updates["duration_seconds"] = max(0.0, (now - current.created_at).total_seconds())
            record = JobRecord.model_validate({**current.model_dump(), **updates})
            atomic_save_json(path, record, self.repo_root)
            return record
