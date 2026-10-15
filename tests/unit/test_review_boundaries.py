"""Independent schema checks for persisted exact-artifact review metadata."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from hybrid_sdlc.review import ReviewEvidence, ReviewRecord


def _evidence() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "path": "reviews/run_fixture-r1.evidence.json",
        "sha256": "e" * 64,
        "run_id": "run_fixture",
        "baseline_commit": "a" * 40,
        "patch_sha256": "b" * 64,
        "profile": "unit",
        "profile_sha256": "c" * 64,
        "verification_worktree_id": "review-fixture",
        "started_at": "2026-10-15T12:00:00+00:00",
        "finished_at": "2026-10-15T12:00:01+00:00",
        "duration_seconds": 1.0,
        "exit_code": 0,
        "timed_out": False,
        "cancelled": False,
        "truncated": False,
        "candidate_before_sha256": "d" * 64,
        "candidate_after_sha256": "d" * 64,
        "unchanged": True,
        "scope_checked": False,
        "verification_completed": True,
    }


def _record() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "run_id": "run_fixture",
        "run_record_sha256": "f" * 64,
        "baseline_commit": "a" * 40,
        "patch_sha256": "b" * 64,
        "revision": 1,
        "reviewer_kind": "human",
        "reviewer": "reviewer-one",
        "reviewer_model": None,
        "worker_model": None,
        "model_relation": "not_applicable",
        "judgment": "accepted",
        "recorded_at": "2026-10-15T12:00:01+00:00",
        "evidence": _evidence(),
    }


def test_valid_review_metadata_preserves_full_artifact_identities() -> None:
    record = ReviewRecord.model_validate(_record())
    assert record.evidence is not None
    assert record.patch_sha256 == record.evidence.patch_sha256
    assert record.worker_model is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0.0"),
        ("schema_version", 1),
        ("extra", "unrecognized"),
        ("revision", True),
        ("revision", "1"),
        ("revision", 1.0),
        ("revision", 0),
        ("run_record_sha256", "f" * 63),
        ("patch_sha256", "not-a-hash"),
        ("baseline_commit", "a" * 7),
        ("reviewer_kind", "agent"),
        ("judgment", "approved"),
        ("reviewer", ""),
        ("reviewer", "x" * 121),
        ("reviewer", "reviewer\nsecond-line"),
        ("reviewer", "token=private-value"),
        ("reviewer", "sk-" + "a" * 30),
        ("reviewer", "ghp_" + "a" * 36),
        ("reviewer", "AKIA" + "A" * 16),
        ("reviewer", "https://user:private-password@example.com"),
    ],
)
def test_review_schema_rejects_unsupported_or_unsafe_values(field: str, value: Any) -> None:
    payload = _record()
    payload[field] = value
    with pytest.raises(ValidationError):
        ReviewRecord.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "2.0.0"),
        ("extra", "unrecognized"),
        ("profile", "token=private-value"),
        ("profile", "profile\nsecond-line"),
        ("profile", "sk-" + "a" * 30),
        ("sha256", "f" * 63),
        ("patch_sha256", "not-a-hash"),
        ("profile_sha256", "c" * 63),
        ("candidate_before_sha256", "d" * 65),
        ("candidate_after_sha256", "z" * 64),
        ("baseline_commit", "a" * 7),
        ("duration_seconds", float("nan")),
        ("duration_seconds", float("inf")),
        ("duration_seconds", -1.0),
        ("duration_seconds", "1"),
        ("duration_seconds", True),
        ("exit_code", False),
        ("exit_code", "0"),
        ("timed_out", 0),
        ("unchanged", "true"),
    ],
)
def test_evidence_schema_rejects_nonfinite_coerced_or_malformed_values(
    field: str, value: Any
) -> None:
    payload = _evidence()
    payload[field] = value
    with pytest.raises(ValidationError):
        ReviewEvidence.model_validate(payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"reviewer_kind": "model", "reviewer_model": None, "model_relation": "unknown"},
        {"reviewer_kind": "human", "reviewer_model": "claimed-model"},
        {"model_relation": "same_model"},
        {
            "reviewer_kind": "model",
            "reviewer_model": "model-b",
            "worker_model": "model-a",
            "model_relation": "same_model",
        },
        {
            "reviewer_kind": "model",
            "reviewer_model": "model-a",
            "worker_model": None,
            "model_relation": "different_model_unverified",
        },
    ],
)
def test_review_provenance_cannot_contradict_its_reviewer_kind(updates: dict[str, Any]) -> None:
    payload = _record()
    payload.update(updates)
    with pytest.raises(ValidationError):
        ReviewRecord.model_validate(payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"run_id": "run_different"},
        {"baseline_commit": "9" * 40},
        {"patch_sha256": "9" * 64},
        {"exit_code": 1},
        {"timed_out": True},
        {"cancelled": True},
        {"truncated": True},
        {"unchanged": False},
        {"verification_completed": False},
        {"candidate_after_sha256": "9" * 64},
    ],
)
def test_accepted_record_requires_consistent_passing_candidate_evidence(
    updates: dict[str, Any],
) -> None:
    payload = _record()
    payload["evidence"].update(updates)
    with pytest.raises(ValidationError):
        ReviewRecord.model_validate(payload)


def test_accepted_record_requires_an_independent_receipt() -> None:
    payload = _record()
    payload["evidence"] = None
    with pytest.raises(ValidationError):
        ReviewRecord.model_validate(payload)


def test_rejected_judgment_can_exist_without_a_test_receipt() -> None:
    payload = _record()
    payload.update(judgment="rejected", evidence=None)
    assert ReviewRecord.model_validate(payload).judgment == "rejected"
