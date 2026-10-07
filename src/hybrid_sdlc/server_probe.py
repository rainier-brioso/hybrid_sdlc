"""OpenAI-compatible local inference endpoint probing and selection."""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from hybrid_sdlc.artifacts import redact_secrets
from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.errors import ModelNotFoundError, ProcessExecutionError, ServerProbeError
from hybrid_sdlc.models import FailureRecord, ProbeResult
from hybrid_sdlc.processes import SubprocessResult, run_bounded_subprocess
from hybrid_sdlc.security import build_sanitized_environment

DEFAULT_PROBE_TIMEOUT_SECONDS = 3.0
READINESS_PROMPT = "ping"
READINESS_MAX_TOKENS = 5


def _get_models_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return f"{cleaned}/models"
    return f"{cleaned}/v1/models"


def _get_chat_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return f"{cleaned}/chat/completions"
    return f"{cleaned}/v1/chat/completions"


def probe_endpoint(
    url: str,
    required_model: str | None = None,
    check_readiness: bool = False,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Probe endpoint availability and optionally perform readiness inference."""
    if not check_readiness:
        return _probe_endpoint_direct(
            url, required_model=required_model, timeout_seconds=timeout_seconds
        )
    from hybrid_sdlc.runtime_strata import (
        RuntimeManagementError,
        _potential_managed_endpoint,
    )

    display_url = redact_secrets(url)
    state_uncertain = False
    try:
        managed_candidate = _potential_managed_endpoint(url)
    except RuntimeManagementError:
        managed_candidate = None
        state_uncertain = True
    if managed_candidate is None and not state_uncertain:
        return _probe_endpoint_direct(
            url,
            required_model=required_model,
            check_readiness=True,
            timeout_seconds=timeout_seconds,
        )
    availability = _probe_endpoint_direct(
        url, required_model=required_model, timeout_seconds=timeout_seconds
    )
    if not availability.available:
        return availability
    if state_uncertain:
        return ProbeResult(
            url=display_url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            error=FailureRecord(
                code="READINESS_GUARD_ERROR", message="Managed readiness guard failed"
            ),
        )
    return probe_readiness(url, required_model, timeout_seconds, availability)


def probe_readiness(
    url: str, required_model: str | None, timeout_seconds: float, availability: ProbeResult
) -> ProbeResult:
    """Run readiness under managed coordination after endpoint availability is known."""
    from hybrid_sdlc.runtime_strata import RuntimeManagementError, managed_readiness_guard

    display_url = redact_secrets(url)
    try:
        with managed_readiness_guard(url):
            readiness = _bounded_readiness_probe(
                url, required_model or availability.matched_model, timeout_seconds, availability
            )
    except RuntimeManagementError as exc:
        return ProbeResult(
            url=display_url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            error=FailureRecord(
                code=exc.code if exc.code == "READINESS_BUSY" else "READINESS_GUARD_ERROR",
                message=exc.message
                if exc.code == "READINESS_BUSY"
                else "Managed readiness guard failed",
            ),
        )
    return ProbeResult(
        url=display_url,
        available=True,
        models=availability.models,
        matched_model=availability.matched_model,
        readiness_tested=readiness.readiness_tested,
        readiness_passed=readiness.readiness_passed,
        latency_ms=availability.latency_ms,
        error=readiness.error,
    )


def _bounded_readiness_probe(
    url: str,
    required_model: str | None,
    timeout_seconds: float,
    availability: ProbeResult,
) -> ProbeResult:
    """Run managed readiness in a process with a hard wall-clock deadline."""
    started = time.perf_counter()
    child = _run_probe_worker("readiness", url, required_model, timeout_seconds)
    if child is None:
        return ProbeResult(
            url=availability.url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            readiness_tested=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=FailureRecord(
                code="READINESS_WORKER_ERROR", message="Bounded readiness worker failed"
            ),
        )

    readiness_started = any(
        line.strip() == "READINESS_STARTED" for line in child.stdout.splitlines()
    )
    if child.timed_out:
        return ProbeResult(
            url=availability.url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            readiness_tested=readiness_started,
            latency_ms=child.duration_seconds * 1000,
            error=FailureRecord(
                code="READINESS_TIMEOUT",
                message="Readiness wall-clock deadline expired; remote request cancellation is unknown",
            ),
        )
    result_line = next(
        (
            line.removeprefix("RESULT:")
            for line in child.stdout.splitlines()
            if line.startswith("RESULT:")
        ),
        None,
    )
    if child.exit_code != 0 or child.is_truncated or result_line is None:
        return ProbeResult(
            url=availability.url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            readiness_tested=readiness_started,
            latency_ms=child.duration_seconds * 1000,
            error=FailureRecord(
                code="READINESS_WORKER_ERROR", message="Bounded readiness worker failed"
            ),
        )
    try:
        child_result = ProbeResult.model_validate_json(result_line)
    except Exception:
        return ProbeResult(
            url=availability.url,
            available=True,
            models=availability.models,
            matched_model=availability.matched_model,
            readiness_tested=readiness_started,
            latency_ms=child.duration_seconds * 1000,
            error=FailureRecord(
                code="READINESS_WORKER_ERROR",
                message="Bounded readiness worker returned invalid data",
            ),
        )
    return child_result


def _run_probe_worker(
    action: str, url: str, model: str | None, timeout_seconds: float
) -> SubprocessResult | None:
    source_root = Path(__file__).resolve().parents[1]
    payload = json.dumps(
        {"action": action, "url": url, "model": model, "timeout": timeout_seconds}
    ).encode("utf-8")
    try:
        return run_bounded_subprocess(
            [sys.executable, "-m", "hybrid_sdlc.probe_worker"],
            source_root,
            build_sanitized_environment(extra_env={"PYTHONPATH": str(source_root)}),
            timeout_seconds=timeout_seconds,
            buffer_cap_bytes=64 * 1024,
            stdin_data=payload,
        )
    except ProcessExecutionError:
        return None


def _worker_result_line(child: SubprocessResult) -> str | None:
    return next(
        (
            line.removeprefix("RESULT:")
            for line in child.stdout.splitlines()
            if line.startswith("RESULT:")
        ),
        None,
    )


def _bounded_model_probe(url: str, required_model: str, timeout_seconds: float) -> ProbeResult:
    """Read model availability with a process-level wall-clock deadline."""
    display_url = redact_secrets(url)
    child = _run_probe_worker("models", url, required_model, timeout_seconds)
    if child is None:
        code, message = "PROBE_WORKER_ERROR", "Bounded endpoint worker failed"
    elif child.timed_out:
        code, message = "PROBE_TIMEOUT", "Endpoint wall-clock deadline expired"
    else:
        result_line = _worker_result_line(child)
        if child.exit_code == 0 and not child.is_truncated and result_line is not None:
            try:
                return ProbeResult.model_validate_json(result_line)
            except Exception:
                pass
        code, message = "PROBE_WORKER_ERROR", "Bounded endpoint worker returned invalid data"
    return ProbeResult(
        url=display_url, available=False, error=FailureRecord(code=code, message=message)
    )


def _bounded_health_probe(url: str, timeout_seconds: float) -> dict[str, Any]:
    """Read only health metadata with a process-level wall-clock deadline."""
    child = _run_probe_worker("health", url, None, timeout_seconds)
    if child is None:
        return {"available": False, "loaded": None, "error_code": "HEALTH_WORKER_ERROR"}
    if child.timed_out:
        return {"available": False, "loaded": None, "error_code": "HEALTH_TIMEOUT"}
    result_line = _worker_result_line(child)
    if child.exit_code != 0 or child.is_truncated or result_line is None:
        return {"available": False, "loaded": None, "error_code": "HEALTH_WORKER_ERROR"}
    try:
        result = json.loads(result_line)
    except (TypeError, ValueError):
        return {"available": False, "loaded": None, "error_code": "HEALTH_WORKER_ERROR"}
    if not isinstance(result, dict):
        return {"available": False, "loaded": None, "error_code": "HEALTH_WORKER_ERROR"}
    return result


def _probe_endpoint_direct(
    url: str,
    required_model: str | None = None,
    check_readiness: bool = False,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    readiness_started: Callable[[], None] | None = None,
) -> ProbeResult:
    """Probe an OpenAI-compatible /v1/models endpoint and test readiness if requested."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        return ProbeResult(
            url=redact_secrets(url),
            available=False,
            error=FailureRecord(
                code="INVALID_TIMEOUT", message="Probe timeout must be finite and positive"
            ),
        )
    display_url = redact_secrets(url)
    models_url = _get_models_url(url)
    start_time = time.perf_counter()

    headers = {"Authorization": "Bearer local-no-key"}

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            resp = client.get(models_url)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            if resp.status_code != 200:
                return ProbeResult(
                    url=display_url,
                    available=False,
                    latency_ms=elapsed_ms,
                    error=FailureRecord(
                        code="HTTP_ERROR",
                        message=f"Endpoint returned HTTP {resp.status_code}",
                        details={"status_code": resp.status_code},
                    ),
                )

            try:
                data = resp.json()
            except Exception:
                return ProbeResult(
                    url=display_url,
                    available=False,
                    latency_ms=elapsed_ms,
                    error=FailureRecord(
                        code="MALFORMED_JSON",
                        message="Endpoint returned malformed JSON",
                    ),
                )

            # Extract model list from standard {"data": [{"id": "..."}, ...]} or {"models": [...]}
            model_ids: list[str] = []
            recognized_schema = False
            if isinstance(data, dict):
                if "data" in data and isinstance(data["data"], list):
                    recognized_schema = True
                    for item in data["data"]:
                        if isinstance(item, dict) and "id" in item:
                            model_ids.append(str(item["id"]))
                elif "models" in data and isinstance(data["models"], list):
                    recognized_schema = True
                    for item in data["models"]:
                        if isinstance(item, str):
                            model_ids.append(item)
                        elif isinstance(item, dict) and "id" in item:
                            model_ids.append(str(item["id"]))

            if not recognized_schema:
                return ProbeResult(
                    url=display_url,
                    available=False,
                    latency_ms=elapsed_ms,
                    error=FailureRecord(
                        code="UNEXPECTED_SCHEMA",
                        message="Endpoint response did not contain a recognized model-list schema",
                    ),
                )

            if not model_ids:
                return ProbeResult(
                    url=display_url,
                    available=False,
                    latency_ms=elapsed_ms,
                    error=FailureRecord(
                        code="EMPTY_MODELS",
                        message="Endpoint returned an empty model list",
                    ),
                )

            # Check model match if required
            matched_model: str | None = None
            if required_model:
                req_lower = required_model.lower()
                for m_id in model_ids:
                    if m_id.lower() == req_lower or req_lower in m_id.lower():
                        matched_model = m_id
                        break
                if not matched_model:
                    return ProbeResult(
                        url=display_url,
                        available=False,
                        models=model_ids,
                        latency_ms=elapsed_ms,
                        error=FailureRecord(
                            code="MODEL_NOT_FOUND",
                            message=f"Required model '{required_model}' not found in available models",
                            details={"available_models": model_ids, "requested": required_model},
                        ),
                    )
            else:
                matched_model = model_ids[0]

            # Optional inference readiness probe
            readiness_passed = False
            if check_readiness:
                chat_url = _get_chat_url(url)
                payload = {
                    "model": matched_model,
                    "messages": [{"role": "user", "content": READINESS_PROMPT}],
                    "max_tokens": READINESS_MAX_TOKENS,
                    "temperature": 0.0,
                }
                try:
                    if readiness_started is not None:
                        readiness_started()
                    chat_resp = client.post(chat_url, json=payload, timeout=timeout_seconds)
                    if chat_resp.status_code == 200:
                        chat_data = chat_resp.json()
                        choices = chat_data.get("choices") if isinstance(chat_data, dict) else None
                        if (
                            isinstance(choices, list)
                            and choices
                            and isinstance(choices[0], dict)
                            and isinstance(choices[0].get("message"), dict)
                            and isinstance(choices[0]["message"].get("content"), str)
                            and bool(choices[0]["message"]["content"].strip())
                        ):
                            readiness_passed = True
                        else:
                            return ProbeResult(
                                url=display_url,
                                available=True,
                                models=model_ids,
                                matched_model=matched_model,
                                readiness_tested=True,
                                readiness_passed=False,
                                latency_ms=elapsed_ms,
                                error=FailureRecord(
                                    code="READINESS_EMPTY_CHOICES",
                                    message="Inference readiness check returned no choices",
                                ),
                            )
                    else:
                        return ProbeResult(
                            url=display_url,
                            available=True,
                            models=model_ids,
                            matched_model=matched_model,
                            readiness_tested=True,
                            readiness_passed=False,
                            latency_ms=elapsed_ms,
                            error=FailureRecord(
                                code="READINESS_HTTP_ERROR",
                                message=f"Readiness check failed with HTTP {chat_resp.status_code}",
                                details={"status_code": chat_resp.status_code},
                            ),
                        )
                except Exception as e:
                    return ProbeResult(
                        url=display_url,
                        available=True,
                        models=model_ids,
                        matched_model=matched_model,
                        readiness_tested=True,
                        readiness_passed=False,
                        latency_ms=elapsed_ms,
                        error=FailureRecord(
                            code="READINESS_TIMEOUT"
                            if isinstance(e, httpx.TimeoutException)
                            else "READINESS_PROBE_ERROR",
                            message="Inference readiness probe timed out; remote request cancellation is unknown"
                            if isinstance(e, httpx.TimeoutException)
                            else "Inference readiness probe failed",
                        ),
                    )

            return ProbeResult(
                url=display_url,
                available=True,
                models=model_ids,
                matched_model=matched_model,
                readiness_tested=check_readiness,
                readiness_passed=readiness_passed if check_readiness else False,
                latency_ms=elapsed_ms,
            )

    except httpx.ConnectTimeout:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="CONNECT_TIMEOUT", message="Connection timed out"),
        )
    except httpx.ReadTimeout:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="READ_TIMEOUT", message="Read timed out"),
        )
    except httpx.ConnectError:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="CONNECT_ERROR", message="Failed to connect"),
        )
    except Exception:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="PROBE_ERROR", message="Endpoint probe failed unexpectedly"),
        )


