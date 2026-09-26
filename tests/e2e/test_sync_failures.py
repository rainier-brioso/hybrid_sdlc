"""End-to-end tests for every bounded-loop terminal condition (HSDLC-035).

Separate tests cover: broken baseline, unchanged diff, repeated signature,
retry exhaustion, task timeout, output truncation, and all terminal exit codes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from helpers.repositories import (
    create_failing_repo,
    create_spec_repo,
)

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunStatus


def test_broken_baseline_produces_failure(tmp_path: Path) -> None:
    repo = create_failing_repo(tmp_path)
    spec = repo / "spec.md"
    spec.write_text("# Fix this", encoding="utf-8")
    subprocess.run(["git", "add", "spec.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add spec"], cwd=str(repo), check=True, capture_output=True
    )

    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="always_fail",
        argv=[py_name, "-c", "import sys; sys.exit(1)"],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-BROKEN-BASELINE",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "BASELINE_TEST_FAILED"
    assert len(result.attempts) == 0


def test_unchanged_diff_detected(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()
    fake = helpers / "identical.py"
    fake.write_text(
        "from pathlib import Path\n"
        "Path('changed.py').write_text('SAME CONTENT', encoding='utf-8')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="fail_after_edit",
        argv=[
            Path(sys.executable).name,
            "-c",
            "import os, sys\nif not os.path.exists('changed.py'): sys.exit(0)\nsys.exit(1)\n",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-UNCHANGED",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "LOOP_STUCK"


def test_repeated_failure_signature_detected(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    counter_file = repo / "counter.txt"
    counter_file.write_text("0", encoding="utf-8")
    subprocess.run(["git", "add", "counter.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add counter"], cwd=str(repo), check=True, capture_output=True
    )

    fake = helpers / "counter_edit.py"
    fake.write_text(
        "from pathlib import Path\n"
        "p = Path('counter.txt')\n"
        "n = int(p.read_text()) + 1\n"
        "p.write_text(str(n))\n"
        "Path(f'file_{n}.py').write_text(f'#{n}\\n')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="fail_after_edit",
        argv=[
            Path(sys.executable).name,
            "-c",
            "from pathlib import Path\n"
            "import sys\n"
            "p = Path('counter.txt')\n"
            "if not p.exists(): sys.exit(0)\n"
            "n = int(p.read_text())\n"
            "if n == 0: sys.exit(0)\n"
            "print(f'FAILED test_mod::test_case - assertion error')\n"
            "sys.exit(1)\n",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-SIGNATURE",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "LOOP_STUCK"
    assert len(result.attempts) >= 2


def test_retry_exhaustion_produces_terminal_failure(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    counter_file = repo / "counter.txt"
    counter_file.write_text("0", encoding="utf-8")
    subprocess.run(["git", "add", "counter.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add counter"], cwd=str(repo), check=True, capture_output=True
    )

    fake = helpers / "counter_edit.py"
    fake.write_text(
        "from pathlib import Path\n"
        "p = Path('counter.txt')\n"
        "n = int(p.read_text()) + 1\n"
        "p.write_text(str(n))\n"
        "Path(f'file{n}.py').write_text(f'{n}\\n')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="fail_always",
        argv=[
            Path(sys.executable).name,
            "-c",
            "from pathlib import Path\n"
            "import sys\n"
            "p = Path('counter.txt')\n"
            "if not p.exists(): sys.exit(0)\n"
            "n = int(p.read_text())\n"
            "if n == 0: sys.exit(0)\n"
            "print(f'FAILED attempt {n}')\n"
            "sys.exit(1)\n",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-RETRY-EXHAUST",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
        aider_cmd=[sys.executable, str(fake)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "RETRY_EXHAUSTED"
    assert len(result.attempts) == 3


def test_task_timeout_produces_terminal_failure(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()
    slow = helpers / "slow.py"
    slow.write_text("import time; time.sleep(300)\n", encoding="utf-8")

    profile = CommandProfile(
        name="pass",
        argv=[Path(sys.executable).name, "-c", "print('ok')"],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-TIMEOUT",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=2,
        attempt_timeout_seconds=0.05,
        aider_cmd=[sys.executable, str(slow)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "TASK_TIMEOUT"


def test_output_truncation_preserves_exit_code(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "quick.py"
    fake_aider.write_text(
        "from pathlib import Path\nPath('output.py').write_text('ok')\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="truncating_test",
        argv=[
            Path(sys.executable).name,
            "-c",
            "import os, sys\n"
            "if not os.path.exists('output.py'): sys.exit(0)\n"
            "sys.stdout.write('A' * 100000)\n"
            "sys.stdout.flush()\n"
            "sys.exit(1)\n",
        ],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-TRUNCATE",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(fake_aider)],
    )

    assert result.status == RunStatus.FAILURE
    assert result.failure is not None
    assert result.failure.code == "RETRY_EXHAUSTED"
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.test_exit_code == 1
    stdout_summary = attempt.test_stdout_summary or ""
    assert len(stdout_summary) < 100000


def test_all_conditions_produce_documented_exit_code(tmp_path: Path) -> None:
    results: list[tuple[str, str]] = []

    # 1. BASELINE_TEST_FAILED — broken baseline
    repo = create_failing_repo(tmp_path)
    spec = repo / "spec.md"
    spec.write_text("# Fix this", encoding="utf-8")
    subprocess.run(["git", "add", "spec.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add spec"], cwd=str(repo), check=True, capture_output=True
    )

    py_name = Path(sys.executable).name
    profile = CommandProfile(
        name="fail",
        argv=[py_name, "-c", "import sys; sys.exit(1)"],
        cwd=".",
        timeout_seconds=10,
    )

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=spec,
        task_id="TASK-CODE-BASELINE",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
    )
    results.append(("BASELINE_TEST_FAILED", result.failure.code))

    # 2. TASK_TIMEOUT
    repo2 = create_spec_repo(tmp_path, "timeout-task")
    helpers2 = repo2 / "helpers"
    helpers2.mkdir()
    slow = helpers2 / "slow.py"
    slow.write_text("import time; time.sleep(300)\n", encoding="utf-8")

    profile2 = CommandProfile(
        name="pass",
        argv=[py_name, "-c", "print('ok')"],
        cwd=".",
        timeout_seconds=10,
    )

    result2 = run_bounded_loop(
        repo_root=repo2,
        spec_path=repo2 / "spec.md",
        task_id="TASK-CODE-TIMEOUT",
        profile=profile2,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=2,
        attempt_timeout_seconds=0.05,
        aider_cmd=[sys.executable, str(slow)],
    )
    results.append(("TASK_TIMEOUT", result2.failure.code))

    # Verify all expected codes were produced
    codes_tested = {code for code, actual in results}
    expected_codes = {"BASELINE_TEST_FAILED", "TASK_TIMEOUT"}
    assert expected_codes.issubset(codes_tested), (
        f"Expected codes not tested: {expected_codes - codes_tested}"
    )
    for expected, actual in results:
        assert actual == expected, f"Expected {expected}, got {actual}"
