"""Explicit host application setup helpers."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HostSetupError(ValueError):
    """Raised when a host configuration cannot be safely updated."""


@dataclass(frozen=True)
class AntigravitySetupResult:
    """Outcome of preparing Antigravity's user-level MCP configuration."""

    config_path: Path
    executable: Path
    changed: bool
    dry_run: bool
    proposal_path: Path | None = None


def antigravity_config_path() -> Path:
    """Return the Antigravity MCP configuration path for the current user."""

    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def _installed_executable() -> Path:
    executable = shutil.which("hybrid-sdlc")
    if executable is None:
        raise HostSetupError(
            "Cannot find the installed hybrid-sdlc executable on PATH. Install the CLI and retry."
        )
    return Path(executable).resolve()


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate keys at every depth."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _existing_entry_matches(entry: Any, executable: Path) -> bool:
    if (
        not isinstance(entry, dict)
        or set(entry) != {"command", "args"}
        or entry.get("args") != ["mcp"]
    ):
        return False
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return False
    resolved = shutil.which(command)
    if resolved is None:
        candidate = Path(command)
        if not candidate.is_absolute():
            return False
        resolved = str(candidate)
    try:
        return _normalized_path(Path(resolved)) == _normalized_path(executable)
    except (OSError, ValueError):
        return False


def _publish_no_clobber(path: Path, contents: bytes, mode: int) -> None:
    """Publish bytes at path atomically without replacing an existing file."""

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(contents)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, mode)
        os.link(temporary_path, path)
    finally:
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)


def _proposal_path(path: Path, contents: bytes) -> Path:
    """Publish a private, uniquely named proposal beside an existing config."""

    suffix = 0
    while True:
        candidate = _proposal_candidate(path, suffix)
        try:
            _publish_no_clobber(candidate, contents, 0o600)
            return candidate
        except FileExistsError:
            suffix += 1


def _proposal_candidate(path: Path, suffix: int) -> Path:
    name = f"{path.name}.proposed.json" if suffix == 0 else f"{path.name}.proposed.{suffix}.json"
    return path.with_name(name)


def _next_proposal_candidate(path: Path) -> Path:
    suffix = 0
    while True:
        candidate = _proposal_candidate(path, suffix)
        if not candidate.exists():
            return candidate
        suffix += 1


def setup_antigravity(
    *,
    config_path: Path | None = None,
    executable: Path | None = None,
    dry_run: bool = False,
) -> AntigravitySetupResult:
    """Register the installed CLI as an Antigravity stdio MCP server."""

    target = config_path or antigravity_config_path()
    command_path = (executable or _installed_executable()).resolve()
    if target.is_symlink():
        raise HostSetupError(f"Refusing to replace symlinked configuration file: {target}")

    if target.exists():
        try:
            target_mode = stat.S_IMODE(target.stat().st_mode)
        except OSError as exc:
            raise HostSetupError(f"Cannot inspect permissions for {target}: {exc}") from exc
        try:
            original_bytes = target.read_bytes()
            document = json.loads(original_bytes, object_pairs_hook=_unique_object)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise HostSetupError(f"Cannot read valid JSON from {target}: {exc}") from exc
        if not isinstance(document, dict):
            raise HostSetupError(f"Expected a JSON object at the root of {target}")
    else:
        target_mode = 0o600
        original_bytes = None
        document = {}

    servers = document.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise HostSetupError(f"Expected 'mcpServers' to be an object in {target}")
    if "hybrid-sdlc" in servers:
        existing = servers["hybrid-sdlc"]
        if _existing_entry_matches(existing, command_path):
            return AntigravitySetupResult(target, command_path, False, dry_run)
        raise HostSetupError(
            "An existing 'hybrid-sdlc' MCP server entry conflicts with this setup; "
            "edit it manually after reviewing the current configuration."
        )

    updated = dict(document)
    updated_servers = dict(servers)
    updated_servers["hybrid-sdlc"] = {"command": str(command_path), "args": ["mcp"]}
    updated["mcpServers"] = updated_servers
    serialized = (json.dumps(updated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    if dry_run:
        proposal = _next_proposal_candidate(target) if original_bytes is not None else None
        return AntigravitySetupResult(target, command_path, True, True, proposal)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HostSetupError(
            f"Could not create configuration directory {target.parent}: {exc}"
        ) from exc

    if original_bytes is not None:
        try:
            proposal = _proposal_path(target, serialized)
        except OSError as exc:
            raise HostSetupError(
                f"Could not prepare a private proposal for {target}: {exc}"
            ) from exc
        return AntigravitySetupResult(target, command_path, True, False, proposal)

    if target.is_symlink():
        raise HostSetupError(f"Refusing to create configuration over symlink: {target}")
    try:
        _publish_no_clobber(target, serialized, target_mode)
    except FileExistsError as exc:
        raise HostSetupError(
            f"Configuration appeared while setup was running; refusing to overwrite {target}"
        ) from exc
    except OSError as exc:
        raise HostSetupError(f"Could not safely create {target}: {exc}") from exc
    return AntigravitySetupResult(target, command_path, True, False)
