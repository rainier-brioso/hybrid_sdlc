"""Tests for single-job worker claims, heartbeats, and CLI execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from hybrid_sdlc.cli import cli
from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.job_manager import InvalidJobTransitionError, JobManager, JobRecord, JobStatus
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.processes import (
    get_process_creation_time,
    process_is_alive,
    run_bounded_subprocess,
)
from hybrid_sdlc.worker import run_worker


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "spec.md").write_text("# Work\n", encoding="utf-8")
    (path / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["pytest"]\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "spec.md", "hybrid_sdlc.toml"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def _success_result(root: Path) -> RunResult:
    return RunResult(
        run_id="run_worker_test",
        task_id="T001",
        spec_path="spec.md",
        repo_root=str(root),
        status=RunStatus.SUCCESS,
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        total_duration_seconds=0.1,
    )


def test_claim_has_exactly_one_winner_and_heartbeat_checks_worker_identity(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    outcomes: list[object] = []
    created_at = datetime.now(UTC)

    def claim(pid: int) -> None:
        try:
            outcomes.append(manager.claim(job.job_id, pid, created_at))
        except InvalidJobTransitionError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=claim, args=(pid,)) for pid in (111, 222)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, InvalidJobTransitionError) for item in outcomes) == 1
    owner = manager.get(job.job_id)
    with pytest.raises(InvalidJobTransitionError, match="not owned"):
        manager.heartbeat(job.job_id, owner.worker_pid or 0, datetime.now(UTC))
    refreshed = manager.heartbeat(job.job_id, owner.worker_pid or 0, created_at)
    assert refreshed.heartbeat_at is not None


def test_claim_is_exclusive_across_processes(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    gate = tmp_path / "start-workers"
    child_script = """
import os, sys, time
from pathlib import Path
from hybrid_sdlc.job_manager import JobManager, InvalidJobTransitionError
from hybrid_sdlc.processes import get_process_creation_time
root, job_id, gate = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
deadline = time.monotonic() + 10
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
try:
    pid = os.getpid()
    record = JobManager(root).claim(job_id, pid, get_process_creation_time(pid))
except InvalidJobTransitionError:
    print('rejected')
else:
    print(f'claimed:{record.worker_pid}')
