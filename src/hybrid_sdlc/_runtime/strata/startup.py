"""Safely apply Hybrid SDLC's Strata model default before the API server starts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

_ENTRYPOINT_PATH = Path("/opt/strata/docker-entrypoint.sh")
_ENTRYPOINT_SHA256 = "fc43a92c783d9d395f317357244f1c93ad5a556acf913d8d1afcd9cd2bdae39d"
_MODEL_CONFIG_PATH = Path("/data/config/strata-coder-iq1_m.json")
_ACTIVE_CONFIG_PATH = Path("/opt/strata/strata-coder-iq1_m.json")
_FINAL_COMMAND = 'exec .venv/bin/python setup.py "$@"'
_APPLY_COMMAND = '/opt/strata/.venv/bin/python /opt/hybrid-sdlc/startup.py --apply "$cfg"'


def _fail(message: str) -> NoReturn:
    print(f"hybrid-sdlc Strata startup: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_pinned_entrypoint(path: Path = _ENTRYPOINT_PATH) -> str:
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("pinned Strata entrypoint is missing or unreadable") from exc
    if hashlib.sha256(source).hexdigest() != _ENTRYPOINT_SHA256:
        raise RuntimeError("pinned Strata entrypoint checksum does not match")
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("pinned Strata entrypoint is not UTF-8") from exc

    lines = text.splitlines(keepends=True)
    matching = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == _FINAL_COMMAND]
    if len(matching) != 1 or not lines or matching[0] != len(lines) - 1:
        raise RuntimeError("pinned Strata entrypoint has an unexpected terminal command")
    lines.insert(matching[0], f"{_APPLY_COMMAND}\n")
    return "".join(lines)


def _apply_model_default(
    config_path: Path = _MODEL_CONFIG_PATH, *, default_budget: object = 1024
) -> None:
    """Set the default only when absent, preserving user-selected model settings."""

    if config_path not in (_MODEL_CONFIG_PATH, _ACTIVE_CONFIG_PATH):
        raise RuntimeError("model config path is outside the supported Strata config location")
    for directory in (config_path.parents[1], config_path.parent):
        try:
            directory_mode = directory.lstat().st_mode
        except OSError as exc:
            raise RuntimeError("Strata model config directory is missing or unreadable") from exc
        if not stat.S_ISDIR(directory_mode):
            raise RuntimeError("Strata model config directory must not be a symlink")

    try:
        file_stat = config_path.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError("Strata model config must be a regular file, not a symlink")
        existing = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Strata model config is missing, unreadable, or invalid JSON") from exc
    if not isinstance(existing, dict):
        raise RuntimeError("Strata model config must contain a JSON object")
    if "reasoning_budget_tokens" in existing:
        return

    updated = dict(existing)
    updated["reasoning_budget_tokens"] = default_budget
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(updated, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, stat.S_IMODE(file_stat.st_mode))
        os.replace(temporary_path, config_path)
        temporary_path = None
    except OSError as exc:
        raise RuntimeError("could not atomically update Strata model config") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _apply_startup_defaults(config_path: Path) -> None:
    """Cover upstream's first-install regular file and later persisted symlink."""
    if config_path != _MODEL_CONFIG_PATH:
        raise RuntimeError("model config path is outside the supported Strata config location")
    linked = _ACTIVE_CONFIG_PATH.is_symlink()
    if linked and _ACTIVE_CONFIG_PATH.resolve() != _MODEL_CONFIG_PATH.resolve():
        raise RuntimeError("active Strata config symlink has an unexpected target")
    _apply_model_default(config_path)
    if not linked:
        # First setup copies /opt's regular file to /data, but still launches
        # from /opt. Keep its default consistent with the persisted selection.
        try:
            saved = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("could not read persisted Strata budget") from exc
        _apply_model_default(_ACTIVE_CONFIG_PATH, default_budget=saved["reasoning_budget_tokens"])


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--apply":
        try:
            _apply_startup_defaults(Path(sys.argv[2]))
        except RuntimeError as exc:
            _fail(str(exc))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        _fail("invalid model config arguments")

    try:
        script = _read_pinned_entrypoint()
    except RuntimeError as exc:
        _fail(str(exc))
    os.execvpe("/bin/sh", ["/bin/sh", "-c", script, "hybrid-sdlc", *sys.argv[1:]], os.environ)


if __name__ == "__main__":
    main()
