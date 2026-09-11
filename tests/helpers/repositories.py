"""Fixture-repository factory for creating disposable Git repositories in tests.

All factories accept a ``tmp_path`` and create a fresh Git repository beneath it.
None of the helpers mutate the toolkit checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hybrid_sdlc.command_profiles import CommandProfile


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


def _init_git(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@local")
    gitignore = repo / ".gitignore"
    gitignore.write_text(".hybrid_sdlc/\nhelpers/\n", encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text("initial", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-m", "initial commit")


def create_passing_repo(tmp_path: Path) -> Path:
    """Create a clean repository with a single passing baseline.

    Returns the repository root path.
    """
    repo = tmp_path / "passing_repo"
    repo.mkdir()
    _init_git(repo)
    helper = repo / "helper.py"
    helper.write_text("def check(): return True\n", encoding="utf-8")
    _git(repo, "add", "helper.py")
    _git(repo, "commit", "-m", "add helper")
    return repo


def create_failing_repo(tmp_path: Path) -> Path:
    """Create a clean repository whose baseline tests fail.

    The repository contains a test script that always exits with code 1.
    Returns the repository root path.
    """
    repo = tmp_path / "failing_repo"
    repo.mkdir()
    _init_git(repo)
    test_script = repo / "run_tests.py"
    test_script.write_text(
        "#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
        encoding="utf-8",
    )
    _git(repo, "add", "run_tests.py")
    _git(repo, "commit", "-m", "add failing test script")
    return repo


def create_dirty_repo(tmp_path: Path) -> Path:
    """Create a repository with uncommitted tracked changes.

    The repository is otherwise clean except for one modified tracked file.
    Returns the repository root path.
    """
    repo = tmp_path / "dirty_repo"
    repo.mkdir()
    _init_git(repo)
    data_file = repo / "data.txt"
    data_file.write_text("hello", encoding="utf-8")
    _git(repo, "add", "data.txt")
    _git(repo, "commit", "-m", "add data")
    data_file.write_text("modified content", encoding="utf-8")
    return repo


def create_untracked_repo(tmp_path: Path) -> Path:
    """Create a repository with untracked files.

    Returns the repository root path.
    """
    repo = tmp_path / "untracked_repo"
    repo.mkdir()
    _init_git(repo)
    leaked = repo / "secrets.txt"
    leaked.write_text("api_key=supersecret123", encoding="utf-8")
    return repo


def create_spec_repo(tmp_path: Path, spec_content: str = "# Spec") -> Path:
    """Create a clean repository with a spec file ready for run-task.

    Returns the repository root path.
    """
    repo = tmp_path / "spec_repo"
    repo.mkdir()
    _init_git(repo)
    spec = repo / "spec.md"
    spec.write_text(spec_content, encoding="utf-8")
    _git(repo, "add", "spec.md")
    _git(repo, "commit", "-m", "add spec")
    return repo


def create_test_profile_repo(tmp_path: Path) -> tuple[Path, CommandProfile]:
    """Create a repository and a matching command profile for testing.

    The profile runs ``python -c "import sys; sys.exit(0)"`` in the repository root.
    Returns ``(repo_root, profile)``.
    """
    repo = tmp_path / "profile_repo"
    repo.mkdir()
    _init_git(repo)
    py_name = "python"
    profile = CommandProfile(
        name="basic_test",
        argv=[py_name, "-c", "import sys; sys.exit(0)"],
        cwd=".",
        timeout_seconds=10,
    )
    return repo, profile


def create_successful_edit_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a repository with a fake Aider that produces a passing edit.

    The fake Aider script writes a solution file that tests can verify.
    Returns ``(repo_root, fake_aider_path)``.
    """
    repo = tmp_path / "edit_repo"
    repo.mkdir()
    _init_git(repo)

    helpers_dir = repo / "helpers"
    helpers_dir.mkdir()
    fake_aider = helpers_dir / "fake_aider.py"
    fake_aider.write_text(
        "from pathlib import Path\n"
        "Path('solution.py').write_text('def answer():\\n    return 42\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    spec = repo / "spec.md"
    spec.write_text("# Implement answer()", encoding="utf-8")
    _git(repo, "add", "spec.md")
    _git(repo, "commit", "-m", "add spec")

    return repo, fake_aider
