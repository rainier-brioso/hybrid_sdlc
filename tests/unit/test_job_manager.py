"""Persisted job-state-machine tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from hybrid_sdlc.errors import PathTraversalError
from hybrid_sdlc.job_manager import (
    InvalidJobTransitionError,
    JobManager,
    JobRecord,
    JobStatus,
    new_job_id,
    validate_job_id,
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
