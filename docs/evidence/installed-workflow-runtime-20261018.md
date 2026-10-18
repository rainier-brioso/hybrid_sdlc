# Installed workflow and managed Strata preparation

Observed 2026-10-18 on Windows with Codex CLI `0.163.0-alpha.10.2`.

## Installation

The Codex profile had a manually registered Hybrid SDLC MCP server, but no
`hybrid-sdlc@hybrid-sdlc-local` plugin. A similarly named local-delegation skill
belonged to another plugin and was not the Hybrid SDLC workflow.

After publishing source commit `f048313`, a separate stable local checkout of
the feature branch was registered as marketplace `hybrid-sdlc-local`. The CLI
installed and enabled `hybrid-sdlc@hybrid-sdlc-local` version `0.1.0`. A binary
comparison confirmed that the cached skill matched the reviewed source.
The old manual MCP entry was removed; subsequent `mcp list --json` showed one
enabled `hybrid-sdlc` stdio server from the plugin, using `hybrid-sdlc mcp`.
No unrelated plugin or MCP registration was changed.

This verifies registration and cached files, not skill selection or tool
availability in a fresh desktop chat. Restart/reload and the HSDLC-077 trial
remain necessary. Keep the stable marketplace checkout available; the clone
currently tracks the reviewed feature branch, not merged `main`.

## Managed runtime setup

The previous manual Strata container was stopped. Read-only inspection found
its existing named data volume and the installed pinned runtime image. The
CLI was explicitly configured on loopback port 8080 to reuse that volume.
Before startup, status reported no lifecycle uncertainty and diagnostics
reported managed activity idle. `runtime strata start --json` succeeded.

The new managed service loaded the existing weights. The old manual container
remained stopped; no model volume or container was deleted. Startup logs showed
the RTX 3090, 16K context and a 1024-token reasoning budget. Non-generating
diagnostics reported loaded/model-available for
`qwen3.8-flash-next-coder-iq1_m` at `http://127.0.0.1:8080/v1`.
Do not start both manual and managed services against this shared volume/GPU.

## Readiness gap discovered

The installed `runtime strata diagnose --readiness --timeout 30 --json` did
not report ready. It returned `READINESS_EMPTY_CHOICES`; the server logs showed
the probe consumed its five output tokens while still thinking and produced no
answer. No restart was attempted on that evidence.

Two separate bounded diagnostic requests followed, with no repository/member
data: the first used 16 output tokens and reasoning budget 0, returned a length
finish and no answer; the second used 256 output tokens, reasoning budget 32
and low reasoning effort, returned HTTP 200, nonempty answer and stop finish.
These are request-local parameters, not changes to service or repository defaults.
The first request does not establish that zero budget disables thinking.

This establishes one successful small generation after managed startup, not
sustained performance or full-feature execution. The built-in readiness probe
still needs a provider-aware, bounded correction (HSDLC-086); do not silently
reinterpret its failed result as a CLI readiness pass.
