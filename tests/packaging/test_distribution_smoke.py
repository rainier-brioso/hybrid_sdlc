"""Opt-in smoke for wheel/sdist builds supplied by CI or a developer."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "packaging_smoke.py"
_SPEC = importlib.util.spec_from_file_location("packaging_smoke", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_PACKAGING_SMOKE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PACKAGING_SMOKE)
check_archive = _PACKAGING_SMOKE.check_archive
check_template_payloads = _PACKAGING_SMOKE.check_template_payloads
check_runtime_asset_payloads = _PACKAGING_SMOKE.check_runtime_asset_payloads


def _write_sdist(
    path: Path, extra: dict[str, bytes] | None = None, omit: set[str] | None = None
) -> None:
    members = {
        "package-0/.specify/memory/constitution.md": b"constitution",
        "package-0/.specify/templates/spec-template.md": b"spec",
        "package-0/.specify/templates/plan-template.md": b"plan",
        "package-0/.specify/templates/tasks-template.md": b"tasks",
        "package-0/README.md": b"readme",
        "package-0/pyproject.toml": b"[build-system]",
        "package-0/src/hybrid_sdlc/__init__.py": b"",
        "package-0/src/hybrid_sdlc/_aider_cache_bootstrap.py": b"# adapter\n",
        "package-0/src/hybrid_sdlc/_runtime/strata/compose.yaml": b"services: {}\n",
        "package-0/src/hybrid_sdlc/_runtime/strata/worker-defaults.json": b"{}\n",
        "package-0/src/hybrid_sdlc/_runtime/strata/startup.py": b"# startup\n",
    }
    for name in omit or set():
        members.pop(name, None)
    members.update(extra or {})
    with tarfile.open(path, "w:gz") as archive:
        for name, contents in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            archive.addfile(info, BytesIO(contents))


def test_sdist_project_root_prefix_is_normalized(tmp_path: Path) -> None:
    archive = tmp_path / "package.tar.gz"
    _write_sdist(archive)
    check_archive(archive)


def test_sdist_rejects_missing_templates_and_local_runtime_config(tmp_path: Path) -> None:
    missing = tmp_path / "missing.tar.gz"
    _write_sdist(missing, omit={"package-0/.specify/templates/tasks-template.md"})
    with pytest.raises(RuntimeError, match="missing required members"):
        check_archive(missing)

    leaked = tmp_path / "leaked.tar.gz"
    _write_sdist(leaked, {"package-0/hybrid_sdlc.toml": b"local"})
    with pytest.raises(RuntimeError, match="local/development files"):
        check_archive(leaked)


def test_sdist_rejects_template_bytes_that_differ_from_source(tmp_path: Path) -> None:
    archive = tmp_path / "drifted.tar.gz"
    _write_sdist(archive)
    canonical = {
        ".specify/memory/constitution.md": b"changed source constitution",
        ".specify/templates/spec-template.md": b"spec",
        ".specify/templates/plan-template.md": b"plan",
        ".specify/templates/tasks-template.md": b"tasks",
    }
    with pytest.raises(RuntimeError, match="template bytes that differ from source"):
        check_template_payloads(archive, canonical)


def test_sdist_requires_standalone_aider_cache_bootstrap(tmp_path: Path) -> None:
    archive = tmp_path / "missing-bootstrap.tar.gz"
    _write_sdist(archive, omit={"package-0/src/hybrid_sdlc/_aider_cache_bootstrap.py"})

    with pytest.raises(RuntimeError, match="missing required members"):
        check_archive(archive)


def test_sdist_requires_and_checks_runtime_assets(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.tar.gz"
    _write_sdist(archive)
    with pytest.raises(RuntimeError, match="runtime assets that differ from source"):
        check_runtime_asset_payloads(
            archive,
            {
                "src/hybrid_sdlc/_runtime/strata/compose.yaml": b"changed",
                "src/hybrid_sdlc/_runtime/strata/worker-defaults.json": b"{}\n",
                "src/hybrid_sdlc/_runtime/strata/startup.py": b"# startup\n",
            },
        )

    missing = tmp_path / "missing-runtime.tar.gz"
    _write_sdist(missing, omit={"package-0/src/hybrid_sdlc/_runtime/strata/compose.yaml"})
    with pytest.raises(RuntimeError, match="missing required members"):
        check_archive(missing)


def test_built_distributions_install_outside_source_tree() -> None:
    distribution_dir = os.environ.get("HYBRID_SDLC_PACKAGING_DIST")
    if not distribution_dir:
        pytest.skip("set HYBRID_SDLC_PACKAGING_DIST to a directory containing wheel and sdist")
    dist = Path(distribution_dir).resolve()
    if not dist.is_dir():
        pytest.fail(f"configured packaging distribution directory is unavailable: {dist}")
    uv = os.environ.get("HYBRID_SDLC_UV") or shutil.which("uv")
    if not uv:
        pytest.fail("uv is required to create isolated packaging smoke environments")

    repository = Path(__file__).resolve().parents[2]
    script = repository / "scripts" / "packaging_smoke.py"
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="hybrid-sdlc-packaging-runner-") as temporary:
        subprocess.run(
            [sys.executable, str(script), "--dist-dir", str(dist), "--uv", uv],
            cwd=temporary,
            env=environment,
            check=True,
            shell=False,
            timeout=1800,
        )


def test_configured_missing_distribution_directory_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HYBRID_SDLC_PACKAGING_DIST", str(tmp_path / "missing"))
    with pytest.raises(pytest.fail.Exception, match="configured packaging distribution directory"):
        test_built_distributions_install_outside_source_tree()


def test_configured_smoke_without_uv_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HYBRID_SDLC_PACKAGING_DIST", str(tmp_path))
    monkeypatch.delenv("HYBRID_SDLC_UV", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(pytest.fail.Exception, match="uv is required"):
        test_built_distributions_install_outside_source_tree()
