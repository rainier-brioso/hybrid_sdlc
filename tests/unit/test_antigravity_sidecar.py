"""Safety contracts for explicit Antigravity App per-job sidecars."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hybrid_sdlc.antigravity_sidecar import (
    _MAX_CONFIG_BYTES,
    AntigravitySidecarError,
    _read_config,
    prepare_antigravity_sidecar,
)
from hybrid_sdlc.job_manager import JobManager

JOB_ID = "job_20261019T123456789012Z_0123abcd"
CONVERSATION_ID = "6bddf934-8c2a-4310-a8cc-b3deed89174c"


@pytest.fixture
def sidecar_context(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    JobManager(repo).create("spec.md", "SIDECAR-TEST", job_id=JOB_ID)
    app = tmp_path / "Antigravity.exe"
    app.write_bytes(b"test host executable")
    cli = tmp_path / "hybrid-sdlc.exe"
    cli.write_bytes(b"test installed cli")
    return {"repo": repo, "app": app, "cli": cli, "config_dir": tmp_path / "gemini" / "config"}


def _call(context: dict[str, Path], **overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "repo_root": context["repo"],
        "job_id": JOB_ID,
        "conversation_id": CONVERSATION_ID,
        "app_executable": context["app"],
        "allow_model_continuation": True,
        "config_dir": context["config_dir"],
        "executable": context["cli"],
    }
    args.update(overrides)
    return prepare_antigravity_sidecar(**args)  # type: ignore[arg-type]


def test_dry_run_reports_plan_without_creating_config_or_sidecar(
    sidecar_context: dict[str, Path],
) -> None:
    result = _call(sidecar_context, dry_run=True)
    config_dir = sidecar_context["config_dir"]

    assert result["action"] == "prepare"
    assert result["dry_run"] is True
    assert result["proposal_path"] is None
    assert not config_dir.exists()


def test_setup_preserves_existing_config_and_publishes_private_proposal(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    original = b'{\n  "plugins": {"github": {"enabled": true}}, "other": 7\n}\n'
    config_path.write_bytes(original)

    result = _call(sidecar_context)

    assert result["action"] == "prepared"
    assert config_path.read_bytes() == original
    proposal_path = Path(str(result["proposal_path"]))
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    sidecar_id = str(result["sidecar_id"])
    assert proposal["plugins"] == {"github": {"enabled": True}}
    assert proposal["other"] == 7
    assert proposal["sidecars"][sidecar_id] == {"enabled": True}
    if os.name != "nt":
        assert proposal_path.stat().st_mode & 0o777 == 0o600
    manifest = json.loads(Path(str(result["manifest_path"])).read_text(encoding="utf-8"))
    assert Path(str(result["manifest_path"])).parent.name == sidecar_id
    assert Path(str(result["manifest_path"])).name == "sidecar.json"
    assert set(manifest) == {"command", "args", "restart_policy"}
    assert manifest["restart_policy"] == "never"
    assert manifest["command"] == str(sidecar_context["cli"])
    assert manifest["args"] == [
        "bridge",
        "antigravity",
        JOB_ID,
        "--repo-root",
        str(sidecar_context["repo"]),
        "--conversation-id",
        CONVERSATION_ID,
        "--app-executable",
        str(sidecar_context["app"]),
        "--allow-model-continuation",
        "--wait-timeout",
        "1800",
        "--send-timeout",
        "30",
        "--json",
    ]


def test_setup_with_new_config_uses_exclusive_creation_and_authorization_flag(
    sidecar_context: dict[str, Path],
) -> None:
    result = _call(sidecar_context, allow_model_continuation=True)

    config_path = Path(str(result["config_path"]))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["proposal_path"] is None
    assert config["sidecars"][result["sidecar_id"]] == {"enabled": True}
    manifest = json.loads(Path(str(result["manifest_path"])).read_text(encoding="utf-8"))
    assert "--allow-model-continuation" in manifest["args"]


def test_setup_is_idempotent_when_owned_manifest_already_exists(
    sidecar_context: dict[str, Path],
) -> None:
    first = _call(sidecar_context)
    second = _call(sidecar_context)

    assert first["sidecar_id"] == second["sidecar_id"]
    assert second["action"] == "already_configured"
    assert first["manifest_path"] == second["manifest_path"]


def test_conflicting_sidecar_entry_is_rejected_without_writes(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    sidecar_id = (
        "hybrid-sdlc-"
        + hashlib.sha256(f"{sidecar_context['repo']}\0{JOB_ID}".encode()).hexdigest()[:20]
    )
    config_path = config_dir / "config.json"
    original = json.dumps({"sidecars": {sidecar_id: {"enabled": False}}}).encode()
    config_path.write_bytes(original)

    with pytest.raises(AntigravitySidecarError, match="conflicting host config"):
        _call(sidecar_context)

    assert config_path.read_bytes() == original
    assert not (config_dir / "sidecars").exists()


def test_enabled_unowned_config_entry_is_not_adopted(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    sidecar_id = (
        "hybrid-sdlc-"
        + hashlib.sha256(f"{sidecar_context['repo']}\0{JOB_ID}".encode()).hexdigest()[:20]
    )
    config_path = config_dir / "config.json"
    original = json.dumps({"sidecars": {sidecar_id: {"enabled": True}}}).encode()
    config_path.write_bytes(original)

    with pytest.raises(AntigravitySidecarError, match="without ownership evidence"):
        _call(sidecar_context)

    assert config_path.read_bytes() == original
    assert not (config_dir / "sidecars").exists()


def test_setup_requires_continuation_authority_without_writes(
    sidecar_context: dict[str, Path],
) -> None:
    with pytest.raises(AntigravitySidecarError, match="explicit model-continuation consent"):
        _call(sidecar_context, allow_model_continuation=False)
    assert not sidecar_context["config_dir"].exists()


def test_duplicate_keys_and_oversized_host_config_fail_closed(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text('{"plugins": {}, "plugins": {}}', encoding="utf-8")
    with pytest.raises(AntigravitySidecarError, match="valid JSON"):
        _call(sidecar_context)
    config_path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(AntigravitySidecarError, match="size limit"):
        _call(sidecar_context)


def test_config_read_is_bounded_if_file_grows_after_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.json"
    config.write_bytes(b'{"ok":true}' + b" " * _MAX_CONFIG_BYTES)
    real_stat = Path.stat
    real_open = Path.open
    read_sizes: list[int] = []

    def stale_stat(path: Path, *args: object, **kwargs: object) -> object:
        result = real_stat(path, *args, **kwargs)
        if path == config:
            return SimpleNamespace(st_size=1, st_mode=result.st_mode)
        return result

    class ReadProbe:
        def __init__(self, stream: object) -> None:
            self.stream = stream

        def __enter__(self) -> ReadProbe:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self.stream.read(size)  # type: ignore[attr-defined,no-any-return]

    def tracked_open(path: Path, *args: object, **kwargs: object) -> object:
        stream = real_open(path, *args, **kwargs)
        return ReadProbe(stream) if path == config else stream

    monkeypatch.setattr(Path, "stat", stale_stat)
    monkeypatch.setattr(Path, "open", tracked_open)

    with pytest.raises(AntigravitySidecarError, match="size limit"):
        _read_config(config)

    assert read_sizes == [_MAX_CONFIG_BYTES + 1]


def test_validation_happens_before_sidecar_config_writes(
    sidecar_context: dict[str, Path],
) -> None:
    with pytest.raises(AntigravitySidecarError, match="persisted job"):
        _call(sidecar_context, job_id="job_20261019T123456789012Z_deadbeef")
    assert not sidecar_context["config_dir"].exists()

    with pytest.raises(AntigravitySidecarError, match="conversation UUID"):
        _call(sidecar_context, conversation_id="not-a-uuid")
    assert not sidecar_context["config_dir"].exists()


def test_setup_rejects_invalid_timeout_and_non_executable_app_path(
    sidecar_context: dict[str, Path],
) -> None:
    with pytest.raises(AntigravitySidecarError, match="wait_timeout_seconds"):
        _call(sidecar_context, wait_timeout_seconds=float("inf"))
    bad_app = sidecar_context["repo"].parent / "Antigravity.cmd"
    bad_app.write_bytes(b"not an exe")
    with pytest.raises(AntigravitySidecarError, match="EXE"):
        _call(sidecar_context, app_executable=bad_app)
    zero_wait = _call(sidecar_context, dry_run=True, wait_timeout_seconds=0)
    assert zero_wait["dry_run"] is True
    assert not sidecar_context["config_dir"].exists()


def test_remove_prepares_disable_proposal_then_removes_only_owned_sidecar(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    original = b'{"plugins":{"github":{"enabled":true}},"sidecars":{}}\n'
    config_path.write_bytes(original)
    prepared = _call(sidecar_context)
    manifest_path = Path(str(prepared["manifest_path"]))
    ownership_path = Path(str(prepared["ownership_path"]))
    config_path.write_bytes(Path(str(prepared["proposal_path"])).read_bytes())
    enabled_config = config_path.read_bytes()

    removed = _call(sidecar_context, remove=True, allow_model_continuation=False)

    assert removed["action"] == "remove"
    assert config_path.read_bytes() == enabled_config
    removal_proposal = json.loads(Path(str(removed["proposal_path"])).read_text())
    assert removal_proposal["sidecars"] == {}
    assert removal_proposal["plugins"] == {"github": {"enabled": True}}
    assert not manifest_path.exists()
    assert not ownership_path.exists()


def test_oversized_owned_manifest_fails_closed(
    sidecar_context: dict[str, Path],
) -> None:
    prepared = _call(sidecar_context)
    manifest_path = Path(str(prepared["manifest_path"]))
    manifest_path.write_bytes(b" " * (64 * 1024 + 1))

    with pytest.raises(AntigravitySidecarError, match="manifest exceeds"):
        _call(sidecar_context, remove=True)

    assert manifest_path.exists()


def test_owner_only_partial_removal_is_recovered_by_explicit_remove(
    sidecar_context: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _call(sidecar_context)
    manifest_path = Path(str(prepared["manifest_path"]))
    ownership_path = Path(str(prepared["ownership_path"]))
    original_unlink = Path.unlink
    fail_owner_unlink = True

    def fail_second_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal fail_owner_unlink
        if path == ownership_path and fail_owner_unlink:
            fail_owner_unlink = False
            raise OSError("injected ownership cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second_unlink)
    with pytest.raises(AntigravitySidecarError, match="safely update"):
        _call(sidecar_context, remove=True, allow_model_continuation=False)
    assert not manifest_path.exists()
    assert ownership_path.exists()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    result = _call(sidecar_context, remove=True, allow_model_continuation=False)
    assert result["action"] == "remove"
    assert not ownership_path.exists()


def test_unreadable_or_unknown_owner_only_state_fails_closed(
    sidecar_context: dict[str, Path],
) -> None:
    prepared = _call(sidecar_context)
    manifest_path = Path(str(prepared["manifest_path"]))
    ownership_path = Path(str(prepared["ownership_path"]))
    manifest_path.unlink()
    ownership_path.write_text('{"schema_version": 1}', encoding="utf-8")

    with pytest.raises(AntigravitySidecarError, match="ownership record"):
        _call(sidecar_context, remove=True, allow_model_continuation=False)

    assert ownership_path.exists()


def test_remove_refuses_tampered_manifest_and_preserves_sidecar(
    sidecar_context: dict[str, Path],
) -> None:
    prepared = _call(sidecar_context)
    manifest_path = Path(str(prepared["manifest_path"]))
    manifest_path.write_text('{"command":"other"}', encoding="utf-8")

    with pytest.raises(AntigravitySidecarError, match="modified"):
        _call(sidecar_context, remove=True)

    assert manifest_path.exists()
    assert Path(str(prepared["ownership_path"])).exists()


def test_remove_accepts_host_disabled_owned_entry(
    sidecar_context: dict[str, Path],
) -> None:
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.json"
    config_path.write_text('{"plugins":{"github":{"enabled":true}}}', encoding="utf-8")
    prepared = _call(sidecar_context)
    enabled = json.loads(Path(str(prepared["proposal_path"])).read_text(encoding="utf-8"))
    sidecar_id = str(prepared["sidecar_id"])
    enabled["sidecars"][sidecar_id]["enabled"] = False
    enabled["sidecars"][sidecar_id]["resolvedPermissions"] = []
    config_path.write_text(json.dumps(enabled), encoding="utf-8")

    removed = _call(sidecar_context, remove=True, allow_model_continuation=False)

    assert removed["action"] == "remove"
    proposal = json.loads(Path(str(removed["proposal_path"])).read_text(encoding="utf-8"))
    assert proposal["sidecars"] == {}
    assert proposal["plugins"] == {"github": {"enabled": True}}


def test_removal_cannot_change_job_or_conversation_target(
    sidecar_context: dict[str, Path],
) -> None:
    prepared = _call(sidecar_context)

    with pytest.raises(AntigravitySidecarError, match="ownership record"):
        _call(sidecar_context, remove=True, conversation_id="0f39f5d2-880a-4570-830c-04797594c101")

    assert Path(str(prepared["manifest_path"])).exists()


def test_symlinked_sidecar_directory_is_rejected(
    sidecar_context: dict[str, Path], tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    config_dir = sidecar_context["config_dir"]
    config_dir.mkdir(parents=True)
    try:
        (config_dir / "sidecars").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")

    with pytest.raises(AntigravitySidecarError, match="symlink"):
        _call(sidecar_context)

    assert list(outside.iterdir()) == []
