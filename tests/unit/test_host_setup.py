"""Tests for explicit host application registration."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

import hybrid_sdlc.host_setup as host_setup
from hybrid_sdlc.cli import cli
from hybrid_sdlc.host_setup import (
    AntigravitySetupResult,
    HostSetupError,
    setup_antigravity,
)


def test_antigravity_setup_prepares_proposal_and_preserves_original(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp_config.json"
    original = '{\n  "mcpServers": {\n    "github": {"command": "docker"}\n  }\n}\n'
    config.write_text(original, encoding="utf-8")
    config.chmod(0o640)
    original_mode = stat.S_IMODE(config.stat().st_mode)
    executable = tmp_path / "bin" / "hybrid-sdlc.exe"

    result = setup_antigravity(config_path=config, executable=executable)

    assert result.changed
    assert result.proposal_path == tmp_path / "mcp_config.json.proposed.json"
    assert config.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(config.stat().st_mode) == original_mode
    if os.name == "posix":
        proposal_mode = stat.S_IMODE(result.proposal_path.stat().st_mode)
        assert proposal_mode & ~0o600 == 0
    data = json.loads(result.proposal_path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["github"] == {"command": "docker"}
    assert data["mcpServers"]["hybrid-sdlc"] == {
        "command": str(executable.resolve()),
        "args": ["mcp"],
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_antigravity_proposal_stays_private_when_source_is_world_readable(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text('{"mcpServers": {"github": {}}}\n', encoding="utf-8")
    config.chmod(0o644)

    result = setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc")

    assert result.proposal_path is not None
    assert stat.S_IMODE(config.stat().st_mode) == 0o644
    assert stat.S_IMODE(result.proposal_path.stat().st_mode) == 0o600


def test_antigravity_dry_run_does_not_create_files_or_directories(tmp_path: Path) -> None:
    config = tmp_path / "missing" / "mcp_config.json"

    result = setup_antigravity(
        config_path=config, executable=tmp_path / "hybrid-sdlc.exe", dry_run=True
    )

    assert result.changed
    assert result.dry_run
    assert not config.parent.exists()


def test_antigravity_existing_config_dry_run_does_not_create_proposal(tmp_path: Path) -> None:
    config = tmp_path / "mcp_config.json"
    original = b'{"mcpServers": {"github": {}}}\n'
    config.write_bytes(original)

    result = setup_antigravity(
        config_path=config, executable=tmp_path / "hybrid-sdlc.exe", dry_run=True
    )

    assert result.changed
    assert result.dry_run
    assert result.proposal_path == tmp_path / "mcp_config.json.proposed.json"
    assert config.read_bytes() == original
    assert not list(tmp_path.glob("*.proposed*"))


def test_antigravity_setup_is_noop_for_identical_absolute_entry(tmp_path: Path) -> None:
    config = tmp_path / "mcp_config.json"
    executable = tmp_path / "hybrid-sdlc.exe"
    config.write_text(
        json.dumps({"mcpServers": {"hybrid-sdlc": {"command": str(executable), "args": ["mcp"]}}}),
        encoding="utf-8",
    )
    before = config.read_bytes()

    result = setup_antigravity(config_path=config, executable=executable)

    assert not result.changed
    assert result.proposal_path is None
    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.proposed*"))


def test_antigravity_setup_accepts_unqualified_command_resolving_to_same_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    executable = tmp_path / "bin" / "hybrid-sdlc.exe"
    config.write_text(
        json.dumps({"mcpServers": {"hybrid-sdlc": {"command": "hybrid-sdlc", "args": ["mcp"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "hybrid_sdlc.host_setup.shutil.which",
        lambda command: str(executable) if command == "hybrid-sdlc" else None,
    )
    before = config.read_bytes()

    result = setup_antigravity(config_path=config, executable=executable)

    assert not result.changed
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        "[]",
        '{"mcpServers": []}',
        '{"mcpServers": {}, "mcpServers": {"github": {}}}',
        '{"mcpServers": {"github": {}, "github": {"command": "docker"}}}',
    ],
)
def test_antigravity_malformed_configuration_fails_without_writes(
    tmp_path: Path, contents: str
) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text(contents, encoding="utf-8")
    before = config.read_bytes()

    with pytest.raises(HostSetupError):
        setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.proposed*"))


def test_antigravity_conflicting_entry_fails_without_writes(tmp_path: Path) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"hybrid-sdlc": {"command": "other-server", "args": ["mcp"]}}}),
        encoding="utf-8",
    )
    before = config.read_bytes()

    with pytest.raises(HostSetupError, match="conflicts"):
        setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.proposed*"))


def test_antigravity_entry_with_extra_options_is_a_conflict(tmp_path: Path) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "hybrid-sdlc": {
                        "command": str(tmp_path / "hybrid-sdlc.exe"),
                        "args": ["mcp"],
                        "env": {"MODE": "custom"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    before = config.read_bytes()

    with pytest.raises(HostSetupError, match="conflicts"):
        setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.proposed*"))


def test_antigravity_proposal_preserves_late_concurrent_config_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    original = b'{"mcpServers": {"github": {"command": "docker"}}}\n'
    concurrent = b'{"mcpServers": {"new-entry": {"command": "keep-me"}}}\n'
    config.write_bytes(original)
    actual_publish = host_setup._publish_no_clobber

    def change_then_publish(path: Path, contents: bytes, mode: int) -> None:
        if path == config.with_name("mcp_config.json.proposed.json"):
            config.write_bytes(concurrent)
        actual_publish(path, contents, mode)

    monkeypatch.setattr(host_setup, "_publish_no_clobber", change_then_publish)

    result = setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == concurrent
    assert result.proposal_path is not None
    assert "github" in json.loads(result.proposal_path.read_text(encoding="utf-8"))["mcpServers"]
    assert not list(tmp_path.glob(".*.tmp"))


def test_antigravity_proposal_uses_numbered_name_without_overwriting(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp_config.json"
    first = tmp_path / "mcp_config.json.proposed.json"
    first.write_text("keep existing proposal", encoding="utf-8")
    config.write_text('{"mcpServers": {"github": {}}}\n', encoding="utf-8")

    result = setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert result.proposal_path == tmp_path / "mcp_config.json.proposed.1.json"
    assert first.read_text(encoding="utf-8") == "keep existing proposal"


def test_antigravity_setup_succeeds_when_temporary_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    actual_unlink = Path.unlink

    def fail_temp_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.endswith(".tmp"):
            raise OSError("simulated temporary file lock")
        actual_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_temp_cleanup)

    result = setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert result.changed
    assert config.exists()
    assert json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["hybrid-sdlc"]
    temporary_files = list(tmp_path.glob(".*.tmp"))
    assert len(temporary_files) == 1
    monkeypatch.setattr(Path, "unlink", actual_unlink)
    temporary_files[0].unlink()


def test_antigravity_new_config_does_not_overwrite_concurrent_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    concurrent = b'{"mcpServers": {"other": {"command": "keep-me"}}}\n'
    actual_link = os.link

    def link_with_concurrent_creator(source: Path, destination: Path) -> None:
        if destination == config:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(concurrent)
        actual_link(source, destination)

    monkeypatch.setattr(host_setup.os, "link", link_with_concurrent_creator)

    with pytest.raises(HostSetupError, match="appeared while setup was running"):
        setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == concurrent
    assert not list(tmp_path.glob(".*.tmp"))


def test_antigravity_setup_creates_new_config(tmp_path: Path) -> None:
    config = tmp_path / "nested" / "mcp_config.json"
    executable = tmp_path / "hybrid-sdlc.exe"

    result = setup_antigravity(config_path=config, executable=executable)

    assert result.changed
    assert result.proposal_path is None
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"hybrid-sdlc": {"command": str(executable.resolve()), "args": ["mcp"]}}
    }


def test_antigravity_cli_forwards_dry_run_and_reports_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    proposal = tmp_path / "mcp_config.json.proposed.json"
    executable = tmp_path / "hybrid-sdlc.exe"
    calls: list[bool] = []

    def setup(*, dry_run: bool = False) -> AntigravitySetupResult:
        calls.append(dry_run)
        return AntigravitySetupResult(config, executable, True, dry_run, proposal)

    monkeypatch.setattr("hybrid_sdlc.cli.setup_antigravity", setup)
    result = CliRunner().invoke(cli, ["setup", "antigravity", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls == [True]
    assert "Would prepare a proposal" in result.output
    assert str(proposal) in result.output
    assert str(executable) in result.output


def test_antigravity_cli_reports_existing_config_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    proposal = tmp_path / "mcp_config.json.proposed.json"
    executable = tmp_path / "hybrid-sdlc.exe"

    def setup(*, dry_run: bool = False) -> AntigravitySetupResult:
        return AntigravitySetupResult(config, executable, True, dry_run, proposal)

    monkeypatch.setattr("hybrid_sdlc.cli.setup_antigravity", setup)
    result = CliRunner().invoke(cli, ["setup", "antigravity"])

    assert result.exit_code == 0, result.output
    assert "Prepared a proposal" in result.output
    assert "original config was not changed" in result.output
    assert "close Antigravity" in result.output
    assert str(proposal) in result.output
    assert "Added hybrid-sdlc" not in result.output


def test_antigravity_cli_reports_setup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*, dry_run: bool = False) -> AntigravitySetupResult:
        raise HostSetupError("configuration refused")

    monkeypatch.setattr("hybrid_sdlc.cli.setup_antigravity", fail)
    result = CliRunner().invoke(cli, ["setup", "antigravity"])

    assert result.exit_code != 0
    assert "configuration refused" in result.output
