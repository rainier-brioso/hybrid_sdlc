"""Controller replay tests for exact-artifact review acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner
from helpers.reviews import make_candidate as _make_candidate

from hybrid_sdlc.artifacts import atomic_save_json
from hybrid_sdlc.cli import cli
from hybrid_sdlc.models import RunResult, RunStatus
from hybrid_sdlc.review import read_review_status, record_review


def test_acceptance_replays_exact_patch_and_status_revalidates_evidence(tmp_path: Path) -> None:
    test_code = (
        "from pathlib import Path; assert Path('candidate.txt').read_text() == 'reviewed change\\n'"
    )
    run, run_record, patch_bytes, baseline = _make_candidate(tmp_path / "repo", test_code=test_code)
    source_before = (tmp_path / "repo" / "candidate.txt").read_bytes()
    patch_digest = hashlib.sha256(patch_bytes).hexdigest()

    initial = read_review_status(run_record=run_record, repo_root=run_record.parents[2])
    assert not initial.current
    assert initial.baseline_commit == baseline
    assert initial.patch_sha256 == patch_digest
    assert not (run_record.parents[1] / "reviews").exists()

    accepted = record_review(
        run_record=run_record,
        repo_root=run_record.parents[2],
        expected_baseline=baseline,
        expected_patch_sha256=patch_digest,
        expected_revision=0,
        reviewer_kind="model",
        reviewer="automated-review-session",
        reviewer_model="fixture-worker-model",
        judgment="accepted",
        profile_name="review",
    )
    assert accepted.judgment == "accepted"
    assert accepted.model_relation == "same_model"
    assert accepted.evidence is not None
    assert accepted.evidence.unchanged
    assert accepted.evidence.exit_code == 0
    assert accepted.evidence.path.endswith("-r1.evidence.json")
    status = read_review_status(run_record=run_record, repo_root=run_record.parents[2])
    assert status.current and status.judgment == "accepted" and status.revision == 1
    assert (tmp_path / "repo" / "candidate.txt").read_bytes() == source_before


def test_tests_that_modify_candidate_record_blocked_without_acceptance(tmp_path: Path) -> None:
    test_code = (
        "from pathlib import Path; Path('candidate.txt').write_text('test changed candidate\\n')"
    )
    _run, run_record, patch_bytes, baseline = _make_candidate(
        tmp_path / "repo", test_code=test_code
    )
    source = run_record.parents[2]

    result = record_review(
        run_record=run_record,
        repo_root=source,
        expected_baseline=baseline,
        expected_patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        expected_revision=0,
        reviewer_kind="human",
        reviewer="Ray",
        judgment="accepted",
        profile_name="review",
    )
    assert result.judgment == "blocked"
    assert result.evidence is not None
    assert result.evidence.exit_code == 0
    assert not result.evidence.unchanged
    status = read_review_status(run_record=run_record, repo_root=source)
    assert status.current and status.judgment == "blocked"
    assert (source / "candidate.txt").read_text(encoding="utf-8") == "baseline\n"


def test_rejection_does_not_run_test_profile_or_require_successful_run(
    tmp_path: Path, monkeypatch
) -> None:
    test_code = "raise SystemExit('must not run')"
    run, run_record, patch_bytes, baseline = _make_candidate(tmp_path / "repo", test_code=test_code)
    run.status = RunStatus.FAILURE
    atomic_save_json(run_record, run, run_record.parents[2])
    monkeypatch.setattr(
        "hybrid_sdlc.review.run_test_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("review must not execute tests")
        ),
    )
    record = record_review(
        run_record=run_record,
        repo_root=run_record.parents[2],
        expected_baseline=baseline,
        expected_patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        expected_revision=0,
        reviewer_kind="human",
        reviewer="Ray",
        judgment="rejected",
    )
    assert record.judgment == "rejected"
    assert record.evidence is None


def test_missing_profile_persists_current_blocked_review(tmp_path: Path) -> None:
    _run, run_record, patch_bytes, baseline = _make_candidate(
        tmp_path / "repo", test_code="raise SystemExit(0)"
    )
    result = record_review(
        run_record=run_record,
        repo_root=run_record.parents[2],
        expected_baseline=baseline,
        expected_patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        expected_revision=0,
        reviewer_kind="human",
        reviewer="Ray",
        judgment="accepted",
        profile_name="missing-profile",
    )
    assert result.judgment == "blocked"
    assert result.evidence is not None
    assert result.evidence.verification_error == "review_policy"
    assert not result.evidence.verification_completed
    status = read_review_status(run_record=run_record, repo_root=run_record.parents[2])
    assert status.current and status.judgment == "blocked"


def test_cli_review_status_and_policy_error_exit_code(tmp_path: Path) -> None:
    _run, run_record, _patch, _baseline = _make_candidate(
        tmp_path / "repo", test_code="raise SystemExit(0)"
    )
    source = run_record.parents[2]
    result = CliRunner().invoke(
        cli, ["review", "status", str(run_record), "--repo-root", str(source), "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.output)["current"] is False

    missing = CliRunner().invoke(
        cli,
        ["review", "status", str(source / "missing.json"), "--repo-root", str(source)],
    )
    assert missing.exit_code == 3


def test_legacy_run_without_worker_model_remains_readable() -> None:
    payload = RunResult(
        run_id="run_legacy",
        task_id="LEGACY",
        spec_path="spec.md",
        repo_root=".",
        source_repo_root=".",
        status=RunStatus.FAILURE,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        total_duration_seconds=1.0,
    ).model_dump(mode="json")
    payload.pop("worker_model", None)
    assert RunResult.model_validate(payload).worker_model is None


def test_review_acceptance_supports_owned_attempt_artifact_root(tmp_path: Path) -> None:
    _run, source_run_record, patch_bytes, baseline = _make_candidate(
        tmp_path / "repo", test_code="raise SystemExit(0)"
    )
    source = source_run_record.parents[2]
    worktree_record = Path(
        json.loads(source_run_record.read_text())["artifacts"]["worktree_record"]
    )
    external_root = worktree_record.parent / ".hybrid_sdlc"
    external_run_record = external_root / "runs" / source_run_record.name
    external_run_record.parent.mkdir(parents=True)
    external_run_record.write_bytes(source_run_record.read_bytes())

    record = record_review(
        run_record=external_run_record,
        repo_root=source,
        expected_baseline=baseline,
        expected_patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        expected_revision=0,
        reviewer_kind="human",
        reviewer="Ray",
        judgment="accepted",
        profile_name="review",
    )
    assert record.judgment == "accepted"
    assert record.evidence is not None
    assert (external_root / record.evidence.path).is_file()
