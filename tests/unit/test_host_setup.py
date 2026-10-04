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


def test_antigravity_setup_adds_server_preserves_entries_and_backs_up(
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
    assert result.backup_path is not None
    assert result.backup_path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(config.stat().st_mode) == original_mode
    backup_mode = stat.S_IMODE(result.backup_path.stat().st_mode)
    assert backup_mode & ~original_mode == 0
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["mcpServers"]["github"] == {"command": "docker"}
    assert data["mcpServers"]["hybrid-sdlc"] == {
        "command": str(executable.resolve()),
        "args": ["mcp"],
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_antigravity_backup_stays_private_when_source_is_world_readable(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp_config.json"
    config.write_text('{"mcpServers": {"github": {}}}\n', encoding="utf-8")
    config.chmod(0o644)

    result = setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc")

    assert result.backup_path is not None
    assert stat.S_IMODE(config.stat().st_mode) == 0o644
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600


def test_antigravity_dry_run_does_not_create_files_or_directories(tmp_path: Path) -> None:
    config = tmp_path / "missing" / "mcp_config.json"

    result = setup_antigravity(
        config_path=config, executable=tmp_path / "hybrid-sdlc.exe", dry_run=True
    )

    assert result.changed
    assert result.dry_run
    assert not config.parent.exists()


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
    assert result.backup_path is None
    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.bak*"))


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
    assert not list(tmp_path.glob("*.bak*"))


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
    assert not list(tmp_path.glob("*.bak*"))


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
    assert not list(tmp_path.glob("*.bak*"))


def test_antigravity_refuses_to_replace_config_changed_during_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    original = b'{"mcpServers": {"github": {"command": "docker"}}}\n'
    concurrent = b'{"mcpServers": {"new-entry": {"command": "keep-me"}}}\n'
    config.write_bytes(original)
    actual_write = host_setup._write_atomically

    def change_then_write(
        path: Path,
        contents: bytes,
        mode: int,
        expected_original: bytes | None,
    ) -> None:
        config.write_bytes(concurrent)
        actual_write(path, contents, mode, expected_original)

    monkeypatch.setattr(host_setup, "_write_atomically", change_then_write)

    with pytest.raises(HostSetupError, match="changed while preparing"):
        setup_antigravity(config_path=config, executable=tmp_path / "hybrid-sdlc.exe")

    assert config.read_bytes() == concurrent
    assert (tmp_path / "mcp_config.json.bak").read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_antigravity_setup_creates_new_config(tmp_path: Path) -> None:
    config = tmp_path / "nested" / "mcp_config.json"
    executable = tmp_path / "hybrid-sdlc.exe"

    result = setup_antigravity(config_path=config, executable=executable)

    assert result.changed
    assert result.backup_path is None
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "mcpServers": {"hybrid-sdlc": {"command": str(executable.resolve()), "args": ["mcp"]}}
    }


def test_antigravity_cli_forwards_dry_run_and_reports_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "mcp_config.json"
    executable = tmp_path / "hybrid-sdlc.exe"
    calls: list[bool] = []

    def setup(*, dry_run: bool = False) -> AntigravitySetupResult:
        calls.append(dry_run)
        return AntigravitySetupResult(config, executable, True, dry_run)

    monkeypatch.setattr("hybrid_sdlc.cli.setup_antigravity", setup)
    result = CliRunner().invoke(cli, ["setup", "antigravity", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls == [True]
    assert "Would add hybrid-sdlc" in result.output
    assert str(executable) in result.output


def test_antigravity_cli_reports_setup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*, dry_run: bool = False) -> AntigravitySetupResult:
        raise HostSetupError("configuration refused")

    monkeypatch.setattr("hybrid_sdlc.cli.setup_antigravity", fail)
    result = CliRunner().invoke(cli, ["setup", "antigravity"])

    assert result.exit_code != 0
    assert "configuration refused" in result.output
