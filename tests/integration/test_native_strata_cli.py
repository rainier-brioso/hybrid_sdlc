from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hybrid_sdlc import cli as cli_module
from hybrid_sdlc.cli import cli
from hybrid_sdlc.runtime_strata import RuntimeManagementError


def test_native_configure_forwards_explicit_paths_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    def configure_native(**kwargs: Any) -> dict[str, object]:
        received.update(kwargs)
        return {"backend": "native"}

    monkeypatch.setattr(cli_module, "configure_native_strata_runtime", configure_native)
    args = [
        "runtime",
        "strata",
        "configure",
        "--backend",
        "native",
        "--install-dir",
        "C:/strata",
        "--components-dir",
        "C:/components",
        "--profile-dir",
        "C:/profiles",
        "--preset",
        "test",
        "--replace-backend",
        "--json",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert received == {
        "install_dir": Path("C:/strata"),
        "components_dir": Path("C:/components"),
        "profile_dir": Path("C:/profiles"),
        "preset": "test",
        "port": 8080,
        "context_size": 131072,
        "replace_backend": True,
    }
    assert json.loads(result.output) == {"backend": "native"}


@pytest.mark.parametrize("missing", ["--components-dir", "--profile-dir", "--preset"])
def test_native_configure_requires_all_native_values(missing: str) -> None:
    values = {
        "--install-dir": "C:/install",
        "--components-dir": "C:/components",
        "--profile-dir": "C:/profiles",
        "--preset": "fast",
    }
    values.pop(missing)
    args = ["runtime", "strata", "configure", "--backend", "native"]
    for option, value in values.items():
        args += [option, value]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "required for native backend" in result.output


def test_native_rejects_relative_paths_and_reused_volume() -> None:
    base = [
        "runtime",
        "strata",
        "configure",
        "--backend",
        "native",
        "--install-dir",
        "install",
        "--components-dir",
        "C:/components",
        "--profile-dir",
        "C:/profiles",
        "--preset",
        "fast",
    ]
    relative = CliRunner().invoke(cli, base)
    assert relative.exit_code != 0
    assert "absolute paths" in relative.output
    volume = CliRunner().invoke(
        cli,
        [
            "runtime",
            "strata",
            "configure",
            "--backend",
            "native",
            "--install-dir",
            "C:/install",
            "--components-dir",
            "C:/components",
            "--profile-dir",
            "C:/profiles",
            "--preset",
            "fast",
            "--reuse-model-volume",
            "models",
        ],
    )
    assert volume.exit_code != 0
    assert "only supported with the Docker backend" in volume.output


def test_docker_configure_preserves_old_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    received: dict[str, Any] = {}

    def configure(**kwargs: Any) -> dict[str, object]:
        received.update(kwargs)
        return {"backend": "docker"}

    monkeypatch.setattr(cli_module, "configure_strata_runtime", configure)
    result = CliRunner().invoke(
        cli,
        ["runtime", "strata", "configure", "--port", "8099", "--context-size", "65536", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert received == {"port": 8099, "model_volume": None, "context_size": 65536}


def test_docker_rejects_native_specific_options() -> None:
    result = CliRunner().invoke(cli, ["runtime", "strata", "configure", "--preset", "fast"])
    assert result.exit_code != 0
    assert "require --backend native" in result.output


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_top_level_actions_call_existing_wrappers(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[str] = []

    def run() -> dict[str, object]:
        received.append(action)
        return {"action": action}

    attribute = {
        "start": "start_strata_runtime",
        "stop": "stop_strata_runtime",
        "restart": "restart_strata_runtime",
    }[action]
    monkeypatch.setattr(cli_module, attribute, run)
    result = CliRunner().invoke(cli, [action, "strata", "--json"])
    assert result.exit_code == 0, result.output
    assert received == [action]
    assert json.loads(result.output) == {"action": action}


def test_top_level_status_strata_dispatches_without_changing_job_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "strata_runtime_status", lambda: {"status": "ok"})
    result = CliRunner().invoke(cli, ["status", "strata", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"status": "ok"}


def test_top_level_action_uses_structured_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> dict[str, object]:
        raise RuntimeManagementError("not configured", code="RUNTIME_NOT_CONFIGURED")

    monkeypatch.setattr(cli_module, "start_strata_runtime", fail)
    result = CliRunner().invoke(cli, ["start", "strata", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_NOT_CONFIGURED"


def test_hsdlc_console_script_alias() -> None:
    from pathlib import Path

    project = Path(__file__).parents[2] / "pyproject.toml"
    assert 'hsdlc = "hybrid_sdlc.cli:cli"' in project.read_text(encoding="utf-8")
