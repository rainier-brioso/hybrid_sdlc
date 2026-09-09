"""Unit tests for configuration loading, precedence, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from hybrid_sdlc.config import DEFAULT_MODEL, load_config
from hybrid_sdlc.errors import ConfigurationError


def test_load_config_defaults(tmp_path: Path) -> None:
    # No hybrid_sdlc.toml present; should load documented defaults
    config = load_config(repo_root=tmp_path)
    assert config.selected_model == DEFAULT_MODEL
    assert config.max_retries == 3
    assert config.task_timeout_seconds == 600
    assert len(config.server_candidates) == 2
    assert config.server_candidates[0].url == "http://127.0.0.1:8090/v1"
    assert config.server_candidates[1].url == "http://127.0.0.1:8089/v1"


def test_load_config_from_toml(tmp_path: Path) -> None:
    toml_content = """
    selected_model = "Qwen/Qwen2.5-Coder-14B-Instruct"
    max_retries = 2
    task_timeout_seconds = 300

    [[server_candidates]]
    url = "http://127.0.0.1:9000/v1"
    model_alias = "custom-qwen"

    [command_profiles.pytest]
    argv = ["pytest", "-v"]
    cwd = "."
    timeout_seconds = 120
    """
    (tmp_path / "hybrid_sdlc.toml").write_text(toml_content, encoding="utf-8")
    config = load_config(repo_root=tmp_path)

    assert config.selected_model == "Qwen/Qwen2.5-Coder-14B-Instruct"
    assert config.max_retries == 2
    assert config.task_timeout_seconds == 300
    assert len(config.server_candidates) == 1
    assert config.server_candidates[0].url == "http://127.0.0.1:9000/v1"
    assert "pytest" in config.command_profiles
    assert config.command_profiles["pytest"].argv == ["pytest", "-v"]


def test_cli_overrides_precedence(tmp_path: Path) -> None:
    toml_content = """
    selected_model = "Qwen/Qwen2.5-Coder-14B-Instruct"
    max_retries = 2
    """
    (tmp_path / "hybrid_sdlc.toml").write_text(toml_content, encoding="utf-8")

    config = load_config(
        repo_root=tmp_path,
        cli_overrides={"max_retries": 5, "selected_model": "CustomModel"},
    )
    assert config.max_retries == 5
    assert config.selected_model == "CustomModel"


def test_load_config_rejects_path_above_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside_config = tmp_path / "outside.toml"
    outside_config.write_text("max_retries = 1", encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(repo_root=repo_root, config_file=outside_config)
    assert exc_info.value.code == "CONFIG_OUTSIDE_REPO"


def test_load_config_syntax_error(tmp_path: Path) -> None:
    bad_toml = tmp_path / "hybrid_sdlc.toml"
    bad_toml.write_text("this is not valid toml = = =", encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(repo_root=tmp_path)
    assert exc_info.value.code == "CONFIG_SYNTAX_ERROR"


def test_load_config_invalid_port(tmp_path: Path) -> None:
    bad_toml = tmp_path / "hybrid_sdlc.toml"
    bad_toml.write_text(
        """
        [[server_candidates]]
        url = "http://127.0.0.1:99999/v1"
        """,
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        load_config(repo_root=tmp_path)
    assert exc_info.value.code == "CONFIG_VALIDATION_ERROR"
