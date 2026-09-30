"""Integration coverage for commits scoped to verified isolated worktrees."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread

import pytest

from hybrid_sdlc.worktrees import WorktreeRecord


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _create_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source repository"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@local")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def _create_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, WorktreeRecord]:
    from hybrid_sdlc import worktrees

    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    repo = _create_repo(tmp_path)
    return repo, worktrees.create_worktree(repo, worktree_id="commit-test")


def _commit(
    record: WorktreeRecord,
    *,
    task_id: str = "HSDLC-053",
    commit_requested: bool = True,
    tests_passed: bool = True,
    policy_passed: bool = True,
) -> str | None:
    from hybrid_sdlc.git_tools import create_scoped_commit

    return create_scoped_commit(
        record,
        task_id,
        "add isolated result",
        commit_requested=commit_requested,
        tests_passed=tests_passed,
        policy_passed=policy_passed,
    )


def test_scoped_commit_accepts_public_multihyphen_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated edit\n", encoding="utf-8")

    commit = _commit(record, task_id="TASK-E2E-001")

    assert commit is not None
    assert _git(record.path, "show", "-s", "--format=%s", "HEAD") == (
        "TASK-E2E-001: add isolated result"
    )


def test_scoped_commit_only_commits_isolated_changes_and_references_task_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, record = _create_isolated(tmp_path, monkeypatch)
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("uncommitted user edit\n", encoding="utf-8")
    (repo / "user file.txt").write_text("preserve me\n", encoding="utf-8")
    source_before = _git(repo, "status", "--porcelain=v1")
    (record.path / "README.md").write_text("isolated result\n", encoding="utf-8")
    (record.path / "new file.txt").write_text("new isolated content\n", encoding="utf-8")

    commit = _commit(record)

    assert commit is not None
    assert _git(record.path, "rev-parse", "HEAD") == commit
    assert _git(record.path, "rev-parse", "HEAD^") == baseline
    assert _git(record.path, "show", "-s", "--format=%s", "HEAD") == (
        "HSDLC-053: add isolated result"
    )
    assert _git(record.path, "show", "--format=", "--name-only", "HEAD") == (
        "README.md\nnew file.txt"
    )
    assert _git(repo, "rev-parse", "HEAD") == baseline
    assert _git(repo, "status", "--porcelain=v1") == source_before
    assert (repo / "README.md").read_text(encoding="utf-8") == "uncommitted user edit\n"
    assert (repo / "user file.txt").read_text(encoding="utf-8") == "preserve me\n"


@pytest.mark.parametrize(
    ("gate", "message"),
    [("tests_passed", "tests did not pass"), ("policy_passed", "policy checks")],
)
def test_failed_gate_never_stages_or_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    message: str,
) -> None:
    repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated edit\n", encoding="utf-8")

    from hybrid_sdlc.errors import RepositoryError

    with pytest.raises(RepositoryError, match=message):
        _commit(
            record,
            tests_passed=gate != "tests_passed",
            policy_passed=gate != "policy_passed",
        )

    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert _git(record.path, "diff", "--cached") == ""
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit


def test_commit_is_inert_without_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated edit\n", encoding="utf-8")

    assert _commit(record, commit_requested=False) is None
    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert _git(record.path, "diff", "--cached") == ""
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit


def test_preexisting_staged_changes_are_rejected_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("staged edit\n", encoding="utf-8")
    _git(record.path, "add", "README.md")

    from hybrid_sdlc.errors import RepositoryError

    with pytest.raises(RepositoryError, match="already contains staged"):
        _commit(record)
    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit


def test_modified_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, record = _create_isolated(tmp_path, monkeypatch)
    link = record.path / "external-link"
    try:
        link.symlink_to(tmp_path / "outside.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")

    from hybrid_sdlc.errors import RepositoryError

    with pytest.raises(RepositoryError, match="symlink"):
        _commit(record)
    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert _git(record.path, "diff", "--cached") == ""
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit


def test_forged_worktree_record_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated edit\n", encoding="utf-8")
    forged = replace(record, path=repo)

    from hybrid_sdlc.errors import RepositoryError

    with pytest.raises(RepositoryError, match="expected isolated checkout"):
        _commit(forged)
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit


def test_staged_gitlink_is_rejected_without_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, record = _create_isolated(tmp_path, monkeypatch)
    _git(
        record.path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{record.baseline_commit},nested-submodule",
    )

    from hybrid_sdlc.errors import RepositoryError

    with pytest.raises(RepositoryError, match="submodules"):
        _commit(record)
    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit


def test_concurrent_commit_cannot_leave_loser_staging_after_winner_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import git_tools, worktrees
    from hybrid_sdlc.errors import RepositoryError

    _repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated result\n", encoding="utf-8")
    monkeypatch.setattr(worktrees, "WORKTREE_OPERATION_LOCK_TIMEOUT_SECONDS", 0.2)
    original_run = git_tools.subprocess.run
    first_add_finished = Event()
    release_first = Event()

    def pause_first_add(command: list[str], *args: object, **kwargs: object) -> object:
        result = original_run(command, *args, **kwargs)
        if (
            command[:3] == ["git", "-C", str(record.path)]
            and command[3:6] == ["add", "--all", "--"]
            and current_thread().name == "commit-first"
        ):
            first_add_finished.set()
            if not release_first.wait(5):
                raise TimeoutError("test did not release first commit")
        return result

    monkeypatch.setattr(git_tools.subprocess, "run", pause_first_add)

    def first_commit() -> str | None:
        current_thread().name = "commit-first"
        return _commit(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_commit)
        assert first_add_finished.wait(5)
        second = pool.submit(_commit, record)
        with pytest.raises(RepositoryError, match="Another operation"):
            second.result(timeout=5)
        release_first.set()
        commit = first.result(timeout=5)

    assert commit is not None
    assert _git(record.path, "rev-parse", "HEAD") == commit
    assert _git(record.path, "diff", "--cached") == ""
    assert _git(record.path, "status", "--porcelain=v1") == ""


def test_rollback_waits_until_scoped_commit_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import git_tools, worktrees

    repo, record = _create_isolated(tmp_path, monkeypatch)
    (record.path / "README.md").write_text("isolated result\n", encoding="utf-8")
    original_run = git_tools.subprocess.run
    original_lock = worktrees.FileLock
    update_ref_started = Event()
    release_update_ref = Event()
    rollback_lock_attempted = Event()

    def pause_update_ref(command: list[str], *args: object, **kwargs: object) -> object:
        if command[:3] == ["git", "-C", str(record.path)] and "update-ref" in command:
            update_ref_started.set()
            if not release_update_ref.wait(5):
                raise TimeoutError("test did not release scoped commit")
        return original_run(command, *args, **kwargs)

    class ObservedFileLock(original_lock):
        def acquire(self, *args: object, **kwargs: object) -> object:
            if current_thread().name == "rollback-waiter":
                rollback_lock_attempted.set()
            return super().acquire(*args, **kwargs)

    monkeypatch.setattr(git_tools.subprocess, "run", pause_update_ref)
    monkeypatch.setattr(worktrees, "FileLock", ObservedFileLock)

    def rollback() -> None:
        current_thread().name = "rollback-waiter"
        worktrees.rollback_worktree(record)

    with ThreadPoolExecutor(max_workers=2) as pool:
        commit_future = pool.submit(_commit, record)
        assert update_ref_started.wait(5)
        rollback_future = pool.submit(rollback)
        assert rollback_lock_attempted.wait(5)
        release_update_ref.set()
        commit = commit_future.result(timeout=5)
        rollback_future.result(timeout=5)

    assert commit is not None
    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert _git(record.path, "status", "--porcelain=v1") == ""
    assert _git(repo, "rev-parse", "HEAD") == record.baseline_commit
