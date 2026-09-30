# Google Antigravity

This guide targets Google Antigravity IDE **1.107.0**. It uses the workspace
MCP and skill discovery locations documented by Antigravity:

- MCP servers: `.agents/mcp_config.json`
- Workspace skills: `.agents/skills/<skill-name>/SKILL.md`

Hybrid SDLC ships both files at those paths in this repository. Open the
repository as an Antigravity workspace, then inspect its MCP server entry. The
server command is `hybrid-sdlc mcp`; the `hybrid-sdlc` executable must be on
`PATH` when Antigravity starts the server. Install the package in the Python
environment available to the IDE first.

The checked in skill at `.agents/skills/local-delegate/SKILL.md` mirrors the
canonical `skills/local-delegate/SKILL.md`. It covers trust assumptions,
execution mode selection, validation, job status and cancellation, and patch
review. Use it only for trusted repositories, committed task specs, and trusted
test profiles; MCP access does not sandbox repository commands or content.

## Verify workspace discovery

After opening this repository as the workspace, start a new Antigravity agent
conversation so it reloads workspace customizations. Confirm that the
`local-delegate` skill is offered and that the `hybrid-sdlc` MCP server exposes
`check_local_model`, `run_spec_task_sync`, `submit_spec_job`, `get_job_status`,
and `cancel_spec_job`. If the server does not appear, confirm that the
`hybrid-sdlc` executable is on Antigravity's `PATH`, then restart Antigravity
with this repository open. Only call `check_local_model` when the configured
local inference endpoint is expected to be running; it contacts that endpoint
but does not submit a task.

This discovery checklist is pending a live Antigravity workspace check; the
CLI version/help probe does not establish that the IDE loaded the workspace
files or connected the server.

Antigravity 1.107.0's CLI help also exposes `--add-mcp <json>`, described as
adding an MCP server definition to the user profile. That command is a separate
user-profile registration route and is not needed for this repository's
workspace configuration. Workspace discovery and live tool availability have
not been verified in an Antigravity session. In particular, do not infer that
Antigravity resumes an agent turn when an asynchronous job finishes.

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
