"""MCP adapter lifecycle tests."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from hybrid_sdlc import mcp_server


def test_signal_shutdown_cleans_up_before_immediate_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, int | None]] = []
    handlers: dict[signal.Signals, Any] = {}

    class ImmediateExitError(Exception):
        pass

    def register_handler(sig: signal.Signals, handler: Any) -> None:
        handlers[sig] = handler

    def immediate_exit(status: int) -> None:
        events.append(("exit", status))
        raise ImmediateExitError

    async def return_from_stdio() -> None:
        events.append(("stdio_eof", None))

    monkeypatch.setattr(mcp_server.signal, "signal", register_handler)
    monkeypatch.setattr(
        mcp_server, "terminate_active_processes", lambda: events.append(("cleanup", None))
    )
    monkeypatch.setattr(mcp_server.os, "_exit", immediate_exit)
    monkeypatch.setattr(mcp_server.mcp, "run_stdio_async", return_from_stdio)

    mcp_server.main()
    assert events == [("stdio_eof", None), ("cleanup", None)]

    with pytest.raises(ImmediateExitError):
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert events == [
        ("stdio_eof", None),
        ("cleanup", None),
        ("cleanup", None),
        ("exit", 128 + signal.SIGTERM),
    ]
