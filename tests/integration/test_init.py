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

    atomic_write = spec_initializer._atomic_write

    def fail_backup(target: Path, content: bytes) -> None:
        if "backups" in target.parts:
            raise OSError("simulated backup disk failure")
        atomic_write(target, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", fail_backup)
    with pytest.raises(InitializationError, match="initialization was incomplete"):
        initialize_spec_kit(tmp_path, force=True)

    assert all((tmp_path / path).read_bytes() == content for path, content in originals.items())


def test_interrupted_template_write_recovers_and_tracks_initializer_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = spec_initializer._atomic_write
    failed_target = tmp_path / _template_paths()[1]

    def fail_second_template(target: Path, content: bytes) -> None:
        if target == failed_target:
            raise OSError("simulated interruption")
        original_write(target, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", fail_second_template)
    with pytest.raises(InitializationError, match="incomplete"):
        initialize_spec_kit(tmp_path)
    assert (tmp_path / JOURNAL).is_file()

    monkeypatch.setattr(spec_initializer, "_atomic_write", original_write)
    initialize_spec_kit(tmp_path)
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert {entry.path for entry in manifest.files} == set(_template_paths())
    assert not (tmp_path / JOURNAL).exists()


def test_recovery_preserves_user_edit_made_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = spec_initializer._atomic_write
    failed_target = tmp_path / _template_paths()[1]

    def interrupt(target: Path, content: bytes) -> None:
        if target == failed_target:
            raise OSError("simulated interruption")
        original_write(target, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", interrupt)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path)
    edited = tmp_path / _template_paths()[0]
    user_bytes = b"edited after interruption\n"
    edited.write_bytes(user_bytes)

    monkeypatch.setattr(spec_initializer, "_atomic_write", original_write)
    result = initialize_spec_kit(tmp_path)
    assert edited.read_bytes() == user_bytes
    assert result.files[0].action is FileAction.PRESERVED
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert _template_paths()[0] not in {entry.path for entry in manifest.files}


def test_interrupted_manifest_write_recovers_on_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_save = spec_initializer.save_manifest

    def fail_manifest(root: Path, manifest: object) -> Path:
        raise OSError("simulated manifest interruption")

    monkeypatch.setattr(spec_initializer, "save_manifest", fail_manifest)
    with pytest.raises(InitializationError, match="incomplete"):
        initialize_spec_kit(tmp_path)
    assert all((tmp_path / path).is_file() for path in _template_paths())
    assert (tmp_path / JOURNAL).is_file()

    monkeypatch.setattr(spec_initializer, "save_manifest", original_save)
    initialize_spec_kit(tmp_path)
    manifest = load_manifest(tmp_path)
    assert manifest is not None
    assert {entry.path for entry in manifest.files} == set(_template_paths())


def test_interrupted_force_reuses_recoverable_original_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    original = b"the only copy of user content\n"
    target.write_bytes(original)
    atomic_write = spec_initializer._atomic_write

    def interrupt_replacement(path: Path, content: bytes) -> None:
        if (
            path == target
            and content == spec_initializer._canonical_templates()[_template_paths()[0]]
        ):
            raise OSError("simulated interruption")
        atomic_write(path, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", interrupt_replacement)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path, force=True)
    backup_paths = list((tmp_path / ".specify" / "backups").rglob(target.name))
    assert len(backup_paths) == 1
    assert backup_paths[0].read_bytes() == original

    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)
    result = initialize_spec_kit(tmp_path)
    backup_paths = list((tmp_path / ".specify" / "backups").rglob(target.name))
    assert len(backup_paths) == 1
    assert backup_paths[0].read_bytes() == original
    assert target.read_bytes() == spec_initializer._canonical_templates()[_template_paths()[0]]
    recovered = next(item for item in result.files if item.path == _template_paths()[0])
    assert recovered.action is FileAction.UPDATED
    assert recovered.backup_path is not None
    assert (tmp_path / recovered.backup_path).read_bytes() == original


def test_interrupted_force_preserves_later_user_edit_and_keeps_original_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    original = b"pre-force user customization\n"
    target.write_bytes(original)
    atomic_write = spec_initializer._atomic_write

    def interrupt(path: Path, content: bytes) -> None:
        if (
            path == target
            and content == spec_initializer._canonical_templates()[_template_paths()[0]]
        ):
            raise OSError("simulated interruption")
        atomic_write(path, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", interrupt)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path, force=True)
    later_edit = b"changed after the interrupted force\n"
    target.write_bytes(later_edit)
    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)

    result = initialize_spec_kit(tmp_path)

    assert target.read_bytes() == later_edit
    backup_path = next(
        item.backup_path for item in result.files if item.path == _template_paths()[0]
    )
    assert backup_path is not None
    assert (tmp_path / backup_path).read_bytes() == original


def test_recovery_does_not_recreate_file_deleted_after_interrupted_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    target.write_bytes(b"customized before force\n")
    atomic_write = spec_initializer._atomic_write

    def interrupt(path: Path, content: bytes) -> None:
        if (
            path == target
            and content == spec_initializer._canonical_templates()[_template_paths()[0]]
        ):
            raise OSError("simulated interruption")
        atomic_write(path, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", interrupt)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path, force=True)
    target.unlink()
    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)

    result = initialize_spec_kit(tmp_path)

    assert not target.exists()
    assert (
        next(item for item in result.files if item.path == _template_paths()[0]).action
        is FileAction.PRESERVED
    )


def test_dry_run_force_rejects_non_directory_backup_root(tmp_path: Path) -> None:
    backup_root = tmp_path / ".specify" / "backups"
    backup_root.parent.mkdir()
    backup_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ManifestError, match="backup path is not a directory"):
        initialize_spec_kit(tmp_path, force=True, dry_run=True)

    assert backup_root.is_file()
    assert not (tmp_path / JOURNAL).exists()


def test_dry_run_force_rejects_unsafe_planned_backup_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    template = tmp_path / _template_paths()[0]
    template.write_bytes(b"customized template")
    invalid_destination = ".specify/backups/fixed/.specify/memory/constitution.md"
    destination = tmp_path.joinpath(*invalid_destination.split("/"))
    destination.mkdir(parents=True)
    monkeypatch.setattr(spec_initializer, "_backup_relative_path", lambda path: invalid_destination)

    with pytest.raises(ManifestError, match="backup destination is not a regular file"):
        initialize_spec_kit(tmp_path, force=True, dry_run=True)

    assert template.read_bytes() == b"customized template"
    assert not (tmp_path / JOURNAL).exists()


def test_dry_run_pending_journal_rejects_symlinked_backup_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    target.write_bytes(b"customized before interrupted force")
    atomic_write = spec_initializer._atomic_write
    journal_path = tmp_path / JOURNAL

    def create_unsafe_backup_root(path: Path, content: bytes) -> None:
        atomic_write(path, content)
        if path == journal_path:
            outside = tmp_path / "outside"
            outside.mkdir()
            backup_root = tmp_path / ".specify" / "backups"
            try:
                backup_root.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                pytest.skip(f"directory symlinks unavailable: {exc}")

    monkeypatch.setattr(spec_initializer, "_atomic_write", create_unsafe_backup_root)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path, force=True)
    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)

    with pytest.raises(ManifestError, match="symlink or reparse point"):
        initialize_spec_kit(tmp_path, dry_run=True)

    assert journal_path.is_file()
    assert target.read_bytes() == b"customized before interrupted force"


