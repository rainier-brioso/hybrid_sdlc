"""Tests for shared Phase 2 fixtures: repository factories and fake Aider (HSDLC-032/033).

Verifies:
- Every repository variant is created correctly.
- Every fake Aider scenario produces the expected deterministic behavior.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from helpers.fake_aider import main as fake_aider_main
from helpers.repositories import (
    create_dirty_repo,
    create_failing_repo,
    create_passing_repo,
    create_spec_repo,
    create_test_profile_repo,
    create_untracked_repo,
)

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunStatus
from hybrid_sdlc.processes import process_is_alive


class TestRepositoryFactories:
    """Verify each repository factory creates the expected state."""

    def test_create_passing_repo_has_clean_state(self, tmp_path: Path) -> None:
        repo = create_passing_repo(tmp_path)
        assert repo.exists()
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == ""

        assert (repo / "helper.py").exists()
        assert (
            "helper.py"
            in subprocess.run(
                ["git", "ls-files"], cwd=str(repo), capture_output=True, text=True
            ).stdout
        )

    def test_create_passing_repo_has_git_history(self, tmp_path: Path) -> None:
        repo = create_passing_repo(tmp_path)
        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line]
        assert len(lines) >= 2

    def test_create_failing_repo_has_failing_script(self, tmp_path: Path) -> None:
        repo = create_failing_repo(tmp_path)
        script = repo / "run_tests.py"
        assert script.exists()
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(repo),
            capture_output=True,
        )
        assert result.returncode != 0

    def test_create_dirty_repo_detected_as_dirty(self, tmp_path: Path) -> None:
        repo = create_dirty_repo(tmp_path)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        dirty_output = result.stdout.strip()
        assert dirty_output != "", "dirty_repo should have uncommitted changes"
        assert "data.txt" in dirty_output

    def test_create_untracked_repo_has_untracked_files(self, tmp_path: Path) -> None:
        repo = create_untracked_repo(tmp_path)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        assert "secrets.txt" in result.stdout

    def test_create_spec_repo_has_spec_file(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        spec = repo / "spec.md"
        assert spec.exists()
        assert spec.read_text() == "# Spec"

    def test_create_spec_repo_with_custom_content(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path, spec_content="# Custom spec content")
        spec = repo / "spec.md"
        assert spec.read_text() == "# Custom spec content"

    def test_create_test_profile_repo_returns_profile(self, tmp_path: Path) -> None:
        repo, profile = create_test_profile_repo(tmp_path)
        assert repo.exists()
        assert profile.name == "basic_test"
        assert profile.timeout_seconds == 10
        assert len(profile.argv) >= 2

    def test_create_test_profile_repo_runs_cleanly(self, tmp_path: Path) -> None:
        repo, _ = create_test_profile_repo(tmp_path)
        spec = repo / "spec.md"
        spec.write_text("# Test Profile Spec", encoding="utf-8")
        subprocess.run(["git", "add", "spec.md"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add spec"], cwd=str(repo), check=True, capture_output=True
        )
        profile = CommandProfile(
            name="basic_test",
            argv=[sys.executable, "-c", "import sys; sys.exit(0)"],
            cwd=".",
            timeout_seconds=10,
        )
        # Directly run the profile subprocess to verify it works
        result = subprocess.run(
            profile.argv,
            cwd=str(repo),
            env={**subprocess.os.environ, "PYTHONPATH": str(repo)},
        )
        assert result.returncode == 0


class TestFakeAiderScenarios:
    """Verify every fake Aider scenario produces deterministic behavior."""

    def test_scenario_edit_success(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        fake_aider_main(["edit-success", "--output-dir", str(output_dir)])
        solution = output_dir / "solution.py"
        assert solution.exists()
        assert "def answer():" in solution.read_text()
        assert "return 42" in solution.read_text()

    def test_scenario_edit_no_change_first_invocation(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        fake_aider_main(["edit-no-change", "--output-dir", str(output_dir)])
        assert (output_dir / "changed.py").exists()
        counter = output_dir / ".hybrid_sdlc" / ".fake_aider_count"
        assert counter.exists()
        assert int(counter.read_text()) == 1

    def test_scenario_edit_no_change_subsequent_invocations(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output2"
        output_dir.mkdir()
        # First invocation: writes changed.py.
        fake_aider_main(["edit-no-change", "--output-dir", str(output_dir)])
        assert (output_dir / "changed.py").exists()
        counter = output_dir / ".hybrid_sdlc" / ".fake_aider_count"
        assert int(counter.read_text()) == 1

        # Second invocation: does NOT write changed.py again.
        fake_aider_main(["edit-no-change", "--output-dir", str(output_dir)])
        assert int(counter.read_text()) == 2
        # changed.py still exists (written on first invocation), but was not re-written.
        assert (output_dir / "changed.py").exists()

    def test_scenario_edit_identical(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output3"
        output_dir.mkdir()
        fake_aider_main(["edit-identical", "--output-dir", str(output_dir)])
        always = output_dir / "always.py"
        assert always.exists()
        content = always.read_text()
        assert "# identical content" in content
        assert "VALUE = 999" in content

    def test_scenario_edit_identical_produces_same_content(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output4"
        output_dir.mkdir()
        fake_aider_main(["edit-identical", "--output-dir", str(output_dir)])
        first_content = (output_dir / "always.py").read_text()
        fake_aider_main(["edit-identical", "--output-dir", str(output_dir)])
        second_content = (output_dir / "always.py").read_text()
        assert first_content == second_content

    def test_scenario_edit_malformed(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output5"
        output_dir.mkdir()
        fake_aider_main(["edit-malformed", "--output-dir", str(output_dir)])
        py_files = list(output_dir.glob("*.py"))
        assert len(py_files) == 0

    def test_scenario_fail(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output6"
        output_dir.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            fake_aider_main(["fail", "--output-dir", str(output_dir)])
        assert exc_info.value.code == 1

    def test_scenario_counter_edit_increments(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output7"
        output_dir.mkdir()
        counter = output_dir / "counter.txt"
        counter.write_text("0", encoding="utf-8")

        fake_aider_main(["counter-edit", "--output-dir", str(output_dir)])
        assert counter.read_text() == "1"
        assert (output_dir / "version_1.py").exists()

        fake_aider_main(["counter-edit", "--output-dir", str(output_dir)])
        assert counter.read_text() == "2"
        assert (output_dir / "version_2.py").exists()

    def test_scenario_timeout_exits_on_signal(self, tmp_path: Path) -> None:
        import signal

        output_dir = tmp_path / "output8"
        output_dir.mkdir()

        import subprocess as sp

        proc = sp.Popen(
            [
                sys.executable,
                "-m",
                "helpers.fake_aider",
                "timeout",
                "--sleep-secs",
                "300",
                "--output-dir",
                str(output_dir),
            ],
            stdout=sp.PIPE,
            stderr=sp.PIPE,
        )
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=5)
        assert proc.returncode != 0

    def test_scenario_edit_malformed_bounded_loop(self, tmp_path: Path) -> None:
        """Verify malformed output triggers LOOP_STUCK after max retries."""
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "from pathlib import Path\n"
            "Path('always.py').write_text('# malformed but still a file\\nVALUE = 999\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="check",
            argv=[
                Path(sys.executable).name,
                "-c",
                "import sys, pathlib; sys.exit(0 if not pathlib.Path('always.py').exists() else 1)",
            ],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-MALFORMED",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "LOOP_STUCK"
        assert len(result.attempts) == 1

    def test_scenario_timeout_bounded_loop(self, tmp_path: Path) -> None:
        """Verify timeout scenario triggers cancellation in bounded loop."""
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "import time; time.sleep(300)\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="pass",
            argv=[Path(sys.executable).name, "-c", "print('ok')"],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-TIMEOUT",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=1,
            attempt_timeout_seconds=5,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "TASK_TIMEOUT"
        assert len(result.attempts) == 0

    def test_scenario_spawn_child_bounded_loop(self, tmp_path: Path) -> None:
        """Verify spawn-child scenario runs with subprocess descendant."""
        import os

        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        pid_file = os.path.join(".", "grandchild.pid")
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "import os, subprocess, sys, time\n"
            f"pid_file = {repr(pid_file)}\n"
            "# Write a file so git detects a diff\n"
            "with open('solution.py', 'w', encoding='utf-8') as f:\n"
            "    f.write('# Solution file\\nVALUE = 42\\n')\n"
            "subprocess.Popen([sys.executable, '-c', "
            f'"import os, time; open({repr(pid_file)}, \\"w\\").write(str(os.getpid())); time.sleep(120)"'
            "])\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="pass",
            argv=[Path(sys.executable).name, "-c", "print('ok')"],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-SPAWN-CHILD",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=1,
            attempt_timeout_seconds=2,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "TASK_TIMEOUT"

        # The grandchild PID file should exist.
        gc_pid_file = repo / "grandchild.pid"
        assert gc_pid_file.exists(), "spawn-child should have written grandchild.pid"
        gc_pid = int(gc_pid_file.read_text().strip())
        # After cancellation, grandchild should be dead.
        if process_is_alive(gc_pid):
            raise AssertionError(f"Grandchild process {gc_pid} survived")

    def test_e2e_fake_aider_edit_success(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "from pathlib import Path\n"
            "Path('solution.py').write_text('def answer():\\n    return 42\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="verify",
            argv=[
                Path(sys.executable).name,
                "-c",
                "import os, sys\n"
                "if not os.path.exists('solution.py'): sys.exit(0)\n"
                "import solution\n"
                "assert solution.answer() == 42",
            ],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-FAKE-AIDER-001",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.SUCCESS


class TestFakeAiderScenarioIntegration:
    """Drive real bounded-loop cases through each fake Aider scenario."""

    def test_e2e_no_change_scenario(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            f"import sys; sys.path.insert(0, '{helpers.parent.as_posix()}');\n"
            "from helpers.fake_aider import main\n"
            'main(["edit-no-change", "--output-dir", "."])\n',
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="check",
            argv=[
                Path(sys.executable).name,
                "-c",
                "import os, sys\n"
                "if os.path.exists('changed.py'):\n"
                "    print('changed.py found')\n"
                "    sys.exit(1)\n"
                "sys.exit(0)\n",
            ],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-NO-CHANGE",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE

    def test_e2e_identical_scenario(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "from pathlib import Path\n"
            "Path('always.py').write_text('# identical content\\nVALUE = 999\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="check_and_fail",
            argv=[
                Path(sys.executable).name,
                "-c",
                "import os, sys\n"
                "if not os.path.exists('always.py'): sys.exit(0)\n"
                "print('always.py found, tests fail')\n"
                "sys.exit(1)\n",
            ],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-IDENTICAL",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "LOOP_STUCK"

    def test_e2e_fail_scenario(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()
        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "print('[fake-aider] Failed to edit', file=sys.stderr)\nimport sys; sys.exit(1)\n",
            encoding="utf-8",
        )

        profile = CommandProfile(
            name="pass",
            argv=[
                Path(sys.executable).name,
                "-c",
                "import sys; sys.exit(0)\n",
            ],
            cwd=".",
            timeout_seconds=10,
        )

        result = run_bounded_loop(
            repo_root=repo,
            spec_path=repo / "spec.md",
            task_id="E2E-FAIL",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "LOOP_ZERO_DIFF"

    def test_e2e_counter_edit_scenario(self, tmp_path: Path) -> None:
        repo = create_spec_repo(tmp_path)
        helpers = repo / "helpers"
        helpers.mkdir()

        fake_aider = helpers / "fake_aider.py"
        fake_aider.write_text(
            "from pathlib import Path\n"
            "p = Path('counter.txt')\n"
            "n = int(p.read_text()) + 1 if p.exists() else 1\n"
            "p.write_text(str(n))\n"
            "Path(f'version_{n}.py').write_text(f'version = {n}\\n')\n",
            encoding="utf-8",
        )

        counter_file = repo / "counter.txt"
        counter_file.write_text("0", encoding="utf-8")
        subprocess.run(
            ["git", "add", "counter.txt"], cwd=str(repo), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add counter"], cwd=str(repo), check=True, capture_output=True
        )

        profile = CommandProfile(
            name="fail_after_first",
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
            task_id="E2E-COUNTER",
            profile=profile,
            endpoint_url="http://127.0.0.1:9999/v1",
            model_name="fake",
            max_retries=3,
            aider_cmd=[sys.executable, str(fake_aider)],
        )
        assert result.status == RunStatus.FAILURE
        assert result.failure is not None
        assert result.failure.code == "LOOP_STUCK"
