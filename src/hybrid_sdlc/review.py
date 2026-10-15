"""Exact-artifact review records and controller-run acceptance checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock, Timeout
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from hybrid_sdlc.aider_runner import (
    _isolated_test_executable,
    run_test_profile,
)
from hybrid_sdlc.artifacts import redact_secrets
from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import load_config
from hybrid_sdlc.errors import CommandPolicyError, ExitCode, HybridSDLCError
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.security import verify_repo_root
from hybrid_sdlc.task_policy import PolicyProvenance, load_task_scope, validate_task_scope_paths
from hybrid_sdlc.worktrees import (
    WorktreeRecord,
    _verified_rollback_record,
    create_worktree,
    export_worktree_result,
    rollback_worktree,
)

_REVIEW_VERSION = "1.0.0"
_MAX_JSON_BYTES = 256 * 1024
_MAX_PATCH_BYTES = 64 * 1024 * 1024
_MAX_LABEL_LENGTH = 120
_HASH = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_VerificationFailure = Literal[
    "command_policy",
    "missing_file",
    "operating_system",
    "review_policy",
    "subprocess",
    "unexpected",
]
_SECRET = re.compile(r"(?i)(token|password|secret|api[_-]?key|bearer)\s*[:=]")


class ReviewError(HybridSDLCError):
    """Review inputs or current acceptance evidence are invalid."""

    default_code = "REVIEW_INVALID"
    default_exit_code = ExitCode.POLICY_ERROR


class ReviewEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    path: StrictStr
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: StrictStr
    baseline_commit: StrictStr = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    patch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    profile: StrictStr
    profile_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    verification_worktree_id: StrictStr | None
    started_at: StrictStr
    finished_at: StrictStr
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    exit_code: StrictInt | None
    timed_out: bool
    cancelled: bool
    truncated: bool
    candidate_before_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_after_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    unchanged: bool
    scope_checked: bool
    verification_completed: bool = False
    verification_error: _VerificationFailure | None = None

    @field_validator("profile")
    @classmethod
    def validate_profile_label(cls, value: str) -> str:
        try:
            cleaned = _validate_label(value, "test profile")
        except ReviewError as exc:
            raise ValueError("Test profile label is unsafe") from exc
        if cleaned != value:
            raise ValueError("Test profile label must match its configured name exactly")
        return value


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: StrictStr
    run_record_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_commit: StrictStr = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    patch_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    revision: StrictInt = Field(ge=1)
    reviewer_kind: Literal["human", "model"]
    reviewer: StrictStr = Field(min_length=1, max_length=_MAX_LABEL_LENGTH)
    reviewer_model: StrictStr | None = None
    worker_model: StrictStr | None
    model_relation: Literal["same_model", "different_model_unverified", "unknown", "not_applicable"]
    judgment: Literal["accepted", "rejected", "blocked"]
    recorded_at: StrictStr
    evidence: ReviewEvidence | None

    @field_validator("reviewer", "reviewer_model", "worker_model")
    @classmethod
    def validate_labels(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if (
            not cleaned
            or len(cleaned) > _MAX_LABEL_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in cleaned)
            or _SECRET.search(cleaned)
            or redact_secrets(cleaned) != cleaned
        ):
            raise ValueError("Reviewer provenance label is unsafe")
        return cleaned

    @model_validator(mode="after")
    def validate_decision_evidence(self) -> ReviewRecord:
        if self.reviewer_kind == "model":
            if self.reviewer_model is None:
                raise ValueError("Model reviewer requires reviewer_model")
            if self.model_relation != _model_relation(
                self.reviewer_kind, self.reviewer_model, self.worker_model
            ):
                raise ValueError("Model reviewer provenance relation contradicts model identifiers")
        elif self.reviewer_model is not None or self.model_relation != "not_applicable":
            raise ValueError("Human reviewer provenance relation is inconsistent")
        if self.judgment == "accepted":
            if self.evidence is None or not _evidence_passed(self.evidence):
                raise ValueError("Accepted review requires passing independent evidence")
        if self.evidence is not None and (
            self.evidence.run_id != self.run_id
            or self.evidence.baseline_commit != self.baseline_commit
            or self.evidence.patch_sha256 != self.patch_sha256
        ):
            raise ValueError("Review evidence does not bind this run, baseline, and patch")
        return self


class ReviewStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: StrictStr
    baseline_commit: StrictStr | None
    patch_sha256: StrictStr | None
    current: bool
    judgment: Literal["accepted", "rejected", "blocked"] | None
    revision: StrictInt = Field(ge=0)
    reason: StrictStr | None = None


def record_review(
    *,
    run_record: Path | str,
    repo_root: Path | str,
    expected_baseline: str,
    expected_patch_sha256: str,
    expected_revision: int,
    reviewer_kind: Literal["human", "model"],
    reviewer: str,
    judgment: Literal["accepted", "rejected", "blocked"],
    reviewer_model: str | None = None,
    profile_name: str | None = None,
) -> ReviewRecord:
    """Record one reviewer judgment; accepted is earned by running a named profile."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ReviewError("Expected revision must be a non-negative integer")
    if profile_name is not None and _validate_label(profile_name, "test profile") != profile_name:
        raise ReviewError("Test profile label must match its configured name exactly")
    source = verify_repo_root(repo_root)
    label = _validate_label(reviewer, "reviewer")
    model_label = _validate_label(reviewer_model, "reviewer model") if reviewer_model else None
    if reviewer_kind == "model" and model_label is None:
        raise ReviewError("Model reviewers require a model identifier")
    if reviewer_kind == "human" and model_label is not None:
        raise ReviewError("Human reviews cannot specify a reviewer model")
    if judgment == "accepted" and not profile_name:
        raise ReviewError("An accepted review requires a named test profile")
    try:
        run_path, artifact_root, raw_run, run = _load_selected_run(run_record, source)
        _, patch_path, patch_bytes = _run_private_artifacts(run, artifact_root)
    except ReviewError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
        subprocess.SubprocessError,
    ) as exc:
        raise ReviewError("Selected run or private artifacts are missing or invalid") from exc
    patch_digest = hashlib.sha256(patch_bytes).hexdigest()
    if run.baseline_commit != expected_baseline or patch_digest != expected_patch_sha256:
        raise ReviewError("Expected baseline or raw patch digest does not match the selected run")
    if judgment == "accepted" and run.status is not RunStatus.SUCCESS:
        raise ReviewError("Only successfully completed runs can be accepted")
    if not _COMMIT.fullmatch(expected_baseline):
        raise ReviewError("Expected baseline must be a full Git commit identifier")
    try:
        head = _git_text(source, "rev-parse", "--verify", "HEAD^{commit}")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError("Could not verify source repository baseline") from exc
    if head != expected_baseline:
        raise ReviewError("Source repository HEAD no longer matches the run baseline")

    review_dir = artifact_root / "reviews"
    if artifact_root == source / ".hybrid_sdlc":
        ignored = subprocess.run(
            ["git", "-C", str(source), "check-ignore", "--quiet", "--", ".hybrid_sdlc"],
            capture_output=True,
            check=False,
        )
        if ignored.returncode != 0:
            raise ReviewError("Repository .hybrid_sdlc artifacts must be Git-ignored before review")
    _validate_private_dir(review_dir, artifact_root, create=True)
    review_path = review_dir / f"{run.run_id}.json"
    lock = FileLock(str(review_path) + ".lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ReviewError("Another review update or retention operation is active") from exc
    evidence_lock: FileLock | None = None
    try:
        previous = _load_review_record(review_path) if review_path.exists() else None
        revision = previous.revision if previous else 0
        if revision != expected_revision:
            raise ReviewError(
                "Review revision changed; reload current status before recording",
                details={"expected_revision": expected_revision, "actual_revision": revision},
            )
        evidence: ReviewEvidence | None = None
        evidence_digest: str | None = None
        profile_fingerprint: str | None = None
        if judgment == "accepted":
            assert profile_name is not None
            evidence, profile_fingerprint = _verify_candidate(
                source=source,
                run=run,
                patch_bytes=patch_bytes,
                patch_sha256=patch_digest,
                profile_name=profile_name,
                evidence_path=f"reviews/{run.run_id}-r{revision + 1}.evidence.json",
            )
            evidence_payload = evidence.model_dump(mode="json")
            evidence_payload["sha256"] = "0" * 64
            evidence_digest = hashlib.sha256(_canonical_json(evidence_payload)).hexdigest()
            evidence = evidence.model_copy(update={"sha256": evidence_digest})
            evidence_bytes = _canonical_json(evidence.model_dump(mode="json"))
            evidence_path = review_dir / f"{run.run_id}-r{revision + 1}.evidence.json"
            evidence_lock = FileLock(str(evidence_path) + ".lock", timeout=0)
            try:
                evidence_lock.acquire()
            except Timeout as exc:
                raise ReviewError("Independent evidence is being cleaned or updated") from exc
            _atomic_private_write(
                evidence_path,
                evidence_bytes,
                artifact_root,
                already_locked=True,
            )

        # Recheck after potentially long test execution, immediately before the CAS write.
        current_run = _read_bounded_regular(run_path, _MAX_JSON_BYTES)
        current_patch = _read_bounded_regular(patch_path, _MAX_PATCH_BYTES)
        if hashlib.sha256(current_run).hexdigest() != hashlib.sha256(raw_run).hexdigest():
            raise ReviewError("Selected run record changed during review")
        if hashlib.sha256(current_patch).hexdigest() != patch_digest:
            raise ReviewError("Selected raw patch changed during review")
        if _git_text(source, "rev-parse", "--verify", "HEAD^{commit}") != expected_baseline:
            raise ReviewError("Source repository HEAD changed during review")
        if profile_name is not None and profile_fingerprint is not None:
            current_profile = _profile_fingerprint(source, profile_name)
            if current_profile != profile_fingerprint:
                raise ReviewError("Named test profile changed during review")
        if evidence is not None:
            persisted_evidence = _read_bounded_regular(evidence_path, _MAX_JSON_BYTES)
            if (
                hashlib.sha256(persisted_evidence).hexdigest()
                != hashlib.sha256(evidence_bytes).hexdigest()
            ):
                raise ReviewError("Independent test evidence changed before review persistence")

        record = ReviewRecord(
            run_id=run.run_id,
            run_record_sha256=hashlib.sha256(raw_run).hexdigest(),
            baseline_commit=expected_baseline,
            patch_sha256=patch_digest,
            revision=revision + 1,
            reviewer_kind=reviewer_kind,
            reviewer=label,
            reviewer_model=model_label,
            worker_model=run.worker_model,
            model_relation=_model_relation(reviewer_kind, model_label, run.worker_model),
            judgment=judgment
            if judgment != "accepted" or _evidence_passed(evidence)
            else "blocked",
            recorded_at=datetime.now(UTC).isoformat(),
            evidence=evidence if judgment == "accepted" else None,
        )
        _atomic_private_write(
            review_path,
            _canonical_json(record.model_dump(mode="json")),
            artifact_root,
            already_locked=True,
        )
        return record
    finally:
        if evidence_lock is not None:
            evidence_lock.release()
        lock.release()


def read_review_status(*, run_record: Path | str, repo_root: Path | str) -> ReviewStatus:
    """Validate exact referenced artifacts and report whether stored review is current."""
    source = verify_repo_root(repo_root)
    try:
        _run_path, artifact_root, raw_run, run = _load_selected_run(run_record, source)
        _, patch_path, patch_bytes = _run_private_artifacts(run, artifact_root)
    except ReviewError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
        subprocess.SubprocessError,
    ) as exc:
        raise ReviewError("Selected run or private artifacts are missing or invalid") from exc
    review_path = artifact_root / "reviews" / f"{run.run_id}.json"
    if not review_path.exists():
        return ReviewStatus(
            run_id=run.run_id,
            baseline_commit=run.baseline_commit,
            patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
            current=False,
            judgment=None,
            revision=0,
            reason="No review record",
        )
    record = _load_review_record(review_path)
    reason: str | None = None
    try:
        if record.run_id != run.run_id:
            raise ReviewError("Review refers to another run")
        if record.run_record_sha256 != hashlib.sha256(raw_run).hexdigest():
            raise ReviewError("Run record changed")
        if record.baseline_commit != run.baseline_commit:
            raise ReviewError("Run baseline changed")
        if record.patch_sha256 != hashlib.sha256(patch_bytes).hexdigest():
            raise ReviewError("Raw patch changed")
        if _git_text(source, "rev-parse", "--verify", "HEAD^{commit}") != record.baseline_commit:
            raise ReviewError("Source repository HEAD changed")
        if record.evidence is not None:
            expected_evidence = f"reviews/{run.run_id}-r{record.revision}.evidence.json"
            if record.evidence.path != expected_evidence:
                raise ReviewError("Evidence reference is outside the selected run review directory")
            evidence_path = artifact_root / expected_evidence
            evidence_bytes = _read_bounded_regular(evidence_path, _MAX_JSON_BYTES)
            payload = _strict_json(evidence_bytes)
            payload["sha256"] = "0" * 64
            if hashlib.sha256(_canonical_json(payload)).hexdigest() != record.evidence.sha256:
                raise ReviewError("Independent test evidence changed")
            persisted_record = ReviewEvidence.model_validate(_strict_json(evidence_bytes))
            if persisted_record != record.evidence:
                raise ReviewError("Embedded and persisted test evidence do not match")
            _validate_evidence(evidence_bytes, record, source, run, patch_path)
        elif record.judgment == "accepted":
            raise ReviewError("Accepted review is missing independent test evidence")
    except (ReviewError, OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ReviewError)
            else "Referenced review artifact is missing or invalid"
        )
    return ReviewStatus(
        run_id=run.run_id,
        baseline_commit=run.baseline_commit,
        patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        current=reason is None,
        judgment=record.judgment if reason is None else None,
        revision=record.revision,
        reason=reason,
    )


