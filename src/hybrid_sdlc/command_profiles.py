"""Named command profile configuration and executable resolution."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hybrid_sdlc.errors import CommandPolicyError


class CommandProfile(BaseModel):
    """Named command profile specification."""

    model_config = ConfigDict(extra="forbid")

    name: str
    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = 180
    env_allowlist: list[str] = Field(default_factory=list)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("argv must not be empty")
        for i, arg in enumerate(v):
            if not isinstance(arg, str):
                raise ValueError(f"argv element {i} must be a string")
            if not arg.strip():
                raise ValueError(f"argv element {i} must not be empty or whitespace")
        return v

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, v: str) -> str:
        p = Path(v)
        if p.is_absolute() or bool(p.drive) or bool(p.root):
            raise ValueError("profile cwd must be a relative path")
        if ".." in p.parts:
            raise ValueError("profile cwd cannot escape with '..'")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v


def resolve_profile_executable(
    profile: CommandProfile,
    repo_root: Path,
    custom_path_env: str | None = None,
) -> Path:
    """Resolve and validate the profile's executable to an approved absolute path.

    Handles relative paths within the repo, as well as system executables,
    incorporating Windows PATHEXT and POSIX executable checks.
    """
    if not profile.argv:
        raise CommandPolicyError(
            f"Command profile '{profile.name}' has an empty argv array",
            code="COMMAND_POLICY_EMPTY_ARGV",
        )

    exe_target = profile.argv[0]
    repo_root = repo_root.resolve()

    # Case 1: Target contains path separators (relative or explicit path)
    if "/" in exe_target or "\\" in exe_target or exe_target.startswith("."):
        candidate = (repo_root / exe_target).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            raise CommandPolicyError(
                f"Executable '{exe_target}' in profile '{profile.name}' resolves outside the repository root",
                code="COMMAND_POLICY_PATH_ESCAPE",
                details={"executable": str(candidate), "repo_root": str(repo_root)},
            ) from None
        if not candidate.is_file():
            raise CommandPolicyError(
                f"Executable file '{exe_target}' in profile '{profile.name}' does not exist at '{candidate}'",
                code="COMMAND_POLICY_EXECUTABLE_NOT_FOUND",
                details={"executable": str(candidate)},
            )
        _verify_executable_mode(candidate, profile.name)
        return candidate

    # Case 2: Bare command name, resolve via PATH
    resolved_str = shutil.which(exe_target, path=custom_path_env)
    if not resolved_str:
        raise CommandPolicyError(
            f"Executable '{exe_target}' for profile '{profile.name}' could not be found in PATH",
            code="COMMAND_POLICY_EXECUTABLE_NOT_FOUND",
            details={"command": exe_target},
        )

    resolved_path = Path(resolved_str).resolve()
    _verify_executable_mode(resolved_path, profile.name)
    return resolved_path


def _verify_executable_mode(exe_path: Path, profile_name: str) -> None:
    """Check platform-specific execution permissions/extensions."""
    if os.name == "nt":
        valid_exts = {
            ext.lower()
            for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PY").split(";")
            if ext
        }
        # In Windows, if it ends in a known executable extension or is an existing file
        if exe_path.suffix and exe_path.suffix.lower() not in valid_exts:
            raise CommandPolicyError(
                f"File '{exe_path}' for profile '{profile_name}' does not have an executable extension: {exe_path.suffix}",
                code="COMMAND_POLICY_NOT_EXECUTABLE",
                details={"file": str(exe_path), "valid_extensions": sorted(valid_exts)},
            )
    else:
        if not os.access(exe_path, os.X_OK):
            raise CommandPolicyError(
                f"File '{exe_path}' for profile '{profile_name}' does not have executable permission",
                code="COMMAND_POLICY_NOT_EXECUTABLE",
                details={"file": str(exe_path)},
            )
