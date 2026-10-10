"""Boundary tests for the opt-in Aider cache adapter and exact patch export."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import venv
from pathlib import Path

import pytest

from hybrid_sdlc.aider_runner import run_bounded_loop
from hybrid_sdlc.command_profiles import CommandProfile
from hybrid_sdlc.models import RunStatus


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _make_repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text(".hybrid_sdlc/\n", encoding="utf-8")
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    (root / "spec.md").write_text("Use answer = 2\n", encoding="utf-8")
    cache = root / ".aider.tags.cache.v4"
    cache.mkdir()
    (cache / "existing-user-cache.txt").write_text("preserve this\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "README.md", "spec.md", ".aider.tags.cache.v4")
    _git(root, "commit", "-m", "initial")
    return root


def _fake_aider_python(
    root: Path,
    *,
    edit_script: str,
    version: str = "0.86.2",
) -> Path:
    environment = root / "aider-python"
    venv.EnvBuilder(with_pip=False).create(environment)
    if os.name == "nt":
        interpreter = environment / "Scripts" / "python.exe"
        site_packages = environment / "Lib" / "site-packages"
    else:
        interpreter = environment / "bin" / "python"
        site_packages = (
            environment
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
    aider = site_packages / "aider"
    aider.mkdir(parents=True)
    (aider / "__init__.py").write_text("", encoding="utf-8")
    (aider / "repomap.py").write_text(
        "class RepoMap:\n    TAGS_CACHE_DIR = '.aider.tags.cache.v4'\n", encoding="utf-8"
    )
    (aider / "main.py").write_text(
        "import sys\nfrom pathlib import Path\nfrom .repomap import RepoMap\n"
        "def main(argv=None):\n"
        "    assert '--map-tokens' in argv\n"
        "    assert argv[argv.index('--map-tokens') + 1] == '1024'\n"
        "    cache = Path(RepoMap.TAGS_CACHE_DIR)\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    marker = cache / 'cache.db'\n"
        "    marker.write_text(marker.read_text() + 'x' if marker.exists() else 'x')\n"
        + edit_script,
        encoding="utf-8",
    )
    dist_info = site_packages / f"aider_chat-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: aider-chat\nVersion: {version}\n", encoding="utf-8"
    )
    return interpreter


def _test_profile() -> CommandProfile:
    return CommandProfile(
        name="smoke",
        argv=[
            Path(sys.executable).name,
            "-c",
            "from pathlib import Path; p=Path('solution.py'); "
            "raise SystemExit(0 if not p.exists() or p.read_text() == 'answer = 2\\n' else 1)",
        ],
        timeout_seconds=10,
    )


def _use_local_worktree_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hybrid_sdlc import worktrees

    temporary = tmp_path / "worktree-temp"
    temporary.mkdir()
    monkeypatch.setattr(worktrees.tempfile, "gettempdir", lambda: str(temporary))


def test_adapter_keeps_cache_external_across_retries_and_exports_unexpected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path / "repo")
    python = _fake_aider_python(
        tmp_path,
        edit_script=(
            "    Path('.aider.tags.cache.v4/unexpected.txt').write_text('visible')\n"
            "    marker = cache / 'cache.db'\n"
            "    if marker.read_text() == 'x':\n"
            "        Path('solution.py').write_text('answer = 1\\n')\n"
            "    else:\n"
            "        Path('solution.py').write_text('answer = 2\\n')\n"
            "    return 0\n"
        ),
    )
    _use_local_worktree_temp(tmp_path, monkeypatch)

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="CACHE-RETRY",
        profile=_test_profile(),
        endpoint_url="http://127.0.0.1:8080/v1",
        model_name="fake-model",
        max_retries=2,
        aider_python=python,
        repo_map_tokens=1024,
    )

    cache_path = Path(result.artifacts["aider_repo_map_cache"])
    assert result.status is RunStatus.SUCCESS, result.failure
    assert len(result.attempts) == 2
    assert (cache_path / "cache.db").read_text(encoding="utf-8") == "xx"
    assert cache_path.is_relative_to(Path(result.worktree_path or "").parent)
    assert not cache_path.is_relative_to(Path(result.worktree_path or ""))
    assert (repo / ".aider.tags.cache.v4" / "existing-user-cache.txt").read_text(
        encoding="utf-8"
    ) == "preserve this\n"
    patch = Path(result.review_patch or "").read_text(encoding="utf-8")
    assert ".aider.tags.cache.v4/unexpected.txt" in patch
    assert "cache.db" not in patch
    assert str(cache_path) not in patch
    assert ".aider.tags.cache.v4/unexpected.txt" in (
        result.final_diff.changed_files if result.final_diff else []
    )


def test_adapter_cache_is_retained_after_timeout_and_final_patch_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path / "repo")
    python = _fake_aider_python(
        tmp_path,
        edit_script=(
            "    Path('partial.py').write_text('partial\\n')\n    time.sleep(5)\n    return 0\n"
        ),
    )
    # Make the local fake Aider delay only after the external cache is written.
    main_file = python.parent.parent / (
        "Lib/site-packages/aider/main.py"
        if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/aider/main.py"
    )
    content = main_file.read_text(encoding="utf-8").replace("import sys\n", "import sys, time\n")
    main_file.write_text(content, encoding="utf-8")
    _use_local_worktree_temp(tmp_path, monkeypatch)

    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="CACHE-TIMEOUT",
        profile=_test_profile(),
        endpoint_url="http://127.0.0.1:8080/v1",
        model_name="fake-model",
        attempt_timeout_seconds=2,
        aider_python=python,
        repo_map_tokens=1024,
    )

    cache_path = Path(result.artifacts["aider_repo_map_cache"])
    assert result.status is RunStatus.FAILURE
    assert result.failure is not None and result.failure.code == "TASK_TIMEOUT"
    assert (cache_path / "cache.db").read_text(encoding="utf-8") == "x"
    assert Path(result.review_patch or "").is_file()
    assert "partial.py" in Path(result.review_patch or "").read_text(encoding="utf-8")
    assert (repo / ".aider.tags.cache.v4" / "existing-user-cache.txt").read_text(
        encoding="utf-8"
    ) == "preserve this\n"


def test_adapter_cache_is_retained_after_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path / "repo")
    ready = tmp_path / "aider-ready"
    python = _fake_aider_python(
        tmp_path,
        edit_script=(
            f"    Path({str(ready)!r}).touch()\n"
            "    Path('partial.py').write_text('partial\\n')\n"
            "    time.sleep(5)\n"
            "    return 0\n"
        ),
    )
    main_file = python.parent.parent / (
        "Lib/site-packages/aider/main.py"
        if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/aider/main.py"
    )
    main_file.write_text(
        main_file.read_text(encoding="utf-8").replace("import sys\n", "import sys, time\n"),
        encoding="utf-8",
    )
    _use_local_worktree_temp(tmp_path, monkeypatch)
    cancel = threading.Event()

    def cancel_after_cache_created() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.01)
        if ready.exists():
            cancel.set()

    watcher = threading.Thread(target=cancel_after_cache_created, daemon=True)
    watcher.start()
    result = run_bounded_loop(
        repo_root=repo,
        spec_path=repo / "spec.md",
        task_id="CACHE-CANCEL",
        profile=_test_profile(),
        endpoint_url="http://127.0.0.1:8080/v1",
        model_name="fake-model",
        attempt_timeout_seconds=5,
        cancel_event=cancel,
        aider_python=python,
        repo_map_tokens=1024,
    )
    watcher.join(timeout=1)

    cache_path = Path(result.artifacts["aider_repo_map_cache"])
    assert result.status is RunStatus.CANCELLED
    assert (cache_path / "cache.db").read_text(encoding="utf-8") == "x"
    assert Path(result.review_patch or "").is_file()
    assert "partial.py" in Path(result.review_patch or "").read_text(encoding="utf-8")