def _verify_candidate(
    *,
    source: Path,
    run: RunResult,
    patch_bytes: bytes,
    patch_sha256: str,
    profile_name: str,
    evidence_path: str,
) -> tuple[ReviewEvidence, str | None]:
    started = datetime.now(UTC).isoformat()
    start_time = time.perf_counter()
    exit_code: int | None = None
    timed_out = False
    cancelled = False
    truncated = False
    scope_checked = False
    unchanged = False
    verification_completed = False
    verification_error: _VerificationFailure | None = None
    before_hash = "0" * 64
    after_hash = "0" * 64
    profile_fingerprint: str | None = None
    record: WorktreeRecord | None = None
    try:
        profile_fingerprint = _profile_fingerprint(source, profile_name)
        profile = load_config(source).command_profiles[profile_name]
        record = create_worktree(source)
        if record.baseline_commit != run.baseline_commit:
            raise ReviewError("Verification checkout baseline differs from the recorded run")
        _git_bytes(
            record.path,
            "apply",
            "--index",
            "--binary",
            "--whitespace=nowarn",
            "-",
            input_bytes=patch_bytes,
        )
        before_result = export_worktree_result(record)
        before_hash = hashlib.sha256(before_result.patch_path.read_bytes()).hexdigest()
        if run.policy_path is not None:
            provenance = PolicyProvenance(
                policy_path=run.policy_path,
                policy_sha256=run.policy_sha256 or "",
                spec_sha256=run.spec_sha256 or "",
                baseline_commit=run.policy_baseline_commit or "",
            )
            scope = load_task_scope(source, provenance)
            validate_task_scope_paths(scope, list(before_result.changed_paths))
            scope_checked = True
        resolved_executable = resolve_profile_executable(profile, source)
        isolated_executable = _isolated_test_executable(
            profile, source, record.path, resolved_executable
        )
        result = run_test_profile(
            profile,
            record.path,
            resolved_executable=isolated_executable,
        )
        exit_code = result.exit_code
        timed_out = result.timed_out
        cancelled = result.cancelled
        truncated = result.is_truncated
        after_result = export_worktree_result(record)
        after_hash = hashlib.sha256(after_result.patch_path.read_bytes()).hexdigest()
        unchanged = before_hash == after_hash
        rollback_worktree(record)
        verification_completed = True
    except Exception as exc:
        if isinstance(exc, CommandPolicyError):
            verification_error = "command_policy"
        elif isinstance(exc, FileNotFoundError):
            verification_error = "missing_file"
        elif isinstance(exc, OSError):
            verification_error = "operating_system"
        elif isinstance(exc, ReviewError):
            verification_error = "review_policy"
        elif isinstance(exc, subprocess.SubprocessError):
            verification_error = "subprocess"
        else:
            verification_error = "unexpected"
        if record is not None:
            try:
                after_result = export_worktree_result(record)
                after_hash = hashlib.sha256(after_result.patch_path.read_bytes()).hexdigest()
                unchanged = before_hash == after_hash
                rollback_worktree(record)
            except Exception:
                unchanged = False
    finally:
        finished = datetime.now(UTC).isoformat()
        duration = max(0.0, time.perf_counter() - start_time)
    evidence = ReviewEvidence(
        path=evidence_path,
        sha256="0" * 64,
        run_id=run.run_id,
        baseline_commit=run.baseline_commit or "",
        patch_sha256=patch_sha256,
        profile=profile_name,
        profile_sha256=profile_fingerprint or ("0" * 64),
        verification_worktree_id=record.worktree_id if record else None,
        started_at=started,
        finished_at=finished,
        duration_seconds=duration,
        exit_code=exit_code,
        timed_out=timed_out,
        cancelled=cancelled,
        truncated=truncated,
        candidate_before_sha256=before_hash,
        candidate_after_sha256=after_hash,
        unchanged=unchanged,
        scope_checked=scope_checked,
        verification_completed=verification_completed,
        verification_error=verification_error,
    )
    return evidence, profile_fingerprint


