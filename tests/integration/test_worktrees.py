"""Integration coverage for safe isolated worktree creation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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


def test_export_worktree_result_writes_exact_patch_without_changing_index_or_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="result-export")

    (repo / "README.md").write_text("source checkout change\n", encoding="utf-8")
    (repo / "source-untracked.txt").write_text("keep this file\n", encoding="utf-8")
    (record.path / "README.md").write_text("tracked staged and unstaged\n", encoding="utf-8")
    _git(record.path, "add", "README.md")
    (record.path / "README.md").write_text("tracked final bytes\n", encoding="utf-8")
    (record.path / "space and café.txt").write_bytes(b"new file\x00with binary bytes\xff\n")
    index_before = _git(record.path, "diff", "--cached", "--binary")
    source_before = _checkout_snapshot(repo)
    source_head_before = _git(repo, "rev-parse", "HEAD")
    source_index_before = _git(repo, "diff", "--cached", "--binary")

    result = worktrees.export_worktree_result(record)

    assert result.baseline_commit == record.baseline_commit
    assert result.commit_hash is None
    assert result.changed_paths == ("README.md", "space and café.txt")
    assert result.patch_path == record.record_path.parent / "result.patch"
    assert result.patch_path.is_file()
    patch_bytes = result.patch_path.read_bytes()
    assert b"GIT binary patch" in patch_bytes
    assert _git(record.path, "diff", "--cached", "--binary") == index_before
    assert _checkout_snapshot(repo) == source_before
    assert _git(repo, "rev-parse", "HEAD") == source_head_before
    assert _git(repo, "diff", "--cached", "--binary") == source_index_before

    target = tmp_path / "patch target"
    _git(repo, "worktree", "add", "--detach", str(target), record.baseline_commit)
    subprocess.run(
        ["git", "-C", str(target), "apply", "--binary", "--check", str(result.patch_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "apply", "--binary", str(result.patch_path)],
        check=True,
        capture_output=True,
    )
    assert (target / "README.md").read_text(encoding="utf-8") == "tracked final bytes\n"
    assert (target / "space and café.txt").read_bytes() == b"new file\x00with binary bytes\xff\n"


def test_exported_patch_conflict_fails_without_changing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="conflicting-result")
    (record.path / "README.md").write_text("agent version\n", encoding="utf-8")
    result = worktrees.export_worktree_result(record)

    target = tmp_path / "conflict target"
    _git(repo, "worktree", "add", "--detach", str(target), record.baseline_commit)
    (target / "README.md").write_text("reviewer version\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "conflicting target change")
    before = (target / "README.md").read_bytes()
    failed = subprocess.run(
        ["git", "-C", str(target), "apply", "--binary", "--check", str(result.patch_path)],
        capture_output=True,
    )

    assert failed.returncode != 0
    assert (target / "README.md").read_bytes() == before


def test_export_result_atomic_replacement_preserves_previous_patch_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="atomic-result")
    (record.path / "README.md").write_text("first result\n", encoding="utf-8")
    result = worktrees.export_worktree_result(record)
    original_patch = result.patch_path.read_bytes()
    (record.path / "README.md").write_text("second result\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(worktrees.os, "replace", fail_replace)
    with pytest.raises(
        worktrees.RepositoryError, match="Could not export isolated worktree result"
    ):
        worktrees.export_worktree_result(record)

    assert result.patch_path.read_bytes() == original_patch
    assert not tuple(result.patch_path.parent.glob(".result-patch-*"))


def test_export_result_git_metadata_failure_preserves_previous_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="metadata-failure-result")
    (record.path / "README.md").write_text("first result\n", encoding="utf-8")
    result = worktrees.export_worktree_result(record)
    original_patch = result.patch_path.read_bytes()
    (record.path / "README.md").write_text("second result\n", encoding="utf-8")
    original_run = subprocess.run

    def fail_name_only(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if "--name-only" in args:
            raise subprocess.CalledProcessError(1, args, stderr=b"simulated metadata failure")
        return original_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worktrees.subprocess, "run", fail_name_only)
    with pytest.raises(
        worktrees.RepositoryError, match="Could not export isolated worktree result"
    ):
        worktrees.export_worktree_result(record)

    assert result.patch_path.read_bytes() == original_patch


def test_export_untracked_snapshot_is_not_reopened_after_parent_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    if os.name == "nt":
        pytest.skip("The test requires directory symlink privileges on Windows")

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="snapshot-path-swap")
    nested = record.path / "nested"
    nested.mkdir()
    (nested / "draft.txt").write_text("inside checkout\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "draft.txt").write_text("outside secret\n", encoding="utf-8")
    saved_directory = record.path / "nested-saved"
    original_run = subprocess.run
    swapped = False

    def swap_before_git_diff(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal swapped
        if "--no-index" in args and not swapped:
            nested.rename(saved_directory)
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worktrees.subprocess, "run", swap_before_git_diff)
    try:
        result = worktrees.export_worktree_result(record)
    finally:
        if nested.is_symlink():
            nested.unlink()
        if saved_directory.exists():
            saved_directory.rename(nested)

    assert swapped
    assert b"inside checkout" in result.patch_path.read_bytes()
    assert b"outside secret" not in result.patch_path.read_bytes()


def test_export_result_reports_only_explicit_scoped_commit_and_includes_later_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import git_tools, worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="committed-result")
    (record.path / "README.md").write_text("committed version\n", encoding="utf-8")
    commit_hash = git_tools.create_scoped_commit(
        record,
        "HSDLC-054",
        "Export committed result",
        commit_requested=True,
        tests_passed=True,
        policy_passed=True,
    )
    assert commit_hash is not None
    (record.path / "later.txt").write_text("after commit\n", encoding="utf-8")

    implicit_commit = worktrees.export_worktree_result(record)
    explicit_commit = worktrees.export_worktree_result(record, scoped_commit_hash=commit_hash)

    assert implicit_commit.commit_hash is None
    assert explicit_commit.commit_hash == commit_hash
    assert explicit_commit.changed_paths == ("README.md", "later.txt")
    target = tmp_path / "committed patch target"
    _git(repo, "worktree", "add", "--detach", str(target), record.baseline_commit)
    subprocess.run(
        ["git", "-C", str(target), "apply", "--binary", str(explicit_commit.patch_path)],
        check=True,
        capture_output=True,
    )
    assert (target / "README.md").read_text(encoding="utf-8") == "committed version\n"
    assert (target / "later.txt").read_text(encoding="utf-8") == "after commit\n"


def test_export_result_rejects_commit_without_scoped_task_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="unscoped-commit")
    (record.path / "README.md").write_text("manual commit\n", encoding="utf-8")
    _git(record.path, "add", "README.md")
    _git(record.path, "commit", "-m", "manual commit")
    commit_hash = _git(record.path, "rev-parse", "HEAD")

    with pytest.raises(worktrees.RepositoryError, match="scoped task ID subject"):
        worktrees.export_worktree_result(record, scoped_commit_hash=commit_hash)


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


def test_rollback_restores_baseline_and_removes_untracked_unicode_and_ignored_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    (repo / ".gitignore").write_text("ignored file.txt\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore test fixture")
    (repo / "README.md").write_text("user checkout change\n", encoding="utf-8")
    (repo / "user file.txt").write_text("leave untouched", encoding="utf-8")
    temp_root = tmp_path / "external ü space"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    record = worktrees.create_worktree(repo, worktree_id="rollback-basic")
    (record.path / "README.md").write_text("changed\n", encoding="utf-8")
    _git(record.path, "add", "README.md")
    (record.path / "nested ü space").mkdir()
    (record.path / "nested ü space" / "new file.txt").write_text("untracked", encoding="utf-8")
    (record.path / "ignored file.txt").write_text("ignored", encoding="utf-8")

    worktrees.rollback_worktree(record)

    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert (record.path / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert _git(record.path, "status", "--porcelain") == ""
    assert not (record.path / "nested ü space").exists()
    assert not (record.path / "ignored file.txt").exists()
    assert _checkout_snapshot(repo) == before


def test_rollback_removes_nested_untracked_repository_without_touching_checkout_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    record = worktrees.create_worktree(repo, worktree_id="rollback-nested-repo")

    outside = tmp_path / "outside checkout.txt"
    outside.write_text("preserve", encoding="utf-8")
    nested = record.path / "nested repository"
    nested.mkdir()
    _git(nested, "init")
    _git(nested, "config", "user.name", "Nested Tester")
    _git(nested, "config", "user.email", "nested@local")
    deeper = nested / "nested directory"
    deeper.mkdir()
    (deeper / "nested file.txt").write_text("remove recursively", encoding="utf-8")
    _git(nested, "add", ".")
    _git(nested, "commit", "-m", "nested repo")
    listing = subprocess.run(
        ["git", "-C", str(record.path), "ls-files", "--others", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    assert b"nested repository" in listing

    worktrees.rollback_worktree(record)

    assert not nested.exists()
    assert outside.read_text(encoding="utf-8") == "preserve"
    assert _git(record.path, "status", "--porcelain") == ""
    assert _checkout_snapshot(repo) == before


def test_rollback_rejects_caller_forgery_and_persisted_record_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="rollback-identity")
    (record.path / "README.md").write_text("preserve on rejection", encoding="utf-8")

    with pytest.raises(worktrees.RepositoryError, match="expected isolated checkout"):
        worktrees.rollback_worktree(replace(record, path=repo))

    data = json.loads(record.record_path.read_text(encoding="utf-8"))
    data["baseline_commit"] = "0" * 40
    record.record_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(worktrees.RepositoryError, match="persisted identity"):
        worktrees.rollback_worktree(record)
    assert (record.path / "README.md").read_text(encoding="utf-8") == "preserve on rejection"


def test_rollback_fails_closed_if_checkout_path_is_swapped_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    record = worktrees.create_worktree(repo, worktree_id="rollback-race")
    extra = record.path / "keep until detected.txt"
    extra.write_text("preserve", encoding="utf-8")
    displaced = record.path.parent / "checkout.displaced"
    original_validate = worktrees._validate_checkout_identity
    swapped = False

    def swap_then_validate(
        source: Path,
        attempt_dir: Path,
        checkout: Path,
        attempt_identity: tuple[int, int],
        checkout_identity: tuple[int, int],
    ) -> None:
        nonlocal swapped
        if not swapped:
            checkout.rename(displaced)
            checkout.mkdir()
            swapped = True
        original_validate(source, attempt_dir, checkout, attempt_identity, checkout_identity)

    monkeypatch.setattr(worktrees, "_validate_checkout_identity", swap_then_validate)
    try:
        with pytest.raises(worktrees.RepositoryError, match="identity changed"):
            worktrees.rollback_worktree(record)
        assert (displaced / extra.name).read_text(encoding="utf-8") == "preserve"
        assert _checkout_snapshot(repo) == before
    finally:
        if record.path.exists():
            record.path.rmdir()
        if displaced.exists():
            displaced.rename(record.path)


def test_rollback_removes_symlink_leaf_without_touching_external_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="rollback-symlink")
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = record.path / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    worktrees.rollback_worktree(record)

    assert not link.exists() and not link.is_symlink()
    assert marker.read_text(encoding="utf-8") == "keep"


def test_create_worktree_rejects_repository_baseline_with_gitlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    linked_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{linked_commit},nested-submodule")
    _git(repo, "commit", "-m", "commit test gitlink")
    before = _checkout_snapshot(repo)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))

    with pytest.raises(worktrees.RepositoryError, match="do not yet support.*submodules"):
        worktrees.create_worktree(repo, worktree_id="submodule-baseline")

    assert _checkout_snapshot(repo) == before
    assert not (temp_root / "hsdlc-wt").exists()


def test_rollback_rejects_gitlink_in_index_before_any_rollback_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    before = _checkout_snapshot(repo)
    record = worktrees.create_worktree(repo, worktree_id="rollback-submodule-index")
    linked_commit = record.baseline_commit
    _git(
        record.path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{linked_commit},nested-submodule",
    )
    (record.path / "README.md").write_text("preserve before rejection", encoding="utf-8")
    staged_index = _git(record.path, "ls-files", "--stage")

    with pytest.raises(worktrees.RepositoryError, match="does not support.*submodules"):
        worktrees.rollback_worktree(record)

    assert _git(record.path, "ls-files", "--stage") == staged_index
    assert (record.path / "README.md").read_text(encoding="utf-8") == "preserve before rejection"
    assert _checkout_snapshot(repo) == before


def test_rollback_moves_only_detached_head_back_to_recorded_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="rollback-head")
    (record.path / "README.md").write_text("temporary commit\n", encoding="utf-8")
    _git(record.path, "add", "README.md")
    _git(record.path, "commit", "-m", "temporary worktree commit")
    source_head = _git(repo, "rev-parse", "HEAD")

    worktrees.rollback_worktree(record)

    assert _git(record.path, "rev-parse", "HEAD") == record.baseline_commit
    assert (record.path / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert _git(repo, "rev-parse", "HEAD") == source_head


def test_rollback_refuses_to_move_a_branch_attached_to_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="rollback-branch")
    _git(record.path, "switch", "-c", "rollback-test-branch")
    (record.path / "README.md").write_text("preserve", encoding="utf-8")
    branch_head = _git(record.path, "rev-parse", "HEAD")

    with pytest.raises(worktrees.RepositoryError, match="move a branch"):
        worktrees.rollback_worktree(record)

    assert _git(record.path, "rev-parse", "HEAD") == branch_head
    assert (record.path / "README.md").read_text(encoding="utf-8") == "preserve"


def test_rollback_can_be_retried_after_interrupted_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import worktrees

    repo = _create_repo(tmp_path)
    temp_root = tmp_path / "external"
    temp_root.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temp_root))
    record = worktrees.create_worktree(repo, worktree_id="rollback-retry")
    (record.path / "README.md").write_text("changed", encoding="utf-8")
    extra = record.path / "extra.txt"
    extra.write_text("remove", encoding="utf-8")
    original_run = worktrees.subprocess.run
    failed = False

    def fail_once_on_restore(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal failed
        if "restore" in args and not failed:
            failed = True
            raise subprocess.CalledProcessError(1, args, stderr=b"simulated interruption")
        return original_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(worktrees.subprocess, "run", fail_once_on_restore)
    with pytest.raises(worktrees.RepositoryError, match="simulated interruption"):
        worktrees.rollback_worktree(record)

    worktrees.rollback_worktree(record)
    assert _git(record.path, "status", "--porcelain") == ""
    assert (record.path / "README.md").read_text(encoding="utf-8") == "initial\n"
    assert not extra.exists()
