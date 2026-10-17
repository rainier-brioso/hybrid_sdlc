# Codex bridge rehearsal: initial block and successful repeat

> Historical App Server investigation. HSDLC-083 replaces its runtime transport
> with the queue-only bridge. Commands and hashes describe the tested revision;
> see [current guidance](../codex-completion-bridge.md).

Observed on 2026-10-17 Europe/Madrid (2026-10-16 22:45 UTC).

## Build and scope

- Hybrid SDLC source checkout: `codex/codex-completion-bridge`, uncommitted
  implementation based on `48efbd29691d62c34d2bafeb41290536b055a032`.
- Development-environment CLI invoked as `python -m hybrid_sdlc bridge codex`;
  this was not a wheel installation or global executable rehearsal.
- Installed host: `codex-cli 0.162.0-alpha.17.2`.
- User configuration selected `gpt-6-luna`; effective model/provider and usage
  were not observed because no model turn was requested.
- Explicitly authorized synthetic job only. Disposable Git repository, no
  application source, Strata request, service restart or host-setting edits.
- Limits: local wait 20 seconds, polling interval 0.2 seconds, host turn
  timeout 120 seconds; existing 30-second protocol startup bound unchanged.

## Initial observation: blocked before model generation

The synthetic persisted job transitioned queued -> running -> completed while
the foreground CLI waited locally. The command ended after approximately
6.17 seconds with exit code 1:

```json
{
  "code": "CODEX_BRIDGE_FAILED",
  "message": "Codex thread has configured MCP servers or an incomplete server list",
  "details": {}
}
```

The bridge's `thread/start` empty `mcp_servers` override did not produce the
required empty, complete MCP inventory on this installed host. This check
failed **before** delivery intent or `turn/start`; no delivery receipt was
created. No model generation or completion was observed. Therefore replay
protection, matching turn completion and model-visible tool output were not
validated live. They remain covered only by synthetic protocol fixtures.

Local raw result and rehearsal harness are retained outside the repository;
machine-specific paths and host configuration are intentionally omitted here.

## Follow-up and successful repeat

Investigate host configuration merging and supported per-thread MCP disabling;
retain the post-configuration inventory check. Do not weaken it to force a
successful run, change the user's global MCP configuration, or treat a fake
host pass as live validation. Add inherited-MCP regression coverage before
repeating the authorized one-turn rehearsal.

The follow-up implemented explicit per-thread disabling of inherited MCP
servers plus apps/plugins, preserving the configured model/provider. The host
can list disabled servers, so the inventory guard now requires explicit
`runtimeStatus: disabled`, empty object-map tools, empty resource/template
arrays, and a complete inventory. Active, unknown or malformed entries still
fail before intent. No global configuration was modified. The documented
`--ignore-user-config` flag was also checked but is not supported by this
installed build; the implementation does not depend on it.

The successful repeat used the same source branch/base and installed Codex
version. Bridge source SHA-256 at execution:
`70e911069b76bea45669b1c3156f213c4de909db7c348305e4a0c15bed77f132`.
User configuration selected `gpt-6-luna`; the owned thread's recorded provider
was `openai`. Effective model identity was not captured from the generation
response and is not independently asserted. Limits remained 20-second local
wait, 0.2-second local polling and 120-second host-turn timeout.

On 2026-10-17 at approximately 00:55 Europe/Madrid, the synthetic job again
transitioned queued -> running -> completed. The actual development CLI returned
exit code 0 after 7.407 seconds, including the four-second synthetic wait:

```json
{
  "job_id": "job_20261016T225500428888Z_f9a4a7d4",
  "job_status": "completed",
  "bridge_state": "completed",
  "thread_id": "01a146ed-14cd-7c40-b462-41441f547222",
  "turn_id": "01a146ed-1558-7b53-bbb8-9d98abc9f041",
  "usage": null
}
```

The durable receipt recorded matching thread/turn IDs, state `completed` and
completion status `completed`. Repeating the identical command returned exit
code 1 with `A bridge receipt already exists; delivery will not be retried`.
No receipt was deleted to obtain that repeat.

A separate read-only `thread/read` of **only this rehearsal-owned thread**
confirmed one matching completed turn containing exactly these item types:

```json
["functionCallOutput", "agentMessage"]
```

There was exactly one `hybrid_sdlc_job_completion` standalone tool output, zero
user messages and one agent message. Its decoded payload matched the synthetic
job ID and completed state; its only keys were `attempt_count`, `failure_reason`,
`job_id`, `run_status`, `status`, and `tests_passed`. This history check did not
start another model turn. Raw host text/configuration was not copied into this
evidence record.

This establishes one successful metadata-only owned-thread handoff and one
receipt-guarded replay refusal on the selected environment. Usage was unavailable;
there is no measured token-saving claim. No application code, model delegation,
Strata request, original-chat wakeup, earlier-planner resumption, full-spec
orchestration or clean installed-package journey was tested. HSDLC-081's
synthetic rehearsal is complete, not those broader capabilities.

## Validation after the fix

- Full offline suite: 1,028 passed, 12 environment/opt-in skips; 82.83% total
  coverage on Windows/Python 3.12.14 (263.36 seconds).
- Focused bridge/CLI suite, including explicit receipt privacy assertions:
  42 passed, one directory-symlink privilege skip.
- Ruff lint/format and mypy (28 source files) passed; verification-map contracts
  passed (12 tests), and offline links/examples checks passed.
- Independent review found no material issue. Skipped Windows breakaway,
  symlink, POSIX-permission and opt-in distribution behavior remains unverified.
