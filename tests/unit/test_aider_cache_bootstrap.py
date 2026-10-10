"""Offline contract tests for the standalone Aider cache bootstrap."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from hybrid_sdlc import _aider_cache_bootstrap as bootstrap
from hybrid_sdlc.aider_runner import _parse_aider_adapter_error, _validate_aider_python
from hybrid_sdlc.errors import AiderAdapterError

BOOTSTRAP = Path(__file__).parents[2] / "src" / "hybrid_sdlc" / "_aider_cache_bootstrap.py"


def _fake_aider_installation(
    root: Path, version: str = "0.86.2", cache_name: str = ".aider.tags.cache.v4"
) -> Path:
    site_packages = root / "site-packages"
    aider = site_packages / "aider"
    aider.mkdir(parents=True)
    (aider / "__init__.py").write_text("", encoding="utf-8")
    (aider / "repomap.py").write_text(
        f"class RepoMap:\n    TAGS_CACHE_DIR = {cache_name!r}\n", encoding="utf-8"
    )
    (aider / "main.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from .repomap import RepoMap\n"
        "def main(argv=None):\n"
        "    cache = Path(RepoMap.TAGS_CACHE_DIR)\n"
        "    cache.mkdir(parents=True, exist_ok=True)\n"
        "    marker = cache / 'cache.db'\n"
        "    marker.write_text(marker.read_text() + 'x' if marker.exists() else 'x')\n"
        "    Path(os.environ['FAKE_AIDER_RECORD']).write_text(json.dumps({"
        "'cache': str(cache), 'argv': argv}))\n"
        "    return 0\n",
        encoding="utf-8",
    )
    dist_info = site_packages / f"aider_chat-{version}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: aider-chat\nVersion: {version}\n", encoding="utf-8"
    )
    return site_packages


def _run_bootstrap(
    tmp_path: Path,
    site_packages: Path,
    cache_dir: Path,
    *,
    version: str = "0.86.2",
    cache_name: str = ".aider.tags.cache.v4",
) -> subprocess.CompletedProcess[str]:
    record = tmp_path / "record.json"
    runner = (
        "import runpy, sys; "
        "sys.path.insert(0, sys.argv.pop(1)); "
        "script = sys.argv.pop(1); sys.argv = [script, *sys.argv[1:]]; "
        "runpy.run_path(script, run_name='__main__')"
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            runner,
            str(site_packages),
            str(BOOTSTRAP),
            "--cache-dir",
            str(cache_dir),
            "--",
            "--map-tokens",
            "1024",
        ],
        cwd=tmp_path,
        env={**os.environ, "FAKE_AIDER_RECORD": str(record)},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_bootstrap_redirects_cache_and_preserves_positive_repo_map_budget(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "owned-cache"
    cache_dir.mkdir()
    existing_cache = checkout / ".aider.tags.cache.v4"
    existing_cache.mkdir()
    (existing_cache / "marker").write_text("user data", encoding="utf-8")
    site_packages = _fake_aider_installation(tmp_path / "fake-install")

    first = _run_bootstrap(checkout, site_packages, cache_dir)
    assert first.returncode == 0, first.stderr
    first_record = json.loads((checkout / "record.json").read_text(encoding="utf-8"))
    second = _run_bootstrap(checkout, site_packages, cache_dir)
    assert second.returncode == 0, second.stderr
    second_record = json.loads((checkout / "record.json").read_text(encoding="utf-8"))

    assert first_record["cache"] == str(cache_dir)
    assert first_record["argv"][first_record["argv"].index("--map-tokens") + 1] == "1024"
    assert second_record["cache"] == str(cache_dir)
    assert (cache_dir / "cache.db").read_text(encoding="utf-8") == "xx"
    assert (existing_cache / "marker").read_text(encoding="utf-8") == "user data"


def test_bootstrap_fails_closed_for_unknown_aider_version(tmp_path: Path) -> None:
    site_packages = _fake_aider_installation(tmp_path / "fake-install", version="0.86.3")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "owned-cache"
    cache_dir.mkdir()

    result = _run_bootstrap(checkout, site_packages, cache_dir)

    assert result.returncode == 86
    parsed = _parse_aider_adapter_error(result.stderr)
    assert parsed == (
        "AIDER_ADAPTER_UNSUPPORTED",
        "The cache adapter supports aider-chat 0.86.2; found 0.86.3",
    )
    assert not (cache_dir / "cache.db").exists()


def test_bootstrap_fails_closed_when_repomap_seam_changes(tmp_path: Path) -> None:
    site_packages = _fake_aider_installation(
        tmp_path / "fake-install", cache_name=".aider.tags.cache.v9"
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "owned-cache"
    cache_dir.mkdir()

    result = _run_bootstrap(checkout, site_packages, cache_dir)

    assert result.returncode == 86
    parsed = _parse_aider_adapter_error(result.stderr)
    assert parsed is not None and parsed[0] == "AIDER_ADAPTER_UNSUPPORTED"
    assert not (cache_dir / "cache.db").exists()


def test_aider_python_missing_path_is_a_structured_adapter_error(tmp_path: Path) -> None:
    with pytest.raises(AiderAdapterError) as exc_info:
        _validate_aider_python(tmp_path / "missing-python")

    assert exc_info.value.code == "AIDER_ADAPTER_UNAVAILABLE"


def test_custom_aider_command_cannot_be_combined_with_adapter(tmp_path: Path) -> None:
    interpreter = Path(sys.executable)
    with pytest.raises(AiderAdapterError) as exc_info:
        _validate_aider_python(interpreter, ["custom-aider", "--custom-flag"])

    assert exc_info.value.code == "AIDER_ADAPTER_INVALID_ARGUMENTS"


def test_adapter_diagnostics_parser_ignores_non_adapter_stderr() -> None:
    assert _parse_aider_adapter_error("ordinary Aider output") is None
    assert _parse_aider_adapter_error("HYBRID_SDLC_AIDER_ADAPTER_ERROR:null") == (
        "AIDER_ADAPTER_UNAVAILABLE",
        "The Aider cache adapter returned invalid diagnostics",
    )


def _install_inprocess_aider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str = "0.86.2",
    cache_name: str = ".aider.tags.cache.v4",
    main: object | None = None,
) -> types.ModuleType:
    package = types.ModuleType("aider")
    package.__path__ = []  # type: ignore[attr-defined]
    repomap = types.ModuleType("aider.repomap")
    repo_map = type("RepoMap", (), {"TAGS_CACHE_DIR": cache_name})
    repomap.RepoMap = repo_map  # type: ignore[attr-defined]
    main_module = types.ModuleType("aider.main")
    main_module.main = main if main is not None else lambda *, argv: 0  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aider", package)
    monkeypatch.setitem(sys.modules, "aider.repomap", repomap)
    monkeypatch.setitem(sys.modules, "aider.main", main_module)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: version)
    return repo_map


def test_bootstrap_main_coverage_redirects_cache_and_invokes_aider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.chdir(checkout)
    calls: list[list[str]] = []
    repo_map = _install_inprocess_aider(monkeypatch, main=lambda *, argv: calls.append(argv) or 7)

    status = bootstrap.main(["--cache-dir", str(cache_dir), "--", "--map-tokens", "1024"])

    assert status == 7
    assert repo_map.TAGS_CACHE_DIR == str(cache_dir.resolve())
    assert calls == [["--map-tokens", "1024"]]


def test_bootstrap_main_coverage_rejects_invalid_arguments_and_checkout_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bootstrap.main([]) == 86
    assert (
        _parse_aider_adapter_error(capsys.readouterr().err)[0] == "AIDER_ADAPTER_INVALID_ARGUMENTS"
    )

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    _install_inprocess_aider(monkeypatch)
    assert bootstrap.main(["--cache-dir", str(checkout), "--", "--map-tokens", "1024"]) == 86
    parsed = _parse_aider_adapter_error(capsys.readouterr().err)
    assert parsed is not None and parsed[0] == "AIDER_CACHE_PATH_INVALID"


def test_bootstrap_main_coverage_rejects_unsupported_version_and_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.chdir(checkout)
    args = ["--cache-dir", str(cache_dir), "--", "--map-tokens", "1024"]

    _install_inprocess_aider(monkeypatch, version="0.86.3")
    assert bootstrap.main(args) == 86
    parsed = _parse_aider_adapter_error(capsys.readouterr().err)
    assert parsed is not None and parsed[0] == "AIDER_ADAPTER_UNSUPPORTED"

    _install_inprocess_aider(monkeypatch, cache_name=".aider.tags.cache.v9")
    assert bootstrap.main(args) == 86
    parsed = _parse_aider_adapter_error(capsys.readouterr().err)
    assert parsed is not None and parsed[0] == "AIDER_ADAPTER_UNSUPPORTED"


def test_bootstrap_main_coverage_reports_missing_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.chdir(checkout)
    _install_inprocess_aider(monkeypatch)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError(name)),
    )

    assert bootstrap.main(["--cache-dir", str(cache_dir), "--", "--map-tokens", "1024"]) == 86
    parsed = _parse_aider_adapter_error(capsys.readouterr().err)
    assert parsed is not None and parsed[0] == "AIDER_ADAPTER_UNAVAILABLE"
