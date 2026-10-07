# Managed Strata lifecycle

Hybrid SDLC can optionally manage a separate Strata Docker service. This does
not adopt an existing container, change repository endpoint settings, or start
inference automatically. The existing manual deployment remains available;
see [Strata Docker setup](strata-server-docker.md).

## Explicit setup

Install the current Hybrid SDLC package and Docker Compose first. The bundled
profile targets the existing RTX 3090 evaluation: pinned Strata, Coder IQ1_M,
16K context, low-RAM mode, int8 KV, no vision, and low-reasoning defaults.
GPU passthrough and sufficient memory/disk are still prerequisites.

```sh
hybrid-sdlc runtime strata configure --port 8080
hybrid-sdlc runtime strata status
```

Configuration creates user-level state under
`~/.hybrid-sdlc/runtime/strata`, outside the task repository. It uses packaged
runtime assets rather than loading a repository's Compose file. Keep this
directory private to the intended account. Do not edit its ownership metadata,
activity records, or rendered assets to bypass a refusal.

The first lifecycle operation binds the service to the selected local Docker
context. Remote Docker endpoints are unsupported. Later operations use that
context rather than following a changed CLI default.

Repeating configuration with the same options is harmless. Changing an existing
service's port or model-volume selection is not supported in this first version;
the CLI refuses to replace its identity or orphan its containers.

Configuration alone does not build an image or download a model. The required
pinned image must already be available to Docker; `start` disables image
building and pulling. For image preparation and GPU validation, follow the
manual deployment guide before starting the managed service.

## Commands

```sh
hybrid-sdlc runtime strata start
hybrid-sdlc runtime strata status
hybrid-sdlc runtime strata logs --tail 100
hybrid-sdlc runtime strata stop
hybrid-sdlc runtime strata restart
hybrid-sdlc runtime strata diagnose
```

Add `--json` to any command for machine-readable output. Use the installed
command's `--help` for options; the existing `hybrid-sdlc status` command still
reports an asynchronous task, not the Docker service.

These are explicit lifecycle operations, not automatic recovery. Logs are a
bounded snapshot, not an indefinite follow stream. Status and logs do not run
inference, build an image, or initiate a model download. Container running or
health status is distinct from measured inference readiness.

## Availability and readiness diagnostics

The managed runtime's configured endpoint can be inspected without selecting a
task repository:

```sh
hybrid-sdlc runtime strata diagnose --json
```

This default operation uses model-list and health GET requests only. It does
not generate text, run Docker commands, download models, or restart anything.
`loading` reflects a reported `loaded: false`; `busy` reflects participating
Hybrid SDLC activity; `available-unverified` means availability does not yet
prove inference readiness. Unsupported or inconsistent health observations
remain `unknown` rather than being interpreted as a stall.

To explicitly generate one small readiness response:

```sh
hybrid-sdlc runtime strata diagnose --readiness --timeout 10 --json
```

`--timeout` accepts 1 through 30 seconds. Without an override, the default is
3 seconds for availability checks and 10 seconds when readiness is requested.
Each diagnostic health/model-list GET and the opt-in readiness probe runs in a
separate bounded process with a wall-clock budget covering its startup and
requests; process cleanup can add bounded overhead. Multiple requests can make
the whole diagnostic take longer than one timeout interval. This is not an
overall command deadline. Ordinary non-generating `check` and MCP availability
checks retain their existing HTTP connection/read timeouts. Only a successful
readiness response reports `ready`.

Managed readiness requests coordinate through the same cross-client activity
gate as task submission and synchronous execution. A queued/running task or
unresolved lifecycle operation prevents diagnostic inference; a readiness
request holds the gate while its HTTP request is active. The existing
`check --readiness` command uses this guard too. Availability GET requests do
not require an idle service, and the MCP `check_local_model` tool remains
non-generating with its existing interface.

Default diagnostics observe persisted activity without deleting reservations.
Verified terminal jobs are excluded from the busy classification; unreadable
job records remain `unknown`. Synchronous/submitting reservations and unresolved
lifecycle operations remain conservatively busy until their owners release or
reconcile them. A busy report is not telemetry from unrelated clients.

Coordination is cooperative: activity from unrelated API clients is not known.
A client-side timeout does not establish that the server stopped processing
its request. The result reports uncertainty; it never claims a single timeout,
long idle period, or low throughput proves a stall, and it never restarts the
service automatically. Inspect the service before further work if processing
may still be unresolved.

In the existing CLI `check` result, `available` describes model-list
availability independently of readiness. With `--readiness`, an unsuccessful
readiness test still produces a failing exit code even when `available` is
true. The structured `readiness_tested` and `readiness_passed` fields explain
what was actually tested.

