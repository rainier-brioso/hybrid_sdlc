"""Creation and durable identity records for isolated Git worktrees."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

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


def _registered_linked_worktree(repo_root: Path, checkout: Path) -> bool:
    """Confirm checkout is a non-bare linked worktree of repo_root."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain", "-z"],
        capture_output=True,
        check=True,
    )
    fields = result.stdout.split(b"\0")
    current_path: Path | None = None
    current_bare = False
    for raw in fields:
        if raw.startswith(b"worktree "):
            if current_path == checkout and not current_bare:
                return True
            current_path = Path(os.fsdecode(raw[len(b"worktree ") :]))
            current_bare = False
        elif raw == b"bare":
            current_bare = True
    return current_path == checkout and not current_bare


def _tree_has_gitlinks(repo_root: Path, revision: str) -> bool:
    """Return whether a commit tree contains a submodule gitlink."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-tree", "-r", "--full-tree", "-z", revision],
        capture_output=True,
        check=True,
    )
    return any(
        entry.split(b" ", 1)[0] == b"160000" for entry in result.stdout.split(b"\0") if entry
    )


def _index_has_gitlinks(checkout: Path) -> bool:
    """Return whether the index contains a submodule gitlink."""
    result = subprocess.run(
        ["git", "-C", str(checkout), "ls-files", "--stage", "-z"],
        capture_output=True,
        check=True,
    )
    return any(
        entry.split(b" ", 1)[0] == b"160000" for entry in result.stdout.split(b"\0") if entry
    )


def _validate_checkout_identity(
    source: Path,
    attempt_dir: Path,
    checkout: Path,
    attempt_identity: tuple[int, int],
    checkout_identity: tuple[int, int],
) -> None:
    """Revalidate the same canonical, non-symlink linked checkout before mutation."""
    for path in (attempt_dir.parent.parent, attempt_dir.parent, attempt_dir, checkout):
        if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
            raise RepositoryError(f"Rollback path changed or is not canonical: {path}")
    try:
        attempt_stat = attempt_dir.stat()
        checkout_stat = checkout.stat()
    except OSError as exc:
        raise RepositoryError("Rollback checkout identity is no longer available") from exc
    if (attempt_stat.st_dev, attempt_stat.st_ino) != attempt_identity:
        raise RepositoryError("Rollback attempt directory identity changed")
    if (checkout_stat.st_dev, checkout_stat.st_ino) != checkout_identity:
        raise RepositoryError("Rollback checkout identity changed")
    try:
        registered = _registered_linked_worktree(source, checkout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryError("Could not revalidate registered rollback worktree") from exc
    if not registered:
        raise RepositoryError("Rollback checkout is no longer registered as a linked worktree")


def _verified_rollback_record(
    record: WorktreeRecord,
) -> tuple[Path, Path, Path, tuple[int, int], tuple[int, int]]:
    """Validate durable identity, expected storage, and linked checkout before mutation."""
    source = verify_repo_root(record.repo_root)
    if record.repo_root != source:
        raise RepositoryError("Rollback record repository path is not canonical")
    if _WORKTREE_ID_PATTERN.fullmatch(record.worktree_id) is None:
        raise RepositoryError("Rollback record has an invalid worktree ID")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", record.baseline_commit):
        raise RepositoryError("Rollback record has an invalid baseline commit")

    repo_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    storage_root = Path(tempfile.gettempdir()).resolve() / "hsdlc-wt"
    repo_storage = storage_root / repo_key[:20]
    attempt_dir = repo_storage / record.worktree_id
    expected_checkout = attempt_dir / "checkout"
    expected_record = attempt_dir / "worktree.json"
    if record.path != expected_checkout or record.record_path != expected_record:
        raise RepositoryError("Rollback record does not identify its expected isolated checkout")

    # Reject symlinked ancestors and non-canonical paths before reading metadata.
    for directory in (storage_root, repo_storage, attempt_dir):
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or directory.resolve(strict=True) != directory
        ):
            raise RepositoryError(f"Rollback storage path is not a safe directory: {directory}")
    if expected_checkout.is_symlink() or not expected_checkout.is_dir():
        raise RepositoryError("Rollback checkout is missing or is a symlink")
    if expected_checkout.resolve(strict=True) != expected_checkout:
        raise RepositoryError("Rollback checkout path is not canonical")
    if expected_record.is_symlink() or not expected_record.is_file():
        raise RepositoryError("Rollback identity record is missing or is a symlink")

    try:
        persisted = json.loads(expected_record.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepositoryError("Could not read durable rollback identity") from exc
    expected_data = {
        "schema_version": 1,
        "worktree_id": record.worktree_id,
        "repo_root": str(record.repo_root),
        "path": str(record.path),
        "baseline_commit": record.baseline_commit,
    }
    if persisted != expected_data:
        raise RepositoryError("Rollback record does not match its persisted identity")

    try:
        baseline = _git(source, "rev-parse", "--verify", f"{record.baseline_commit}^{{commit}}")
        linked = _registered_linked_worktree(source, expected_checkout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryError("Could not verify registered rollback worktree") from exc
    if baseline.lower() != record.baseline_commit.lower() or not linked:
        raise RepositoryError("Rollback checkout is not registered at the recorded baseline")
    try:
        attempt_stat = attempt_dir.stat()
        checkout_stat = expected_checkout.stat()
    except OSError as exc:
        raise RepositoryError("Could not capture rollback checkout identity") from exc
    return (
        source,
        attempt_dir,
        expected_checkout,
        (attempt_stat.st_dev, attempt_stat.st_ino),
        (checkout_stat.st_dev, checkout_stat.st_ino),
    )


def _remove_untracked_entries(checkout: Path, validate: Callable[[], None]) -> None:
    """Remove untracked and ignored entries without following symlinks."""
    untracked = subprocess.run(
        ["git", "-C", str(checkout), "ls-files", "--others", "-z"],
        capture_output=True,
        check=True,
    )
    ignored = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ],
        capture_output=True,
        check=True,
    )
    candidates: set[Path] = set()
    for listing in (untracked.stdout, ignored.stdout):
        for raw in listing.split(b"\0"):
            if not raw:
                continue
            relative = Path(os.fsdecode(raw.replace(b"/", os.fsencode(os.sep))))
            if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
                raise RepositoryError("Git returned an unsafe untracked path")
            if relative.parts and relative.parts[0] == ".git":
                raise RepositoryError("Refusing to remove Git worktree metadata")
            candidates.add(checkout / relative)

    for candidate in candidates:
        try:
            relative = candidate.relative_to(checkout)
            current = checkout
            for part in relative.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise RepositoryError("Untracked path traverses a symlink")
            if candidate.is_symlink() or candidate.is_file():
                _unlink_untracked_leaf(checkout, candidate, validate)
            elif candidate.is_dir():
                validate()
                _assert_untracked_path_is_safe(checkout, candidate, allow_leaf_symlink=False)
                _remove_tree_without_following_symlinks(checkout, candidate, validate)
        except OSError as exc:
            raise RepositoryError(f"Could not remove untracked path: {candidate}") from exc

    # Empty directories are not represented in Git's index. Walk without following
    # symlinks and remove only directories that are empty after leaf cleanup.
    for root, directories, _files in os.walk(checkout, topdown=False, followlinks=False):
        root_path = Path(root)
        directories[:] = [name for name in directories if name != ".git"]
        for name in directories:
            directory = root_path / name
            if directory.is_symlink():
                continue
            try:
                validate()
                _assert_untracked_path_is_safe(checkout, directory, allow_leaf_symlink=False)
                directory.rmdir()
            except OSError:
                pass


def _remove_tree_without_following_symlinks(
    checkout: Path, path: Path, validate: Callable[[], None]
) -> None:
    """Remove an untracked directory tree using leaf operations only."""
    validate()
    _assert_untracked_path_is_safe(checkout, path, allow_leaf_symlink=False)
    for entry in os.scandir(path):
        child = Path(entry.path)
        if entry.is_symlink():
            _unlink_untracked_leaf(checkout, child, validate)
        elif entry.is_dir(follow_symlinks=False):
            validate()
            _remove_tree_without_following_symlinks(checkout, child, validate)
        else:
            _unlink_untracked_leaf(checkout, child, validate)
    validate()
    _assert_untracked_path_is_safe(checkout, path, allow_leaf_symlink=False)
    path.rmdir()


def _unlink_untracked_leaf(checkout: Path, path: Path, validate: Callable[[], None]) -> None:
    """Unlink one verified leaf, clearing Windows read-only attributes if needed."""
    validate()
    _assert_untracked_path_is_safe(checkout, path, allow_leaf_symlink=True)
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        if os.name != "nt" or path.is_symlink():
            raise
        path.chmod(path.stat().st_mode | stat.S_IWRITE)
        validate()
        _assert_untracked_path_is_safe(checkout, path, allow_leaf_symlink=True)
        path.unlink(missing_ok=True)


def _assert_untracked_path_is_safe(checkout: Path, path: Path, *, allow_leaf_symlink: bool) -> None:
    """Reject symlink traversal and path replacement before a filesystem mutation."""
    try:
        relative = path.relative_to(checkout)
    except ValueError as exc:
        raise RepositoryError("Untracked path escaped the verified checkout") from exc
    current = checkout
    for index, part in enumerate(relative.parts):
        current = current / part
        is_leaf = index == len(relative.parts) - 1
        if current.is_symlink() and not (is_leaf and allow_leaf_symlink):
            raise RepositoryError("Untracked path traverses a symlink")
    if not allow_leaf_symlink and path.resolve(strict=True) != path:
        raise RepositoryError("Untracked path is not canonical")


def rollback_worktree(record: WorktreeRecord) -> None:
    """Restore a verified isolated checkout and its detached HEAD to its baseline."""
    source, attempt_dir, checkout, attempt_identity, checkout_identity = _verified_rollback_record(
        record
    )
    lock = FileLock(str(attempt_dir.parent / ".rollback.lock"), timeout=30)
    try:
        with lock:
            _rollback_verified_worktree(
                record, source, attempt_dir, checkout, attempt_identity, checkout_identity
            )
    except Timeout as exc:
        raise RepositoryError("Another rollback for this repository is still running") from exc


def _rollback_verified_worktree(
    record: WorktreeRecord,
    source: Path,
    attempt_dir: Path,
    checkout: Path,
    attempt_identity: tuple[int, int],
    checkout_identity: tuple[int, int],
) -> None:
    """Perform rollback under the per-repository cooperative rollback lock."""

    def validate_identity() -> None:
        _validate_checkout_identity(
            source, attempt_dir, checkout, attempt_identity, checkout_identity
        )

    validate_identity()
    try:
        has_submodules = _tree_has_gitlinks(source, record.baseline_commit) or _index_has_gitlinks(
            checkout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryError(
            "Could not inspect rollback baseline or index for submodules"
        ) from exc
    if has_submodules:
        raise RepositoryError("Rollback does not support repositories or indexes with submodules")

    # A symbolic HEAD would make update-ref alter a branch. Refuse before touching
    # files; only a detached linked worktree is eligible for rollback.
    symbolic_head = subprocess.run(
        ["git", "-C", str(checkout), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        check=False,
    )
    if symbolic_head.returncode == 0:
        raise RepositoryError("Rollback refuses to move a branch attached to the worktree")
    if symbolic_head.returncode != 1:
        raise RepositoryError("Could not verify detached rollback HEAD")

    try:
        current_head = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}")
        if current_head.lower() != record.baseline_commit.lower():
            validate_identity()
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "update-ref",
                    "--no-deref",
                    "HEAD",
                    record.baseline_commit,
                    current_head,
                ],
                capture_output=True,
                check=True,
            )
        _remove_untracked_entries(checkout, validate_identity)
        validate_identity()
        subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "restore",
                f"--source={record.baseline_commit}",
                "--staged",
                "--worktree",
                "--",
                ":/",
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        diagnostic = getattr(exc, "stderr", None)
        detail = os.fsdecode(diagnostic).strip() if diagnostic else str(exc)
        raise RepositoryError(f"Could not roll back isolated worktree: {detail}") from exc


def _ensure_storage_directory(
    directory: Path,
    expected_parent: Path,
) -> None:
    """Create or accept a concurrent winner, then validate the directory path."""
    try:
        directory.mkdir(mode=0o700)
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
        if os.name != "nt":
            directory_stat = directory.stat()
            getuid = getattr(os, "getuid", None)
            if getuid is None or directory_stat.st_uid != getuid():
                raise RepositoryError(
                    f"Worktree storage is not owned by the current user: {directory}"
                )
            if stat.S_IMODE(directory_stat.st_mode) & 0o077:
                directory.chmod(0o700)
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
    try:
        contains_gitlinks = _tree_has_gitlinks(source, baseline)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryError("Could not inspect baseline for submodules") from exc
    if contains_gitlinks:
        raise RepositoryError("Isolated worktrees do not yet support repositories with submodules")

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
        attempt_dir.mkdir(mode=0o700)
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
