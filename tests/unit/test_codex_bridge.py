"""Codex queue bridge safety contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers.repositories import create_passing_repo

from hybrid_sdlc.codex_bridge import BridgeError, run_codex_bridge
from hybrid_sdlc.job_manager import JobManager, JobStatus

THREAD = "01a14601-3d14-7e63-b6a2-f8a984cf96ae"


def _terminal_job(repo: Path, status: JobStatus = JobStatus.COMPLETED) -> str:
    manager = JobManager(repo)
    job = manager.create("private-spec.md", "PRIVATE_TASK_LABEL")
    manager.transition(job.job_id, JobStatus.RUNNING)
    manager.transition(
        job.job_id,
        status,
        failure_reason="test_failure" if status is JobStatus.FAILED else None,
    )
    return job.job_id


def test_queue_requires_explicit_opt_in_and_uuid_before_invoking_codex(tmp_path: Path) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    with pytest.raises(BridgeError, match="explicit model-continuation opt-in"):
        run_codex_bridge(repo, job_id, thread_id=THREAD)
    with pytest.raises(BridgeError, match="valid Codex thread UUID"):
        run_codex_bridge(
            repo,
            job_id,
            thread_id="some-thread-name",
            allow_model_continuation=True,
        )
    assert not (repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json").exists()


def test_payload_contains_only_allowlisted_terminal_metadata(tmp_path: Path) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo, JobStatus.FAILED)
    from hybrid_sdlc.codex_bridge import _payload

    record = JobManager(repo).get(job_id)
    assert _payload(record) == {
        "job_id": job_id,
        "status": "failed",
        "run_status": None,
        "attempt_count": 0,
        "tests_passed": None,
        "failure_reason": "test_failure",
    }
    assert "PRIVATE_TASK_LABEL" not in str(_payload(record))
    assert "private-spec.md" not in str(_payload(record))


def test_cancelled_queue_result_is_ambiguous_and_intent_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    observed: dict[str, object] = {}

    def cancelled(argv: list[str], **kwargs: object) -> SimpleNamespace:
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=True)

    monkeypatch.setattr("hybrid_sdlc.codex_bridge.run_bounded_subprocess", cancelled)
    with pytest.raises(BridgeError, match="may have been accepted") as caught:
        run_codex_bridge(
            repo,
            job_id,
            thread_id=THREAD,
            allow_model_continuation=True,
        )
    assert caught.value.code == "BRIDGE_DELIVERY_UNKNOWN"
    assert observed["stdin_data"] == b""
    receipt = json.loads(
        (repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json").read_text()
    )
    assert receipt["state"] == "delivery_unknown"
