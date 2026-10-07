"""Unit tests for server endpoint probing and selection."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest

from hybrid_sdlc import runtime_strata, server_probe
from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.errors import ServerProbeError
from hybrid_sdlc.models import ProbeResult
from hybrid_sdlc.processes import SubprocessResult
from hybrid_sdlc.server_probe import probe_endpoint, select_active_endpoint

_orig_client = httpx.Client


@pytest.fixture(autouse=True)
def isolate_managed_runtime_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "runtime-state"
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._state_root", lambda: root)


def test_probe_endpoint_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "Qwen/Qwen2.5-Coder-32B-Instruct"}, {"id": "other"}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    result = probe_endpoint("http://127.0.0.1:8090/v1", required_model="Qwen2.5-Coder-32B")
    assert result.available
    assert result.matched_model == "Qwen/Qwen2.5-Coder-32B-Instruct"
    assert result.error is None


def test_probe_endpoint_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen"}]})
        if request.url.path.endswith("/v1/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    result = probe_endpoint("http://127.0.0.1:8090/v1", check_readiness=True)
    assert result.available
    assert result.readiness_tested
    assert result.readiness_passed


def test_readiness_failure_keeps_endpoint_available_and_rejects_malformed_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )
    result = probe_endpoint("http://127.0.0.1:8090/v1", check_readiness=True)
    assert result.available
    assert result.readiness_tested
    assert not result.readiness_passed
    assert result.error is not None
    assert result.error.code == "READINESS_EMPTY_CHOICES"


def test_managed_readiness_refuses_active_task_even_with_userinfo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_strata.configure(port=8090)
    posted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "Qwen"}]})
        posted = True
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )
    with runtime_strata.sync_task_lease("http://127.0.0.1:8090/v1"):
        result = probe_endpoint("http://user:secret@localhost:8090/v1", check_readiness=True)
    assert result.available
    assert not result.readiness_tested
    assert result.error is not None
    assert result.error.code == "READINESS_BUSY"
    assert not posted
    assert "secret" not in result.url


def test_managed_readiness_wall_clock_timeout_kills_slow_drip_and_releases_gate() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"Qwen"}]}')

        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.15)
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_address[1]
    runtime_strata.configure(port=port)
    started = time.monotonic()
    try:
        result = probe_endpoint(
            f"http://127.0.0.1:{port}/v1", check_readiness=True, timeout_seconds=2
        )
        elapsed = time.monotonic() - started
        assert result.available
        assert result.readiness_tested
        assert not result.readiness_passed
        assert result.error is not None
        assert result.error.code == "READINESS_TIMEOUT"
        assert elapsed < 4
        with runtime_strata.sync_task_lease(f"http://127.0.0.1:{port}/v1"):
            pass
        assert runtime_strata._load_config()[2]["leases"] == []
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_readiness_timeout_marker_accepts_windows_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server_probe,
        "run_bounded_subprocess",
        lambda *args, **kwargs: SubprocessResult(
            exit_code=-1,
            stdout="READINESS_STARTED\r\n",
            stderr="",
            is_truncated=False,
            duration_seconds=0.2,
            timed_out=True,
        ),
    )
    available = ProbeResult(url="http://127.0.0.1:8080/v1", available=True, models=["Qwen"])
    result = server_probe._bounded_readiness_probe(available.url, "Qwen", 1.0, available)
    assert result.readiness_tested
    assert result.error is not None
    assert result.error.code == "READINESS_TIMEOUT"


def test_probe_endpoint_model_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "Mistral-7B"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    result = probe_endpoint("http://127.0.0.1:8090/v1", required_model="Qwen")
    assert not result.available
    assert result.error is not None
    assert result.error.code == "MODEL_NOT_FOUND"


def test_probe_endpoint_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    result = probe_endpoint("http://127.0.0.1:8090/v1")
    assert not result.available
    assert result.error is not None
    assert result.error.code == "HTTP_ERROR"


def test_probe_result_redacts_url_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "Qwen"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    result = probe_endpoint("http://alice:super-secret@127.0.0.1:8090/v1")
    assert "super-secret" not in result.url
    assert "[REDACTED]" in result.url


def test_select_active_endpoint_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # 8090 fails (500), 8089 succeeds
    def handler(request: httpx.Request) -> httpx.Response:
        if "8090" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, json={"data": [{"id": "Qwen"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    candidates = [
        ServerCandidateConfig(url="http://127.0.0.1:8090/v1"),
        ServerCandidateConfig(url="http://127.0.0.1:8089/v1"),
    ]

    chosen, probe_res = select_active_endpoint(candidates, required_model="Qwen")
    assert chosen.url == "http://127.0.0.1:8089/v1"
    assert probe_res.available


def test_select_active_endpoint_none_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _orig_client(transport=transport, **kwargs)
    )

    candidates = [ServerCandidateConfig(url="http://127.0.0.1:8090/v1")]
    with pytest.raises(ServerProbeError):
        select_active_endpoint(candidates)
