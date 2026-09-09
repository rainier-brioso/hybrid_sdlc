"""Unit tests for Aider argv constructor, failure signature, and test profile runner."""

from __future__ import annotations

import sys
from pathlib import Path

from hybrid_sdlc.aider_runner import (
    build_aider_argv,
    extract_failure_signature,
    run_test_profile,
)
from hybrid_sdlc.command_profiles import CommandProfile


def test_build_aider_argv(tmp_path: Path) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Spec", encoding="utf-8")

    argv = build_aider_argv(
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen2.5-Coder-32B",
        spec_file=spec_file,
        task_instruction="Implement task 1",
        target_files=[tmp_path / "app.py"],
    )

    assert "aider" in argv[0]
    assert "--model" in argv
    assert "openai/Qwen2.5-Coder-32B" in argv
    assert "--openai-api-base" in argv
    assert "http://127.0.0.1:8090/v1" in argv
    assert "--openai-api-key" in argv
    assert "local-no-key" in argv
    assert "--edit-format" in argv
    assert "diff" in argv
    assert "--no-auto-commits" in argv
    assert "--no-suggest-shell-commands" in argv
    assert "--yes-always" in argv
    assert str(spec_file) in argv
    assert str(tmp_path / "app.py") in argv


def test_extract_failure_signature_normalization() -> None:
    out1 = (
        "FAILED tests/test_calc.py::test_add - AssertionError: at 0x7f884a 2026-09-09 18:00:01 in 0.15s\n"
        "assert 1 == 2"
    )
    out2 = (
        "FAILED tests/test_calc.py::test_add - AssertionError: at 0x99ff22 2026-09-09 18:05:22 in 0.32s\n"
        "assert 1 == 2"
    )
    sig1 = extract_failure_signature(out1, "")
    sig2 = extract_failure_signature(out2, "")

    # Normalization should make volatile timestamps, memory addresses, and durations identical
    assert sig1 == sig2

    # Different failure test name should produce different signature
    out3 = "FAILED tests/test_calc.py::test_multiply - ZeroDivisionError"
    sig3 = extract_failure_signature(out3, "")
    assert sig1 != sig3


def test_run_test_profile_execution(tmp_path: Path) -> None:
    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="test_py",
        argv=[py_name, "-c", "print('PASS_OUTPUT'); import sys; sys.exit(0)"],
        cwd=".",
        timeout_seconds=10,
    )
    res = run_test_profile(profile, repo_root=tmp_path)
    assert res.exit_code == 0
    assert "PASS_OUTPUT" in res.stdout
