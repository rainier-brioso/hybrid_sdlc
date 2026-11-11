from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from helpers.repositories import create_spec_repo

from hybrid_sdlc import aider_runner
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunResult, RunStatus


@pytest.mark.parametrize(
    ("stdout", "stderr", "exit_code", "expected_indicator", "truncated"),
    [
        ("", "", 0, None, False),
        ("", "synthetic provider failure", 7, None, False),
        ("The LLM did not conform to the edit format.", "", 0, "malformed_edit_warning", False),
        ("x" * 8192, "", 0, None, True),
        (
            "-- possibly exceeded output limit! HEALTH_CANARY_72kg_2020-01-01",
            "api_key=SECRET_CANARY_123456 C:/private/notes.txt",
            0,
            "output_limit_warning",
            False,
        ),
    ],
)
def test_empty_patch_persists_safe_diagnostics_without_candidate_test_or_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
    exit_code: int,
    expected_indicator: str | None,
    truncated: bool,
) -> None:
    repo = create_spec_repo(tmp_path)
    fake = tmp_path / "fake_aider.py"
    fake.write_text(
        f"import sys\nsys.stdout.write({stdout!r})\nsys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    original_test = aider_runner.run_test_profile
    test_calls = 0

    def count_tests(*args: Any, **kwargs: Any) -> Any:
        nonlocal test_calls
        test_calls += 1
        return original_test(*args, **kwargs)

    monkeypatch.setattr(aider_runner, "run_test_profile", count_tests)
    result = aider_runner.run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="SYNTHETIC-EMPTY-PATCH",
        profile=CommandProfile(name="synthetic", argv=[sys.executable, "-c", "pass"]),
        resolved_test_executable=Path(sys.executable),
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="synthetic-no-network",
        aider_cmd=[sys.executable, str(fake)],
        max_retries=2,
        buffer_cap_bytes=1024,
    )
    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "LOOP_ZERO_DIFF"
    assert result.attempts == []  # AttemptRecord still means an edit tested as a candidate.
    assert test_calls == 1  # Baseline only; no candidate test ran.
    assert result.final_diff is not None and result.final_diff.is_empty
    record = repo / ".hybrid_sdlc" / "runs" / f"{result.run_id}.json"
    persisted = RunResult.model_validate_json(record.read_text(encoding="utf-8"))
    assert persisted.failure == result.failure
    metadata = result.failure.details["aider_diagnostics"]
    assert metadata["attempt_index"] == 1
    assert metadata["output_truncated"] is truncated
    assert metadata["candidate_tests_run"] is False
    assert metadata["cause"] == "unknown"
    assert metadata["retained_stdout_bytes"] <= 1024
    assert metadata["retained_stderr_bytes"] <= 1024
    if not truncated:
        assert metadata["exit_code"] == exit_code
    assert metadata["output_indicators"] == ([expected_indicator] if expected_indicator else [])
    assert len(json.dumps(metadata).encode()) < 1024
    for artifact in (repo / ".hybrid_sdlc").rglob("*.json"):
        text = artifact.read_text(encoding="utf-8")
        for forbidden in ("HEALTH_CANARY", "72kg", "2020-01-01", "SECRET_CANARY", "notes.txt"):
            assert forbidden not in text
