"""Opt-in, at-most-once Antigravity App sidecar delivery of job metadata."""

from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from hybrid_sdlc.codex_bridge import _MESSAGE_PREFIX, _payload, _terminal_digest
from hybrid_sdlc.errors import HybridSDLCError, PathTraversalError, ProcessExecutionError
from hybrid_sdlc.job_manager import TERMINAL_STATUSES, JobManager, validate_job_id
from hybrid_sdlc.processes import run_bounded_subprocess
from hybrid_sdlc.security import verify_repo_root

_OUTPUT_CAP = 16 * 1024
_RECEIPT_VERSION = 1
_PRODUCT_VERSION = (2, 24, 0, 0)
_MAX_VERSION_RESOURCE_BYTES = 1024 * 1024
_MAX_WRAPPER_BYTES = 8 * 1024


class AntigravityBridgeError(HybridSDLCError):
    """The experimental Antigravity App bridge could not safely deliver."""

    default_code = "ANTIGRAVITY_BRIDGE_FAILED"


def _finite(value: float, name: str, lower: float, upper: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < lower
        or value > upper
    ):
        raise AntigravityBridgeError(f"{name} must be finite and between {lower} and {upper}")


def _canonical_conversation_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise AntigravityBridgeError("A valid Antigravity conversation UUID is required") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise AntigravityBridgeError("Antigravity conversation ID must be a canonical UUID")
    return canonical


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def _read_windows_file_version(path: Path) -> tuple[int, int, int, int]:
    """Read the PE file version resource without launching the application."""
    if sys.platform != "win32":
        raise AntigravityBridgeError("Antigravity App bridge is supported only on Windows")
    version_api = ctypes.WinDLL("version", use_last_error=True)  # type: ignore[attr-defined]
    ignored = ctypes.c_uint32()
    size = version_api.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size or size > _MAX_VERSION_RESOURCE_BYTES:
        raise AntigravityBridgeError("Could not read the Antigravity App version resource")
    data = ctypes.create_string_buffer(size)
    if not version_api.GetFileVersionInfoW(str(path), 0, size, data):
        raise AntigravityBridgeError("Could not read the Antigravity App version resource")
    value = ctypes.c_void_p()
    length = ctypes.c_uint32()
    if not version_api.VerQueryValueW(
        data, "\\", ctypes.byref(value), ctypes.byref(length)
    ) or length.value < ctypes.sizeof(_VSFixedFileInfo):
        raise AntigravityBridgeError("Antigravity App has no valid fixed version resource")
    info = ctypes.cast(value, ctypes.POINTER(_VSFixedFileInfo)).contents
    if info.dwSignature != 0xFEEF04BD:
        raise AntigravityBridgeError("Antigravity App has an invalid version signature")
    ms, ls = info.dwProductVersionMS, info.dwProductVersionLS
    return ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF


