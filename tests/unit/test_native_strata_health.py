"""Native identity checks retain the probe worker's hard wall-clock bound."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from hybrid_sdlc import probe_worker, server_probe
from hybrid_sdlc import runtime_strata_native as native
from hybrid_sdlc.runtime_strata import RuntimeManagementError


def test_native_health_worker_exposes_only_fixed_identity_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.Client
    payload = {
        "status": "ok",
        "service": "strata",
        "loaded": True,
        "model": "qwen3.8-flash-next-coder-iq1_m",
        "max_context": 131072,
        "secret": "never-retain-this",
    }

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)), **kwargs
        )

    monkeypatch.setattr(httpx, "Client", client)
    actual = probe_worker._native_health("http://127.0.0.1:8080/v1", 1)
    assert actual["available"] is True
    assert actual["max_context"] == 131072
    assert "never-retain" not in json.dumps(actual)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://example.com"}),
        httpx.Response(200, content=b" " * 16385),
        httpx.Response(200, json=[]),
        httpx.Response(200, content=b"invalid"),
    ],
)
def test_native_health_refuses_redirect_oversize_and_invalid_body(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(lambda r: response), **kwargs),
    )
    assert probe_worker._native_health("http://127.0.0.1:8080/v1", 1)["available"] is False


@pytest.mark.parametrize(
    "changed",
    [
        {"model": "other"},
        {"max_context": 65536},
        {"max_context": True},
        {"loaded": False},
        {"available": False},
        {"service": "other"},
    ],
)
def test_native_health_identity_must_match_registration(
    monkeypatch: pytest.MonkeyPatch, changed: dict[str, Any]
) -> None:
    observed = {
        "available": True,
        "status": "ok",
        "service": "strata",
        "loaded": True,
        "model": "qwen3.8-flash-next-coder-iq1_m",
        "max_context": 131072,
        **changed,
    }

    def bounded(url: str, timeout: float, *, native: bool) -> dict[str, Any]:
        assert native is True
        assert timeout == 5
        return observed

    monkeypatch.setattr(server_probe, "_bounded_health_probe", bounded)
    with pytest.raises(RuntimeManagementError, match="identity, model, context"):
        native._health(
            {
                "endpoint": "http://127.0.0.1:8080/v1",
                "model": "qwen3.8-flash-next-coder-iq1_m",
                "context_size": 131072,
            }
        )


def test_native_health_slow_drip_has_total_deadline() -> None:
    started_request = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            started_request.set()
            self.send_response(200)
            self.send_header("Content-Length", "200")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                return

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        before = time.monotonic()
        observed = server_probe._bounded_health_probe(
            f"http://127.0.0.1:{server.server_port}/v1", 3, native=True
        )
        assert time.monotonic() - before < 6
        assert started_request.is_set(), "The worker must actually receive the slow response"
        assert observed["available"] is False
        assert observed["error_code"] == "HEALTH_TIMEOUT"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
