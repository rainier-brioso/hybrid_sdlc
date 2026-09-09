"""Security boundaries, path confinement, and sanitized environment construction."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from hybrid_sdlc.errors import (
    PathTraversalError,
    RepositoryRootNotFoundError,
)

SECRET_PATTERNS = [
    re.compile(r".*API_KEY.*", re.IGNORECASE),
    re.compile(r".*SECRET.*", re.IGNORECASE),
    re.compile(r".*TOKEN.*", re.IGNORECASE),
    re.compile(r".*CREDENTIAL.*", re.IGNORECASE),
    re.compile(r".*PASSWORD.*", re.IGNORECASE),
    re.compile(r".*AUTH.*", re.IGNORECASE),
    re.compile(r"^(AWS_|GITHUB_|ANTHROPIC_|GEMINI_|OPENAI_)", re.IGNORECASE),
]

SAFE_BASELINE_VARS_WINDOWS = [
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "COMSPEC",
    "USERPROFILE",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
]

SAFE_BASELINE_VARS_POSIX = [
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM",
]


def verify_repo_root(candidate_path: Path | str | None = None) -> Path:
    """Verify and return the canonical absolute repository root.

    Fails closed if the path does not exist, is not a directory, is not a Git
    repository, or if a nested subdirectory was supplied instead of the root.
    """
    if candidate_path is None:
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=True,
            )
            resolved_str = res.stdout.strip()
            if not resolved_str:
                raise RepositoryRootNotFoundError("git rev-parse returned empty repository root")
            return Path(resolved_str).resolve()
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            raise RepositoryRootNotFoundError(
                f"Could not discover git repository root from current directory: {e}",
                details={"error": str(e)},
            ) from e

    target = Path(candidate_path).resolve()
    if not target.exists():
        raise RepositoryRootNotFoundError(
            f"Specified repository root path does not exist: '{target}'",
            details={"path": str(target)},
        )
    if not target.is_dir():
        raise RepositoryRootNotFoundError(
            f"Specified repository root is not a directory: '{target}'",
            details={"path": str(target)},
        )

    try:
        res = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        git_root = Path(res.stdout.strip()).resolve()
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        raise RepositoryRootNotFoundError(
            f"Path '{target}' is not a valid git repository: {e}",
            details={"path": str(target), "error": str(e)},
        ) from e

    if target != git_root:
        raise RepositoryRootNotFoundError(
            f"Specified path '{target}' is a nested directory inside git repository '{git_root}'. Expected top-level root.",
            details={"specified": str(target), "git_root": str(git_root)},
        )

    return target


def resolve_confined_path(
    path: Path | str,
    repo_root: Path,
    must_exist: bool = True,
) -> Path:
    """Resolve a relative or absolute path and strictly confine it within repo_root.

    Rejects parent traversal, symlink escapes, and Windows alternate data streams.
    If must_exist is False, validates that the nearest existing parent is confined.
    """
    repo_root = repo_root.resolve()
    path_str = str(path).strip()

    # Reject Windows Alternate Data Streams (e.g. file.txt:secret)
    if os.name == "nt":
        # Strip drive specifier before checking for colon
        check_part = path_str[2:] if (len(path_str) >= 2 and path_str[1] == ":") else path_str
        if ":" in check_part:
            raise PathTraversalError(
                f"Invalid path containing alternate data stream colon: '{path_str}'",
                details={"path": path_str},
            )

    p = Path(path_str)
    candidate = (repo_root / p).resolve() if not p.is_absolute() else p.resolve()

    if must_exist:
        if not candidate.exists():
            raise PathTraversalError(
                f"Path does not exist: '{candidate}'",
                details={"path": str(candidate)},
            )
        try:
            candidate.relative_to(repo_root)
        except ValueError:
            raise PathTraversalError(
                f"Path '{candidate}' escapes repository root '{repo_root}'",
                details={"path": str(candidate), "repo_root": str(repo_root)},
            ) from None
        return candidate

    # If file doesn't need to exist yet, check its nearest existing parent
    curr = candidate
    while not curr.exists():
        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    try:
        curr.relative_to(repo_root)
        candidate.relative_to(repo_root)
    except ValueError:
        raise PathTraversalError(
            f"Path '{candidate}' or parent '{curr}' escapes repository root '{repo_root}'",
            details={"path": str(candidate), "parent": str(curr), "repo_root": str(repo_root)},
        ) from None

    return candidate


def build_sanitized_environment(
    allowlist: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Construct a minimal, sanitized environment for subprocess execution.

    Strips secret variables and injects the local OpenAI dummy credential.
    """
    allowlisted_keys = set(allowlist or [])
    safe_baseline_keys = SAFE_BASELINE_VARS_WINDOWS if os.name == "nt" else SAFE_BASELINE_VARS_POSIX

    clean_env: dict[str, str] = {}

    for k, v in os.environ.items():
        # Include if in safe baseline
        if k.upper() in safe_baseline_keys:
            clean_env[k] = v
            continue

        # Include if in explicit allowlist
        if k in allowlisted_keys:
            # Check secret patterns unless explicitly allowlisted
            clean_env[k] = v

    # Ensure no accidentally leaked secret patterns survive unless explicitly allowlisted
    for k in list(clean_env.keys()):
        if k not in allowlisted_keys and any(p.search(k) for p in SECRET_PATTERNS):
            del clean_env[k]

    # Inject local OpenAI-compatible dummy credentials
    clean_env["OPENAI_API_KEY"] = "local-no-key"

    if extra_env:
        for key, value in extra_env.items():
            if key != "OPENAI_API_KEY":
                clean_env[key] = value

    # This credential is deliberately fixed for local OpenAI-compatible endpoints.
    clean_env["OPENAI_API_KEY"] = "local-no-key"

    return clean_env
