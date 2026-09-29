"""Integration coverage for safe isolated worktree creation."""

from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, current_thread

import pytest


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _checkout_snapshot(repo: Path) -> tuple[str, dict[str, bytes]]:
    contents = {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }
    return _git(repo, "status", "--porcelain=v1"), contents


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


def test_create_worktree_uses_unique_detached_baseline_and_preserves_source_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    (repo / "README.md").write_text("user modification\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("uncommitted user file\n", encoding="utf-8")
    before = _checkout_snapshot(repo)
    baseline = _git(repo, "rev-parse", "HEAD")

    first = worktrees.create_worktree(repo)
    second = worktrees.create_worktree(repo)

    assert first.path != second.path
    assert first.path.is_dir() and second.path.is_dir()
    assert first.path != repo and repo not in first.path.parents
    assert _git(first.path, "rev-parse", "HEAD") == baseline
    assert _git(first.path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert first.baseline_commit == baseline
    assert (first.path / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert first.record_path.is_file()
    assert _checkout_snapshot(repo) == before


def test_failed_add_cleans_unregistered_partial_checkout_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    original_git = worktrees._git
    attempted_checkout: Path | None = None

    def fail_during_add(root: Path, *args: str) -> str:
        nonlocal attempted_checkout
        if args[:2] == ("worktree", "add"):
            attempted_checkout = Path(args[3])
            attempted_checkout.mkdir()
            (attempted_checkout / "partial.txt").write_text("partial", encoding="utf-8")
            raise subprocess.CalledProcessError(1, ["git", "worktree", "add"])
        return original_git(root, *args)

    monkeypatch.setattr(worktrees, "_git", fail_during_add)
    with pytest.raises(worktrees.RepositoryError, match="Could not create isolated Git worktree"):
        worktrees.create_worktree(repo, worktree_id="partial-failure")

    assert attempted_checkout is not None
    assert not attempted_checkout.exists()
    assert _checkout_snapshot(repo) == before
    assert len(_git(repo, "worktree", "list", "--porcelain").split("worktree ")) == 2


def test_failure_after_registration_removes_only_attempt_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    original_git = worktrees._git
    attempted_checkout: Path | None = None

    def fail_after_add(root: Path, *args: str) -> str:
        nonlocal attempted_checkout
        result = original_git(root, *args)
        if args[:2] == ("worktree", "add"):
            attempted_checkout = Path(args[3])
            raise subprocess.CalledProcessError(1, ["git", "worktree", "add"])
        return result

    monkeypatch.setattr(worktrees, "_git", fail_after_add)
    with pytest.raises(worktrees.RepositoryError, match="Could not create isolated Git worktree"):
        worktrees.create_worktree(repo, worktree_id="registered-failure")

    assert attempted_checkout is not None
    assert not attempted_checkout.exists()
    assert _checkout_snapshot(repo) == before
    paths = [
        line
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    assert paths == [f"worktree {repo.resolve().as_posix()}"]


def test_existing_attempt_directory_is_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    repo_key = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()
    existing = temp_root / "hsdlc-wt" / repo_key[:20] / "collision"
    existing.mkdir(parents=True)
    marker = existing / "user-data.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(worktrees.RepositoryError, match="already exists"):
        worktrees.create_worktree(repo, worktree_id="collision")

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_concurrent_first_creation_accepts_the_other_callers_storage_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    original_ensure = worktrees._ensure_storage_directory
    first_directory_barrier = Barrier(2)

    def race_on_shared_directory(directory: Path, expected_parent: Path) -> None:
        if directory.name == "hsdlc-wt":
            first_directory_barrier.wait(timeout=10)
        original_ensure(directory, expected_parent)

    monkeypatch.setattr(worktrees, "_ensure_storage_directory", race_on_shared_directory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(worktrees.create_worktree, repo)
        second_future = pool.submit(worktrees.create_worktree, repo)
        first = first_future.result(timeout=30)
        second = second_future.result(timeout=30)

    assert first.path != second.path
    assert first.path.is_dir() and second.path.is_dir()
    assert first.baseline_commit == second.baseline_commit == _git(repo, "rev-parse", "HEAD")


def test_failed_creator_preserves_shared_directories_adopted_by_concurrent_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    original_ensure = worktrees._ensure_storage_directory
    original_git = worktrees._git
    first_storage_ready = Event()
    second_storage_validated = Event()
    release_second = Event()

    def coordinate_storage(directory: Path, expected_parent: Path) -> None:
        thread_name = current_thread().name
        is_first = thread_name.endswith("_0")
        if not is_first:
            assert first_storage_ready.wait(timeout=10)
        original_ensure(directory, expected_parent)
        if is_first and directory.name != "hsdlc-wt":
            first_storage_ready.set()
        if not is_first and directory.name != "hsdlc-wt":
            second_storage_validated.set()
            assert release_second.wait(timeout=10)

    def fail_first_add(root: Path, *args: str) -> str:
        if current_thread().name.endswith("_0") and args[:2] == ("worktree", "add"):
            assert second_storage_validated.wait(timeout=10)
            raise subprocess.CalledProcessError(1, ["git", "worktree", "add"])
        return original_git(root, *args)

    monkeypatch.setattr(worktrees, "_ensure_storage_directory", coordinate_storage)
    monkeypatch.setattr(worktrees, "_git", fail_first_add)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="worktree-race") as pool:
        failed_creator = pool.submit(worktrees.create_worktree, repo, worktree_id="failed-first")
        concurrent_creator = pool.submit(worktrees.create_worktree, repo, worktree_id="surviving")
        try:
            with pytest.raises(
                worktrees.RepositoryError, match="Could not create isolated Git worktree"
            ):
                failed_creator.result(timeout=30)
        finally:
            release_second.set()
        surviving = concurrent_creator.result(timeout=30)

    assert surviving.path.is_dir()
    assert surviving.record_path.is_file()


def test_worktree_id_rejects_path_syntax(tmp_path: Path) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    with pytest.raises(ValueError, match="safe identifier"):
        worktrees.create_worktree(repo, worktree_id="../escape")
