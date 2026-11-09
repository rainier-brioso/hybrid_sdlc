"""Native lifecycle uses the same cross-repository gate as Docker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hybrid_sdlc import runtime_strata as runtime
from hybrid_sdlc import runtime_strata_native as native


@pytest.fixture
def registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / ".hybrid-sdlc" / "runtime" / "strata"
    monkeypatch.setattr(runtime, "_state_root", lambda: root)
    config = {
        "backend": "native",
        "port": 8080,
        "endpoint": "http://127.0.0.1:8080/v1",
        "context_size": 131072,
        "preset": "coder-128k",
    }
    monkeypatch.setattr(native, "prepare", lambda **kwargs: dict(config))
    monkeypatch.setattr(native, "validate", lambda config: None)
    return root, config


def register(root: Path) -> dict[str, Any]:
    return runtime.configure_native(
        install_dir=root, components_dir=root, profile_dir=root, preset="coder-128k"
    )


def test_native_registration_is_explicit_and_idempotent(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = registration
    monkeypatch.setattr(
        runtime, "_docker", lambda *a, **k: pytest.fail("native must not use Docker")
    )
    assert register(root)["changed"] is True
    assert register(root)["changed"] is False
    assert not (root / "compose.yaml").exists()
    assert runtime._load_config()[1]["context_size"] == 131072
    with pytest.raises(runtime.RuntimeManagementError, match="Native Strata is selected"):
        runtime.configure()


@pytest.mark.parametrize("action", [runtime.start, runtime.stop, runtime.restart])
def test_native_mutations_are_blocked_by_managed_task(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, action: Any
) -> None:
    root, _ = registration
    register(root)
    monkeypatch.setattr(
        native, "preflight", lambda *a: pytest.fail("active task reached native CLI")
    )
    with runtime.sync_task_lease("http://localhost:8080/v1"):
        with pytest.raises(runtime.RuntimeManagementError, match="queued or running"):
            action()
    assert runtime._load_config()[2]["leases"] == []


def test_native_failed_mutation_keeps_uncertainty(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = registration
    register(root)
    monkeypatch.setattr(native, "preflight", lambda *a: {"status": "running"})

    def fail(*args: Any) -> dict[str, Any]:
        raise runtime.RuntimeManagementError("native command timed out")

    monkeypatch.setattr(native, "mutate", fail)
    with pytest.raises(runtime.RuntimeManagementError, match="timed out"):
        runtime.stop()
    assert runtime._load_config()[2]["leases"][0]["kind"] == "lifecycle_unknown"
    with pytest.raises(runtime.RuntimeManagementError, match="prior Strata lifecycle"):
        with runtime.sync_task_lease("http://127.0.0.1:8080/v1"):
            pytest.fail("uncertain runtime accepted a task")


def test_native_preflight_failure_does_not_mark_mutation(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = registration
    register(root)

    def fail(*args: Any) -> dict[str, Any]:
        raise runtime.RuntimeManagementError("owner mismatch")

    monkeypatch.setattr(native, "preflight", fail)
    with pytest.raises(runtime.RuntimeManagementError, match="owner mismatch"):
        runtime.stop()
    assert runtime._load_config()[2]["leases"] == []


def test_native_success_clears_only_operation_lease(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = registration
    register(root)
    monkeypatch.setattr(native, "preflight", lambda *a: {"status": "exited"})
    monkeypatch.setattr(native, "mutate", lambda *a: {"status": "started", "backend": "native"})
    assert runtime.start()["status"] == "started"
    assert runtime._load_config()[2]["leases"] == []


def test_switch_from_docker_requires_explicit_opt_in_and_stopped_containers(
    registration: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = registration
    runtime.configure(context_size=65536)
    old = runtime._load_config()[1]
    with pytest.raises(runtime.RuntimeManagementError, match="replace-backend"):
        register(root)
    monkeypatch.setattr(runtime, "_local_context", lambda *a: "local")
    monkeypatch.setattr(runtime, "_owned_containers", lambda *a: ["owned"])
    monkeypatch.setattr(runtime, "_docker", lambda *a: "running\n")
    kwargs = {
        "install_dir": root,
        "components_dir": root,
        "profile_dir": root,
        "preset": "coder-128k",
        "replace_backend": True,
    }
    with pytest.raises(runtime.RuntimeManagementError, match="Stop every owned"):
        runtime.configure_native(**kwargs)
    assert runtime._load_config()[1] == old
    monkeypatch.setattr(runtime, "_docker", lambda *a: "exited\n")
    assert runtime.configure_native(**kwargs)["backend"] == "native"
    assert json.loads((root / "docker-runtime.json").read_text()) == old
    assert runtime._load_config()[1]["owner_token"] == old["owner_token"]
    assert (root / "compose.yaml").exists()


def test_native_read_only_status_logs_do_not_call_docker(
    registration: tuple[Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = registration
    register(root)
    monkeypatch.setattr(runtime, "_docker", lambda *a, **k: pytest.fail("Docker called"))
    monkeypatch.setattr(native, "status", lambda *a: {"status": "running", "context_size": 131072})
    monkeypatch.setattr(native, "logs", lambda *a: {"status": "ok", "logs": "bounded"})
    assert runtime.status()["context_size"] == 131072
    assert runtime.logs(tail=10)["logs"] == "bounded"


def test_failed_first_marker_publish_can_retry_empty_registry(
    registration: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = registration
    original = runtime._atomic_json

    def fail_marker(path: Path, value: dict[str, Any]) -> None:
        if path.name == "runtime.json":
            raise runtime.RuntimeManagementError("injected publication failure")
        original(path, value)

    monkeypatch.setattr(runtime, "_atomic_json", fail_marker)
    with pytest.raises(runtime.RuntimeManagementError, match="publication failure"):
        register(root)
    assert not (root / "runtime.json").exists()
    monkeypatch.setattr(runtime, "_atomic_json", original)
    assert register(root)["changed"] is True
