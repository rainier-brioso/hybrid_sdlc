"""Keep the installable Codex plugin aligned with its repository-root files."""

from __future__ import annotations

import json
from pathlib import Path


def test_codex_plugin_package_matches_root_manifests_and_skill() -> None:
    root = Path(__file__).resolve().parents[2]
    package = root / "plugins" / "hybrid-sdlc"

    mirrored_files = (
        (root / ".codex-plugin" / "plugin.json", package / ".codex-plugin" / "plugin.json"),
        (root / ".mcp.json", package / ".mcp.json"),
        (
            root / "skills" / "local-delegate" / "SKILL.md",
            package / "skills" / "local-delegate" / "SKILL.md",
        ),
    )
    for source, packaged in mirrored_files:
        assert source.read_bytes() == packaged.read_bytes()

    with (root / ".agents" / "plugins" / "marketplace.json").open("rb") as stream:
        marketplace = json.load(stream)
    plugin = marketplace["plugins"][0]
    assert plugin["name"] == "hybrid-sdlc"
    assert plugin["source"]["path"] == "./plugins/hybrid-sdlc"


def test_codex_plugin_package_has_skill_and_stdio_mcp_server() -> None:
    root = Path(__file__).resolve().parents[2]
    package = root / "plugins" / "hybrid-sdlc"

    with (package / ".mcp.json").open("rb") as stream:
        mcp_manifest = json.load(stream)
    server = mcp_manifest["mcpServers"]["hybrid-sdlc"]
    assert server["command"] == "hybrid-sdlc"
    assert server["args"] == ["mcp"]
    assert (package / "skills" / "local-delegate" / "SKILL.md").is_file()


def test_antigravity_workspace_files_match_canonical_skill_and_mcp() -> None:
    root = Path(__file__).resolve().parents[2]

    assert (root / ".agents" / "skills" / "local-delegate" / "SKILL.md").read_bytes() == (
        root / "skills" / "local-delegate" / "SKILL.md"
    ).read_bytes()

    with (root / ".mcp.json").open("rb") as stream:
        root_mcp = json.load(stream)
    with (root / ".agents" / "mcp_config.json").open("rb") as stream:
        antigravity_mcp = json.load(stream)
    assert antigravity_mcp["mcpServers"]["hybrid-sdlc"] == root_mcp["mcpServers"]["hybrid-sdlc"]
