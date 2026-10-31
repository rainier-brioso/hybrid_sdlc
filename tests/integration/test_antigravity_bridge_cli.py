"""Installed CLI shape using fixture jobs and mocked host transport, never a real App."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from helpers.repositories import create_passing_repo

import hybrid_sdlc.antigravity_bridge as bridge
from hybrid_sdlc.cli import cli
from hybrid_sdlc.job_manager import JobManager, JobStatus

CHAT = "6bddf934-8c2a-4310-a8cc-b3deed89174c"


def _fixture(tmp_path: Path) -> tuple[Path, str, Path]:
    repo = create_passing_repo(tmp_path)
    manager = JobManager(repo)
    record = manager.create("private-contract.md", "PRIVATE_TASK")
    manager.transition(record.job_id, JobStatus.CANCELLED)
    app = tmp_path / "Antigravity.exe"
    app.touch()
    return repo, record.job_id, app


def _arguments(repo: Path, job: str, app: Path, command: str = "antigravity") -> list[str]:
    return [
        "bridge",
        command,
        job,
        "--repo-root",
        str(repo),
        "--conversation-id",
        CHAT,
        "--app-executable",
        str(app),
        "--allow-model-continuation",
        "--wait-timeout",
        "0",
        "--json",
    ]


def test_cli_antigravity_delivers_minimized_metadata_and_preserves_replay_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, job, app = _fixture(tmp_path)
    invocations: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        invocations.append(argv)
        assert argv[:4] == ["trusted-host.exe", "agentapi", "send-message", CHAT]
        assert "PRIVATE_TASK" not in argv[-1]
        assert "private-contract.md" not in argv[-1]
        receipt = repo / ".hybrid_sdlc" / "bridges" / "antigravity" / f"{job}.json"
        assert json.loads(receipt.read_text())["state"] == "intent"
        assert kwargs["stdin_data"] == b""
        return SimpleNamespace(exit_code=0, timed_out=False, cancelled=False)

    monkeypatch.setattr(bridge, "validate_host", lambda _app: ["trusted-host.exe", "agentapi"])
    monkeypatch.setattr(bridge, "run_bounded_subprocess", fake_run)
    args = _arguments(repo, job, app)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["delivery_status"] == "send_accepted"
    assert receipt["response_completed"] is None
    assert receipt["usage"] is None
    replay = CliRunner().invoke(cli, args)
    assert replay.exit_code != 0
    assert len(invocations) == 1
    assert JobManager(repo).get(job).status is JobStatus.CANCELLED


def test_cli_antigravity_requires_target_and_authority_before_host_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, job, app = _fixture(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> list[str]:
        pytest.fail("No host access is allowed without authority and a valid target")

    monkeypatch.setattr(bridge, "validate_host", forbidden)
    args = _arguments(repo, job, app)
    args.remove("--allow-model-continuation")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    args = _arguments(repo, job, app)
    args[args.index(CHAT)] = "latest"
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0


def test_cli_antigravity_sidecar_dry_run_is_nonmutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hybrid_sdlc.antigravity_sidecar import prepare_antigravity_sidecar

    repo, job, app = _fixture(tmp_path)
    config_dir = tmp_path / "fake-user-config"
    executable = tmp_path / "hybrid-sdlc.exe"
    executable.touch()

    def prepare(root: Path, job_id: str, **kwargs: object) -> dict[str, object]:
        return prepare_antigravity_sidecar(
            root,
            job_id,
            config_dir=config_dir,
            executable=executable,
            **kwargs,  # type: ignore[arg-type]
        )

    monkeypatch.setattr("hybrid_sdlc.cli.prepare_antigravity_sidecar", prepare)
    result = CliRunner().invoke(
        cli, [*_arguments(repo, job, app, "antigravity-sidecar"), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    assert not config_dir.exists()


def test_cli_antigravity_help_states_experimental_boundary() -> None:
    for command in ("antigravity", "antigravity-sidecar"):
        result = CliRunner().invoke(cli, ["bridge", command, "--help"])
        assert result.exit_code == 0
        assert "--conversation-id" in result.output
        assert "--app-executable" in result.output
        assert "--allow-model-continuation" in result.output
