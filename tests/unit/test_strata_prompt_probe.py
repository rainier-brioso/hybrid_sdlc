"""Bounded unit coverage for the Strata streaming probe's success criteria."""

from __future__ import annotations

import importlib.util
import io
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROBE_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "strata_prompt_probe.py"
PROBE_SPEC = importlib.util.spec_from_file_location("strata_prompt_probe", PROBE_PATH)
if PROBE_SPEC is None or PROBE_SPEC.loader is None:
    raise ImportError(f"Cannot load Strata probe module from {PROBE_PATH}")
strata_prompt_probe = importlib.util.module_from_spec(PROBE_SPEC)
PROBE_SPEC.loader.exec_module(strata_prompt_probe)


class FakeResponse:
    def __init__(self, payload: str) -> None:
        self._stream = io.BytesIO(payload.encode("utf-8"))

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        self._stream.close()

    def __iter__(self) -> FakeResponse:
        return self

    def __next__(self) -> bytes:
        line = self._stream.readline()
        if not line:
            raise StopIteration
        return line


def sse(*events: dict[str, object], done: bool = True) -> str:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


class StrataPromptProbeTests(unittest.TestCase):
    def run_payload(self, payload: str) -> dict[str, object]:
        with patch.object(strata_prompt_probe, "urlopen", return_value=FakeResponse(payload)):
            return strata_prompt_probe.stream_one("synthetic prompt", time.monotonic() + 10, 1)

    def test_stop_with_ack_is_success(self) -> None:
        result = self.run_payload(
            sse(
                {"choices": [{"delta": {"content": "ACK"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            )
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "ACK")

    def test_missing_done_marker_fails(self) -> None:
        result = self.run_payload(
            sse(
                {"choices": [{"delta": {"content": "ACK"}, "finish_reason": "stop"}]},
                done=False,
            )
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("[DONE]", str(result["error"]))

    def test_length_finish_reason_fails(self) -> None:
        result = self.run_payload(
            sse({"choices": [{"delta": {"content": "ACK"}, "finish_reason": "length"}]})
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("finish_reason", str(result["error"]))

    def test_tool_call_finish_reason_fails(self) -> None:
        result = self.run_payload(
            sse(
                {
                    "choices": [
                        {
                            "delta": {"tool_calls": [{"index": 0}]},
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            )
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("tool_calls", str(result["error"]))


if __name__ == "__main__":
    unittest.main()
