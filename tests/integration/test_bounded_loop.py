"""Integration tests for the bounded editing and testing state machine."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunStatus


def _setup_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    # Commit gitignore to ignore helper scripts
    gitignore = path / ".gitignore"
    gitignore.write_text(".hybrid_sdlc/\nhelpers/\n", encoding="utf-8")
    init_file = path / "README.md"
    init_file.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=str(path), check=True, capture_output=True
    )


def _commit_file(path: Path, filename: str) -> None:
    subprocess.run(["git", "add", filename], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"add {filename}"], cwd=str(path), check=True, capture_output=True
    )


def test_bounded_loop_baseline_failure_aborts(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")

    # Command profile that immediately fails
    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="failing_profile",
        argv=[py_name, "-c", "import sys; sys.exit(1)"],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-001",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        max_retries=3,
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "BASELINE_TEST_FAILED"
    assert len(result.attempts) == 0


def test_bounded_loop_success(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")

    # A fake aider script placed under ignored helpers/
    helpers_dir = tmp_path / "helpers"
    helpers_dir.mkdir()
    fake_aider = helpers_dir / "fake_aider.py"
    fake_aider.write_text(
        "import sys\n"
        "with open('solution.py', 'w') as f:\n"
        "    f.write('def answer(): return 42\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    # Test profile checks solution.py exists and returns 42
    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="test_solution",
        argv=[
            py_name,
            "-c",
            "import os, sys\n"
            "if not os.path.exists('solution.py'): sys.exit(0)\n"
            "import solution; assert solution.answer() == 42; print('ALL PASS')",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-002",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.SUCCESS
    assert result.baseline_commit is not None
    assert result.initial_status == "clean"
    assert len(result.attempts) == 1
    assert result.attempts[0].test_passed
    assert result.final_diff is not None
    assert not result.final_diff.is_empty
    assert "solution.py" in result.final_diff.changed_files
    assert result.final_diff.patch_file is not None
    assert not Path(result.final_diff.patch_file).is_absolute()


def test_bounded_loop_stuck_identical_diff(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")

    # A fake aider script that makes the same edit every time, but tests fail
    helpers_dir = tmp_path / "helpers"
    helpers_dir.mkdir()
    fake_aider = helpers_dir / "fake_aider.py"
    fake_aider.write_text(
        "import sys\nwith open('bad.py', 'w') as f:\n    f.write('x = 1\\n')\nsys.exit(0)\n",
        encoding="utf-8",
    )

    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="test_fail",
        argv=[
            py_name,
            "-c",
            "import sys, os\n"
            "if not os.path.exists('bad.py'): sys.exit(0)\n"  # Baseline passes
            "sys.exit(1)\n",  # Once bad.py exists, test fails
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-003",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "LOOP_STUCK"


def test_bounded_loop_zero_diff(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")
    helper = tmp_path / "helpers" / "no_edit.py"
    helper.parent.mkdir()
    helper.write_text("pass\n", encoding="utf-8")
    profile = CommandProfile(
        name="passing",
        argv=[Path(sys.executable).name, "-c", "print('pass')"],
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-ZERO",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        aider_cmd=[sys.executable, str(helper)],
    )

    assert result.failure is not None
    assert result.failure.code == "LOOP_ZERO_DIFF"
    assert result.final_diff is not None
    assert result.final_diff.is_empty


def test_bounded_loop_aider_timeout(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")
    helper = tmp_path / "helpers" / "slow.py"
    helper.parent.mkdir()
    helper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    profile = CommandProfile(
        name="passing",
        argv=[Path(sys.executable).name, "-c", "print('pass')"],
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-TIMEOUT",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        attempt_timeout_seconds=0.05,
        aider_cmd=[sys.executable, str(helper)],
    )

    assert result.failure is not None
    assert result.failure.code == "TASK_TIMEOUT"


def test_bounded_loop_baseline_cancellation(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")
    cancel = threading.Event()
    cancel.set()
    profile = CommandProfile(
        name="slow",
        argv=[Path(sys.executable).name, "-c", "import time; time.sleep(30)"],
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-CANCEL",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "CANCELLED"


def test_bounded_loop_retry_exhaustion(tmp_path: Path) -> None:
    _setup_git_repo(tmp_path)
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec", encoding="utf-8")
    _commit_file(tmp_path, "spec.md")
    helper = tmp_path / "helpers" / "increment.py"
    helper.parent.mkdir()
    helper.write_text(
        "from pathlib import Path\n"
        "p = Path('counter.txt')\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "p.write_text(str(n))\n",
        encoding="utf-8",
    )
    test_code = (
        "from pathlib import Path; import sys; p=Path('counter.txt'); "
        "print(f'FAILED test_mod.py::test_case - failure {p.read_text()}' if p.exists() else 'ok'); "
        "sys.exit(1 if p.exists() else 0)"
    )
    profile = CommandProfile(
        name="changing-failure",
        argv=[Path(sys.executable).name, "-c", test_code],
    )

    result = run_bounded_loop(
        repo_root=tmp_path,
        spec_path=spec,
        task_id="TASK-RETRY",
        profile=profile,
        endpoint_url="http://127.0.0.1:8090/v1",
        model_name="Qwen",
        max_retries=3,
        aider_cmd=[sys.executable, str(helper)],
    )

    assert len(result.attempts) == 3
    assert result.failure is not None
    assert result.failure.code == "RETRY_EXHAUSTED"
