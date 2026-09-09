"""Typed error and exit-code contracts for hybrid-sdlc."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from hybrid_sdlc.models import FailureRecord


class ExitCode(IntEnum):
    """Documented stable exit codes for hybrid-sdlc CLI."""

    SUCCESS = 0
    INTERNAL_ERROR = 1
    CONFIG_ERROR = 2
    POLICY_ERROR = 3
    SERVER_PROBE_ERROR = 4
    WORKTREE_DIRTY_OR_LOCKED = 5
    TASK_FAILED = 6
    CANCELLED = 7


class HybridSDLCError(Exception):
    """Base exception for all hybrid-sdlc operational failures."""

    default_code: str = "INTERNAL_ERROR"
    default_exit_code: ExitCode = ExitCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        exit_code: ExitCode | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}
        self.exit_code = exit_code or self.default_exit_code

    def to_failure_record(self) -> FailureRecord:
        """Convert this error into a serializable FailureRecord."""
        return FailureRecord(
            code=self.code,
            message=self.message,
            details=self.details,
        )


class ConfigurationError(HybridSDLCError):
    """Invalid configuration, missing config keys, or command profile misconfiguration."""

    default_code = "CONFIG_ERROR"
    default_exit_code = ExitCode.CONFIG_ERROR


class RepositoryError(HybridSDLCError):
    """Base repository interaction error."""

    default_code = "REPOSITORY_ERROR"
    default_exit_code = ExitCode.POLICY_ERROR


class RepositoryRootNotFoundError(RepositoryError):
    """Repository root could not be determined or is not a git repository."""

    default_code = "REPO_ROOT_NOT_FOUND"
    default_exit_code = ExitCode.POLICY_ERROR


class SecurityBoundaryError(HybridSDLCError):
    """Security policy violation (path traversal, unauthorized command, disallowed env)."""

    default_code = "SECURITY_BOUNDARY_VIOLATION"
    default_exit_code = ExitCode.POLICY_ERROR


class PathTraversalError(SecurityBoundaryError):
    """Path escapes the verified repository boundary."""

    default_code = "PATH_TRAVERSAL"
    default_exit_code = ExitCode.POLICY_ERROR


class CommandPolicyError(SecurityBoundaryError):
    """Test command profile or argument violates security policy."""

    default_code = "COMMAND_POLICY_VIOLATION"
    default_exit_code = ExitCode.POLICY_ERROR


class ServerProbeError(HybridSDLCError):
    """Failure probing or communicating with local model server."""

    default_code = "SERVER_PROBE_FAILED"
    default_exit_code = ExitCode.SERVER_PROBE_ERROR


class ModelNotFoundError(ServerProbeError):
    """Requested model not found on local endpoint."""

    default_code = "MODEL_NOT_FOUND"
    default_exit_code = ExitCode.SERVER_PROBE_ERROR


class WorktreeDirtyError(RepositoryError):
    """Worktree has uncommitted changes, untracked files, or ongoing merge/rebase."""

    default_code = "WORKTREE_DIRTY"
    default_exit_code = ExitCode.WORKTREE_DIRTY_OR_LOCKED


class WorktreeLockError(RepositoryError):
    """Repository worktree is currently locked by another runner."""

    default_code = "WORKTREE_LOCKED"
    default_exit_code = ExitCode.WORKTREE_DIRTY_OR_LOCKED


class TaskExecutionError(HybridSDLCError):
    """Task editing and test execution loop failed."""

    default_code = "TASK_EXECUTION_FAILED"
    default_exit_code = ExitCode.TASK_FAILED


class BaselineTestFailureError(TaskExecutionError):
    """Baseline test check failed before any edits were applied."""

    default_code = "BASELINE_TEST_FAILED"
    default_exit_code = ExitCode.TASK_FAILED


class StuckLoopError(TaskExecutionError):
    """Loop stuck due to repeated identical diffs or identical failure signatures."""

    default_code = "LOOP_STUCK"
    default_exit_code = ExitCode.TASK_FAILED


class TaskTimeoutError(TaskExecutionError):
    """Task execution exceeded the allocated wall-clock timeout."""

    default_code = "TASK_TIMEOUT"
    default_exit_code = ExitCode.TASK_FAILED


class RetryExhaustedError(TaskExecutionError):
    """Task execution exhausted maximum retry attempts without passing tests."""

    default_code = "RETRY_EXHAUSTED"
    default_exit_code = ExitCode.TASK_FAILED


class CancellationError(HybridSDLCError):
    """Task execution was cancelled by user or host."""

    default_code = "CANCELLED"
    default_exit_code = ExitCode.CANCELLED


class ProcessExecutionError(HybridSDLCError):
    """Subprocess terminated abnormally or timed out."""

    default_code = "PROCESS_EXECUTION_ERROR"
    default_exit_code = ExitCode.TASK_FAILED


class InternalError(HybridSDLCError):
    """Unexpected internal failure or bug."""

    default_code = "INTERNAL_ERROR"
    default_exit_code = ExitCode.INTERNAL_ERROR
