# Codex CLI

This page covers manual registration of Hybrid SDLC as a Codex CLI MCP server.
It does not install a Codex plugin.

## Prerequisites

- Codex CLI is installed and the `codex` command resolves in the terminal where
  you will run the commands below.
- Hybrid SDLC is installed in the environment available to Codex, and its
  `hybrid-sdlc` console entrypoint resolves on `PATH` when Codex starts the
  server. The project defines this entrypoint as `hybrid_sdlc.cli:cli` and
  requires Python 3.11 through 3.13.
- If you installed Hybrid SDLC in a virtual environment, make sure its scripts
  directory is on `PATH` for the Codex process, or activate that environment
  before launching Codex. The registration command stores the executable name;
  it does not install the package or configure a Python environment.

## Register the MCP server

Run this in a terminal:

```sh
codex mcp add hybrid-sdlc -- hybrid-sdlc mcp
```

The arguments after `--` are the command Codex launches for the server. The
server uses standard input and output for MCP communication; keep those streams
available to Codex and do not add shell prompts or other output to them.

## Verify or remove the registration

List configured MCP servers and inspect this entry:

```sh
codex mcp list
codex mcp get hybrid-sdlc
```

`get` should show the `hybrid-sdlc mcp` command. To remove this registration
from Codex later, run:

```sh
codex mcp remove hybrid-sdlc
```

These commands manage the Codex user's MCP configuration. `remove` deletes the
named registration from that configuration.

## Smoke test

After restarting or refreshing Codex so it loads the new server, confirm that
the Hybrid SDLC MCP tools are available. For a non-job probe, call
`check_local_model` with the absolute path of a trusted repository configured
for Hybrid SDLC. This reads that repository's configuration and probes its
configured local inference endpoint and selected model. It does not submit a
task or modify repository files. Only use it when that endpoint is expected to
be available; the probe contacts the configured endpoint.

Task-running tools such as `run_spec_task_sync` and `submit_spec_job` can invoke
a local model and run repository-configured test commands. Use them only for a
trusted repository, committed task spec, and trusted test profile, following
the [local delegation skill](../../skills/local-delegate/SKILL.md). MCP tool
access is not a sandbox boundary for untrusted repositories or instructions.

## Manual registration and plugin installation

The commands on this page register an MCP server manually through Codex CLI.
They do not add a marketplace, install plugin files, or install the
`local-delegate` skill into Codex. The separate HSDLC-058 work item covers
packaging the Codex plugin and local marketplace entry.

For Codex's current MCP CLI command reference, see the
[Codex CLI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
