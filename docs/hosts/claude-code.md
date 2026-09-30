# Claude Code

This repository includes a project MCP configuration for Hybrid SDLC. The
instructions here were checked with Claude Code CLI **2.1.268**.

## Prerequisites and project trust

- Install Hybrid SDLC in an environment whose `hybrid-sdlc` executable is on
  `PATH` when Claude Code starts MCP servers. The server command is
  `hybrid-sdlc mcp`.
- Open this repository in Claude Code and approve the project MCP server when
  prompted. Project servers from `.mcp.json` remain pending and are not
  connected until approved in an interactive session. Approve only projects
  whose repository instructions and code you trust.
- The runner invokes a local model and the repository's named test profile.
  Use it only for trusted repositories, committed task specs, and trusted test
  commands. MCP access does not sandbox repository content or commands.

## MCP configuration

Claude Code reads project MCP servers from the repository root `.mcp.json`.
This repository already defines `hybrid-sdlc` there, using stdio with command
`hybrid-sdlc` and argument `mcp`; no second Claude configuration file or
duplicate registration is needed.

From the repository root, inspect discovery and approval state with:

```sh
claude mcp list
claude mcp get hybrid-sdlc
```

With Claude Code 2.1.268, both commands identify this entry as project-scoped
and pending approval until an interactive session approves it. The `get`
command displays the configured server command. You can also start an
interactive Claude Code session in the trusted repository and follow its
project-server approval prompt.

For another repository without this configuration, add a project-scoped
server from that repository root with:

```sh
claude mcp add --scope project hybrid-sdlc -- hybrid-sdlc mcp
```

This writes that repository's `.mcp.json`. Do not run it here: this repository
already carries the entry. To remove a registration created this way, use
`claude mcp remove --scope project hybrid-sdlc` from its repository root; it
changes that project's MCP configuration.

## Synchronous and asynchronous work

Use `run_spec_task_sync` when Claude Code can wait for the bounded run and
present its result in the current session. Claude Code 2.1.212 and later can
automatically background some long-running synchronous tool calls after about
two minutes. That host behavior does not turn the call into a persisted Hybrid
SDLC job or guarantee a notification after the Claude session ends. Progress
or completion notifications are useful only in the session that is still
available to receive them.

For persisted work that can be checked later, call `submit_spec_job`, retain
the returned job ID, and call `get_job_status` with that ID until a terminal
state appears. Continue explicit polling when the result is still queued or
running; MCP does not promise cross-session reactive wakeup. Cancellation is
also a request: poll status until the worker reports a terminal state.

Follow the [local delegation workflow](../../skills/local-delegate/SKILL.md)
to validate the repository, task spec, and test profile before submission and
to review the baseline, attempts, test output, diff summary, and exact patch
afterward. A passing test suite is not a substitute for reviewing the patch.
