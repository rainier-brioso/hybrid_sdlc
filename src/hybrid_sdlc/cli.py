"""Canonical Command Line Interface for hybrid-sdlc."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from hybrid_sdlc import __version__
from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.artifacts import clean_artifacts
from hybrid_sdlc.command_profiles import resolve_profile_executable
from hybrid_sdlc.config import ServerCandidateConfig, load_config
from hybrid_sdlc.errors import (
    ConfigurationError,
    ExitCode,
    HybridSDLCError,
    ServerProbeError,
)
from hybrid_sdlc.models import ProbeResult, RunStatus
from hybrid_sdlc.security import verify_repo_root
from hybrid_sdlc.server_probe import probe_endpoint, select_active_endpoint


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
@click.option("--json", "json_mode", is_flag=True, help="Output only structured RunResult JSON.")
def run_task_cmd(
    spec_file: Path,
    repo_root: Path | None,
    task_id: str,
    test_profile: str,
    host_url: str | None,
    model: str | None,
    max_retries: int | None,
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

    # 1. Preflight: reject dirty tree BEFORE contacting model
    from hybrid_sdlc.git_tools import check_worktree_clean

    try:
        check_worktree_clean(verified_root)
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

    if result.status == RunStatus.SUCCESS:
        sys.exit(ExitCode.SUCCESS)
    sys.exit(ExitCode.TASK_FAILED)


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
