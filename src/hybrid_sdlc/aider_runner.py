"""Aider invocation argv builder, test runner, failure signature, and bounded state machine."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from hybrid_sdlc.artifacts import atomic_save_json, redact_secrets
from hybrid_sdlc.command_profiles import CommandProfile, resolve_profile_executable
from hybrid_sdlc.errors import (
    BaselineTestFailureError,
    CancellationError,
    HybridSDLCError,
    RetryExhaustedError,
    StuckLoopError,
    TaskExecutionError,
    TaskTimeoutError,
)
from hybrid_sdlc.git_tools import (
    acquire_repo_lock,
    capture_diff_summary,
    check_worktree_clean,
    get_baseline_commit,
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
) -> list[str]:
    """Construct a deterministic argv array for invoking Aider.

    Includes diff mode, no auto-commit, no shell suggestions, yes-always.
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

    if extra_flags:
        argv.extend(extra_flags)

    return argv


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
) -> RunResult:
    """Execute the bounded editing and testing state machine."""
    repo_root = repo_root.resolve()
    run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    start_iso = datetime.now(UTC).isoformat()
    start_perf = time.perf_counter()

    spec_file = resolve_confined_path(spec_path, repo_root=repo_root, must_exist=True)

    # 1. Verify clean worktree before starting
    check_worktree_clean(repo_root)

    # 2. Acquire advisory execution lock
    with acquire_repo_lock(repo_root, run_id=run_id):
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
            out_path = repo_root / ".hybrid_sdlc" / "runs" / f"{run_id}.json"
            atomic_save_json(out_path, result, repo_root=repo_root)
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
            patch_path = repo_root / ".hybrid_sdlc" / "runs" / f"{run_id}_att{attempt_idx}.patch"
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
        out_path = repo_root / ".hybrid_sdlc" / "runs" / f"{run_id}.json"
        atomic_save_json(out_path, result, repo_root=repo_root)
        return result
