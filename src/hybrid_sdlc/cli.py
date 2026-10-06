"""Canonical Command Line Interface for hybrid-sdlc."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import click

from hybrid_sdlc import __version__
from hybrid_sdlc.aider_runner import run_bounded_loop, validate_aider_target_files
from hybrid_sdlc.artifacts import clean_artifacts
from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import ServerCandidateConfig, load_config
from hybrid_sdlc.errors import (
    ConfigurationError,
    ExitCode,
    HybridSDLCError,
    ServerProbeError,
)
from hybrid_sdlc.git_tools import validate_committed_path
from hybrid_sdlc.host_setup import HostSetupError, setup_antigravity
from hybrid_sdlc.job_manager import InvalidJobTransitionError, JobManager, JobRecord, JobStatus
from hybrid_sdlc.models import ProbeResult, RunStatus
from hybrid_sdlc.runtime_strata import (
    RuntimeManagementError,
)
from hybrid_sdlc.runtime_strata import (
    configure as configure_strata_runtime,
)
from hybrid_sdlc.runtime_strata import (
    logs as strata_runtime_logs,
)
from hybrid_sdlc.runtime_strata import (
    restart as restart_strata_runtime,
)
from hybrid_sdlc.runtime_strata import (
    start as start_strata_runtime,
)
from hybrid_sdlc.runtime_strata import (
    status as strata_runtime_status,
)
from hybrid_sdlc.runtime_strata import (
    stop as stop_strata_runtime,
)
from hybrid_sdlc.security import resolve_confined_path, verify_repo_root
from hybrid_sdlc.server_probe import probe_endpoint, select_active_endpoint
from hybrid_sdlc.spec_initializer import (
    InitializationError,
    initialize_spec_kit,
)
from hybrid_sdlc.submission import JobSubmissionError, submit_job
from hybrid_sdlc.worker import run_worker


def parse_duration_seconds(val: str) -> float:
    """Parse a human duration string (e.g. 7d, 24h, 30m, 60s) into seconds."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    s = val.strip().lower()
    if not s:
        raise ConfigurationError("Duration string cannot be empty", code="CONFIG_INVALID_DURATION")
    if s[-1] in units:
        unit = s[-1]
        num_str = s[:-1]
        try:
            num = float(num_str)
            if num < 0:
                raise ValueError()
            return num * units[unit]
        except ValueError:
            raise ConfigurationError(
                f"Invalid duration format '{val}'. Expected number followed by s, m, h, d, or w.",
                code="CONFIG_INVALID_DURATION",
            ) from None
    try:
        num = float(s)
        if num < 0:
            raise ValueError()
        return num
    except ValueError:
        raise ConfigurationError(
            f"Invalid duration format '{val}'. Expected numeric value or duration suffix (e.g. 7d).",
            code="CONFIG_INVALID_DURATION",
        ) from None


@click.group()
@click.version_option(version=__version__, package_name="hybrid-sdlc")
def cli() -> None:
    """Hybrid Spec-Driven SDLC Toolkit."""
    pass


@cli.group("setup")
def setup_group() -> None:
    """Configure optional integrations with host applications."""


@setup_group.command("antigravity")
@click.option("--dry-run", is_flag=True, help="Preview the MCP config change without writing it.")
def setup_antigravity_cmd(dry_run: bool) -> None:
    """Register hybrid-sdlc in Antigravity's user-level MCP configuration."""

    try:
        result = setup_antigravity(dry_run=dry_run)
    except HostSetupError as exc:
        raise click.ClickException(str(exc)) from exc

    if not result.changed:
        click.echo(f"Already configured in {result.config_path}")
        return
    if result.dry_run:
        if result.proposal_path is not None:
            click.echo(f"Would prepare a proposal for {result.config_path}")
            click.echo(f"  proposal: {result.proposal_path}")
        else:
            click.echo(f"Would add hybrid-sdlc to {result.config_path}")
    elif result.proposal_path is not None:
        click.echo(f"Prepared a proposal for {result.config_path}")
        click.echo("  original config was not changed")
        click.echo(f"  proposal: {result.proposal_path}")
        click.echo(
            "  close Antigravity, review the proposal, then merge its hybrid-sdlc entry "
            "into the current config manually"
        )
    else:
        click.echo(f"Added hybrid-sdlc to {result.config_path}")
    click.echo(f"  command: {result.executable}")
    click.echo("  args: mcp")


@cli.group("runtime")
def runtime_group() -> None:
    """Manage explicitly configured local inference runtimes."""


