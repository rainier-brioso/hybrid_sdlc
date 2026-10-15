"""Explicit, versioned task policy validation and provenance pinning."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import subprocess
from pathlib import Path
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from hybrid_sdlc.errors import ExitCode, HybridSDLCError, TaskScopeViolationError
from hybrid_sdlc.security import resolve_confined_path

_MAX_POLICY_BYTES = 64 * 1024
_VERSION = "1.0.0"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class TaskPolicyError(HybridSDLCError):
    """A selected policy or its pinned task inputs are invalid."""

    default_code = "TASK_POLICY_INVALID"
    default_exit_code = ExitCode.POLICY_ERROR


class _Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_retries: StrictInt = Field(ge=1, le=10)
    task_timeout_seconds: StrictInt = Field(ge=10, le=3600)
    attempt_timeout_seconds: StrictInt = Field(ge=5, le=1800)
    aider_max_tokens: StrictInt | None = Field(default=None, ge=1)
    aider_reasoning_budget_tokens: StrictInt | None = Field(default=None, ge=0)


class _Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: StrictStr
    task_id: StrictStr
    spec_path: StrictStr
    test_profile: StrictStr
    intended_paths: list[StrictStr] = Field(min_length=1)
    excluded_paths: list[StrictStr] = Field(default_factory=list)
    limits: _Limits


class PolicyProvenance(BaseModel):
    """Stable identity of the selected policy and contract at a Git baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_path: str
    policy_sha256: str
    spec_sha256: str
    baseline_commit: str


class PolicyRunOptions(TypedDict, total=False):
    """Optional runner arguments present only for an explicitly selected policy."""

    policy_path: str
    policy_provenance: PolicyProvenance | None
    test_profile: str


