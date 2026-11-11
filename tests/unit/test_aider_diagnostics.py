from __future__ import annotations

import json

import pytest

from hybrid_sdlc.aider_diagnostics import SCAN_CHAR_LIMIT, empty_patch_metadata
from hybrid_sdlc.artifacts import redact_data
from hybrid_sdlc.processes import SubprocessResult


def _result(stdout: str = "", stderr: str = "", **kwargs: object) -> SubprocessResult:
    values = dict(
        exit_code=0,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=1.23456,
        is_truncated=False,
        timed_out=False,
        cancelled=False,
    )
    values.update(kwargs)
    return SubprocessResult(**values)  # type: ignore[arg-type]


def test_fixed_metadata_preserves_process_facts_without_copying_content() -> None:
    result = _result(
        "HEALTH_CANARY_72kg_2020-01-01 -- possibly exceeded output limit!",
        "C:/private/notes.txt api_key=private-secret-0123456789 "
        "The LLM did not conform to the edit format.",
        exit_code=3,
        is_truncated=True,
    )
    metadata = empty_patch_metadata(result, 2)
    assert metadata["attempt_index"] == 2
    assert metadata["exit_code"] == 3
    assert metadata["duration_seconds"] == 1.235
    assert metadata["output_truncated"] is True
    assert metadata["indicator_scan_incomplete"] is True
    assert metadata["retained_stdout_bytes"] == len(result.stdout.encode())
    assert metadata["retained_stderr_bytes"] == len(result.stderr.encode())
    assert metadata["output_indicators"] == ["malformed_edit_warning", "output_limit_warning"]
    assert metadata["cause"] == "unknown"
    assert metadata["candidate_tests_run"] is False
    assert redact_data(metadata) == metadata
    serialized = json.dumps(metadata)
    assert len(serialized.encode()) < 1024
    for forbidden in ("HEALTH_CANARY", "72kg", "2020-01-01", "private-secret", "notes.txt"):
        assert forbidden not in serialized


def test_warning_scan_is_bounded_and_does_not_join_stream_fragments() -> None:
    result = _result(
        "x" * SCAN_CHAR_LIMIT + "The LLM did not conform to the edit format.",
        "-- possibly exhausted context window!",
    )
    metadata = empty_patch_metadata(result, 1)
    assert metadata["output_indicators"] == ["context_limit_warning"]
    assert metadata["indicator_scan_incomplete"] is True
    split = _result("The LLM did not conform", " to the edit format.")
    assert empty_patch_metadata(split, 1)["output_indicators"] == []


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1.0, 1e30])
def test_invalid_numeric_metadata_remains_bounded_json(duration: float) -> None:
    metadata = empty_patch_metadata(_result(duration_seconds=duration, exit_code=10**100), 1)
    assert metadata["exit_code"] == 2**63 - 1
    assert len(json.dumps(metadata, allow_nan=False).encode()) < 1024


def test_absent_evidence_stays_unknown_even_with_successful_exit() -> None:
    metadata = empty_patch_metadata(_result(), 1)
    assert metadata["output_indicators"] == []
    assert metadata["cause"] == "unknown"
    assert metadata["candidate_tests_run"] is False
    assert metadata["retained_stdout_bytes"] == 0
    assert metadata["timed_out"] is False
    assert metadata["cancelled"] is False


def test_cancel_and_timeout_flags_are_process_facts_only() -> None:
    metadata = empty_patch_metadata(_result(timed_out=True, cancelled=True), 1)
    assert metadata["timed_out"] is True
    assert metadata["cancelled"] is True
    assert metadata["cause"] == "unknown"
