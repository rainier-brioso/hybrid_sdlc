"""Real stdio MCP process-disconnect behavior (HSDLC-045)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from helpers.repositories import create_spec_repo
from mcp import Client, StdioServerParameters
from mcp.client import stdio as stdio_client_module

from hybrid_sdlc.errors import AsyncJobHostUnsupportedError
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.processes import get_process_identity
from hybrid_sdlc.submission import _verify_windows_worker_breakaway

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parameters(repo: Path, patch_dir: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "hybrid_sdlc", "mcp"],
        cwd=_PROJECT_ROOT,
        env={"PYTHONPATH": os.pathsep.join((str(patch_dir), str(_PROJECT_ROOT / "src")))},
    )


def _capture_adapter_processes(
    monkeypatch: pytest.MonkeyPatch, *, disable_sdk_job: bool = True
) -> list[Any]:
    captured: list[Any] = []
    original = stdio_client_module._create_platform_compatible_process

    if os.name == "nt" and disable_sdk_job:
        # The SDK's test transport puts the server in a KILL_ON_JOB_CLOSE job
        # and then kills all descendants when Client exits. This lifecycle test
        # must terminate the adapter PID alone, as real hosts may do, so leave
        # process-tree ownership with the adapter under test.
        from mcp.os.win32 import utilities as win32_utilities

        monkeypatch.setattr(win32_utilities, "_create_job_object", lambda: None)

    async def capture(*args: Any, **kwargs: Any) -> Any:
        process = await original(*args, **kwargs)
        captured.append(process)
        return process

    monkeypatch.setattr(stdio_client_module, "_create_platform_compatible_process", capture)
    return captured


def _wait_for(predicate: Any, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.025)
    return bool(predicate())


async def _wait_for_async(predicate: Any, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.025)
    return bool(predicate())


def _write_async_test_hooks(
    patch_dir: Path, worker_script: Path, marker: Path, release: Path, diagnostic: Path
) -> None:
    patch_dir.mkdir(parents=True)
    (patch_dir / "sitecustomize.py").write_text(
        "import sys\n"
        "from hybrid_sdlc import mcp_server\n"
        "from hybrid_sdlc import submission\n"
        "from hybrid_sdlc.submission import submit_job as _submit_job\n"
        f"root = {str(marker.parent)!r}\n"
        "def preflight(**kwargs):\n"
        "    return __import__('pathlib').Path(kwargs['repo_root']), 'spec.md', 'http://127.0.0.1:8090/v1', 'fake', 1\n"
        "mcp_server.preflight_task_request = preflight\n"
        "submission.preflight_task_request = preflight\n"
        f"worker = {str(worker_script)!r}\n"
        f"marker = {str(marker)!r}\n"
        f"release = {str(release)!r}\n"
        f"diagnostic = {str(diagnostic)!r}\n"
        "from hybrid_sdlc.job_manager import JobManager\n"
        "from hybrid_sdlc.processes import get_process_identity\n"
        "_status = JobManager.status\n"
        "def debug_status(manager, job_id, **kwargs):\n"
        "    record = manager.get(job_id)\n"
        "    observed = get_process_identity(record.worker_pid) if record.worker_pid else None\n"
        "    with open(diagnostic, 'a', encoding='utf-8') as output: output.write(repr((record.worker_pid, record.worker_process_identity, observed)) + '\\n')\n"
        "    return _status(manager, job_id, **kwargs)\n"
        "JobManager.status = debug_status\n"
        "def submit_job(**kwargs):\n"
        "    def command(job_id, repo_root):\n"
        "        return [sys.executable, worker, job_id, str(repo_root), marker, release]\n"
        "    return _submit_job(**kwargs, _worker_command_factory=command)\n"
        "mcp_server.submit_job = submit_job\n",
        encoding="utf-8",
    )


def test_submitted_worker_survives_stdio_adapter_disconnect_and_reconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        try:
            _verify_windows_worker_breakaway()
        except AsyncJobHostUnsupportedError as exc:
            pytest.skip(f"The test runner itself is in a restrictive Windows Job Object: {exc}")

    repo = create_spec_repo(tmp_path)
    (repo / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["python", "-c", "pass"]\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "hybrid_sdlc.toml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add profile"], cwd=repo, check=True)
    marker = tmp_path / "worker-claimed"
    release = tmp_path / "worker-release"
    diagnostic = tmp_path / "worker-probes.log"
    worker_log = tmp_path / "worker-log.json"
    worker_script = tmp_path / "detached_worker.py"
    worker_script.write_text(
        "import json, os, sys, time, traceback\n"
        "from pathlib import Path\n"
        "from hybrid_sdlc.job_manager import JobManager, JobStatus\n"
        "from hybrid_sdlc.processes import get_process_identity\n"
        "job_id, root, marker, release = sys.argv[1:]\n"
        "started = time.monotonic()\n"
        "log_path = Path(marker).with_name('worker-log.json')\n"
        "try:\n"
        "    identity = get_process_identity(__import__('os').getpid())\n"
        "    manager = JobManager(Path(root))\n"
        "    manager.claim(job_id, __import__('os').getpid(), identity.created_at, identity.start_token, identity.precision)\n"
        "    in_job = False\n"
        "    if os.name == 'nt':\n"
        "        import ctypes\n"
        "        member = ctypes.c_int()\n"
        "        kernel32 = ctypes.windll.kernel32\n"
        "        kernel32.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "        kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]\n"
        "        kernel32.IsProcessInJob.restype = ctypes.c_int\n"
        "        kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(member))\n"
        "        in_job = bool(member.value)\n"
        "    Path(marker).write_text(json.dumps({'pid': os.getpid(), 'in_job': in_job, 'started': time.time()}), encoding='utf-8')\n"
        "    deadline = time.monotonic() + 60\n"
        "    while not Path(release).exists() and time.monotonic() < deadline: time.sleep(0.025)\n"
        "    result = manager.transition(job_id, JobStatus.COMPLETED)\n"
        "    log_path.write_text(json.dumps({'completed': result.status.value, 'elapsed': time.monotonic() - started}), encoding='utf-8')\n"
        "except BaseException:\n"
        "    log_path.write_text(traceback.format_exc(), encoding='utf-8')\n"
        "    raise\n",
        encoding="utf-8",
    )
    patch_dir = tmp_path / "adapter-patches"
    _write_async_test_hooks(patch_dir, worker_script, marker, release, diagnostic)
    adapter_processes = _capture_adapter_processes(monkeypatch)
    params = _parameters(repo, patch_dir)
    job_id = ""
    worker_pid: int | None = None

    async def scenario() -> None:
        nonlocal job_id, worker_pid
        client = Client(params)
        await client.__aenter__()
        try:
            response = await client.call_tool(
                "submit_spec_job",
                {
                    "repo_root": str(repo),
                    "spec_path": "spec.md",
                    "task_id": "DISC-1",
                    "test_profile": "pytest",
                },
            )
            assert response.is_error is False
            job_id = response.structured_content["job_id"]
            assert await _wait_for_async(marker.exists)
            before = JobManager(repo).get(job_id)
            worker_pid = before.worker_pid
            assert before.status is JobStatus.RUNNING
            assert worker_pid is not None
            worker_details = json.loads(marker.read_text(encoding="utf-8"))
            assert worker_details["pid"] == worker_pid
            adapter_process = adapter_processes[-1]
            adapter_pid = adapter_process.pid
            os.kill(adapter_pid, signal.SIGTERM)
            assert await _wait_for_async(lambda: adapter_process.returncode is not None), (
                f"Adapter did not exit after SIGTERM (pid={adapter_pid}, "
                f"returncode={adapter_process.returncode})"
            )
            assert adapter_process.returncode is not None
            assert get_process_identity(worker_pid).state == "alive"
        finally:
            try:
                await client.__aexit__(None, None, None)
            except BaseExceptionGroup:
                pass  # The server process was intentionally terminated.
        assert worker_pid is not None and get_process_identity(worker_pid).state == "alive"

        async with Client(params) as reconnected:
            current = await reconnected.call_tool(
                "get_job_status", {"repo_root": str(repo), "job_id": job_id}
            )
            assert current.is_error is False
            assert current.structured_content["status"] == JobStatus.RUNNING.value, (
                f"status={current.structured_content.get('status')}, "
                f"failure={current.structured_content.get('failure_reason')}, "
                f"metadata={current.structured_content.get('failure_metadata')}, "
                f"worker_pid={current.structured_content.get('worker_pid')}, "
                f"diagnostics={diagnostic.read_text(encoding='utf-8') if diagnostic.exists() else None}, "
                f"marker={marker.read_text(encoding='utf-8') if marker.exists() else None}, "
                f"worker_log={worker_log.read_text(encoding='utf-8') if worker_log.exists() else None}"
            )

    try:
        asyncio.run(scenario())
        assert worker_pid is not None and get_process_identity(worker_pid).state == "alive"
        release.write_text("finish", encoding="utf-8")
        assert _wait_for(lambda: JobManager(repo).get(job_id).status is JobStatus.COMPLETED)
        final = JobManager(repo).get(job_id)
        assert final.status is JobStatus.COMPLETED
        assert final.finished_at is not None
    finally:
        release.write_text("cleanup", encoding="utf-8")
        if job_id:
            _wait_for(
                lambda: (
                    JobManager(repo).get(job_id).status in {JobStatus.COMPLETED, JobStatus.FAILED}
                )
            )
            record = JobManager(repo).get(job_id)
            if record.worker_pid and get_process_identity(record.worker_pid).state != "dead":
                try:
                    os.kill(record.worker_pid, signal.SIGTERM)
                except OSError:
                    pass
        for process in adapter_processes:
            if get_process_identity(process.pid).state != "dead":
                try:
                    os.kill(process.pid, signal.SIGTERM)
                except OSError:
                    pass


@pytest.mark.skipif(os.name != "nt", reason="requires the Windows SDK Job Object")
def test_default_windows_stdio_job_scope_rejects_async_submission_before_job_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The MCP SDK's default KILL_ON_JOB_CLOSE scope includes adapter descendants."""
    repo = create_spec_repo(tmp_path)
    (repo / "hybrid_sdlc.toml").write_text(
        '[command_profiles.pytest]\nargv = ["python", "-c", "pass"]\n', encoding="utf-8"
    )
    subprocess.run(["git", "add", "hybrid_sdlc.toml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add profile"], cwd=repo, check=True)
    marker = tmp_path / "worker-claimed.json"
    release = tmp_path / "worker-release"
    diagnostic = tmp_path / "worker-probes.log"
    worker_script = tmp_path / "detached_worker.py"
    worker_script.write_text(
        "import ctypes, json, os, sys, time\n"
        "from pathlib import Path\n"
        "from hybrid_sdlc.job_manager import JobManager\n"
        "from hybrid_sdlc.processes import get_process_identity\n"
        "job_id, root, marker, release = sys.argv[1:]\n"
        "pid = os.getpid()\n"
        "identity = get_process_identity(pid)\n"
        "manager = JobManager(Path(root))\n"
        "manager.claim(job_id, pid, identity.created_at, identity.start_token, identity.precision)\n"
        "member = ctypes.c_int()\n"
        "kernel32 = ctypes.windll.kernel32\n"
        "kernel32.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]\n"
        "kernel32.IsProcessInJob.restype = ctypes.c_int\n"
        "kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(member))\n"
        "Path(marker).write_text(json.dumps({'pid': pid, 'in_job': bool(member.value)}), encoding='utf-8')\n"
        "deadline = time.monotonic() + 60\n"
        "while not Path(release).exists() and time.monotonic() < deadline: time.sleep(0.025)\n",
        encoding="utf-8",
    )
    patch_dir = tmp_path / "adapter-patches"
    _write_async_test_hooks(patch_dir, worker_script, marker, release, diagnostic)
    adapter_processes = _capture_adapter_processes(monkeypatch, disable_sdk_job=False)
    params = _parameters(repo, patch_dir)

    async def scenario() -> None:
        client = Client(params)
        await client.__aenter__()
        try:
            response = await client.call_tool(
                "submit_spec_job",
                {
                    "repo_root": str(repo),
                    "spec_path": "spec.md",
                    "task_id": "DISC-DEFAULT",
                    "test_profile": "pytest",
                },
            )
            assert response.is_error is True
            message = " ".join(item.text for item in response.content if hasattr(item, "text"))
            assert "ASYNC_UNSUPPORTED_BY_HOST" in message
            assert "Job Object" in message
            assert not (repo / ".hybrid_sdlc" / "jobs").exists()
        finally:
            try:
                await client.__aexit__(None, None, None)
            except BaseExceptionGroup:
                pass
        assert not marker.exists()

    asyncio.run(scenario())
    release.write_text("cleanup", encoding="utf-8")
    assert not marker.exists()
    assert adapter_processes
    for process in adapter_processes:
        if get_process_identity(process.pid).state != "dead":
            try:
                os.kill(process.pid, signal.SIGTERM)
            except OSError:
                pass


