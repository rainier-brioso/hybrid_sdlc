"""Versioned data models and schemas for hybrid-sdlc."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"


class BaseSchemaModel(BaseModel):
    """Base model with forward-compatibility for unknown fields."""

    model_config = ConfigDict(extra="ignore")


class RunStatus(StrEnum):
    """Overall terminal status of a run."""

    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"
    CANCELLED = "cancelled"


class FailureRecord(BaseSchemaModel):
    """Serialized error or failure record."""

    schema_version: str = SCHEMA_VERSION
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DiffSummary(BaseSchemaModel):
    """Structured summary of a git diff."""

    schema_version: str = SCHEMA_VERSION
    diff_hash: str
    changed_files: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    patch_file: str | None = None
    is_empty: bool = False
    is_binary: bool = False
    is_truncated: bool = False


class AttemptRecord(BaseSchemaModel):
    """Record of a single edit-test attempt in the bounded loop."""

    schema_version: str = SCHEMA_VERSION
    attempt_index: int
    started_at: str
    finished_at: str
    duration_seconds: float
    test_exit_code: int
    test_passed: bool
    test_stdout_summary: str = ""
    test_stderr_summary: str = ""
    failure_signature: str | None = None
    diff_summary: DiffSummary | None = None


class ProbeResult(BaseSchemaModel):
    """Structured result of probing an inference server endpoint."""

    schema_version: str = SCHEMA_VERSION
    url: str
    available: bool
    models: list[str] = Field(default_factory=list)
    matched_model: str | None = None
    readiness_tested: bool = False
    readiness_passed: bool = False
    latency_ms: float | None = None
    error: FailureRecord | None = None


class RunResult(BaseSchemaModel):
    """Terminal structured run result record."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_id: str
    spec_path: str
    repo_root: str
    source_repo_root: str | None = None
    worktree_path: str | None = None
    review_patch: str | None = None
    commit_hash: str | None = None
    worktree_rolled_back: bool = False
    status: RunStatus
    started_at: str
    finished_at: str
    total_duration_seconds: float
    baseline_commit: str | None = None
    worker_model: str | None = None
    policy_path: str | None = None
    policy_sha256: str | None = None
    spec_sha256: str | None = None
    policy_baseline_commit: str | None = None
    initial_status: str | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    final_diff: DiffSummary | None = None
    failure: FailureRecord | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy_provenance(self) -> RunResult:
        values = (
            self.policy_path,
            self.policy_sha256,
            self.spec_sha256,
            self.policy_baseline_commit,
        )
        if any(value is not None for value in values):
            if any(value is None for value in values):
                raise ValueError("Task policy provenance fields must be present together")
            if re.fullmatch(r"[0-9a-f]{64}", self.policy_sha256 or "") is None:
                raise ValueError("Invalid task policy provenance hash")
            if re.fullmatch(r"[0-9a-f]{64}", self.spec_sha256 or "") is None:
                raise ValueError("Invalid task contract provenance hash")
            if (
                re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.policy_baseline_commit or "")
                is None
            ):
                raise ValueError("Invalid task policy baseline commit")
        return self
