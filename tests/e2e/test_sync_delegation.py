"""End-to-end tests for successful synchronous delegation (HSDLC-034).

Verifies: a failing fixture is edited, tests pass, artifacts are recorded,
and no commit is created.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers.repositories import (
    create_successful_edit_repo,
)

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunStatus


def _get_git_status(repo: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_successful_delegation_produces_passing_artifacts(tmp_path: Path) -> None:
    repo, fake_aider = create_successful_edit_repo(tmp_path)
    spec = repo / "spec.md"
    py_name = Path(sys.executable).name

    profile = CommandProfile(
        name="verify",
        argv=[
            py_name,
            "-c",
            "import os, sys\n"
            "if not os.path.exists('solution.py'): sys.exit(0)\n"
            "import solution\n"
            "assert solution.answer() == 42\n"
            "print('ALL PASS')",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-E2E-001",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake-model",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.SUCCESS
    assert result.failure is None
    assert len(result.attempts) >= 1
    assert result.attempts[0].test_passed
    assert result.final_diff is not None
    assert not result.final_diff.is_empty
    assert "solution.py" in result.final_diff.changed_files
    assert result.baseline_commit is not None

    run_record_path = repo / ".hybrid_sdlc" / "runs" / f"{result.run_id}.json"
    assert run_record_path.exists()
    run_record = json.loads(run_record_path.read_text(encoding="utf-8"))
    assert run_record["status"] == "success"
    assert run_record["task_id"] == "TASK-E2E-001"
    assert run_record["schema_version"] == "1.0.0"


def test_successful_delegation_no_git_commit(tmp_path: Path) -> None:
    repo, fake_aider = create_successful_edit_repo(tmp_path)
    spec = repo / "spec.md"
    py_name = Path(sys.executable).name

    profile = CommandProfile(
        name="verify",
        argv=[
            py_name,
            "-c",
            "import os, sys\n"
            "if not os.path.exists('solution.py'): sys.exit(0)\n"
            "import solution\n"
            "assert solution.answer() == 42\n"
            "print('PASS')",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-E2E-NO-COMMIT",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake-model",
        max_retries=2,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    status = _get_git_status(repo)
    assert "solution.py" in status, "solution.py should appear as untracked"


def test_successful_delegation_diff_contains_expected_files(tmp_path: Path) -> None:
    repo, fake_aider = create_successful_edit_repo(tmp_path)
    spec = repo / "spec.md"
    py_name = Path(sys.executable).name

    profile = CommandProfile(
        name="verify",
        argv=[
            py_name,
            "-c",
            "import os, sys\n"
            "if not os.path.exists('solution.py'): sys.exit(0)\n"
            "import solution\n"
            "print('ok')",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-E2E-FILES",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake-model",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.SUCCESS
    assert result.final_diff is not None
    changed = result.final_diff.changed_files
    assert "solution.py" in changed
    assert "helpers" not in changed

    assert result.final_diff.patch_file is not None
    patch_path = repo / result.final_diff.patch_file
    assert patch_path.exists()
    patch_content = patch_path.read_text(encoding="utf-8")
    assert "solution.py" in patch_content


def test_successful_delegation_with_secret_in_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, fake_aider = create_successful_edit_repo(tmp_path)
    spec = repo / "spec.md"
    py_name = Path(sys.executable).name

    # Inject the actual secret value into the environment
    monkeypatch.setenv("API_KEY", "sk-live-secret-value-12345")

    profile = CommandProfile(
        name="verify_with_env",
        argv=[
            py_name,
            "-c",
            "import os, sys\n"
            'actual_key = os.environ.get("API_KEY", "none")\n'
            'print(f"API_KEY={actual_key}")\n'
            'if not os.path.exists("solution.py"): sys.exit(0)\n'
            "import solution\n"
            "assert solution.answer() == 42",
        ],
        cwd=".",
        timeout_seconds=10,
        env_allowlist=["API_KEY"],
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-E2E-SECRET",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake-model",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.SUCCESS
    assert len(result.attempts) >= 1
    attempt = result.attempts[0]
    stdout_summary = attempt.test_stdout_summary or ""

    # Prove the injected secret is absent from artifacts
    assert "sk-live-secret-value-12345" not in stdout_summary

    # Prove redaction markers are present
    runs_dir = repo / ".hybrid_sdlc" / "runs"
    json_files = list(runs_dir.glob("*.json"))
    assert len(json_files) >= 1
    latest = max(json_files, key=lambda p: p.stat().st_mtime)
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert len(record.get("attempts", [])) >= 1
    record_stdout = record["attempts"][0].get("test_stdout_summary", "")
    assert "sk-live-secret-value-12345" not in record_stdout
    assert "[REDACTED]" in record_stdout
