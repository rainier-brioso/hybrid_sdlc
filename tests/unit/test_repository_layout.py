"""Unit tests verifying repository layout and gitignore rules."""

from __future__ import annotations

from pathlib import Path


def test_gitignore_contains_required_rules() -> None:
    root = Path(__file__).resolve().parents[2]
    gitignore_file = root / ".gitignore"
    assert gitignore_file.is_file()

    content = gitignore_file.read_text(encoding="utf-8")
    assert ".hybrid_sdlc/" in content
    assert "*.gguf" in content
    assert ".venv/" in content
    assert "*.lock" in content
    assert "!uv.lock" in content
