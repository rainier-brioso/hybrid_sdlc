"""Offline checks for the pinned Strata default injection hook."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from hybrid_sdlc._runtime.strata import startup


@pytest.fixture
def model_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "data/config/strata-coder-iq1_m.json"
    config.parent.mkdir(parents=True)
    monkeypatch.setattr(startup, "_MODEL_CONFIG_PATH", config)
    return config


def test_pinned_entrypoint_digest_and_terminal_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        startup._ENTRYPOINT_SHA256
        == "fc43a92c783d9d395f317357244f1c93ad5a556acf913d8d1afcd9cd2bdae39d"
    )
    source = '#!/bin/sh\nset -e\nsetup --no-start\nexec .venv/bin/python setup.py "$@"\n'
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_bytes(source.encode())
    monkeypatch.setattr(startup, "_ENTRYPOINT_SHA256", hashlib.sha256(source.encode()).hexdigest())
    hooked = startup._read_pinned_entrypoint(entrypoint)
    assert hooked.count(startup._APPLY_COMMAND) == 1
    assert hooked.index("setup --no-start") < hooked.index(startup._APPLY_COMMAND)
    assert hooked.endswith(startup._FINAL_COMMAND + "\n")
    assert hooked.index(startup._APPLY_COMMAND) < hooked.index(startup._FINAL_COMMAND)


def test_untrusted_entrypoint_is_rejected(tmp_path: Path) -> None:
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text("echo untrusted\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="checksum"):
        startup._read_pinned_entrypoint(entrypoint)
    with pytest.raises(RuntimeError, match="missing or unreadable"):
        startup._read_pinned_entrypoint(tmp_path / "missing")


@pytest.mark.parametrize(
    "source",
    [
        "echo no-terminal\n",
        startup._FINAL_COMMAND + "\necho later\n",
        startup._FINAL_COMMAND + "\n" + startup._FINAL_COMMAND + "\n",
    ],
)
def test_changed_terminal_shape_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_bytes(source.encode())
    monkeypatch.setattr(startup, "_ENTRYPOINT_SHA256", hashlib.sha256(source.encode()).hexdigest())
    with pytest.raises(RuntimeError, match="terminal command"):
        startup._read_pinned_entrypoint(entrypoint)


def test_default_is_atomic_preserves_fields_and_idempotent(model_config: Path) -> None:
    original = {
        "exe": "engine",
        "args": ["--context", "16384"],
        "private": "synthetic",
        "sampling": {"temperature": 0.3},
    }
    model_config.write_text(json.dumps(original), encoding="utf-8")
    startup._apply_model_default(model_config)
    assert json.loads(model_config.read_text()) == dict(original, reasoning_budget_tokens=1024)
    saved = model_config.read_bytes()
    startup._apply_model_default(model_config)
    assert model_config.read_bytes() == saved
    assert not list(model_config.parent.glob("*.tmp"))


@pytest.mark.parametrize("budget", [0, 2048, None])
def test_explicit_budget_is_not_changed(model_config: Path, budget: object) -> None:
    model_config.write_text(
        json.dumps({"reasoning_budget_tokens": budget, "other": True}), encoding="utf-8"
    )
    saved = model_config.read_bytes()
    startup._apply_model_default(model_config)
    assert model_config.read_bytes() == saved


@pytest.mark.parametrize("content", ["not-json", "[]", '"scalar"'])
def test_invalid_config_is_never_overwritten(model_config: Path, content: str) -> None:
    model_config.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError):
        startup._apply_model_default(model_config)
    assert model_config.read_text() == content


def test_outside_path_is_rejected(model_config: Path) -> None:
    with pytest.raises(RuntimeError, match="outside"):
        startup._apply_model_default(model_config.with_name("other.json"))


def test_bom_model_config_is_supported(model_config: Path) -> None:
    model_config.write_text('{"other": true}', encoding="utf-8-sig")
    startup._apply_model_default(model_config)
    assert json.loads(model_config.read_text()) == {"other": True, "reasoning_budget_tokens": 1024}


@pytest.mark.parametrize("budget", [1024, 0, 2048])
def test_first_install_applies_default_to_active_and_persisted_configs(
    model_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    active = tmp_path / "opt/strata/strata-coder-iq1_m.json"
    active.parent.mkdir(parents=True)
    monkeypatch.setattr(startup, "_ACTIVE_CONFIG_PATH", active)
    persisted: dict[str, object] = {"args": ["keep"]}
    if budget != 1024:
        persisted["reasoning_budget_tokens"] = budget
    model_config.write_text(json.dumps(persisted), encoding="utf-8")
    active.write_text('{"args": ["keep"]}', encoding="utf-8")
    startup._apply_startup_defaults(model_config)
    assert json.loads(model_config.read_text()) == {
        "args": ["keep"],
        "reasoning_budget_tokens": budget,
    }
    assert json.loads(active.read_text()) == {"args": ["keep"], "reasoning_budget_tokens": budget}


def test_explicit_active_config_value_is_preserved(
    model_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = tmp_path / "opt/strata/strata-coder-iq1_m.json"
    active.parent.mkdir(parents=True)
    monkeypatch.setattr(startup, "_ACTIVE_CONFIG_PATH", active)
    model_config.write_text("{}", encoding="utf-8")
    active.write_text('{"reasoning_budget_tokens": 0}', encoding="utf-8")
    startup._apply_startup_defaults(model_config)
    assert active.read_text() == '{"reasoning_budget_tokens": 0}'


def test_unexpected_active_symlink_is_rejected_before_update(
    model_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = tmp_path / "opt/strata/strata-coder-iq1_m.json"
    monkeypatch.setattr(startup, "_ACTIVE_CONFIG_PATH", active)
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == active)
    model_config.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unexpected target"):
        startup._apply_startup_defaults(model_config)
    assert model_config.read_text() == "{}"


@pytest.mark.parametrize("which", ["data", "config", "file"])
def test_symlink_boundaries_are_rejected_without_writes(
    model_config: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    model_config.write_text("{}\n", encoding="utf-8")
    saved = model_config.read_bytes()
    blocked = {
        "data": model_config.parents[1],
        "config": model_config.parent,
        "file": model_config,
    }[which]
    original_lstat = Path.lstat

    def simulated_link(path: Path) -> object:
        if path == blocked:
            return SimpleNamespace(st_mode=stat.S_IFLNK)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", simulated_link)
    with pytest.raises(RuntimeError, match="symlink"):
        startup._apply_model_default(model_config)
    assert model_config.read_bytes() == saved


def test_atomic_failure_preserves_original_and_cleans_temp(
    model_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_config.write_text("{}\n", encoding="utf-8")

    def refuse(*args: object) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(startup.os, "replace", refuse)
    with pytest.raises(RuntimeError, match="atomically"):
        startup._apply_model_default(model_config)
    assert model_config.read_text() == "{}\n"
    assert list(model_config.parent.iterdir()) == [model_config]


def test_exec_keeps_arguments_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []
    script = "# trusted synthetic script\n"
    monkeypatch.setattr(startup, "_read_pinned_entrypoint", lambda: script)
    monkeypatch.setattr(startup.sys, "argv", ["startup.py", "argument;not-code"])
    monkeypatch.setattr(startup.os, "execvpe", lambda *args: captured.extend(args))
    startup.main()
    assert captured[0] == "/bin/sh"
    assert captured[1] == ["/bin/sh", "-c", script, "hybrid-sdlc", "argument;not-code"]
