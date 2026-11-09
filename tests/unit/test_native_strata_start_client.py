"""The runtime-start client must not own the daemon's process lifetime."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from hybrid_sdlc.runtime_strata_native import _start_client


@pytest.mark.parametrize("timeout_client", [False, True])
def test_native_start_client_keeps_owned_daemon_for_status_reconciliation(
    tmp_path: Path,
    timeout_client: bool,
) -> None:
    heartbeat = tmp_path / "heartbeat"
    pid_file = tmp_path / "daemon.pid"
    daemon = "import pathlib,sys,time; time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text('alive'); time.sleep(20)"
    script = tmp_path / "client.py"
    script.write_text(
        "import pathlib,subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{daemon!r},{str(heartbeat)!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid))\n"
        "print('{\"ok\":true}',flush=True)\n" + ("time.sleep(20)\n" if timeout_client else ""),
        encoding="utf-8",
    )
    try:
        before = time.monotonic()
        result = _start_client(
            [sys.executable, str(script)], tmp_path, dict(os.environ), 1 if timeout_client else 5
        )
        assert result.timed_out is timeout_client
        assert json.loads(result.stdout)["ok"] is True
        assert time.monotonic() - before < 7
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeat.read_text() == "alive", "Daemon must survive client exit or timeout"
    finally:
        if pid_file.exists():
            # This is the PID emitted by this test's freshly spawned daemon.
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
