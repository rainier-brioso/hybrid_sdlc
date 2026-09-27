"""Integration coverage for asynchronous job submission and worker startup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.processes import get_process_identity
from hybrid_sdlc.submission import JobSubmissionError, submit_job


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "spec.md").write_text("# Work\n", encoding="utf-8")
    (path / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["python", "-c", "pass"]\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "spec.md", "hybrid_sdlc.toml"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def _patch_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8090/v1"), None),
    )


def _claiming_worker_command(job_id: str, root: Path) -> list[str]:
    script = textwrap.dedent(
        """
        import os, sys, time
        from datetime import UTC, datetime
        from pathlib import Path
        from hybrid_sdlc.job_manager import JobManager, JobStatus
        manager = JobManager(Path(sys.argv[2]))
        record = manager.claim(sys.argv[1], os.getpid(), datetime.now(UTC))
        time.sleep(0.2)
        manager.transition(record.job_id, JobStatus.FAILED, failure_reason="internal_error")
        """
    )
    return [sys.executable, "-c", script, job_id, str(root)]


def test_invalid_submission_creates_no_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    _patch_endpoint(monkeypatch)

    with pytest.raises(ValueError, match="task_id"):
        submit_job(
            repo_root=tmp_path,
            spec_path="spec.md",
            task_id="../unsafe",
            test_profile="pytest",
        )

    assert not (tmp_path / ".hybrid_sdlc" / "jobs").exists()


def test_status_reports_queued_job_and_returns_after_bounded_wait(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001")

    assert manager.status(job.job_id).status is JobStatus.QUEUED
    waited = manager.status(job.job_id, wait_timeout_seconds=0.03, poll_interval_seconds=0.01)
    assert waited.status is JobStatus.QUEUED


def test_cancel_queued_job_is_immediate_and_idempotent(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001")

    cancelled = manager.cancel(job.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.cancellation_requested is True
    assert cancelled.finished_at is not None
    assert manager.cancel(job.job_id) == cancelled


def test_cancel_request_wins_or_loses_terminal_transition_by_lock_order(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001")
    identity = get_process_identity(os.getpid())
    assert identity.created_at is not None
    manager.claim(job.job_id, os.getpid(), identity.created_at, identity.start_token)

    requested = manager.cancel(job.job_id)
    assert requested.status is JobStatus.RUNNING
    assert requested.cancellation_requested is True
    failed_completion = manager.transition(
        job.job_id, JobStatus.FAILED, failure_reason="internal_error"
    )
    assert failed_completion.status is JobStatus.CANCELLED
    assert failed_completion.failure_reason is None
    assert failed_completion.failure_metadata is None
    assert failed_completion.run_result is None
    assert manager.cancel(job.job_id) == failed_completion

    already_completed = manager.create("spec.md", "T002")
    manager.transition(already_completed.job_id, JobStatus.RUNNING)
    completed = manager.transition(already_completed.job_id, JobStatus.COMPLETED)
    assert manager.cancel(already_completed.job_id) == completed


def test_status_wait_observes_worker_terminal_transition(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001")
    identity = get_process_identity(os.getpid())
    assert identity.created_at is not None
    manager.claim(
        job.job_id,
        os.getpid(),
        identity.created_at,
        identity.start_token,
        identity.precision,
    )

    def complete() -> None:
        time.sleep(0.03)
        manager.transition(job.job_id, JobStatus.COMPLETED)

    completion = threading.Thread(target=complete)
    completion.start()
    result = manager.status(job.job_id, wait_timeout_seconds=1, poll_interval_seconds=0.01)
    completion.join(timeout=1)

    assert result.status is JobStatus.COMPLETED


def test_unconfigured_host_override_is_rejected_before_probe_or_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)

    def reject_probe(**kwargs: object) -> None:
        raise AssertionError("untrusted host override reached endpoint probe")

    monkeypatch.setattr("hybrid_sdlc.submission.select_active_endpoint", reject_probe)
    with pytest.raises(ValueError, match="configured server candidate"):
        submit_job(
            repo_root=tmp_path,
            spec_path="spec.md",
            task_id="T001",
            test_profile="pytest",
            host_url="http://169.254.169.254/latest/meta-data/",
        )

    assert not (tmp_path / ".hybrid_sdlc" / "jobs").exists()


def test_spawn_failure_is_persisted_and_exposes_diagnostic_job_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _patch_endpoint(monkeypatch)

    def fail_spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError("simulated spawn failure")

    monkeypatch.setattr("hybrid_sdlc.submission._launch_worker", fail_spawn)
    with pytest.raises(JobSubmissionError, match="simulated spawn failure") as caught:
        submit_job(
            repo_root=tmp_path,
            spec_path="spec.md",
            task_id="T001",
            test_profile="pytest",
        )

    record = JobManager(tmp_path).get(caught.value.job_id)
    assert record.status is JobStatus.FAILED
    assert record.failure_reason == "launch_failure"
    assert record.worker_pid is None


def test_submission_returns_only_after_worker_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _patch_endpoint(monkeypatch)

    record = submit_job(
        repo_root=tmp_path,
        spec_path="spec.md",
        task_id="T001",
        test_profile="pytest",
        startup_timeout_seconds=3,
        _worker_command_factory=_claiming_worker_command,
    )

    assert record.status is JobStatus.RUNNING
    assert record.worker_pid is not None
    assert record.started_at is not None


def test_startup_timeout_fails_queued_job_and_stops_unclaimed_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _patch_endpoint(monkeypatch)

    with pytest.raises(JobSubmissionError, match="startup timeout") as caught:
        submit_job(
            repo_root=tmp_path,
            spec_path="spec.md",
            task_id="T001",
            test_profile="pytest",
            startup_timeout_seconds=0.1,
            _worker_command_factory=lambda job_id, root: [
                sys.executable,
                "-c",
                "import time; time.sleep(20)",
            ],
        )

    record = JobManager(tmp_path).get(caught.value.job_id)
    assert record.status is JobStatus.FAILED
    assert record.failure_reason == "launch_failure"
    assert record.worker_pid is None


def test_detached_worker_continues_after_submitter_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    _patch_endpoint(monkeypatch)
    source_root = Path(__file__).parents[2] / "src"
    submitter = textwrap.dedent(
        """
        import json, sys
        from pathlib import Path
        from hybrid_sdlc.config import ServerCandidateConfig
        from hybrid_sdlc.submission import submit_job
        import hybrid_sdlc.submission as submission
        submission.select_active_endpoint = lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8090/v1"), None)
        script = '''
        import os, sys, time
        from datetime import UTC, datetime
        from pathlib import Path
        from hybrid_sdlc.job_manager import JobManager, JobStatus
        manager = JobManager(Path(sys.argv[2]))
        record = manager.claim(sys.argv[1], os.getpid(), datetime.now(UTC))
        time.sleep(0.8)
        manager.transition(record.job_id, JobStatus.FAILED, failure_reason="internal_error")
        '''
        record = submit_job(
            repo_root=Path(sys.argv[1]), spec_path="spec.md", task_id="T001",
            test_profile="pytest", startup_timeout_seconds=3,
            _worker_command_factory=lambda job_id, root: [sys.executable, "-c", script, job_id, str(root)],
        )
        print(json.dumps({"job_id": record.job_id, "status": record.status.value}))
        """
    )
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(source_root)
    result = subprocess.run(
        [sys.executable, "-c", submitter, str(tmp_path)],
        cwd=tmp_path,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    submitted = json.loads(result.stdout)
    assert submitted["status"] == "running"

    manager = JobManager(tmp_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = manager.get(submitted["job_id"])
        if record.status is JobStatus.FAILED:
            break
        time.sleep(0.02)
    assert record.status is JobStatus.FAILED
    assert record.worker_pid is not None
    assert record.failure_reason == "internal_error"


def test_packaged_worker_command_claims_without_available_model_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    # Submission preflight is faked so the packaged worker can independently
    # exercise its endpoint-failure path without a live model server.
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:1/v1"), None),
    )

    submitted = submit_job(
        repo_root=tmp_path,
        spec_path="spec.md",
        task_id="T001",
        test_profile="pytest",
        startup_timeout_seconds=5,
    )

    manager = JobManager(tmp_path)
    deadline = time.monotonic() + 10
    record = submitted
    while time.monotonic() < deadline:
        record = manager.get(submitted.job_id)
        if record.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            break
        time.sleep(0.05)
    assert record.status is JobStatus.FAILED
    assert record.worker_pid is not None
    assert record.failure_reason is not None
