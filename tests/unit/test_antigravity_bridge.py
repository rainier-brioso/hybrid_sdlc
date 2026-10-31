"""Safety tests for the opt-in Antigravity App bridge."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from filelock import Timeout
from helpers.repositories import create_passing_repo

import hybrid_sdlc.antigravity_bridge as bridge
from hybrid_sdlc.antigravity_bridge import (
    AntigravityBridgeError,
    run_antigravity_bridge,
    validate_host,
)
from hybrid_sdlc.job_manager import JobManager, JobStatus, new_job_id

CONVERSATION = "6bddf934-8c2a-4310-a8cc-b3deed89174c"


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


def _host_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "Antigravity.exe"
    app.touch()
    language_server = tmp_path / "resources" / "bin" / "language_server.exe"
    language_server.parent.mkdir(parents=True)
    language_server.touch()
    wrapper = tmp_path / "agentapi.bat"
    wrapper.write_text(
        f'@echo off\r\n"{str(language_server).replace("/", chr(92))}" agentapi %*\r\n',
        encoding="utf-8",
    )
    return app, language_server, wrapper


def test_validate_host_accepts_only_observed_windows_app_and_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, language_server, wrapper = _host_files(tmp_path)
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.sys.platform", "win32")
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge._read_windows_file_version",
        lambda path: (2, 24, 0, 0) if path == app else pytest.fail("wrong version target"),
    )
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.shutil.which", lambda _name: str(wrapper))

    assert validate_host(app) == [str(language_server), "agentapi"]


def test_validate_host_rejects_other_os_and_unknown_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _language_server, wrapper = _host_files(tmp_path)
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.sys.platform", "linux")
    with pytest.raises(AntigravityBridgeError, match="only on Windows"):
        validate_host(app)

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.sys.platform", "win32")
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge._read_windows_file_version",
        lambda _path: (2, 25, 0, 0),
    )
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.shutil.which", lambda _name: str(wrapper))
    with pytest.raises(AntigravityBridgeError, match="Only Antigravity App 2.24.0"):
        validate_host(app)

    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge._read_windows_file_version",
        lambda _path: (2, 24, 0, 1),
    )
    with pytest.raises(AntigravityBridgeError, match="Only Antigravity App 2.24.0"):
        validate_host(app)


def test_validate_host_rejects_modified_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _language_server, wrapper = _host_files(tmp_path)
    wrapper.write_text("@echo off\r\nmalicious.exe agentapi %*\r\n", encoding="utf-8")
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.sys.platform", "win32")
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge._read_windows_file_version",
        lambda _path: (2, 24, 0, 0),
    )
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.shutil.which", lambda _name: str(wrapper))
    with pytest.raises(AntigravityBridgeError, match="wrapper does not match"):
        validate_host(app)

    wrapper.write_bytes(b"@echo off\r\n" + b"x" * 8192)
    with pytest.raises(AntigravityBridgeError, match="exceeds the size limit"):
        validate_host(app)


def test_version_resource_size_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "Antigravity.exe"
    app.touch()

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.sys.platform", "win32")
    monkeypatch.setattr(
        bridge.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: SimpleNamespace(
            GetFileVersionInfoSizeW=lambda _path, _ignored: 1024 * 1024 + 1
        ),
        raising=False,
    )
    with pytest.raises(AntigravityBridgeError, match="Could not read.*version resource"):
        bridge._read_windows_file_version(app)


def test_invalid_authority_and_conversation_are_rejected_before_host_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host",
        lambda _path: pytest.fail("host accessed before input validation"),
    )
    with pytest.raises(AntigravityBridgeError, match="explicit model-continuation opt-in"):
        run_antigravity_bridge(
            repo, job_id, conversation_id=CONVERSATION, app_executable=Path("App.exe")
        )
    with pytest.raises(AntigravityBridgeError, match="conversation UUID"):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id="test chat",
            app_executable=Path("App.exe"),
            allow_model_continuation=True,
        )
    with pytest.raises(AntigravityBridgeError, match="Invalid job ID"):
        run_antigravity_bridge(
            repo,
            "..\\outside",
            conversation_id=CONVERSATION,
            app_executable=Path("App.exe"),
            allow_model_continuation=True,
        )


def test_missing_job_is_rejected_before_host_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host",
        lambda _path: pytest.fail("host accessed for a missing job"),
    )
    with pytest.raises(AntigravityBridgeError, match="does not exist or is invalid"):
        run_antigravity_bridge(
            repo,
            new_job_id(),
            conversation_id=CONVERSATION,
            app_executable=Path("App.exe"),
            allow_model_continuation=True,
        )


def test_success_persists_intent_before_shell_free_minimized_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo, JobStatus.FAILED)
    prefix = [str(tmp_path / "language_server.exe"), "agentapi"]
    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.validate_host", lambda _path: prefix)
    observed: dict[str, Any] = {}

    def send(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        observed["argv"] = argv
        observed.update(kwargs)
        receipt_path = repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job_id}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["state"] == "intent"
        assert receipt["delivery_attempted"] is None
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False)

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.run_bounded_subprocess", send)
    result = run_antigravity_bridge(
        repo,
        job_id,
        conversation_id=CONVERSATION,
        app_executable=Path("unused.exe"),
        allow_model_continuation=True,
    )
    assert observed["argv"][:4] == [*prefix, "send-message", CONVERSATION]
    assert observed["argv"][4].startswith("Hybrid SDLC job completion.")
    assert "PRIVATE_TASK_LABEL" not in observed["argv"][4]
    assert "private-spec.md" not in observed["argv"][4]
    assert observed["stdin_data"] == b""
    assert observed["timeout_seconds"] == 30
    assert result["delivery_status"] == "send_accepted"
    assert result["response_completed"] is None
    receipt = json.loads(
        (repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job_id}.json").read_text()
    )
    assert receipt["state"] == "command_succeeded"
    assert receipt["delivery_attempted"] is True
    assert receipt["command_succeeded"] is True
    assert receipt["automatic_response_observed"] is None


def test_changed_host_version_after_wait_prevents_intent_and_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    validations = 0

    def validate(_app: Path) -> list[str]:
        nonlocal validations
        validations += 1
        if validations == 1:
            return ["trusted-host.exe", "agentapi"]
        raise AntigravityBridgeError("Only Antigravity App 2.24.0 is currently supported")

    monkeypatch.setattr(bridge, "validate_host", validate)
    monkeypatch.setattr(
        bridge,
        "run_bounded_subprocess",
        lambda *_args, **_kwargs: pytest.fail("changed host must prevent the send"),
    )
    with pytest.raises(AntigravityBridgeError, match="Only Antigravity App 2.24.0"):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )
    assert validations == 2
    assert not (repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job_id}.json").exists()


def test_existing_even_malformed_receipt_blocks_host_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    receipt_dir = repo / ".hybrid_sdlc" / "bridges" / "antigravity"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / f"{job_id}.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host",
        lambda _path: pytest.fail("host accessed on replay"),
    )
    with pytest.raises(AntigravityBridgeError, match="blocks replay or retargeting"):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )


def test_wait_timeout_does_not_send_or_write_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    manager = JobManager(repo)
    job = manager.create("spec.md", "T1")
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host", lambda _path: ["ls", "agentapi"]
    )
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.run_bounded_subprocess",
        lambda *_args, **_kwargs: pytest.fail("job timed out before sending"),
    )
    with pytest.raises(AntigravityBridgeError, match="did not reach a terminal state"):
        run_antigravity_bridge(
            repo,
            job.job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
            wait_timeout_seconds=0,
        )
    assert not (repo / ".hybrid_sdlc" / "bridges" / "antigravity").exists()


def test_same_conversation_lock_contention_prevents_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    bridge_dir = repo / ".hybrid_sdlc" / "bridges" / "antigravity"
    bridge_dir.mkdir(parents=True)
    lock_path = bridge_dir / f"conversation-{CONVERSATION}.lock"
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host",
        lambda _path: ["trusted-host.exe", "agentapi"],
    )
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.run_bounded_subprocess",
        lambda *_args, **_kwargs: pytest.fail("conversation lock contention must block send"),
    )
    original_file_lock = bridge.FileLock

    class ContendedConversationLock:
        def acquire(self) -> None:
            raise Timeout("simulated another process owning this conversation")

        def release(self) -> None:
            raise AssertionError("a lock that was not acquired must not be released")

    def contended_file_lock(path: str, **kwargs: Any) -> Any:
        if Path(path).name == lock_path.name:
            return ContendedConversationLock()
        return original_file_lock(path, **kwargs)

    monkeypatch.setattr(bridge, "FileLock", contended_file_lock)
    with pytest.raises(AntigravityBridgeError, match="owns this conversation"):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )
    assert not (bridge_dir / f"{job_id}.json").exists()


def test_failed_success_receipt_write_keeps_intent_and_blocks_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host",
        lambda _path: ["trusted-host.exe", "agentapi"],
    )
    sent = 0

    def send(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal sent
        sent += 1
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False)

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.run_bounded_subprocess", send)
    original_save = bridge._save_receipt
    saves = 0

    def fail_final_saves(path: Path, data: dict[str, Any], root: Path, receipt_job_id: str) -> None:
        nonlocal saves
        saves += 1
        if saves > 1:
            raise OSError("simulated receipt write failure")
        original_save(path, data, root, receipt_job_id)

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge._save_receipt", fail_final_saves)
    args = {
        "conversation_id": CONVERSATION,
        "app_executable": Path("unused.exe"),
        "allow_model_continuation": True,
    }
    with pytest.raises(AntigravityBridgeError) as caught:
        run_antigravity_bridge(repo, job_id, **args)
    assert caught.value.code == "BRIDGE_DELIVERY_UNKNOWN"
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job_id}.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["state"] == "intent"
    with pytest.raises(AntigravityBridgeError, match="blocks replay or retargeting"):
        run_antigravity_bridge(repo, job_id, **args)
    assert sent == 1


@pytest.mark.parametrize(
    ("result", "state"),
    [
        (SimpleNamespace(exit_code=1, timed_out=False, cancelled=False), "delivery_unknown"),
        (SimpleNamespace(exit_code=0, timed_out=True, cancelled=False), "delivery_unknown"),
        (SimpleNamespace(exit_code=0, timed_out=False, cancelled=True), "delivery_unknown"),
    ],
)
def test_uncertain_send_is_durable_and_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: SimpleNamespace,
    state: str,
) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    monkeypatch.setattr(
        "hybrid_sdlc.antigravity_bridge.validate_host", lambda _path: ["ls", "agentapi"]
    )
    calls = 0

    def send(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return result

    monkeypatch.setattr("hybrid_sdlc.antigravity_bridge.run_bounded_subprocess", send)
    with pytest.raises(AntigravityBridgeError) as caught:
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )
    assert caught.value.code == "BRIDGE_DELIVERY_UNKNOWN"
    receipt_path = repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job_id}.json"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["state"] == state
    with pytest.raises(AntigravityBridgeError, match="blocks replay or retargeting"):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )
    assert calls == 1


def test_symlinked_bridge_directory_is_rejected(tmp_path: Path) -> None:
    repo = create_passing_repo(tmp_path)
    job_id = _terminal_job(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    bridges = repo / ".hybrid_sdlc" / "bridges"
    try:
        bridges.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")
    with pytest.raises((AntigravityBridgeError, OSError)):
        run_antigravity_bridge(
            repo,
            job_id,
            conversation_id=CONVERSATION,
            app_executable=Path("unused.exe"),
            allow_model_continuation=True,
        )