def test_dry_run_pending_journal_rejects_mismatched_existing_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    target.write_bytes(b"customized before interrupted force")
    atomic_write = spec_initializer._atomic_write
    journal_path = tmp_path / JOURNAL
    backup_file: Path | None = None

    def occupy_backup_after_journal(path: Path, content: bytes) -> None:
        nonlocal backup_file
        atomic_write(path, content)
        if path == journal_path:
            journal = spec_initializer._load_journal(journal_path)
            backup_relative_path = str(journal["entries"][0]["backup_path"])
            backup_file = tmp_path.joinpath(*backup_relative_path.split("/"))
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_bytes(b"different file already at backup destination")

    monkeypatch.setattr(spec_initializer, "_atomic_write", occupy_backup_after_journal)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path, force=True)
    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)

    with pytest.raises(ManifestError, match="recoverable backup is occupied or changed"):
        initialize_spec_kit(tmp_path, dry_run=True)

    assert backup_file is not None and backup_file.is_file()
    assert backup_file.read_bytes() == b"different file already at backup destination"
    assert target.read_bytes() == b"customized before interrupted force"


def test_fresh_dry_run_rejects_mismatched_backup_destination_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_spec_kit(tmp_path)
    target = tmp_path / _template_paths()[0]
    target.write_bytes(b"customized template")
    backup_relative = (
        ".specify/backups/20260928T000000.000000Z-"
        "0123456789abcdef0123456789abcdef/.specify/memory/constitution.md"
    )
    backup = tmp_path.joinpath(*backup_relative.split("/"))
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"different file already at backup destination")
    monkeypatch.setattr(spec_initializer, "_backup_relative_path", lambda path: backup_relative)

    with pytest.raises(ManifestError, match="recoverable backup is occupied or changed"):
        initialize_spec_kit(tmp_path, force=True, dry_run=True)

    assert target.read_bytes() == b"customized template"
    assert backup.read_bytes() == b"different file already at backup destination"


