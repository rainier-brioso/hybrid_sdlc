"""Keep the repository-owned verification inventory aligned with shipped APIs."""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

import click
import pytest
from click.testing import CliRunner

from hybrid_sdlc.cli import cli
from hybrid_sdlc.mcp_server import mcp


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_map(root: Path | None = None) -> dict[str, Any]:
    path = (root or _repository_root()) / "docs" / "verification-map.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _public_cli_leaves(group: click.Group, prefix: str = "") -> set[str]:
    operations: set[str] = set()
    for name, command in group.commands.items():
        if command.hidden:
            continue
        operation = f"{prefix} {name}".strip()
        if isinstance(command, click.Group):
            operations.update(_public_cli_leaves(command, operation))
        else:
            operations.add(operation)
    return operations


def _hidden_cli_leaves(group: click.Group, prefix: str = "") -> set[str]:
    operations: set[str] = set()
    for name, command in group.commands.items():
        operation = f"{prefix} {name}".strip()
        if command.hidden:
            operations.add(operation)
        elif isinstance(command, click.Group):
            operations.update(_hidden_cli_leaves(command, operation))
    return operations


def _assert_map_contract(data: dict[str, Any], root: Path) -> None:
    assert set(data) == {
        "schema_version",
        "baseline_date",
        "baseline_note",
        "journeys",
        "excluded_internal_surfaces",
        "gaps",
    }
    assert data["schema_version"] == "1.0.0", "unsupported schema_version"
    assert isinstance(data["baseline_date"], str) and data["baseline_date"]
    assert isinstance(data["baseline_note"], str) and data["baseline_note"]
    assert isinstance(data["gaps"], list) and data["gaps"]
    assert all(isinstance(gap, str) and gap.strip() for gap in data["gaps"])

    journey_ids: set[str] = set()
    mapped_cli: list[str] = []
    mapped_mcp: list[str] = []
    mapped_meta: list[str] = []
    installed_statuses = {
        "not_run",
        "historical_observation",
        "automated_help_only",
        "blocked",
    }
    live_statuses = {"not_run", "historical_observation", "blocked"}
    journey_keys = {
        "id",
        "cli_operations",
        "mcp_tools",
        "automated_test_nodeids",
        "protocol_paths",
        "evidence_paths",
        "installed_status",
        "live_status",
        "prerequisites",
        "limitations",
    }
    for journey in data["journeys"]:
        assert set(journey) == journey_keys | (
            {"meta_operations"} if "meta_operations" in journey else set()
        )
        assert isinstance(journey["id"], str) and journey["id"]
        assert journey["id"] not in journey_ids
        journey_ids.add(journey["id"])
        list_fields = [
            "cli_operations",
            "mcp_tools",
            "automated_test_nodeids",
            "protocol_paths",
            "evidence_paths",
            "prerequisites",
            "limitations",
        ]
        if "meta_operations" in journey:
            list_fields.append("meta_operations")
        for key in list_fields:
            assert isinstance(journey[key], list)
            assert all(isinstance(value, str) and value.strip() for value in journey[key])
        assert journey["automated_test_nodeids"]
        assert journey["protocol_paths"]
        assert journey["prerequisites"]
        assert journey["limitations"]
        assert journey["installed_status"] in installed_statuses, "invalid installed_status"
        assert journey["live_status"] in live_statuses, "invalid live_status"
        mapped_cli.extend(journey["cli_operations"])
        mapped_mcp.extend(journey["mcp_tools"])
        mapped_meta.extend(journey.get("meta_operations", []))

        for nodeid in journey["automated_test_nodeids"]:
            _assert_test_node_exists(root, nodeid)
        for reference in [*journey["protocol_paths"], *journey["evidence_paths"]]:
            _assert_repo_file_exists(root, reference)

        if (
            journey["installed_status"] == "historical_observation"
            or journey["live_status"] == "historical_observation"
        ):
            assert journey["evidence_paths"], (
                "Historical observations need a dated evidence artifact."
            )
            assert re.search(
                r"20\d{2}(?:\d{4}|-\d{2}-\d{2})",
                " ".join(journey["evidence_paths"]),
            ), "Historical evidence path must include its observation date."

    cli_leaves = _public_cli_leaves(cli)
    assert len(mapped_cli) == len(set(mapped_cli)), "A CLI leaf is assigned more than once."
    assert set(mapped_cli) == cli_leaves, "CLI leaf inventory drift"
    assert len(mapped_mcp) == len(set(mapped_mcp)), "An MCP tool is assigned more than once."
    listed_tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert set(mapped_mcp) == listed_tools, "MCP tool inventory drift"
    assert mapped_meta.count("--help") == 1
    assert mapped_meta.count("--version") == 1

    assert all(
        set(entry) == {"name", "reason"}
        and isinstance(entry["name"], str)
        and entry["name"]
        and isinstance(entry["reason"], str)
        and entry["reason"].strip()
        for entry in data["excluded_internal_surfaces"]
    )
    excluded = {entry["name"]: entry["reason"] for entry in data["excluded_internal_surfaces"]}
    assert set(excluded) == _hidden_cli_leaves(cli)


