"""Version-checked entry point for Aider with an external RepoMap cache.

This file is launched directly by the configured Aider Python interpreter. Keep
it standalone: it must not import hybrid_sdlc or depend on its environment.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import stat
import sys
from pathlib import Path

SUPPORTED_AIDER_VERSION = "0.86.2"
SUPPORTED_CACHE_DIRS = {".aider.tags.cache.v3", ".aider.tags.cache.v4"}
ERROR_PREFIX = "HYBRID_SDLC_AIDER_ADAPTER_ERROR:"


def _fail(code: str, message: str) -> int:
    payload = json.dumps({"code": code, "message": message}, separators=(",", ":"))
    print(f"{ERROR_PREFIX}{payload}", file=sys.stderr, flush=True)
    return 86


def _parse_args(argv: list[str]) -> tuple[Path, list[str]]:
    if len(argv) < 3 or argv[0] != "--cache-dir" or "--" not in argv[1:]:
        raise ValueError("expected --cache-dir PATH -- AIDER_ARGUMENTS")
    separator = argv.index("--", 1)
    if separator != 2 or separator + 1 >= len(argv):
        raise ValueError("expected --cache-dir PATH -- AIDER_ARGUMENTS")
    return Path(argv[1]), argv[separator + 1 :]


def _contains_link_component(path: Path) -> bool:
    current = path
    while True:
        try:
            info = current.lstat()
        except OSError:
            return True
        if current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)()):
            return True
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if getattr(info, "st_file_attributes", 0) & reparse_point:
            return True
        if current.parent == current:
            return False
        current = current.parent


def main(argv: list[str] | None = None) -> int:
    """Validate the installed Aider seam, redirect its cache, then invoke it."""
    arguments = sys.argv[1:] if argv is None else argv
    try:
        cache_dir, aider_arguments = _parse_args(arguments)
    except ValueError as exc:
        return _fail("AIDER_ADAPTER_INVALID_ARGUMENTS", str(exc))

    try:
        if not cache_dir.is_absolute() or _contains_link_component(cache_dir):
            return _fail("AIDER_CACHE_PATH_INVALID", "The owned cache path is not a directory")
        resolved_cache_dir = cache_dir.resolve(strict=True)
        if resolved_cache_dir != cache_dir:
            return _fail("AIDER_CACHE_PATH_INVALID", "The owned cache path is not canonical")
        cache_dir = resolved_cache_dir
        if not cache_dir.is_dir():
            return _fail("AIDER_CACHE_PATH_INVALID", "The owned cache path is not a directory")
        checkout = Path.cwd().resolve(strict=True)
        try:
            cache_dir.relative_to(checkout)
        except ValueError:
            pass
        else:
            return _fail("AIDER_CACHE_PATH_INVALID", "The cache path is inside the task checkout")
    except (OSError, RuntimeError) as exc:
        return _fail("AIDER_CACHE_PATH_INVALID", f"The owned cache path is unavailable: {exc}")

    try:
        version = importlib.metadata.version("aider-chat")
    except importlib.metadata.PackageNotFoundError:
        return _fail("AIDER_ADAPTER_UNAVAILABLE", "The selected Python has no aider-chat package")
    except Exception as exc:
        return _fail("AIDER_ADAPTER_UNAVAILABLE", f"Could not inspect aider-chat: {exc}")

    if version != SUPPORTED_AIDER_VERSION:
        return _fail(
            "AIDER_ADAPTER_UNSUPPORTED",
            f"The cache adapter supports aider-chat {SUPPORTED_AIDER_VERSION}; found {version}",
        )

    try:
        repomap = importlib.import_module("aider.repomap")
        repo_map_class = getattr(repomap, "RepoMap", None)
        cache_name = getattr(repo_map_class, "TAGS_CACHE_DIR", None)
        if (
            repo_map_class is None
            or not isinstance(cache_name, str)
            or cache_name not in SUPPORTED_CACHE_DIRS
        ):
            return _fail(
                "AIDER_ADAPTER_UNSUPPORTED",
                "The installed RepoMap cache-directory seam differs from the supported Aider build",
            )
        aider_main = importlib.import_module("aider.main").main
        if not callable(aider_main):
            return _fail(
                "AIDER_ADAPTER_UNSUPPORTED", "The installed Aider main entry point is invalid"
            )
    except Exception as exc:
        return _fail(
            "AIDER_ADAPTER_UNAVAILABLE", f"Could not load the supported Aider entry point: {exc}"
        )

    repo_map_class.TAGS_CACHE_DIR = str(cache_dir)
    result = aider_main(argv=aider_arguments)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
