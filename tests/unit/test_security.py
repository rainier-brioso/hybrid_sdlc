"""Unit tests for security policies, path confinement, and environment sanitization."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hybrid_sdlc.errors import PathTraversalError, RepositoryRootNotFoundError
from hybrid_sdlc.security import (
    build_sanitized_environment,
    resolve_confined_path,
    verify_repo_root,
)


def test_verify_repo_root_valid(tmp_path: Path) -> None:
    repo = tmp_path / "my_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)

    verified = verify_repo_root(repo)
    assert verified == repo.resolve()


def test_verify_repo_root_nonexistent(tmp_path: Path) -> None:
    with pytest.raises(RepositoryRootNotFoundError):
        verify_repo_root(tmp_path / "does_not_exist")


def test_verify_repo_root_not_git(tmp_path: Path) -> None:
    not_git = tmp_path / "not_git"
    not_git.mkdir()
    with pytest.raises(RepositoryRootNotFoundError):
        verify_repo_root(not_git)


def test_verify_repo_root_nested_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "top_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subdir = repo / "sub" / "deep"
    subdir.mkdir(parents=True)

    with pytest.raises(RepositoryRootNotFoundError) as exc_info:
        verify_repo_root(subdir)
    assert "nested directory" in exc_info.value.message


def test_resolve_confined_path_valid(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hello')", encoding="utf-8")

    resolved = resolve_confined_path("src/app.py", repo_root=tmp_path)
    assert resolved == target.resolve()


def test_resolve_confined_path_escape_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(PathTraversalError):
        resolve_confined_path("../outside.txt", repo_root=repo)


def test_resolve_confined_path_alternate_data_stream(tmp_path: Path) -> None:
    if os.name == "nt":
        with pytest.raises(PathTraversalError):
            resolve_confined_path("file.txt:hidden_stream", repo_root=tmp_path, must_exist=False)


def test_build_sanitized_environment() -> None:
    # Inject synthetic secret into os.environ for testing
    os.environ["SUPER_SECRET_API_KEY"] = "super_secret_val_123"
    os.environ["GITHUB_TOKEN"] = "ghp_1234567890abcdef1234567890abcdef"
    os.environ["NORMAL_VAR"] = "normal_val"

    try:
        clean_env = build_sanitized_environment(allowlist=["NORMAL_VAR"])
        # Secret variables must NOT be present
        assert "SUPER_SECRET_API_KEY" not in clean_env
        assert "GITHUB_TOKEN" not in clean_env
        # Allowlisted variable must be present
        assert clean_env.get("NORMAL_VAR") == "normal_val"
        # Local OpenAI dummy credentials must be present
        assert clean_env.get("OPENAI_API_KEY") == "local-no-key"
        # System baseline variables should be present
        if os.name == "nt":
            assert "SYSTEMROOT" in clean_env or "PATH" in clean_env
        else:
            assert "PATH" in clean_env
    finally:
        os.environ.pop("SUPER_SECRET_API_KEY", None)
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("NORMAL_VAR", None)
