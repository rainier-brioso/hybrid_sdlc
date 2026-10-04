"""Aider invocation argv builder, test runner, failure signature, and bounded state machine."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hybrid_sdlc.artifacts import atomic_save_json, redact_secrets
from hybrid_sdlc.command_profiles import CommandProfile, resolve_profile_executable
from hybrid_sdlc.config import normalize_aider_edit_files
from hybrid_sdlc.errors import (
    BaselineTestFailureError,
    CancellationError,
    CommandPolicyError,
    HybridSDLCError,
    RepositoryError,
    RetryExhaustedError,
    StuckLoopError,
    TaskExecutionError,
    TaskTimeoutError,
)
from hybrid_sdlc.git_tools import (
    acquire_repo_lock,
    capture_diff_summary,
    check_worktree_clean,
    create_scoped_commit,
    get_baseline_commit,
    validate_committed_path,
)
from hybrid_sdlc.models import (
    AttemptRecord,
    DiffSummary,
    FailureRecord,
    RunResult,
    RunStatus,
)
from hybrid_sdlc.processes import SubprocessResult, run_bounded_subprocess
from hybrid_sdlc.security import build_sanitized_environment, resolve_confined_path
from hybrid_sdlc.worktrees import (
    WorktreeRecord,
    create_worktree,
    export_worktree_result,
    rollback_worktree,
)

# Regexes for normalizing failure signatures
RE_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")
RE_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
RE_SECONDS = re.compile(r"\b\d+(?:\.\d+)?s\b")
RE_PYTEST_SUMMARY = re.compile(r"FAILED\s+([^\s:]+::[^\s:]+)(?:\s+-\s+([^\n]+))?")


def build_aider_argv(
    endpoint_url: str,
    model_name: str,
    spec_file: Path,
    task_instruction: str,
    target_files: list[Path] | None = None,
    extra_flags: list[str] | None = None,
    aider_cmd: list[str] | str = "aider",
    repo_map_tokens: int | None = None,
) -> list[str]:
    """Construct a deterministic argv array for invoking Aider.

    Includes diff mode, no auto-commit, no shell suggestions, yes-always.
    Redirects Aider histories and prevents automatic ignore-file edits.
    Skips the metadata-warning prompt so yes-always cannot launch its browser URL.
    """
    cmd_prefix = [aider_cmd] if isinstance(aider_cmd, str) else list(aider_cmd)
    argv = cmd_prefix + [
        "--model",
        f"openai/{model_name}",
        "--openai-api-base",
        endpoint_url,
        "--openai-api-key",
        "local-no-key",
        "--edit-format",
        "diff",
        "--no-auto-commits",
        "--no-show-model-warnings",
        "--no-gitignore",
        "--input-history-file",
        os.devnull,
        "--chat-history-file",
        os.devnull,
        "--no-suggest-shell-commands",
        "--yes-always",
        "--read",
        str(spec_file),
        "--message",
        task_instruction,
    ]

    if target_files:
        for tf in target_files:
            argv.append(str(tf))

    if repo_map_tokens is not None:
        argv.extend(["--map-tokens", str(repo_map_tokens)])

    if extra_flags:
        argv.extend(extra_flags)

    return argv


def validate_aider_target_files(
    repo_root: Path,
    spec_file: Path,
    target_files: list[Path] | None,
) -> list[Path]:
    """Resolve configured Aider context files and require committed regular files."""
    if not target_files:
        return []

    root = repo_root.resolve()
    resolved_spec = spec_file.resolve()
    spec_relative = resolved_spec.relative_to(root).as_posix()
    try:
        relative_paths = normalize_aider_edit_files([str(path) for path in target_files])
    except ValueError as exc:
        raise RepositoryError(f"Invalid Aider context file path: {exc}") from exc
    resolved: list[Path] = []
    for relative in relative_paths:
        if relative == spec_relative:
            raise RepositoryError("aider_edit_files cannot include the task specification")
        candidate = root / Path(relative)
        try:
            source_mode = candidate.lstat().st_mode
        except OSError as exc:
            raise RepositoryError(f"Aider context file '{relative}' does not exist") from exc
        if not stat.S_ISREG(source_mode):
            raise RepositoryError(
                f"Aider context file '{relative}' must be a regular file, not a symlink or directory"
            )
        try:
            confined = resolve_confined_path(candidate, repo_root=root, must_exist=True)
        except (OSError, ValueError) as exc:
            raise RepositoryError(
                f"Aider context file '{relative}' is not a regular repository file"
            ) from exc
        if confined == resolved_spec:
            raise RepositoryError("aider_edit_files cannot include the task specification")
        validate_committed_path(root, relative)
        resolved.append(confined)
    return resolved


def extract_failure_signature(stdout: str, stderr: str) -> str:
    """Extract a normalized failure signature from test stdout and stderr.

    Normalizes volatile timestamps, durations, addresses, and extracts failure lines.
    """
    combined = f"{stdout}\n{stderr}"
    lines = combined.splitlines()

    failing_items: list[str] = []
    for line in lines:
        m = RE_PYTEST_SUMMARY.search(line)
        if m:
            test_id = m.group(1)
            reason = m.group(2) or ""
            norm_reason = RE_HEX_ADDR.sub("0xADDR", reason)
            norm_reason = RE_TIMESTAMP.sub("TIMESTAMP", norm_reason)
            norm_reason = RE_SECONDS.sub("0.0s", norm_reason)
            failing_items.append(f"{test_id}:{norm_reason.strip()}")

    if not failing_items:
        # Fallback: extract lines containing Error, Exception, or FAILED
        for line in lines:
            if any(term in line for term in ("Error:", "Exception:", "FAILED", "AssertionError")):
                norm = RE_HEX_ADDR.sub("0xADDR", line)
                norm = RE_TIMESTAMP.sub("TIMESTAMP", norm)
                norm = RE_SECONDS.sub("0.0s", norm)
                failing_items.append(norm.strip())

    if not failing_items:
        # Generic hash of normalized output
        norm = RE_HEX_ADDR.sub("0xADDR", combined)
        norm = RE_TIMESTAMP.sub("TIMESTAMP", norm)
        norm = RE_SECONDS.sub("0.0s", norm)
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

    joined = "\n".join(sorted(failing_items[:20]))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def run_test_profile(
    profile: CommandProfile,
    repo_root: Path,
    buffer_cap_bytes: int = 500 * 1024,
    resolved_executable: Path | None = None,
    cancel_event: threading.Event | None = None,
    process_observer: Callable[[int, datetime | None], None] | None = None,
) -> SubprocessResult:
    """Execute a named test profile in a confined, sanitized environment."""
    repo_root = repo_root.resolve()
    resolved_exe = resolved_executable or resolve_profile_executable(profile, repo_root)

    cmd_argv = [str(resolved_exe)] + profile.argv[1:]
    target_cwd = resolve_confined_path(profile.cwd, repo_root=repo_root, must_exist=True)
    if not target_cwd.is_dir():
        raise TaskExecutionError(
            f"Command profile working directory is not a directory: '{profile.cwd}'",
            code="PROFILE_CWD_NOT_DIRECTORY",
        )

    clean_env = build_sanitized_environment(allowlist=profile.env_allowlist)

    return run_bounded_subprocess(
        argv=cmd_argv,
        cwd=target_cwd,
        env=clean_env,
        timeout_seconds=float(profile.timeout_seconds),
        buffer_cap_bytes=buffer_cap_bytes,
        cancel_event=cancel_event,
        process_observer=process_observer,
    )


def _run_bounded_loop_in_checkout(
    repo_root: Path,
    spec_path: Path | str,
    task_id: str,
    profile: CommandProfile,
    endpoint_url: str,
    model_name: str,
    max_retries: int = 3,
    task_timeout_seconds: float = 600.0,
    attempt_timeout_seconds: float = 180.0,
    buffer_cap_bytes: int = 500 * 1024,
    aider_cmd: list[str] | str = "aider",
    task_instruction: str = "",
    resolved_test_executable: Path | None = None,
    cancel_event: threading.Event | None = None,
    process_observer: Callable[[int, datetime | None], None] | None = None,
    artifact_root: Path | None = None,
    repo_map_tokens: int | None = None,
    target_files: list[Path] | None = None,
) -> RunResult:
    """Execute the bounded editing and testing state machine."""
    repo_root = repo_root.resolve()
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    start_iso = datetime.now(UTC).isoformat()
    start_perf = time.perf_counter()

    spec_file = resolve_confined_path(spec_path, repo_root=repo_root, must_exist=True)
    resolved_target_files = validate_aider_target_files(repo_root, spec_file, target_files)

    # 1. Verify clean worktree before starting
    check_worktree_clean(repo_root)

    # 2. Acquire advisory execution lock
    with acquire_repo_lock(
        repo_root,
        run_id=run_id,
        lock_directory=(artifact_root / "locks") if artifact_root is not None else None,
    ):
        resolved_test_executable = resolved_test_executable or resolve_profile_executable(
            profile, repo_root
        )
        baseline_commit = get_baseline_commit(repo_root)
        attempts: list[AttemptRecord] = []
        final_diff: DiffSummary | None = None
        failure_record: FailureRecord | None = None
        run_status = RunStatus.FAILURE

        # 3. Pre-flight Baseline Test Check
        baseline_sub_res = run_test_profile(
            profile,
            repo_root,
            buffer_cap_bytes=buffer_cap_bytes,
            resolved_executable=resolved_test_executable,
            cancel_event=cancel_event,
            process_observer=process_observer,
        )
        if (
            baseline_sub_res.cancelled
            or baseline_sub_res.timed_out
            or baseline_sub_res.exit_code != 0
        ):
            sig = extract_failure_signature(baseline_sub_res.stdout, baseline_sub_res.stderr)
            err: HybridSDLCError
            if baseline_sub_res.cancelled:
                err = CancellationError("Task was cancelled during baseline tests")
                run_status = RunStatus.CANCELLED
            elif baseline_sub_res.timed_out:
                err = TaskTimeoutError(
                    "Baseline test check timed out",
                    details={"timeout": profile.timeout_seconds},
                )
            else:
                err = BaselineTestFailureError(
                    f"Baseline test check failed before any edits (exit code {baseline_sub_res.exit_code})",
                    details={
                        "exit_code": baseline_sub_res.exit_code,
                        "signature": sig,
                        "stdout_excerpt": redact_secrets(baseline_sub_res.stdout[:500]),
                    },
                )
            failure_record = err.to_failure_record()
            total_dur = time.perf_counter() - start_perf
            result = RunResult(
                run_id=run_id,
                task_id=task_id,
                spec_path=str(spec_file.relative_to(repo_root)),
                repo_root=str(repo_root),
                status=run_status,
                started_at=start_iso,
                finished_at=datetime.now(UTC).isoformat(),
                total_duration_seconds=total_dur,
                baseline_commit=baseline_commit,
                initial_status="clean",
                attempts=[],
                final_diff=None,
                failure=failure_record,
            )
            artifact_dir = artifact_root or (repo_root / ".hybrid_sdlc")
            out_path = artifact_dir / "runs" / f"{run_id}.json"
            atomic_save_json(
                out_path,
                result,
                repo_root=artifact_root.parent if artifact_root is not None else repo_root,
            )
            return result

        # 4. Attempt loop
        last_diff_hash: str | None = None
        last_failure_sig: str | None = None
        instruction = task_instruction or f"Implement task {task_id} according to specification."

        for attempt_idx in range(1, max_retries + 1):
            # Check total wall-clock timeout
            current_elapsed = time.perf_counter() - start_perf
            if current_elapsed > task_timeout_seconds:
                err = TaskTimeoutError(
                    f"Task execution exceeded overall timeout of {task_timeout_seconds}s",
                    details={"elapsed_seconds": current_elapsed, "timeout": task_timeout_seconds},
                )
                failure_record = err.to_failure_record()
                break

            att_start_iso = datetime.now(UTC).isoformat()
            att_start_perf = time.perf_counter()

            # Construct Aider argv
            aider_argv = build_aider_argv(
                endpoint_url=endpoint_url,
                model_name=model_name,
                spec_file=spec_file,
                task_instruction=instruction,
                aider_cmd=aider_cmd,
                repo_map_tokens=repo_map_tokens,
                target_files=resolved_target_files,
            )

            # Run Aider
            clean_env = build_sanitized_environment()
            aider_result = run_bounded_subprocess(
                argv=aider_argv,
                cwd=repo_root,
                env=clean_env,
                timeout_seconds=attempt_timeout_seconds,
                buffer_cap_bytes=buffer_cap_bytes,
                cancel_event=cancel_event,
                process_observer=process_observer,
            )

            if aider_result.cancelled:
                err = CancellationError(f"Task was cancelled during Aider attempt {attempt_idx}")
                failure_record = err.to_failure_record()
                run_status = RunStatus.CANCELLED
                break
            if aider_result.timed_out:
                err = TaskTimeoutError(
                    f"Aider attempt {attempt_idx} exceeded its timeout",
                    details={"attempt": attempt_idx, "timeout": attempt_timeout_seconds},
                )
                failure_record = err.to_failure_record()
                break

            # Capture diff
            artifact_dir = artifact_root or (repo_root / ".hybrid_sdlc")
            patch_path = artifact_dir / "runs" / f"{run_id}_att{attempt_idx}.patch"
            diff_summary = capture_diff_summary(
                repo_root=repo_root,
                baseline_commit=baseline_commit,
                patch_output_path=patch_path,
            )
            final_diff = diff_summary

            if diff_summary.is_empty:
                err = StuckLoopError(
                    "Model produced no repository changes",
                    code="LOOP_ZERO_DIFF",
                    details={"attempt": attempt_idx},
                )
                failure_record = err.to_failure_record()
                break

            # Check if diff is stuck (same diff hash or zero edit when test was failing)
            if diff_summary.diff_hash == last_diff_hash:
                err = StuckLoopError(
                    "Model produced an identical diff to previous attempt",
                    details={"attempt": attempt_idx, "diff_hash": diff_summary.diff_hash},
                )
                failure_record = err.to_failure_record()
                break

            last_diff_hash = diff_summary.diff_hash

            # Run test profile
            test_res = run_test_profile(
                profile,
                repo_root,
                buffer_cap_bytes=buffer_cap_bytes,
                resolved_executable=resolved_test_executable,
                cancel_event=cancel_event,
                process_observer=process_observer,
            )
            att_dur = time.perf_counter() - att_start_perf
            att_end_iso = datetime.now(UTC).isoformat()

            test_passed = test_res.exit_code == 0
            if test_res.cancelled:
                err = CancellationError(f"Task was cancelled during test attempt {attempt_idx}")
                failure_record = err.to_failure_record()
                run_status = RunStatus.CANCELLED
                break
            fail_sig = (
                None if test_passed else extract_failure_signature(test_res.stdout, test_res.stderr)
            )

            att_record = AttemptRecord(
                attempt_index=attempt_idx,
                started_at=att_start_iso,
                finished_at=att_end_iso,
                duration_seconds=att_dur,
                test_exit_code=test_res.exit_code,
                test_passed=test_passed,
                test_stdout_summary=redact_secrets(test_res.stdout[:500]),
                test_stderr_summary=redact_secrets(test_res.stderr[:500]),
                failure_signature=fail_sig,
                diff_summary=diff_summary,
            )
            attempts.append(att_record)

            if test_passed:
                run_status = RunStatus.SUCCESS
                break

            # Check repeated identical failure signature
            if fail_sig is not None and fail_sig == last_failure_sig:
                err = StuckLoopError(
                    "Model produced the same failure signature twice in a row",
                    details={"signature": fail_sig, "attempt": attempt_idx},
                )
                failure_record = err.to_failure_record()
                break

            last_failure_sig = fail_sig

            # Prepare feedback instruction for next attempt
            instruction = (
                f"Previous edit attempt {attempt_idx} failed tests with exit code {test_res.exit_code}.\n"
                f"Error output:\n{redact_secrets(test_res.stdout[:500])}\n"
                f"{redact_secrets(test_res.stderr[:500])}\n"
                f"Please fix the code so tests pass."
            )

        if run_status != RunStatus.SUCCESS and failure_record is None:
            err = RetryExhaustedError(
                f"Exhausted maximum retries ({max_retries}) without passing tests",
                details={"attempts": len(attempts)},
            )
            failure_record = err.to_failure_record()

        total_dur = time.perf_counter() - start_perf
        result = RunResult(
            run_id=run_id,
            task_id=task_id,
            spec_path=str(spec_file.relative_to(repo_root)),
            repo_root=str(repo_root),
            status=run_status,
            started_at=start_iso,
            finished_at=datetime.now(UTC).isoformat(),
            total_duration_seconds=total_dur,
            baseline_commit=baseline_commit,
            initial_status="clean",
            attempts=attempts,
            final_diff=final_diff,
            failure=failure_record,
            artifacts={
                "run_record": f".hybrid_sdlc/runs/{run_id}.json",
            },
        )
        artifact_dir = artifact_root or (repo_root / ".hybrid_sdlc")
        out_path = artifact_dir / "runs" / f"{run_id}.json"
        atomic_save_json(
            out_path,
            result,
            repo_root=artifact_root.parent if artifact_root is not None else repo_root,
        )
        return result


def _isolated_test_executable(
    profile: CommandProfile,
    source_root: Path,
    checkout: Path,
    resolved_executable: Path | None,
) -> Path:
    """Re-resolve repository-local executables in the isolated checkout."""
    source_executable = resolved_executable or resolve_profile_executable(profile, source_root)
    try:
        relative = source_executable.resolve().relative_to(source_root.resolve())
    except ValueError:
        return source_executable
    checkout_executable = checkout / relative
    if not checkout_executable.is_file():
        raise CommandPolicyError(
            "Repository-local test executable is missing from the isolated worktree: "
            f"'{relative.as_posix()}'. Install the test runtime in the isolated checkout, "
            "or configure a bare/external executable that can run with the isolated checkout as cwd.",
            code="COMMAND_POLICY_ISOLATED_EXECUTABLE_MISSING",
            details={
                "source_executable": str(source_executable),
                "isolated_executable": str(checkout_executable),
            },
        )
    return resolve_profile_executable(
        profile.model_copy(update={"argv": [f"./{relative.as_posix()}", *profile.argv[1:]]}),
        checkout,
    )


def run_bounded_loop(
    repo_root: Path,
    spec_path: Path | str,
    task_id: str,
    profile: CommandProfile,
    endpoint_url: str,
    model_name: str,
    max_retries: int = 3,
    task_timeout_seconds: float = 600.0,
    attempt_timeout_seconds: float = 180.0,
    buffer_cap_bytes: int = 500 * 1024,
    aider_cmd: list[str] | str = "aider",
    task_instruction: str = "",
    resolved_test_executable: Path | None = None,
    cancel_event: threading.Event | None = None,
    process_observer: Callable[[int, datetime | None], None] | None = None,
    commit_requested: bool = False,
    rollback_on_failure: bool = False,
    repo_map_tokens: int | None = None,
    target_files: list[Path] | None = None,
) -> RunResult:
    """Run the bounded workflow in a detached worktree based on source HEAD."""
    source_root = repo_root.resolve()
    source_spec = resolve_confined_path(spec_path, repo_root=source_root, must_exist=True)
    if not source_spec.is_file():
        raise RepositoryError("spec_path must identify an existing regular file")
    relative_spec = source_spec.relative_to(source_root).as_posix()
    validate_committed_path(source_root, relative_spec)
    source_target_files = validate_aider_target_files(source_root, source_spec, target_files)
    relative_target_files = [
        Path(path.relative_to(source_root).as_posix()) for path in source_target_files
    ]

    record: WorktreeRecord = create_worktree(source_root)
    try:
        validate_committed_path(record.path, relative_spec)
        isolated_executable = _isolated_test_executable(
            profile, source_root, record.path, resolved_test_executable
        )
        result = _run_bounded_loop_in_checkout(
            repo_root=record.path,
            spec_path=record.path / Path(relative_spec),
            task_id=task_id,
            profile=profile,
            endpoint_url=endpoint_url,
            model_name=model_name,
            max_retries=max_retries,
            task_timeout_seconds=task_timeout_seconds,
            attempt_timeout_seconds=attempt_timeout_seconds,
            buffer_cap_bytes=buffer_cap_bytes,
            aider_cmd=aider_cmd,
            task_instruction=task_instruction,
            resolved_test_executable=isolated_executable,
            cancel_event=cancel_event,
            process_observer=process_observer,
            artifact_root=record.record_path.parent / ".hybrid_sdlc",
            repo_map_tokens=repo_map_tokens,
            target_files=relative_target_files,
        )

        commit_hash = None
        if commit_requested and result.status == RunStatus.SUCCESS:
            commit_hash = create_scoped_commit(
                record,
                task_id,
                f"Implement {task_id}",
                commit_requested=True,
                tests_passed=bool(result.attempts and result.attempts[-1].test_passed),
                policy_passed=True,
            )
        exported = export_worktree_result(record, scoped_commit_hash=commit_hash)
        if rollback_on_failure and result.status != RunStatus.SUCCESS:
            rollback_worktree(record)
    except HybridSDLCError as exc:
        raise RepositoryError(
            f"{exc.message}. Isolated worktree retained at '{record.path}'; "
            f"recovery record: '{record.record_path}'",
            code=exc.code,
            details={
                **exc.details,
                "worktree_path": str(record.path),
                "worktree_record": str(record.record_path),
            },
            exit_code=exc.exit_code,
        ) from exc
    except Exception as exc:
        raise RepositoryError(
            f"Isolated run failed with {type(exc).__name__}; worktree retained at '{record.path}' "
            f"with recovery record '{record.record_path}'",
            details={
                "worktree_path": str(record.path),
                "worktree_record": str(record.record_path),
            },
        ) from exc
    final_diff = result.final_diff
    if final_diff is not None:
        final_diff = final_diff.model_copy(
            update={
                "patch_file": str(exported.patch_path),
                "changed_files": list(exported.changed_paths),
                "is_empty": not exported.changed_paths,
            }
        )

    result = result.model_copy(
        update={
            "repo_root": str(source_root),
            "source_repo_root": str(source_root),
            "worktree_path": str(record.path),
            "baseline_commit": record.baseline_commit,
            "spec_path": relative_spec,
            "final_diff": final_diff,
            "review_patch": str(exported.patch_path),
            "commit_hash": commit_hash,
            "worktree_rolled_back": rollback_on_failure and result.status != RunStatus.SUCCESS,
            "artifacts": {
                **result.artifacts,
                "review_patch": str(exported.patch_path),
                "worktree_record": str(record.record_path),
            },
        }
    )
    source_record_dir = source_root / ".hybrid_sdlc" / "runs"
    ignored = subprocess.run(
        ["git", "-C", str(source_root), "check-ignore", "--quiet", ".hybrid_sdlc/runs"],
        capture_output=True,
        check=False,
    )
    if ignored.returncode == 0:
        out_path = source_record_dir / f"{result.run_id}.json"
        result_repo_root = source_root
        run_record_ref = out_path.relative_to(source_root).as_posix()
    else:
        out_path = record.record_path.parent / ".hybrid_sdlc" / "runs" / f"{result.run_id}.json"
        result_repo_root = record.record_path.parent
        run_record_ref = str(out_path)
    result = result.model_copy(
        update={"artifacts": {**result.artifacts, "run_record": run_record_ref}}
    )
    atomic_save_json(
        out_path,
        result,
        repo_root=result_repo_root,
    )
    return result
