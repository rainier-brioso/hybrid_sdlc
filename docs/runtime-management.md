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
```

Add `--json` to any command for machine-readable output. Use the installed
command's `--help` for options; the existing `hybrid-sdlc status` command still
reports an asynchronous task, not the Docker service.

These are explicit lifecycle operations, not automatic recovery. Logs are a
bounded snapshot, not an indefinite follow stream. Status and logs do not run
inference, build an image, or initiate a model download. Container running or
health status is distinct from measured inference readiness.

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
Automatic restart, suspected-stall classification, inference diagnostics, and
retained Aider timeout output are separate follow-up work (HSDLC-064E).
