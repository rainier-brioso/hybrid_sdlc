"""User-facing lifecycle commands, without touching Docker or real user state."""

from __future__ import annotations

import json
import multiprocessing
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from hybrid_sdlc import runtime_strata, server_probe
from hybrid_sdlc.cli import cli
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.models import RunResult, RunStatus

_HTTPX_CLIENT = httpx.Client
_BOUNDED_HEALTH = server_probe._bounded_health_probe
_BOUNDED_MODELS = server_probe._bounded_model_probe
_BOUNDED_READINESS = server_probe._bounded_readiness_probe


@pytest.mark.parametrize("mode", ["default", "timeout", "ready", "busy", "malformed"])
def test_explicit_timeout_evidence_is_advisory_not_automatic_recovery(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from hybrid_sdlc.artifacts import atomic_save_json
    from hybrid_sdlc.processes import SubprocessResult
    from hybrid_sdlc.timeout_diagnostics import timeout_metadata

    runtime_strata.configure()
    run_id = "run_100_ab12cd34"
    repo = tmp_path / "repo"
    repo.mkdir()
    metadata = timeout_metadata(
        SubprocessResult(
            exit_code=-1,
            stdout="private",
            stderr="",
            is_truncated=False,
            duration_seconds=150,
            timed_out=True,
            cancelled=False,
        ),
        "http://localhost:8080/v1",
        "qwen3.8-flash-next-coder-iq1_m",
    )
    atomic_save_json(
        repo / ".hybrid_sdlc" / "runs" / f"{run_id}.json",
        {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "status": "failure",
            "finished_at": datetime.now(UTC).isoformat(),
            "failure": {"code": "TASK_TIMEOUT", "details": {"timeout_diagnostics": metadata}},
        },
        repo,
    )
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/health":
            return httpx.Response(200, json={"loaded": True})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        if mode == "timeout":
            raise httpx.ReadTimeout("private-provider-body")
        if mode == "malformed":
            return httpx.Response(200, json={"choices": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: _HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )

    def no_lifecycle(*args: object, **kwargs: object) -> None:
        raise AssertionError("Diagnostics invoked Docker/recovery")

    monkeypatch.setattr(runtime_strata, "_run_lifecycle", no_lifecycle)
    monkeypatch.setattr(runtime_strata, "_docker", no_lifecycle)
    options = [
        "runtime",
        "strata",
        "diagnose",
        "--repo-root",
        str(repo),
        "--run-id",
        run_id,
        "--json",
    ]
    if mode != "default":
        options.append("--readiness")
    if mode == "busy":
        with runtime_strata.sync_task_lease("http://localhost:8080/v1"):
            result = CliRunner().invoke(cli, options)
    else:
        result = CliRunner().invoke(cli, options)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["timeout_evidence"]["status"] == "available"
    assert (
        report["status"]
        == {
            "default": "available-unverified",
            "timeout": "suspected-stalled",
            "ready": "ready",
            "busy": "busy",
            "malformed": "unknown",
        }[mode]
    )
    assert "private" not in result.output
    assert methods.count("POST") == (0 if mode in {"default", "busy"} else 1)


def test_timeout_evidence_requires_both_cli_options(runtime_root: Path, tmp_path: Path) -> None:
    runtime_strata.configure()
    result = CliRunner().invoke(
        cli, ["runtime", "strata", "diagnose", "--repo-root", str(tmp_path), "--json"]
    )
    assert result.exit_code != 0
    assert "both repository root and run ID" in result.output


def _hold_child_lease(root: str, ready: Any, release: Any) -> None:
    """Spawn-safe child using test state, never the real user's registry."""
    with patch.object(runtime_strata, "_state_root", return_value=Path(root)):
        with runtime_strata.sync_task_lease("http://localhost:8080/v1"):
            ready.set()
            if not release.wait(30):
                raise RuntimeError("Parent did not release the test lease")


@pytest.fixture
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".hybrid-sdlc" / "runtime" / "strata"
    monkeypatch.setattr(runtime_strata, "_state_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def inline_readiness_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def health(url: str, timeout: float) -> dict[str, Any]:
        health_url = url.rstrip("/")[:-3] + "/health"
        try:
            with httpx.Client(
                timeout=timeout, headers={"Authorization": "Bearer local-no-key"}
            ) as client:
                response = client.get(health_url)
            if response.status_code != 200:
                return {
                    "available": False,
                    "loaded": None,
                    "error_code": f"HEALTH_HTTP_{response.status_code}",
                }
            payload = response.json()
            return {
                "available": isinstance(payload, dict),
                "loaded": payload.get("loaded")
                if isinstance(payload, dict) and isinstance(payload.get("loaded"), bool)
                else None,
                "error_code": None if isinstance(payload, dict) else "HEALTH_UNEXPECTED_SCHEMA",
            }
        except httpx.TimeoutException:
            return {"available": False, "loaded": None, "error_code": "HEALTH_TIMEOUT"}
        except (httpx.HTTPError, ValueError):
            return {"available": False, "loaded": None, "error_code": "HEALTH_UNAVAILABLE"}

    def models(url: str, model: str, timeout: float) -> Any:
        return server_probe._probe_endpoint_direct(
            url, required_model=model, timeout_seconds=timeout
        )

    def direct(url: str, model: str | None, timeout: float, availability: Any) -> Any:
        return server_probe._probe_endpoint_direct(
            url, required_model=model, check_readiness=True, timeout_seconds=timeout
        )

    monkeypatch.setattr(server_probe, "_bounded_health_probe", health)
    monkeypatch.setattr(server_probe, "_bounded_model_probe", models)
    monkeypatch.setattr(server_probe, "_bounded_readiness_probe", direct)


def test_lifecycle_help_is_distinct_from_job_status(runtime_root: Path) -> None:
    result = CliRunner().invoke(cli, ["runtime", "strata", "--help"])
    assert result.exit_code == 0
    for command in ("configure", "start", "stop", "restart", "status", "logs"):
        assert command in result.output
    assert not runtime_root.exists()


def test_diagnose_is_get_only_and_does_not_need_a_repository(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_strata.configure(port=8080)
    monkeypatch.chdir(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "available-unverified"
    assert report["endpoint_available"] is True
    assert report["readiness"] is None
    assert requests == ["GET /health", "GET /v1/models"]


@pytest.mark.parametrize("readiness", [False, True])
def test_diagnose_loading_skips_models_and_readiness(
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    readiness: bool,
) -> None:
    runtime_strata.configure()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        return httpx.Response(200, json={"loaded": False})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    args = ["runtime", "strata", "diagnose", "--json"]
    if readiness:
        args.append("--readiness")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "loading"
    assert report["loaded"] is False
    assert report["endpoint_available"] is True
    assert requests == ["GET /health"]
    if readiness:
        assert report["readiness"]["tested"] is False
        assert report["readiness"]["status"] == "skipped-loading"


def test_diagnose_readiness_success_reports_ready(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--readiness", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "ready"
    assert report["readiness"]["tested"] is True
    assert report["readiness"]["passed"] is True


def test_diagnose_readiness_timeout_is_uncertain_and_redacted(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        raise httpx.ReadTimeout("sensitive response body", request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--readiness", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "unknown"
    assert report["endpoint_available"] is True
    assert report["readiness"]["tested"] is True
    assert report["readiness"]["error_code"] == "READINESS_TIMEOUT"
    assert report["readiness"]["remote_request_cancellation"] == "unknown"
    assert "sensitive response body" not in result.output


@pytest.mark.parametrize("slow_path", ["/health", "/v1/models"])
def test_diagnose_get_wall_clock_bounds_slow_drip(
    runtime_root: Path, slow_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    monkeypatch.setattr(server_probe, "_bounded_health_probe", _BOUNDED_HEALTH)
    monkeypatch.setattr(server_probe, "_bounded_model_probe", _BOUNDED_MODELS)
    monkeypatch.setattr(server_probe, "_bounded_readiness_probe", _BOUNDED_READINESS)

    methods: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            methods.append("GET " + self.path)
            if self.path == slow_path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "200")
                self.end_headers()
                try:
                    for _ in range(200):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if self.path == "/health":
                self.wfile.write(b'{"loaded":true}')
            else:
                self.wfile.write(b'{"data":[{"id":"qwen3.8-flash-next-coder-iq1_m"}]}')

        def do_POST(self) -> None:
            methods.append("POST " + self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    runtime_strata.configure(port=server.server_address[1])
    started = time.monotonic()
    try:
        report = runtime_strata.diagnose(timeout=1)
        elapsed = time.monotonic() - started
        assert report["status"] == "unknown"
        assert elapsed < 4
        assert methods == ["GET /health", "GET /v1/models"]
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.parametrize("active_kind", ["sync", "async", "lifecycle_unknown"])
def test_diagnose_reports_managed_busy_without_readiness_post(
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    active_kind: str,
) -> None:
    runtime_strata.configure()
    lease_context = None
    if active_kind == "sync":
        lease_context = runtime_strata.sync_task_lease("http://127.0.0.1:8080/v1")
    elif active_kind == "async":
        repo = tmp_path / "repo"
        manager = JobManager(repo)
        runtime_strata.reserve_async_task(
            "http://127.0.0.1:8080/v1",
            repo,
            lambda job_id: manager.create("spec.md", "T001", job_id=job_id),
        )
    else:
        runtime_strata._set_lifecycle_uncertain(runtime_root, "stop")

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method + " " + request.url.path)
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    if lease_context is not None:
        lease_context.__enter__()
    try:
        result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--readiness", "--json"])
    finally:
        if lease_context is not None:
            lease_context.__exit__(None, None, None)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "busy"
    assert report["readiness"]["tested"] is False
    assert report["readiness"]["status"] == "busy"
    assert requests == ["GET /health", "GET /v1/models"]


@pytest.mark.parametrize("loaded", ["false", None])
def test_diagnose_untrusted_health_shape_is_unknown(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch, loaded: str | None
) -> None:
    runtime_strata.configure()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={} if loaded is None else {"loaded": loaded})
        return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "unknown"
    assert report["loaded"] is None


def test_diagnose_model_missing_keeps_endpoint_available(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        return httpx.Response(200, json={"data": [{"id": "different-model"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["endpoint_available"] is True
    assert report["model_available"] is False
    assert report["status"] == "unknown"


def test_diagnose_reconciles_terminal_async_activity_before_classifying(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_strata.configure()
    repo = tmp_path / "repo"
    manager = JobManager(repo)
    job = runtime_strata.reserve_async_task(
        "http://127.0.0.1:8080/v1",
        repo,
        lambda job_id: manager.create(
            "spec.md", "T001", job_id=job_id, host_url="http://127.0.0.1:8080/v1"
        ),
    )
    manager.transition(job.job_id, JobStatus.FAILED, failure_reason="test_complete")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"loaded": True})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-flash-next-coder-iq1_m"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    default_result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--json"])
    assert default_result.exit_code == 0, default_result.output
    default_report = json.loads(default_result.output)
    assert default_report["managed_activity"] == "idle"
    assert len(runtime_strata._load_config()[2]["leases"]) == 1

    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--readiness", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "ready"
    assert report["managed_activity"] == "idle"


def test_diagnose_unavailable_and_untrusted_health_are_unknown(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(502, text="secret upstream body")
        raise httpx.ConnectError("secret connection detail", request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "diagnose", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "unavailable"
    assert report["endpoint_available"] is False
    assert report["health_error_code"] == "HEALTH_HTTP_502"
    assert "secret" not in result.output


@pytest.mark.parametrize("timeout", ["0", "31"])
def test_diagnose_rejects_timeout_outside_bounds(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch, timeout: str
) -> None:
    runtime_strata.configure()
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"loaded": True})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(transport=transport, **kwargs)
    )
    result = CliRunner().invoke(
        cli, ["runtime", "strata", "diagnose", "--timeout", timeout, "--json"]
    )
    assert result.exit_code != 0
    assert not called


def test_configure_uses_packaged_assets_not_repository_compose(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "untrusted-compose"
    repo.mkdir()
    (repo / "compose.yaml").write_text("services: {danger: {image: unwanted}}", encoding="utf-8")
    monkeypatch.chdir(repo)

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Configure must not contact Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    result = CliRunner().invoke(cli, ["runtime", "strata", "configure", "--port", "8091", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] is True
    assert payload["endpoint"] == "http://127.0.0.1:8091/v1"
    compose = (runtime_root / "compose.yaml").read_text(encoding="utf-8")
    assert "127.0.0.1:8091:8080" in compose
    assert "unwanted" not in compose
    assert "strata-server" in compose


def test_repeat_configure_preserves_identity(runtime_root: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert first.exit_code == 0, first.output
    original = (runtime_root / "runtime.json").read_bytes()
    again = runner.invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert again.exit_code == 0, again.output
    assert json.loads(again.output)["changed"] is False
    assert (runtime_root / "runtime.json").read_bytes() == original


def test_configure_refuses_changed_identity_with_json_error(runtime_root: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["runtime", "strata", "configure"]).exit_code == 0
    original = (runtime_root / "runtime.json").read_bytes()
    result = runner.invoke(cli, ["runtime", "strata", "configure", "--port", "8091", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert (runtime_root / "runtime.json").read_bytes() == original


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status", "logs"])
def test_unconfigured_actions_return_structured_failure(runtime_root: Path, action: str) -> None:
    result = CliRunner().invoke(cli, ["runtime", "strata", action, "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert not runtime_root.exists()


def test_corrupt_activity_returns_structured_failure(runtime_root: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["runtime", "strata", "configure"]).exit_code == 0
    (runtime_root / "activity.json").write_bytes(b"\xff\xfeinvalid")
    result = runner.invoke(cli, ["runtime", "strata", "status", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"


def test_permission_failure_returns_structured_failure(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_mkdir = Path.mkdir

    def denied(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == runtime_root:
            raise PermissionError("Test denied runtime directory access")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    result = CliRunner().invoke(cli, ["runtime", "strata", "configure", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert not runtime_root.exists()


def test_log_tail_is_forwarded_without_following(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import cli as cli_module

    received: list[int] = []

    def snapshot(*, tail: int) -> dict[str, str]:
        received.append(tail)
        return {"status": "ok", "logs": "bounded snapshot"}

    monkeypatch.setattr(cli_module, "strata_runtime_logs", snapshot)
    result = CliRunner().invoke(cli, ["runtime", "strata", "logs", "--tail", "23", "--json"])
    assert result.exit_code == 0, result.output
    assert received == [23]
    assert json.loads(result.output)["logs"] == "bounded snapshot"
    invalid = CliRunner().invoke(cli, ["runtime", "strata", "logs", "--tail", "10001"])
    assert invalid.exit_code != 0
    assert received == [23]


def test_another_process_activity_blocks_stop(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    child = context.Process(target=_hold_child_lease, args=(str(runtime_root), ready, release))

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Busy refusal must happen before contacting Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    child.start()
    try:
        assert ready.wait(20), "Child failed to reserve the managed endpoint"
        with pytest.raises(runtime_strata.RuntimeManagementError, match="queued or running"):
            runtime_strata.stop()
    finally:
        release.set()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0
    assert runtime_strata._load_config()[2]["leases"] == []


def test_submission_reserves_before_launch_and_releases_after_launch_failure(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import submission

    runtime_strata.configure()
    repo = tmp_path / "task-repo"
    repo.mkdir()
    monkeypatch.setattr(
        submission,
        "preflight_task_request",
        lambda **kwargs: (repo, "spec.md", "http://127.0.0.1:8080/v1", "model", 1),
    )
    monkeypatch.setattr(submission, "_verify_windows_worker_breakaway", lambda: None)
    submitted: list[str] = []

    def fail_launch(*args: object, **kwargs: object) -> Any:
        lease = runtime_strata._load_config()[2]["leases"][0]
        submitted.append(lease["job_id"])
        assert lease["kind"] == "job"
        assert JobManager(repo).get(lease["job_id"]).status is JobStatus.QUEUED
        raise OSError("Simulated worker launch failure")

    monkeypatch.setattr(submission, "_launch_worker", fail_launch)
    with pytest.raises(submission.JobSubmissionError, match="launch failed"):
        submission.submit_job(
            repo_root=repo, spec_path="spec.md", task_id="T001", test_profile="smoke"
        )
    assert len(submitted) == 1
    assert JobManager(repo).get(submitted[0]).status is JobStatus.FAILED
    assert runtime_strata._load_config()[2]["leases"] == []


@pytest.mark.parametrize("execution_fails", [False, True])
def test_worker_terminalization_releases_managed_reservation(
    runtime_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    execution_fails: bool,
) -> None:
    from hybrid_sdlc import worker

    runtime_strata.configure()
    repo = tmp_path / "worker-repo"
    repo.mkdir()
    manager = JobManager(repo)
    job = runtime_strata.reserve_async_task(
        "http://localhost:8080/v1",
        repo,
        lambda job_id: manager.create(
            "spec.md", "T001", job_id=job_id, host_url="http://localhost:8080/v1"
        ),
    )
    monkeypatch.setattr(worker, "verify_repo_root", lambda root: repo)

    def execute(*args: object, **kwargs: object) -> RunResult:
        assert runtime_strata._load_config()[2]["leases"]
        assert manager.get(job.job_id).status is JobStatus.RUNNING
        if execution_fails:
            raise RuntimeError("Simulated execution failure")
        return RunResult(
            run_id="run_lifecycle_hook_test",
            task_id="T001",
            spec_path="spec.md",
            repo_root=str(repo),
            status=RunStatus.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            total_duration_seconds=0.1,
        )

    monkeypatch.setattr(worker, "_execute", execute)
    result = worker.run_worker(job.job_id, repo)
    assert result.status is (JobStatus.FAILED if execution_fails else JobStatus.COMPLETED)
    assert manager.get(job.job_id).status is result.status
    assert runtime_strata._load_config()[2]["leases"] == []


def test_submission_failure_with_corrupt_record_keeps_protection(
    runtime_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_strata.configure()
    repo = tmp_path / "corrupt-job-repo"
    repo.mkdir()
    manager = JobManager(repo)

    def create_then_corrupt(job_id: str) -> Any:
        manager.create("spec.md", "T001", job_id=job_id)
        manager._job_path(job_id).write_text("{broken-json", encoding="utf-8")
        raise RuntimeError("Simulated failure with uncertain persisted state")

    with pytest.raises(RuntimeError, match="uncertain persisted state"):
        runtime_strata.reserve_async_task("http://127.0.0.1:8080/v1", repo, create_then_corrupt)
    leases = runtime_strata._load_config()[2]["leases"]
    assert len(leases) == 1
    assert leases[0]["kind"] == "submitting"

    def forbid_docker(*args: object, **kwargs: object) -> str:
        raise AssertionError("Uncertain job state must block before Docker")

    monkeypatch.setattr(runtime_strata, "_docker", forbid_docker)
    with pytest.raises(runtime_strata.RuntimeManagementError, match="queued or running"):
        runtime_strata.stop()
