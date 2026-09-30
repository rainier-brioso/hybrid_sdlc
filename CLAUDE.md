# Hybrid SDLC in Claude Code

This repository configures the `hybrid-sdlc` MCP server in its root `.mcp.json`.
Claude Code may show it as pending approval until you open this trusted project
interactively and approve the project server. Check it with `claude mcp list`
and `claude mcp get hybrid-sdlc`.

Use the server only with a trusted repository, committed task spec, and trusted
test profile. Prefer `run_spec_task_sync` when the session can wait for the
bounded run. For persisted background work, use `submit_spec_job`, retain its
job ID, and poll `get_job_status` until terminal; do not promise a wakeup in a
later session. Review the exact patch and test evidence before returning work.
See [the Claude Code host guide](docs/hosts/claude-code.md).
