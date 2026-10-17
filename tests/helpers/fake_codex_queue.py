"""Fake Codex CLI executable for queue bridge tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "queue":
        return 64
    capture = os.environ.get("FAKE_CODEX_CAPTURE")
    if capture:
        message = sys.argv[5] if len(sys.argv) > 5 else ""
        try:
            job_id = json.loads(message.split("Metadata: ", 1)[1])["job_id"]
            receipt_path = Path.cwd() / ".hybrid_sdlc" / "bridges" / "codex" / f"{job_id}.json"
            intent_state = json.loads(receipt_path.read_text(encoding="utf-8")).get("state")
        except (IndexError, KeyError, OSError, ValueError):
            intent_state = "missing_or_invalid"
        Path(capture).write_text(
            json.dumps({"argv": sys.argv[1:], "intent_state": intent_state}), encoding="utf-8"
        )
    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "success")
    if scenario == "timeout":
        time.sleep(5)
    elif scenario == "nonzero":
        sys.stderr.write("private fake failure details\n")
        return 17
    else:
        print("Queued message FAKE_PRIVATE_STDOUT for thread fake-thread.")
        sys.stderr.write("FAKE_PRIVATE_STDERR\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