def select_active_endpoint(
    candidates: list[ServerCandidateConfig],
    explicit_url: str | None = None,
    required_model: str | None = None,
    check_readiness: bool = False,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> tuple[ServerCandidateConfig, ProbeResult]:
    """Evaluate candidate endpoints and return the first verified active endpoint.

    Raises ServerProbeError or ModelNotFoundError summarizing all attempts if none succeed.
    """
    to_probe: list[ServerCandidateConfig] = []
    if explicit_url:
        to_probe.append(ServerCandidateConfig(url=explicit_url, model_alias=required_model))
    else:
        to_probe.extend(candidates)

    attempts_summary: list[dict[str, Any]] = []

    for candidate in to_probe:
        model_to_check = candidate.model_alias or required_model
        probe_res = probe_endpoint(
            url=candidate.url,
            required_model=model_to_check,
            check_readiness=check_readiness,
            timeout_seconds=timeout_seconds,
        )

        if probe_res.available and (not check_readiness or probe_res.readiness_passed):
            return candidate, probe_res

        summary = {
            "url": redact_secrets(candidate.url),
            "code": probe_res.error.code if probe_res.error else "UNKNOWN",
            "message": probe_res.error.message if probe_res.error else "Unavailable",
        }
        attempts_summary.append(summary)

    # If no candidate succeeded
    # Check if any failure was due to model not found
    is_model_missing = any(a.get("code") == "MODEL_NOT_FOUND" for a in attempts_summary)
    err_cls = ModelNotFoundError if is_model_missing else ServerProbeError

    msg = f"No active inference endpoint available among {len(to_probe)} candidate(s)"
    raise err_cls(
        msg,
        details={"attempts": attempts_summary, "requested_model": required_model},
    )
