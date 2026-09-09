"""Unit tests for error and exit code contracts."""

from __future__ import annotations

from hybrid_sdlc.errors import (
    BaselineTestFailureError,
    CancellationError,
    CommandPolicyError,
    ConfigurationError,
    ExitCode,
    HybridSDLCError,
    ModelNotFoundError,
    PathTraversalError,
    ProcessExecutionError,
    RepositoryRootNotFoundError,
    RetryExhaustedError,
    SecurityBoundaryError,
    ServerProbeError,
    StuckLoopError,
    TaskTimeoutError,
    WorktreeDirtyError,
    WorktreeLockError,
)


def test_exit_codes_values() -> None:
    assert ExitCode.SUCCESS == 0
    assert ExitCode.INTERNAL_ERROR == 1
    assert ExitCode.CONFIG_ERROR == 2
    assert ExitCode.POLICY_ERROR == 3
    assert ExitCode.SERVER_PROBE_ERROR == 4
    assert ExitCode.WORKTREE_DIRTY_OR_LOCKED == 5
    assert ExitCode.TASK_FAILED == 6
    assert ExitCode.CANCELLED == 7


def test_error_to_failure_record() -> None:
    err = PathTraversalError(
        "Path traversal detected",
        details={"path": "../secret.txt"},
    )
    record = err.to_failure_record()

    assert record.schema_version == "1.0.0"
    assert record.code == "PATH_TRAVERSAL"
    assert record.message == "Path traversal detected"
    assert record.details == {"path": "../secret.txt"}
    assert err.exit_code == ExitCode.POLICY_ERROR


def test_error_hierarchy_and_exit_codes() -> None:
    cases = [
        (ConfigurationError("cfg"), ExitCode.CONFIG_ERROR, "CONFIG_ERROR"),
        (RepositoryRootNotFoundError("repo"), ExitCode.POLICY_ERROR, "REPO_ROOT_NOT_FOUND"),
        (SecurityBoundaryError("sec"), ExitCode.POLICY_ERROR, "SECURITY_BOUNDARY_VIOLATION"),
        (PathTraversalError("path"), ExitCode.POLICY_ERROR, "PATH_TRAVERSAL"),
        (CommandPolicyError("cmd"), ExitCode.POLICY_ERROR, "COMMAND_POLICY_VIOLATION"),
        (ServerProbeError("probe"), ExitCode.SERVER_PROBE_ERROR, "SERVER_PROBE_FAILED"),
        (ModelNotFoundError("model"), ExitCode.SERVER_PROBE_ERROR, "MODEL_NOT_FOUND"),
        (WorktreeDirtyError("dirty"), ExitCode.WORKTREE_DIRTY_OR_LOCKED, "WORKTREE_DIRTY"),
        (WorktreeLockError("locked"), ExitCode.WORKTREE_DIRTY_OR_LOCKED, "WORKTREE_LOCKED"),
        (BaselineTestFailureError("baseline"), ExitCode.TASK_FAILED, "BASELINE_TEST_FAILED"),
        (StuckLoopError("stuck"), ExitCode.TASK_FAILED, "LOOP_STUCK"),
        (TaskTimeoutError("timeout"), ExitCode.TASK_FAILED, "TASK_TIMEOUT"),
        (RetryExhaustedError("retries"), ExitCode.TASK_FAILED, "RETRY_EXHAUSTED"),
        (CancellationError("cancel"), ExitCode.CANCELLED, "CANCELLED"),
        (ProcessExecutionError("proc"), ExitCode.TASK_FAILED, "PROCESS_EXECUTION_ERROR"),
    ]

    for err, expected_exit, expected_code in cases:
        assert isinstance(err, HybridSDLCError)
        assert err.exit_code == expected_exit
        assert err.code == expected_code
        rec = err.to_failure_record()
        assert rec.code == expected_code
        assert rec.message == err.message
