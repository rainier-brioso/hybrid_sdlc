"""Integration tests for CLI subcommands: check, run-task, clean."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from hybrid_sdlc import spec_initializer
from hybrid_sdlc.cli import cli
from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.errors import ExitCode
from hybrid_sdlc.job_manager import JobManager, JobRecord, JobStatus
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.processes import get_process_identity
from hybrid_sdlc.spec_initializer import SpecKitCapability, SpecKitFeature
from hybrid_sdlc.submission import JobSubmissionError

_orig_client = httpx.Client


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(path), check=True, capture_output=True
    )


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "run-task" in result.output
    assert "submit" in result.output
    assert "status" in result.output
    assert "cancel" in result.output
    assert "clean" in result.output
    assert "init" in result.output

    run_help = runner.invoke(cli, ["run-task", "--help"])
    assert run_help.exit_code == 0
    assert "--commit" in run_help.output
    assert "--rollback-on-failure" in run_help.output


def _mock_spec_kit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spec_initializer,
        "detect_spec_kit",
        lambda: SpecKitCapability("specify", "test", (SpecKitFeature("spec", True),), True, "ok"),
    )


def test_cli_init_empty_git_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    _mock_spec_kit(monkeypatch)
    result = CliRunner().invoke(cli, ["init", "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["repository"] == str(tmp_path.resolve())
    assert all(item["action"] == "created" for item in data["files"])
    assert (tmp_path / data["manifest_path"]).is_file()


def test_cli_init_existing_spec_kit_project_preserves_custom_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    _mock_spec_kit(monkeypatch)
    template = tmp_path / ".specify" / "templates" / "spec-template.md"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"existing Spec Kit customization")
    result = CliRunner().invoke(cli, ["init", "--target-dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    actions = {item["path"]: item["action"] for item in json.loads(result.output)["files"]}
    assert actions[".specify/templates/spec-template.md"] == "preserved"
    assert template.read_bytes() == b"existing Spec Kit customization"


def test_cli_init_dry_run_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    _mock_spec_kit(monkeypatch)
    result = CliRunner().invoke(cli, ["init", "--target-dir", str(tmp_path), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["dry_run"] is True
    assert all(item["action"].startswith("would_") for item in data["files"])
    assert not (tmp_path / ".specify").exists()
    assert not (tmp_path / ".hybrid-sdlc-init-journal.json").exists()


def test_cli_init_force_reports_backup_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    _mock_spec_kit(monkeypatch)
    template = tmp_path / ".specify" / "memory" / "constitution.md"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"custom constitution")
    result = CliRunner().invoke(cli, ["init", "--target-dir", str(tmp_path), "--force", "--json"])
    assert result.exit_code == 0, result.output
    item = next(
        item
        for item in json.loads(result.output)["files"]
        if item["path"].endswith("constitution.md")
    )
    assert item["action"] == "updated"
    assert item["backup_path"]
    assert (tmp_path / item["backup_path"]).read_bytes() == b"custom constitution"


@pytest.mark.parametrize("target", ["missing", "not-repository", "nested"])
def test_cli_init_target_errors_are_actionable_json(
    tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_spec_kit(monkeypatch)
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    if target == "missing":
        path = tmp_path / "does-not-exist"
    elif target == "not-repository":
        path = tmp_path / "plain"
        path.mkdir()
    else:
        path = repo / "nested"
        path.mkdir()
    result = CliRunner().invoke(cli, ["init", "--target-dir", str(path), "--json"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["message"]
    assert data["code"]


def test_cli_check_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen2.5-Coder-32B-Instruct"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check", "--repo-root", str(tmp_path), "--host-url", "http://127.0.0.1:8090/v1", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["available"] is True
    assert data["matched_model"] == "Qwen/Qwen2.5-Coder-32B-Instruct"


def test_cli_check_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check", "--repo-root", str(tmp_path), "--host-url", "http://127.0.0.1:8090/v1", "--json"],
    )
    assert result.exit_code == ExitCode.SERVER_PROBE_ERROR
    data = json.loads(result.output)
    assert data["available"] is False


def test_cli_clean(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    runs_dir = tmp_path / ".hybrid_sdlc" / "runs"
    runs_dir.mkdir(parents=True)
    old_run = runs_dir / "old_run.json"
    old_run.write_text("{}", encoding="utf-8")

    import os

    past = time.time() - 1000
    os.utime(old_run, (past, past))

    runner = CliRunner()
    # Dry run
    res_dry = runner.invoke(
        cli, ["clean", "--repo-root", str(tmp_path), "--older-than", "500s", "--dry-run", "--json"]
    )
    assert res_dry.exit_code == 0
    data_dry = json.loads(res_dry.output)
    assert data_dry["deleted_count"] == 1
    assert old_run.exists()

    # Real clean
    res_real = runner.invoke(
        cli, ["clean", "--repo-root", str(tmp_path), "--older-than", "500s", "--json"]
    )
    assert res_real.exit_code == 0
    data_real = json.loads(res_real.output)
    assert data_real["deleted_count"] == 1
    assert not old_run.exists()


def test_cli_run_task_rejects_uncommitted_spec(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    # Commit config with test profile
    config_file = tmp_path / "hybrid_sdlc.toml"
    config_file.write_text(
        "[command_profiles.pytest]\nargv = ['pytest']\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add config"], cwd=str(tmp_path), check=True, capture_output=True
    )

    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    # The task spec itself must be committed, even though other dirty source files are allowed.

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run-task",
            str(spec),
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--json",
        ],
    )
    assert result.exit_code == ExitCode.POLICY_ERROR
    data = json.loads(result.output)
    assert data["code"] == "REPOSITORY_ERROR"


def test_cli_run_task_json_stdout_only(tmp_path: Path) -> None:
    """run-task --json must emit ONLY valid JSON on stdout — no decorative text (HSDLC-029).

    We deliberately trigger an uncommitted-spec rejection so we don't need a model,
    then assert the raw output is a single parseable JSON object and nothing else.
    """
    _init_git_repo(tmp_path)

    config_file = tmp_path / "hybrid_sdlc.toml"
    config_file.write_text(
        "[command_profiles.pytest]\nargv = ['pytest']\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add config"], cwd=str(tmp_path), check=True, capture_output=True
    )

    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    # Leave spec untracked → committed-spec validation triggers early rejection.

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run-task",
            str(spec),
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--json",
        ],
    )

    # The stdout must be exactly one JSON object with no leading/trailing decoration
    stdout = result.output.strip()
    parsed = json.loads(stdout)  # Raises if not valid JSON or if there is extra text
    assert isinstance(parsed, dict)
    # Ensure no decorative lines snuck in before/after the JSON object
    assert stdout.startswith("{") and stdout.endswith("}")


def test_cli_run_task_forwards_explicit_commit_and_reports_isolated_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    config_file = tmp_path / "hybrid_sdlc.toml"
    config_file.write_text("[command_profiles.pytest]\nargv = ['pytest']\n", encoding="utf-8")
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    subprocess.run(["git", "add", "hybrid_sdlc.toml", "spec.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add task inputs"], cwd=tmp_path, check=True)
    (tmp_path / "untracked.txt").write_text("preserve", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            run_id="run_cli_commit",
            task_id="T-1",
            spec_path="spec.md",
            repo_root=str(tmp_path),
            source_repo_root=str(tmp_path),
            worktree_path=str(tmp_path / "isolated"),
            review_patch=str(tmp_path / "isolated.patch"),
            commit_hash="a" * 40,
            status=RunStatus.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            total_duration_seconds=0.1,
        )

    monkeypatch.setattr(
        "hybrid_sdlc.cli.resolve_profile_executable", lambda profile, root: Path("pytest")
    )
    monkeypatch.setattr(
        "hybrid_sdlc.cli.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8090/v1"), None),
    )
    monkeypatch.setattr("hybrid_sdlc.cli.run_bounded_loop", fake_run)

    response = CliRunner().invoke(
        cli,
        [
            "run-task",
            str(spec),
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--commit",
            "--json",
        ],
    )

    assert response.exit_code == 0, response.output
    payload = json.loads(response.output)
    assert captured["commit_requested"] is True
    assert captured["rollback_on_failure"] is False
    assert payload["commit_hash"] == "a" * 40
    assert payload["worktree_path"] == str(tmp_path / "isolated")
    assert payload["review_patch"] == str(tmp_path / "isolated.patch")
    assert (tmp_path / "untracked.txt").read_text(encoding="utf-8") == "preserve"


def test_cli_submit_human_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_git_repo(tmp_path)
    manager = JobManager(tmp_path)
    queued = manager.create("spec.md", "T-1", test_profile="pytest")
    identity = get_process_identity(os.getpid())
    assert identity.created_at is not None
    record = manager.claim(queued.job_id, os.getpid(), identity.created_at)
    monkeypatch.setattr("hybrid_sdlc.cli.submit_job", lambda **kwargs: record)
    runner = CliRunner()
    args = [
        "submit",
        "spec.md",
        "--repo-root",
        str(tmp_path),
        "--task-id",
        "T-1",
        "--test-profile",
        "pytest",
    ]

    human = runner.invoke(cli, args)
    assert human.exit_code == 0
    assert f"Submitted job {record.job_id} (RUNNING)" in human.output

    machine = runner.invoke(cli, [*args, "--json"])
    assert machine.exit_code == 0
    assert json.loads(machine.output)["job_id"] == record.job_id


def test_cli_submit_error_json_includes_allocated_job_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    diagnostic_id = "job_20260101T010203000000Z_0123abcd"
    monkeypatch.setattr(
        "hybrid_sdlc.cli.submit_job",
        lambda **kwargs: (_ for _ in ()).throw(JobSubmissionError(diagnostic_id, "startup failed")),
    )
    result = CliRunner().invoke(
        cli,
        [
            "submit",
            "spec.md",
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--json",
        ],
    )
    assert result.exit_code == ExitCode.POLICY_ERROR
    data = json.loads(result.output)
    assert data["code"] == "JOB_SUBMISSION_FAILED"
    assert data["details"]["job_id"] == diagnostic_id


def test_cli_status_lists_jobs_and_emits_json(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    record = JobManager(tmp_path).create("spec.md", "T-1")
    runner = CliRunner()

    human = runner.invoke(cli, ["status", "--repo-root", str(tmp_path)])
    assert human.exit_code == 0
    assert f"Job {record.job_id}: QUEUED" in human.output

    machine = runner.invoke(cli, ["status", "--repo-root", str(tmp_path), "--json"])
    assert machine.exit_code == 0
    assert json.loads(machine.output)["jobs"][0]["job_id"] == record.job_id


def test_cli_status_by_id_waits_and_reports_missing_or_invalid_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    manager = JobManager(tmp_path)
    record = manager.create("spec.md", "T-1")
    observed: dict[str, float] = {}

    def capture_status(self: JobManager, job_id: str, **kwargs: float) -> JobRecord:
        observed.update(kwargs)
        return self.get(job_id)

    monkeypatch.setattr(JobManager, "status", capture_status)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["status", record.job_id, "--repo-root", str(tmp_path), "--wait", "--json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["job_id"] == record.job_id
    assert observed == {"wait_timeout_seconds": 60.0, "poll_interval_seconds": 0.25}

    missing = runner.invoke(
        cli,
        ["status", "job_20260101T010203000000Z_0123abcd", "--repo-root", str(tmp_path), "--json"],
    )
    assert missing.exit_code == ExitCode.POLICY_ERROR
    assert json.loads(missing.output)["code"] == "JOB_NOT_FOUND"

    invalid = runner.invoke(cli, ["status", "../../unsafe", "--repo-root", str(tmp_path), "--json"])
    assert invalid.exit_code == ExitCode.POLICY_ERROR
    assert json.loads(invalid.output)["code"] == "INVALID_JOB_ID"


def test_cli_status_rejects_wait_without_id_as_json(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = CliRunner().invoke(cli, ["status", "--repo-root", str(tmp_path), "--wait", "--json"])
    assert result.exit_code == ExitCode.POLICY_ERROR
    assert json.loads(result.output)["code"] == "INVALID_STATUS_OPTIONS"


def test_cli_cancel_running_job_reports_request_without_claiming_termination(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    manager = JobManager(tmp_path)
    created = manager.create("spec.md", "T-1")
    identity = get_process_identity(os.getpid())
    assert identity.created_at is not None
    manager.claim(created.job_id, os.getpid(), identity.created_at)
    runner = CliRunner()

    human = runner.invoke(cli, ["cancel", created.job_id, "--repo-root", str(tmp_path)])
    assert human.exit_code == 0
    assert "Cancellation requested" in human.output
    assert "status remains RUNNING" in human.output


def test_cli_cancel_queued_job_json_and_invalid_id(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    manager = JobManager(tmp_path)
    record = manager.create("spec.md", "T-1")
    runner = CliRunner()

    cancelled = runner.invoke(
        cli, ["cancel", record.job_id, "--repo-root", str(tmp_path), "--json"]
    )
    assert cancelled.exit_code == 0
    data = json.loads(cancelled.output)
    assert data["job"]["status"] == JobStatus.CANCELLED.value
    assert data["cancellation"] == "cancelled"

    invalid = runner.invoke(cli, ["cancel", "../../unsafe", "--repo-root", str(tmp_path), "--json"])
    assert invalid.exit_code == ExitCode.POLICY_ERROR
    assert json.loads(invalid.output)["code"] == "INVALID_JOB_ID"
