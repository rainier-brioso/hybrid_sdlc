"""Public Codex queue bridge contracts; never invoke an installed host or model."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from filelock import FileLock, Timeout
from helpers.repositories import create_passing_repo

import hybrid_sdlc.codex_bridge as codex_bridge
from hybrid_sdlc.cli import cli
from hybrid_sdlc.errors import ExitCode
from hybrid_sdlc.job_manager import JobManager, JobStatus

THREAD = "01a14601-3d14-7e63-b6a2-f8a984cf96ae"
FIXTURE = Path(__file__).resolve().parents[1] / "helpers" / "fake_codex_queue.py"


def _job(
    repo: Path,
    status: JobStatus = JobStatus.COMPLETED,
    failure_reason: str | None = None,
) -> str:
    manager = JobManager(repo)
    job = manager.create("private-spec.md", "PRIVATE_TASK_LABEL")
    manager.transition(job.job_id, JobStatus.RUNNING)
    manager.transition(job.job_id, status, failure_reason=failure_reason)
    return job.job_id


def _invoke(
    repo: Path,
    job_id: str,
    *,
    thread_id: str = THREAD,
    wait_timeout: float = 0,
) -> object:
    return CliRunner().invoke(
        cli,
        [
            "bridge",
            "codex",
            job_id,
            "--repo-root",
            str(repo),
            "--thread-id",
            thread_id,
            "--allow-model-continuation",
            "--codex-executable",
            str(FIXTURE),
            "--wait-timeout",
            str(wait_timeout),
            "--queue-timeout",
            "2",
            "--json",
        ],
    )


def test_bridge_queues_allowlisted_message_and_reports_acceptance_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))

    result = _invoke(repo, job_id)
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    assert response["delivery_status"] == "queue_accepted"
    assert response["queue_accepted"] is True
    assert response["response_completed"] is None
    assert "turn_id" not in response
    assert "FAKE_PRIVATE_STDOUT" not in result.output
    assert "FAKE_PRIVATE_STDERR" not in result.output
    invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert invocation["argv"][:4] == ["queue", "--thread", THREAD, "--message"]
    assert invocation["intent_state"] == "intent"
    message = invocation["argv"][4]
    assert "PRIVATE_TASK_LABEL" not in message
    assert "private-spec.md" not in message
    assert json.loads(message.split("Metadata: ", 1)[1]) == {
        "job_id": job_id,
        "status": "completed",
        "run_status": None,
        "attempt_count": 0,
        "tests_passed": None,
        "failure_reason": None,
    }
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "queue_accepted"
    assert receipt["thread_id"] == THREAD
    assert receipt["response_completed"] is None
    assert "FAKE_PRIVATE_STDOUT" not in json.dumps(receipt)
    assert "FAKE_PRIVATE_STDERR" not in json.dumps(receipt)
    replay = _invoke(repo, job_id)
    assert replay.exit_code != 0
    assert "will not be retried" in json.loads(replay.output)["message"]


@pytest.mark.parametrize(
    ("status", "failure_reason", "expected_reason"),
    [
        (JobStatus.FAILED, "private_custom_failure", "other_failure"),
        (JobStatus.CANCELLED, None, None),
    ],
)
def test_terminal_failure_and_cancellation_messages_use_safe_reason_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: JobStatus,
    failure_reason: str | None,
    expected_reason: str | None,
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo, status, failure_reason)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    result = _invoke(repo, job_id)
    assert result.exit_code == 0, result.output
    message = json.loads(capture.read_text(encoding="utf-8"))["argv"][4]
    metadata = json.loads(message.split("Metadata: ", 1)[1])
    assert metadata["status"] == status.value
    assert metadata["failure_reason"] == expected_reason
    assert "private_custom_failure" not in message


def test_bridge_waits_for_terminal_job_before_queueing_and_does_not_cancel_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    manager = JobManager(repo)
    job = manager.create("private-spec.md", "PRIVATE_TASK_LABEL")
    manager.transition(job.job_id, JobStatus.RUNNING)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    result_holder: dict[str, object] = {}

    def invoke() -> None:
        result_holder["result"] = _invoke(repo, job.job_id, wait_timeout=5)

    worker = threading.Thread(target=invoke)
    worker.start()
    time.sleep(0.15)
    assert not capture.exists()
    assert JobManager(repo).get(job.job_id).status is JobStatus.RUNNING
    manager.transition(job.job_id, JobStatus.COMPLETED)
    worker.join(timeout=5)
    assert not worker.is_alive()
    result = result_holder["result"]
    assert result.exit_code == 0, result.output
    assert JobManager(repo).get(job.job_id).status is JobStatus.COMPLETED
    assert capture.exists()


def test_wait_timeout_does_not_queue_or_cancel_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    manager = JobManager(repo)
    job = manager.create("private-spec.md", "PRIVATE_TASK_LABEL")
    manager.transition(job.job_id, JobStatus.RUNNING)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    result = _invoke(repo, job.job_id, wait_timeout=0)
    assert result.exit_code != 0
    assert not capture.exists()
    assert JobManager(repo).get(job.job_id).status is JobStatus.RUNNING


@pytest.mark.parametrize("scenario", ["nonzero", "timeout"])
def test_ambiguous_queue_result_is_persisted_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", scenario)
    first = _invoke(repo, job_id)
    assert first.exit_code != 0
    error = json.loads(first.output)
    assert error["code"] == "BRIDGE_DELIVERY_UNKNOWN"
    assert "private fake failure details" not in first.output
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "delivery_unknown"
    assert receipt["queue_accepted"] is None
    assert "private fake failure details" not in json.dumps(receipt)
    replay = _invoke(repo, job_id)
    assert replay.exit_code != 0
    assert "will not be retried" in json.loads(replay.output)["message"]


def test_job_receipt_prevents_retargeting_after_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(tmp_path / "capture.json"))
    assert _invoke(repo, job_id).exit_code == 0
    changed_target = _invoke(repo, job_id, thread_id="5fe8e8db-77d0-4be4-8851-55eb241ff309")
    assert changed_target.exit_code != 0
    assert "blocks replay or retargeting" in json.loads(changed_target.output)["message"]


@pytest.mark.parametrize("receipt_content", ['{"schema_version": 1}', "{"])
def test_existing_or_malformed_receipt_blocks_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt_content: str
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(receipt_content, encoding="utf-8")

    result = _invoke(repo, job_id)
    assert result.exit_code != 0
    assert "blocks replay or retargeting" in json.loads(result.output)["message"]
    assert not capture.exists()
    assert receipt_path.read_text(encoding="utf-8") == receipt_content


def test_receipt_symlink_cannot_escape_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    capture = tmp_path / "capture.json"
    outside = tmp_path / "outside.json"
    outside.write_text("private outside receipt", encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        receipt_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")

    result = _invoke(repo, job_id)
    assert result.exit_code != 0
    assert not capture.exists()
    assert outside.read_text(encoding="utf-8") == "private outside receipt"


def test_failed_accepted_receipt_persistence_leaves_replay_blocking_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("FAKE_CODEX_CAPTURE", str(capture))
    actual_save = codex_bridge._save_receipt

    def fail_accepted(path: Path, data: dict[str, object], root: Path, current_job_id: str) -> None:
        if data.get("state") == "queue_accepted":
            raise OSError("private persistence failure")
        actual_save(path, data, root, current_job_id)

    monkeypatch.setattr(codex_bridge, "_save_receipt", fail_accepted)
    result = _invoke(repo, job_id)
    assert result.exit_code != 0
    assert "private persistence failure" not in result.output
    assert capture.exists()
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "delivery_unknown"
    assert "private persistence failure" not in json.dumps(receipt)

    monkeypatch.setattr(codex_bridge, "_save_receipt", actual_save)
    replay = _invoke(repo, job_id)
    assert replay.exit_code != 0
    assert "blocks replay or retargeting" in json.loads(replay.output)["message"]


def test_concurrent_queue_to_same_thread_fails_before_writing_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _job(repo)
    actual_file_lock = FileLock

    class ContendedThreadLock:
        def __init__(self, path: str, **kwargs: object) -> None:
            self.path = path
            self.lock = actual_file_lock(path, **kwargs)

        def acquire(self) -> None:
            if f"thread-{THREAD}.lock" in self.path:
                raise Timeout(self.path)
            self.lock.acquire()

        def release(self) -> None:
            self.lock.release()

    monkeypatch.setattr(codex_bridge, "FileLock", ContendedThreadLock)
    result = _invoke(repo, job_id)
    assert result.exit_code != 0
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
    assert not receipt_path.exists()
    assert json.loads(result.output)["code"] == "CODEX_BRIDGE_FAILED"


def test_cli_requires_thread_and_opt_in_and_help_describes_queue_boundary() -> None:
    missing_thread = CliRunner().invoke(
        cli,
        ["bridge", "codex", "fixture", "--repo-root", ".", "--allow-model-continuation"],
    )
    assert missing_thread.exit_code != 0
    missing_opt_in = CliRunner().invoke(
        cli,
        ["bridge", "codex", "fixture", "--repo-root", ".", "--thread-id", THREAD, "--json"],
    )
    assert missing_opt_in.exit_code == ExitCode.POLICY_ERROR
    help_result = CliRunner().invoke(cli, ["bridge", "codex", "--help"])
    assert help_result.exit_code == 0
    assert "--queue-timeout" in help_result.output
    assert "--exclusive-thread-owner" not in help_result.output
    assert "--turn-timeout" not in help_result.output