def _assert_repo_file_exists(root: Path, reference: str) -> None:
    assert isinstance(reference, str) and reference
    path = PurePosixPath(reference)
    assert not path.is_absolute() and ".." not in path.parts
    assert "\\" not in reference and ":" not in reference
    target = (root / Path(*path.parts)).resolve()
    target.relative_to(root.resolve())
    assert target.is_file(), f"stale or missing repository reference: {reference}"


def _assert_test_node_exists(root: Path, nodeid: str) -> None:
    file_part, separator, test_name = nodeid.partition("::")
    assert separator and test_name and "::" not in test_name
    node_path = PurePosixPath(file_part)
    assert (
        len(node_path.parts) >= 3
        and node_path.parts[0] == "tests"
        and node_path.name.startswith("test_")
        and node_path.suffix == ".py"
        and test_name.startswith("test_")
    ), f"not a pytest test reference: {nodeid}"
    _assert_repo_file_exists(root, file_part)
    tree = ast.parse((root / file_part).read_text(encoding="utf-8"), filename=file_part)
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    assert test_name in definitions, f"stale test reference: {nodeid}"


def test_verification_map_covers_public_click_and_mcp_surfaces() -> None:
    root = _repository_root()
    _assert_map_contract(_read_map(root), root)


def test_cli_version_flag_is_available() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "version" in result.output


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["journeys"][0]["cli_operations"].pop(), "CLI leaf"),
        (lambda data: data["journeys"][2]["mcp_tools"].append("unknown_tool"), "MCP tool"),
        (
            lambda data: data["journeys"][0]["automated_test_nodeids"].append(
                "tests/unit/test_missing.py::test_missing"
            ),
            "stale",
        ),
        (
            lambda data: data["journeys"][0]["automated_test_nodeids"].append(
                "tests/host_contracts/test_verification_map.py::_repository_root"
            ),
            "not a pytest test reference",
        ),
        (lambda data: data.__setitem__("schema_version", "9.9.9"), "schema_version"),
        (lambda data: data["journeys"][0].__setitem__("live_status", "passed"), "live_status"),
        (
            lambda data: data["journeys"][0].__setitem__("protocol_paths", ["../outside.md"]),
            "not in",
        ),
    ],
)
def test_verification_map_rejects_stale_or_unclassified_entries(mutate, message: str) -> None:
    root = _repository_root()
    data = copy.deepcopy(_read_map(root))
    mutate(data)
    with pytest.raises((AssertionError, ValueError), match=message):
        _assert_map_contract(data, root)


def test_verification_map_rejects_historical_status_without_evidence() -> None:
    root = _repository_root()
    data = copy.deepcopy(_read_map(root))
    journey = next(
        item for item in data["journeys"] if item["live_status"] == "historical_observation"
    )
    journey["evidence_paths"] = []
    with pytest.raises(AssertionError, match="Historical observations"):
        _assert_map_contract(data, root)


def test_verification_map_rejects_undated_historical_evidence() -> None:
    root = _repository_root()
    data = copy.deepcopy(_read_map(root))
    journey = next(
        item for item in data["journeys"] if item["live_status"] == "historical_observation"
    )
    journey["evidence_paths"] = ["docs/host-compatibility.md"]
    with pytest.raises(AssertionError, match="observation date"):
        _assert_map_contract(data, root)


def test_live_status_does_not_accept_automated_only_state() -> None:
    root = _repository_root()
    data = copy.deepcopy(_read_map(root))
    data["journeys"][0]["live_status"] = "automated_help_only"
    with pytest.raises(AssertionError, match="invalid live_status"):
        _assert_map_contract(data, root)
