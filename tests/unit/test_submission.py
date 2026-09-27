"""Unit tests for asynchronous host capability checks."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import hybrid_sdlc.submission as submission
from hybrid_sdlc.errors import AsyncJobHostUnsupportedError


def _mock_windows_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int,
) -> SimpleNamespace:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode)

    fake_subprocess = SimpleNamespace(
        DEVNULL=-3,
        DETACHED_PROCESS=8,
        CREATE_NEW_PROCESS_GROUP=512,
        CREATE_BREAKAWAY_FROM_JOB=16777216,
        SubprocessError=subprocess.SubprocessError,
        run=run,
    )
    monkeypatch.setattr(submission, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(submission, "subprocess", fake_subprocess)
    return SimpleNamespace(calls=calls)


def test_windows_breakaway_probe_accepts_child_outside_all_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _mock_windows_probe(monkeypatch, returncode=0)

    submission._verify_windows_worker_breakaway()

    args, kwargs = probe.calls[0]
    assert args[0][1:3] == ["-I", "-c"]
    assert "IsProcessInJob" in args[0][3]
    assert kwargs["creationflags"] == (
        submission.subprocess.DETACHED_PROCESS
        | submission.subprocess.CREATE_NEW_PROCESS_GROUP
        | submission.subprocess.CREATE_BREAKAWAY_FROM_JOB
    )


@pytest.mark.parametrize("returncode", [42, 43, 3])
def test_windows_breakaway_probe_fails_closed_for_membership_or_unknown_result(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    _mock_windows_probe(monkeypatch, returncode=returncode)

    with pytest.raises(AsyncJobHostUnsupportedError, match="Windows background job"):
        submission._verify_windows_worker_breakaway()


def test_windows_probe_launch_failure_is_reported_as_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_windows_probe(monkeypatch, returncode=0)

    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("probe", timeout=5)

    monkeypatch.setattr(submission.subprocess, "run", fail)

    with pytest.raises(AsyncJobHostUnsupportedError, match="Cannot verify"):
        submission._verify_windows_worker_breakaway()


def test_unsupported_windows_host_is_rejected_before_job_creation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(submission, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        submission,
        "_verify_windows_worker_breakaway",
        lambda: (_ for _ in ()).throw(
            AsyncJobHostUnsupportedError("Windows host cannot detach the worker")
        ),
    )
    preflight_called = False

    def preflight(**kwargs: object) -> tuple[object, str, str, str, int]:
        nonlocal preflight_called
        preflight_called = True
        return tmp_path, "spec.md", "http://127.0.0.1:8090/v1", "fake", 1

    monkeypatch.setattr(submission, "preflight_task_request", preflight)

    with pytest.raises(AsyncJobHostUnsupportedError, match="cannot detach"):
        submission.submit_job(
            repo_root=tmp_path,
            spec_path="spec.md",
            task_id="T001",
            test_profile="pytest",
        )

    assert preflight_called
    assert not (tmp_path / ".hybrid_sdlc" / "jobs").exists()
