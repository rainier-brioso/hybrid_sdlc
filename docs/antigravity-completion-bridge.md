# Experimental Antigravity App completion bridge

The CLI waits locally for one persisted Hybrid SDLC job, then sends minimized
terminal metadata to one explicitly authorized existing App chat. It does not
submit work, accept a patch, create a conversation or resume a suspended MCP
call. The five MCP tools remain unchanged. Codex keeps its separate queue bridge.

## Supported boundary

Initial transport validation is deliberately limited to **Windows Antigravity
App 2.24.0** and its observed host-advertised `agentapi.bat` interface. The
adapter verifies executable version metadata and the wrapper before invoking
its executable directly with an argument array, never through `cmd` or a shell.
Unknown versions, platforms, wrapper formats and unsafe paths fail closed.
An upgrade needs fresh compatibility evidence; do not falsify the version check
to bypass it. Antigravity IDE and CLI are distinct, unverified surfaces.

The [2026-10-19 synthetic probe](evidence/antigravity-notification-investigation-20261019.md)
produced command success and a user-confirmed automatic ACK in the existing idle
App chat. It used a disposable helper, not this shipped adapter or a real worker
job. Repeated reliability, busy/closed-target behavior, host shutdown/restart,
cross-repository concurrency and installed real-job delivery remain unverified.

## Direct bounded delivery

First obtain the actual chat UUID from a user-confirmed supported host surface.
Do not infer a target from a title, recency, cached history or a private database.
Obtain permission for model continuation in that exact chat: its existing
provider, context, tools and permissions still apply, potentially incurring usage.

```powershell
hybrid-sdlc bridge antigravity ACTUAL_JOB_ID --repo-root C:\work\project --conversation-id ACTUAL_CHAT_UUID --app-executable 'C:\trusted\Antigravity\Antigravity.exe' --allow-model-continuation --json
```

Use the installed executable path observed on the selected machine; the example
path is a placeholder, not an installation default. This is a foreground command.
Running it directly requires the same validated host command interface available
to the user; the preferred host-owned lifetime is the per-job sidecar below.
Safe async worker submission remains a separate prerequisite. In particular,
the App sidecar does not bypass Windows Job Object restrictions on the worker.

Waiting defaults to 1800 seconds (maximum 86400), delivery to 30 seconds (maximum
3600), local checks to 0.5 seconds (0.01–5 permitted). Set `--wait-timeout` for
queueing, baseline checks, attempts/retries and separately timed test profiles,
plus a finite margin. `--send-timeout` bounds the owned command/process tree.
These are not host response deadlines or model token settings.

`send_accepted` means command success only. Response completion and usage remain
unknown; no chat text, transcript or turn ID is collected. Failure/cancellation
of the underlying job can still generate terminal metadata. Inspect the saved
full result and exact patch before reviewing or integrating anything.

## Explicit per-job sidecar setup

Plan the target, authority and lifetime before async submission. Once submission
returns a job ID, preview and then explicitly prepare its sidecar:

```powershell
hybrid-sdlc bridge antigravity-sidecar ACTUAL_JOB_ID --repo-root C:\work\project --conversation-id ACTUAL_CHAT_UUID --app-executable 'C:\trusted\Antigravity\Antigravity.exe' --allow-model-continuation --dry-run --json
hybrid-sdlc bridge antigravity-sidecar ACTUAL_JOB_ID --repo-root C:\work\project --conversation-id ACTUAL_CHAT_UUID --app-executable 'C:\trusted\Antigravity\Antigravity.exe' --allow-model-continuation --json
```

The installed CLI is resolved to an absolute path. Its one-job manifest contains
the exact target and bounds and disables automatic restart. Global configuration
lives in the user's `.gemini/config/`; no repository settings, MCP registration
or other host surface is rewritten. [Official sidecar documentation](https://antigravity.google/docs/sidecars/)
describes host-managed discovery and explicit enablement.

An existing `config.json` is **never overwritten**. The CLI writes a private
sibling proposal; compare it with the latest configuration and manually merge
only this sidecar's entry. Do not replace the whole file with a stale proposal.
Proposals may contain copied private configuration: do not commit or share them.
Files use user-only modes on POSIX; Windows inherits directory ACLs, so keep the
user configuration directory restricted to the intended account.
New configuration uses no-clobber publication. A prepared manifest is not proof
that the App launched it or that a notification arrived. Do not leave a prepared
manifest indefinitely and later enable it without reconciling job/target state.
No automatic restart, authentication change or permission widening is performed.

## Removal, interruption and replay

Preview removal with the same job/target/setup arguments plus `--remove --dry-run`;
then remove with `--remove`. Retain the sidecar ID and original bounds in the
planning checkpoint. Removal validates ownership and refuses altered manifests.
It removes only the owned manifest and proposes removing its global config entry;
merge that proposal against the current file, preserving unrelated entries.
An interrupted setup/removal that leaves only an exact matching ownership record
can be recovered by explicit removal. Setup refuses to adopt that partial state,
and removal still refuses unknown, mismatched or modified ownership data.
Removing a manifest does not kill a running bridge, cancel its job, retract a
message or erase receipts. Inspect retained status separately; an already running
bridge may still deliver. If stopping that bridge is needed, terminate its owned
host process through supported host controls and treat an in-progress send as
uncertain. No automatic resend follows interruption.
There is no portable bridge-PID verification or CLI stop command for the App
adapter yet. Do not guess a PID or kill all host processes. If the App does not
expose a supported control for that specific sidecar process, report that stopping
it cannot be verified and keep delivery uncertain; removing its files is not a
substitute for stopping it. The finite bridge timeout still bounds local waiting.

Receipts under `.hybrid_sdlc/bridges/antigravity/` persist delivery intent before
the host call. Existing, malformed or uncertain receipts prevent another attempt
or retargeting. Normal artifact cleanup preserves bridge receipts; deleting them
defeats replay protection. Timeouts, nonzero exits, crashes and lost acknowledgments
must be reconciled, not retried. Receipt/target locks are per repository and
per adapter: they do not deduplicate Codex versus Antigravity or coordinate every
other client. Do not route one job to both hosts expecting a global exactly-once
guarantee. No exactly-once delivery is claimed.

The completion payload contains only allowlisted lifecycle metadata, never
source, patches, task prompts, paths, arbitrary logs or error messages. The
selected chat may already hold private context; this allowlist does not redact
its history. The trusted host command inherits its required environment, which
is not persisted or forwarded in the message. MCP and advisory file locks are
not an OS sandbox. If notification cannot be arranged, offer synchronous work
or explicitly agreed manual status inspection, not silent model polling.
