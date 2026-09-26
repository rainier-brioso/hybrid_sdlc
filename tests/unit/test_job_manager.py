"""Persisted job-state-machine tests."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import hybrid_sdlc.processes as process_api
from hybrid_sdlc.errors import PathTraversalError
from hybrid_sdlc.job_manager import (
    InvalidJobTransitionError,
    JobManager,
    JobRecord,
    JobStatus,
    new_job_id,
    validate_job_id,
)
from hybrid_sdlc.processes import ProcessIdentity


def _running_job(manager: JobManager, pid: int = 4242) -> JobRecord:
    queued = manager.create("specs/001/tasks.md", "T001")
    return manager.claim(
        queued.job_id,
        pid,
        datetime.now(UTC),
        "linux:boot:12345",
        "100hz",
    )


def test_job_ids_are_unique_and_valid() -> None:
    ids = {new_job_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(validate_job_id(job_id) == job_id for job_id in ids)


@pytest.mark.parametrize(
    "job_id",
    ["", "../job_fake", "job_../../secret", "job_20260926T000000000000Z_zzzzzzzz", "job_x"],
)
def test_invalid_job_id_is_rejected_before_path_access(tmp_path: Path, job_id: str) -> None:
    manager = JobManager(tmp_path)
    with pytest.raises(ValueError, match="Invalid job ID"):
        manager.get(job_id)
    with pytest.raises(ValueError, match="Invalid job ID"):
        manager.transition(job_id, JobStatus.FAILED, failure_reason="internal_error")
    assert not (tmp_path / ".hybrid_sdlc").exists()


def test_symlinked_artifacts_directory_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    try:
        (tmp_path / ".hybrid_sdlc").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable")
    with pytest.raises(PathTraversalError):
        JobManager(tmp_path).create("specs/001/tasks.md", "T001")
    assert list(outside.iterdir()) == []


def test_create_and_reload_persist_queued_state(tmp_path: Path) -> None:
    created = JobManager(tmp_path).create("specs/001/tasks.md", "T001")
    reloaded = JobManager(tmp_path).get(created.job_id)
    assert reloaded == created
    assert reloaded.status is JobStatus.QUEUED
    assert reloaded.failure_reason is None
    assert reloaded.finished_at is None
    assert (tmp_path / ".hybrid_sdlc" / "jobs" / f"{created.job_id}.json").is_file()


def test_create_retries_colliding_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = JobManager(tmp_path)
    first = manager.create("specs/001/tasks.md", "T001")
    second_id = new_job_id()
    ids = iter((first.job_id, second_id))
    monkeypatch.setattr("hybrid_sdlc.job_manager.new_job_id", lambda: next(ids))
    second = manager.create("specs/002/tasks.md", "T002")
    assert second.job_id == second_id
    assert manager.get(first.job_id) == first


def test_legal_success_transition_and_terminal_immutability(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    queued = manager.create("specs/001/tasks.md", "T001")
    running = manager.transition(queued.job_id, JobStatus.RUNNING)
    assert running.started_at is not None
    completed = manager.transition(queued.job_id, JobStatus.COMPLETED)
    assert completed.finished_at is not None
    assert completed.duration_seconds is not None
    assert completed.duration_seconds >= 0
    assert JobManager(tmp_path).get(queued.job_id) == completed
    with pytest.raises(InvalidJobTransitionError):
        manager.transition(queued.job_id, JobStatus.CANCELLED)
    assert manager.get(queued.job_id) == completed


def test_queued_job_can_be_cancelled(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    cancelled = manager.transition(job.job_id, JobStatus.CANCELLED)
    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert cancelled.failure_reason is None


@pytest.mark.parametrize("source", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_failure_requires_documented_reason(tmp_path: Path, source: JobStatus) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    if source is JobStatus.RUNNING:
        manager.transition(job.job_id, JobStatus.RUNNING)
    with pytest.raises(ValidationError, match="failure_reason"):
        manager.transition(job.job_id, JobStatus.FAILED)
    assert manager.get(job.job_id).status is source
    failed = manager.transition(job.job_id, JobStatus.FAILED, failure_reason="test_failure")
    assert failed.failure_reason == "test_failure"
    assert failed.finished_at is not None


def test_illegal_transition_and_unexpected_reason_do_not_modify_record(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    with pytest.raises(InvalidJobTransitionError):
        manager.transition(job.job_id, JobStatus.COMPLETED)
    with pytest.raises(ValidationError, match="failure_reason"):
        manager.transition(job.job_id, JobStatus.CANCELLED, failure_reason="timeout")
    assert manager.get(job.job_id) == job


def test_failed_record_cannot_be_loaded_without_reason(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    path = tmp_path / ".hybrid_sdlc" / "jobs" / f"{job.job_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    payload["finished_at"] = payload["updated_at"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError, match="failure_reason"):
        manager.get(job.job_id)


def test_concurrent_terminal_transitions_have_one_winner(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    manager.transition(job.job_id, JobStatus.RUNNING)
    outcomes: list[JobRecord] = []
    failures: list[InvalidJobTransitionError] = []

    def complete(status: JobStatus) -> None:
        try:
            outcomes.append(manager.transition(job.job_id, status))
        except InvalidJobTransitionError as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=complete, args=(status,))
        for status in (JobStatus.COMPLETED, JobStatus.CANCELLED)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(outcomes) == 1
    assert len(failures) == 1
    assert manager.get(job.job_id) == outcomes[0]


def test_status_immediate_and_bounded_wait(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    start = time.monotonic()
    assert manager.status(job.job_id).status is JobStatus.QUEUED
    waiting = manager.status(job.job_id, wait_timeout_seconds=0.04, poll_interval_seconds=0.01)
    assert waiting.status is JobStatus.QUEUED
    assert 0.03 <= time.monotonic() - start < 1
    manager.transition(job.job_id, JobStatus.CANCELLED)
    assert manager.status(job.job_id, wait_timeout_seconds=1).status is JobStatus.CANCELLED


@pytest.mark.parametrize(
    ("timeout", "interval"),
    [(float("nan"), 0.1), (float("inf"), 0.1), (-1, 0.1), (61, 0.1), (1, 0), (1, 6)],
)
def test_status_rejects_unbounded_wait_values(
    tmp_path: Path, timeout: float, interval: float
) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    with pytest.raises(ValueError, match="seconds"):
        manager.status(job.job_id, wait_timeout_seconds=timeout, poll_interval_seconds=interval)


@pytest.mark.parametrize(
    ("identity", "expected_cause"),
    [
        (ProcessIdentity("dead"), "worker_process_missing"),
        (
            ProcessIdentity("alive", start_token="linux:boot:99999", precision="100hz"),
            "worker_pid_reused",
        ),
    ],
)
def test_status_recovers_confirmed_dead_or_reused_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: ProcessIdentity, expected_cause: str
) -> None:
    manager = JobManager(tmp_path)
    job = _running_job(manager)
    monkeypatch.setattr("hybrid_sdlc.job_manager.get_process_identity", lambda pid: identity)

    failed = manager.status(job.job_id)

    assert failed.status is JobStatus.FAILED
    assert failed.failure_reason == "abandoned_process"
    assert failed.failure_metadata is not None
    assert failed.failure_metadata["cause"] == expected_cause
    assert failed.failure_metadata["worker_pid"] == job.worker_pid
    assert "detected_at" in failed.failure_metadata
    assert manager.get(job.job_id) == failed


def test_live_worker_is_preserved_and_unknown_probe_is_not_abandoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    job = _running_job(manager)
    monkeypatch.setattr(
        "hybrid_sdlc.job_manager.get_process_identity",
        lambda pid: ProcessIdentity("alive", start_token="linux:boot:12345", precision="100hz"),
    )
    assert manager.status(job.job_id) == job

    monkeypatch.setattr(
        "hybrid_sdlc.job_manager.get_process_identity", lambda pid: ProcessIdentity("unknown")
    )
    assert manager.status(job.job_id) == job


@pytest.mark.parametrize("proc_error", [FileNotFoundError, PermissionError])
def test_hidden_or_inaccessible_proc_entry_preserves_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proc_error: type[OSError]
) -> None:
    manager = JobManager(tmp_path)
    job = _running_job(manager)

    class HiddenProcPath:
        def __init__(self, value: str) -> None:
            self.value = value

        def read_text(self, encoding: str) -> str:
            raise proc_error("proc entry unavailable")

    monkeypatch.setattr(process_api, "Path", HiddenProcPath)
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(
            name="posix", environ=os.environ, sysconf=lambda name: 100, kill=lambda pid, sig: None
        ),
    )
    monkeypatch.setattr(process_api.sys, "platform", "linux")
    monkeypatch.setattr(
        "hybrid_sdlc.job_manager.get_process_identity", process_api.get_process_identity
    )

    assert manager.status(job.job_id).status is JobStatus.RUNNING


def test_missing_proc_entry_and_esrch_fails_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    job = _running_job(manager)

    class MissingProcPath:
        def __init__(self, value: str) -> None:
            self.value = value

        def read_text(self, encoding: str) -> str:
            raise FileNotFoundError(self.value)

    def kill_missing(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(process_api, "Path", MissingProcPath)
    monkeypatch.setattr(
        process_api,
        "os",
        SimpleNamespace(
            name="posix", environ=os.environ, sysconf=lambda name: 100, kill=kill_missing
        ),
    )
    monkeypatch.setattr(process_api.sys, "platform", "linux")
    monkeypatch.setattr(
        "hybrid_sdlc.job_manager.get_process_identity", process_api.get_process_identity
    )

    assert manager.status(job.job_id).failure_reason == "abandoned_process"


def test_legacy_job_without_start_token_uses_conservative_timestamp_fallback(
    tmp_path: Path,
) -> None:
    manager = JobManager(tmp_path)
    job = manager.create("specs/001/tasks.md", "T001")
    started = datetime.now(UTC)
    running = manager.claim(job.job_id, 4242, started)
    matching = ProcessIdentity("alive", created_at=started, precision="100hz")
    assert manager._abandonment_diagnostic(running, matching) is None
    reused = ProcessIdentity(
        "alive", created_at=started.replace(year=started.year - 1), precision="100hz"
    )
    diagnostic = manager._abandonment_diagnostic(running, reused)
    assert diagnostic is not None
    assert diagnostic["cause"] == "worker_pid_reused_legacy_identity"


def test_recovery_does_not_clobber_completion_racing_with_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    job = _running_job(manager)

    def finish_during_probe(pid: int) -> ProcessIdentity:
        manager.transition(job.job_id, JobStatus.COMPLETED)
        return ProcessIdentity("dead")

    monkeypatch.setattr("hybrid_sdlc.job_manager.get_process_identity", finish_during_probe)
    assert manager.status(job.job_id).status is JobStatus.COMPLETED


def test_startup_recovery_scans_only_running_job_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    abandoned = _running_job(manager, 1001)
    live = _running_job(manager, 1002)
    monkeypatch.setattr(
        "hybrid_sdlc.job_manager.get_process_identity",
        lambda pid: ProcessIdentity(
            "dead" if pid == 1001 else "alive",
            start_token="linux:boot:12345",
            precision="100hz",
        ),
    )

    recovered = manager.recover_running_jobs()

    assert {item.job_id for item in recovered} == {abandoned.job_id, live.job_id}
    assert manager.get(abandoned.job_id).failure_reason == "abandoned_process"
    assert manager.get(live.job_id).status is JobStatus.RUNNING
