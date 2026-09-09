"""Unit tests for artifact redaction, atomic persistence, and retention cleanup."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from hybrid_sdlc.artifacts import (
    atomic_save_json,
    clean_artifacts,
    redact_data,
    redact_secrets,
)
from hybrid_sdlc.errors import PathTraversalError


def test_redact_secrets_strings() -> None:
    text = "Authorization: Bearer sk-1234567890abcdef1234567890 with key API_KEY=supersecret123"
    redacted = redact_secrets(text)
    assert "sk-1234567890abcdef1234567890" not in redacted
    assert "supersecret123" not in redacted
    assert "[REDACTED]" in redacted

    url = "https://admin:mysecretpassword@models.example.com/v1"
    redacted_url = redact_secrets(url)
    assert "mysecretpassword" not in redacted_url
    assert "https://admin:[REDACTED]@models.example.com/v1" == redacted_url


def test_redact_data_nested_dict() -> None:
    data = {
        "run_id": "run_001",
        "api_key": "raw_sensitive_key",
        "nested": {
            "token": "ghp_1234567890abcdef1234567890abcdef",
            "safe_value": "hello world",
        },
        "log_messages": [
            "Connecting with key=secret_val_123",
            "All good",
        ],
    }
    cleaned = redact_data(data)
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["nested"]["safe_value"] == "hello world"
    assert "secret_val_123" not in cleaned["log_messages"][0]


def test_atomic_save_json(tmp_path: Path) -> None:
    repo_root = tmp_path
    target_path = repo_root / ".hybrid_sdlc" / "runs" / "run_test.json"
    data = {"task": "TASK-1", "api_key": "secret"}

    saved = atomic_save_json(target_path, data, repo_root=repo_root)
    assert saved.is_file()
    loaded = json.loads(saved.read_text(encoding="utf-8"))
    assert loaded["task"] == "TASK-1"
    assert loaded["api_key"] == "[REDACTED]"


def test_atomic_save_json_rejects_outside_artifacts(tmp_path: Path) -> None:
    repo_root = tmp_path
    outside = repo_root / "src" / "test.json"
    with pytest.raises(PathTraversalError):
        atomic_save_json(outside, {"foo": "bar"}, repo_root=repo_root)


def test_clean_artifacts_retention(tmp_path: Path) -> None:
    repo_root = tmp_path
    artifacts_dir = repo_root / ".hybrid_sdlc" / "runs"
    artifacts_dir.mkdir(parents=True)

    old_file = artifacts_dir / "old_run.json"
    old_file.write_text("{}", encoding="utf-8")
    # Simulate older file by changing mtime
    past_time = time.time() - 1000
    import os

    os.utime(old_file, (past_time, past_time))

    new_file = artifacts_dir / "new_run.json"
    new_file.write_text("{}", encoding="utf-8")

    # Lock file should never be deleted
    lock_file = repo_root / ".hybrid_sdlc" / "runner.lock"
    lock_file.write_text("", encoding="utf-8")
    os.utime(lock_file, (past_time, past_time))

    # Dry run
    dry_deleted = clean_artifacts(repo_root=repo_root, older_than_seconds=500, dry_run=True)
    assert ".hybrid_sdlc/runs/old_run.json" in dry_deleted.deleted
    assert old_file.exists()  # Not deleted in dry-run

    # Real run
    deleted = clean_artifacts(repo_root=repo_root, older_than_seconds=500, dry_run=False)
    assert ".hybrid_sdlc/runs/old_run.json" in deleted.deleted
    assert not old_file.exists()
    assert new_file.exists()
    assert lock_file.exists()


def test_atomic_save_json_no_partial_reads(tmp_path: Path) -> None:
    """Readers must never observe partial JSON during concurrent writes (HSDLC-015).

    Two writer threads repeatedly overwrite the same artifact with distinct
    payloads.  A reader thread continuously reads the file content.  Every read
    must be a complete, parseable JSON document — never a partial write.
    """
    repo_root = tmp_path
    target = repo_root / ".hybrid_sdlc" / "runs" / "concurrent.json"
    errors: list[str] = []
    stop_event = threading.Event()

    def writer(payload: dict) -> None:  # type: ignore[type-arg]
        for _ in range(20):
            if stop_event.is_set():
                break
            atomic_save_json(target, payload, repo_root=repo_root)
            time.sleep(0.005)

    def reader() -> None:
        while not stop_event.is_set():
            if target.exists():
                try:
                    raw = target.read_bytes()
                except PermissionError:
                    continue
                if raw:
                    try:
                        json.loads(raw)
                    except json.JSONDecodeError as exc:
                        errors.append(f"Partial read detected: {exc} | raw={raw[:80]!r}")
            time.sleep(0.001)

    w1 = threading.Thread(target=writer, args=({"writer": 1, "data": "A" * 200},))
    w2 = threading.Thread(target=writer, args=({"writer": 2, "data": "B" * 200},))
    r = threading.Thread(target=reader)

    r.start()
    w1.start()
    w2.start()
    w1.join(timeout=5.0)
    w2.join(timeout=5.0)
    stop_event.set()
    r.join(timeout=2.0)

    assert not errors, "Concurrent write/read produced corrupt reads:\n" + "\n".join(errors)
