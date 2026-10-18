# Installed Hybrid SDLC queue bridge rehearsal

Observed 2026-10-17, approximately 21:58 UTC (Europe/Madrid: 23:58).

## Build and scope

- Merged Hybrid SDLC source: `9c2b1274a35bdb099adff3c0a7ae3cc50f868329`.
- Wheel: `hybrid_sdlc-0.1.0-py3-none-any.whl`, built from merged main.
- Wheel SHA256: `9b3f5fe06e010437c36809a953f710f2c536d330312dbb8de4fd08f7de24bbfa`.
- Installed bridge source SHA256: `54575c060765cbeed0d22ee608668d52b5a241c9754a71c3320cfad680c61bed`.
- Host: `codex-cli 0.162.0-alpha.17.2`; Windows/Python 3.12.14.

The wheel was installed with its dependencies into a separate virtual environment.
The actual console executable ran from a disposable Git repository outside the
source checkout. The fixture confirmed imports came from installed
`site-packages`, not the development source tree. No model/provider override or
host setting change was used. Host usage remains unavailable.

The user authorized one synthetic completion to the existing disposable chat
**MCP completion wakeup smoke test**, target
`01a14601-3d14-7e63-b6a2-f8a984cf96ae`, then confirmed it was visible and idle.
The synthetic job had no Aider execution, patch, tests or member data. A local
timer changed its persisted state from running to completed after three seconds.
This exercises the installed bridge, not async worker survival or real editing.

## Delivery

The installed console command used the real Codex executable:

```text
hybrid-sdlc bridge codex JOB_ID --repo-root DISPOSABLE_REPO --thread-id TARGET_UUID --allow-model-continuation --codex-executable TRUSTED_CODEX_EXECUTABLE --wait-timeout 30 --queue-timeout 45 --json
```

The outer harness also wrote an exclusive intent record to prevent accidental
resend. It did not call App Server RPC or issue repeated host-model status polls.

- Job: `job_20261017T215449658191Z_8e781595`.
- Synthetic terminal transition: `2026-10-17T21:58:08.836762+00:00`.
- Bridge intent/receipt created: `2026-10-17T21:58:09.258008+00:00`.
- Exit code: 0; elapsed approximately 4.609 seconds including local waiting.
- Result: `delivery_status: queue_accepted`, `queue_accepted: true`,
  `response_completed: null`, `usage: null`.
- Persisted version-2 receipt state: `queue_accepted`, bound to that job/thread.

The fixture repository remained clean. A second installed-CLI invocation for the
same job was refused with `CODEX_BRIDGE_FAILED`: the existing receipt blocks
replay or retargeting. No second completion was sent.

## Human observation followed by history verification

After queue acceptance, before any history read, refresh or navigation, the user
reported **A new response appeared automatically**. Only then was history read.

That read confirmed a new completed turn in the same target chat:
`01a14bdf-78c6-7573-b65c-3ec8097e6113`, recorded duration 5,860 ms. The incoming
ordinary message contained the exact job ID and allowlisted metadata. Its final
response acknowledged that job as completed and correctly stated that no run
status or test results were supplied, and completion was not patch acceptance.
There were no tool-call items in the new turn. The chat was idle afterwards.

## Installation observation and limits

The initial global `uv tool install --force` attempt failed because existing
Codex MCP processes held the environment's Python directory open. The failed
update had removed package files and dependencies before hitting that lock.
Installing the built wheel and its dependencies in place restored the global
entry point; `hybrid-sdlc bridge codex --help` and `uv tool list` succeeded.
No active MCP process was stopped. The live delivery above used the separate
installed environment, not those already-running MCP processes.

This establishes one installed-wheel bridge invocation, accepted delivery,
automatic visible response and same-job replay refusal on this host/build.
It is separate from the earlier [direct queue probe](codex-queue-desktop-rehearsal-20261017.md).
It does not establish a clean-machine installation, repeated reliability,
cross-repository concurrency, active-turn interruption, Windows worker breakaway,
real Strata task quality, full-feature orchestration or measured token savings.
Raw operational reports remain outside the repository; no private configuration
or unrelated chat history is included.
