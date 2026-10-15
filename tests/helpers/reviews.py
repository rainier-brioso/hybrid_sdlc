"""Real Git candidates shared by independent acceptance regression tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from hybrid_sdlc.artifacts import atomic_save_json
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.worktrees import create_worktree, export_worktree_result


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def make_candidate(
    root: Path, *, test_code: str, policy_paths: list[str] | None = None
) -> tuple[RunResult, Path, bytes, str]:
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Review Fixture")
    git(root, "config", "user.email", "review@example.invalid")
    (root / ".gitignore").write_text(".hybrid_sdlc/\n", encoding="utf-8")
    (root / "candidate.txt").write_text("baseline\n", encoding="utf-8")
    # Explicit external paths are intentionally rejected by command policy;
    # resolve this approved interpreter's bare name from the test runner PATH.
    profile_command = json.dumps([Path(sys.executable).name, "-c", test_code])
    config_text = f'[command_profiles.review]\nargv = {profile_command}\ncwd = "."\n'
    (root / "hybrid_sdlc.toml").write_text(config_text, encoding="utf-8")
    git(root, "add", ".gitignore", "candidate.txt", "hybrid_sdlc.toml")
    if policy_paths is not None:
        (root / "task.md").write_text("# REVIEW-001\nUpdate candidate.txt.\n", encoding="utf-8")
        policy = {
            "schema_version": "1.0.0",
            "task_id": "REVIEW-001",
            "spec_path": "task.md",
            "test_profile": "review",
            "intended_paths": policy_paths,
            "excluded_paths": [],
            "limits": {
                "max_retries": 2,
                "task_timeout_seconds": 600,
                "attempt_timeout_seconds": 180,
            },
        }
        (root / "task.policy.json").write_text(json.dumps(policy), encoding="utf-8")
        git(root, "add", "task.md", "task.policy.json")
    git(root, "commit", "-m", "fixture baseline")
    baseline = git(root, "rev-parse", "HEAD")
    candidate = create_worktree(root)
    (candidate.path / "candidate.txt").write_text("reviewed change\n", encoding="utf-8")
    exported = export_worktree_result(candidate)
    patch_bytes = exported.patch_path.read_bytes()
    (candidate.record_path.parent / ".hybrid_sdlc").mkdir()
    run_id = "run_review_fixture"
    record = RunResult(
        run_id=run_id,
        task_id="REVIEW-001",
        spec_path="task.md",
        repo_root=str(root),
        source_repo_root=str(root),
        worktree_path=str(candidate.path),
        review_patch=str(exported.patch_path),
        status=RunStatus.SUCCESS,
        started_at=datetime.now(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        total_duration_seconds=0.1,
        baseline_commit=baseline,
        worker_model="fixture-worker-model",
        artifacts={
            "run_record": f".hybrid_sdlc/runs/{run_id}.json",
            "review_patch": str(exported.patch_path),
            "worktree_record": str(candidate.record_path),
        },
    )
    if policy_paths is not None:
        record = record.model_copy(
            update={
                "policy_path": "task.policy.json",
                "policy_sha256": hashlib.sha256(
                    git(root, "show", "HEAD:task.policy.json").encode()
                ).hexdigest(),
                "spec_sha256": hashlib.sha256(
                    (git(root, "show", "HEAD:task.md") + "\n").encode()
                ).hexdigest(),
                "policy_baseline_commit": baseline,
            }
        )
    run_record = root / ".hybrid_sdlc" / "runs" / f"{run_id}.json"
    atomic_save_json(run_record, record, root)
    return record, run_record, patch_bytes, baseline
