"""Measure Strata prompt processing with a deterministic, synthetic code prompt.

Run from the repository root with Python 3.11+: ``python benchmarks/strata_prompt_probe.py``.
The script only contacts the fixed loopback Strata server at 127.0.0.1:8080,
checks readiness/settings, then sends three identical bounded streaming requests.
It does not read repository content, write files, retry, or change server configuration.
Inference updates the ordinary prompt/expert caches and request metrics.
Socket timeouts bound inactivity; deadline checks run between received lines,
so these are diagnostic limits, not a process-enforced hard wall-clock cutoff.
The first request is a new prompt (not a cold model start); later KV/prompt
reuse is unknown unless Strata's metrics explicitly reports it. This probe does
not compare Docker with a native runtime or establish runtime parity.

Output is JSON Lines: one flushed result per request and one final summary.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:8080"
MODEL = "qwen3.8-flash-next-coder-iq1_m"
REPEATS = 3
REQUEST_TIMEOUT_SECONDS = 120
OVERALL_TIMEOUT_SECONDS = 400
MAX_TOKENS = 64

# These fields are enough to associate a new monitor row with a request while
# excluding arbitrary older monitor history and any potentially large fields.
METRIC_FIELDS = (
    "time",
    "duration_s",
    "prompt_tokens",
    "reused",
    "output_tokens",
    "prompt_read",
    "prompt_ms",
    "decode_ms",
    "decode_tok_s",
    "hit_rate",
    "file_mb",
)
METADATA_FIELDS = ("hardware", "engine", "conversation_cache")


def make_prompt() -> str:
    """Build a fixed synthetic Python context, never sourced from a repo."""
    sections = [
        "Synthetic code-review context for a timing probe. No repository files are included.",
        "Read the code below and reply with exactly the single word ACK.",
        "```python",
        "from dataclasses import dataclass",
        "from typing import Iterable",
        "",
        "@dataclass(frozen=True)",
        "class SampleRecord:",
        "    key: str",
        "    value: int",
        "    active: bool = True",
        "",
        "def normalize_records(records: Iterable[SampleRecord]) -> list[SampleRecord]:",
        '    """Return active records sorted by normalized key and value."""',
        "    selected = [item for item in records if item.active and item.key.strip()]",
        "    return sorted(selected, key=lambda item: (item.key.strip().lower(), item.value))",
        "",
    ]
    # Twelve small, distinct functions make a stable ~1.2–2K-token code
    # context on common code tokenizers without loading any user project files.
    for index in range(12):
        sections.extend(
            (
                f"def summarize_group_{index:02d}(records: list[SampleRecord]) -> dict[str, int]:",
                f'    """Summarize valid values for synthetic group {index:02d}."""',
                "    totals: dict[str, int] = {}",
                "    for record in records:",
                "        key = record.key.strip().lower()",
                "        if record.active and key:",
                "            totals[key] = totals.get(key, 0) + record.value",
                "    return dict(sorted(totals.items()))",
                "",
            )
        )
    sections.extend(("```", ""))
    return "\n".join(sections)


def request_json(path: str, *, timeout: float = 8.0) -> Any:
    request = Request(f"{BASE_URL}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def filtered_metrics() -> dict[str, Any]:
    """Return a bounded snapshot of current monitor rows, if available."""
    try:
        payload = request_json("/metrics")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:240]}
    rows = payload.get("requests") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {"available": True, "rows_available": False}
    return {
        "available": True,
        "rows_available": True,
        "metadata": {key: payload[key] for key in METADATA_FIELDS if key in payload},
        "requests": [
            {key: row[key] for key in METRIC_FIELDS if key in row}
            for row in rows
            if isinstance(row, dict)
        ],
    }


def row_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if not before.get("rows_available") or not after.get("rows_available"):
        return {"status": "unavailable"}
    before_rows = before["requests"]
    after_rows = after["requests"]
    old = Counter(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in before_rows)
    added = []
    for row in after_rows:
        key = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if old[key]:
            old[key] -= 1
        else:
            added.append(row)
    if len(added) == 1:
        return {"status": "matched_new_row", "request": added[0]}
    return {"status": "ambiguous", "new_rows": added[:3], "new_row_count": len(added)}


def emit(record: dict[str, Any]) -> None:
    print(json.dumps(record, separators=(",", ":"), ensure_ascii=False), flush=True)


def stream_one(prompt: str, deadline: float, index: int) -> dict[str, Any]:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning_effort": "none",
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
    }
    request = Request(
        f"{BASE_URL}/v1/chat/completions",
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.monotonic()
    result: dict[str, Any] = {
        "repeat": index,
        "status": "error",
        "started_at": datetime.now(UTC).isoformat(),
        "first_sse_ms": None,
        "first_nonempty_content_ms": None,
        "first_nonempty_reasoning_ms": None,
        "elapsed_ms": None,
        "content": "",
        "reasoning_chars": 0,
        "usage": None,
        "finish_reason": None,
        "unknown_terminal_fields": [],
        "error": None,
    }
    saw_done = False
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Overall probe deadline reached before request")
        timeout = min(REQUEST_TIMEOUT_SECONDS, remaining)
        with urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                now = time.monotonic()
                if now >= deadline or now - started >= REQUEST_TIMEOUT_SECONDS:
                    raise TimeoutError("Probe request deadline reached while streaming")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                if result["first_sse_ms"] is None:
                    result["first_sse_ms"] = round((time.monotonic() - started) * 1000, 1)
                data = line[5:].strip()
                if data == "[DONE]":
                    saw_done = True
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                choices = event.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    choice = choices[0]
                    delta = choice.get("delta")
                    if isinstance(delta, dict):
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            if result["first_nonempty_content_ms"] is None:
                                result["first_nonempty_content_ms"] = round(
                                    (time.monotonic() - started) * 1000, 1
                                )
                            result["content"] += text
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str):
                            if reasoning and result["first_nonempty_reasoning_ms"] is None:
                                result["first_nonempty_reasoning_ms"] = round(
                                    (time.monotonic() - started) * 1000, 1
                                )
                            result["reasoning_chars"] += len(reasoning)
                    if choice.get("finish_reason") is not None:
                        result["finish_reason"] = choice["finish_reason"]
                        result["unknown_terminal_fields"] = sorted(
                            key for key in choice if key not in {"delta", "finish_reason", "index"}
                        )
                usage = event.get("usage")
                if isinstance(usage, dict):
                    result["usage"] = usage
        if not saw_done:
            raise RuntimeError("Streaming response ended without the SSE [DONE] marker")
        if result["finish_reason"] != "stop":
            raise RuntimeError(f"Unexpected completion finish_reason: {result['finish_reason']!r}")
        if result["content"].strip() != "ACK":
            raise RuntimeError("Completion did not contain exactly ACK")
        result["status"] = "ok"
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return result


def main() -> int:
    prompt = make_prompt()
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    base_record = {
        "model": MODEL,
        "prompt_sha256": prompt_sha256,
        "prompt_chars": len(prompt),
        "settings": {
            "reasoning_effort": "none",
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "repeats": REPEATS,
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "overall_timeout_seconds": OVERALL_TIMEOUT_SECONDS,
        },
        "cache_note": "repeat 1 is a new prompt, not a cold model; later reuse is unknown unless reported by metrics",
    }
    try:
        health = request_json("/health")
        settings = request_json("/settings")
        if not isinstance(health, dict) or health.get("loaded") is not True:
            raise RuntimeError("Strata health did not report loaded=true")
        if health.get("model") != MODEL:
            raise RuntimeError(
                f"Loaded model mismatch: expected {MODEL!r}, got {health.get('model')!r}"
            )
        readiness = {
            "loaded": health.get("loaded"),
            "model": health.get("model"),
            "max_context": health.get("max_context"),
            "settings": settings,
        }
        readiness.update({key: health[key] for key in METADATA_FIELDS if key in health})
        base_record["readiness"] = readiness
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        emit({**base_record, "status": "not_ready", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return 2

    deadline = time.monotonic() + OVERALL_TIMEOUT_SECONDS
    results = []
    for index in range(1, REPEATS + 1):
        before = filtered_metrics()
        result = stream_one(prompt, deadline, index)
        after = filtered_metrics()
        result["strata_metrics"] = {
            "before_snapshot": {
                "available": before.get("available"),
                "row_count": len(before.get("requests", [])),
            },
            "after_snapshot": {
                "available": after.get("available"),
                "row_count": len(after.get("requests", [])),
            },
            "metadata": after.get("metadata", {}),
            "request_row": row_delta(before, after),
        }
        results.append(result)
        emit({"type": "request_result", **result})
        if result["status"] != "ok":
            break
    success = len(results) == REPEATS and all(item["status"] == "ok" for item in results)
    emit(
        {
            "type": "probe_summary",
            **base_record,
            "status": "ok" if success else "error",
            "completed_repeats": len(results),
            "results": results,
        }
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
