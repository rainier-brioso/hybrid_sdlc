"""Unit tests for Aider argv constructor, failure signature, and test profile runner."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hybrid_sdlc.aider_runner import (
    _isolated_test_executable,
    build_aider_argv,
    extract_failure_signature,
    run_test_profile,
)
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.errors import CommandPolicyError


def test_repo_root_executable_is_resolved_inside_isolated_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    source_executable = source / "runner.exe"
    isolated_executable = checkout / "runner.exe"
    source_executable.write_bytes(b"source executable")
    isolated_executable.write_bytes(b"isolated executable")
    source_executable.chmod(0o755)
    isolated_executable.chmod(0o755)
    profile = CommandProfile(name="repo-runner", argv=["runner.exe"])

    resolved = _isolated_test_executable(profile, source, checkout, source_executable)

    assert resolved == isolated_executable.resolve()


def test_repo_local_executable_missing_from_isolated_checkout_fails_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()
    checkout.mkdir()
    source_executable = source / ".venv" / "Scripts" / "pytest.exe"
    source_executable.parent.mkdir(parents=True)
    source_executable.write_bytes(b"source-only test runtime")
    profile = CommandProfile(name="pytest", argv=[".venv/Scripts/pytest.exe", "-q"])

    with pytest.raises(CommandPolicyError, match="Install the test runtime") as exc_info:
        _isolated_test_executable(profile, source, checkout, source_executable)

    assert exc_info.value.code == "COMMAND_POLICY_ISOLATED_EXECUTABLE_MISSING"


def test_build_aider_argv() -> None:
    spec_file = Path("spec.md")
    target_file = Path("app.py")

    argv = build_aider_argv(
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen2.5-Coder-32B",
        spec_file=spec_file,
        task_instruction="Implement task 1",
        target_files=[target_file],
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
    assert "--no-gitignore" in argv
    assert argv[argv.index("--input-history-file") + 1] == os.devnull
    assert argv[argv.index("--chat-history-file") + 1] == os.devnull
    assert "--no-suggest-shell-commands" in argv
    assert "--yes-always" in argv
    assert str(spec_file) in argv
    assert str(target_file) in argv


def test_automated_aider_skips_metadata_prompt_without_disabling_settings_checks() -> None:
    argv = build_aider_argv(
        endpoint_url="http://127.0.0.1:8080/v1",
        model_name="qwen3.8-flash-next-coder-iq1_m",
        spec_file=Path("spec.md"),
        task_instruction="Implement SMOKE-001",
    )

    assert "--yes-always" in argv
    assert "--no-show-model-warnings" in argv
    assert "--no-check-model-accepts-settings" not in argv


def test_build_aider_argv_only_sets_repo_map_when_explicit() -> None:
    common = {
        "endpoint_url": "http://127.0.0.1:8080/v1",
        "model_name": "local-model",
        "spec_file": Path("spec.md"),
        "task_instruction": "Implement task",
    }

    default_argv = build_aider_argv(**common)
    zero_argv = build_aider_argv(**common, repo_map_tokens=0)
    positive_argv = build_aider_argv(**common, repo_map_tokens=1024)

    assert "--map-tokens" not in default_argv
    assert zero_argv[zero_argv.index("--map-tokens") + 1] == "0"
    assert positive_argv[positive_argv.index("--map-tokens") + 1] == "1024"


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
