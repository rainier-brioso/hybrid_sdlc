# Codex saved-thread continuation rehearsal

> Historical App Server investigation. HSDLC-083 replaces its runtime transport
> with the queue-only bridge. Commands and hashes describe the tested revision;
> see [current guidance](../codex-completion-bridge.md).

Observed 2026-10-17 at approximately 01:15 Europe/Madrid. This is one synthetic
saved-thread continuation, not proof that an open desktop client was awakened.

## Build, authorization and boundaries

- Source CLI: `codex/codex-completion-bridge`, uncommitted implementation based
  on `48efbd29691d62c34d2bafeb41290536b055a032`, run as `python -m hybrid_sdlc`.
- Bridge source SHA-256:
  `052daae8900ab7721f5aebebc3e88a2698d180be27229e691703b2e4003fb851`.
- Installed Codex: `codex-cli 0.162.0-alpha.17.2`.
- Seed-thread effective model/provider: `gpt-6-luna` / `openai`. The bridge did
  not request a model/provider override; the recorded continued thread provider
  was `openai`. Effective continuation model identity was not separately captured.
- Explicitly authorized disposable planner-context test only. Two model turns:
  initial context setup and the subsequent bridge continuation. No application
  source/member data, Strata request, package install or global setting edit.
- Both generation turns bounded at 120 seconds. Bridge local wait 20 seconds,
  local polling 0.2 seconds, protocol startup 30 seconds. Usage unavailable.

## Procedure and observations

1. A disposable App Server thread was created with read-only permissions,
   disabled MCP/apps/plugins and instructions for a synthetic memory test.
   Its initial user turn supplied a unique checkpoint. That turn completed.
2. The initial App Server process was closed before the bridge ran, giving the
   bridge exclusive use of this synthetic saved conversation. This did not
   involve any open personal or project chat.
3. A separate disposable Git repository held a synthetic job transitioning
   queued -> running -> completed after four seconds. The actual CLI watched
   that job with `--thread-id`, `--exclusive-thread-owner` and
   `--allow-model-continuation`.
4. The bridge used `thread/resume`, not `thread/start` or `thread/fork`, and sent
   only the compact completion result as standalone tool output. Its CLI returned
   exit code 0 after 7.734 seconds, including the synthetic local wait:

```json
{
  "job_id": "job_20261016T231549863644Z_8ed68dbd",
  "job_status": "completed",
  "bridge_state": "completed",
  "thread_id": "01a14700-075b-7050-abdd-ff8c91310686",
  "turn_id": "01a14700-25e3-77a0-a7cb-eef990a410ac",
  "usage": null
}
```

The seed thread ID was exactly the returned thread ID. Its seed turn
`01a14700-07c7-7f61-b28b-8b98f706f475` remained in history. The durable receipt
recorded `thread_mode: resume`, the exact requested thread ID, and a matching
completed continuation turn. Running the identical CLI command again returned
exit code 1 with `A bridge receipt already exists; delivery will not be retried`.
The receipt was not deleted or altered.

## Retained-context verification

A read-only history check targeted only this synthetic thread, without starting
another model turn. It confirmed:

- The original seed turn was retained and completed.
- The new completed turn contained `functionCallOutput`, `reasoning`, and
  `agentMessage` items, with zero new user messages.
- Exactly one `hybrid_sdlc_job_completion` output matched the synthetic job.
- The checkpoint was **absent from the completion payload**, but the continued
  agent response began with that earlier checkpoint.
- The original working directory and thread identity were retained.

Thus the continued model used earlier conversation context, rather than merely
receiving a fresh copy of it in a new thread. Raw conversation text, reasoning,
host configuration and machine-specific paths are not included in this record.
The external local harness retains a bounded boolean/metadata evidence report.

## What this does not establish

The bridge owns a separate App Server connection. Its same-repository thread
lock and operator acknowledgment do not provide an atomic lease against other
clients or repositories. Reported active/unknown/error states are rejected, but
cross-client check/start races cannot be ruled out by the tested interface.
This rehearsal does not establish open-desktop UI notification, active-turn
handoff, automatic planner thread discovery, unrestricted planner/tool
orchestration, a real local-model task, clean package installation, repeated
reliability, or measured token savings. The continuation remained read-only
with integrations disabled; it did not implement a next coding task.

## Validation checkpoint

Full offline suite on Windows/Python 3.12.14: **1,045 passed, 12 skipped**, with
**82.86%** total coverage (259.74 seconds). Focused backend tests passed (45,
one symlink privilege skip), as did CLI tests (14), verification-map contracts
(12), lint, formatting, mypy (28 source files) and offline documentation checks.
Independent review found no material issue after the status-schema correction.
Skipped Windows breakaway/symlink, POSIX-permission and opt-in distribution
checks remain unverified, not inferred from this successful rehearsal.
