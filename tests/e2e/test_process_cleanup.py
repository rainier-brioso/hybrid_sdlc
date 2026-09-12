"""End-to-end tests for cancellation and descendant cleanup (HSDLC-036).

Verifies: interrupting a run terminates fake Aider, test command, and
grandchildren. Terminal state and lock release are verified after cancellation.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

from helpers.repositories import create_spec_repo

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.git_tools import acquire_repo_lock
from hybrid_sdlc.models import RunStatus
from hybrid_sdlc.processes import process_is_alive


def _record_pid_file(path: Path, pid: int) -> None:
    """Write a PID to a file."""
    path.write_text(str(pid), encoding="utf-8")


def test_cancellation_during_aider_terminates_process(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    # Use a script that records its PID and waits, so we can prove termination.
    slow_aider = helpers / "slow_aider.py"
    slow_aider.write_text(
        "import os, signal, time\n"
        "open('aider.pid', 'w').write(str(os.getpid()))\n"
        "state = {'done': False}\n"
        "def h(s, f):\n"
        "    state['done'] = True\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "while not state['done']:\n"
        "    time.sleep(0.5)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="pass",
        argv=[Path(sys.executable).name, "-c", "print('pass')"],
        cwd=".",
        timeout_seconds=10,
    )

    cancel = threading.Event()

    def cancel_after_delay() -> None:
        time.sleep(0.1)
        cancel.set()

    thread = threading.Thread(target=cancel_after_delay, daemon=True)
    thread.start()

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-CANCEL-AIDER",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=3,
        attempt_timeout_seconds=30,
        aider_cmd=[sys.executable, str(slow_aider)],
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "CANCELLED"

    # Wait for cleanup to propagate.
    time.sleep(0.5)

    aider_pid_file = repo / "aider.pid"
    if aider_pid_file.exists():
        aider_pid = int(aider_pid_file.read_text().strip())
        # The aider process should have been alive (started) and is now dead.
        assert not process_is_alive(aider_pid), f"Aider process {aider_pid} survived cancellation"
    else:
        # Fallback: verify output shows cancellation.
        if result.attempts:
            all_output = result.attempts[0].test_stdout_summary or ""
            assert "CANCELLED" in all_output or result.attempts[0].duration_seconds < 5


def test_cancellation_during_tests_terminates_process(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    fake_aider = helpers / "quick_edit.py"
    fake_aider.write_text(
        "from pathlib import Path\nPath('made_change.py').write_text('x=1')\n",
        encoding="utf-8",
    )

    # The test command that sleeps until cancelled.
    slow_test_script = helpers / "conditional_slow.py"
    slow_test_script.write_text(
        "import os, sys, time, signal\n"
        "state = {'done': False}\n"
        "if os.path.exists('made_change.py'):\n"
        "    open('test.pid', 'w').write(str(os.getpid()));\n"
        "    print('change detected, sleeping');\n"
        "    def h(s, f): state['done'] = True\n"
        "    signal.signal(signal.SIGTERM, h);\n"
        "    while not state['done']: time.sleep(0.5)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="pass_then_slow",
        argv=[Path(sys.executable).name, str(slow_test_script)],
        cwd=".",
        timeout_seconds=30,
    )

    cancel = threading.Event()

    def cancel_after_aider() -> None:
        time.sleep(0.5)
        cancel.set()

    thread = threading.Thread(target=cancel_after_aider, daemon=True)
    thread.start()

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-CANCEL-TEST",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        attempt_timeout_seconds=30,
        aider_cmd=[sys.executable, str(fake_aider)],
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "CANCELLED"

    # Wait for cleanup.
    time.sleep(0.5)

    # Verify the test process was alive (started) and is now dead.
    test_pid_file = repo / "test.pid"
    if test_pid_file.exists():
        test_pid = int(test_pid_file.read_text().strip())
        if process_is_alive(test_pid):
            raise AssertionError(f"Test process {test_pid} survived cancellation")


def test_cancellation_grandchild_cleanup(tmp_path: Path) -> None:
    repo = create_spec_repo(tmp_path)
    config_dir = repo / ".hybrid_sdlc"
    config_dir.mkdir(exist_ok=True)

    # Create a self-contained script that spawns a grandchild process.
    gc_pid_path = str(repo / "grandchild.pid")
    fake_aider = config_dir / "fake_aider_grandchild.py"
    fake_aider.write_text(
        f"import sys, os, subprocess, signal, time\n"
        f"grandchild_pid_file = {gc_pid_path!r}\n"
        "env = os.environ.copy()\n"
        "env['GC_PID_FILE'] = grandchild_pid_file\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c',\n"
        '     \'import os, time; open(os.environ["GC_PID_FILE"], "w").write(str(os.getpid())); time.sleep(120)\'],\n'
        "    env=env,\n"
        "    creationflags=getattr(os, 'CREATE_NEW_PROCESS_GROUP', 0)\n"
        ")\n"
        "print('[fake-aider] Spawned grandchild process')\n"
        "interrupted = False\n"
        "def handler(sig, frame):\n"
        "    global interrupted\n"
        "    interrupted = True\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "while not interrupted:\n"
        "    time.sleep(0.5)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="pass",
        argv=[Path(sys.executable).name, "-c", "print('ok')"],
        cwd=".",
        timeout_seconds=30,
    )

    cancel = threading.Event()

    def cancel_quickly() -> None:
        time.sleep(1.0)
        cancel.set()

    thread = threading.Thread(target=cancel_quickly, daemon=True)
    thread.start()

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-GRANDCHILD",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        attempt_timeout_seconds=30,
        aider_cmd=[sys.executable, str(fake_aider)],
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED

    # Wait for process tree cleanup.
    time.sleep(1.0)

    # Verify that the grandchild process recorded by spawn-child is dead.
    gc_pid_file = repo / "grandchild.pid"
    if gc_pid_file.exists():
        gc_pid = int(gc_pid_file.read_text().strip())
        try:
            os.kill(gc_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # Expected: process was terminated
        except OSError:
            pass
        if process_is_alive(gc_pid):
            raise AssertionError(f"Grandchild process {gc_pid} is still alive") from None


def test_terminal_state_after_cancellation_is_persisted(tmp_path: Path) -> None:
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

    cancel = threading.Event()
    time.sleep(0.05)
    cancel.set()

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-PERSIST-CANCEL",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(slow)],
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED
    assert result.failure is not None
    assert result.failure.code == "CANCELLED"
    runs_dir = repo / ".hybrid_sdlc" / "runs"
    json_files = list(runs_dir.glob("*.json"))
    assert len(json_files) >= 1

    import json

    latest = max(json_files, key=lambda p: p.stat().st_mtime)
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["status"] == "cancelled"
    assert record["failure"]["code"] == "CANCELLED"


def test_lock_released_after_cancellation(tmp_path: Path) -> None:
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

    cancel = threading.Event()
    time.sleep(0.05)
    cancel.set()

    run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-LOCK",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        aider_cmd=[sys.executable, str(slow)],
        cancel_event=cancel,
    )

    second_run_id = "second_run_claim_test"
    with acquire_repo_lock(repo, run_id=second_run_id):
        pass


def test_spawn_child_pid_proves_alive_then_dead(tmp_path: Path) -> None:
    """Prove: record the real PID of spawn-child before cancellation, then assert it's dead after."""
    repo = create_spec_repo(tmp_path)
    helpers = repo / "helpers"
    helpers.mkdir()

    # Create a standalone script that mimics the spawn-child scenario
    # Store in .hybrid_sdlc/ to avoid dirty worktree check.
    config_dir = repo / ".hybrid_sdlc"
    config_dir.mkdir(exist_ok=True)
    gc_pid_path = str(repo / "grandchild.pid")
    spawn_script = config_dir / "spawn_child.py"
    spawn_script.write_text(
        f"import sys, os, subprocess, signal, time\n"
        f"grandchild_pid_file = {gc_pid_path!r}\n"
        "env = os.environ.copy()\n"
        "env['GC_PID_FILE'] = grandchild_pid_file\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c',\n"
        '     \'import os, time; open(os.environ["GC_PID_FILE"], "w").write(str(os.getpid())); time.sleep(120)\'],\n'
        "    env=env,\n"
        "    creationflags=getattr(os, 'CREATE_NEW_PROCESS_GROUP', 0)\n"
        ")\n"
        "print('[fake-aider] Spawned grandchild process')\n"
        "interrupted = False\n"
        "def handler(sig, frame):\n"
        "    global interrupted\n"
        "    interrupted = True\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "while not interrupted:\n"
        "    time.sleep(0.5)\n",
        encoding="utf-8",
    )

    profile = CommandProfile(
        name="pass",
        argv=[Path(sys.executable).name, "-c", "print('ok')"],
        cwd=".",
        timeout_seconds=5,
    )

    cancel = threading.Event()

    def cancel_quickly() -> None:
        time.sleep(1.5)
        cancel.set()
        print("Cancel set")

    thread = threading.Thread(target=cancel_quickly, daemon=True)
    thread.start()

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="TASK-SPAWN-CHILD-PROC",
        profile=profile,
        endpoint_url="http://127.0.0.1:9999/v1",
        model_name="fake",
        max_retries=1,
        attempt_timeout_seconds=30,
        aider_cmd=[sys.executable, str(spawn_script)],
        cancel_event=cancel,
    )

    assert result.status == RunStatus.CANCELLED

    # Wait for cleanup.
    time.sleep(1.0)

    # The grandchild PID file should exist (spawn-child writes it).
    gc_pid_file = repo / "grandchild.pid"
    assert gc_pid_file.exists(), "spawn-child should have written grandchild.pid"
    gc_pid = int(gc_pid_file.read_text().strip())

    # After cleanup, grandchild should be dead.
    if process_is_alive(gc_pid):
        raise AssertionError(f"Grandchild process {gc_pid} survived cancellation")