@pytest.mark.parametrize("shutdown", ["signal", "stdin_eof"])
def test_sync_call_shutdown_kills_child_and_grandchild(
    shutdown: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = create_spec_repo(tmp_path)
    pid_file = tmp_path / "descendant-pids.json"
    child_script = tmp_path / "child_tree.py"
    child_script.write_text(
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(), grandchild.pid]), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    patch_dir = tmp_path / "adapter-patches"
    patch_dir.mkdir()
    (patch_dir / "sitecustomize.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from hybrid_sdlc import mcp_server\n"
        "from hybrid_sdlc import submission\n"
        "from hybrid_sdlc.config import ToolkitConfig\n"
        "from hybrid_sdlc.command_profiles import CommandProfile\n"
        "from hybrid_sdlc.processes import run_bounded_subprocess\n"
        f"root = {str(repo)!r}\n"
        f"child_script = {str(child_script)!r}\n"
        f"pid_file = {str(pid_file)!r}\n"
        "mcp_server.preflight_task_request = lambda **kwargs: (Path(root), 'spec.md', 'http://127.0.0.1:9/v1', 'fake', 1)\n"
        "submission.preflight_task_request = mcp_server.preflight_task_request\n"
        "mcp_server.load_config = lambda **kwargs: ToolkitConfig(command_profiles={'pytest': CommandProfile(name='pytest', argv=[sys.executable, '-c', 'pass'], cwd='.', timeout_seconds=30)})\n"
        "mcp_server.resolve_profile_executable = lambda *args: Path(sys.executable)\n"
        "def bounded_loop(**kwargs):\n"
        "    run_bounded_subprocess([sys.executable, child_script, pid_file], Path(root), os.environ.copy(), 60)\n"
        "    return SimpleNamespace(model_dump=lambda **options: {'status': 'stopped'})\n"
        "mcp_server.run_bounded_loop = bounded_loop\n",
        encoding="utf-8",
    )
    adapter_processes = _capture_adapter_processes(monkeypatch)
    params = _parameters(repo, patch_dir)

    async def scenario() -> None:
        client = Client(params)
        await client.__aenter__()
        try:
            request = asyncio.create_task(
                client.call_tool(
                    "run_spec_task_sync",
                    {
                        "repo_root": str(repo),
                        "spec_path": "spec.md",
                        "task_id": "SYNC-1",
                        "test_profile": "pytest",
                    },
                )
            )
            assert await _wait_for_async(lambda: pid_file.exists() or request.done())
            assert pid_file.exists(), (
                request.result().content[0].text if request.done() else "sync child did not start"
            )
            descendant_pids = json.loads(pid_file.read_text(encoding="utf-8"))
            adapter_process = adapter_processes[-1]
            adapter_pid = adapter_process.pid
            if shutdown == "stdin_eof":
                assert adapter_process.stdin is not None
                await adapter_process.stdin.aclose()
            else:
                os.kill(adapter_pid, signal.SIGTERM)
            assert await _wait_for_async(lambda: adapter_process.returncode is not None), (
                f"Adapter did not exit after {shutdown} (pid={adapter_pid}, "
                f"returncode={adapter_process.returncode})"
            )
            assert adapter_process.returncode is not None
            try:
                await asyncio.wait_for(request, timeout=5)
            except Exception:
                pass  # The adapter was intentionally terminated while the tool was active.
            assert await _wait_for_async(
                lambda: all(get_process_identity(pid).state == "dead" for pid in descendant_pids)
            ), f"Synchronous process tree survived shutdown: {descendant_pids}"
        finally:
            try:
                await client.__aexit__(None, None, None)
            except BaseExceptionGroup:
                pass  # The adapter was intentionally terminated while the tool was active.

    try:
        asyncio.run(scenario())
    finally:
        if pid_file.exists():
            for pid in json.loads(pid_file.read_text(encoding="utf-8")):
                if get_process_identity(pid).state != "dead":
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass
        for process in adapter_processes:
            if get_process_identity(process.pid).state != "dead":
                try:
                    os.kill(process.pid, signal.SIGTERM)
                except OSError:
                    pass