def _validate_evidence(
    data: bytes, record: ReviewRecord, source: Path, run: RunResult, patch_path: Path
) -> None:
    raw = _strict_json(data)
    evidence = ReviewEvidence.model_validate(raw)
    expected_path = f"reviews/{run.run_id}-r{record.revision}.evidence.json"
    if evidence.schema_version != _REVIEW_VERSION or evidence.path != expected_path:
        raise ReviewError("Unsupported or mismatched evidence reference")
    if (
        evidence.run_id != run.run_id
        or evidence.baseline_commit != record.baseline_commit
        or evidence.patch_sha256 != record.patch_sha256
    ):
        raise ReviewError("Independent test evidence refers to different run inputs")
    if record.judgment == "accepted" and not _evidence_passed(evidence):
        raise ReviewError("Accepted review does not have passing independent test evidence")
    if record.judgment == "accepted" and evidence.scope_checked != (run.policy_path is not None):
        raise ReviewError("Independent evidence task-scope metadata does not match the run")
    if record.judgment == "accepted" and evidence.profile_sha256 != _profile_fingerprint(
        source, evidence.profile
    ):
        raise ReviewError("Named test profile changed after verification")


def _load_selected_run(path: Path | str, source: Path) -> tuple[Path, Path, bytes, RunResult]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = source / candidate
    candidate = candidate.absolute()
    data = _read_bounded_regular(candidate, _MAX_JSON_BYTES)
    try:
        result = RunResult.model_validate(_strict_json(data))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ReviewError("Selected run record is malformed or unsupported") from exc
    if result.schema_version != "1.0.0" or result.source_repo_root is None:
        raise ReviewError("Selected run record is missing supported source provenance")
    if Path(result.source_repo_root).resolve() != source:
        raise ReviewError("Selected run record belongs to another repository")
    if (
        candidate.name != f"{result.run_id}.json"
        or candidate.parent.name != "runs"
        or candidate.parent.parent.name != ".hybrid_sdlc"
    ):
        raise ReviewError("Run record must be selected from a toolkit-owned runs directory")
    artifact_root = candidate.parent.parent
    if artifact_root == source / ".hybrid_sdlc":
        _validate_private_dir(artifact_root, artifact_root, create=False)
    else:
        _validate_private_dir(artifact_root, artifact_root, create=False)
        if not (artifact_root.parent / "worktree.json").is_file():
            raise ReviewError("External run artifacts have no registered worktree identity")
    return candidate.resolve(), artifact_root, data, result


