"""Unit tests for git tools, worktree preflight, locking, and diff capture."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hybrid_sdlc.errors import WorktreeDirtyError, WorktreeLockError
from hybrid_sdlc.git_tools import (
    acquire_repo_lock,
    capture_diff_summary,
    check_worktree_clean,
    get_baseline_commit,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def test_worktree_clean_and_dirty(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    file1 = tmp_path / "hello.txt"
    file1.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    # Clean worktree should pass
    check_worktree_clean(tmp_path)

    # Modify file -> dirty unstaged
    file1.write_text("modified", encoding="utf-8")
    with pytest.raises(WorktreeDirtyError) as exc_info:
        check_worktree_clean(tmp_path)
    assert "unstaged" in exc_info.value.message

    # Revert modification
    file1.write_text("initial", encoding="utf-8")
    check_worktree_clean(tmp_path)

    # Add untracked file -> dirty untracked
    untracked = tmp_path / "new_file.txt"
    untracked.write_text("untracked", encoding="utf-8")
    with pytest.raises(WorktreeDirtyError) as exc_info:
        check_worktree_clean(tmp_path)
    assert "untracked" in exc_info.value.message

    # Remove untracked file
    untracked.unlink()
    check_worktree_clean(tmp_path)

    # Ignored .hybrid_sdlc/ directory should NOT make worktree dirty
    artifact = tmp_path / ".hybrid_sdlc" / "run_123.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}", encoding="utf-8")
    check_worktree_clean(tmp_path)


def test_linked_worktree_merge_state_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    linked = tmp_path / "linked"
    repo.mkdir()
    _init_git_repo(repo)
    tracked = repo / "tracked.txt"
    tracked.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "linked-test", str(linked)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    merge_path_result = subprocess.run(
        ["git", "rev-parse", "--git-path", "MERGE_HEAD"],
        cwd=linked,
        check=True,
        capture_output=True,
        text=True,
    )
    merge_path = Path(merge_path_result.stdout.strip())
    if not merge_path.is_absolute():
        merge_path = linked / merge_path
    merge_path.write_text("synthetic-state", encoding="utf-8")

    with pytest.raises(WorktreeDirtyError) as exc_info:
        check_worktree_clean(linked)
    assert exc_info.value.details["state"] == "merge_in_progress"


def test_repo_locking(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    with acquire_repo_lock(tmp_path, run_id="run_alpha") as lock_file:
        assert lock_file.exists()
        # Attempt to acquire a second lock concurrently should fail with WorktreeLockError
        with pytest.raises(WorktreeLockError) as exc_info:
            with acquire_repo_lock(tmp_path, run_id="run_beta", timeout_seconds=0.05):
                pass
        assert "run_alpha" in exc_info.value.message

    # Lock is released after exiting context manager; now acquiring succeeds
    with acquire_repo_lock(tmp_path, run_id="run_beta") as lock_file2:
        assert lock_file2.exists()


def test_capture_diff_summary(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    file1 = tmp_path / "code.py"
    file1.write_text("def foo():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "code.py"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True
    )
    baseline = get_baseline_commit(tmp_path)

    # No diff initially
    initial_diff = capture_diff_summary(tmp_path, baseline_commit=baseline)
    assert initial_diff.is_empty
    assert initial_diff.additions == 0

    # Apply changes
    file1.write_text("def foo():\n    return 42\n", encoding="utf-8")
    patch_path = tmp_path / ".hybrid_sdlc" / "patch.patch"
    diff = capture_diff_summary(tmp_path, baseline_commit=baseline, patch_output_path=patch_path)

    assert not diff.is_empty
    assert diff.changed_files == ["code.py"]
    assert diff.additions >= 1
    assert diff.deletions >= 1
    assert patch_path.is_file()
    assert "return 42" in patch_path.read_text(encoding="utf-8")


def test_capture_diff_patch_redacts_secrets(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    tracked = tmp_path / "config.txt"
    tracked.write_text("safe\n", encoding="utf-8")
    subprocess.run(["git", "add", "config.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    tracked.write_text(f"token={secret}\n", encoding="utf-8")
    patch_path = tmp_path / ".hybrid_sdlc" / "redacted.patch"

    capture_diff_summary(tmp_path, patch_output_path=patch_path)

    persisted = patch_path.read_text(encoding="utf-8")
    assert secret not in persisted
    assert "[REDACTED]" in persisted
