"""Atomic persistence, recursive redaction, and retention cleanup for artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from pydantic import BaseModel

from hybrid_sdlc.errors import PathTraversalError, SecurityBoundaryError

# Common secret regex patterns for text redaction
REDACTION_PATTERNS = [
    # URLs with embedded credentials (e.g. https://user:pass@example.com)
    re.compile(r"(https?://)([^:\s/@]+):([^@\s/]+)@", re.IGNORECASE),
    # Assignment format: API_KEY=xyz, key=xyz, token: "xyz", secret = 'xyz'
    re.compile(
        r"""(?i)\b(api_key|key|token|secret|password|bearer|auth)[\s:=]+["']?([A-Za-z0-9_\-\.]{8,})["']?"""
    ),
    # GitHub personal access tokens
    re.compile(r"\b(ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})\b"),
    # Generic API tokens (e.g. OpenAI sk-..., Anthropic sk-ant-...)
    re.compile(r"\b(sk-[A-Za-z0-9\-_]{20,})\b"),
    # AWS access key ID
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
]


def redact_secrets(text: str, configured_literals: list[str] | None = None) -> str:
    """Recursively redact recognized API keys, secrets, tokens, and URL credentials from text."""
    if not isinstance(text, str):
        return text

    redacted = text

    # Redact URL credentials: replace password with [REDACTED]
    redacted = REDACTION_PATTERNS[0].sub(r"\1\2:[REDACTED]@", redacted)

    # Redact assignment format
    redacted = REDACTION_PATTERNS[1].sub(r"\1=[REDACTED]", redacted)

    # Redact known token formats
    for pattern in REDACTION_PATTERNS[2:]:
        redacted = pattern.sub("[REDACTED]", redacted)

    for literal in configured_literals or []:
        if literal:
            redacted = redacted.replace(literal, "[REDACTED]")

    return redacted


def redact_data(obj: Any, configured_literals: list[str] | None = None) -> Any:
    """Recursively traverse and redact sensitive values in dictionaries, lists, and strings."""
    if isinstance(obj, str):
        return redact_secrets(obj, configured_literals)
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            k_str = str(k)
            # If the key itself indicates a sensitive value, redact the whole value
            if any(
                term in k_str.lower() for term in ("key", "secret", "token", "password", "auth")
            ):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_data(v, configured_literals)
        return cleaned
    if isinstance(obj, list):
        return [redact_data(item, configured_literals) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_data(item, configured_literals) for item in obj)
    return obj


def atomic_save_json(
    target_path: Path,
    data: Any,
    repo_root: Path,
) -> Path:
    """Atomically write redacted JSON data to target_path within repo_root/.hybrid_sdlc.

    Uses a same-directory temporary file and atomic replacement (os.replace).
    """
    repo_root = repo_root.resolve()
    target_path = target_path.resolve()

    # Confine to .hybrid_sdlc within repo_root
    artifacts_root = repo_root / ".hybrid_sdlc"
    try:
        target_path.relative_to(artifacts_root)
    except ValueError:
        raise PathTraversalError(
            f"Artifact path '{target_path}' is outside artifacts directory '{artifacts_root}'",
            details={"target_path": str(target_path), "artifacts_root": str(artifacts_root)},
        ) from None

    target_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(data, BaseModel):
        raw_dict = data.model_dump(mode="json")
    elif hasattr(data, "__dict__"):
        raw_dict = data.__dict__
    else:
        raw_dict = data

    cleaned_dict = redact_data(raw_dict)
    json_bytes = json.dumps(cleaned_dict, indent=2).encode("utf-8")

    # Serialize writers with a sidecar lock, then replace from the same directory.
    artifact_lock = FileLock(str(target_path) + ".lock")
    with artifact_lock:
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=str(target_path.parent),
            prefix=f".tmp_{target_path.name}_",
        )
        tmp_path = Path(tmp_path_str)

        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(json_bytes)
                f.flush()
                os.fsync(f.fileno())

            # chmod expresses user-only access on POSIX. Windows inherits the
            # repository's ACL; chmod there can briefly disrupt concurrent readers.
            if os.name != "nt":
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass

            for retry in range(20):
                try:
                    os.replace(tmp_path, target_path)
                    break
                except PermissionError:
                    if retry == 19:
                        raise
                    time.sleep(0.01)
            return target_path
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


@dataclass
class CleanupResult:
    """Artifacts deleted and artifacts skipped because a writer owns their lock."""

    deleted: list[str] = field(default_factory=list)
    skipped_active: list[str] = field(default_factory=list)


def clean_artifacts(
    repo_root: Path,
    older_than_seconds: float,
    dry_run: bool = False,
) -> CleanupResult:
    """Clean up stale artifacts under .hybrid_sdlc older than the specified duration.

    Never deletes outside .hybrid_sdlc/. Skips active locks and live files.
    """
    repo_root = repo_root.resolve()
    artifacts_root = repo_root / ".hybrid_sdlc"

    if not artifacts_root.is_dir():
        return CleanupResult()

    if older_than_seconds < 0:
        raise SecurityBoundaryError(
            "Artifact retention duration must not be negative",
            details={"older_than_seconds": older_than_seconds},
        )

    cutoff_time = time.time() - older_than_seconds
    result = CleanupResult()

    # Files to never delete
    protected_basenames = {"runner.lock", "runner.lock.meta"}

    for root, _dirs, files in os.walk(artifacts_root):
        root_path = Path(root).resolve()
        # Verify boundary
        try:
            root_path.relative_to(artifacts_root)
        except ValueError:
            raise SecurityBoundaryError(
                f"Directory traversal outside artifacts root detected: '{root_path}'",
                details={"dir": str(root_path)},
            ) from None

        for filename in files:
            if filename in protected_basenames or filename.endswith(".lock"):
                continue

            file_path = root_path / filename
            try:
                stat = file_path.stat()
                if stat.st_mtime < cutoff_time:
                    rel_path = file_path.relative_to(repo_root).as_posix()
                    file_lock = FileLock(str(file_path) + ".lock", timeout=0)
                    try:
                        file_lock.acquire()
                    except Timeout:
                        result.skipped_active.append(rel_path)
                        continue
                    try:
                        if not dry_run:
                            file_path.unlink(missing_ok=True)
                        result.deleted.append(rel_path)
                    finally:
                        file_lock.release()
            except OSError:
                continue

    return result
