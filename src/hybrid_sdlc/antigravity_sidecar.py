"""Explicit, per-job Antigravity App sidecar configuration.

Existing host configuration is never replaced in place. When it exists, this
module writes a private sibling proposal for the user to merge manually.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from hybrid_sdlc.job_manager import JobManager, validate_job_id
from hybrid_sdlc.security import verify_repo_root

_CONFIG_NAME = "config.json"
_SIDECAR_DIR = "sidecars"
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_OWNERSHIP_BYTES = 16 * 1024
_OWNERSHIP_VERSION = 1
_ALLOWED_HOST_ENTRY_KEYS = {"enabled", "projectId", "resolvedPermissions", "schemaVersion"}


class AntigravitySidecarError(ValueError):
    """The Antigravity sidecar could not be prepared or safely removed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AntigravitySidecarError("A valid Antigravity conversation UUID is required") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise AntigravitySidecarError("Conversation ID must be a canonical UUID")
    return canonical


def _finite(value: float, name: str, lower: float, upper: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < lower
        or value > upper
    ):
        raise AntigravitySidecarError(f"{name} must be finite and between {lower} and {upper}")


def _path_is_safe(path: Path) -> bool:
    """Reject a symlink/reparse-point ancestor or a path resolving elsewhere."""
    absolute = Path(os.path.abspath(path))
    current = absolute
    while True:
        if current.is_symlink():
            return False
        try:
            if current.resolve(strict=False) != current:
                return False
        except OSError:
            return False
        if current.parent == current:
            return True
        current = current.parent


