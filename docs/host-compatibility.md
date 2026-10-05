# Host compatibility and evidence

This table records what was observed for the host integrations as of 2026-10-05.
Registration, tool discovery, an MCP call, and a complete task run are separate
levels of evidence. A configured server alone does not establish that the host
can run or resume a task.

| Host | Version recorded | Registration and discovery evidence | Synchronous task | Asynchronous task and progress | Wakeup after completion |
| --- | --- | --- | --- | --- | --- |
| Codex CLI | `0.158.0-alpha.2.1` for isolated plugin check; version for the separate historical manual smoke not recorded | Isolated `CODEX_HOME` successfully added the local marketplace, installed/listed the plugin, discovered its skill and MCP configuration, and removed both. A separate [installed MCP SDK stdio smoke](../benchmarks/results/strata/installed-mcp-smoke-20261004.json) completed one synchronous fixture run; it proves the server path, not a Codex-session connection. | **Historical user-reported manual Codex MCP smoke:** trusted `SMOKE-001` and its tests passed. The host version was not captured; this is not pinned-version E2E evidence. | API provides `submit_spec_job` and bounded `get_job_status` polling. Codex-host async survival and host progress presentation are unverified. | **Unverified.** No cross-session resume claim. |
| Claude Code CLI | `2.1.268` | Project `.mcp.json` was inspected with CLI commands in an isolated setup; server remained pending interactive approval. No approved live session/tool call is recorded. | **Unverified in host.** The project server requires interactive approval first. | Async submission and polling are available in the server API; host execution, progress presentation, and reconnect behavior have not been exercised. | **Unverified.** No automatic resume claim. |
| Google Antigravity IDE | `1.107.0` for discovery; version for the 2026-10-05 task calls not supplied | Workspace skill and user-profile MCP registration were observed; all five tools appeared. Workspace `.agents/mcp_config.json` was not discovered. User-reported in-IDE probe and task calls reached the server. | **Successful synchronous smoke.** [Retry after restart](../benchmarks/results/strata/antigravity-mcp-smoke-20261005.json) completed in 52.4 seconds; patch and both tests independently verified. The [earlier timeout](../benchmarks/results/strata/antigravity-mcp-timeout-20261005.json) remains recorded separately. | Async submission and polling exist in the API; host async execution, survival, and displayed progress need separate tests. | **Experimental / unverified.** No automatic resume claim. |

## Shared MCP API surface

The server exposes `check_local_model`, `run_spec_task_sync`,
`submit_spec_job`, `get_job_status`, and `cancel_spec_job`. Synchronous calls
can report start and finish progress through MCP. Asynchronous work is
persisted and clients can poll status; this API behavior does not prove that a
host process survives disconnection or wakes an agent turn when work finishes.
Use the host guide for setup details: [Codex CLI](hosts/codex.md),
[Claude Code](hosts/claude-code.md), and
[Antigravity](hosts/antigravity.md).

## Windows background-job caveat

The detached-worker reconnect E2E test is skipped when its own runner is inside
a restrictive Windows Job Object: the child cannot safely break away and
would remain owned by that Job Object. The same restriction was observed on
the available Windows CI runner. Therefore Windows detached-worker survival
and reconnect are **not verified** by that test. Use synchronous execution on
such a host. Polling can inspect a persisted job only after submission has
succeeded; it does not enable background submission when Job Object
breakaway is denied. Run the detached-worker E2E on a host that permits the
required breakaway before claiming Windows survival/reconnect support.
