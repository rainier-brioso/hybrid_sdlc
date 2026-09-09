"""Git operations: worktree cleanliness check, locking, and diff capture."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from hybrid_sdlc.artifacts import redact_secrets
from hybrid_sdlc.errors import WorktreeDirtyError, WorktreeLockError
from hybrid_sdlc.models import DiffSummary

MAX_DIFF_BYTES = 500 * 1024


def _resolve_git_path(repo_root: Path, name: str) -> Path:
    """Resolve a path inside Git's actual directory, including linked worktrees."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-path", name],
        capture_output=True,
        text=True,
        check=True,
    )
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def check_worktree_clean(repo_root: Path) -> None:
    """Verify that the repository worktree is completely clean.

    Any tracked modification, staged change, untracked file (outside .hybrid_sdlc/),
    or merge/rebase state will raise WorktreeDirtyError.
    """
    repo_root = repo_root.resolve()

    # Ask Git for its state paths because linked worktrees use a .git pointer file.
    try:
        merge_head = _resolve_git_path(repo_root, "MERGE_HEAD")
        rebase_merge = _resolve_git_path(repo_root, "rebase-merge")
        rebase_apply = _resolve_git_path(repo_root, "rebase-apply")
    except (OSError, subprocess.SubprocessError) as e:
        raise WorktreeDirtyError(
            f"Failed to inspect git state: {e}",
            details={"error": str(e)},
        ) from e

    if merge_head.exists():
        raise WorktreeDirtyError(
            "Repository has a merge in progress",
            details={"state": "merge_in_progress"},
        )
    if rebase_merge.exists() or rebase_apply.exists():
        raise WorktreeDirtyError(
            "Repository has a rebase in progress",
            details={"state": "rebase_in_progress"},
        )

    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.SubprocessError as e:
        raise WorktreeDirtyError(
            f"Failed to check git status: {e}",
            details={"error": str(e)},
        ) from e

    dirty_staged: list[str] = []
    dirty_unstaged: list[str] = []
    dirty_untracked: list[str] = []

    for line in res.stdout.splitlines():
        line = line.rstrip()
        if not line or len(line) < 3:
            continue
        index_status = line[0]
        worktree_status = line[1]
        file_path = line[3:].strip().strip('"')

        # Ignore .hybrid_sdlc/ and .gitignore if it's ignored
        if file_path.startswith(".hybrid_sdlc/") or file_path.startswith(".hybrid_sdlc\\"):
            continue

        if index_status == "?" and worktree_status == "?":
            dirty_untracked.append(file_path)
        else:
            if index_status not in (" ", "?"):
                dirty_staged.append(file_path)
            if worktree_status not in (" ", "?"):
                dirty_unstaged.append(file_path)

    if dirty_staged or dirty_unstaged or dirty_untracked:
        details: dict[str, Any] = {}
        if dirty_staged:
            details["staged_changes"] = dirty_staged
        if dirty_unstaged:
            details["unstaged_changes"] = dirty_unstaged
        if dirty_untracked:
            details["untracked_files"] = dirty_untracked

        categories = []
        if dirty_staged:
            categories.append(f"{len(dirty_staged)} staged")
        if dirty_unstaged:
            categories.append(f"{len(dirty_unstaged)} unstaged")
        if dirty_untracked:
            categories.append(f"{len(dirty_untracked)} untracked")

        raise WorktreeDirtyError(
            f"Repository worktree is dirty: {', '.join(categories)} file(s)",
            details=details,
        )


@contextmanager
def acquire_repo_lock(
    repo_root: Path,
    run_id: str,
    timeout_seconds: float = 0.5,
) -> Iterator[Path]:
    """Acquire an advisory execution lock on the repository.

    Raises WorktreeLockError if another runner holds the lock.
    """
    repo_root = repo_root.resolve()
    lock_dir = repo_root / ".hybrid_sdlc"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "runner.lock"
    meta_file = lock_dir / "runner.lock.meta"

    lock = FileLock(str(lock_file), timeout=timeout_seconds)
    try:
        lock.acquire()
    except Timeout as e:
        meta: dict[str, Any] = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {"raw": "unreadable_metadata"}
        raise WorktreeLockError(
            f"Repository is locked by another running session ({meta.get('run_id', 'unknown')})",
            details={"lock_file": str(lock_file), "held_by": meta},
        ) from e

    try:
        # Write metadata under lock
        meta_data = {
            "run_id": run_id,
            "pid": os.getpid(),
            "repo_root": str(repo_root),
        }
        meta_file.write_text(json.dumps(meta_data), encoding="utf-8")
        yield lock_file
    finally:
        if meta_file.exists():
            try:
                meta_file.unlink()
            except OSError:
                pass
        lock.release()


