"""Timeout evidence privacy, retention, confinement and advisory classification."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from filelock import FileLock

from hybrid_sdlc.artifacts import atomic_save_json, clean_artifacts
from hybrid_sdlc.processes import SubprocessResult
from hybrid_sdlc.timeout_diagnostics import (
    MAX_RECORD_BYTES,
    endpoint_identity,
    read_timeout_evidence,
    suspected_stall,
    timeout_metadata,
)

URL = "http://127.0.0.1:8080/v1"
MODEL = "qwen3.8-flash-next-coder-iq1_m"
RUN_ID = "run_100_ab12cd34"
NOW = datetime(2026, 10, 7, tzinfo=UTC)


def _metadata() -> dict[str, object]:
    result = SubprocessResult(
        exit_code=-1,
        stdout="private-prompt secret=private-secret-123",
        stderr="private-source",
        is_truncated=True,
        duration_seconds=150.5,
        timed_out=True,
        cancelled=False,
    )
    return timeout_metadata(result, URL, MODEL)


def _record() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "run_id": RUN_ID,
        "status": "failure",
        "finished_at": NOW.isoformat(),
        "failure": {"code": "TASK_TIMEOUT", "details": {"timeout_diagnostics": _metadata()}},
    }


def _save(root: Path, record: dict[str, object]) -> Path:
    return atomic_save_json(root / ".hybrid_sdlc" / "runs" / f"{RUN_ID}.json", record, root)


def test_metadata_is_small_and_never_copies_output_or_credentials() -> None:
    text = json.dumps(_metadata())
    assert len(text.encode()) < 1024
    assert "private" not in text
    assert _metadata()["output_truncated"] is True
    assert endpoint_identity(
        "http://name:private-password@localhost:8080/v1/", MODEL.upper()
    ) != endpoint_identity(URL, MODEL)
    assert endpoint_identity("http://localhost:8080/v1/", MODEL.upper()) == endpoint_identity(
        URL, MODEL
    )


@pytest.mark.parametrize("suffix", ["?tenant=a", "?tenant=b"])
def test_query_routes_do_not_share_evidence(suffix: str) -> None:
    assert endpoint_identity(URL + suffix, MODEL) != endpoint_identity(URL, MODEL)


def test_different_accounts_do_not_share_evidence() -> None:
    assert endpoint_identity("http://user-a@localhost:8080/v1", MODEL) != endpoint_identity(
        "http://user-b@localhost:8080/v1", MODEL
    )


def test_symlinked_artifact_boundary_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.resolve

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        return tmp_path / "outside" if path == tmp_path / ".hybrid_sdlc" else original(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert read_timeout_evidence(tmp_path, RUN_ID, URL, MODEL)["status"] == "invalid"


def test_explicit_matching_evidence(tmp_path: Path) -> None:
    _save(tmp_path, _record())
    result = read_timeout_evidence(tmp_path, RUN_ID, URL, MODEL, now=NOW)
    assert result == {"status": "available", "aider_timeout": True, "age_seconds": 0.0}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["root", "artifacts", "runs", "record"])
@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_unresolvable_boundary_is_invalid_without_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    error_type: type[Exception],
) -> None:
    root = tmp_path.resolve()
    artifacts = root / ".hybrid_sdlc"
    targets = {
        "root": root,
        "artifacts": artifacts,
        "runs": artifacts / "runs",
        "record": artifacts / "runs" / f"{RUN_ID}.json",
    }
    original = Path.resolve

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == targets[boundary]:
            raise error_type("private filesystem error")
        return original(path, *args, **kwargs)

    def unexpected_open(path: Path, *args: object, **kwargs: object) -> None:
        pytest.fail("Invalid artifact boundary must not be opened")

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "open", unexpected_open)
    assert read_timeout_evidence(root, RUN_ID, URL, MODEL, now=NOW) == {
        "status": "invalid",
        "aider_timeout": False,
    }


@pytest.mark.parametrize("run_id", ["../outside", "run_100_bad", "", "/tmp/secret"])
def test_invalid_run_id_is_not_read(tmp_path: Path, run_id: str) -> None:
    assert read_timeout_evidence(tmp_path, run_id, URL, MODEL)["status"] == "invalid"


@pytest.mark.parametrize("age", [-1, 1801])
def test_future_or_old_evidence_is_expired(tmp_path: Path, age: int) -> None:
    record = _record()
    record["finished_at"] = (NOW - timedelta(seconds=age)).isoformat()
    _save(tmp_path, record)
    assert read_timeout_evidence(tmp_path, RUN_ID, URL, MODEL, now=NOW)["status"] == "expired"


@pytest.mark.parametrize("url,model", [("http://127.0.0.1:8081/v1", MODEL), (URL, "other-model")])
def test_other_endpoint_or_model_is_not_evidence(tmp_path: Path, url: str, model: str) -> None:
    _save(tmp_path, _record())
    assert (
        read_timeout_evidence(tmp_path, RUN_ID, url, model, now=NOW)["status"]
        == "endpoint-mismatch"
    )


@pytest.mark.parametrize(
    "kind", ["missing", "malformed", "oversized", "extra", "naive", "non-timeout"]
)
def test_untrusted_record_does_not_supply_evidence(tmp_path: Path, kind: str) -> None:
    record = _record()
    if kind == "extra":
        record["failure"] = {
            "code": "TASK_TIMEOUT",
            "details": {"timeout_diagnostics": {**_metadata(), "prompt": "private"}},
        }
    elif kind == "naive":
        record["finished_at"] = NOW.replace(tzinfo=None).isoformat()
    elif kind == "non-timeout":
        record["status"] = "success"
    if kind != "missing":
        path = _save(tmp_path, record)
        if kind == "malformed":
            path.write_text("not JSON", encoding="utf-8")
        elif kind == "oversized":
            path.write_bytes(b" " * (MAX_RECORD_BYTES + 1))
    result = read_timeout_evidence(tmp_path, RUN_ID, URL, MODEL, now=NOW)
    assert result["aider_timeout"] is False
    assert "private" not in json.dumps(result)


def test_evidence_uses_existing_artifact_retention_and_active_lock(tmp_path: Path) -> None:
    path = _save(tmp_path, _record())
    os.utime(path, (1, 1))
    with FileLock(str(path) + ".lock"):
        result = clean_artifacts(tmp_path, older_than_seconds=1)
        assert path.exists()
        assert result.skipped_active
    assert clean_artifacts(tmp_path, older_than_seconds=1, dry_run=True).deleted
    assert path.exists()
    assert clean_artifacts(tmp_path, older_than_seconds=1).deleted
    assert read_timeout_evidence(tmp_path, RUN_ID, URL, MODEL)["status"] == "missing"


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"evidence": None},
        {"evidence": {"status": "expired"}},
        {"loaded": False},
        {"loaded": "true"},
        {"model_available": False},
        {"managed_activity": "busy"},
        {"managed_activity": "unknown"},
        {"readiness_tested": False},
        {"error_code": None},
        {"error_code": "READINESS_HTTP_ERROR"},
    ],
)
def test_suspicion_requires_distinct_matching_task_and_probe_timeouts(
    change: dict[str, object],
) -> None:
    args = {
        "evidence": {"status": "available", "aider_timeout": True},
        "loaded": True,
        "model_available": True,
        "managed_activity": "idle",
        "readiness_tested": True,
        "error_code": "READINESS_TIMEOUT",
    }
    args.update(change)
    assert suspected_stall(**args) == (not change)  # type: ignore[arg-type]
