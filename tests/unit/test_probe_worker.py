"""Private diagnostic worker protocol and privacy regressions."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hybrid_sdlc import probe_worker, server_probe
from hybrid_sdlc.models import ProbeResult
from hybrid_sdlc.processes import SubprocessResult


def _stdin(monkeypatch: pytest.MonkeyPatch, request: Any) -> None:
    monkeypatch.setattr(
        probe_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"url": 1, "timeout": 1},
        {"url": "http://local/v1", "model": [], "timeout": 1},
        {"url": "http://local/v1", "timeout": True},
        {"url": "http://local/v1", "timeout": 0},
        {"url": "http://local/v1", "timeout": -1},
        {"url": "http://local/v1", "timeout": float("nan")},
        {"url": "http://local/v1", "timeout": 1, "action": "invalid"},
    ],
)
def test_worker_rejects_invalid_input_without_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: Any
) -> None:
    _stdin(monkeypatch, payload)
    assert probe_worker.main() == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("action", ["models", "readiness"])
def test_worker_dispatch_and_readiness_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], action: str
) -> None:
    _stdin(monkeypatch, {"action": action, "url": "http://local/v1", "model": "Qwen", "timeout": 1})

    def probe(url: str, **kwargs: Any) -> ProbeResult:
        assert url == "http://local/v1"
        assert kwargs["required_model"] == "Qwen"
        assert kwargs["check_readiness"] == (action == "readiness")
        callback = kwargs["readiness_started"]
        if callback is not None:
            callback()
        return ProbeResult(url=url, available=True)

    monkeypatch.setattr(probe_worker, "_probe_endpoint_direct", probe)
    assert probe_worker.main() == 0
    output = capsys.readouterr().out
    assert ("READINESS_STARTED\n" in output) == (action == "readiness")
    assert "RESULT:" in output


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200, json={"loaded": True, "secret": "private-body"}), (True, True, None)),
        (httpx.Response(200, json={"loaded": "yes"}), (True, None, None)),
        (httpx.Response(200, json=[]), (False, None, "HEALTH_UNEXPECTED_SCHEMA")),
        (httpx.Response(503, text="private-body"), (False, None, "HEALTH_HTTP_503")),
        (httpx.Response(200, text="private-body"), (False, None, "HEALTH_UNAVAILABLE")),
    ],
)
def test_worker_health_serializes_only_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    response: httpx.Response,
    expected: tuple[bool, bool | None, str | None],
) -> None:
    _stdin(monkeypatch, {"action": "health", "url": "http://local/v1", "timeout": 1})
    client_type = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/health"
        return response

    monkeypatch.setattr(
        probe_worker.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert probe_worker.main() == 0
    output = capsys.readouterr().out
    assert "private-body" not in output
    result = json.loads(output.removeprefix("RESULT:"))
    assert (result["available"], result["loaded"], result["error_code"]) == expected


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("private-error"), httpx.ConnectError("private-error")]
)
def test_worker_health_errors_are_static(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: httpx.HTTPError
) -> None:
    _stdin(monkeypatch, {"action": "health", "url": "http://local/v1", "timeout": 1})
    client_type = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    monkeypatch.setattr(
        probe_worker.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert probe_worker.main() == 0
    output = capsys.readouterr().out
    assert "private-error" not in output
    assert json.loads(output.removeprefix("RESULT:"))["error_code"] == (
        "HEALTH_TIMEOUT" if isinstance(error, httpx.TimeoutException) else "HEALTH_UNAVAILABLE"
    )


def test_worker_unexpected_exception_is_not_serialized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stdin(monkeypatch, {"url": "http://local/v1", "timeout": 1})

    def fail(*args: Any, **kwargs: Any) -> ProbeResult:
        raise RuntimeError("private-error")

    monkeypatch.setattr(probe_worker, "_probe_endpoint_direct", fail)
    assert probe_worker.main() == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("action", ["health", "models"])
@pytest.mark.parametrize("outcome", ["timeout", "failed", "invalid", "missing", "unavailable"])
def test_bounded_get_worker_failures_are_static(
    monkeypatch: pytest.MonkeyPatch, action: str, outcome: str
) -> None:
    child = (
        None
        if outcome == "unavailable"
        else SubprocessResult(
            exit_code=1 if outcome == "failed" else 0,
            stdout="RESULT:private-data" if outcome == "invalid" else "private-data",
            stderr="private-error",
            is_truncated=False,
            duration_seconds=1,
            timed_out=outcome == "timeout",
            cancelled=False,
        )
    )
    monkeypatch.setattr(server_probe, "_run_probe_worker", lambda *args: child)
    if action == "health":
        result: Any = server_probe._bounded_health_probe("http://local/v1", 1)
    else:
        result = server_probe._bounded_model_probe("http://local/v1", "Qwen", 1).model_dump()
    assert "private" not in json.dumps(result)
