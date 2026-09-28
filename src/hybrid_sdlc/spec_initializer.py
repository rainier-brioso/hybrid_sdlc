"""Safe, idempotent installation of the toolkit's canonical Spec Kit files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.resources
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast

MANIFEST_RELATIVE_PATH = ".specify/.hybrid-sdlc-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEMPLATE_VERSION = "0.1.0"
_TEMPLATE_FILES = (
    ".specify/memory/constitution.md",
    ".specify/templates/spec-template.md",
    ".specify/templates/plan-template.md",
    ".specify/templates/tasks-template.md",
)
_SPEC_KIT_PROBE_TIMEOUT_SECONDS = 5
JOURNAL_RELATIVE_PATH = ".hybrid-sdlc-init-journal.json"


@dataclass(frozen=True, slots=True)
class SpecKitFeature:
    """One feature reported by the installed Spec Kit CLI."""

    name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class SpecKitCapability:
    """Result of probing the locally installed Spec Kit executable."""

    executable: str | None
    version: str | None
    features: tuple[SpecKitFeature, ...]
    compatible: bool
    guidance: str


class ManifestError(ValueError):
    """Raised when initializer ownership metadata is invalid or unsafe."""


class InitializationError(RuntimeError):
    """Raised when a safe, complete initialization cannot be performed."""


class FileOwnership(StrEnum):
    """Ownership state for a managed template destination."""

    UNCHANGED_GENERATED = "unchanged_generated"
    MODIFIED_USER_OWNED = "modified_user_owned"
    UNTRACKED_USER_OWNED = "untracked_user_owned"
    MISSING = "missing"


class FileAction(StrEnum):
    """Action taken for one canonical template path."""

    CREATED = "created"
    UPDATED = "updated"
    PRESERVED = "preserved"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class InitializedFile:
    """Per-path initialization result, including any recoverable backup."""

    path: str
    action: FileAction
    backup_path: str | None = None


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """Summary of one initialization run."""

    files: tuple[InitializedFile, ...]
    manifest_path: str
    spec_kit: SpecKitCapability
    dry_run: bool = False


def _canonical_templates() -> dict[str, bytes]:
    """Load canonical assets from package resources (also works from a wheel)."""
    package = importlib.resources.files("hybrid_sdlc").joinpath("_specify")
    result: dict[str, bytes] = {}
    try:
        for relative_path in _TEMPLATE_FILES:
            resource_path = relative_path.removeprefix(".specify/")
            result[relative_path] = package.joinpath(*resource_path.split("/")).read_bytes()
        return result
    except (FileNotFoundError, OSError) as resource_error:
        # Hatch maps Python sources for editable installs, while force-include
        # assets are guaranteed in built wheels. Read the canonical files from
        # the repository when running directly from a source checkout.
        checkout_root = Path(__file__).resolve().parents[2]
        if not (checkout_root / "pyproject.toml").is_file():
            raise InitializationError(
                "packaged Spec Kit templates are incomplete"
            ) from resource_error
        try:
            return {
                relative_path: (checkout_root / relative_path).read_bytes()
                for relative_path in _TEMPLATE_FILES
            }
        except OSError as exc:
            raise InitializationError("source checkout Spec Kit templates are incomplete") from exc


def detect_spec_kit() -> SpecKitCapability:
    """Read the local Spec Kit CLI's machine-readable capabilities without network access."""
    executable = shutil.which("specify")
    if executable is None:
        return SpecKitCapability(
            None,
            None,
            (),
            False,
            "Install GitHub Spec Kit and ensure its `specify` executable is on PATH, "
            "then rerun `hybrid-sdlc init`.",
        )

    try:
        completed = subprocess.run(
            [executable, "version", "--features", "--json"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=_SPEC_KIT_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SpecKitCapability(
            executable,
            None,
            (),
            False,
            "The local `specify version --features --json` probe timed out. Check the "
            "Spec Kit installation and rerun `hybrid-sdlc init`.",
        )
    except UnicodeError as exc:
        return SpecKitCapability(
            executable,
            None,
            (),
            False,
            f"The Spec Kit capability response could not be decoded ({exc}). Repair or "
            "update Spec Kit, then rerun `hybrid-sdlc init`.",
        )
    except OSError as exc:
        return SpecKitCapability(
            executable,
            None,
            (),
            False,
            f"Could not run the local Spec Kit executable ({exc}). Repair or reinstall "
            "Spec Kit, then rerun `hybrid-sdlc init`.",
        )

    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        return SpecKitCapability(
            executable,
            None,
            (),
            False,
            f"The installed Spec Kit CLI does not support the capability probe ({detail}). "
            "Update Spec Kit to a release that supports `specify version --features --json`, "
            "then rerun `hybrid-sdlc init`.",
        )

    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("response must be a JSON object")
        version = payload.get("version")
        features = payload.get("features")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if not isinstance(features, dict) or any(
            not isinstance(name, str) or not isinstance(enabled, bool)
            for name, enabled in features.items()
        ):
            raise ValueError("features must map names to boolean values")
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        return SpecKitCapability(
            executable,
            None,
            (),
            False,
            f"The installed Spec Kit CLI returned an invalid capability response ({exc}). "
            "Update or repair Spec Kit so `specify version --features --json` returns a "
            "version and boolean feature map, then rerun `hybrid-sdlc init`.",
        )

    return SpecKitCapability(
        executable,
        version,
        tuple(SpecKitFeature(name, enabled) for name, enabled in sorted(features.items())),
        True,
        "Spec Kit capability detection succeeded.",
    )


def initialize_spec_kit(
    repo_root: Path, *, force: bool = False, dry_run: bool = False
) -> InitializationResult:
    """Install canonical Spec Kit files, preserving user-owned content by default.

    All target paths and the existing manifest are validated before any writes.
    Existing customized and untracked files are preserved unless ``force`` is
    true; force first saves their exact bytes in a collision-safe backup tree.
    """
    if type(force) is not bool:
        raise TypeError("force must be a bool")
    if type(dry_run) is not bool:
        raise TypeError("dry_run must be a bool")
    try:
        root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise InitializationError(f"repository root is unavailable: {repo_root}") from exc
    if not root.is_dir():
        raise InitializationError("repository root must be a directory")

    capability = detect_spec_kit()
    if not capability.compatible:
        raise InitializationError(capability.guidance)

    journal_path = _safe_path(root, JOURNAL_RELATIVE_PATH, allow_missing_leaf=True)
    if journal_path.exists() and not journal_path.is_file():
        raise ManifestError("initialization journal is not a regular file")
    recovered_files: tuple[InitializedFile, ...] = ()
    if journal_path.exists():
        journal = _load_journal(journal_path)
        if dry_run:
            return _preview_recovery(root, journal, capability, force=force)
        recovered_files = _recover_journal(root, journal, journal_path)
    recovery_conflicts = {
        item.path for item in recovered_files if item.action is FileAction.PRESERVED
    }

    sources = _canonical_templates()
    manifest = load_manifest(root)
    ownership = classify_files(root, manifest, _TEMPLATE_FILES)
    targets = {
        relative_path: _safe_path(root, relative_path, allow_missing_leaf=True)
        for relative_path in _TEMPLATE_FILES
    }

    previous_records = {entry.path: entry for entry in manifest.files} if manifest else {}
    new_records = dict(previous_records)
    planned: dict[str, tuple[FileAction, bytes | None, bytes | None]] = {}
    # tuple fields are action, original bytes to back up, and desired bytes to write.
    for relative_path, desired in sources.items():
        state = ownership[relative_path]
        old_entry = previous_records.get(relative_path)
        if relative_path in recovery_conflicts:
            planned[relative_path] = (FileAction.PRESERVED, None, None)
            if old_entry is None:
                new_records.pop(relative_path, None)
        elif state is FileOwnership.MISSING:
            planned[relative_path] = (FileAction.CREATED, None, desired)
            new_records[relative_path] = ManagedFile(
                relative_path, sha256_bytes(desired), sha256_bytes(desired)
            )
        elif state is FileOwnership.UNCHANGED_GENERATED:
            current = targets[relative_path].read_bytes()
            if current == desired:
                planned[relative_path] = (FileAction.UNCHANGED, None, None)
            else:
                planned[relative_path] = (FileAction.UPDATED, None, desired)
                new_records[relative_path] = ManagedFile(
                    relative_path, sha256_bytes(desired), sha256_bytes(desired)
                )
        elif force:
            original = targets[relative_path].read_bytes()
            action = FileAction.UPDATED
            planned[relative_path] = (action, original, desired)
            new_records[relative_path] = ManagedFile(
                relative_path, sha256_bytes(desired), sha256_bytes(desired)
            )
        else:
            planned[relative_path] = (FileAction.PRESERVED, None, None)
            if old_entry is None:
                # Untracked files stay untracked; do not claim them as generated.
                new_records.pop(relative_path, None)

    backup_paths: dict[str, str] = {}
    entries: list[dict[str, object]] = []
    for relative_path, (action, backup_content, write_content) in planned.items():
        target = targets[relative_path]
        observed_bytes = target.read_bytes() if target.is_file() else None
        if write_content is None:
            continue
        backup_path = None
        if backup_content is not None:
            backup_path = _backup_relative_path(relative_path)
            backup_paths[relative_path] = backup_path
        entries.append(
            {
                "path": relative_path,
                "before_sha256": (
                    sha256_bytes(observed_bytes) if observed_bytes is not None else None
                ),
                "desired": base64.b64encode(write_content).decode("ascii"),
                "desired_sha256": sha256_bytes(write_content),
                "backup_path": backup_path,
                "backup": (
                    base64.b64encode(backup_content).decode("ascii")
                    if backup_content is not None
                    else None
                ),
                "backup_sha256": (
                    sha256_bytes(backup_content) if backup_content is not None else None
                ),
                "action": action.value,
            }
        )
    updated_manifest = OwnershipManifest(TEMPLATE_VERSION, tuple(new_records.values()))
    journal = {
        "schema_version": 1,
        "entries": entries,
        "manifest": base64.b64encode(updated_manifest.to_bytes()).decode("ascii"),
    }
    if entries:
        _validate_recorded_backups(root, entries)
    if dry_run:
        if force:
            _validate_backup_destinations(root, backup_paths.values())
        results = tuple(
            InitializedFile(path, action, backup_paths.get(path))
            for path, (action, _, content) in planned.items()
        )
        return InitializationResult(results, MANIFEST_RELATIVE_PATH, capability, True)
    try:
        if backup_paths:
            _validate_backup_destinations(root, backup_paths.values())
        _atomic_write(journal_path, _journal_bytes(journal))
        _recover_journal(root, journal, journal_path)
    except (OSError, ManifestError, InitializationError) as exc:
        raise InitializationError(f"Spec Kit initialization was incomplete: {exc}") from exc
    saved_manifest = root / MANIFEST_RELATIVE_PATH
    recovered_by_path = {item.path: item for item in recovered_files}
    result_files_list: list[InitializedFile] = []
    for path, (planned_action, _, _) in planned.items():
        recovered = recovered_by_path.get(path)
        if recovered is not None and planned_action in {
            FileAction.UNCHANGED,
            FileAction.PRESERVED,
        }:
            result_files_list.append(recovered)
        else:
            result_files_list.append(InitializedFile(path, planned_action, backup_paths.get(path)))
    result_files = tuple(result_files_list)
    return InitializationResult(
        result_files, saved_manifest.relative_to(root).as_posix(), capability
    )


def _backup_relative_path(relative_path: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f".specify/backups/{stamp}-{uuid.uuid4().hex}/{relative_path}"


def _validate_backup_destinations(root: Path, relative_paths: Iterable[str]) -> None:
    """Validate backup directory and destinations without creating them."""
    backup_root = _safe_path(root, ".specify/backups", allow_missing_leaf=True)
    if backup_root.exists() and not backup_root.is_dir():
        raise ManifestError("Spec Kit backup path is not a directory")
    for relative_path in relative_paths:
        target = _safe_path(root, relative_path, allow_missing_leaf=True)
        if target.exists() and not target.is_file():
            raise ManifestError(f"backup destination is not a regular file: {relative_path!r}")


def _validate_recorded_backups(root: Path, entries: Iterable[dict[str, object]]) -> None:
    """Check existing backup bytes against the journal before preview or recovery."""
    backup_paths = [
        str(entry["backup_path"]) for entry in entries if entry["backup_path"] is not None
    ]
    _validate_backup_destinations(root, backup_paths)
    for entry in entries:
        if entry["backup_path"] is None:
            continue
        backup_path = str(entry["backup_path"])
        target = _safe_path(root, backup_path, allow_missing_leaf=True)
        if target.exists() and sha256_bytes(target.read_bytes()) != entry["backup_sha256"]:
            raise ManifestError(f"recoverable backup is occupied or changed: {backup_path}")


def _journal_bytes(journal: dict[str, object]) -> bytes:
    return (json.dumps(journal, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _load_journal(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
        if not isinstance(value, dict) or set(value) != {"schema_version", "entries", "manifest"}:
            raise ValueError("invalid journal shape")
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ValueError("unsupported journal schema")
        if not isinstance(value["entries"], list) or not isinstance(value["manifest"], str):
            raise ValueError("invalid journal fields")
        seen_paths: set[str] = set()
        for entry in value["entries"]:
            if not isinstance(entry, dict) or set(entry) != {
                "path",
                "before_sha256",
                "desired",
                "desired_sha256",
                "backup_path",
                "backup",
                "backup_sha256",
                "action",
            }:
                raise ValueError("invalid journal entry")
            validate_managed_path(entry["path"])
            if entry["path"] not in _TEMPLATE_FILES or entry["path"] in seen_paths:
                raise ValueError("journal contains an unsupported or duplicate template path")
            seen_paths.add(entry["path"])
            if not isinstance(entry["desired"], str) or not isinstance(
                entry["desired_sha256"], str
            ):
                raise ValueError("invalid desired content")
            desired = base64.b64decode(entry["desired"], validate=True)
            if sha256_bytes(desired) != entry["desired_sha256"]:
                raise ValueError("desired content digest mismatch")
            if entry["before_sha256"] is not None:
                _validate_digest(entry["before_sha256"], "before_sha256")
            if entry["backup"] is not None:
                backup = base64.b64decode(entry["backup"], validate=True)
                if sha256_bytes(backup) != entry["backup_sha256"]:
                    raise ValueError("backup content digest mismatch")
                if not isinstance(entry["backup_path"], str) or not entry["backup_path"].startswith(
                    ".specify/backups/"
                ):
                    raise ValueError("invalid backup path")
                backup_parts = PurePosixPath(entry["backup_path"]).parts
                target_parts = PurePosixPath(entry["path"]).parts
                if (
                    len(backup_parts) != len(target_parts) + 3
                    or backup_parts[:2] != (".specify", "backups")
                    or backup_parts[3:] != target_parts
                    or not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{32}", backup_parts[2])
                ):
                    raise ValueError("backup path does not match the managed file")
                _validate_digest(entry["backup_sha256"], "backup_sha256")
            elif any(entry[field] is not None for field in ("backup_path", "backup_sha256")):
                raise ValueError("incomplete backup metadata")
            if entry["action"] not in {item.value for item in FileAction}:
                raise ValueError("invalid file action")
        manifest = base64.b64decode(value["manifest"], validate=True)
        desired_manifest = OwnershipManifest.from_bytes(manifest)
        desired_records = {record.path: record.sha256 for record in desired_manifest.files}
        for entry in value["entries"]:
            assert isinstance(entry, dict)
            if desired_records.get(str(entry["path"])) != entry["desired_sha256"]:
                raise ValueError("journal manifest does not claim each intended template digest")
        return value
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise ManifestError(f"invalid initialization journal: {exc}") from exc


def _recover_journal(
    root: Path, journal: dict[str, object], journal_path: Path
) -> tuple[InitializedFile, ...]:
    manifest = load_manifest(root)
    records = {record.path: record for record in manifest.files} if manifest else {}
    entries = cast(list[dict[str, object]], journal["entries"])
    # Complete and verify every force backup before replacing any managed file.
    _validate_recorded_backups(root, entries)
    for raw_entry in entries:
        if raw_entry["backup"] is None:
            continue
        backup_path = str(raw_entry["backup_path"])
        backup_target = _safe_path(root, backup_path, allow_missing_leaf=True)
        backup_content = base64.b64decode(str(raw_entry["backup"]), validate=True)
        if backup_target.exists():
            if (
                not backup_target.is_file()
                or sha256_bytes(backup_target.read_bytes()) != raw_entry["backup_sha256"]
            ):
                raise ManifestError(f"recoverable backup is occupied or changed: {backup_path}")
        else:
            _atomic_write(backup_target, backup_content)

    outcomes: list[InitializedFile] = []
    for raw_entry in entries:
        relative_path = str(raw_entry["path"])
        target = _safe_path(root, relative_path, allow_missing_leaf=True)
        desired = base64.b64decode(str(raw_entry["desired"]), validate=True)
        desired_hash = str(raw_entry["desired_sha256"])
        before_hash = raw_entry["before_sha256"]
        current = target.read_bytes() if target.is_file() else None
        current_hash = sha256_bytes(current) if current is not None else None
        if current_hash == before_hash:
            _atomic_write(target, desired)
            current_hash = desired_hash
        if current_hash == desired_hash:
            records[relative_path] = ManagedFile(relative_path, desired_hash, desired_hash)
        elif before_hash is None and relative_path in records:
            records.pop(relative_path, None)
        outcomes.append(
            InitializedFile(
                relative_path,
                FileAction(str(raw_entry["action"]))
                if current_hash == desired_hash
                else FileAction.PRESERVED,
                str(raw_entry["backup_path"]) if raw_entry["backup_path"] else None,
            )
        )
    desired_manifest = OwnershipManifest(TEMPLATE_VERSION, tuple(records.values()))
    save_manifest(root, desired_manifest)
    journal_path.unlink(missing_ok=True)
    return tuple(outcomes)


def _preview_recovery(
    root: Path, journal: dict[str, object], capability: SpecKitCapability, *, force: bool
) -> InitializationResult:
    entries = cast(list[dict[str, object]], journal["entries"])
    _validate_recorded_backups(root, entries)
    by_path: dict[str, InitializedFile] = {}
    for raw_entry in entries:
        path = str(raw_entry["path"])
        target = _safe_path(root, path, allow_missing_leaf=True)
        current = target.read_bytes() if target.is_file() else None
        digest = sha256_bytes(current) if current is not None else None
        before_hash = raw_entry["before_sha256"]
        desired_hash = raw_entry["desired_sha256"]
        action = (
            FileAction.UNCHANGED
            if digest == desired_hash
            else (FileAction.CREATED if before_hash is None else FileAction.UPDATED)
            if digest == before_hash
            else FileAction.PRESERVED
        )
        backup_path = (
            str(raw_entry["backup_path"])
            if action is not FileAction.PRESERVED and raw_entry["backup_path"]
            else None
        )
        by_path[path] = InitializedFile(path, action, backup_path)
    manifest = load_manifest(root)
    ownership = classify_files(root, manifest, _TEMPLATE_FILES)
    sources = _canonical_templates()
    for path in _TEMPLATE_FILES:
        if path in by_path:
            continue
        state = ownership[path]
        if state is FileOwnership.MISSING:
            action = FileAction.CREATED
        elif state is FileOwnership.UNCHANGED_GENERATED:
            action = (
                FileAction.UNCHANGED
                if (root / path).read_bytes() == sources[path]
                else FileAction.UPDATED
            )
        elif force:
            action = FileAction.UPDATED
        else:
            action = FileAction.PRESERVED
        backup_path = (
            _backup_relative_path(path) if force and action is FileAction.UPDATED else None
        )
        by_path[path] = InitializedFile(path, action, backup_path)
    _validate_backup_destinations(
        root,
        [item.backup_path for item in by_path.values() if item.backup_path is not None],
    )
    return InitializationResult(
        tuple(by_path[path] for path in _TEMPLATE_FILES), MANIFEST_RELATIVE_PATH, capability, True
    )


def _atomic_write(target: Path, content: bytes) -> None:
    """Atomically replace one file with exact bytes."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


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
        elif not target.is_file():
            raise ManifestError(f"managed path is not a regular file: {path!r}")
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
