"""Unit tests for command profiles and executable resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hybrid_sdlc.command_profiles import CommandProfile, resolve_profile_executable
from hybrid_sdlc.errors import CommandPolicyError


def test_command_profile_validation() -> None:
    profile = CommandProfile(
        name="test",
        argv=["pytest", "-v"],
        cwd=".",
        timeout_seconds=60,
        env_allowlist=["PYTEST_ADDOPTS"],
    )
    assert profile.name == "test"
    assert profile.argv == ["pytest", "-v"]
    assert profile.cwd == "."
    assert profile.timeout_seconds == 60


def test_command_profile_rejects_empty_or_whitespace_argv() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        CommandProfile(name="test", argv=[])

    with pytest.raises(ValueError, match="empty or whitespace"):
        CommandProfile(name="test", argv=["   "])


def test_command_profile_rejects_escaping_cwd() -> None:
    with pytest.raises(ValueError, match="profile cwd cannot escape"):
        CommandProfile(name="test", argv=["pytest"], cwd="../other")

    with pytest.raises(ValueError, match="profile cwd must be a relative path"):
        CommandProfile(name="test", argv=["pytest"], cwd="/tmp")


def test_resolve_profile_system_executable(tmp_path: Path) -> None:
    # Use python executable as a known-existing system executable
    py_name = Path(sys.executable).name
    profile = CommandProfile(name="python_run", argv=[py_name, "--version"])
    resolved = resolve_profile_executable(profile, repo_root=tmp_path)
    assert resolved.is_file()
    assert resolved.name.lower().startswith("python")


def test_resolve_profile_relative_executable(tmp_path: Path) -> None:
    script_name = "run_test.bat" if os.name == "nt" else "run_test.sh"
    script = tmp_path / script_name
    script.write_text("echo test", encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)

    profile = CommandProfile(name="custom_script", argv=[f"./{script_name}"])
    resolved = resolve_profile_executable(profile, repo_root=tmp_path)
    assert resolved == script.resolve()


def test_resolve_profile_rejects_path_escape(tmp_path: Path) -> None:
    outside_script = tmp_path.parent / "outside.bat"
    outside_script.write_text("echo outside", encoding="utf-8")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    profile = CommandProfile(name="escape", argv=["../outside.bat"])
    with pytest.raises(CommandPolicyError) as exc_info:
        resolve_profile_executable(profile, repo_root=repo_dir)
    assert exc_info.value.code == "COMMAND_POLICY_PATH_ESCAPE"


def test_resolve_profile_missing_executable(tmp_path: Path) -> None:
    profile = CommandProfile(name="nonexistent", argv=["nonexistent_cmd_xyz_123"])
    with pytest.raises(CommandPolicyError) as exc_info:
        resolve_profile_executable(profile, repo_root=tmp_path)
    assert exc_info.value.code == "COMMAND_POLICY_EXECUTABLE_NOT_FOUND"
