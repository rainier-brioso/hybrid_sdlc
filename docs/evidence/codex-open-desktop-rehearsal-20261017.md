# Open-desktop continuation probe: blocked by the active writer

> Historical App Server investigation. HSDLC-083 replaces its runtime transport
> with the queue-only bridge. Commands and hashes describe the tested revision;
> see [current guidance](../codex-completion-bridge.md).

Observed 2026-10-17 at approximately 01:22 Europe/Madrid.

## Scope and setup

The user explicitly authorized a synthetic test against an already-open desktop
conversation and confirmed it was visible and idle before triggering the probe.
The existing disposable chat was **MCP completion wakeup smoke test**, thread
`01a14601-3d14-7e63-b6a2-f8a984cf96ae`. No application chat or repository was used.
The chat was opened through the supported desktop navigation tool. No desktop
thread-read, refresh or further navigation was performed after the trigger.

Installed host: `codex-cli 0.162.0-alpha.17.2`. The development bridge source
SHA-256 was `052daae8900ab7721f5aebebc3e88a2698d180be27229e691703b2e4003fb851`,
on the uncommitted `codex/codex-completion-bridge` branch based on
`48efbd29691d62c34d2bafeb41290536b055a032`.

This was a separate diagnostic, **not** the production CLI: that CLI correctly
requires exclusive ownership and other clients closed. The probe did not
falsely supply `--exclusive-thread-owner`, weaken production checks, modify
global settings, clear locks, attach to hidden control sockets or edit history.
Its intended model turn was bounded at 120 seconds; protocol preflight at 30.

## Result

A disposable persisted job reached completed state. A separate App Server
connection could read the selected thread's metadata and reported its local
status as `notLoaded`. It read effective configuration and attempted
`thread/resume` with `excludeTurns: true` plus the existing narrow integration
disable overrides. The host rejected resume with JSON-RPC code **-32600**:

```text
thread <thread-id> already has an active writer
```

A no-generation diagnostic isolated the same rejection to `thread/resume`.
The initial local `notLoaded` status therefore did **not** establish that another
App Server lacked ownership. An idle visible chat can still have an active
writer owned by Desktop. The host refused the second writer before delivery.

No delivery intent or `turn/start` was issued, no model generation/completion
was observed, and no completion response was expected in the desktop UI.
The diagnostic's local guard retained the failed-preflight record; no ambiguous
delivery was automatically replayed. No raw conversation or host configuration
is included in this evidence. Machine-specific diagnostics remain outside the
repository.

## Interpretation and next boundary

This test establishes that the current separately launched App Server route
cannot continue this already-open desktop thread on the selected build.
It does not invalidate the earlier successful **closed saved-thread** test,
and it does not show that every supported host-owned continuation route is
unavailable. Desktop UI refresh behavior after a successful external turn was
not reached and remains unverified.

Next research must establish a supported connection or dispatch operation on
the **owning** desktop App Server. Any host-message or CLI-queue alternative must
be labeled separately from tool-output delivery and validated in this same
open/idle scenario. Do not remove writer locks or convert an ownership rejection
into a new user-message fallback merely to claim bridge success.

Subsequent, separately authorized test: the [installed CLI queue route](codex-queue-desktop-rehearsal-20261017.md)
did produce an automatic visible response in this same open idle chat. That
ordinary-message transport is distinct from this rejected App Server route
and has not yet been integrated into the Hybrid SDLC CLI.