### Timeout evidence and advisory stall classification

New Aider attempt timeouts retain metadata in the run record's
`failure.details.timeout_diagnostics`: elapsed time, exit code, observed output
byte counts, truncation, and an opaque endpoint/model fingerprint. This fixed
allowlist is under 1 KiB; it contains no stdout/stderr text, prompt, source,
command line, environment, or raw endpoint credentials. Counts describe the
captured output, not necessarily all output emitted when its buffer truncated.
Remote request cancellation remains unknown. The versioned run result and
`TASK_TIMEOUT` failure code are unchanged. Fingerprints are correlation metadata,
not encryption; keep the entire artifact directory private.

To associate one recent timeout with a diagnostic, explicitly supply its
repository and run ID:

```sh
hybrid-sdlc runtime strata diagnose --repo-root . --run-id run_1234567890_ab12cd34 --json
hybrid-sdlc runtime strata diagnose --repo-root . --run-id run_1234567890_ab12cd34 --readiness --timeout 10 --json
```

Replace the example ID with the actual run ID. Both options are required
together. The first command still uses GETs only. The reader examines only
`.hybrid_sdlc/runs/<run-id>.json`, rejects escaping/symlinked paths and records
over 128 KiB, and requires matching endpoint/model metadata from the preceding
30 minutes. Missing, legacy, malformed, future-dated, expired, or mismatched
records do not establish timeout evidence. It neither scans other repositories
nor deletes or rewrites records. Normal `hybrid-sdlc clean` retention applies;
use `--dry-run` before deleting artifacts.

`suspected-stalled` requires all of: matching recent Aider timeout evidence,
reported `loaded: true`, an available matching model, known-idle managed
activity, and a separately requested readiness inference that actually started
and timed out. A single timeout, default GETs, low throughput, or long idle time
alone cannot produce that state. Successful readiness reports `ready` instead.
This is an advisory correlation, not proof of a server stall or its cause;
overload, external requests, and client problems remain possible. No restart,
automatic retry, or service mutation is triggered.

The first explicit start with a new model volume can download approximately
58.4 GB of model files and take hours. Later starts reuse that volume. Stop
and restart do not delete containers' model volumes or user configuration.
No command removes model data as part of normal lifecycle management.

## Existing manual deployment

The managed service has its own Compose identity and ownership labels. A
matching name or port is not evidence of ownership. It cannot stop or restart
the manually started `hybrid-sdlc-strata-strata-server-1` container.

To reuse an existing Docker named model volume, opt in explicitly:

```sh
hybrid-sdlc runtime strata configure --port 8080 --reuse-model-volume VOLUME_NAME
```

Confirm the actual volume name in Docker before using it. Reuse does not
transfer ownership of the original container. Do not run two Strata instances
against the same model volume or GPU at the same time. If moving from the
manual deployment, first finish all its work and stop it yourself; only then
start the separately configured managed service. The CLI does not perform
that migration or silently stop a service that occupies the chosen port.

In each trusted task repository, configure the selected endpoint explicitly
and preserve its existing profiles and limits:

```toml
selected_model = "qwen3.8-flash-next-coder-iq1_m"

[[server_candidates]]
url = "http://127.0.0.1:8080/v1"
```

## Activity protection and limits

Managed endpoint activity is coordinated across participating Hybrid SDLC
clients and repositories under the same user-level state. Synchronous runs
reserve activity for their lifetime. Submitted jobs retain a durable
reservation during queuing, launch, execution, and terminal-state persistence.
Lifecycle operations coordinate with those reservations, rather than just
checking one repository or taking a snapshot before a restart.

Disruptive operations are refused while activity is unresolved. Corrupt or
unreadable ownership/activity state fails closed. Do not remove records merely
because an operation is taking a long time: an orphaned or ambiguous record
requires investigation before disruptive recovery.

A failed or timed-out lifecycle operation can leave Docker still completing
the request. The registry retains an unresolved-operation marker rather than
allowing new managed tasks or another lifecycle change during that uncertainty.
Status and bounded logs remain available for investigation. There is no
automatic retry or recovery command in this version; do not delete the marker
without confirming what Docker and the service are actually doing.

This is cooperative coordination, not an OS sandbox. It does not track direct
API requests from unrelated clients, prevent someone using Docker manually,
or protect work submitted by an older Hybrid SDLC version without activity
registration. Finish that work before running a disruptive lifecycle command.

The earlier stall after a reported long idle period remains unconfirmed.
Metadata-only timeout evidence and conservative suspected-stall classification
are implemented; controlled long-idle/retry validation remains open under HSDLC-064E.
Automatic restart is not part of the diagnostic workflow.
