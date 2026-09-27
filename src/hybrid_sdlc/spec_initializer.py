"""Ownership metadata for files installed by the Spec Kit initializer.

This module deliberately contains no template loading or installation logic.  It
defines the on-disk contract that an initializer can use to tell its own
unchanged files from user edits and files that were never managed by it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_RELATIVE_PATH = ".specify/.hybrid-sdlc-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """Raised when initializer ownership metadata is invalid or unsafe."""


class FileOwnership(StrEnum):
    """Ownership state for a managed template destination."""

    UNCHANGED_GENERATED = "unchanged_generated"
    MODIFIED_USER_OWNED = "modified_user_owned"
    UNTRACKED_USER_OWNED = "untracked_user_owned"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ManagedFile:
    """A repository-relative generated file and the digest of its installed bytes."""

    path: str
    sha256: str
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        validate_managed_path(self.path)
        _validate_digest(self.sha256, "sha256")
        if self.source_sha256 is not None:
            _validate_digest(self.source_sha256, "source_sha256")


@dataclass(frozen=True, slots=True)
class OwnershipManifest:
    """Versioned, deterministic ownership record for installed Spec Kit files."""

    toolkit_version: str
    files: tuple[ManagedFile, ...]
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise ManifestError("schema_version must be an integer")
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ManifestError(f"unsupported manifest schema version: {self.schema_version!r}")
        if not isinstance(self.toolkit_version, str) or not self.toolkit_version.strip():
            raise ManifestError("toolkit_version must be a non-empty string")
        paths = [entry.path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ManifestError("manifest contains duplicate managed paths")
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda record: record.path)))

    @classmethod
    def from_bytes(cls, data: bytes) -> OwnershipManifest:
        """Parse and strictly validate serialized manifest bytes."""
        try:
            payload = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
            raise ManifestError(f"invalid ownership manifest: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "toolkit_version",
            "files",
        }:
            raise ManifestError(
                "manifest must contain only schema_version, toolkit_version, and files"
            )
        if type(payload["schema_version"]) is not int:
            raise ManifestError("schema_version must be an integer")
        if not isinstance(payload["toolkit_version"], str):
            raise ManifestError("toolkit_version must be a string")
        if not isinstance(payload["files"], list):
            raise ManifestError("files must be an array")
        records: list[ManagedFile] = []
        for index, item in enumerate(payload["files"]):
            if not isinstance(item, dict) or set(item) - {"path", "sha256", "source_sha256"}:
                raise ManifestError(f"files[{index}] has an invalid shape")
            if not {"path", "sha256"}.issubset(item):
                raise ManifestError(f"files[{index}] is missing path or sha256")
            if not isinstance(item["path"], str) or not isinstance(item["sha256"], str):
                raise ManifestError(f"files[{index}] path and sha256 must be strings")
            source_digest = item.get("source_sha256")
            if source_digest is not None and not isinstance(source_digest, str):
                raise ManifestError(f"files[{index}] source_sha256 must be a string")
            records.append(ManagedFile(item["path"], item["sha256"], source_digest))
        return cls(payload["toolkit_version"], tuple(records), payload["schema_version"])

    def to_bytes(self) -> bytes:
        """Serialize in stable path order with a single trailing newline."""
        payload = {
            "schema_version": self.schema_version,
            "toolkit_version": self.toolkit_version,
            "files": [
                {
                    "path": record.path,
                    "sha256": record.sha256,
                    **({"source_sha256": record.source_sha256} if record.source_sha256 else {}),
                }
                for record in sorted(self.files, key=lambda record: record.path)
            ],
        }
        return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def validate_managed_path(path: str) -> str:
    """Validate canonical POSIX repo-relative path in initializer-owned directories."""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path or ":" in path:
        raise ManifestError("managed path must be a non-empty POSIX repository-relative path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ManifestError(f"invalid managed path: {path!r}")
    if pure.as_posix() != path or not (
        path.startswith(".specify/memory/") or path.startswith(".specify/templates/")
    ):
        raise ManifestError(f"path is outside managed Spec Kit directories: {path!r}")
    if len(pure.parts) < 3:
        raise ManifestError(f"managed path must name a file: {path!r}")
    return path


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact file bytes."""
    return hashlib.sha256(data).hexdigest()


def load_manifest(repo_root: Path) -> OwnershipManifest | None:
    """Load the dedicated manifest; return ``None`` when it does not exist.

    Symlink components and non-regular manifest files are rejected to avoid
    following user-controlled paths while trusting ownership metadata.
    """
    root = repo_root.resolve(strict=True)
    manifest_path = _safe_path(root, MANIFEST_RELATIVE_PATH, allow_missing_leaf=True)
    if not manifest_path.exists():
        return None
    if not manifest_path.is_file():
        raise ManifestError("ownership manifest is not a regular file")
    try:
        return OwnershipManifest.from_bytes(manifest_path.read_bytes())
    except OSError as exc:
        raise ManifestError(f"could not read ownership manifest: {exc}") from exc


def save_manifest(repo_root: Path, manifest: OwnershipManifest) -> Path:
    """Atomically save manifest bytes at the dedicated in-repository location."""
    root = repo_root.resolve(strict=True)
    target = _safe_path(root, MANIFEST_RELATIVE_PATH, allow_missing_leaf=True)
    if target.exists() and not target.is_file():
        raise ManifestError("ownership manifest is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=target.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(manifest.to_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


def classify_files(
    repo_root: Path,
    manifest: OwnershipManifest | None,
    paths: list[str] | tuple[str, ...],
) -> dict[str, FileOwnership]:
    """Classify expected managed paths using manifest membership and raw-byte hashes."""
    root = repo_root.resolve(strict=True)
    recorded = {entry.path: entry.sha256 for entry in manifest.files} if manifest else {}
    result: dict[str, FileOwnership] = {}
    for path in paths:
        validate_managed_path(path)
        target = _safe_path(root, path, allow_missing_leaf=True)
        if not target.exists():
            result[path] = FileOwnership.MISSING
        elif path not in recorded:
            result[path] = FileOwnership.UNTRACKED_USER_OWNED
        else:
            try:
                actual_digest = sha256_bytes(target.read_bytes())
            except OSError as exc:
                raise ManifestError(f"could not read managed file {path!r}: {exc}") from exc
            result[path] = (
                FileOwnership.UNCHANGED_GENERATED
                if actual_digest == recorded[path]
                else FileOwnership.MODIFIED_USER_OWNED
            )
    return result


def _safe_path(root: Path, relative_path: str, *, allow_missing_leaf: bool) -> Path:
    """Resolve a validated path while rejecting symlinks in every existing component."""
    candidate = root
    parts = PurePosixPath(relative_path).parts
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            # Continue through absent parents so the returned path is the full target.
            continue
        except OSError as exc:
            raise ManifestError(f"could not inspect managed path {relative_path!r}: {exc}") from exc
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
        is_reparse_point = bool(getattr(info, "st_file_attributes", 0) & reparse_flag)
        if candidate.is_symlink() or is_reparse_point:
            raise ManifestError(
                f"symlink or reparse point in managed path is not allowed: {relative_path!r}"
            )
        if index < len(parts) - 1 and not candidate.is_dir():
            raise ManifestError(f"managed path parent is not a directory: {relative_path!r}")
        if index == len(parts) - 1 and not (candidate.is_file() or candidate.is_dir()):
            raise ManifestError(f"managed path is not a regular file: {relative_path!r}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"managed path escapes repository root: {relative_path!r}") from exc
    return candidate


def _validate_digest(digest: str, field_name: str) -> None:
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ManifestError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result