def get_baseline_commit(repo_root: Path) -> str:
    """Retrieve the current HEAD commit hash as the baseline."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except subprocess.SubprocessError:
        # Repository might have no commits yet (empty repo)
        return "EMPTY_TREE"


def capture_diff_summary(
    repo_root: Path,
    baseline_commit: str = "HEAD",
    patch_output_path: Path | None = None,
) -> DiffSummary:
    """Capture git diff against baseline commit or working tree.

    Extracts changed files, additions, deletions, diff hash, and optionally saves
    the patch file.
    """
    repo_root = repo_root.resolve()

    # Capture tracked changes without mutating the index, then append synthetic
    # no-index patches for untracked files.
    cmd = (
        ["git", "-C", str(repo_root), "diff", baseline_commit]
        if baseline_commit != "EMPTY_TREE"
        else ["git", "-C", str(repo_root), "diff"]
    )
    try:
        diff_res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        diff_parts = [diff_res.stdout]
    except subprocess.SubprocessError:
        diff_parts = []

    untracked_res = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    untracked_files = [
        item.decode("utf-8", errors="replace") for item in untracked_res.stdout.split(b"\0") if item
    ]
    for relative_path in untracked_files:
        untracked_diff = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "diff",
                "--no-index",
                "--binary",
                "--",
                "/dev/null",
                relative_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if untracked_diff.returncode in (0, 1):
            diff_parts.append(untracked_diff.stdout)

    diff_text = "".join(diff_parts)

    # Check if empty
    if not diff_text.strip():
        if patch_output_path:
            patch_output_path.parent.mkdir(parents=True, exist_ok=True)
            patch_output_path.write_text("", encoding="utf-8")
        return DiffSummary(
            diff_hash=hashlib.sha256(b"").hexdigest(),
            changed_files=[],
            additions=0,
            deletions=0,
            patch_file=_relative_artifact_path(patch_output_path, repo_root),
            is_empty=True,
        )

    diff_bytes = diff_text.encode("utf-8")
    diff_hash = hashlib.sha256(diff_bytes).hexdigest()
    is_truncated = len(diff_bytes) > MAX_DIFF_BYTES
    is_binary = "\x00" in diff_text

    # Parse numstat for changed files, additions, deletions
    stat_cmd = (
        ["git", "-C", str(repo_root), "diff", "--numstat", baseline_commit]
        if baseline_commit != "EMPTY_TREE"
        else ["git", "-C", str(repo_root), "diff", "--numstat"]
    )
    changed_files: list[str] = []
    total_add = 0
    total_del = 0

    try:
        stat_res = subprocess.run(
            stat_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        for line in stat_res.stdout.splitlines():
            parts = line.strip().split(maxsplit=2)
            if len(parts) == 3:
                add_s, del_s, fpath = parts
                changed_files.append(fpath)
                if add_s.isdigit():
                    total_add += int(add_s)
                if del_s.isdigit():
                    total_del += int(del_s)
    except subprocess.SubprocessError:
        pass

    for relative_path in untracked_files:
        if relative_path not in changed_files:
            changed_files.append(relative_path)
        file_path = repo_root / relative_path
        try:
            if b"\0" in file_path.read_bytes()[:8192]:
                is_binary = True
            else:
                total_add += len(
                    file_path.read_text(encoding="utf-8", errors="replace").splitlines()
                )
        except OSError:
            pass

    if patch_output_path:
        patch_output_path.parent.mkdir(parents=True, exist_ok=True)
        bounded_patch = diff_bytes[:MAX_DIFF_BYTES].decode("utf-8", errors="replace")
        patch_output_path.write_text(redact_secrets(bounded_patch), encoding="utf-8")

    return DiffSummary(
        diff_hash=diff_hash,
        changed_files=changed_files,
        additions=total_add,
        deletions=total_del,
        patch_file=_relative_artifact_path(patch_output_path, repo_root),
        is_empty=False,
        is_binary=is_binary,
        is_truncated=is_truncated,
    )


def _relative_artifact_path(path: Path | None, repo_root: Path) -> str | None:
    """Return a repository-relative artifact path when one was requested."""
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return str(path.resolve())
