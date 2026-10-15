"""Checked policy ingress, persisted provenance and exported scope enforcement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from mcp import Client

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.cli import cli
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.config import ServerCandidateConfig, load_config
from hybrid_sdlc.errors import RepositoryError
from hybrid_sdlc.job_manager import JobManager
from hybrid_sdlc.mcp_server import mcp
from hybrid_sdlc.models import FailureRecord, RunResult, RunStatus
from hybrid_sdlc.submission import JobSubmissionError, submit_job
from hybrid_sdlc.task_policy import TaskPolicyError, validate_task_policy
from hybrid_sdlc.worker import _execute


@pytest.fixture(autouse=True)
def _isolate_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._state_root", lambda: tmp_path / "runtime-state"
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _task_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Policy test")
    _git(root, "config", "user.email", "policy-test@example.invalid")
    (root / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    (root / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["python", "-c", "pass"]\n',
        encoding="utf-8",
    )
    spec = root / "docs" / "tasks" / "task.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Task contract\n", encoding="utf-8")
    config = load_config(repo_root=root)
    policy = {
        "schema_version": "1.0.0",
        "task_id": "POLICY-001",
        "spec_path": "docs/tasks/task.md",
        "test_profile": "pytest",
        "intended_paths": ["generated.py"],
        "excluded_paths": [],
        "limits": {
            "max_retries": config.max_retries,
            "task_timeout_seconds": config.task_timeout_seconds,
            "attempt_timeout_seconds": config.attempt_timeout_seconds,
        },
    }
    policy_text = json.dumps(policy)
    (root / "task-policy.json").write_text(policy_text, encoding="utf-8")
    (root / "docs" / "tasks" / "task.policy.json").write_text(policy_text, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "Add policy fixture")
    return "docs/tasks/task.md"


def test_cli_accepts_explicit_committed_policy_and_passes_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            run_id="run-policy-cli",
            task_id="POLICY-001",
            spec_path=spec_path,
            repo_root=str(tmp_path),
            status=RunStatus.SUCCESS,
            started_at="2026-10-14T00:00:00Z",
            finished_at="2026-10-14T00:00:01Z",
            total_duration_seconds=1,
        )

    monkeypatch.setattr(
        "hybrid_sdlc.cli.resolve_profile_executable", lambda *args: tmp_path / "python"
    )
    monkeypatch.setattr(
        "hybrid_sdlc.cli.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8089/v1"), None),
    )
    monkeypatch.setattr("hybrid_sdlc.cli.validate_aider_target_files", lambda *args: [])
    monkeypatch.setattr("hybrid_sdlc.cli.run_bounded_loop", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "run-task",
            spec_path,
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "POLICY-001",
            "--test-profile",
            "pytest",
            "--policy-file",
            "docs/tasks/task.policy.json",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    provenance = captured["policy_provenance"]
    assert provenance.policy_path == "docs/tasks/task.policy.json"  # type: ignore[union-attr]
    assert provenance.spec_sha256  # type: ignore[union-attr]
    assert captured["test_profile"] == "pytest"


def test_cli_rejects_modified_policy_before_endpoint_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    (tmp_path / "task-policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "hybrid_sdlc.cli.select_active_endpoint",
        lambda **kwargs: pytest.fail("invalid policy must be rejected before probing"),
    )

    result = CliRunner().invoke(
        cli,
        [
            "run-task",
            spec_path,
            "--repo-root",
            str(tmp_path),
            "--task-id",
            "POLICY-001",
            "--test-profile",
            "pytest",
            "--policy-file",
            "task-policy.json",
            "--json",
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.output)["code"] == "TASK_POLICY_INVALID"


def test_submit_rejects_uncommitted_policy_before_endpoint_or_job_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    (tmp_path / "task-policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: pytest.fail("invalid policy must be rejected before probing"),
    )

    with pytest.raises(TaskPolicyError):
        submit_job(
            repo_root=tmp_path,
            spec_path=spec_path,
            task_id="POLICY-001",
            test_profile="pytest",
            policy_path="task-policy.json",
        )
    assert not (tmp_path / ".hybrid_sdlc" / "jobs").exists()


def test_submit_rejects_untracked_policy_before_endpoint_or_job_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    (tmp_path / "untracked-policy.json").write_bytes((tmp_path / "task-policy.json").read_bytes())
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: pytest.fail("untracked policy must be rejected before probing"),
    )

    with pytest.raises(TaskPolicyError, match="committed regular files"):
        submit_job(
            repo_root=tmp_path,
            spec_path=spec_path,
            task_id="POLICY-001",
            test_profile="pytest",
            policy_path="untracked-policy.json",
        )
    assert not (tmp_path / ".hybrid_sdlc" / "jobs").exists()


def test_policy_rejects_content_change_even_with_git_assume_unchanged(
    tmp_path: Path,
) -> None:
    spec_path = _task_repo(tmp_path)
    policy_file = tmp_path / "task-policy.json"
    original = policy_file.read_bytes()
    _git(tmp_path, "update-index", "--assume-unchanged", "task-policy.json")
    try:
        policy_file.write_text("{}", encoding="utf-8")
        config = load_config(repo_root=tmp_path)
        with pytest.raises(TaskPolicyError, match="committed and unchanged"):
            validate_task_policy(
                repo_root=tmp_path,
                policy_path="task-policy.json",
                spec_path=spec_path,
                task_id="POLICY-001",
                test_profile="pytest",
                max_retries=config.max_retries,
                config=config,
            )
    finally:
        policy_file.write_bytes(original)
        _git(tmp_path, "update-index", "--no-assume-unchanged", "task-policy.json")


def test_async_submit_persists_pinned_provenance_before_worker_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8089/v1"), None),
    )
    monkeypatch.setattr("hybrid_sdlc.submission._verify_windows_worker_breakaway", lambda: None)
    monkeypatch.setattr("hybrid_sdlc.submission.release_async_task", lambda *args: None)
    monkeypatch.setattr(
        "hybrid_sdlc.submission.reserve_async_task",
        lambda endpoint, root, create: create("job_20261014T120000000000Z_12345678"),
    )
    monkeypatch.setattr(
        "hybrid_sdlc.submission._launch_worker",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic launch failure")),
    )

    with pytest.raises(JobSubmissionError, match="Worker launch failed"):
        submit_job(
            repo_root=tmp_path,
            spec_path=spec_path,
            task_id="POLICY-001",
            test_profile="pytest",
            policy_path="task-policy.json",
        )

    job = JobManager(tmp_path).get("job_20261014T120000000000Z_12345678")
    expected = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert expected is not None
    assert job.policy_path == expected.policy_path
    assert job.policy_sha256 == expected.policy_sha256
    assert job.spec_sha256 == expected.spec_sha256
    assert job.policy_baseline_commit == expected.baseline_commit


def test_preflight_pins_policy_and_contract_committed_blob_hashes(tmp_path: Path) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )

    assert provenance is not None
    assert len(provenance.policy_sha256) == 64
    assert len(provenance.spec_sha256) == 64
    assert provenance.baseline_commit


def test_policy_provenance_hashes_are_stable_for_crlf_checkout(tmp_path: Path) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    for path in (tmp_path / "task-policy.json", tmp_path / spec_path):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    after_checkout_conversion = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert after_checkout_conversion == provenance


def test_actual_runner_revalidates_policy_in_isolated_worktree_and_records_it(
    tmp_path: Path,
) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    profile = config.command_profiles["pytest"]
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=tmp_path / spec_path,
        task_id="POLICY-001",
        profile=profile,
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        max_retries=config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        aider_cmd=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('generated.py').write_text('value=1\\n')",
        ],
        policy_path="task-policy.json",
        policy_provenance=provenance,
        test_profile="pytest",
    )

    assert result.status is RunStatus.SUCCESS, result.failure
    assert result.policy_path == "task-policy.json"
    assert result.policy_sha256 == provenance.policy_sha256
    assert result.spec_sha256 == provenance.spec_sha256
    assert result.policy_baseline_commit == provenance.baseline_commit
    persisted_path = Path(result.artifacts["run_record"])
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["policy_sha256"] == provenance.policy_sha256


@pytest.mark.parametrize(
    ("changed_path", "excluded", "reason"),
    [("surprise.py", [], "not_intended"), ("blocked.py", ["blocked.py"], "excluded")],
)
def test_policy_scope_rejects_candidate_and_preserves_patch_without_commit(
    tmp_path: Path,
    changed_path: str,
    excluded: list[str],
    reason: str,
) -> None:
    spec_path = _task_repo(tmp_path)
    policy_file = tmp_path / "docs" / "tasks" / "task.policy.json"
    policy = json.loads(policy_file.read_text(encoding="utf-8"))
    policy["excluded_paths"] = excluded
    policy_file.write_text(json.dumps(policy) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/tasks/task.policy.json")
    _git(tmp_path, "commit", "-qm", "Set task path exclusions")
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="docs/tasks/task.policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    source_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    helper_code = (
        "from pathlib import Path; "
        "Path('generated.py').write_text('value=1\\n'); "
        f"Path({changed_path!r}).write_text('out of scope\\n')"
    )
    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=tmp_path / spec_path,
        task_id="POLICY-001",
        profile=config.command_profiles["pytest"],
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        max_retries=config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        aider_cmd=[sys.executable, "-c", helper_code],
        policy_path="docs/tasks/task.policy.json",
        policy_provenance=provenance,
        test_profile="pytest",
        commit_requested=True,
    )

    assert result.status is RunStatus.FAILURE
    assert result.failure is not None and result.failure.code == "TASK_SCOPE_VIOLATION"
    assert result.failure.details["violations"] == [{"path": changed_path, "reason": reason}]
    assert result.commit_hash is None
    assert result.final_diff is not None
    assert result.final_diff.changed_files == sorted(["generated.py", changed_path])
    assert result.review_patch is not None
    patch_bytes = Path(result.review_patch).read_bytes()
    assert patch_bytes
    assert result.final_diff.diff_hash == hashlib.sha256(patch_bytes).hexdigest()
    assert Path(result.worktree_path or "").is_dir()
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=result.worktree_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == result.baseline_commit
    )
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()
        == source_head
    )


def test_policy_scope_is_pinned_when_model_edits_policy_to_widen_it(tmp_path: Path) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="docs/tasks/task.policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    code = (
        "import json; from pathlib import Path; "
        "Path('generated.py').write_text('value=1\\n'); "
        "Path('surprise.py').write_text('value=2\\n'); "
        "p=Path('docs/tasks/task.policy.json'); d=json.loads(p.read_text()); "
        "d['intended_paths'].append('surprise.py'); p.write_text(json.dumps(d))"
    )
    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=tmp_path / spec_path,
        task_id="POLICY-001",
        profile=config.command_profiles["pytest"],
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        max_retries=config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        aider_cmd=[sys.executable, "-c", code],
        policy_path="docs/tasks/task.policy.json",
        policy_provenance=provenance,
        test_profile="pytest",
    )

    assert result.failure is not None and result.failure.code == "TASK_SCOPE_VIOLATION"
    assert {entry["path"] for entry in result.failure.details["violations"]} == {
        "docs/tasks/task.policy.json",
        "surprise.py",
    }


def test_scope_failure_preserves_prior_timeout_or_cancellation_evidence() -> None:
    from hybrid_sdlc.aider_runner import _scope_failure_result
    from hybrid_sdlc.errors import TaskScopeViolationError

    original = RunResult(
        run_id="run-scope-after-timeout",
        task_id="POLICY-001",
        spec_path="task.md",
        repo_root=".",
        status=RunStatus.CANCELLED,
        started_at="2026-10-15T00:00:00Z",
        finished_at="2026-10-15T00:00:01Z",
        total_duration_seconds=1,
        failure=FailureRecord(code="CANCELLED", message="Host cancelled the task"),
    )
    error = TaskScopeViolationError(
        "Task changed paths outside its declared scope",
        details={"violations": [{"path": "extra.py", "reason": "not_intended"}]},
    )

    failed = _scope_failure_result(original, error)

    assert failed.status is RunStatus.FAILURE
    assert failed.failure is not None
    assert failed.failure.details["violations"] == [{"path": "extra.py", "reason": "not_intended"}]
    assert failed.failure.details["prior_status"] == "cancelled"
    assert failed.failure.details["prior_failure"]["code"] == "CANCELLED"


def test_policy_scope_catches_nonignored_file_created_by_test_after_edit(tmp_path: Path) -> None:
    spec_path = _task_repo(tmp_path)
    test_command = (
        "from pathlib import Path; "
        "Path('post-test.txt').write_text('created during tests') "
        "if Path('generated.py').exists() else None"
    )
    toml_argv = json.dumps([Path(sys.executable).name, "-c", test_command])
    (tmp_path / "hybrid_sdlc.toml").write_text(
        f"[command_profiles.pytest]\nargv = {toml_argv}\ntimeout_seconds = 10\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "hybrid_sdlc.toml")
    _git(tmp_path, "commit", "-qm", "Add post-test file fixture")
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="docs/tasks/task.policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=tmp_path / spec_path,
        task_id="POLICY-001",
        profile=config.command_profiles["pytest"],
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        max_retries=config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        aider_cmd=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('generated.py').write_text('ok')",
        ],
        policy_path="docs/tasks/task.policy.json",
        policy_provenance=provenance,
        test_profile="pytest",
    )

    assert result.status is RunStatus.FAILURE
    assert result.failure is not None and result.failure.code == "TASK_SCOPE_VIOLATION"
    assert result.attempts[0].test_passed
    assert [entry["path"] for entry in result.failure.details["violations"]] == ["post-test.txt"]


def test_policy_scope_allows_committed_unicode_and_space_paths(tmp_path: Path) -> None:
    spec_path = _task_repo(tmp_path)
    policy_file = tmp_path / "docs" / "tasks" / "task.policy.json"
    policy = json.loads(policy_file.read_text(encoding="utf-8"))
    policy["intended_paths"].append("notes/résumé draft.txt")
    policy_file.write_text(json.dumps(policy) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/tasks/task.policy.json")
    _git(tmp_path, "commit", "-qm", "Allow unicode result path")
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="docs/tasks/task.policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    code = (
        "from pathlib import Path; Path('generated.py').write_text('value=1\\n'); "
        "p=Path('notes/résumé draft.txt'); p.parent.mkdir(); p.write_text('reviewable')"
    )
    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=tmp_path / spec_path,
        task_id="POLICY-001",
        profile=config.command_profiles["pytest"],
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        max_retries=config.max_retries,
        task_timeout_seconds=float(config.task_timeout_seconds),
        attempt_timeout_seconds=float(config.attempt_timeout_seconds),
        aider_cmd=[sys.executable, "-c", code],
        policy_path="docs/tasks/task.policy.json",
        policy_provenance=provenance,
        test_profile="pytest",
        commit_requested=True,
    )

    assert result.status is RunStatus.SUCCESS, result.failure
    assert result.commit_hash
    assert result.final_diff is not None
    assert "notes/résumé draft.txt" in result.final_diff.changed_files


@pytest.mark.parametrize(
    "profile",
    [
        CommandProfile(name="other", argv=["python", "-c", "pass"]),
        CommandProfile(name="pytest", argv=["python", "-c", "raise SystemExit(1)"]),
    ],
)
def test_runner_rejects_profile_drift_before_lease_or_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: CommandProfile
) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    monkeypatch.setattr(
        "hybrid_sdlc.aider_runner._run_bounded_loop_reserved",
        lambda **kwargs: pytest.fail("profile drift must fail before runtime lease"),
    )

    with pytest.raises(RepositoryError, match="configured named test profile"):
        run_bounded_loop(
            repo_root=tmp_path,
            spec_path=spec_path,
            task_id="POLICY-001",
            profile=profile,
            endpoint_url="http://127.0.0.1:8089/v1",
            model_name="synthetic-local-model",
            policy_path="task-policy.json",
            policy_provenance=provenance,
            test_profile="pytest",
        )


def test_worker_rejects_changed_baseline_before_endpoint_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    provenance = validate_task_policy(
        repo_root=tmp_path,
        policy_path="task-policy.json",
        spec_path=spec_path,
        task_id="POLICY-001",
        test_profile="pytest",
        max_retries=config.max_retries,
        config=config,
    )
    assert provenance is not None
    record = JobManager(tmp_path).create(
        spec_path,
        "POLICY-001",
        test_profile="pytest",
        policy_path=provenance.policy_path,
        policy_sha256=provenance.policy_sha256,
        spec_sha256=provenance.spec_sha256,
        policy_baseline_commit=provenance.baseline_commit,
    )
    (tmp_path / "followup.txt").write_text("new baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "followup.txt")
    _git(tmp_path, "commit", "-qm", "Advance baseline")
    monkeypatch.setattr(
        "hybrid_sdlc.worker.select_active_endpoint",
        lambda **kwargs: pytest.fail("changed baseline must fail before endpoint probe"),
    )

    with pytest.raises(TaskPolicyError, match="changed after submission"):
        _execute(
            JobManager(tmp_path), record, cancel_event=None, process_observer=lambda *args: None
        )  # type: ignore[arg-type]


def test_legacy_job_record_without_policy_provenance_remains_readable(tmp_path: Path) -> None:
    _task_repo(tmp_path)
    manager = JobManager(tmp_path)
    job = manager.create("docs/tasks/task.md", "POLICY-001", test_profile="pytest")
    loaded = manager.get(job.job_id)

    assert loaded.policy_path is None
    assert loaded.policy_sha256 is None
    assert loaded.spec_sha256 is None
    assert loaded.policy_baseline_commit is None


def test_sync_mcp_accepts_optional_policy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _task_repo(tmp_path)
    captured: dict[str, object] = {}
    submitted: dict[str, object] = {}
    config = load_config(repo_root=tmp_path)

    def fake_run(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            run_id="run-policy-mcp",
            task_id="POLICY-001",
            spec_path="docs/tasks/task.md",
            repo_root=str(tmp_path),
            status=RunStatus.SUCCESS,
            started_at="2026-10-14T00:00:00Z",
            finished_at="2026-10-14T00:00:01Z",
            total_duration_seconds=1,
        )

    monkeypatch.setattr("hybrid_sdlc.submission.load_config", lambda **kwargs: config)
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: (ServerCandidateConfig(url="http://127.0.0.1:8089/v1"), None),
    )
    monkeypatch.setattr("hybrid_sdlc.mcp_server.load_config", lambda **kwargs: config)
    monkeypatch.setattr("hybrid_sdlc.mcp_server.run_bounded_loop", fake_run)

    def fake_submit(**kwargs: object) -> object:
        submitted.update(kwargs)
        return JobManager(tmp_path).create(
            "docs/tasks/task.md", "POLICY-001", test_profile="pytest"
        )

    monkeypatch.setattr("hybrid_sdlc.mcp_server.submit_job", fake_submit)

    async def invoke() -> tuple[object, object]:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            schemas = {item.name: item.input_schema for item in tools.tools}
            assert "policy_path" not in schemas["run_spec_task_sync"]["required"]
            sync_response = await client.call_tool(
                "run_spec_task_sync",
                {
                    "repo_root": str(tmp_path),
                    "spec_path": "docs/tasks/task.md",
                    "task_id": "POLICY-001",
                    "test_profile": "pytest",
                    "policy_path": "task-policy.json",
                },
            )
            submit_response = await client.call_tool(
                "submit_spec_job",
                {
                    "repo_root": str(tmp_path),
                    "spec_path": "docs/tasks/task.md",
                    "task_id": "POLICY-001",
                    "test_profile": "pytest",
                    "policy_path": "task-policy.json",
                },
            )
            return sync_response, submit_response

    response, submit_response = asyncio.run(invoke())
    assert response.is_error is False  # type: ignore[union-attr]
    assert captured["policy_path"] == "task-policy.json"
    assert captured["policy_provenance"] is not None
    assert submit_response.is_error is False  # type: ignore[union-attr]
    assert submitted["policy_path"] == "task-policy.json"

    (tmp_path / "task-policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "hybrid_sdlc.submission.select_active_endpoint",
        lambda **kwargs: pytest.fail("invalid MCP policy must fail before probing"),
    )

    async def invoke_invalid_policy() -> object:
        async with Client(mcp) as client:
            return await client.call_tool(
                "run_spec_task_sync",
                {
                    "repo_root": str(tmp_path),
                    "spec_path": "docs/tasks/task.md",
                    "task_id": "POLICY-001",
                    "test_profile": "pytest",
                    "policy_path": "task-policy.json",
                },
            )

    invalid_response = asyncio.run(invoke_invalid_policy())
    assert invalid_response.is_error is True  # type: ignore[union-attr]
    assert "TASK_POLICY_INVALID" in invalid_response.content[0].text  # type: ignore[union-attr]


def test_runner_refreshes_candidate_after_late_commit_scope_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import aider_runner
    from hybrid_sdlc.worktrees import WorktreeRecord

    spec_path = _task_repo(tmp_path)
    config = load_config(repo_root=tmp_path)
    original_commit = aider_runner.create_scoped_commit
    records: list[WorktreeRecord] = []

    def add_unlisted_before_commit(
        record: WorktreeRecord, task_id: str, message: str, **kwargs: object
    ) -> str | None:
        records.append(record)
        (record.path / "late unlisted.txt").write_text(
            "late candidate evidence\n", encoding="utf-8"
        )
        return original_commit(record, task_id, message, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(aider_runner, "create_scoped_commit", add_unlisted_before_commit)
    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec_path,
        task_id="POLICY-001",
        profile=config.command_profiles["pytest"],
        endpoint_url="http://127.0.0.1:8089/v1",
        model_name="synthetic-local-model",
        aider_cmd=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('generated.py').write_text('value=1\\n')",
        ],
        policy_path="docs/tasks/task.policy.json",
        test_profile="pytest",
        commit_requested=True,
    )

    assert result.status is RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "TASK_SCOPE_VIOLATION"
    assert result.failure.details["violations"] == [
        {"path": "late unlisted.txt", "reason": "not_intended"}
    ]
    assert result.commit_hash is None
    assert len(records) == 1
    assert result.review_patch is not None
    patch = Path(result.review_patch).read_bytes()
    assert b"late candidate evidence" in patch
    assert result.final_diff is not None
    assert result.final_diff.changed_files == ["generated.py", "late unlisted.txt"]
    assert result.final_diff.diff_hash == hashlib.sha256(patch).hexdigest()
    for root in (tmp_path, records[0].path):
        head = (
            subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        assert head == records[0].baseline_commit
    staged = subprocess.run(
        ["git", "-C", str(records[0].path), "diff", "--cached", "--name-only"],
        capture_output=True,
        check=True,
    ).stdout
    assert staged == b""