class TaskScope(BaseModel):
    """Immutable allowed/excluded path sets loaded from a pinned policy blob."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intended_paths: frozenset[str]
    excluded_paths: frozenset[str]


def load_task_scope(repo_root: Path, provenance: PolicyProvenance) -> TaskScope:
    """Load scope from the policy blob at its pinned commit and verify its hash."""
    try:
        head = _git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
        if head != provenance.baseline_commit:
            raise TaskPolicyError("Repository baseline changed before task scope was captured")
        policy_bytes = _git(
            repo_root, "show", f"{provenance.baseline_commit}:{provenance.policy_path}"
        )
        if hashlib.sha256(policy_bytes).hexdigest() != provenance.policy_sha256:
            raise TaskPolicyError("Pinned task policy content does not match its recorded hash")
        policy = _parse_policy(policy_bytes)
        _validate_path_lists(policy)
        return TaskScope(
            intended_paths=frozenset(policy.intended_paths),
            excluded_paths=frozenset(policy.excluded_paths),
        )
    except TaskPolicyError:
        raise
    except Exception as exc:
        raise TaskPolicyError("Could not load pinned task scope") from exc


def validate_task_scope_paths(scope: TaskScope, paths: list[str] | tuple[str, ...]) -> None:
    """Reject paths excluded explicitly or absent from the literal allow-list."""
    violations = []
    for path in sorted(set(paths)):
        if path in scope.excluded_paths:
            violations.append({"path": path, "reason": "excluded"})
        elif path not in scope.intended_paths:
            violations.append({"path": path, "reason": "not_intended"})
    if violations:
        raise TaskScopeViolationError(
            "Task changed paths outside its declared scope",
            details={"violations": violations},
        )


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaskPolicyError("Could not verify committed task policy inputs") from exc


def _literal_path(value: str, *, field: str) -> str:
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or _CONTROL.search(value)
        or ":" in value
        or any(char in value for char in "*?[]{}")
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise TaskPolicyError(f"Invalid {field} in task policy")
    return value


def _entry_blob(root: Path, relative: str, baseline: str) -> bytes:
    """Return the committed regular-file blob, rejecting aliases and symlinks."""

    _literal_path(relative, field="repository path")
    try:
        candidate = root / relative
        original_info = candidate.lstat()
        if (
            stat.S_ISLNK(original_info.st_mode)
            or getattr(original_info, "st_file_attributes", 0) & 0x400
        ):
            raise TaskPolicyError("Task policy inputs must be regular files, not links")
        absolute = resolve_confined_path(relative, root, must_exist=True)
        if absolute.relative_to(root).as_posix() != relative:
            raise TaskPolicyError("Task policy paths must use their canonical repository spelling")
        info = absolute.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise TaskPolicyError("Task policy inputs must be regular files, not links")
        if not stat.S_ISREG(info.st_mode):
            raise TaskPolicyError("Task policy inputs must be regular files")

        tree = _git(root, "ls-tree", "-z", baseline, "--", relative)
        expected = relative.encode("utf-8")
        entries = [item for item in tree.split(b"\0") if item]
        matching = [item for item in entries if item.partition(b"\t")[2] == expected]
        if len(matching) != 1:
            raise TaskPolicyError("Task policy inputs must be committed regular files")
        metadata = matching[0].partition(b"\t")[0].split()
        if (
            len(metadata) != 3
            or metadata[0] not in (b"100644", b"100755")
            or metadata[1] != b"blob"
        ):
            raise TaskPolicyError("Task policy inputs must be committed regular files")
        current_oid = subprocess.run(
            ["git", "-C", str(root), "hash-object", f"--path={relative}", "--stdin"],
            input=absolute.read_bytes(),
            check=True,
            capture_output=True,
        )
        if current_oid.stdout.strip() != metadata[2]:
            raise TaskPolicyError("Task policy inputs must be committed and unchanged")
        return _git(root, "show", f"{baseline}:{relative}")
    except TaskPolicyError:
        raise
    except Exception as exc:
        raise TaskPolicyError(
            "Task policy inputs must be committed, confined, unchanged files"
        ) from exc


def _duplicate_free_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaskPolicyError("Task policy JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise TaskPolicyError("Task policy JSON contains a non-finite number")


def _parse_policy(data: bytes) -> _Policy:
    try:
        raw = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_duplicate_free_object,
            parse_constant=_reject_constant,
        )
        policy = _Policy.model_validate(raw)
    except TaskPolicyError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise TaskPolicyError("Task policy JSON does not match schema version 1.0.0") from exc
    if policy.schema_version != _VERSION:
        raise TaskPolicyError("Unsupported task policy schema_version")
    return policy


def _validate_path_lists(policy: _Policy) -> None:
    intended = [_literal_path(path, field="intended_paths entry") for path in policy.intended_paths]
    excluded = [_literal_path(path, field="excluded_paths entry") for path in policy.excluded_paths]
    if len(set(intended)) != len(intended) or len(set(excluded)) != len(excluded):
        raise TaskPolicyError("Task policy path lists must not contain duplicates")
    if set(intended).intersection(excluded):
        raise TaskPolicyError("A task policy path cannot be both intended and excluded")


def _check_limits(policy: _Policy, *, max_retries: int, config: Any) -> None:
    requested: dict[str, int | None] = {
        "max_retries": max_retries,
        "task_timeout_seconds": config.task_timeout_seconds,
        "attempt_timeout_seconds": config.attempt_timeout_seconds,
        "aider_max_tokens": config.aider_max_tokens,
        "aider_reasoning_budget_tokens": config.aider_reasoning_budget_tokens,
    }
    for key, ceiling in policy.limits.model_dump(exclude_unset=False).items():
        if ceiling is None:
            continue
        effective = requested[key]
        if effective is None:
            raise TaskPolicyError(f"Task policy ceiling for {key} cannot be checked")
        if isinstance(effective, bool) or not isinstance(effective, (int, float)):
            raise TaskPolicyError(f"Effective {key} is not a checkable integer")
        if isinstance(effective, float) and not math.isfinite(effective):
            raise TaskPolicyError(f"Effective {key} is not a checkable integer")
        if key in {
            "max_retries",
            "aider_max_tokens",
            "aider_reasoning_budget_tokens",
        } and not isinstance(effective, int):
            raise TaskPolicyError(f"Effective {key} is not a checkable integer")
        if effective > ceiling:
            raise TaskPolicyError(f"Effective {key} exceeds the task policy ceiling")


def validate_task_policy(
    *,
    repo_root: Path,
    policy_path: str | Path | None,
    spec_path: str,
    task_id: str,
    test_profile: str,
    max_retries: int,
    config: Any,
    expected: PolicyProvenance | None = None,
) -> PolicyProvenance | None:
    """Validate an explicitly selected policy against committed input blobs."""

    if policy_path is None:
        if expected is not None:
            raise TaskPolicyError("Pinned task policy is missing")
        return None

    raw_path = policy_path.as_posix() if isinstance(policy_path, Path) else policy_path
    policy_relative = _literal_path(raw_path, field="policy path")
    if "\\" in raw_path or Path(raw_path).is_absolute():
        raise TaskPolicyError("Policy path must be repository-relative with '/' separators")
    try:
        policy_file = resolve_confined_path(policy_relative, repo_root, must_exist=True)
        if policy_file.stat().st_size > _MAX_POLICY_BYTES:
            raise TaskPolicyError("Task policy input exceeds the 64 KiB limit")
        baseline = _git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
        policy_bytes = _entry_blob(repo_root, policy_relative, baseline)
        if len(policy_bytes) > _MAX_POLICY_BYTES:
            raise TaskPolicyError("Task policy input exceeds the 64 KiB limit")
        policy = _parse_policy(policy_bytes)
        _validate_path_lists(policy)
        normalized_spec = _literal_path(spec_path.replace("\\", "/"), field="spec path")
        if (
            policy.task_id != task_id
            or policy.spec_path != normalized_spec
            or policy.test_profile != test_profile
        ):
            raise TaskPolicyError("Task policy identity does not match the requested task")
        _check_limits(policy, max_retries=max_retries, config=config)
        spec_bytes = _entry_blob(repo_root, normalized_spec, baseline)
        if _git(repo_root, "rev-parse", "HEAD").decode("ascii").strip() != baseline:
            raise TaskPolicyError("Repository baseline changed during task policy validation")
        provenance = PolicyProvenance(
            policy_path=policy_relative,
            policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
            spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
            baseline_commit=baseline,
        )
    except TaskPolicyError:
        raise
    except Exception as exc:
        raise TaskPolicyError("Could not validate selected task policy") from exc

    if expected is not None and provenance != expected:
        raise TaskPolicyError("Task policy, contract, or baseline changed after submission")
    return provenance