def test_stale_journal_after_manifest_save_is_idempotently_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_unlink = Path.unlink
    journal_path = tmp_path / JOURNAL

    def leave_journal(path: Path, *args: object, **kwargs: object) -> None:
        if path == journal_path:
            return
        original_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", leave_journal)
    initialize_spec_kit(tmp_path)
    assert journal_path.is_file()
    monkeypatch.setattr(Path, "unlink", original_unlink)
    initialize_spec_kit(tmp_path)
    assert not journal_path.exists()
    assert len(load_manifest(tmp_path).files) == 4  # type: ignore[union-attr]


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    journal = tmp_path / JOURNAL
    journal.write_text("{broken", encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid initialization journal"):
        initialize_spec_kit(tmp_path)
    assert not (tmp_path / _template_paths()[0]).exists()


def test_dry_run_creates_no_files_or_directories(tmp_path: Path) -> None:
    result = initialize_spec_kit(tmp_path, dry_run=True)
    assert all(item.action is FileAction.CREATED for item in result.files)
    assert not (tmp_path / ".specify").exists()
    assert not (tmp_path / JOURNAL).exists()


def test_dry_run_previews_interrupted_journal_without_recovery_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    atomic_write = spec_initializer._atomic_write
    failed_target = tmp_path / _template_paths()[1]

    def interrupt(target: Path, content: bytes) -> None:
        if target == failed_target:
            raise OSError("simulated interruption")
        atomic_write(target, content)

    monkeypatch.setattr(spec_initializer, "_atomic_write", interrupt)
    with pytest.raises(InitializationError):
        initialize_spec_kit(tmp_path)
    journal = tmp_path / JOURNAL
    journal_bytes = journal.read_bytes()
    template_bytes = (tmp_path / _template_paths()[0]).read_bytes()

    monkeypatch.setattr(spec_initializer, "_atomic_write", atomic_write)
    result = initialize_spec_kit(tmp_path, dry_run=True)

    assert result.dry_run
    assert len(result.files) == len(_template_paths())
    assert result.files[0].action is FileAction.UNCHANGED
    assert result.files[1].action is FileAction.CREATED
    assert journal.read_bytes() == journal_bytes
    assert (tmp_path / _template_paths()[0]).read_bytes() == template_bytes
    assert not (tmp_path / MANIFEST_RELATIVE_PATH).exists()


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


JOURNAL = ".hybrid-sdlc-init-journal.json"
MANIFEST_RELATIVE_PATH = ".specify/.hybrid-sdlc-manifest.json"
