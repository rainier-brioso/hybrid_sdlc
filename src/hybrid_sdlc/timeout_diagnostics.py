"""Metadata-only timeout evidence; never retain Aider text or request contents."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from hybrid_sdlc.processes import SubprocessResult

MAX_RECORD_BYTES = 128 * 1024
EVIDENCE_WINDOW_SECONDS = 30 * 60
_RUN_ID = re.compile(r"run_[0-9]+_[0-9a-f]{8}\Z")


class TimeoutMetadata(BaseModel):
    """Fixed-size allowlist, not a redacted excerpt of arbitrary model output."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(default=1, ge=1, le=1)
    endpoint_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_seconds: float = Field(ge=0, le=1e9, allow_inf_nan=False)
    exit_code: int = Field(ge=-(2**63), le=2**63 - 1)
    observed_stdout_bytes: int = Field(ge=0, le=40 * 1024 * 1024)
    observed_stderr_bytes: int = Field(ge=0, le=40 * 1024 * 1024)
    output_truncated: bool
    remote_request_cancellation: str = Field(default="unknown", pattern=r"^unknown$")


def endpoint_identity(url: str, model: str) -> str:
    """Associate evidence without persisting credentials, query strings or model text."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        host = "loopback"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path.rstrip("/") or "/v1"
    identity = json.dumps(
        [
            parsed.scheme.lower(),
            host,
            port,
            path,
            parsed.query,
            parsed.username,
            parsed.password,
            model.lower(),
        ]
    )
    return hashlib.sha256(identity.encode()).hexdigest()


def timeout_metadata(result: SubprocessResult, url: str, model: str) -> dict[str, Any]:
    """Only scalar metadata can enter a durable timeout record (under 1 KiB)."""
    metadata = TimeoutMetadata(
        endpoint_identity=endpoint_identity(url, model),
        duration_seconds=result.duration_seconds,
        exit_code=result.exit_code,
        observed_stdout_bytes=len(result.stdout.encode("utf-8")),
        observed_stderr_bytes=len(result.stderr.encode("utf-8")),
        output_truncated=result.is_truncated,
    )
    return metadata.model_dump()


def read_timeout_evidence(
    repo_root: Path, run_id: str, url: str, model: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read one explicit, confined, size-capped run record; no scans or mutations."""
    invalid = {"status": "invalid", "aider_timeout": False}
    if not _RUN_ID.fullmatch(run_id):
        return invalid
    try:
        root = repo_root.resolve()
        path = root / ".hybrid_sdlc" / "runs" / f"{run_id}.json"
        if any(p.resolve() != p for p in (root / ".hybrid_sdlc", path.parent, path)):
            return invalid
        with path.open("rb") as stream:
            contents = stream.read(MAX_RECORD_BYTES + 1)
        if len(contents) > MAX_RECORD_BYTES:
            return invalid
        record = json.loads(contents)
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            return invalid
        failure = record.get("failure")
        if (
            record.get("schema_version") != "1.0.0"
            or record.get("status") != "failure"
            or not isinstance(failure, dict)
            or failure.get("code") != "TASK_TIMEOUT"
        ):
            return {"status": "not-aider-timeout", "aider_timeout": False}
        metadata = TimeoutMetadata.model_validate(failure["details"]["timeout_diagnostics"])
        finished = datetime.fromisoformat(record["finished_at"])
        if finished.tzinfo is None:
            return invalid
        age = ((now or datetime.now(UTC)) - finished).total_seconds()
        if not math.isfinite(age) or age < 0 or age > EVIDENCE_WINDOW_SECONDS:
            return {"status": "expired", "aider_timeout": False}
        if metadata.endpoint_identity != endpoint_identity(url, model):
            return {"status": "endpoint-mismatch", "aider_timeout": False}
        return {"status": "available", "aider_timeout": True, "age_seconds": round(age, 3)}
    except FileNotFoundError:
        return {"status": "missing", "aider_timeout": False}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, OverflowError):
        return invalid


def suspected_stall(
    *,
    evidence: dict[str, Any] | None,
    loaded: object,
    model_available: bool,
    managed_activity: str,
    readiness_tested: bool,
    error_code: str | None,
) -> bool:
    """Two distinct timeouts suggest investigation, never a diagnosis or recovery."""
    return bool(
        evidence is not None
        and evidence.get("status") == "available"
        and evidence.get("aider_timeout") is True
        and loaded is True
        and model_available
        and managed_activity == "idle"
        and readiness_tested
        and error_code == "READINESS_TIMEOUT"
    )
