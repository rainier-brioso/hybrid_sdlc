"""Private subprocess entry point for wall-clock-bounded readiness probes."""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import httpx

from hybrid_sdlc.server_probe import _probe_endpoint_direct


def _mark_readiness_started() -> None:
    print("READINESS_STARTED", flush=True)


def main() -> int:
    try:
        request: Any = json.loads(sys.stdin.buffer.read())
        if not isinstance(request, dict):
            return 2
        url = request.get("url")
        model = request.get("model")
        timeout = request.get("timeout")
        action = request.get("action", "readiness")
        if (
            not isinstance(url, str)
            or (model is not None and not isinstance(model, str))
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or action not in {"health", "models", "readiness"}
        ):
            return 2
        if action == "health":
            health_url = (
                url.rstrip("/")[:-3] + "/health"
                if url.rstrip("/").endswith("/v1")
                else url.rstrip("/") + "/v1/health"
            )
            try:
                with httpx.Client(
                    timeout=float(timeout), headers={"Authorization": "Bearer local-no-key"}
                ) as client:
                    response = client.get(health_url)
                if response.status_code != 200:
                    result = {
                        "available": False,
                        "loaded": None,
                        "error_code": f"HEALTH_HTTP_{response.status_code}",
                    }
                else:
                    payload = response.json()
                    result = {
                        "available": isinstance(payload, dict),
                        "loaded": payload.get("loaded")
                        if isinstance(payload, dict) and isinstance(payload.get("loaded"), bool)
                        else None,
                        "error_code": None
                        if isinstance(payload, dict)
                        else "HEALTH_UNEXPECTED_SCHEMA",
                    }
            except httpx.TimeoutException:
                result = {"available": False, "loaded": None, "error_code": "HEALTH_TIMEOUT"}
            except (httpx.HTTPError, ValueError):
                result = {
                    "available": False,
                    "loaded": None,
                    "error_code": "HEALTH_UNAVAILABLE",
                }
            print("RESULT:" + json.dumps(result), flush=True)
            return 0
        probe_result = _probe_endpoint_direct(
            url,
            required_model=model,
            check_readiness=action == "readiness",
            timeout_seconds=float(timeout),
            readiness_started=_mark_readiness_started if action == "readiness" else None,
        )
        print("RESULT:" + probe_result.model_dump_json(), flush=True)
        return 0
    except Exception:
        # Never serialize an exception, response body, or request data.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