@runtime_group.group("strata")
def runtime_strata_group() -> None:
    """Manage this user's opt-in Docker Strata service."""


def _runtime_result(result: dict[str, object], *, json_mode: bool) -> None:
    if json_mode:
        click.echo(json.dumps(result, indent=2))
        return
    for key, value in result.items():
        click.echo(f"{key}: {value}")


def _runtime_error(exc: RuntimeManagementError, *, json_mode: bool) -> NoReturn:
    if json_mode:
        click.echo(
            json.dumps(
                {"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                indent=2,
            ),
            err=True,
        )
        raise click.exceptions.Exit(int(exc.exit_code)) from exc
    raise click.ClickException(exc.message) from exc


@runtime_strata_group.command("configure")
@click.option("--port", type=click.IntRange(1, 65535), default=8080, show_default=True)
@click.option(
    "--reuse-model-volume",
    "model_volume",
    help="Explicitly reuse an existing Docker volume by name for model files.",
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_configure(port: int, model_volume: str | None, json_mode: bool) -> None:
    """Write the managed runtime definition; this does not contact Docker or start it."""

    try:
        result = configure_strata_runtime(port=port, model_volume=model_volume)
    except RuntimeManagementError as exc:
        _runtime_error(exc, json_mode=json_mode)
    _runtime_result(result, json_mode=json_mode)


def _runtime_action(action: Callable[[], dict[str, object]], json_mode: bool) -> None:
    try:
        result = action()
    except RuntimeManagementError as exc:
        _runtime_error(exc, json_mode=json_mode)
    _runtime_result(result, json_mode=json_mode)


@runtime_strata_group.command("start")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_start(json_mode: bool) -> None:
    """Start the managed service without building or pulling its image."""

    _runtime_action(start_strata_runtime, json_mode)


@runtime_strata_group.command("stop")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_stop(json_mode: bool) -> None:
    """Stop the managed service after checking for queued or running tasks."""

    _runtime_action(stop_strata_runtime, json_mode)


@runtime_strata_group.command("restart")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_restart(json_mode: bool) -> None:
    """Restart the managed service after checking for queued or running tasks."""

    _runtime_action(restart_strata_runtime, json_mode)


@runtime_strata_group.command("status")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_status_cmd(json_mode: bool) -> None:
    """Report the managed Docker container state without probing model readiness."""

    _runtime_action(strata_runtime_status, json_mode)


@runtime_strata_group.command("logs")
@click.option("--tail", type=click.IntRange(1, 10000), default=100, show_default=True)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def runtime_strata_logs_cmd(tail: int, json_mode: bool) -> None:
    """Show bounded logs from the managed service."""

    try:
        result = strata_runtime_logs(tail=tail)
    except RuntimeManagementError as exc:
        _runtime_error(exc, json_mode=json_mode)
    _runtime_result(result, json_mode=json_mode)


@cli.command("init")
@click.option(
    "--target-dir",
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
    default=None,
    help="Existing Git repository to initialize (defaults to the current directory).",
)
@click.option("--dry-run", is_flag=True, help="Show the initialization plan without writing files.")
@click.option("--force", is_flag=True, help="Back up and replace customized managed templates.")
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def init_cmd(target_dir: Path | None, dry_run: bool, force: bool, json_mode: bool) -> None:
    """Install Hybrid SDLC's canonical Spec Kit templates in a Git repository."""
    try:
        verified_root = verify_repo_root(target_dir or Path.cwd())
        result = initialize_spec_kit(verified_root, force=force, dry_run=dry_run)
    except HybridSDLCError as exc:
        if json_mode:
            click.echo(json.dumps(exc.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Error [{exc.code}]: {exc.message}", err=True)
        sys.exit(exc.exit_code)
    except (InitializationError, ValueError, OSError) as exc:
        if json_mode:
            click.echo(
                json.dumps(
                    {"code": "SPEC_INITIALIZATION_FAILED", "message": str(exc), "details": {}},
                    indent=2,
                )
            )
        else:
            click.echo(f"Initialization failed: {exc}", err=True)
        sys.exit(ExitCode.POLICY_ERROR)

    files = [
        {
            "path": item.path,
            "action": ("would_" + item.action.value) if dry_run else item.action.value,
            "backup_path": item.backup_path,
        }
        for item in result.files
    ]
    if json_mode:
        click.echo(
            json.dumps(
                {
                    "repository": str(verified_root),
                    "dry_run": dry_run,
                    "files": files,
                    "manifest_path": result.manifest_path,
                    "spec_kit": {
                        "executable": result.spec_kit.executable,
                        "version": result.spec_kit.version,
                        "compatible": result.spec_kit.compatible,
                        "features": {
                            feature.name: feature.enabled for feature in result.spec_kit.features
                        },
                    },
                },
                indent=2,
            )
        )
        return
    verb = "Would initialize" if dry_run else "Initialized"
    click.echo(f"{verb} Spec Kit files in {verified_root}:")
    for item in result.files:
        action = item.action.value
        if dry_run:
            action = "would " + action
        click.echo(f"  {item.path}: {action}")
        if item.backup_path:
            click.echo(f"    backup: {item.backup_path}")
    if not result.spec_kit.compatible:
        click.echo(result.spec_kit.guidance, err=True)


@cli.command("check")
@click.option("--host-url", help="Explicit inference host URL to check.")
@click.option("--probe-all", is_flag=True, help="Probe all configured candidate server endpoints.")
@click.option("--readiness", is_flag=True, help="Execute a lightweight test inference prompt.")
@click.option("--model", help="Override required model identifier.")
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def check_cmd(
    host_url: str | None,
    probe_all: bool,
    readiness: bool,
    model: str | None,
    repo_root: Path | None,
    json_mode: bool,
) -> None:
    """Check local inference server health, model availability, and readiness."""
    try:
        verified_root = verify_repo_root(repo_root)
        config = load_config(repo_root=verified_root)
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Configuration error: {e.message}", err=True)
        sys.exit(e.exit_code)

    model_to_verify = model or config.selected_model
    results: list[ProbeResult] = []

    if host_url:
        candidates = [ServerCandidateConfig(url=host_url, model_alias=model_to_verify)]
    else:
        candidates = config.server_candidates

    any_healthy = False
    if probe_all or len(candidates) == 1:
        for c in candidates:
            res = probe_endpoint(
                url=c.url,
                required_model=model_to_verify,
                check_readiness=readiness,
            )
            results.append(res)
            if res.available:
                any_healthy = True
    else:
        try:
            chosen, chosen_res = select_active_endpoint(
                candidates=candidates,
                required_model=model_to_verify,
                check_readiness=readiness,
            )
            results.append(chosen_res)
            any_healthy = True
        except ServerProbeError as e:
            any_healthy = False
            if json_mode:
                click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
                sys.exit(ExitCode.SERVER_PROBE_ERROR)
            else:
                click.echo(f"Server check failed: {e.message}", err=True)
                sys.exit(ExitCode.SERVER_PROBE_ERROR)

    if json_mode:
        if len(results) == 1:
            click.echo(results[0].model_dump_json(indent=2))
        else:
            dumped = [r.model_dump(mode="json") for r in results]
            click.echo(json.dumps(dumped, indent=2))
    else:
        for r in results:
            status_str = "HEALTHY" if r.available else "UNAVAILABLE"
            click.echo(f"Endpoint: {r.url} -> {status_str}")
            if r.available:
                click.echo(f"  Model: {r.matched_model or 'default'}")
                if readiness:
                    click.echo(f"  Readiness: {'PASSED' if r.readiness_passed else 'FAILED'}")
            elif r.error:
                click.echo(f"  Error [{r.error.code}]: {r.error.message}")

    sys.exit(ExitCode.SUCCESS if any_healthy else ExitCode.SERVER_PROBE_ERROR)


@cli.command("run-task")
@click.argument("spec_file", type=click.Path(path_type=Path))
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--task-id", required=True, help="Spec task identifier to implement.")
@click.option(
    "--test-profile", required=True, help="Named test command profile in hybrid_sdlc.toml."
)
@click.option("--host-url", help="Override inference host URL.")
@click.option("--model", help="Override target model name.")
@click.option("--max-retries", type=int, help="Override maximum edit attempts.")
@click.option(
    "--commit",
    "commit_requested",
    is_flag=True,
    help="Create a task-scoped commit after the final tests pass.",
)
@click.option(
    "--rollback-on-failure",
    is_flag=True,
    help="Discard failed isolated changes after saving the review patch.",
)
@click.option("--json", "json_mode", is_flag=True, help="Output only structured RunResult JSON.")
def run_task_cmd(
    spec_file: Path,
    repo_root: Path | None,
    task_id: str,
    test_profile: str,
    host_url: str | None,
    model: str | None,
    max_retries: int | None,
    commit_requested: bool,
    rollback_on_failure: bool,
    json_mode: bool,
) -> None:
    """Synchronously execute a delegated spec task through the bounded editing and testing loop."""
    try:
        verified_root = verify_repo_root(repo_root)
        config = load_config(repo_root=verified_root)
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Error: {e.message}", err=True)
        sys.exit(e.exit_code)

    if test_profile not in config.command_profiles:
        err = ConfigurationError(
            f"Test profile '{test_profile}' not found in configuration",
            code="CONFIG_PROFILE_NOT_FOUND",
            details={"available_profiles": list(config.command_profiles.keys())},
        )
        if json_mode:
            click.echo(json.dumps(err.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Error: {err.message}", err=True)
        sys.exit(ExitCode.CONFIG_ERROR)

    profile = config.command_profiles[test_profile]
    retries = max_retries if max_retries is not None else config.max_retries
    target_model = model or config.selected_model

    # Validate the committed spec before contacting the model. Other source
    # checkout changes are safe because execution occurs in a detached worktree.
    try:
        resolved_spec = resolve_confined_path(spec_file, verified_root, must_exist=True)
        validate_committed_path(verified_root, resolved_spec.relative_to(verified_root).as_posix())
        validate_aider_target_files(
            verified_root, resolved_spec, [Path(path) for path in config.aider_edit_files]
        )
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Worktree check failed: {e.message}", err=True)
        sys.exit(e.exit_code)

    # Resolve once before model contact. The exact approved executable is then
    # reused for baseline and attempt tests.
    try:
        resolved_test_executable = resolve_profile_executable(profile, verified_root)
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Command profile error: {e.message}", err=True)
        sys.exit(e.exit_code)

    # 2. Check inference endpoint
    try:
        candidate, probe_res = select_active_endpoint(
            candidates=config.server_candidates,
            explicit_url=host_url,
            required_model=target_model,
        )
        endpoint_url = candidate.url
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Server probe failed: {e.message}", err=True)
        sys.exit(e.exit_code)

    try:
        result = run_bounded_loop(
            repo_root=verified_root,
            spec_path=spec_file,
            task_id=task_id,
            profile=profile,
            endpoint_url=endpoint_url,
            model_name=target_model,
            max_retries=retries,
            task_timeout_seconds=float(config.task_timeout_seconds),
            attempt_timeout_seconds=float(config.attempt_timeout_seconds),
            buffer_cap_bytes=config.log_buffer_cap_bytes,
            resolved_test_executable=resolved_test_executable,
            commit_requested=commit_requested,
            rollback_on_failure=rollback_on_failure,
            repo_map_tokens=config.aider_repo_map_tokens,
            target_files=[Path(path) for path in config.aider_edit_files],
        )
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Task failed before loop: {e.message}", err=True)
        sys.exit(e.exit_code)

    if json_mode:
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(f"Task {task_id}: {result.status.value.upper()}")
        click.echo(f"  Duration: {result.total_duration_seconds:.1f}s")
        click.echo(f"  Attempts: {len(result.attempts)}")
        if result.status == RunStatus.SUCCESS:
            click.echo("  Result: Tests passed successfully!")
            if result.final_diff:
                click.echo(f"  Changed files: {', '.join(result.final_diff.changed_files)}")
        else:
            if result.failure:
                click.echo(f"  Failure [{result.failure.code}]: {result.failure.message}")
        if result.worktree_path:
            click.echo(f"  Isolated worktree: {result.worktree_path}")
        if result.review_patch:
            click.echo(f"  Review patch: {result.review_patch}")
        if result.commit_hash:
            click.echo(f"  Commit: {result.commit_hash}")
        if result.worktree_rolled_back:
            click.echo("  Failed isolated worktree was rolled back after patch export.")
        elif result.worktree_path:
            click.echo("  Isolated worktree retained for inspection.")

    if result.status == RunStatus.SUCCESS:
        sys.exit(ExitCode.SUCCESS)
    sys.exit(ExitCode.TASK_FAILED)


def _async_error(
    message: str,
    *,
    code: str,
    exit_code: ExitCode,
    json_mode: bool,
    details: dict[str, object] | None = None,
) -> NoReturn:
    """Emit one stable error shape for asynchronous job commands."""

    if json_mode:
        click.echo(
            json.dumps(
                {
                    "code": code,
                    "message": message,
                    "details": details or {},
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Error [{code}]: {message}", err=True)
    sys.exit(exit_code)


def _emit_job(record: JobRecord, *, json_mode: bool) -> None:
    if json_mode:
        click.echo(record.model_dump_json(indent=2))
    else:
        click.echo(f"Job {record.job_id}: {record.status.value.upper()}")


@cli.command("submit")
@click.argument("spec_file", type=click.Path(path_type=Path))
@click.option(
    "--repo-root", type=click.Path(path_type=Path), required=True, help="Target repository root."
)
@click.option("--task-id", required=True, help="Spec task identifier to implement.")
@click.option("--test-profile", required=True, help="Named test command profile.")
@click.option("--host-url", help="Override inference host URL.")
@click.option("--model", help="Override target model name.")
@click.option("--max-retries", type=int, help="Override maximum edit attempts.")
@click.option("--json", "json_mode", is_flag=True, help="Output only the persisted job record.")
def submit_cmd(
    spec_file: Path,
    repo_root: Path,
    task_id: str,
    test_profile: str,
    host_url: str | None,
    model: str | None,
    max_retries: int | None,
    json_mode: bool,
) -> None:
    """Validate, queue, and start an asynchronous spec task."""

    try:
        record = submit_job(
            spec_path=spec_file,
            repo_root=repo_root,
            task_id=task_id,
            test_profile=test_profile,
            host_url=host_url,
            model=model,
            max_retries=max_retries,
        )
    except JobSubmissionError as exc:
        _async_error(
            str(exc),
            code="JOB_SUBMISSION_FAILED",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
            details={"job_id": exc.job_id},
        )
    except HybridSDLCError as exc:
        _async_error(
            exc.message,
            code=exc.code,
            exit_code=exc.exit_code,
            json_mode=json_mode,
            details=exc.details,
        )
    except (ValueError, OSError) as exc:
        _async_error(
            str(exc),
            code="INVALID_SUBMISSION",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    if json_mode:
        _emit_job(record, json_mode=True)
    else:
        click.echo(f"Submitted job {record.job_id} ({record.status.value.upper()})")


@cli.command("status")
@click.argument("job_id", required=False)
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--wait", "wait_for_completion", is_flag=True, help="Wait up to 60 seconds.")
@click.option(
    "--poll-interval",
    type=float,
    default=0.25,
    show_default=True,
    help="Polling interval in seconds (0.01 to 5).",
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def status_cmd(
    job_id: str | None,
    repo_root: Path | None,
    wait_for_completion: bool,
    poll_interval: float,
    json_mode: bool,
) -> None:
    """Show one job's status, or list validated jobs in the repository."""

    if wait_for_completion and job_id is None:
        _async_error(
            "--wait requires a job ID",
            code="INVALID_STATUS_OPTIONS",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    try:
        verified_root = verify_repo_root(repo_root)
        manager = JobManager(verified_root)
        if job_id is None:
            records = manager.list_jobs()
            if json_mode:
                click.echo(
                    json.dumps(
                        {"jobs": [item.model_dump(mode="json") for item in records]}, indent=2
                    )
                )
            elif not records:
                click.echo("No jobs found.")
            else:
                for record in records:
                    click.echo(f"Job {record.job_id}: {record.status.value.upper()}")
            return
        record = manager.status(
            job_id,
            wait_timeout_seconds=60.0 if wait_for_completion else 0.0,
            poll_interval_seconds=poll_interval,
        )
    except HybridSDLCError as exc:
        _async_error(
            exc.message,
            code=exc.code,
            exit_code=exc.exit_code,
            json_mode=json_mode,
            details=exc.details,
        )
    except FileNotFoundError:
        _async_error(
            "Job not found",
            code="JOB_NOT_FOUND",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    except ValueError as exc:
        code = "INVALID_JOB_ID" if str(exc) == "Invalid job ID" else "INVALID_STATUS_OPTIONS"
        _async_error(
            str(exc),
            code=code,
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    _emit_job(record, json_mode=json_mode)


@cli.command("cancel")
@click.argument("job_id")
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--json", "json_mode", is_flag=True, help="Output machine-readable JSON.")
def cancel_cmd(job_id: str, repo_root: Path | None, json_mode: bool) -> None:
    """Request cancellation of a queued or running asynchronous job."""

    try:
        verified_root = verify_repo_root(repo_root)
        record = JobManager(verified_root).cancel(job_id)
    except HybridSDLCError as exc:
        _async_error(
            exc.message,
            code=exc.code,
            exit_code=exc.exit_code,
            json_mode=json_mode,
            details=exc.details,
        )
    except FileNotFoundError:
        _async_error(
            "Job not found",
            code="JOB_NOT_FOUND",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    except ValueError as exc:
        _async_error(
            str(exc),
            code="INVALID_JOB_ID",
            exit_code=ExitCode.POLICY_ERROR,
            json_mode=json_mode,
        )
    if record.status is JobStatus.RUNNING:
        cancellation = "requested"
    elif record.status is JobStatus.CANCELLED:
        cancellation = "cancelled"
    else:
        cancellation = "already_terminal"
    if json_mode:
        click.echo(
            json.dumps(
                {"job": record.model_dump(mode="json"), "cancellation": cancellation}, indent=2
            )
        )
    elif cancellation == "requested":
        click.echo(f"Cancellation requested for job {record.job_id}; status remains RUNNING.")
    elif cancellation == "already_terminal":
        click.echo(f"Job {record.job_id} is already {record.status.value.upper()}.")
    elif cancellation == "cancelled":
        click.echo(f"Job {record.job_id}: CANCELLED")
    else:
        click.echo(f"Job {record.job_id}: CANCELLED")


@cli.command("worker")
@click.argument("job_id")
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--json", "json_mode", is_flag=True, help="Output the final persisted job record.")
def worker_cmd(job_id: str, repo_root: Path | None, json_mode: bool) -> None:
    """Claim and execute one queued asynchronous job."""
    try:
        record = run_worker(job_id, repo_root)
    except InvalidJobTransitionError as exc:
        click.echo(f"Worker could not claim job: {exc}", err=True)
        sys.exit(ExitCode.POLICY_ERROR)
    except (HybridSDLCError, FileNotFoundError, ValueError) as exc:
        message = exc.message if isinstance(exc, HybridSDLCError) else str(exc)
        click.echo(f"Worker failed: {message}", err=True)
        sys.exit(exc.exit_code if isinstance(exc, HybridSDLCError) else ExitCode.POLICY_ERROR)

    if json_mode:
        click.echo(record.model_dump_json(indent=2))
    else:
        click.echo(f"Job {record.job_id}: {record.status.value.upper()}")
        if record.failure_reason:
            click.echo(f"  Failure: {record.failure_reason}")
    sys.exit(ExitCode.SUCCESS if record.status is JobStatus.COMPLETED else ExitCode.TASK_FAILED)


@cli.command("mcp")
def mcp_cmd() -> None:
    """Run the stdio Model Context Protocol adapter."""
    from hybrid_sdlc.mcp_server import main

    main()


@cli.command("clean")
@click.option(
    "--repo-root", type=click.Path(path_type=Path), default=None, help="Target repository root."
)
@click.option("--older-than", default="7d", help="Retention threshold (e.g. 7d, 24h, 3600s).")
@click.option("--dry-run", is_flag=True, help="Display files to be deleted without removing them.")
@click.option("--json", "json_mode", is_flag=True, help="Output results as JSON.")
def clean_cmd(
    repo_root: Path | None,
    older_than: str,
    dry_run: bool,
    json_mode: bool,
) -> None:
    """Remove expired artifacts under .hybrid_sdlc/ older than the specified duration."""
    try:
        verified_root = verify_repo_root(repo_root)
        seconds = parse_duration_seconds(older_than)
        cleanup = clean_artifacts(
            repo_root=verified_root, older_than_seconds=seconds, dry_run=dry_run
        )
    except HybridSDLCError as e:
        if json_mode:
            click.echo(json.dumps(e.to_failure_record().model_dump(mode="json"), indent=2))
        else:
            click.echo(f"Error: {e.message}", err=True)
        sys.exit(e.exit_code)

    if json_mode:
        click.echo(
            json.dumps(
                {
                    "deleted_count": len(cleanup.deleted),
                    "deleted_files": cleanup.deleted,
                    "skipped_active": cleanup.skipped_active,
                    "dry_run": dry_run,
                },
                indent=2,
            )
        )
    else:
        action = "Would delete" if dry_run else "Deleted"
        click.echo(f"{action} {len(cleanup.deleted)} artifact file(s) older than {older_than}:")
        for f in cleanup.deleted:
            click.echo(f"  {f}")
        if cleanup.skipped_active:
            click.echo(f"Skipped {len(cleanup.skipped_active)} active artifact(s):")
            for f in cleanup.skipped_active:
                click.echo(f"  {f}")

    sys.exit(ExitCode.SUCCESS)


if __name__ == "__main__":
    cli()