def _canonical_file(path: Path, description: str) -> Path:
    if not path.is_absolute():
        raise AntigravityBridgeError(f"{description} must be an absolute path")
    if path.is_symlink():
        raise AntigravityBridgeError(f"{description} cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AntigravityBridgeError(f"{description} does not exist") from exc
    if resolved != path.absolute() or not resolved.is_file():
        raise AntigravityBridgeError(f"{description} must be a canonical existing file")
    return resolved


def _agentapi_wrapper(language_server: Path) -> Path:
    advertised = shutil.which("agentapi")
    wrapper = (
        Path(advertised)
        if advertised
        else Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    )
    wrapper = _canonical_file(wrapper, "Antigravity agentapi wrapper")
    if wrapper.suffix.casefold() != ".bat":
        raise AntigravityBridgeError("Antigravity must advertise its agentapi BAT wrapper")
    try:
        if wrapper.stat().st_size > _MAX_WRAPPER_BYTES:
            raise AntigravityBridgeError("Antigravity agentapi wrapper exceeds the size limit")
        with wrapper.open("rb") as stream:
            content = stream.read(_MAX_WRAPPER_BYTES + 1)
        if len(content) > _MAX_WRAPPER_BYTES:
            raise AntigravityBridgeError("Antigravity agentapi wrapper exceeds the size limit")
        lines = [line.strip() for line in content.decode("utf-8-sig").splitlines()]
    except AntigravityBridgeError:
        raise
    except (OSError, UnicodeError) as exc:
        raise AntigravityBridgeError("Could not read the Antigravity agentapi wrapper") from exc
    lines = [line for line in lines if line]
    expected_executable = str(language_server).replace("/", "\\")
    expected = ["@echo off", f'"{expected_executable}" agentapi %*']
    if len(lines) != 2 or any(
        actual.casefold().replace("/", "\\") != wanted.casefold()
        for actual, wanted in zip(lines, expected, strict=True)
    ):
        raise AntigravityBridgeError(
            "Antigravity agentapi wrapper does not match the installed host"
        )
    return wrapper


def validate_host(app_executable: Path) -> list[str]:
    """Validate the observed Windows Antigravity App installation and return argv prefix."""
    if sys.platform != "win32":
        raise AntigravityBridgeError("Antigravity App bridge is supported only on Windows")
    app = _canonical_file(Path(app_executable), "Antigravity App executable")
    if app.name.casefold() != "antigravity.exe":
        raise AntigravityBridgeError("The executable must be Antigravity.exe")
    version = _read_windows_file_version(app)
    if version != _PRODUCT_VERSION:
        raise AntigravityBridgeError("Only Antigravity App 2.24.0 is currently supported")
    language_server = _canonical_file(
        app.parent / "resources" / "bin" / "language_server.exe",
        "Antigravity language server executable",
    )
    _agentapi_wrapper(language_server)
    return [str(language_server), "agentapi"]


def _bridge_paths(repo_root: Path, job_id: str, conversation_id: str) -> tuple[Path, Path, Path]:
    root = Path(repo_root)
    if not root.is_dir() or root.resolve() != root.absolute():
        raise AntigravityBridgeError("Repository root must be an existing canonical directory")
    root = root.resolve()
    artifacts = root / ".hybrid_sdlc"
    bridges = artifacts / "bridges"
    antigravity = bridges / "antigravity"
    receipt = antigravity / f"{job_id}.json"
    conversation_lock = antigravity / f"conversation-{conversation_id}.lock"
    for path in (
        artifacts,
        bridges,
        antigravity,
        receipt,
        Path(str(receipt) + ".lock"),
        conversation_lock,
    ):
        if path.is_symlink() or path.resolve() != path.absolute():
            raise PathTraversalError(
                "Antigravity bridge path contains a symlink or escapes the repository"
            )
        try:
            path.resolve().relative_to(root)
        except ValueError:
            raise PathTraversalError("Antigravity bridge path escapes the repository") from None
    return antigravity, receipt, conversation_lock


def _save_receipt(path: Path, data: dict[str, Any], root: Path, job_id: str) -> None:
    expected_dir, expected, _ = _bridge_paths(root, job_id, str(data["conversation_id"]))
    if path != expected or path.parent != expected_dir:
        raise PathTraversalError("Antigravity bridge receipt path changed unexpectedly")
    encoded = json.dumps(data, sort_keys=True, indent=2).encode("utf-8")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".tmp_{path.name}_")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_antigravity_bridge(
    repo_root: Path,
    job_id: str,
    *,
    conversation_id: str,
    app_executable: Path,
    allow_model_continuation: bool = False,
    wait_timeout_seconds: float = 1800,
    send_timeout_seconds: float = 30,
    poll_interval_seconds: float = 0.5,
) -> dict[str, Any]:
    """Wait for one job and send allowlisted status to one authorized App chat.

    Receipts and locks are scoped to the Antigravity namespace in this repository;
    they do not deduplicate against Codex or other repositories.
    """
    if allow_model_continuation is not True:
        raise AntigravityBridgeError("Delivery requires explicit model-continuation opt-in")
    conversation_id = _canonical_conversation_id(conversation_id)
    try:
        validate_job_id(job_id)
    except (ValueError, TypeError) as exc:
        raise AntigravityBridgeError("Invalid job ID") from exc
    _finite(wait_timeout_seconds, "wait_timeout_seconds", 0, 86400)
    _finite(send_timeout_seconds, "send_timeout_seconds", 0.1, 3600)
    _finite(poll_interval_seconds, "poll_interval_seconds", 0.01, 5)
    try:
        root = verify_repo_root(repo_root)
    except HybridSDLCError as exc:
        raise AntigravityBridgeError("Repository root is invalid") from exc

    # Existing receipts, including malformed ones, block all later host access.
    bridge_dir, receipt_path, conversation_lock_path = _bridge_paths(root, job_id, conversation_id)
    if receipt_path.exists():
        raise AntigravityBridgeError("An existing receipt blocks replay or retargeting")
    manager = JobManager(root)
    try:
        record = manager.get(job_id)
    except (OSError, ValueError) as exc:
        raise AntigravityBridgeError("The requested job does not exist or is invalid") from exc
    host_prefix = validate_host(app_executable)
    deadline = time.monotonic() + wait_timeout_seconds
    while record.status not in TERMINAL_STATUSES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AntigravityBridgeError("Job did not reach a terminal state before timeout")
        time.sleep(min(poll_interval_seconds, remaining))
        try:
            record = manager.get(job_id)
        except (OSError, ValueError) as exc:
            raise AntigravityBridgeError("Could not read the persisted job state") from exc

    try:
        bridge_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AntigravityBridgeError("Could not create the confined receipt directory") from exc
    bridge_dir, receipt_path, conversation_lock_path = _bridge_paths(root, job_id, conversation_id)
    receipt_lock = FileLock(str(receipt_path) + ".lock", timeout=0, is_singleton=True)
    try:
        receipt_lock.acquire()
    except Timeout as exc:
        raise AntigravityBridgeError("Another bridge invocation owns this job") from exc
    except OSError as exc:
        raise AntigravityBridgeError("Could not acquire the receipt lock") from exc

    conversation_lock: FileLock | None = None
    conversation_lock_acquired = False
    try:
        if receipt_path.exists():
            raise AntigravityBridgeError("An existing receipt blocks replay or retargeting")
        # Serialize same-repository Antigravity sends. Other host namespaces and
        # repositories intentionally remain independent.
        conversation_lock = FileLock(str(conversation_lock_path), timeout=0, is_singleton=True)
        try:
            conversation_lock.acquire()
            conversation_lock_acquired = True
        except Timeout as exc:
            raise AntigravityBridgeError(
                "Another bridge invocation owns this conversation"
            ) from exc
        except OSError as exc:
            raise AntigravityBridgeError("Could not acquire the conversation lock") from exc

        # The App or advertised wrapper may change during a long job wait.
        # Revalidate under both locks immediately before persisting intent.
        host_prefix = validate_host(app_executable)
        payload = _payload(record)
        message = _MESSAGE_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        receipt: dict[str, Any] = {
            "schema_version": _RECEIPT_VERSION,
            "job_id": job_id,
            "terminal_digest": _terminal_digest(record),
            "terminal_status": record.status.value,
            "state": "intent",
            "created_at": datetime.now(UTC).isoformat(),
            "conversation_id": conversation_id,
            "delivery_attempted": None,
            "command_succeeded": None,
            "automatic_response_observed": None,
        }
        try:
            _save_receipt(receipt_path, receipt, root, job_id)
        except OSError as exc:
            raise AntigravityBridgeError("Could not persist delivery intent") from exc

        try:
            result = run_bounded_subprocess(
                [*host_prefix, "send-message", conversation_id, message],
                cwd=root,
                env=os.environ.copy(),
                timeout_seconds=send_timeout_seconds,
                buffer_cap_bytes=_OUTPUT_CAP,
                stdin_data=b"",
            )
            receipt["delivery_attempted"] = True
            if result.timed_out or result.cancelled or result.exit_code != 0:
                raise AntigravityBridgeError(
                    "Antigravity delivery is uncertain; inspect the receipt and do not retry",
                    code="BRIDGE_DELIVERY_UNKNOWN",
                )
            receipt["state"] = "command_succeeded"
            receipt["command_succeeded"] = True
            _save_receipt(receipt_path, receipt, root, job_id)
            return {
                "schema_version": "1.0.0",
                "job_id": job_id,
                "job_status": record.status.value,
                "delivery_status": "send_accepted",
                "send_accepted": True,
                "conversation_id": conversation_id,
                "response_completed": None,
                "usage": None,
            }
        except BaseException as exc:
            receipt["delivery_attempted"] = True
            receipt["state"] = "delivery_unknown"
            try:
                _save_receipt(receipt_path, receipt, root, job_id)
            except Exception:
                pass
            if isinstance(exc, KeyboardInterrupt):
                raise
            if isinstance(exc, AntigravityBridgeError):
                raise
            if isinstance(exc, ProcessExecutionError):
                raise AntigravityBridgeError(
                    "Antigravity delivery is uncertain; inspect the receipt and do not retry",
                    code="BRIDGE_DELIVERY_UNKNOWN",
                ) from exc
            raise AntigravityBridgeError(
                "Antigravity delivery is uncertain; inspect the receipt and do not retry",
                code="BRIDGE_DELIVERY_UNKNOWN",
            ) from exc
    finally:
        if conversation_lock is not None and conversation_lock_acquired:
            conversation_lock.release()
        receipt_lock.release()
