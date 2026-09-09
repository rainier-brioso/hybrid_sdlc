"""Unit tests for server endpoint probing and selection."""

from __future__ import annotations

import httpx
import pytest

from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.errors import ServerProbeError
from hybrid_sdlc.server_probe import probe_endpoint, select_active_endpoint

_orig_client = httpx.Client


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
