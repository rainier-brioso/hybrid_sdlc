# Native Strata lifecycle validation — 2026-11-09

## Scope

HSDLC-090 adds explicit native-backend registration and the `hsdlc` console
alias with `start strata`, `stop strata`, `restart strata`, and `status strata`.
Canonical lifecycle commands and existing job-status commands remain available.
The feature checkout is `codex/native-strata-lifecycle`, based on pending PR
#19 at `9372789`; main and other worktrees were not modified.

Native service: Windows, RTX 3090 24 GB, 32 GB RAM, Strata Terminal 0.2.13,
engine 0.1.59, Coder IQ1_M, 131072 context tokens, INT8 KV, one request slot,
MTP, vision off and 1536 MiB requested VRAM reserve. Existing model files and
saved preset were reused. No model download or preset edit was part of this
lifecycle validation.

## Safety and implementation

- Native registration validates the selected preset, profile, effective model,
  loopback binding, port/context and provisioned engine. Stored configuration
  hashes detect drift.
- Task leases, lifecycle locks, queued-job reconciliation and uncertain-mutation
  records are shared with Docker. Native status/logs use no Docker operations.
- Explicit Docker replacement checks every owned container is stopped and
  retains its definition and volume. This transition is covered by fake-Docker
  regression tests; the live test did not replace the user's global registration.
- Stop sends the exact native owner and instance IDs observed in its read-only
  plan; a busy/draining or unverified instance is refused.
- Start invokes only the already provisioned native `runtime start` path,
  without model acquisition. Post-start identity/context are checked.
- Health runs in the existing process-bounded probe worker, with redirects and
  environment proxies disabled and a 16 KiB body ceiling. A real slow-drip
  regression proves a total deadline, rather than socket inactivity alone.

## Live installed-wheel checks

A built wheel was installed into an isolated environment. A small test harness
changed only the Hybrid SDLC state root; it invoked the wheel's normal Click
commands against the real native profile. This keeps the normal installed
Hybrid SDLC tool and its Docker registration unchanged. It is not a global
rollout or a clean-machine provisioning test.

1. Read-only registration accepted the existing `hsdlc-coder-128k` preset and
   verified its effective model and 131072 context.
2. `status strata` reported the verified ready owned instance.
3. `stop strata` stopped only that instance and verified exited/stopped.
4. The initial `start strata` exposed a daemon-lifetime defect: the generic
   task subprocess Job Object killed Strata's background owner after the client
   exited. Its uncertainty record was retained. Strata's own profile-scoped
   cleanup reported recovered, then its status confirmed stopped; no duplicate
   start was launched while that state was unresolved.
5. The fixed start path bounds only the trusted runtime-start client and its
   output. It allows Strata's owned daemon to outlive a successful client; a
   client timeout leaves uncertainty and does not guess that the daemon stopped.
   Generic task subprocess cleanup is unchanged. Real subprocess tests verify
   daemon survival after both normal client exit and a client timeout.
6. A fresh isolated registration then passed `start strata`; a later independent
   `status strata` confirmed the daemon survived the command. A second start
   was a safe no-op with unchanged identity.
7. `restart strata` passed and returned a new verified owner/instance, the same
   model/endpoint and 131072 context. It was left running; Docker Strata stayed
   stopped. Registered preset/profile/source hashes remained valid throughout.
8. `runtime strata diagnose --readiness --timeout 30 --json` passed after restart:
   model available, loaded, managed activity idle, readiness tested/passed,
   status ready. A later independent status still reported the same running
   instance and no lifecycle uncertainty in the successful test state.

The failed test's isolated uncertainty state was preserved separately from the
fresh successful fixture. No running job or external inference was intentionally
interrupted. Global package installation, real registration migration and host
MCP-process reload are separate rollout steps.

## Automated and review evidence

Focused lifecycle/CLI/map/health checks: 58 passed. Additional real native-start
client and adapter checks: 13 passed. Format, lint, strict source typing and
diff whitespace checks passed. Independent coordinator and adapter reviews
identified and closed first-marker retry and slow-drip deadline issues; the
specialized daemon-start helper received a further read-only review with no
verified material finding.

The final full gate, including the daemon-lifetime fix, passed **1123 tests,
14 platform/opt-in skips, 152 subtests and 81.99% coverage** on Python 3.12.14.
Format, lint, strict source typing, skill-package parity and verification-map
checks also passed. Skipped Windows
breakaway, symlink, POSIX permission and opt-in packaging behavior remains
unverified. The earlier 70079-token inference trial is separate evidence:
successful lifecycle operations do not prove full-window stress, sustained
throughput, readiness under load or successful Metria feature edits.
