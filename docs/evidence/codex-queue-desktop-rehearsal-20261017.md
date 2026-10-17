# Codex queue: automatic response in an already-open desktop chat

Observed 2026-10-17 at approximately 19:13 Europe/Madrid.

Implementation follow-up: HSDLC-083 adopts this command as the sole bridge
transport and removes the unused App Server transport. This record remains a
direct CLI probe, not a live rehearsal of that later integrated implementation.

## Scope and setup

The user authorized one `codex queue` test and confirmed the existing disposable
chat **MCP completion wakeup smoke test** was visible and idle. Target thread:
`01a14601-3d14-7e63-b6a2-f8a984cf96ae`. No new chat, application task, Strata
request, repository implementation or global configuration change was involved.

Installed host: `codex-cli 0.162.0-alpha.17.2`. This was a direct installed-CLI
diagnostic, **not an implemented Hybrid SDLC queue transport**. The command used
the exact target UUID, a single synthetic message and default host routing:

```text
codex queue --thread TARGET_UUID --message SYNTHETIC_ACK_REQUEST
```

The message labeled itself as a user-authorized synthetic wakeup test and asked
only for `ACK HSDLC-QUEUE-4bc1729c43`, with no tools, file inspection, service
calls, changes or messages to other chats. It was ordinary text, not structured
tool output. No model/provider override or remote endpoint was supplied. The
command had a 45-second timeout and an external intent record refusing automatic
resend. Model usage was unavailable; host generation limits were not set by this
queue invocation.

## Command acceptance

The single invocation returned exit code 0 on 2026-10-17 at 17:12:51 UTC:

```text
Queued message 01a14ada-1dec-75c0-9ad3-3bdafd8e9cb1 for thread 01a14601-3d14-7e63-b6a2-f8a984cf96ae.
```

Acceptance alone was not counted as wakeup. No desktop navigation, refresh or
thread-history read occurred after sending and before the user's observation.

## Desktop observation, then history verification

The user explicitly reported **The ACK response appeared automatically**, without
refreshing or reopening the visible test chat. Only after that report did a
read-only desktop thread-history check run.

The check confirmed the same target thread had one new completed turn:
`01a14ada-44b7-7e31-a9cd-026e82a83501`. Its recorded duration was 2,318 ms;
recorded start/completion times were approximately 17:13:00/17:13:03 UTC.
The turn contained exactly:

1. A `userMessage` with the synthetic queued ACK request and unique marker.
2. An `agentMessage`, final answer: `ACK HSDLC-QUEUE-4bc1729c43`.

There were no tool-call items in that turn. The thread was idle after completion.
The raw local command report remains outside the repository; unrelated history,
host configuration and machine-specific paths are not included here.

## What this establishes

One explicitly targeted `codex queue` message caused a new model turn and an
automatic visible reply in the already-open idle desktop chat. The human did
not send the test message in that chat, press Send there, or refresh it. No
model-issued status polling was used to trigger the response.

This is a supported CLI queued-message path, not a second App Server taking
writer ownership, an MCP notification, structured `functionCallOutput` delivery,
or resumption of a suspended active turn. It does not invalidate the separate
App Server's earlier active-writer rejection.

This single observation does not establish repeated reliability, active/busy
thread behavior, exactly-once delivery, real-job orchestration, token savings,
or current Hybrid SDLC queue integration. A production queue adapter must be
explicitly selected and preserve target binding, authorization, bounded waits,
payload minimization and replay protection. Queue acceptance must remain
distinct from observed model completion; ambiguous delivery must not be retried
or silently substituted for structured tool-output delivery.
