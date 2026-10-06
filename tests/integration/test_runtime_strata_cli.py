"""User-facing lifecycle commands, without touching Docker or real user state."""

from __future__ import annotations

import json
import multiprocessing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hybrid_sdlc import runtime_strata
from hybrid_sdlc.cli import cli
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.models import RunResult, RunStatus


def _hold_child_lease(root: str, ready: Any, release: Any) -> None:
    """Spawn-safe child using test state, never the real user's registry."""
    with patch.object(runtime_strata, "_state_root", return_value=Path(root)):
        with runtime_strata.sync_task_lease("http://localhost:8080/v1"):
            ready.set()
            if not release.wait(30):
                raise RuntimeError("Parent did not release the test lease")


@pytest.fixture
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".hybrid-sdlc" / "runtime" / "strata"
    monkeypatch.setattr(runtime_strata, "_state_root", lambda: root)
    return root


def test_lifecycle_help_is_distinct_from_job_status(runtime_root: Path) -> None:
    result = CliRunner().invoke(cli, ["runtime", "strata", "--help"])
    assert result.exit_code == 0
    for command in ("configure", "start", "stop", "restart", "status", "logs"):
        assert command in result.output
    assert not runtime_root.exists()


def test_configure_uses_packaged_assets_not_repository_compose(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "untrusted-compose"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {danger: {image: unwanted}}", encoding="utf-8")
    monkeypatch.chdir(repo)

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Configure must not contact Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    result = CliRunner().invoke(cli, ["runtime", "strata", "configure", "--port", "8091", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] is True
    assert payload["endpoint"] == "http://127.0.0.1:8091/v1"
    compose = (runtime_root / "compose.yaml").read_text(encoding="utf-8")
    assert "127.0.0.1:8091:8080" in compose
    assert "unwanted" not in compose
    assert "strata-server" in compose


def test_repeat_configure_preserves_identity(runtime_root: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert first.exit_code == 0, first.output
    original = (runtime_root / "runtime.json").read_bytes()
    again = runner.invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert again.exit_code == 0, again.output
    assert json.loads(again.output)["changed"] is False
    assert (runtime_root / "runtime.json").read_bytes() == original


def test_configure_refuses_changed_identity_with_json_error(runtime_root: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["runtime", "strata", "configure"]).exit_code == 0
    original = (runtime_root / "runtime.json").read_bytes()
    result = runner.invoke(cli, ["runtime", "strata", "configure", "--port", "8091", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert (runtime_root / "runtime.json").read_bytes() == original


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status", "logs"])
def test_unconfigured_actions_return_structured_failure(runtime_root: Path, action: str) -> None:
    result = CliRunner().invoke(cli, ["runtime", "strata", action, "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert not runtime_root.exists()


def test_corrupt_activity_returns_structured_failure(runtime_root: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["runtime", "strata", "configure"]).exit_code == 0
    (runtime_root / "activity.json").write_bytes(b"\xff\xfeinvalid")
    result = runner.invoke(cli, ["runtime", "strata", "status", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"


def test_permission_failure_returns_structured_failure(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_mkdir = Path.mkdir

    def denied(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == runtime_root:
            raise PermissionError("Test denied runtime directory access")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    result = CliRunner().invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert not runtime_root.exists()


def test_log_tail_is_forwarded_without_following(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import cli as cli_module

    received: list[int] = []

    def snapshot(*, tail: int) -> dict[str, str]:
        received.append(tail)
        return {"status": "ok", "logs": "bounded snapshot"}

    monkeypatch.setattr(cli_module, "strata_runtime_logs", snapshot)
    result = CliRunner().invoke(cli, ["runtime", "strata", "logs", "--tail", "23", "--json"])
    assert result.exit_code == 0, result.output
    assert received == [23]
    assert json.loads(result.output)["logs"] == "bounded snapshot"
    invalid = CliRunner().invoke(cli, ["runtime", "strata", "logs", "--tail", "10001"])
    assert invalid.exit_code != 0
    assert received == [23]


def test_another_process_activity_blocks_stop(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    child = context.Process(target=_hold_child_lease, args=(str(runtime_root), ready, release))

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Busy refusal must happen before contacting Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    child.start()
    try:
        assert ready.wait(20), "Child failed to reserve the managed endpoint"
        with pytest.raises(runtime_strata.RuntimeManagementError, match="queued or running"):
            runtime_strata.stop()
    finally:
        release.set()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0
    assert runtime_strata._load_config()[2]["leases"] == []


def test_submission_reserves_before_launch_and_releases_after_launch_failure(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import submission

    runtime_strata.configure()
    repo = tmp_path / "task-repo"
    repo.mkdir()
    monkeypatch.setattr(
        submission,
        "preflight_task_request",
        lambda **kwargs: (repo, "spec.md", "http://127.0.0.1:8080/v1", "model", 1),
    )
    monkeypatch.setattr(submission, "_verify_windows_worker_breakaway", lambda: None)
    submitted: list[str] = []

    def fail_launch(*args: object, **kwargs: object) -> Any:
        lease = runtime_strata._load_config()[2]["leases"][0]
        submitted.append(lease["job_id"])
        assert lease["kind"] == "job"
        assert JobManager(repo).get(lease["job_id"]).status is JobStatus.QUEUED
        raise OSError("Simulated worker launch failure")

    monkeypatch.setattr(submission, "_launch_worker", fail_launch)
    with pytest.raises(submission.JobSubmissionError, match="launch failed"):
        submission.submit_job(
            repo_root=repo, spec_path="spec.md", task_id="T001", test_profile="smoke"
        )
    assert len(submitted) == 1
    assert JobManager(repo).get(submitted[0]).status is JobStatus.FAILED
    assert runtime_strata._load_config()[2]["leases"] == []


@pytest.mark.parametrize("execution_fails", [False, True])
def test_worker_terminalization_releases_managed_reservation(
    runtime_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    execution_fails: bool,
) -> None:
    from hybrid_sdlc import worker

    runtime_strata.configure()
    repo = tmp_path / "worker-repo"
    repo.mkdir()
    manager = JobManager(repo)
    job = runtime_strata.reserve_async_task(
        "http://localhost:8080/v1",
        repo,
        lambda job_id: manager.create(
            "spec.md", "T001", job_id=job_id, host_url="http://localhost:8080/v1"
        ),
    )
    monkeypatch.setattr(worker, "verify_repo_root", lambda root: repo)

    def execute(*args: object, **kwargs: object) -> RunResult:
        assert runtime_strata._load_config()[2]["leases"]
        assert manager.get(job.job_id).status is JobStatus.RUNNING
        if execution_fails:
            raise RuntimeError("Simulated execution failure")
        return RunResult(
            run_id="run_lifecycle_hook_test",
            task_id="T001",
            spec_path="spec.md",
            repo_root=str(repo),
            status=RunStatus.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            total_duration_seconds=0.1,
        )

    monkeypatch.setattr(worker, "_execute", execute)
    result = worker.run_worker(job.job_id, repo)
    assert result.status is (JobStatus.FAILED if execution_fails else JobStatus.COMPLETED)
    assert manager.get(job.job_id).status is result.status
    assert runtime_strata._load_config()[2]["leases"] == []


def test_submission_failure_with_corrupt_record_keeps_protection(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()
    repo = tmp_path / "corrupt-job-repo"
    repo.mkdir()
    manager = JobManager(repo)

    def create_then_corrupt(job_id: str) -> Any:
        manager.create("spec.md", "T001", job_id=job_id)
        manager._job_path(job_id).write_text("{broken-json", encoding="utf-8")
        raise RuntimeError("Simulated failure with uncertain persisted state")

    with pytest.raises(RuntimeError, match="uncertain persisted state"):
        runtime_strata.reserve_async_task("http://127.0.0.1:8080/v1", repo, create_then_corrupt)
    leases = runtime_strata._load_config()[2]["leases"]
    assert len(leases) == 1
    assert leases[0]["kind"] == "submitting"

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Uncertain job state must block before Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    with pytest.raises(runtime_strata.RuntimeManagementError, match="queued or running"):
        runtime_strata.stop()
