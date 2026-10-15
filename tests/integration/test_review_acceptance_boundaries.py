"""Independent stale-artifact, reference and concurrency review regressions."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock
from helpers.reviews import git, make_candidate

from hybrid_sdlc.artifacts import clean_artifacts
from hybrid_sdlc.review import ReviewError, read_review_status, record_review


@pytest.fixture
def accepted(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    source = tmp_path / "source"
    run, run_path, patch, baseline = make_candidate(source, test_code="pass")
    options = {
        "run_record": run_path,
        "repo_root": source,
        "expected_baseline": baseline,
        "expected_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "expected_revision": 0,
        "reviewer_kind": "human",
        "reviewer": "reviewer-one",
        "judgment": "accepted",
        "profile_name": "review",
    }
    result = record_review(**options)
    assert result.judgment == "accepted"
    assert read_review_status(run_record=run_path, repo_root=source).current
    assert run.review_patch is not None
    return source, run_path, options


@pytest.mark.parametrize("changed", ["patch", "run", "baseline", "profile", "receipt"])
def test_acceptance_is_invalid_after_any_bound_input_changes(
    accepted: tuple[Path, Path, dict[str, Any]], changed: str
) -> None:
    source, run_path, _options = accepted
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if changed == "patch":
        with Path(run["review_patch"]).open("ab") as stream:
            stream.write(b"\n")
    elif changed == "run":
        run["task_id"] = "CHANGED-001"
        run_path.write_text(json.dumps(run), encoding="utf-8")
    elif changed == "baseline":
        git(source, "commit", "--allow-empty", "-m", "advance source baseline")
    elif changed == "profile":
        config = source / "hybrid_sdlc.toml"
        config.write_text(
            config.read_text(encoding="utf-8") + "timeout_seconds = 29\n", encoding="utf-8"
        )
    else:
        receipt = source / ".hybrid_sdlc/reviews/run_review_fixture-r1.evidence.json"
        receipt.unlink()
    status = read_review_status(run_record=run_path, repo_root=source)
    assert not status.current
    assert status.judgment != "accepted"
    assert status.reason


def test_external_receipt_copy_cannot_replace_deleted_private_evidence(
    accepted: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    source, run_path, _options = accepted
    review_path = source / ".hybrid_sdlc/reviews/run_review_fixture.json"
    private_receipt = source / ".hybrid_sdlc/reviews/run_review_fixture-r1.evidence.json"
    external = tmp_path / "external-receipt.json"
    external.write_bytes(private_receipt.read_bytes())
    private_receipt.unlink()
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["evidence"]["path"] = str(external)
    review_path.write_text(json.dumps(review), encoding="utf-8")
    status = read_review_status(run_record=run_path, repo_root=source)
    assert not status.current
    assert status.judgment != "accepted"


def test_embedded_receipt_cannot_disagree_with_referenced_receipt(
    accepted: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, run_path, _options = accepted
    review_path = source / ".hybrid_sdlc/reviews/run_review_fixture.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["evidence"]["duration_seconds"] += 1
    review_path.write_text(json.dumps(review), encoding="utf-8")
    status = read_review_status(run_record=run_path, repo_root=source)
    assert not status.current
    assert status.judgment != "accepted"


def test_stale_revision_cannot_overwrite_a_recorded_decision(
    accepted: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, run_path, options = accepted
    with pytest.raises(ReviewError, match="revision"):
        record_review(**{**options, "judgment": "rejected", "profile_name": None})
    status = read_review_status(run_record=run_path, repo_root=source)
    assert status.current and status.judgment == "accepted" and status.revision == 1


def test_concurrent_decisions_from_same_revision_have_one_winner(
    accepted: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, run_path, options = accepted

    def decide() -> str:
        try:
            result = record_review(
                **{**options, "expected_revision": 1, "judgment": "rejected", "profile_name": None}
            )
            return result.judgment
        except ReviewError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: decide(), range(2)))
    assert sorted(outcomes) == ["conflict", "rejected"]
    status = read_review_status(run_record=run_path, repo_root=source)
    assert status.current and status.judgment == "rejected" and status.revision == 2


def test_status_does_not_regenerate_or_rewrite_evidence(
    accepted: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, run_path, _options = accepted
    root = source / ".hybrid_sdlc"
    before = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    for _ in range(2):
        assert read_review_status(run_record=run_path, repo_root=source).current
    after = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_retention_skips_active_metadata_but_cleaned_receipt_invalidates_acceptance(
    accepted: tuple[Path, Path, dict[str, Any]],
) -> None:
    source, run_path, _options = accepted
    review_path = source / ".hybrid_sdlc/reviews/run_review_fixture.json"
    receipt = source / ".hybrid_sdlc/reviews/run_review_fixture-r1.evidence.json"
    with FileLock(str(run_path) + ".lock"), FileLock(str(review_path) + ".lock"):
        clean_artifacts(source, older_than_seconds=0)
        assert run_path.is_file() and review_path.is_file()
        assert not receipt.exists()
        assert not read_review_status(run_record=run_path, repo_root=source).current
        assert not receipt.exists()


@pytest.mark.parametrize("allowed", [True, False])
def test_acceptance_verification_rechecks_pinned_task_scope(tmp_path: Path, allowed: bool) -> None:
    source = tmp_path / "source"
    _run, run_path, patch, baseline = make_candidate(
        source,
        test_code="pass",
        policy_paths=["candidate.txt"] if allowed else ["other.txt"],
    )
    result = record_review(
        run_record=run_path,
        repo_root=source,
        expected_baseline=baseline,
        expected_patch_sha256=hashlib.sha256(patch).hexdigest(),
        expected_revision=0,
        reviewer_kind="human",
        reviewer="reviewer-one",
        judgment="accepted",
        profile_name="review",
    )
    assert result.judgment == ("accepted" if allowed else "blocked")
    assert result.evidence is not None
    assert result.evidence.scope_checked is allowed
    if not allowed:
        assert not result.evidence.verification_completed
