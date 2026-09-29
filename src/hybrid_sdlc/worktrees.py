"""Creation and durable identity records for isolated Git worktrees."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from hybrid_sdlc.errors import RepositoryError
from hybrid_sdlc.security import verify_repo_root

_WORKTREE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class WorktreeRecord:
    """Identity and baseline for one isolated checkout."""

    worktree_id: str
    repo_root: Path
    path: Path
    baseline_commit: str
    record_path: Path


def _git(repo_root: Path, *args: str) -> str:
    """Run a Git command against the verified source repository."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _registered_worktrees(repo_root: Path) -> set[Path]:
    """Return canonical worktree paths registered with this repository."""
    output = _git(repo_root, "worktree", "list", "--porcelain")
    paths: set[Path] = set()
    for line in output.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line.removeprefix("worktree ")).resolve())
    return paths


def _ensure_storage_directory(
    directory: Path,
    expected_parent: Path,
) -> None:
    """Create or accept a concurrent winner, then validate the directory path."""
    try:
        directory.mkdir()
    except FileExistsError:
        # Another caller may have created this shared storage directory between
        # our prior path checks and mkdir. Validate its final state below.
        pass

    if directory.is_symlink() or not directory.is_dir():
        raise RepositoryError(f"Worktree storage path is not a safe directory: {directory}")
    try:
        resolved = directory.resolve(strict=True)
        parent = expected_parent.resolve(strict=True)
        if resolved.parent != parent:
            raise RepositoryError(f"Worktree storage path escaped its expected parent: {directory}")
    except OSError as exc:
        raise RepositoryError(f"Could not validate worktree storage path: {directory}") from exc


def _remove_attempt_checkout(
    repo_root: Path,
    checkout: Path,
    attempt_dir: Path,
    repo_root_dir: Path,
    attempt_identity: tuple[int, int],
) -> bool:
    """Remove only the checkout inside the unique directory created by this attempt."""
    if (
        attempt_dir.is_symlink()
        or repo_root_dir.is_symlink()
        or repo_root_dir.parent.is_symlink()
        or attempt_dir.resolve() != attempt_dir
        or attempt_dir.parent.resolve() != repo_root_dir.resolve()
        or checkout.parent != attempt_dir
        or checkout.is_symlink()
        or (checkout.exists() and checkout.resolve() != checkout)
    ):
        return False
    try:
        stat = attempt_dir.stat()
    except OSError:
        return False
    if (stat.st_dev, stat.st_ino) != attempt_identity:
        return False

    try:
        registered = checkout.resolve() in _registered_worktrees(repo_root)
    except (OSError, subprocess.SubprocessError):
        # If registration cannot be inspected, leave the path for manual recovery.
        return False

    if registered:
        try:
            _git(repo_root, "worktree", "remove", str(checkout))
        except (OSError, subprocess.SubprocessError):
            # Do not fall back to filesystem deletion after Git removal fails.
            return False
    elif checkout.exists():
        # A failed worktree add may leave files without registering the worktree.
        # This exact child was absent before the command and its parent is private
        # to this attempt, so cleanup cannot address another repository path.
        try:
            shutil.rmtree(checkout)
        except OSError:
            return False
    return not checkout.exists()


def create_worktree(
    repo_root: Path | str,
    *,
    worktree_id: str | None = None,
) -> WorktreeRecord:
    """Create a detached worktree at the source repository's current commit.

    Worktrees and their JSON records are created under the system temporary
    directory, outside the user's checkout. Each attempt owns an exclusive
    directory, and failure cleanup is confined to the checkout created there.
    """
    source = verify_repo_root(repo_root)
    identifier = uuid.uuid4().hex if worktree_id is None else worktree_id
    if _WORKTREE_ID_PATTERN.fullmatch(identifier) is None:
        raise ValueError("worktree_id must be 1-64 safe identifier characters")

    baseline = _git(source, "rev-parse", "--verify", "HEAD^{commit}")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", baseline):
        raise RepositoryError("Git returned an invalid baseline commit identifier")

    repo_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    temp_root = Path(tempfile.gettempdir()).resolve()
    # Keep the path short enough for Windows Git installations that still have
    # MAX_PATH-limited worktree metadata, while retaining an 80-bit repo key.
    shared_root = temp_root / "hsdlc-wt"
    repo_root_dir = shared_root / repo_key[:20]
    attempt_dir: Path | None = None
    attempt_dir_created = False
    attempt_identity: tuple[int, int] | None = None
    checkout_owned = False
    record_created = False

    try:
        _ensure_storage_directory(shared_root, temp_root)
        _ensure_storage_directory(repo_root_dir, shared_root)

        attempt_dir = repo_root_dir / identifier
        # Exclusive mkdir distinguishes an existing path from one created here.
        attempt_dir.mkdir()
        attempt_dir_created = True
        attempt_stat = attempt_dir.stat()
        attempt_identity = (attempt_stat.st_dev, attempt_stat.st_ino)
        checkout = attempt_dir / "checkout"
        record_path = attempt_dir / "worktree.json"

        if checkout.exists() or checkout.is_symlink():
            raise RepositoryError("The new worktree target unexpectedly already exists")
        checkout_owned = True
        _git(source, "worktree", "add", "--detach", str(checkout), baseline)
        record = WorktreeRecord(
            worktree_id=identifier,
            repo_root=source,
            path=checkout.resolve(),
            baseline_commit=baseline,
            record_path=record_path.resolve(),
        )
        with record_path.open("x", encoding="utf-8") as record_file:
            record_created = True
            record_file.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "worktree_id": record.worktree_id,
                        "repo_root": str(record.repo_root),
                        "path": str(record.path),
                        "baseline_commit": record.baseline_commit,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        return record
    except Exception as exc:
        if attempt_dir is not None and attempt_dir_created and attempt_identity is not None:
            checkout = attempt_dir / "checkout"
            checkout_cleaned = False
            if checkout_owned:
                checkout_cleaned = _remove_attempt_checkout(
                    source,
                    checkout,
                    attempt_dir,
                    repo_root_dir,
                    attempt_identity,
                )
            record_path = attempt_dir / "worktree.json"
            try:
                attempt_stat = attempt_dir.stat()
                owns_attempt_dir = (attempt_stat.st_dev, attempt_stat.st_ino) == attempt_identity
            except OSError:
                owns_attempt_dir = False
            if (
                checkout_cleaned
                and owns_attempt_dir
                and record_created
                and record_path.is_file()
                and not record_path.is_symlink()
            ):
                try:
                    record_path.unlink()
                except OSError:
                    pass
            # Remove only if empty; unexpected contents are preserved for inspection.
            if owns_attempt_dir:
                try:
                    attempt_dir.rmdir()
                except OSError:
                    pass
        if isinstance(exc, (ValueError, RepositoryError)):
            raise
        if isinstance(exc, FileExistsError):
            raise RepositoryError("The requested worktree ID already exists") from exc
        if isinstance(exc, (OSError, subprocess.SubprocessError)):
            diagnostic = getattr(exc, "stderr", None)
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode("utf-8", errors="replace")
            message = str(diagnostic).strip() if diagnostic else str(exc)
            raise RepositoryError(f"Could not create isolated Git worktree: {message}") from exc
        raise
