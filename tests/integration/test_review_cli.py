"""Exercise the public review record CLI against real disposable Git artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner
from helpers.reviews import make_candidate

from hybrid_sdlc.cli import cli


def test_review_record_cli_accepts_verified_patch_and_status(tmp_path: Path) -> None:
    _run, run_record, patch_bytes, baseline = make_candidate(
        tmp_path / "repo",
        test_code=(
            "from pathlib import Path; "
            "assert Path('candidate.txt').read_text() == 'reviewed change\\n'"
        ),
    )
    source = run_record.parents[2]
    original_source = (source / "candidate.txt").read_bytes()
    arguments = [
        "review",
        "record",
        str(run_record),
        "--repo-root",
        str(source),
        "--expected-baseline",
        baseline,
        "--expected-patch-sha256",
        hashlib.sha256(patch_bytes).hexdigest(),
        "--expected-revision",
        "0",
        "--reviewer",
        "fixture-reviewer",
        "--reviewer-kind",
        "human",
        "--decision",
        "accepted",
        "--test-profile",
        "review",
        "--json",
    ]

    recorded = CliRunner().invoke(cli, arguments)

    assert recorded.exit_code == 0, recorded.output
    payload = json.loads(recorded.output)
    assert payload["judgment"] == "accepted"
    assert payload["evidence"]["exit_code"] == 0
    status = CliRunner().invoke(
        cli, ["review", "status", str(run_record), "--repo-root", str(source), "--json"]
    )
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["current"] is True
    assert json.loads(status.output)["judgment"] == "accepted"
    assert (source / "candidate.txt").read_bytes() == original_source


def test_review_record_cli_reports_patch_mismatch_as_structured_error(tmp_path: Path) -> None:
    _run, run_record, _patch_bytes, baseline = make_candidate(
        tmp_path / "repo", test_code="raise SystemExit(0)"
    )
    source = run_record.parents[2]
    result = CliRunner().invoke(
        cli,
        [
            "review",
            "record",
            str(run_record),
            "--repo-root",
            str(source),
            "--expected-baseline",
            baseline,
            "--expected-patch-sha256",
            "0" * 64,
            "--expected-revision",
            "0",
            "--reviewer",
            "fixture-reviewer",
            "--reviewer-kind",
            "human",
            "--decision",
            "accepted",
            "--test-profile",
            "review",
            "--json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "REVIEW_INVALID"
    assert "digest does not match" in payload["error"]["message"]
