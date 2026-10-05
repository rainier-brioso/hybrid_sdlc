"""Check that the host compatibility record stays aligned with shipped contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from hybrid_sdlc.mcp_server import mcp


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _compatibility_rows() -> dict[str, dict[str, str]]:
    document = (_repository_root() / "docs" / "host-compatibility.md").read_text(encoding="utf-8")
    lines = [line for line in document.splitlines() if line.startswith("|")]
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows: dict[str, dict[str, str]] = {}
    for line in lines[2:]:  # Ignore the Markdown separator row.
        values = [cell.strip() for cell in line.strip("|").split("|")]
        rows[values[0]] = dict(zip(headers, values, strict=True))
    return rows


def test_compatibility_document_covers_each_host_guide_and_current_mcp_tools() -> None:
    root = _repository_root()
    rows = _compatibility_rows()

    assert set(rows) == {"Codex CLI", "Claude Code CLI", "Google Antigravity IDE"}
    for guide in ("codex.md", "claude-code.md", "antigravity.md"):
        assert (root / "docs" / "hosts" / guide).is_file()

    listed_tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert listed_tools == {
        "check_local_model",
        "run_spec_task_sync",
        "submit_spec_job",
        "get_job_status",
        "cancel_spec_job",
    }
    document = (root / "docs" / "host-compatibility.md").read_text(encoding="utf-8")
    assert all(f"`{tool}`" in document for tool in listed_tools)


def test_mcp_tool_schemas_keep_required_arguments_and_polling_bounds() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    run_required = tools["run_spec_task_sync"].input_schema["required"]
    submit_required = tools["submit_spec_job"].input_schema["required"]
    check_required = tools["check_local_model"].input_schema["required"]
    status_required = tools["get_job_status"].input_schema["required"]
    cancel_required = tools["cancel_spec_job"].input_schema["required"]
    expected_task_arguments = {"repo_root", "spec_path", "task_id", "test_profile"}

    assert set(run_required) == expected_task_arguments
    assert set(submit_required) == expected_task_arguments
    assert check_required == ["repo_root"]
    assert set(status_required) == {"repo_root", "job_id"}
    assert set(cancel_required) == {"repo_root", "job_id"}

    status_properties = tools["get_job_status"].input_schema["properties"]
    assert status_properties["wait_timeout_seconds"]["minimum"] == 0
    assert status_properties["wait_timeout_seconds"]["maximum"] == 60
    assert status_properties["wait_timeout_seconds"]["default"] == 0
    assert status_properties["poll_interval_seconds"]["minimum"] == 0.01
    assert status_properties["poll_interval_seconds"]["maximum"] == 5
