"""Integration coverage for safe Spec Kit initialization."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from hybrid_sdlc import spec_initializer
from hybrid_sdlc.spec_initializer import (
    FileAction,
    InitializationError,
    ManifestError,
    SpecKitCapability,
    SpecKitFeature,
    initialize_spec_kit,
    load_manifest,
    sha256_bytes,
)


@pytest.fixture(autouse=True)
def fake_spec_kit_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give integration tests a deterministic local Spec Kit executable response."""
    executable = "fake-specify"
    original_run = subprocess.run
    monkeypatch.setattr(spec_initializer.shutil, "which", lambda name: executable)

    def run(args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(args, (list, tuple)) and args and args[0] == executable:
            assert args == [executable, "version", "--features", "--json"]
            return subprocess.CompletedProcess(
                args, 0, '{"version":"test-version","features":{"spec":true}}', ""
            )
        return original_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(spec_initializer.subprocess, "run", run)


def test_initialization_creates_templates_and_second_run_is_idempotent(tmp_path: Path) -> None:
    first = initialize_spec_kit(tmp_path)
    first_bytes = {path: (tmp_path / path).read_bytes() for path in _template_paths()}

    assert first.spec_kit.compatible
    assert first.spec_kit.version == "test-version"
    assert first.spec_kit.features == (SpecKitFeature("spec", True),)
    assert all(file.action is FileAction.CREATED for file in first.files)
    assert (tmp_path / first.manifest_path).is_file()
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert len(manifest.files) == 4

    second = initialize_spec_kit(tmp_path)

    assert all(file.action is FileAction.UNCHANGED for file in second.files)
    assert all((tmp_path / path).read_bytes() == content for path, content in first_bytes.items())


def test_canonical_assets_load_from_source_checkout() -> None:
    root = Path(__file__).resolve().parents[2]
    assets = spec_initializer._canonical_templates()
    assert all(assets[path] == (root / path).read_bytes() for path in _template_paths())


def test_modified_managed_template_is_preserved_and_original_digest_retained(
    tmp_path: Path,
) -> None:
    initialize_spec_kit(tmp_path)
    template = tmp_path / _template_paths()[0]
    original = template.read_bytes()
    custom = b"user customization\n"
    template.write_bytes(custom)

    result = initialize_spec_kit(tmp_path)

    assert result.files[0].action is FileAction.PRESERVED
    assert template.read_bytes() == custom
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    record = next(entry for entry in manifest.files if entry.path == _template_paths()[0])
    assert record.sha256 == sha256_bytes(original)


def test_untracked_user_file_is_preserved_and_not_claimed(tmp_path: Path) -> None:
    template_path = _template_paths()[0]
    target = tmp_path / template_path
    target.parent.mkdir(parents=True)
    custom = b"created before initializer\n"
    target.write_bytes(custom)

    result = initialize_spec_kit(tmp_path)

    assert result.files[0].action is FileAction.PRESERVED
    assert target.read_bytes() == custom
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert template_path not in {entry.path for entry in manifest.files}


def test_force_backs_up_modified_files_and_reports_replacement(tmp_path: Path) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    custom = b"custom bytes\x00\xff"
    target.write_bytes(custom)

    result = initialize_spec_kit(tmp_path, force=True)

    file_result = result.files[0]
    assert file_result.action is FileAction.UPDATED
    assert file_result.backup_path is not None
    assert (tmp_path / file_result.backup_path).read_bytes() == custom
    assert target.read_bytes() == spec_initializer._canonical_templates()[_template_paths()[0]]

    second = initialize_spec_kit(tmp_path, force=True)
    assert all(file.backup_path is None for file in second.files)
    assert all(file.action is FileAction.UNCHANGED for file in second.files)


def test_unchanged_generated_file_updates_when_packaged_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    relative_path = _template_paths()[0]
    previous = spec_initializer._canonical_templates()
    changed = dict(previous)
    changed[relative_path] += b"\nNew toolkit clause.\n"
    monkeypatch.setattr(spec_initializer, "_canonical_templates", lambda: changed)

    result = initialize_spec_kit(tmp_path)

    assert result.files[0].action is FileAction.UPDATED
    assert (tmp_path / relative_path).read_bytes() == changed[relative_path]
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    record = next(entry for entry in manifest.files if entry.path == relative_path)
    assert record.sha256 == sha256_bytes(changed[relative_path])


def test_corrupt_manifest_fails_before_creating_templates(tmp_path: Path) -> None:
    specify = tmp_path / ".specify"
    specify.mkdir()
    (specify / ".hybrid-sdlc-manifest.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid ownership manifest"):
        initialize_spec_kit(tmp_path)

    assert not (tmp_path / _template_paths()[0]).exists()


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_incompatible_spec_kit_fails_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    capability = SpecKitCapability(
        None if missing else "specify",
        None,
        (),
        False,
        "Install or update Spec Kit before initializing.",
    )
    monkeypatch.setattr(spec_initializer, "detect_spec_kit", lambda: capability)

    with pytest.raises(InitializationError, match="Install or update Spec Kit"):
        initialize_spec_kit(tmp_path)

    assert not (tmp_path / ".specify").exists()
    assert not (tmp_path / ".specify" / ".hybrid-sdlc-manifest.json").exists()


def test_unsafe_directory_at_template_path_fails_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / _template_paths()[0]
    target.mkdir(parents=True)

    with pytest.raises(ManifestError, match="not a regular file"):
        initialize_spec_kit(tmp_path)

    assert not (tmp_path / _template_paths()[1]).exists()
    assert not (tmp_path / ".specify" / ".hybrid-sdlc-manifest.json").exists()


def test_backup_failure_preserves_every_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    originals: dict[str, bytes] = {}
    for relative_path in _template_paths()[:2]:
        content = b"custom " + relative_path.encode()
        (tmp_path / relative_path).write_bytes(content)
        originals[relative_path] = content

    def fail_backup(root: Path, relative_path: str, content: bytes) -> str:
        raise OSError("simulated backup disk failure")

    monkeypatch.setattr(spec_initializer, "_save_backup", fail_backup)
    with pytest.raises(InitializationError, match="recoverable backup"):
        initialize_spec_kit(tmp_path, force=True)

    assert all((tmp_path / path).read_bytes() == content for path, content in originals.items())


def test_canonical_assets_are_in_wheel(tmp_path: Path) -> None:
    """Build the actual wheel and verify all initializer resources are present."""
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = set(wheel.namelist())
        installed = tmp_path / "installed"
        wheel.extractall(installed)
        canonical_assets = {
            relative_path: (root / relative_path).read_bytes()
            for relative_path in _template_paths()
        }
    for relative_path in _template_paths():
        resource_path = "hybrid_sdlc/_specify/" + relative_path.removeprefix(".specify/")
        assert resource_path in members
        with zipfile.ZipFile(wheels[0]) as wheel:
            assert wheel.read(resource_path) == canonical_assets[relative_path]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; "
            "import hybrid_sdlc.spec_initializer as module; "
            "assert Path(module.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); "
            "assert len(module._canonical_templates()) == 4",
            str(installed),
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _template_paths() -> tuple[str, ...]:
    return (
        ".specify/memory/constitution.md",
        ".specify/templates/spec-template.md",
        ".specify/templates/plan-template.md",
        ".specify/templates/tasks-template.md",
    )
