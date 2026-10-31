# Managed Strata context change — 2026-10-30

## Scope and state

The user requested 65536 context tokens, keeping the 4096-token output default.
HSDLC-089 adds an explicit supported configuration path rather than hand-editing
validated runtime assets. The default remains 16384. Existing model weights,
endpoint, port, ownership identity and model-specific reasoning settings are
preserved. The earlier 16K benchmarks remain historical observations, not 65K
performance measurements.

After the user started Docker Desktop, current status confirmed the owned
service was exited, with no lifecycle uncertainty, and diagnostics reported
managed activity idle. The installed package was updated from the validated
local wheel without dependency replacement or force-removal of the tool
environment. `configure --port 8080 --context-size 65536 --json` selected 65536
and marked recreation required; the explicit `start` recreated only the owned
service using the existing image and model volume.

## Source verification

- Focused runtime/startup/CLI tests: **212 passed, 1 skipped** (Windows symlink
  privilege unavailable). The skip is not verified behavior.
- Source mypy: no issues in 30 files.
- Ruff lint passed; formatting check: 137 files already formatted.
- Independent review found no remaining code blocker after fixes for pending
  migration reservations, stale-container restart and unmounted startup-file
  preservation.
- Offline wheel and sdist builds succeeded. Artifacts are retained outside Git;
  building does not establish clean-install or live behavior.
- Full coverage run: **1075 passed, 14 skipped, 82.41% coverage**, Windows/Python
  3.12.14, 257.49 seconds. Skips cover unavailable Windows breakaway/symlink or
  POSIX permissions and opt-in clean-distribution installation, not failures.
- The initial full run exposed an unsynchronized manual-deployment startup
  mirror; synchronizing it resolved the failure. The final runtime/startup/CLI
  plus repository-layout checks passed: **216 passed, 1 symlink skip**.
- Both distribution archives passed packaging checks. An isolated installed
  wheel advertised the bounded `--context-size` option and accurate help.

The regression cases cover legacy markers, strict context bounds, preservation
of identity and unrelated model settings, stopped/idle guards, interrupted asset
publication and explicit retry recovery, forced recreation, container environment
verification and refusal of task/readiness reservations before application.

## Live startup observations

- Desired context: 65536; `needs_recreate` and `context_update_pending` false
  after successful recreation; no lifecycle uncertainty.
- Startup log selects `--max-context 65536 --kv int8 --resident-experts` and
  reasoning budget 1024.
- A filtered read of the saved model configuration confirms context 65536,
  reasoning budget 1024 and unchanged shared defaults: low effort, 4096 output
  tokens. No prompts, repository data or full model configuration were exported.
- Pre-start GPU memory: RTX 3090, 24576 MiB total, 1002 MiB used.
- Loading allocated an expert cache of 8275 experts / 15.75 GiB according to
  the startup log. This is not a throughput or quality measurement.
- The server's ready log reports context 65536. One installed CLI
  `runtime strata diagnose --readiness --timeout 30 --json` succeeded:
  status ready, endpoint/model available, loaded true, readiness tested/passed,
  error null, managed activity idle. Command wall time was 6.35 seconds;
  this is not an inference-only latency or throughput benchmark.
- Final status: running and healthy, context 65536, no pending configuration,
  no recreation required and no lifecycle uncertainty.
- Post-readiness GPU memory: 23963 / 24576 MiB used (about 23.4 / 24 GiB,
  including desktop/other GPU allocations). The profile fits at idle/readiness
  but has little free VRAM; simultaneous GPU model use was not tested.
- Docker still publishes only `127.0.0.1:8080`. The generic server log's
  no-API-key warning is not evidence of a newly published LAN port.
- No Metria files, branches or database were touched. No source commit, push
  or PR was created by this runtime validation.

## Limits and follow-up

The model loaded within the five-minute observation window. No automatic
readiness retry or restart of an uncertain inference request was performed.
Live rollback, restart persistence after a host reboot, a fully populated 65K
prompt and representative coding performance remain untested. The rollback
path is the same stopped/idle CLI migration with `--context-size 16384`, followed
by explicit `start`; do not bypass activity guards or delete model volumes.

Readiness is not a full-context coding benchmark or proof of performance at
65536 input tokens. Preserve separate evidence for a later representative task.
