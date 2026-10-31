# Antigravity notification investigation — 2026-10-19

Status: one installed App send command succeeded and the user confirmed the
matching automatic response in the open idle chat. HSDLC-087 investigation is
complete; production integration remains HSDLC-088. Baseline: main `a8cd739`.
Initial research was read-only. The subsequently authorized App probe temporarily
configured one sidecar and sent one synthetic completion. No Strata operation,
package installation or production adapter implementation was performed.

## Supported-interface research

The current [official sidecar documentation](https://antigravity.google/docs/sidecars/)
describes an Antigravity App integration, not a guarantee for the historically
tested IDE. It provides:

- A host-managed background process with global or plugin configuration.
- Explicit enablement and configurable restart policy (default `always`).
- A host-supplied `agentapi` executable on the sidecar's PATH.
- `agentapi send-message <conversation_id> <prompt>` for an existing chat.

This is a candidate transport, not evidence of automatic response, delivery
acknowledgment semantics, idle-chat wakeup or resumption of a suspended turn.
The page does not establish safe conversation-ID discovery, duplicate
suppression, busy/closed-target behavior or a completion acknowledgment contract.
Those remain probe questions. Do not substitute `new-conversation` when an
existing target cannot be addressed.

The [CLI resume documentation](https://antigravity.google/docs/cli/commands/resume)
describes session selection and import of App history into the CLI. Import
clones context; it is not proof of notification in the original open App chat.
The [headless documentation](https://antigravity.google/docs/cli/headless/)
provides explicit conversation selection with `--conversation`, but runs a new
CLI process. That is a separate continuation surface, not desktop wakeup proof.
Never select the most recent chat implicitly for completion delivery.

[Lifecycle hooks](https://antigravity.google/docs/hooks/) can expose a
`conversationId` at session start and inject context around model invocations.
A deliberately enabled, metadata-only hook might supply a target identifier;
this needs version verification and explicit configuration approval. Hook
injection while an invocation is already happening does not establish an
external event waking an idle chat. Do not collect transcripts or infer IDs
from private host databases.

## Local observations

Read-only Windows inventory found registered Antigravity and Antigravity IDE
launch entries. No `agy`, `antigravity`, or `google-antigravity` command resolved
on the inspected PATH. Registered launch names do not establish version or
capability. The package inventory query was access-denied; no bypass was
attempted. This inventory did not establish the installed build.

The user subsequently supplied Help/About information on 2026-10-19:

- Antigravity IDE: `2.5.5` (user setup).
- VSCode OSS base: `1.107.0` (not the Antigravity product version).
- Commit: `ecfbad74d93962fc8ca485d93ab9b4f3d4cb6cf8`.
- Build date: `2026-08-13T08:37:22.547Z`.
- Language Server CL: `963923152`.
- Platform: Windows x64, `10.0.26200`; Electron `39.2.3`.

This is user-reported build evidence, not a binary inspection. Earlier records
labeled `1.107.0` as an IDE version; retain those dated observations but do not
use that underlying editor version as the Antigravity product version. The
sidecar page explicitly labels its surface Antigravity App. It does not
establish that this VSCode-based IDE build supports the same integration.

Absence of a shell command is not evidence that host-injected `agentapi` is
unavailable inside a supported sidecar. Conversely, current web documentation
does not prove this machine's installed host implements it.

## App follow-up and authorization

The user subsequently installed Antigravity App, confirmed sidecars are present,
and explicitly approved temporary sidecar configuration and one synthetic
completion in the disposable **Hybrid SDLC notification test** chat. This does
not authorize another target, repeated delivery or a production adapter.

Read-only executable metadata reports Antigravity App `2.24.0` (product version
`2.24.0.0`), separate from the earlier IDE `2.5.5` report. The documented global
configuration and sidecar directory exist. A names-only configuration inspection
found no configured sidecar entries; no settings were changed. The UI inspection
returned a capture inconsistent with its selected App window, so no UI inputs
were issued. No private conversation store was inspected.

The user then supplied the exact authorized conversation ID:
`6bddf934-8c2a-4310-a8cc-b3deed89174c`. It was not inferred from the title.

## Installed one-shot result

Synthetic job: `HSDLC-AG-PROBE-20261019-a1`. One temporary sidecar used
`restart_policy: never`. Its first preflight refused delivery because the host
advertised a BAT wrapper, not an EXE; that receipt records
`delivery_attempted: false` and was retained unchanged. A metadata-only helper
located the advertised wrapper. Its inspected contents invoke the installed
`language_server.exe` with an `agentapi` prefix. No private endpoint or host
conversation database was accessed.

The revised resolver accepted only that exact wrapper path and contents with
the expected executable present. It invoked the same executable/prefix directly
using Hybrid SDLC's bounded, shell-free process-tree runner, preserving the
host environment, with a 30-second timeout and capped discarded command output.
The synthetic message contained the job ID and categorical state plus an ACK
instruction, not source, paths, patches, arbitrary errors or private data.

A separate actual-send receipt persisted intent before invocation:

- `delivery_attempted: true`.
- `state: command_succeeded`; command exit code `0`.
- Recorded start/finish interval: approximately `0.167` seconds.
- `automatic_response_observed: null` in the original command receipt. The
  receipt was not rewritten to imply an instrumented host observation.

The user subsequently answered **"Yes—the matching ACK appeared automatically"**
to the question naming this exact job and asking for observation without refresh
or reopening. This is user-reported visible automatic-response evidence, separate
from the command's exit code. No transcript read, host turn ID or token usage was
collected, and no additional message was sent to verify it.

Running the probe again refused another attempt because that receipt existed;
it did not issue a second host command. Eight mocked offline checks passed,
covering the fixed resolver, unknown command rejection, intent before send,
existing/malformed receipt refusal, success, nonzero exit and timeout without
resend. Independent pre-live review identified an overly broad PATH EXE branch;
it was removed and the final review found no material blocker.

The temporary sidecar entry and manifest were removed after command completion.
All five pre-existing plugin entries remained; probe receipts, helper files and
host-generated probe logs were retained. No unrelated settings were replaced.
Removing the integration does not retract the message.

Together, command evidence and the user's observation support one automatic
follow-up response in the selected open idle App chat. They do not establish
repeated reliability, independent transcript verification, IDE compatibility,
actual Hybrid SDLC job completion delivery or resumption of a suspended MCP turn.

## Probe protocol and remaining limits

Use the now-approved App probe, not assumed IDE compatibility. Establish the
exact authorized target before enabling the temporary integration. The approval
includes potential model/provider usage; the message inherits that chat's
permissions and must not authorize editing, tool use or integration.

The test should:

1. Capture the exact authorized chat ID through a supported host surface. If
   unavailable, stop; do not guess, scrape unrelated history or create a fallback.
2. Use a one-shot, finite-lifetime probe under a trusted executable path, with
   restart disabled and a durable delivery intent written before invocation.
   Preserve uncertainty on timeout, interruption, nonzero exit or lost receipt;
   never resend automatically after an attempted delivery.
3. Send a unique synthetic job ID and categorical terminal state only, asking
   for a matching ACK without tools. Exclude repository paths, source, patches,
   prompts, arbitrary errors, secrets and user data.
4. Record command exit/acceptance separately from message receipt, model-visible
   input and a matching automatic response. Have the user observe the open chat
   without refresh or reopening before inspecting supported history. Do not
   equate a new response with resuming an earlier waiting turn.
5. Verify replay refusal locally without another send. Remove only the approved
   temporary integration afterward, preserving evidence and receipt; removal
   cannot retract an accepted message or cancel a separate Hybrid SDLC job.

Busy/closed targets, host shutdown, process survival, cancellation races,
restart recovery and cross-repository concurrency need separate authorized
tests. Until their behavior is known, do not retry, retarget, bypass ownership,
enable Remote Control or broaden permissions to force success.

## Product decision

Prefer testing the documented sidecar `agentapi` route rather than recreating
the removed App Server experiment or treating MCP logging as chat delivery.
No `bridge antigravity` command is implemented by this report. The observed
outcome warrants a separately scoped adapter proposal (HSDLC-088): an explicitly
configured App sidecar would run the installed Hybrid SDLC CLI, wait locally for
one persisted job, then deliver minimized metadata to an authorized exact chat.
Host-side lifetime management should not mean repeated delivery; a durable intent
must precede execution, and restart/replay must never resend an uncertain attempt.
Provide explicit setup preview/apply/removal preserving unrelated settings,
version-aware executable validation without shell interpolation, bounded process
cleanup, explicit target binding and continuation authority. The disposable
probe's machine-specific paths are evidence, not portable product defaults.
Test the production adapter offline and require a separately approved installed
real-job rehearsal before promoting it to a supported automatic workflow.
Keep the existing Codex bridge unchanged.
For Antigravity today, use synchronous execution or explicitly agreed manual
status inspection; neither is automatic notification.
