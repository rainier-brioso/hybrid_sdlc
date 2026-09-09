"""OpenAI-compatible local inference endpoint probing and selection."""

from __future__ import annotations

import time
from typing import Any

import httpx

from hybrid_sdlc.artifacts import redact_secrets
from hybrid_sdlc.config import ServerCandidateConfig
from hybrid_sdlc.errors import ModelNotFoundError, ServerProbeError
from hybrid_sdlc.models import FailureRecord, ProbeResult

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
    """Probe an OpenAI-compatible /v1/models endpoint and test readiness if requested."""
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
            except Exception as e:
                return ProbeResult(
                    url=display_url,
                    available=False,
                    latency_ms=elapsed_ms,
                    error=FailureRecord(
                        code="MALFORMED_JSON",
                        message=f"Endpoint returned malformed JSON: {e}",
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
                    chat_resp = client.post(chat_url, json=payload, timeout=timeout_seconds)
                    if chat_resp.status_code == 200:
                        chat_data = chat_resp.json()
                        if "choices" in chat_data and len(chat_data["choices"]) > 0:
                            readiness_passed = True
                        else:
                            return ProbeResult(
                                url=display_url,
                                available=False,
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
                            available=False,
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
                        available=False,
                        models=model_ids,
                        matched_model=matched_model,
                        readiness_tested=True,
                        readiness_passed=False,
                        latency_ms=elapsed_ms,
                        error=FailureRecord(
                            code="READINESS_PROBE_ERROR",
                            message=f"Inference readiness probe failed: {e}",
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
    except httpx.ConnectError as e:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="CONNECT_ERROR", message=f"Failed to connect: {e}"),
        )
    except Exception as e:
        return ProbeResult(
            url=display_url,
            available=False,
            error=FailureRecord(code="PROBE_ERROR", message=f"Probe unexpected error: {e}"),
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

        if probe_res.available:
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