def _run_private_artifacts(run: RunResult, artifact_root: Path) -> tuple[Path, Path, bytes]:
    source = Path(run.source_repo_root or "").resolve()
    worktree_record_text = run.artifacts.get("worktree_record")
    patch_text = run.review_patch or run.artifacts.get("review_patch")
    if not worktree_record_text or not patch_text:
        raise ReviewError("Selected run is missing its private worktree or raw patch reference")
    worktree_record_path = Path(worktree_record_text)
    patch_path = Path(patch_text)
    if not worktree_record_path.is_absolute() or not patch_path.is_absolute():
        raise ReviewError("Run worktree and patch references must be absolute owned paths")
    worktree_record_path = worktree_record_path.resolve(strict=True)
    patch_path = patch_path.resolve(strict=True)
    if worktree_record_path.name != "worktree.json":
        raise ReviewError("Invalid worktree recovery record")
    expected_attempt = worktree_record_path.parent
    expected_artifact = expected_attempt / ".hybrid_sdlc"
    if artifact_root != expected_artifact and artifact_root != source / ".hybrid_sdlc":
        raise ReviewError("Run artifact root does not match the owned worktree")
    _validate_private_dir(
        expected_artifact, source / ".hybrid_sdlc", create=False, allow_external=True
    )
    _validate_no_links(worktree_record_path, expected_attempt)
    recovery = _strict_json(_read_bounded_regular(worktree_record_path, _MAX_JSON_BYTES))
    record = WorktreeRecord(
        worktree_id=recovery["worktree_id"],
        repo_root=Path(recovery["repo_root"]),
        path=Path(recovery["path"]),
        baseline_commit=recovery["baseline_commit"],
        record_path=worktree_record_path,
    )
    if record.repo_root.resolve() != source or record.baseline_commit != run.baseline_commit:
        raise ReviewError("Worktree record does not match selected run source and baseline")
    if run.worktree_path is None or Path(run.worktree_path).resolve() != record.path.resolve():
        raise ReviewError("Run record worktree path does not match the owned recovery record")
    try:
        _verified_rollback_record(record)
    except Exception as exc:
        raise ReviewError("Worktree identity is not a registered toolkit-owned checkout") from exc
    if patch_path != expected_attempt / "result.patch":
        raise ReviewError("Raw review patch is not the expected owned result.patch artifact")
    if artifact_root not in (source / ".hybrid_sdlc", expected_artifact):
        raise ReviewError("Selected run record is outside its private artifact roots")
    patch_data = _read_bounded_regular(patch_path, _MAX_PATCH_BYTES)
    return artifact_root, patch_path, patch_data


