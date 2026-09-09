"""Unit tests for versioned models and schemas."""

from __future__ import annotations

import pytest

from hybrid_sdlc.models import (
    SCHEMA_VERSION,
    AttemptRecord,
    DiffSummary,
    FailureRecord,
    ProbeResult,
    RunResult,
    RunStatus,
)


def test_schema_version_defaults() -> None:
    fail = FailureRecord(code="FAIL", message="test fail")
    assert fail.schema_version == SCHEMA_VERSION

    diff = DiffSummary(diff_hash="abc123")
    assert diff.schema_version == SCHEMA_VERSION

    probe = ProbeResult(url="http://127.0.0.1:8090/v1", available=True)
    assert probe.schema_version == SCHEMA_VERSION

    attempt = AttemptRecord(
        attempt_index=1,
        started_at="2026-09-09T18:00:00Z",
        finished_at="2026-09-09T18:01:00Z",
        duration_seconds=60.0,
        test_exit_code=0,
        test_passed=True,
    )
    assert attempt.schema_version == SCHEMA_VERSION


def test_failure_record_round_trip() -> None:
    original = FailureRecord(
        code="TEST_ERROR",
        message="Something failed",
        details={"key": "value", "count": 42},
    )
    dumped = original.model_dump_json()
    loaded = FailureRecord.model_validate_json(dumped)
    assert loaded == original


def test_run_result_round_trip_success() -> None:
    result = RunResult(
        run_id="run_12345",
        task_id="TASK-001",
        spec_path="specs/001/spec.md",
        repo_root="C:/Users/ray/repo",
        status=RunStatus.SUCCESS,
        started_at="2026-09-09T18:00:00Z",
        finished_at="2026-09-09T18:05:00Z",
        total_duration_seconds=300.0,
        attempts=[
            AttemptRecord(
                attempt_index=1,
                started_at="2026-09-09T18:00:00Z",
                finished_at="2026-09-09T18:02:00Z",
                duration_seconds=120.0,
                test_exit_code=1,
                test_passed=False,
                failure_signature="test_foo:AssertionError",
            ),
            AttemptRecord(
                attempt_index=2,
                started_at="2026-09-09T18:02:00Z",
                finished_at="2026-09-09T18:04:00Z",
                duration_seconds=120.0,
                test_exit_code=0,
                test_passed=True,
            ),
        ],
        final_diff=DiffSummary(
            diff_hash="deadbeef1234",
            changed_files=["src/module.py"],
            additions=10,
            deletions=2,
            patch_file=".hybrid_sdlc/runs/run_12345.patch",
        ),
        artifacts={"patch": ".hybrid_sdlc/runs/run_12345.patch"},
    )

    dumped = result.model_dump_json()
    loaded = RunResult.model_validate_json(dumped)
    assert loaded.status == RunStatus.SUCCESS
    assert len(loaded.attempts) == 2
    assert loaded.final_diff is not None
    assert loaded.final_diff.diff_hash == "deadbeef1234"


def test_run_result_round_trip_failure() -> None:
    result = RunResult(
        run_id="run_failed",
        task_id="TASK-002",
        spec_path="specs/001/spec.md",
        repo_root="C:/Users/ray/repo",
        status=RunStatus.FAILURE,
        started_at="2026-09-09T18:00:00Z",
        finished_at="2026-09-09T18:02:00Z",
        total_duration_seconds=120.0,
        failure=FailureRecord(
            code="LOOP_STUCK",
            message="Model produced identical diff twice in a row",
            details={"diff_hash": "samehash123"},
        ),
    )

    dumped = result.model_dump_json()
    loaded = RunResult.model_validate_json(dumped)
    assert loaded.status == RunStatus.FAILURE
    assert loaded.failure is not None
    assert loaded.failure.code == "LOOP_STUCK"


@pytest.mark.parametrize(
    "status",
    [RunStatus.FAILURE, RunStatus.ABORTED, RunStatus.CANCELLED],
)
def test_run_result_terminal_failure_categories_round_trip(status: RunStatus) -> None:
    result = RunResult(
        run_id=f"run_{status.value}",
        task_id="TASK-TERMINAL",
        spec_path="spec.md",
        repo_root="C:/repo",
        status=status,
        started_at="2026-09-09T18:00:00Z",
        finished_at="2026-09-09T18:00:01Z",
        total_duration_seconds=1.0,
        failure=FailureRecord(code=status.value.upper(), message="terminal result"),
    )

    loaded = RunResult.model_validate_json(result.model_dump_json())
    assert loaded.status == status
    assert loaded.failure is not None


def test_forward_compatibility_extra_fields() -> None:
    data = {
        "schema_version": "1.0.0",
        "code": "FOO",
        "message": "bar",
        "details": {},
        "some_future_field_v2": "ignored_gracefully",
    }
    loaded = FailureRecord.model_validate(data)
    assert loaded.code == "FOO"
    assert loaded.message == "bar"
    assert not hasattr(loaded, "some_future_field_v2")
