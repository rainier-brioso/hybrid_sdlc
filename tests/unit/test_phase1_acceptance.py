"""Focused coverage for Phase 1 acceptance branches and safety failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from filelock import FileLock
from pydantic import BaseModel

from hybrid_sdlc.aider_runner import run_test_profile
from hybrid_sdlc.artifacts import atomic_save_json, clean_artifacts, redact_data, redact_secrets
from hybrid_sdlc.cli import cli, parse_duration_seconds
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.errors import (
    ConfigurationError,
    PathTraversalError,
    ProcessExecutionError,
    RepositoryRootNotFoundError,
    SecurityBoundaryError,
    TaskExecutionError,
)
from hybrid_sdlc.processes import run_bounded_subprocess
from hybrid_sdlc.security import (
    build_sanitized_environment,
    resolve_confined_path,
    verify_repo_root,
)
from hybrid_sdlc.server_probe import probe_endpoint

_ORIGINAL_CLIENT = httpx.Client


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    (path / ".gitignore").write_text(".hybrid_sdlc/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("5", 5.0), ("2m", 120.0), ("1h", 3600.0), ("1d", 86400.0), ("1w", 604800.0)],
)
def test_duration_parser_valid(value: str, seconds: float) -> None:
    assert parse_duration_seconds(value) == seconds


@pytest.mark.parametrize("value", ["", "-1", "nonsense", "-2h"])
def test_duration_parser_invalid(value: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_duration_seconds(value)


def test_repo_discovery_and_empty_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(["git"], 0, stdout=str(tmp_path), stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    assert verify_repo_root() == tmp_path.resolve()

    completed.stdout = ""
    with pytest.raises(RepositoryRootNotFoundError):
        verify_repo_root()


def test_nonexistent_output_confinement_and_absolute_escape(tmp_path: Path) -> None:
    target = resolve_confined_path("new/deep/result.json", tmp_path, must_exist=False)
    assert target == (tmp_path / "new/deep/result.json").resolve()
    with pytest.raises(PathTraversalError):
        resolve_confined_path(tmp_path.parent / "escape.json", tmp_path, must_exist=False)


def test_environment_dummy_key_cannot_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPLICIT_SECRET", "synthetic")
    env = build_sanitized_environment(
        allowlist=["EXPLICIT_SECRET"],
        extra_env={"OPENAI_API_KEY": "real-looking-value", "SAFE_EXTRA": "yes"},
    )
    assert env["EXPLICIT_SECRET"] == "synthetic"
    assert env["OPENAI_API_KEY"] == "local-no-key"
    assert env["SAFE_EXTRA"] == "yes"


class _Payload(BaseModel):
    label: str
    token: str


def test_redaction_literals_tuples_and_pydantic_persistence(tmp_path: Path) -> None:
    literal = "custom-sensitive-literal"
    assert literal not in redact_secrets(f"value={literal}", [literal])
    assert redact_data((literal, "safe"), [literal]) == ("[REDACTED]", "safe")

    target = tmp_path / ".hybrid_sdlc" / "runs" / "model.json"
    atomic_save_json(target, _Payload(label="ok", token="synthetic"), tmp_path)
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "label": "ok",
        "token": "[REDACTED]",
    }


def test_cleanup_empty_negative_and_locked_artifact(tmp_path: Path) -> None:
    assert clean_artifacts(tmp_path, 10).deleted == []
    artifacts = tmp_path / ".hybrid_sdlc" / "runs"
    artifacts.mkdir(parents=True)
    target = artifacts / "active.json"
    target.write_text("{}", encoding="utf-8")
    old = time.time() - 100
    os.utime(target, (old, old))

    lock = FileLock(str(target) + ".lock")
    with lock:
        result = clean_artifacts(tmp_path, 10)
    assert result.deleted == []
    assert result.skipped_active == [".hybrid_sdlc/runs/active.json"]

    with pytest.raises(SecurityBoundaryError):
        clean_artifacts(tmp_path, -1)


def test_process_empty_spawn_failure_and_cancellation(tmp_path: Path) -> None:
    with pytest.raises(ProcessExecutionError):
        run_bounded_subprocess([], tmp_path, dict(os.environ), 1)
    with pytest.raises(ProcessExecutionError):
        run_bounded_subprocess([str(tmp_path / "missing.exe")], tmp_path, dict(os.environ), 1)

    cancel = threading.Event()
    cancel.set()
    result = run_bounded_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        dict(os.environ),
        30,
        cancel_event=cancel,
    )
    assert result.cancelled
    assert not result.timed_out


def test_profile_cwd_must_be_directory(tmp_path: Path) -> None:
    cwd_file = tmp_path / "not-a-directory"
    cwd_file.write_text("x", encoding="utf-8")
    profile = CommandProfile(
        name="bad-cwd",
        argv=[Path(sys.executable).name, "-c", "print('never')"],
        cwd=cwd_file.name,
    )
    with pytest.raises(TaskExecutionError):
        run_test_profile(profile, tmp_path, resolved_executable=Path(sys.executable))


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, text="not-json"), "MALFORMED_JSON"),
        (httpx.Response(200, json={"unexpected": []}), "UNEXPECTED_SCHEMA"),
        (httpx.Response(200, json={"data": []}), "EMPTY_MODELS"),
    ],
)
def test_probe_response_failures(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, code: str
) -> None:
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _ORIGINAL_CLIENT(transport=transport, **kwargs)
    )
    result = probe_endpoint("http://127.0.0.1:8090/v1")
    assert result.error is not None
    assert result.error.code == code


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ConnectTimeout("timeout"), "CONNECT_TIMEOUT"),
        (httpx.ReadTimeout("timeout"), "READ_TIMEOUT"),
        (httpx.ConnectError("offline"), "CONNECT_ERROR"),
    ],
)
def test_probe_network_failures(monkeypatch: pytest.MonkeyPatch, exc: Exception, code: str) -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _ORIGINAL_CLIENT(transport=transport, **kwargs)
    )
    result = probe_endpoint("http://127.0.0.1:8090/v1")
    assert result.error is not None
    assert result.error.code == code


def test_cli_invalid_root_and_missing_profile(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid = runner.invoke(cli, ["check", "--repo-root", str(tmp_path / "missing"), "--json"])
    assert invalid.exit_code != 0
    assert json.loads(invalid.output)["code"] == "REPO_ROOT_NOT_FOUND"

    _init_repo(tmp_path)
    missing_profile = runner.invoke(
        cli,
        [
            "run-task",
            ".gitignore",
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "missing",
            "--json",
        ],
    )
    assert missing_profile.exit_code != 0
    assert json.loads(missing_profile.output)["code"] == "CONFIG_PROFILE_NOT_FOUND"


def test_cli_clean_invalid_duration(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    result = CliRunner().invoke(
        cli, ["clean", "--repo-root", str(tmp_path), "--older-than", "invalid", "--json"]
    )
    assert result.exit_code != 0
    assert json.loads(result.output)["code"] == "CONFIG_INVALID_DURATION"


def test_cli_check_human_probe_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_repo(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if "8090" in str(request.url):
            return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen2.5-Coder-32B-Instruct"}]})
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _ORIGINAL_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["check", "--repo-root", str(tmp_path), "--probe-all"])
    assert result.exit_code == 0
    assert "HEALTHY" in result.output
    assert "UNAVAILABLE" in result.output


def test_cli_clean_human_output(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    result = CliRunner().invoke(cli, ["clean", "--repo-root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert "Would delete 0 artifact file(s)" in result.output
