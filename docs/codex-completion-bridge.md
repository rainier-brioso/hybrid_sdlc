# Experimental Codex completion bridge

This CLI adapter waits locally for one existing Hybrid SDLC job, then uses
`codex queue` to send a compact completion message to an explicitly selected
existing Codex chat. It does not require model-driven status polling. The five
MCP tools remain unchanged; this bridge is a separate foreground CLI command.

## Setup and use

Install and authenticate a Codex CLI that supports `codex queue` with `--thread`
and `--message`. The bridge does not install Codex or change its configuration.
Use a trusted repository and the existing job ID returned by `hybrid-sdlc submit`
or `submit_spec_job`. Submission is a separate action.

Record the intended chat's actual UUID when arranging the handoff. Do not guess
the target from a title, the latest chat, or unrelated chat history.

```sh
hybrid-sdlc bridge codex ACTUAL_JOB_ID --repo-root . --thread-id ACTUAL_THREAD_UUID --allow-model-continuation --json
```

`--allow-model-continuation` authorizes sending the completion message to that
chat. The configured Codex model/provider may incur paid or cloud usage. The
selected chat retains its context, permissions, tools and integrations. The
bridge does not impose a new read-only policy on it. Review and implementation
continue under the chat's existing instructions and the user's authorization.

The bridge checks persisted job state locally until it becomes `completed`,
`failed`, or `cancelled`. It then invokes the trusted executable once with an
argument array and `shell=False`. Terminal job failure or cancellation is still
eligible for a completion message. Sending the message does not accept a patch,
apply changes, commit, or merge anything.

## What the result proves

A `queue_accepted` bridge result means the queue command exited successfully.
It does not prove that Codex started or completed a model turn, that the desktop
displayed a response, or that the underlying job succeeded. No host turn ID,
model reply, or token usage is inferred from command output. The completion is
an ordinary chat message containing labeled metadata, not structured tool output
or resumption of a suspended MCP call.

An installed synthetic probe on 2026-10-17 produced an automatic response in an
already-open idle desktop chat. The user observed it before any refresh or
history read, and the matching completed turn was verified afterwards. This
single observation is documented separately from automated tests and is not an
installed-package rehearsal of this integrated CLI command.

## Bounds and interruption

`--wait-timeout` bounds local waiting (default 1800 seconds, maximum 86400).
`--poll-interval` controls local checks (default 0.5 seconds, 0.01–5 allowed).
`--queue-timeout` bounds the queue command (default 30 seconds, maximum 3600).
`--codex-executable` selects the trusted executable, defaulting to `codex` on
PATH. It is an executable path, not a shell command. Unsupported CLI builds fail;
there is no automatic alternate transport.

Interrupting the bridge stops its owned queue process; it does not cancel the
Hybrid SDLC job or retract a message that Codex may already have accepted. Cancel
a job separately with `hybrid-sdlc cancel`, then verify its terminal state.

## Payload and replay protection

The message contains only allowlisted lifecycle metadata: job ID, terminal
status, run status, attempt count, last-test result and a categorical failure
reason. Source, patches, task prompts, paths, arbitrary error text and logs are
not forwarded. The target chat's existing history may already contain private
data available to its configured provider; the payload allowlist does not
redact that history. Queue command output is not persisted or shown as evidence.

A receipt is retained under `.hybrid_sdlc/bridges/codex/ACTUAL_JOB_ID.json`.
The bridge locks the job and writes a durable delivery intent binding the job
snapshot and target chat **before** invoking the queue command. An existing
receipt prevents another attempt, including delivery to a different chat.
Receipts from the earlier experimental transport also block resend.

If a timeout, interruption, nonzero exit or lost acknowledgement follows that
intent, delivery is uncertain and automatic retry is refused. Reconcile the
target chat manually. This is an at-most-one-attempt policy, not guaranteed
exactly-once delivery. Normal `hybrid-sdlc clean` retention preserves receipts;
deleting or rewriting local artifacts defeats the replay guard. Advisory locks
coordinate bridge calls within the same repository, not every host client or
filesystem actor. Different repositories can queue to the same chat concurrently;
that cross-repository behavior has not been validated.

## Investigation and evidence

The earlier App Server transport was removed when queue delivery became the
selected workflow. Its results remain documented as historical experiments:

- [New owned-thread handoff](evidence/codex-bridge-rehearsal-20261017.md): one
  synthetic tool-output turn completed and replay was refused.
- [Saved-thread continuation](evidence/codex-saved-thread-rehearsal-20261017.md):
  closing the original owner allowed a separate App Server to retain prior
  context in the same thread.
- [Open-desktop resume rejection](evidence/codex-open-desktop-rehearsal-20261017.md):
  the separate App Server was rejected because the desktop held an active writer.
- [Queue desktop probe](evidence/codex-queue-desktop-rehearsal-20261017.md): one
  ordinary queued message produced the observed automatic desktop response.

Commands and source hashes in historical records describe the tested revisions,
not the current CLI. No App Server RPC client, new-thread mode, saved-thread
resume mode or exclusive-owner option remains in this bridge.

Automated checks use synthetic persisted jobs and a fake queue executable. They
do not invoke installed Codex inference, Strata, Docker or external APIs. See
the [verification map](verification-map.md) for current checks and evidence gaps.
No token-saving measurement or unavailable usage is invented.

Integration checkpoint (2026-10-17, Windows/Python 3.12): 1,002 tests passed,
12 environment or opt-in skips, 82.91% coverage. Formatting, lint, strict source
typing, documentation inventory and independent review passed. The queue-focused
suite passed 16 tests with one symlink privilege skip. Skipped behavior remains
unverified; this checkpoint involved no new real Codex message or model call.

Command reference: [official Codex CLI documentation](https://learn.chatgpt.com/docs/cli/reference).
