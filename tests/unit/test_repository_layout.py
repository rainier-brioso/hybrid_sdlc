"""Unit tests verifying repository layout and gitignore rules."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_gitignore_contains_required_rules() -> None:
    root = Path(__file__).resolve().parents[2]
    gitignore_file = root / ".gitignore"
    assert gitignore_file.is_file()

    content = gitignore_file.read_text(encoding="utf-8")
    assert ".hybrid_sdlc/" in content
    assert "*.gguf" in content
    assert ".venv/" in content
    assert "*.lock" in content
    assert "!uv.lock" in content


def test_docker_model_profiles_are_portable_and_parseable() -> None:
    root = Path(__file__).resolve().parents[2]
    compose_file = root / "compose.yaml"
    env_example = root / ".env.example"
    profiles = root / "config" / "model-profiles"

    assert compose_file.is_file()
    assert env_example.is_file()

    compose = compose_file.read_text(encoding="utf-8")
    assert "127.0.0.1:${LLAMA_HOST_PORT:-8089}:8080" in compose
    assert "read_only: true" in compose
    assert "gpus: all" in compose

    env_content = env_example.read_text(encoding="utf-8")
    assert "C:\\Users\\" not in env_content
    assert "C:\\AI\\" not in env_content

    expected_profiles = {
        "qwen36-35b-a3b-rtx3090-worker.toml": 16384,
        "qwen36-35b-a3b-rtx3090-interactive.toml": 65536,
    }
    for filename, expected_context in expected_profiles.items():
        with (profiles / filename).open("rb") as stream:
            profile = tomllib.load(stream)
        assert profile["schema_version"] == 1
        assert profile["endpoint"]["max_concurrency"] == 1
        assert profile["limits"]["context_tokens"] == expected_context


def test_spec_kit_templates_use_canonical_layout_and_require_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    constitution = root / ".specify" / "memory" / "constitution.md"
    templates = root / ".specify" / "templates"
    spec = templates / "spec-template.md"
    plan = templates / "plan-template.md"
    tasks = templates / "tasks-template.md"

    assert all(path.is_file() for path in (constitution, spec, plan, tasks))
    assert "specs/<NNN-feature>/" in spec.read_text(encoding="utf-8")
    assert "Acceptance Criteria" in spec.read_text(encoding="utf-8")
    assert "Interfaces & Contracts" in plan.read_text(encoding="utf-8")
    contents = {
        path: path.read_text(encoding="utf-8") for path in (constitution, spec, plan, tasks)
    }
    assert "[command_profiles.<id>]" in contents[constitution]
    assert "CI quality gates are validation workflows" in contents[constitution]
    assert "command_profiles.<id>" in contents[spec]
    assert "command_profiles.<id>" in contents[plan]
    assert "CI quality gates are not runnable named profiles" in contents[plan]
    assert "[command_profiles.<id>]" in contents[tasks]
    assert "<profile-id>" in contents[tasks]
    assert "atomic, reviewable" in contents[tasks]

    with (root / "pyproject.toml").open("rb") as stream:
        build_config = tomllib.load(stream)
    force_include = build_config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert set(force_include) == {
        ".specify/memory/constitution.md",
        ".specify/templates/spec-template.md",
        ".specify/templates/plan-template.md",
        ".specify/templates/tasks-template.md",
    }
    assert all(path.startswith("hybrid_sdlc/_specify/") for path in force_include.values())