"""
    source_path = Path(__file__).parents[2] / "src"
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(source_path), child_env.get("PYTHONPATH", "")])
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", child_script, str(tmp_path), job.job_id, str(gate)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=child_env,
        )
        for _ in range(2)
    ]
    gate.touch()
    results = [worker.communicate(timeout=15) for worker in workers]
    assert all(worker.returncode == 0 for worker in workers), results
    outputs = [stdout.strip() for stdout, _ in results]
    assert sum(output.startswith("claimed:") for output in outputs) == 1
    assert outputs.count("rejected") == 1


def test_worker_executes_claim_and_persists_result_while_ignoring_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "context.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "hybrid_sdlc.toml").write_text(
        'aider_repo_map_tokens = 2048\naider_edit_files = ["context.py"]\n'
        "aider_max_tokens = 8192\naider_reasoning_budget_tokens = 1024\n"
        '[command_profiles.pytest]\nargv = ["pytest"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "hybrid_sdlc.toml", "context.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "configure Aider context"], cwd=tmp_path, check=True)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    # The queued record and transition lock live under the ignored artifact directory.
    assert (tmp_path / ".hybrid_sdlc" / "jobs" / f"{job.job_id}.json").is_file()
    monkeypatch.setattr(
        "hybrid_sdlc.worker.resolve_profile_executable", lambda profile, root: Path("pytest")
    )
    monkeypatch.setattr(
        "hybrid_sdlc.worker.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8090/v1"), None),
    )

    observed_children: list[tuple[int, datetime | None, datetime | None, int | None]] = []

    def fake_run_loop(**kwargs: object) -> RunResult:
        observer = kwargs["process_observer"]
        assert callable(observer)

        def capture_observer(pid: int, created_at: datetime | None) -> None:
            observer(pid, created_at)
            if created_at is not None:
                record = manager.get(job.job_id)
                observed_children.append(
                    (
                        pid,
                        record.child_created_at,
                        get_process_creation_time(pid),
                        record.child_pid,
                    )
                )

        child_result = run_bounded_subprocess(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_seconds=5,
            process_observer=capture_observer,
        )
        assert child_result.exit_code == 0
        return _success_result(tmp_path)

    captured_args: dict[str, object] = {}

    def capture_run_loop(**kwargs: object) -> RunResult:
        captured_args.update(kwargs)
        return fake_run_loop(**kwargs)

    monkeypatch.setattr("hybrid_sdlc.worker.run_bounded_loop", capture_run_loop)

    completed = run_worker(job.job_id, tmp_path, heartbeat_interval_seconds=0.01)

    assert completed.status is JobStatus.COMPLETED
    assert captured_args["repo_map_tokens"] == 2048
    assert captured_args["target_files"] == [Path("context.py")]
    assert captured_args["aider_max_tokens"] == 8192
    assert captured_args["aider_reasoning_budget_tokens"] == 1024
    assert completed.worker_pid is not None
    assert completed.worker_created_at is not None
    assert abs(
        (
            completed.worker_created_at - get_process_creation_time(completed.worker_pid)
        ).total_seconds()
    ) <= (1.1 if sys.platform == "darwin" else 0.1)
    assert completed.heartbeat_at is not None
    assert observed_children[0][1] is not None
    assert observed_children[0][2] is not None
    assert abs((observed_children[0][1] - observed_children[0][2]).total_seconds()) <= (
        1.1 if sys.platform == "darwin" else 0.1
    )
    assert observed_children[0][3] == observed_children[0][0]
    assert completed.child_pid is None
    assert completed.child_created_at is None
    assert completed.run_result is not None
    assert completed.run_result.status is RunStatus.SUCCESS
    assert manager.get(job.job_id) == completed


def test_worker_persists_launch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="missing")
    failed = run_worker(job.job_id, tmp_path)
    assert failed.status is JobStatus.FAILED
    assert failed.failure_reason == "launch_failure"
    assert failed.finished_at is not None


def test_worker_accepts_dirty_source_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    (tmp_path / "untracked.txt").write_text("dirty", encoding="utf-8")
    monkeypatch.setattr(
        "hybrid_sdlc.worker.resolve_profile_executable", lambda profile, root: Path("pytest")
    )
    monkeypatch.setattr(
        "hybrid_sdlc.worker.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8090/v1"), None),
    )
    monkeypatch.setattr(
        "hybrid_sdlc.worker.run_bounded_loop", lambda **kwargs: _success_result(tmp_path)
    )

    completed = run_worker(job.job_id, tmp_path)

    assert completed.status is JobStatus.COMPLETED
    assert (tmp_path / "untracked.txt").read_text(encoding="utf-8") == "dirty"


def test_heartbeat_failure_interrupts_worker_and_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")

    def fail_heartbeat(
        self: JobManager, job_id: str, worker_pid: int, created_at: datetime
    ) -> None:
        raise OSError("cannot persist heartbeat")

    def wait_for_cancel(
        current_manager: JobManager,
        claimed: object,
        cancel_event: threading.Event,
        process_observer: object,
    ) -> RunResult:
        assert cancel_event.wait(timeout=2)
        return _success_result(tmp_path)

    monkeypatch.setattr(JobManager, "heartbeat", fail_heartbeat)
    monkeypatch.setattr("hybrid_sdlc.worker._execute", wait_for_cancel)
    failed = run_worker(job.job_id, tmp_path, heartbeat_interval_seconds=0.01)
    assert failed.status is JobStatus.FAILED
    assert failed.failure_reason == "internal_error"


def test_async_cancellation_stops_worker_child_and_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    grandchild_file = tmp_path / "grandchild.pid"
    observed_child: list[int] = []
    result_record: list[JobRecord] = []
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "open(sys.argv[1], 'w').write(str(child.pid)); time.sleep(30)"
    )

    def execute(
        current_manager: JobManager,
        claimed: object,
        cancel_event: threading.Event,
        process_observer: object,
    ) -> RunResult:
        def observe(pid: int, created_at: datetime | None) -> None:
            assert callable(process_observer)
            process_observer(pid, created_at)
            observed_child.append(pid)

        completed = run_bounded_subprocess(
            [sys.executable, "-c", script, str(grandchild_file)],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout_seconds=20,
            cancel_event=cancel_event,
            process_observer=observe,
        )
        assert completed.cancelled
        return RunResult(
            run_id="cancelled_worker_test",
            task_id="T001",
            spec_path="spec.md",
            repo_root=str(tmp_path),
            status=RunStatus.CANCELLED,
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            total_duration_seconds=0.1,
        )

    monkeypatch.setattr("hybrid_sdlc.worker._execute", execute)
    worker = threading.Thread(
        target=lambda: result_record.append(
            run_worker(job.job_id, tmp_path, heartbeat_interval_seconds=0.01)
        )
    )
    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if grandchild_file.exists() and manager.get(job.job_id).child_pid is not None:
            break
        time.sleep(0.01)
    assert grandchild_file.exists()
    grandchild_pid = int(grandchild_file.read_text(encoding="utf-8"))
    assert manager.cancel(job.job_id).status is JobStatus.RUNNING
    assert manager.cancel(job.job_id).cancellation_requested is True
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert result_record[0].status is JobStatus.CANCELLED
    assert observed_child
    assert not process_is_alive(observed_child[0])
    assert not process_is_alive(grandchild_pid)


def test_worker_rejects_nonpositive_heartbeat_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        run_worker("job_20260926T000000000000Z_01234567", tmp_path, heartbeat_interval_seconds=0)


def test_worker_cli_passes_job_id_and_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("spec.md", "T001", test_profile="pytest")
    seen: list[tuple[str, Path | None]] = []

    def fake_worker(job_id: str, repo_root: Path | None) -> object:
        seen.append((job_id, repo_root))
        manager.claim(job_id, 123, datetime.now(UTC))
        return manager.transition(job_id, JobStatus.COMPLETED, run_result=_success_result(tmp_path))

    monkeypatch.setattr("hybrid_sdlc.cli.run_worker", fake_worker)
    result = CliRunner().invoke(cli, ["worker", job.job_id, "--repo-root", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert seen == [(job.job_id, tmp_path)]
    assert json.loads(result.output)["status"] == "completed"