def _load_review_record(path: Path) -> ReviewRecord:
    data = _read_bounded_regular(path, _MAX_JSON_BYTES)
    try:
        record = ReviewRecord.model_validate(_strict_json(data))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ReviewError("Review record is malformed or uses an unsupported schema") from exc
    if record.schema_version != _REVIEW_VERSION:
        raise ReviewError("Unsupported review record schema version")
    _validate_label(record.reviewer, "reviewer")
    return record


def _profile_fingerprint(source: Path, name: str) -> str:
    config = load_config(source)
    profile = config.command_profiles.get(name)
    if profile is None:
        raise ReviewError(f"Named test profile is not configured: {name}")
    return hashlib.sha256(_canonical_json(profile.model_dump(mode="json"))).hexdigest()


def _candidate_fingerprint(checkout: Path) -> str:
    status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
        check=True,
    ).stdout
    return hashlib.sha256(status).hexdigest()


def _validate_private_dir(
    path: Path, source_artifact_root: Path, *, create: bool, allow_external: bool = False
) -> None:
    if create:
        source_artifact_root.mkdir(parents=True, exist_ok=True)
        path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
        raise ReviewError("Review artifacts must use an existing canonical private directory")
    if not allow_external:
        try:
            path.relative_to(source_artifact_root)
        except ValueError as exc:
            raise ReviewError("Review artifacts escape the repository private directory") from exc
    _validate_no_links(path, path.parent)


