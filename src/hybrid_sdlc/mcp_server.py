"""MCP tools for local model checks and policy-checked spec task execution."""

from __future__ import annotations

import asyncio
import math
import re
import signal
from typing import Annotated, Any, NoReturn, cast
from urllib.parse import urlsplit, urlunsplit

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from hybrid_sdlc import __version__
from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.artifacts import redact_data, redact_secrets
from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import load_config
from hybrid_sdlc.errors import HybridSDLCError
from hybrid_sdlc.job_manager import JobManager
from hybrid_sdlc.processes import terminate_active_processes
from hybrid_sdlc.security import verify_repo_root
from hybrid_sdlc.server_probe import select_active_endpoint
from hybrid_sdlc.submission import JobSubmissionError, preflight_task_request, submit_job

mcp = MCPServer("hybrid-sdlc", version=__version__)
_StructuredResponse = dict[str, Any]
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"


def _sanitize_url(url: str) -> str:
    """Remove URL userinfo, query, and fragment while preserving its host and path."""

    parsed = urlsplit(url)
    netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _sanitize_urls(value: Any) -> Any:
    """Sanitize URLs recursively, including those embedded in nested text values."""

    if isinstance(value, dict):
        return {key: _sanitize_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_urls(item) for item in value]
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            raw_url = match.group(0)
            url = raw_url.rstrip(_URL_TRAILING_PUNCTUATION)
            trailing = raw_url[len(url) :]
            try:
                return _sanitize_url(url) + trailing
            except ValueError:
                return "[URL REDACTED]" + trailing

        return _URL_PATTERN.sub(replace, value)
    return value


def _safe_response(data: _StructuredResponse) -> _StructuredResponse:
    """Redact secrets recursively before returning any structured MCP result."""

    redacted = redact_data(data)
    return cast(_StructuredResponse, _sanitize_urls(redacted))


def _safe_message(message: str) -> str:
    """Apply the same URL boundary to error text as successful tool output."""

    return cast(str, _sanitize_urls(redact_secrets(message)))


def _tool_error(error: Exception) -> ToolError:
    """Convert expected failures into concise, non-traceback tool errors."""

    if isinstance(error, JobSubmissionError):
        message = _safe_message(str(error))
        return ToolError(f"JOB_SUBMISSION_FAILED: {message} (job_id={error.job_id})")
    if isinstance(error, HybridSDLCError):
        message = _safe_message(error.message)
        return ToolError(f"{error.code}: {message}")
    if isinstance(error, FileNotFoundError):
        return ToolError("JOB_NOT_FOUND: no job record exists for that ID.")
    if isinstance(error, ValueError):
        return ToolError("INVALID_ARGUMENT: one or more request values are invalid.")
    if isinstance(error, OSError):
        return ToolError("OPERATION_FAILED: the requested operation could not be completed.")
    return ToolError("INTERNAL_ERROR: the requested operation could not be completed.")


def _raise_tool_error(error: Exception) -> NoReturn:
    raise _tool_error(error) from None


@mcp.tool()
def check_local_model(repo_root: str) -> dict[str, Any]:
    """Probe the configured local inference endpoint and selected model."""

    try:
        root = verify_repo_root(repo_root)
        config = load_config(repo_root=root)
        _, probe = select_active_endpoint(
            candidates=config.server_candidates,
            required_model=config.selected_model,
        )
        return _safe_response(probe.model_dump(mode="json"))
    except Exception as exc:
        _raise_tool_error(exc)


@mcp.tool()
async def run_spec_task_sync(
    repo_root: str,
    spec_path: str,
    task_id: str,
    test_profile: str,
    ctx: Context,
) -> dict[str, Any]:
    """Run one spec task through the bounded edit and test loop, returning RunResult."""

    try:
        root, relative_spec, endpoint_url, model, retries = preflight_task_request(
            repo_root=repo_root,
            spec_path=spec_path,
            task_id=task_id,
            test_profile=test_profile,
            host_url=None,
            model=None,
            max_retries=None,
        )
        config = load_config(repo_root=root)
        profile = config.command_profiles[test_profile]
        executable = resolve_profile_executable(profile, root)
        await ctx.report_progress(0, total=1, message="Bounded task run started")
        result = await asyncio.to_thread(
            run_bounded_loop,
            repo_root=root,
            spec_path=relative_spec,
            task_id=task_id,
            profile=profile,
            endpoint_url=endpoint_url,
            model_name=model,
            max_retries=retries,
            task_timeout_seconds=float(config.task_timeout_seconds),
            attempt_timeout_seconds=float(config.attempt_timeout_seconds),
            buffer_cap_bytes=config.log_buffer_cap_bytes,
            resolved_test_executable=executable,
        )
        await ctx.report_progress(1, total=1, message="Bounded task run finished")
        return _safe_response(result.model_dump(mode="json"))
    except Exception as exc:
        _raise_tool_error(exc)


@mcp.tool()
def submit_spec_job(
    repo_root: str,
    spec_path: str,
    task_id: str,
    test_profile: str,
) -> dict[str, Any]:
    """Validate and submit one asynchronous spec task, returning its persisted job."""

    try:
        record = submit_job(
            repo_root=repo_root,
            spec_path=spec_path,
            task_id=task_id,
            test_profile=test_profile,
        )
        return _safe_response(record.model_dump(mode="json"))
    except Exception as exc:
        _raise_tool_error(exc)


@mcp.tool()
def get_job_status(
    repo_root: str,
    job_id: str,
    wait_timeout_seconds: Annotated[float, Field(ge=0, le=60)] = 0,
    poll_interval_seconds: Annotated[float, Field(ge=0.01, le=5)] = 0.25,
) -> dict[str, Any]:
    """Get one persisted job record, optionally waiting up to 60 seconds."""

    try:
        if not math.isfinite(wait_timeout_seconds) or not math.isfinite(poll_interval_seconds):
            raise ValueError("wait and polling values must be finite")
        root = verify_repo_root(repo_root)
        record = JobManager(root).status(
            job_id,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        return _safe_response(record.model_dump(mode="json"))
    except Exception as exc:
        _raise_tool_error(exc)


@mcp.tool()
def cancel_spec_job(repo_root: str, job_id: str) -> dict[str, Any]:
    """Request cancellation for a queued or running job."""

    try:
        root = verify_repo_root(repo_root)
        record = JobManager(root).cancel(job_id)
        if record.status.value == "running":
            cancellation = "requested"
        elif record.status.value == "cancelled":
            cancellation = "cancelled"
        else:
            cancellation = "already_terminal"
        return _safe_response({"job": record.model_dump(mode="json"), "cancellation": cancellation})
    except Exception as exc:
        _raise_tool_error(exc)


def main() -> None:
    """Run this adapter over stdio; protocol output is written only by the SDK."""

    def shutdown_handler(signum: int, frame: Any) -> NoReturn:
        del frame
        terminate_active_processes()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    mcp.run(transport="stdio")
