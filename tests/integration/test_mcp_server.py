"""In-process MCP client coverage for schemas and job tool behavior."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from hybrid_sdlc.config import ServerCandidateConfig, ToolkitConfig
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.mcp_server import mcp
from hybrid_sdlc.models import FailureRecord, ProbeResult, RunResult, RunStatus
from hybrid_sdlc.submission import JobSubmissionError

_CREDENTIAL_URL = "http://agent:topSecret123@127.0.0.1/v1"
_USERINFO_URL = "http://secretUser@127.0.0.1/v1"
_QUERY_SECRET_URL = "http://127.0.0.1/v1?access_token=secretValue"


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "spec.md").write_text("# Work\n", encoding="utf-8")
    (path / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["python", "-c", "pass"]\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "spec.md", "hybrid_sdlc.toml"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    async def call() -> Any:
        async with Client(mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


def test_tools_are_discoverable_with_explicit_repository_and_task_schema() -> None:
    async def discover() -> Any:
        async with Client(mcp) as client:
            return await client.list_tools()

    listed = asyncio.run(discover())
    schemas = {tool.name: tool.input_schema for tool in listed.tools}

    assert set(schemas) == {
        "check_local_model",
        "run_spec_task_sync",
        "submit_spec_job",
        "get_job_status",
        "cancel_spec_job",
    }
    for schema in schemas.values():
        assert "repo_root" in schema["required"]
    assert {"spec_path", "task_id", "test_profile"} <= set(
        schemas["run_spec_task_sync"]["required"]
    )
    assert {"spec_path", "task_id", "test_profile"} <= set(schemas["submit_spec_job"]["required"])
    assert "job_id" in schemas["get_job_status"]["required"]
    assert "job_id" in schemas["cancel_spec_job"]["required"]


def test_stdio_entrypoint_completes_sdk_client_handshake() -> None:
    async def connect() -> Any:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hybrid_sdlc", "mcp"],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with Client(parameters) as client:
            return await client.list_tools()

    listed = asyncio.run(connect())
    assert "check_local_model" in {tool.name for tool in listed.tools}


def test_status_and_cancel_return_structured_job_records(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    record = JobManager(tmp_path).create("spec.md", "TASK-1", test_profile="pytest")

    status = _call_tool("get_job_status", {"repo_root": str(tmp_path), "job_id": record.job_id})
    cancelled = _call_tool("cancel_spec_job", {"repo_root": str(tmp_path), "job_id": record.job_id})

    assert status.is_error is False
    assert status.structured_content["status"] == JobStatus.QUEUED.value
    assert cancelled.is_error is False
    assert cancelled.structured_content["cancellation"] == "cancelled"
    assert cancelled.structured_content["job"]["status"] == JobStatus.CANCELLED.value


def test_expected_failures_are_tool_errors_without_tracebacks(tmp_path: Path) -> None:
    result = _call_tool("check_local_model", {"repo_root": str(tmp_path / "missing")})

    assert result.is_error is True
    text = result.content[0].text
    assert "REPO_ROOT_NOT_FOUND" in text
    assert "Traceback" not in text


def test_successful_tool_responses_redact_credentials_recursively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    record = JobManager(tmp_path).create(
        "spec.md", "TASK-1", test_profile="pytest", host_url=_QUERY_SECRET_URL
    )
    config = ToolkitConfig()
    monkeypatch.setattr("hybrid_sdlc.mcp_server.verify_repo_root", lambda path: tmp_path)
    monkeypatch.setattr("hybrid_sdlc.mcp_server.load_config", lambda **kwargs: config)
    monkeypatch.setattr(
        "hybrid_sdlc.mcp_server.select_active_endpoint",
        lambda **kwargs: (
            ServerCandidateConfig(url=_USERINFO_URL),
            ProbeResult(
                url=_USERINFO_URL,
                available=True,
                error=FailureRecord(
                    code="PROBE_NOTE",
                    message="diagnostic",
                    details={"diagnostic_url": _QUERY_SECRET_URL},
                ),
            ),
        ),
    )
    monkeypatch.setattr("hybrid_sdlc.mcp_server.submit_job", lambda **kwargs: record)
    monkeypatch.setattr(
        "hybrid_sdlc.mcp_server.JobManager.status",
        lambda self, job_id, **kwargs: record,
    )
    monkeypatch.setattr(
        "hybrid_sdlc.mcp_server.JobManager.cancel",
        lambda self, job_id: record,
    )

    results = [
        _call_tool("check_local_model", {"repo_root": str(tmp_path)}),
        _call_tool(
            "submit_spec_job",
            {
                "repo_root": str(tmp_path),
                "spec_path": "spec.md",
                "task_id": "TASK-1",
                "test_profile": "pytest",
            },
        ),
        _call_tool("get_job_status", {"repo_root": str(tmp_path), "job_id": record.job_id}),
        _call_tool("cancel_spec_job", {"repo_root": str(tmp_path), "job_id": record.job_id}),
    ]

    for result in results:
        assert result.is_error is False
        assert "topSecret123" not in str(result.structured_content)
        assert "secretUser" not in str(result.structured_content)
        assert "secretValue" not in str(result.structured_content)
        assert "access_token" not in str(result.structured_content)


def test_submission_error_keeps_job_id_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_submit(**kwargs: Any) -> None:
        raise JobSubmissionError(
            "job-123",
            "launch failed, token=superSecret123 at "
            "http://secretUser@host.invalid/v1?access_token=secretValue",
        )

    monkeypatch.setattr("hybrid_sdlc.mcp_server.submit_job", fail_submit)
    result = _call_tool(
        "submit_spec_job",
        {
            "repo_root": ".",
            "spec_path": "spec.md",
            "task_id": "TASK-1",
            "test_profile": "pytest",
        },
    )

    assert result.is_error is True
    text = result.content[0].text
    assert "job-123" in text
    assert "superSecret123" not in text
    assert "secretUser" not in text
    assert "secretValue" not in text
    assert "[REDACTED]" in text


def test_sync_tool_returns_structured_run_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    config = ToolkitConfig.model_validate(
        {"command_profiles": {"pytest": {"name": "pytest", "argv": ["python", "-c", "pass"]}}}
    )
    started = datetime.now(UTC).isoformat()
    result = RunResult(
        run_id="run-mcp-test",
        task_id="TASK-1",
        spec_path="spec.md",
        repo_root=str(tmp_path),
        status=RunStatus.SUCCESS,
        started_at=started,
        finished_at=started,
        total_duration_seconds=0.0,
        artifacts={
            "endpoint_url": f"{_CREDENTIAL_URL}; nested {_USERINFO_URL}; {_QUERY_SECRET_URL}"
        },
    )
    monkeypatch.setattr(
        "hybrid_sdlc.mcp_server.preflight_task_request",
        lambda **kwargs: (tmp_path, "spec.md", "http://127.0.0.1:8090/v1", "model", 2),
    )
    monkeypatch.setattr("hybrid_sdlc.mcp_server.load_config", lambda **kwargs: config)
    monkeypatch.setattr("hybrid_sdlc.mcp_server.resolve_profile_executable", lambda *args: tmp_path)
    monkeypatch.setattr("hybrid_sdlc.mcp_server.run_bounded_loop", lambda **kwargs: result)

    response = _call_tool(
        "run_spec_task_sync",
        {
            "repo_root": str(tmp_path),
            "spec_path": "spec.md",
            "task_id": "TASK-1",
            "test_profile": "pytest",
        },
    )

    assert response.is_error is False
    assert response.structured_content["status"] == RunStatus.SUCCESS.value
    assert response.structured_content["run_id"] == "run-mcp-test"
    assert "topSecret123" not in str(response.structured_content)
    assert "secretUser" not in str(response.structured_content)
    assert "secretValue" not in str(response.structured_content)
    assert "access_token" not in str(response.structured_content)


def test_status_schema_rejects_wait_above_limit() -> None:
    response = _call_tool(
        "get_job_status",
        {"repo_root": ".", "job_id": "job-123", "wait_timeout_seconds": 60.1},
    )

    assert response.is_error is True
    assert "60" in response.content[0].text
