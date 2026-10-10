"""Unit tests for configuration loading, precedence, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hybrid_sdlc.config import DEFAULT_MODEL, ToolkitConfig, load_config
from hybrid_sdlc.errors import ConfigurationError


def test_load_config_defaults(tmp_path: Path) -> None:
    # No hybrid_sdlc.toml present; should load documented defaults
    config = load_config(repo_root=tmp_path)
    assert config.selected_model == DEFAULT_MODEL
    assert config.max_retries == 3
    assert config.task_timeout_seconds == 600
    assert config.aider_repo_map_tokens is None
    assert config.aider_max_tokens is None
    assert config.aider_reasoning_budget_tokens is None
    assert config.aider_python is None
    assert config.aider_edit_files == []
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


@pytest.mark.parametrize("value", [True, "0", 1.5, -1])
def test_repo_map_tokens_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ToolkitConfig(aider_repo_map_tokens=value)


@pytest.mark.parametrize("value", [0, 1, 4096])
def test_repo_map_tokens_accepts_nonnegative_integers(value: int) -> None:
    assert ToolkitConfig(aider_repo_map_tokens=value).aider_repo_map_tokens == value


def test_load_config_repo_map_tokens_from_toml(tmp_path: Path) -> None:
    (tmp_path / "hybrid_sdlc.toml").write_text("aider_repo_map_tokens = 0\n", encoding="utf-8")

    assert load_config(repo_root=tmp_path).aider_repo_map_tokens == 0


def test_load_config_aider_edit_files_from_toml(tmp_path: Path) -> None:
    (tmp_path / "hybrid_sdlc.toml").write_text(
        'aider_edit_files = ["src\\\\module.py", "tests/test_module.py"]\n',
        encoding="utf-8",
    )

    assert load_config(repo_root=tmp_path).aider_edit_files == [
        "src/module.py",
        "tests/test_module.py",
    ]


def test_aider_python_accepts_absolute_string_or_path(tmp_path: Path) -> None:
    interpreter = tmp_path / "python"
    assert ToolkitConfig(aider_python=str(interpreter)).aider_python == interpreter
    assert ToolkitConfig(aider_python=interpreter).aider_python == interpreter


@pytest.mark.parametrize("value", ["", "python", ".\\python.exe", 3, True, [], {}])
def test_aider_python_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ToolkitConfig(aider_python=value)


def test_aider_python_loads_from_toml(tmp_path: Path) -> None:
    interpreter = tmp_path / "aider-venv" / "Scripts" / "python.exe"
    (tmp_path / "hybrid_sdlc.toml").write_text(
        f'aider_python = "{interpreter.as_posix()}"\n', encoding="utf-8"
    )

    assert load_config(repo_root=tmp_path).aider_python == interpreter


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aider_max_tokens", True),
        ("aider_max_tokens", "8192"),
        ("aider_max_tokens", 0),
        ("aider_max_tokens", -1),
        ("aider_reasoning_budget_tokens", True),
        ("aider_reasoning_budget_tokens", "0"),
        ("aider_reasoning_budget_tokens", -1),
    ],
)
def test_aider_token_budgets_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ToolkitConfig(**{field: value})


@pytest.mark.parametrize(
    ("max_tokens", "reasoning", "expected"),
    [(None, 0, 0), (8192, None, None), (8192, 0, 0), (8192, 1024, 1024)],
)
def test_aider_token_budgets_accept_single_and_valid_values(
    max_tokens: int | None, reasoning: int | None, expected: int | None
) -> None:
    config = ToolkitConfig(aider_max_tokens=max_tokens, aider_reasoning_budget_tokens=reasoning)
    assert config.aider_max_tokens == max_tokens
    assert config.aider_reasoning_budget_tokens == expected


@pytest.mark.parametrize("reasoning", [8192, 9000])
def test_aider_reasoning_budget_must_be_below_output_limit(reasoning: int) -> None:
    with pytest.raises(ValidationError, match="must be less than"):
        ToolkitConfig(aider_max_tokens=8192, aider_reasoning_budget_tokens=reasoning)


def test_aider_token_budgets_load_from_toml_and_cli_overrides(tmp_path: Path) -> None:
    (tmp_path / "hybrid_sdlc.toml").write_text(
        "aider_max_tokens = 4096\naider_reasoning_budget_tokens = 512\n", encoding="utf-8"
    )
    from_toml = load_config(repo_root=tmp_path)
    assert from_toml.aider_max_tokens == 4096
    assert from_toml.aider_reasoning_budget_tokens == 512

    overridden = load_config(
        repo_root=tmp_path,
        cli_overrides={"aider_max_tokens": 8192, "aider_reasoning_budget_tokens": 1024},
    )
    assert overridden.aider_max_tokens == 8192
    assert overridden.aider_reasoning_budget_tokens == 1024


@pytest.mark.parametrize(
    "value",
    [
        "src/example.py",
        [""],
        ["/etc/passwd"],
        ["C:\\temp\\file.py"],
        ["\\\\server\\share\\file.py"],
        ["../outside.py"],
        ["src/../outside.py"],
        [r"src\..\outside.py"],
        ["."],
        ["src//module.py"],
        ["src/module.py", "src\\module.py"],
        [1],
    ],
)
def test_aider_edit_files_rejects_invalid_paths_and_types(value: object) -> None:
    with pytest.raises(ValidationError):
        ToolkitConfig(aider_edit_files=value)


def test_aider_edit_files_accepts_empty_list() -> None:
    assert ToolkitConfig(aider_edit_files=[]).aider_edit_files == []


@pytest.mark.parametrize("value", ["true", '"0"', "1.5", "-1"])
def test_load_config_rejects_invalid_repo_map_tokens_from_toml(tmp_path: Path, value: str) -> None:
    (tmp_path / "hybrid_sdlc.toml").write_text(
        f"aider_repo_map_tokens = {value}\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(repo_root=tmp_path)

    assert exc_info.value.code == "CONFIG_VALIDATION_ERROR"