def _validate_no_links(path: Path, boundary: Path) -> None:
    absolute = path.absolute()
    current = absolute
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if current == boundary:
                break
            current = current.parent
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReviewError("Review artifact paths must not contain links or reparse points")
        if current == boundary:
            break
        if current.parent == current:
            raise ReviewError("Review artifact path boundary is invalid")
        current = current.parent


def _read_bounded_regular(path: Path, limit: int) -> bytes:
    _validate_no_links(path, path.parent)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ReviewError("Referenced artifact is not a bounded regular file")
    data = path.read_bytes()
    if len(data) > limit:
        raise ReviewError("Referenced artifact exceeds size limit")
    return data


def _strict_json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReviewError("Review JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Review JSON is malformed") from exc


def _validate_label(value: str, field: str) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > _MAX_LABEL_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or _SECRET.search(value)
        or redact_secrets(value) != value
    ):
        raise ReviewError(
            f"{field.capitalize()} label is empty, too long, or contains secret-like text"
        )
    return value


def _model_relation(
    kind: Literal["human", "model"],
    reviewer: str | None,
    worker_model: str | None,
) -> Literal["same_model", "different_model_unverified", "unknown", "not_applicable"]:
    """Return only a provenance comparison, never a reviewer independence claim."""
    if kind != "model":
        return "not_applicable"
    if reviewer is None or worker_model is None:
        return "unknown"
    # The caller passes the explicitly supplied reviewer model identifier.
    return (
        "same_model"
        if reviewer.casefold() == worker_model.casefold()
        else "different_model_unverified"
    )


def _evidence_passed(evidence: ReviewEvidence | None) -> bool:
    return bool(
        evidence
        and evidence.exit_code == 0
        and evidence.unchanged
        and evidence.candidate_before_sha256 == evidence.candidate_after_sha256
        and evidence.verification_completed
        and not evidence.timed_out
        and not evidence.cancelled
        and not evidence.truncated
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _atomic_private_write(
    path: Path,
    data: bytes,
    artifact_root: Path,
    *,
    already_locked: bool = False,
) -> None:
    _validate_private_dir(path.parent, artifact_root, create=True)

    def write() -> None:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            for retry in range(20):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if retry == 19:
                        raise
                    time.sleep(0.01)
        finally:
            temporary.unlink(missing_ok=True)

    if already_locked:
        write()
    else:
        with FileLock(str(path) + ".lock"):
            write()


def _git_text(root: Path, *args: str) -> str:
    return (
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
        .stdout.decode()
        .strip()
    )


def _git_bytes(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, input=input_bytes
    ).stdout
