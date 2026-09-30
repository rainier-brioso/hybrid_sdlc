# Codex CLI

This page covers two ways to connect Hybrid SDLC to Codex CLI: installing the
repository's local plugin, or registering its MCP server manually.

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

## Install the repository plugin

From the repository root, add its local marketplace and install the plugin:

```sh
codex plugin marketplace add .
codex plugin add hybrid-sdlc@hybrid-sdlc-local
codex plugin list --marketplace hybrid-sdlc-local
```

The marketplace points to the self-contained package under
`plugins/hybrid-sdlc`, which installs the `local-delegate` skill and configures
the stdio MCP server using `hybrid-sdlc mcp`. The `hybrid-sdlc` executable must
be available on `PATH` when Codex starts the server. Start a new Codex
conversation to load the installed skill and MCP tools. Confirm the skill is
available and that the `hybrid-sdlc` MCP server exposes its tools before
submitting a task.

To uninstall the plugin and remove the local marketplace registration:

```sh
codex plugin remove hybrid-sdlc@hybrid-sdlc-local
codex plugin marketplace remove hybrid-sdlc-local
```

## Register the MCP server manually

Use manual registration when you want the MCP server without installing the
plugin and skill. The plugin already configures this same server, so avoid
registering it both ways in the same Codex profile.

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

For Codex's current MCP CLI command reference, see the
[Codex CLI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