def _canonical_file(path: Path, label: str, *, exe_only: bool = False) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or not _path_is_safe(candidate):
        raise AntigravitySidecarError(f"{label} must be an absolute canonical file path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AntigravitySidecarError(f"{label} does not exist") from exc
    if resolved != Path(os.path.abspath(candidate)) or not resolved.is_file():
        raise AntigravitySidecarError(f"{label} must be an existing canonical file")
    if exe_only and resolved.suffix.lower() != ".exe":
        raise AntigravitySidecarError(f"{label} must name an EXE file")
    return resolved


def _installed_cli() -> Path:
    found = shutil.which("hybrid-sdlc")
    if found is None:
        raise AntigravitySidecarError("Cannot find the installed hybrid-sdlc executable on PATH")
    return _canonical_file(Path(found).resolve(), "Installed Hybrid SDLC executable")


def _repo_root(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or not _path_is_safe(candidate):
        raise AntigravitySidecarError("Repository root must be an absolute canonical directory")
    try:
        root = verify_repo_root(candidate)
    except Exception as exc:
        raise AntigravitySidecarError("Repository root is invalid") from exc
    if root != Path(os.path.abspath(candidate)):
        raise AntigravitySidecarError("Repository root must be canonical")
    return root


def _paths(config_dir: Path, sidecar_id: str) -> tuple[Path, Path, Path, Path, Path, Path]:
    directory = Path(config_dir)
    if not directory.is_absolute() or not _path_is_safe(directory):
        raise AntigravitySidecarError("Antigravity config directory must be canonical and absolute")
    config_path = directory / _CONFIG_NAME
    sidecars_root = directory / _SIDECAR_DIR
    sidecar_dir = sidecars_root / sidecar_id
    manifest_path = sidecar_dir / "sidecar.json"
    ownership_path = sidecar_dir / "ownership.json"
    lock_path = directory / ".hybrid-sdlc-sidecar.lock"
    for candidate in (
        config_path,
        sidecars_root,
        sidecar_dir,
        manifest_path,
        ownership_path,
        lock_path,
    ):
        if not _path_is_safe(candidate):
            raise AntigravitySidecarError("Antigravity configuration path contains a symlink")
    return config_path, sidecars_root, sidecar_dir, manifest_path, ownership_path, lock_path


def _read_config(path: Path) -> tuple[dict[str, Any], bytes | None, int]:
    if not path.exists():
        return {}, None, 0o600
    if path.is_symlink() or not path.is_file():
        raise AntigravitySidecarError("Refusing to read a non-regular Antigravity config file")
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            raise AntigravitySidecarError("Antigravity config exceeds the supported size limit")
        content = _read_limited(path, _MAX_CONFIG_BYTES, "Antigravity config")
        document = json.loads(content, object_pairs_hook=_unique_object)
    except AntigravitySidecarError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AntigravitySidecarError("Antigravity config is not readable valid JSON") from exc
    if not isinstance(document, dict):
        raise AntigravitySidecarError("Antigravity config must contain a JSON object")
    return document, content, stat.S_IMODE(path.stat().st_mode)


def _encode(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read_limited(path: Path, limit: int, label: str) -> bytes:
    """Read at most limit+1 bytes so a concurrent grow cannot bypass bounds."""
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise AntigravitySidecarError(f"{label} exceeds the supported size limit")
    return content


def _publish_no_clobber(path: Path, content: bytes, mode: int = 0o600) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(OSError):
            os.chmod(temporary, mode)
        if os.name == "nt":
            # Windows rename is atomic on the same volume and refuses to replace
            # an existing destination; hard links may be denied by host policy.
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _proposal_candidate(config_path: Path, index: int) -> Path:
    suffix = ".proposed.json" if index == 0 else f".proposed.{index}.json"
    return config_path.with_name(config_path.name + suffix)


def _proposal_path(config_path: Path, content: bytes, *, publish: bool) -> Path:
    index = 0
    while True:
        candidate = _proposal_candidate(config_path, index)
        if not _path_is_safe(candidate):
            raise AntigravitySidecarError("Proposal path contains a symlink or reparse point")
        if candidate.is_symlink():
            raise AntigravitySidecarError("A proposal path is a symlink; refusing to overwrite it")
        if not candidate.exists():
            if publish:
                try:
                    _publish_no_clobber(candidate, content)
                except FileExistsError:
                    index += 1
                    continue
            return candidate
        index += 1


def _updated_config(
    document: dict[str, Any], sidecar_id: str, *, remove: bool
) -> tuple[dict[str, Any], bool]:
    original_entries = document.get("sidecars", {})
    if not isinstance(original_entries, dict):
        raise AntigravitySidecarError("Antigravity config 'sidecars' field must be an object")
    if sidecar_id in original_entries:
        entry = original_entries[sidecar_id]
        if (
            not isinstance(entry, dict)
            or not set(entry).issubset(_ALLOWED_HOST_ENTRY_KEYS)
            or (entry.get("enabled") is not True and not (remove and entry.get("enabled") is False))
        ):
            raise AntigravitySidecarError(
                "The deterministic sidecar ID already has a conflicting host config entry"
            )
        changed = remove
        updated_entries = dict(original_entries)
        if remove:
            del updated_entries[sidecar_id]
    else:
        if remove:
            return dict(document), False
        changed = True
        updated_entries = dict(original_entries)
        updated_entries[sidecar_id] = {"enabled": True}
    updated = dict(document)
    updated["sidecars"] = updated_entries
    return updated, changed


def _manifest(
    *,
    job_id: str,
    repo_root: Path,
    conversation_id: str,
    app_executable: Path,
    cli_executable: Path,
    wait_timeout_seconds: float,
    send_timeout_seconds: float,
) -> dict[str, Any]:
    args = [
        "bridge",
        "antigravity",
        job_id,
        "--repo-root",
        str(repo_root),
        "--conversation-id",
        conversation_id,
        "--app-executable",
        str(app_executable),
    ]
    args.append("--allow-model-continuation")
    args.extend(
        [
            "--wait-timeout",
            str(wait_timeout_seconds),
            "--send-timeout",
            str(send_timeout_seconds),
            "--json",
        ]
    )
    return {"command": str(cli_executable), "args": args, "restart_policy": "never"}


def _ownership(
    *,
    sidecar_id: str,
    job_id: str,
    repo_root: Path,
    conversation_id: str,
    manifest_bytes: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": _OWNERSHIP_VERSION,
        "sidecar_id": sidecar_id,
        "job_id": job_id,
        "repo_root": str(repo_root),
        "conversation_id": conversation_id,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def _read_owned_manifest(
    manifest_path: Path,
    ownership_path: Path,
    expected: dict[str, Any],
    *,
    allow_missing_manifest: bool = False,
) -> bool:
    manifest_exists = manifest_path.exists()
    ownership_exists = ownership_path.exists()
    if not manifest_exists and not ownership_exists:
        return False
    if not ownership_exists or (not manifest_exists and not allow_missing_manifest):
        raise AntigravitySidecarError("Sidecar ownership state is incomplete; refusing removal")
    if (manifest_exists and manifest_path.is_symlink()) or ownership_path.is_symlink():
        raise AntigravitySidecarError("Sidecar ownership files cannot be symlinked")
    try:
        if manifest_exists and manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise AntigravitySidecarError("Sidecar manifest exceeds the supported size limit")
        if ownership_path.stat().st_size > _MAX_OWNERSHIP_BYTES:
            raise AntigravitySidecarError("Sidecar ownership file exceeds the supported size limit")
        manifest_bytes = (
            _read_limited(manifest_path, _MAX_MANIFEST_BYTES, "Sidecar manifest")
            if manifest_exists
            else None
        )
        owner_bytes = _read_limited(ownership_path, _MAX_OWNERSHIP_BYTES, "Sidecar ownership file")
        owner = json.loads(owner_bytes, object_pairs_hook=_unique_object)
    except AntigravitySidecarError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AntigravitySidecarError(
            "Sidecar ownership files are unreadable or malformed"
        ) from exc
    if not isinstance(owner, dict) or {
        key: value for key, value in owner.items() if key != "manifest_sha256"
    } != {key: value for key, value in expected.items() if key != "manifest_sha256"}:
        raise AntigravitySidecarError("Sidecar ownership record does not match this job and target")
    if owner.get("manifest_sha256") != expected["manifest_sha256"] or (
        manifest_bytes is not None
        and hashlib.sha256(manifest_bytes).hexdigest() != expected["manifest_sha256"]
    ):
        raise AntigravitySidecarError(
            "Sidecar manifest was modified; refusing to overwrite or remove it"
        )
    if manifest_bytes is not None:
        try:
            json.loads(manifest_bytes, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AntigravitySidecarError("Sidecar manifest is malformed") from exc
    return True


def prepare_antigravity_sidecar(
    repo_root: Path,
    job_id: str,
    *,
    conversation_id: str,
    app_executable: Path,
    allow_model_continuation: bool = False,
    config_dir: Path | None = None,
    executable: Path | None = None,
    dry_run: bool = False,
    remove: bool = False,
    wait_timeout_seconds: float = 1800,
    send_timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Create or remove an explicitly targeted, one-job Antigravity sidecar.

    If ``config.json`` already exists, its proposed update is written to a
    private sibling file and must be merged by the user. This function never
    rewrites that host-owned file.
    """
    if allow_model_continuation is not True and allow_model_continuation is not False:
        raise AntigravitySidecarError("allow_model_continuation must be a boolean")
    if not isinstance(dry_run, bool) or not isinstance(remove, bool):
        raise AntigravitySidecarError("dry_run and remove must be booleans")
    try:
        validate_job_id(job_id)
    except (ValueError, TypeError) as exc:
        raise AntigravitySidecarError("Invalid job ID") from exc
    conversation_id = _canonical_uuid(conversation_id)
    if not remove and allow_model_continuation is not True:
        raise AntigravitySidecarError(
            "Setting up an Antigravity sidecar requires explicit model-continuation consent"
        )
    _finite(wait_timeout_seconds, "wait_timeout_seconds", 0.0, 86400)
    _finite(send_timeout_seconds, "send_timeout_seconds", 0.1, 3600)
    root = _repo_root(Path(repo_root))
    try:
        JobManager(root).get(job_id)
    except (OSError, ValueError) as exc:
        raise AntigravitySidecarError("The requested persisted job does not exist") from exc
    app_path = _canonical_file(Path(app_executable), "Antigravity App executable", exe_only=True)
    cli_path = (
        _canonical_file(Path(executable), "Hybrid SDLC executable")
        if executable is not None
        else _installed_cli()
    )
    target_dir = Path(config_dir) if config_dir is not None else Path.home() / ".gemini" / "config"
    sidecar_id = "hybrid-sdlc-" + hashlib.sha256(f"{root}\0{job_id}".encode()).hexdigest()[:20]
    (
        config_path,
        sidecars_root,
        sidecar_dir,
        manifest_path,
        ownership_path,
        lock_path,
    ) = _paths(target_dir, sidecar_id)
    manifest = _manifest(
        job_id=job_id,
        repo_root=root,
        conversation_id=conversation_id,
        app_executable=app_path,
        cli_executable=cli_path,
        wait_timeout_seconds=wait_timeout_seconds,
        send_timeout_seconds=send_timeout_seconds,
    )
    manifest_bytes = _encode(manifest)
    owner = _ownership(
        sidecar_id=sidecar_id,
        job_id=job_id,
        repo_root=root,
        conversation_id=conversation_id,
        manifest_bytes=manifest_bytes,
    )
    owner_bytes = _encode(owner)
    if not all(
        _path_is_safe(path)
        for path in (config_path, sidecars_root, sidecar_dir, manifest_path, ownership_path)
    ):
        raise AntigravitySidecarError("Antigravity configuration paths contain a symlink")

    try:
        config, original_bytes, config_mode = _read_config(config_path)
    except AntigravitySidecarError:
        raise
    updated, config_changed = _updated_config(config, sidecar_id, remove=remove)
    expected_manifest = _read_owned_manifest(
        manifest_path, ownership_path, owner, allow_missing_manifest=remove
    )
    if not remove and sidecar_id in config.get("sidecars", {}) and not expected_manifest:
        raise AntigravitySidecarError(
            "Refusing to adopt an existing sidecar config entry without ownership evidence"
        )
    if not remove and not expected_manifest:
        if manifest_path.exists() or ownership_path.exists():
            raise AntigravitySidecarError(
                "Unowned sidecar path already exists; refusing to replace it"
            )
    if remove and not expected_manifest:
        if sidecar_id in config.get("sidecars", {}):
            raise AntigravitySidecarError(
                "Refusing to remove a config entry without ownership evidence"
            )
        action = "already_absent"
        proposal = None
    elif remove:
        action = "remove"
        proposal = (
            _proposal_path(config_path, _encode(updated), publish=False)
            if original_bytes is not None and config_changed
            else None
        )
    else:
        action = "prepare"
        proposal = (
            _proposal_path(config_path, _encode(updated), publish=False)
            if original_bytes is not None and config_changed
            else None
        )

    if dry_run or action == "already_absent":
        return {
            "schema_version": "1.0.0",
            "action": action,
            "sidecar_id": sidecar_id,
            "job_id": job_id,
            "config_path": str(config_path),
            "manifest_path": str(manifest_path),
            "ownership_path": str(ownership_path),
            "proposal_path": str(proposal) if proposal is not None else None,
            "config_would_change": config_changed,
            "dry_run": dry_run,
        }

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not _path_is_safe(target_dir):
            raise AntigravitySidecarError("Antigravity config directory changed to a symlink")
        lock = FileLock(str(lock_path), timeout=0, is_singleton=True)
        lock.acquire()
    except Timeout as exc:
        raise AntigravitySidecarError("Another sidecar operation is active") from exc
    except OSError as exc:
        raise AntigravitySidecarError("Could not prepare Antigravity config directory") from exc

    try:
        # Re-read after taking the cooperating-process lock to catch intervening edits.
        (
            config_path,
            sidecars_root,
            sidecar_dir,
            manifest_path,
            ownership_path,
            lock_path,
        ) = _paths(target_dir, sidecar_id)
        config, original_bytes, config_mode = _read_config(config_path)
        updated, config_changed = _updated_config(config, sidecar_id, remove=remove)
        expected_manifest = _read_owned_manifest(
            manifest_path, ownership_path, owner, allow_missing_manifest=remove
        )
        if not remove and sidecar_id in config.get("sidecars", {}) and not expected_manifest:
            raise AntigravitySidecarError(
                "Refusing to adopt an existing sidecar config entry without ownership evidence"
            )
        if remove:
            if not expected_manifest:
                if sidecar_id in config.get("sidecars", {}):
                    raise AntigravitySidecarError(
                        "Refusing to remove a config entry without ownership evidence"
                    )
                action = "already_absent"
                proposal = None
            else:
                proposal = (
                    _proposal_path(config_path, _encode(updated), publish=True)
                    if original_bytes is not None and config_changed
                    else None
                )
                # The proposal is safely published before disabling the sidecar files.
                if not all(
                    _path_is_safe(path)
                    for path in (sidecars_root, sidecar_dir, manifest_path, ownership_path)
                ):
                    raise AntigravitySidecarError(
                        "Sidecar paths changed to symlinks; refusing removal"
                    )
                if not _read_owned_manifest(
                    manifest_path, ownership_path, owner, allow_missing_manifest=True
                ):
                    raise AntigravitySidecarError(
                        "Sidecar ownership changed before removal; refusing to remove it"
                    )
                if manifest_path.exists():
                    manifest_path.unlink()
                ownership_path.unlink()
                action = "remove"
        else:
            if expected_manifest:
                action = "already_configured"
                proposal = (
                    None
                    if not config_changed
                    else _proposal_path(config_path, _encode(updated), publish=True)
                )
            else:
                sidecar_dir.mkdir(parents=True, exist_ok=True)
                if not all(
                    _path_is_safe(path)
                    for path in (sidecars_root, sidecar_dir, manifest_path, ownership_path)
                ):
                    raise AntigravitySidecarError("Sidecar directory changed to a symlink")
                created: list[Path] = []
                try:
                    if not _path_is_safe(ownership_path):
                        raise AntigravitySidecarError("Sidecar ownership path changed to a symlink")
                    _publish_no_clobber(ownership_path, owner_bytes)
                    created.append(ownership_path)
                    if not _path_is_safe(manifest_path):
                        raise AntigravitySidecarError("Sidecar manifest path changed to a symlink")
                    _publish_no_clobber(manifest_path, manifest_bytes)
                    created.append(manifest_path)
                    if original_bytes is not None and config_changed:
                        proposal = _proposal_path(config_path, _encode(updated), publish=True)
                    elif original_bytes is None:
                        if not _path_is_safe(config_path):
                            raise AntigravitySidecarError(
                                "Antigravity config path changed to a symlink"
                            )
                        _publish_no_clobber(config_path, _encode(updated), config_mode)
                    else:
                        proposal = None
                except BaseException:
                    for created_path in reversed(created):
                        with suppress(OSError):
                            created_path.unlink()
                    raise
                action = "prepared"
        return {
            "schema_version": "1.0.0",
            "action": action,
            "sidecar_id": sidecar_id,
            "job_id": job_id,
            "config_path": str(config_path),
            "manifest_path": str(manifest_path),
            "ownership_path": str(ownership_path),
            "proposal_path": str(proposal) if proposal is not None else None,
            "config_would_change": config_changed,
            "dry_run": False,
        }
    except AntigravitySidecarError:
        raise
    except FileExistsError as exc:
        raise AntigravitySidecarError(
            "A sidecar or config path appeared during setup; refusing to overwrite it"
        ) from exc
    except OSError as exc:
        raise AntigravitySidecarError("Could not safely update Antigravity sidecar files") from exc
    finally:
        lock.release()
