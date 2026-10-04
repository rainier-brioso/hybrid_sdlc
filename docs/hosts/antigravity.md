# Google Antigravity

This guide targets Google Antigravity IDE **1.107.0**. It uses the workspace
skill discovery location documented by Antigravity:

- Workspace skills: `.agents/skills/<skill-name>/SKILL.md`
- Workspace MCP definition: `.agents/mcp_config.json` (not discovered in the
  live IDE check described below)

Hybrid SDLC ships both workspace files in this repository. Install the package
in the Python environment available to the IDE first. The server command is
`hybrid-sdlc mcp`; the executable must be on `PATH` when Antigravity starts it,
or its absolute path must be used in the MCP definition.

The checked in skill at `.agents/skills/local-delegate/SKILL.md` mirrors the
canonical `skills/local-delegate/SKILL.md`. It covers trust assumptions,
execution mode selection, validation, job status and cancellation, and patch
review. Use it only for trusted repositories, committed task specs, and trusted
test profiles; MCP access does not sandbox repository commands or content.

## Connect the MCP server

Open this repository as an Antigravity workspace and reload the IDE window.
Confirm that the `local-delegate` skill is offered. In a live IDE 1.107.0
check, the checked-in `.agents/mcp_config.json` did **not** make the server
appear, even though the skill loaded and the IDE terminal resolved
`hybrid-sdlc` on `PATH`. A correctly shaped workspace plugin under
`.agents/plugins/hybrid-sdlc/` also did not appear. Treat automatic
workspace MCP discovery as unverified on this IDE version.

The working registration in that session was an explicit entry in the user's
`~/.gemini/config/mcp_config.json`, preserving existing server entries. To set
this up with the CLI, preview first, then apply:

```powershell
hybrid-sdlc setup antigravity --dry-run
hybrid-sdlc setup antigravity
```

The setup command must be invoked explicitly. It uses the absolute path of the
installed `hybrid-sdlc` executable and rejects a conflicting `hybrid-sdlc`
entry. If the config already exists and needs an update, the command leaves it
untouched and creates a private sibling proposal such as
`mcp_config.json.proposed.json` (numbered if that name already exists). Close
Antigravity, review the proposal against the current config, and manually merge
the `hybrid-sdlc` entry into the current `mcpServers` object. Do not blindly
replace the current file with a proposal, since another process may have
updated the config after the proposal was prepared. If the config does not yet
exist, the command creates it atomically and refuses to overwrite a file
created concurrently. Proposal files use mode `0600` on POSIX systems. Windows
uses the access control inherited from the parent directory, so keep the user
configuration directory restricted to the intended Windows account. For
manual setup, the entry has this shape; the CLI uses an absolute executable
path for `command`:

```json
"hybrid-sdlc": {
  "command": "hybrid-sdlc",
  "args": ["mcp"]
}
```

Add this as a sibling inside the existing `mcpServers` object if setting it up
manually; do not replace other servers. An absolute executable path may be
used for `command` if the IDE cannot resolve `hybrid-sdlc`. This is
**user-profile-wide**, not limited to this repository. Antigravity IDE's
`--add-mcp <json>` option also targets the user profile. The server now appears
in the IDE's MCP list and exposes
`check_local_model`, `run_spec_task_sync`, `submit_spec_job`, `get_job_status`,
and `cancel_spec_job`. An end-to-end call remains to be verified. Only call
`check_local_model` when the configured local inference endpoint is expected
to be running; it contacts that endpoint but does not submit a task. Do not
infer that Antigravity resumes an agent turn when an asynchronous job finishes.

## Synchronous and asynchronous runs

Use `run_spec_task_sync` when the current Antigravity turn can wait for the
bounded task and return its result. For persisted work, call `submit_spec_job`,
retain its job ID, then call `get_job_status` with the same repository root
until the job reaches a terminal state. MCP waits are bounded to 60 seconds;
poll again when the job remains queued or running. Treat cancellation as a
request and check status until termination is confirmed.

Reactive wakeup is unverified and remains experimental. Keep polling explicitly
for completion; do not rely on automatic resume or cross-session notifications.
Follow the [local delegation skill](../../skills/local-delegate/SKILL.md) to
validate a task before submission and review the final patch and test evidence.
