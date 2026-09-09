"""Integration tests for CLI subcommands: check, run-task, clean."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from hybrid_sdlc.cli import cli
from hybrid_sdlc.errors import ExitCode

_orig_client = httpx.Client


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    readme = path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(path), check=True, capture_output=True
    )


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "run-task" in result.output
    assert "clean" in result.output


def test_cli_check_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen2.5-Coder-32B-Instruct"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check", "--repo-root", str(tmp_path), "--host-url", "http://127.0.0.1:8090/v1", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["available"] is True
    assert data["matched_model"] == "Qwen/Qwen2.5-Coder-32B-Instruct"


def test_cli_check_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check", "--repo-root", str(tmp_path), "--host-url", "http://127.0.0.1:8090/v1", "--json"],
    )
    assert result.exit_code == ExitCode.SERVER_PROBE_ERROR
    data = json.loads(result.output)
    assert data["available"] is False


def test_cli_clean(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    runs_dir = tmp_path / ".hybrid_sdlc" / "runs"
    runs_dir.mkdir(parents=True)
    old_run = runs_dir / "old_run.json"
    old_run.write_text("{}", encoding="utf-8")

    import os

    past = time.time() - 1000
    os.utime(old_run, (past, past))

    runner = CliRunner()
    # Dry run
    res_dry = runner.invoke(
        cli, ["clean", "--repo-root", str(tmp_path), "--older-than", "500s", "--dry-run", "--json"]
    )
    assert res_dry.exit_code == 0
    data_dry = json.loads(res_dry.output)
    assert data_dry["deleted_count"] == 1
    assert old_run.exists()

    # Real clean
    res_real = runner.invoke(
        cli, ["clean", "--repo-root", str(tmp_path), "--older-than", "500s", "--json"]
    )
    assert res_real.exit_code == 0
    data_real = json.loads(res_real.output)
    assert data_real["deleted_count"] == 1
    assert not old_run.exists()


def test_cli_run_task_dirty_worktree_rejected(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    # Commit config with test profile
    config_file = tmp_path / "hybrid_sdlc.toml"
    config_file.write_text(
        "[command_profiles.pytest]\nargv = ['pytest']\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add config"], cwd=str(tmp_path), check=True, capture_output=True
    )

    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    # Leave spec untracked -> dirty worktree!

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run-task",
            str(spec),
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--json",
        ],
    )
    assert result.exit_code == ExitCode.WORKTREE_DIRTY_OR_LOCKED
    data = json.loads(result.output)
    assert data["code"] == "WORKTREE_DIRTY"


def test_cli_run_task_json_stdout_only(tmp_path: Path) -> None:
    """run-task --json must emit ONLY valid JSON on stdout — no decorative text (HSDLC-029).

    We deliberately trigger a dirty-worktree rejection so we don't need a model,
    then assert the raw output is a single parseable JSON object and nothing else.
    """
    _init_git_repo(tmp_path)

    config_file = tmp_path / "hybrid_sdlc.toml"
    config_file.write_text(
        "[command_profiles.pytest]\nargv = ['pytest']\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add config"], cwd=str(tmp_path), check=True, capture_output=True
    )

    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    # Leave spec untracked → dirty worktree triggers early rejection

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "run-task",
            str(spec),
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "T-1",
            "--test-profile",
            "pytest",
            "--json",
        ],
    )

    # The stdout must be exactly one JSON object with no leading/trailing decoration
    stdout = result.output.strip()
    parsed = json.loads(stdout)  # Raises if not valid JSON or if there is extra text
    assert isinstance(parsed, dict)
    # Ensure no decorative lines snuck in before/after the JSON object
    assert stdout.startswith("{") and stdout.endswith("}")
