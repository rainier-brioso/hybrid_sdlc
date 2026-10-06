# Dockerized Strata Server (Evaluation)

This is an optional evaluation deployment for the RTX 3090 worker profile. It
keeps Strata's model files in a Docker named volume and exposes its OpenAI
compatible API only on localhost. The setup is pinned to Strata v0.1.39 at
revision `6f32ec070f23ced9f50e704d854d775da52591ab`; review upstream changes
before updating either pin.

The configuration selects Strata Coder `IQ1_M`, a 16,384 token context, no
vision, low RAM mode, GPU 0, and one request at a time. The model is downloaded
into the persistent `strata-data` volume. Allow at least 80 GB of free disk
space for the image, build layers, and model files. The first build compiles or
assembles the server image and can take a while; the first start downloads
approximately 58.4 GB of Coder model data (upstream's generic startup message
says approximately 70 GB). Prepared packs and image layers require additional
space. Later starts reuse the named volume.

## Requirements

- Docker Desktop with the WSL2 backend and NVIDIA GPU support.
- NVIDIA driver 580 or newer and Docker GPU passthrough.
- Unrestricted memlock inside the container; Compose sets the Strata-required
  `memlock` soft and hard limits to unlimited.
- An RTX 3090 (24 GB VRAM) and at least 32 GB system RAM for this candidate
  profile. Strata reports that it can run with 24 GB GPU VRAM in low-RAM mode,
  but disk activity and system memory pressure can make it slow.
- An internet connection for the image build and model download.

The current Docker daemon reports about 24 GiB of memory to containers, below
Strata's documented 32 GB minimum. Check Docker Desktop's WSL2 memory limit and
raise it if the Windows host has enough physical RAM. Do not assume that having
32 GB installed in Windows makes all of it available to the Docker VM.

Before the build, verify that Docker can see the GPU:

```powershell
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi
```

## Start and use

The commands below manage the manual evaluation deployment. For the optional
installed CLI lifecycle commands, see [Managed Strata lifecycle](runtime-management.md).
That service uses a separate ownership identity and never adopts this deployment.

Run these commands from the repository root. No `.env` file is required:

```powershell
docker compose -f compose.strata.yaml build
docker compose -f compose.strata.yaml up -d
docker compose -f compose.strata.yaml logs -f strata-server
```

The health check stays in `starting` state until Strata reports
`loaded: true`. The first model download can take hours. The health check allows
two hours before reporting unhealthy; a longer download can exceed that window.
An unhealthy state does not stop the container or trigger a restart. Follow the
logs and let the resumable download continue. Leave the log view with Ctrl+C;
the container keeps running. Check readiness and the model identifier:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/health'
Invoke-RestMethod 'http://127.0.0.1:8080/v1/models'
```

The expected Hybrid SDLC endpoint is `http://127.0.0.1:8080/v1`. The expected
model ID is `qwen3.8-flash-next-coder-iq1_m`; verify that Strata's model list
matches before using [the candidate worker profile](../config/model-profiles/strata-coder-rtx3090-worker.toml).
Compose mounts [worker-defaults.json](../config/strata/worker-defaults.json)
read-only at the shared-settings path Strata reads during startup. It sets
`reasoning_effort` to `low` and the default `max_tokens` to 4096. A request that
supplies its own reasoning effort or output limit takes precedence. Low
reasoning guides the model's thinking and does not enforce a hard reasoning
token cap.

Keep this JSON file as the durable source of defaults. The Strata web/API
settings editor changes shared defaults in memory; the mounted file is
read-only, so those edits cannot be written back to it and will be lost when
the container is recreated. Edit the JSON file and recreate the container to
change durable defaults. The named model volume remains in place.

Stop while retaining downloaded model files:

```powershell
docker compose -f compose.strata.yaml down
```

Start again with `docker compose -f compose.strata.yaml up -d`. Removing the
named volume deletes the downloaded model and is intentionally not part of the
normal stop command.

## Connect Hybrid SDLC

The candidate profile records evaluation settings; the toolkit does not
automatically load files from `config/model-profiles/`. For a trusted task
repository, configure its `hybrid_sdlc.toml` explicitly:

```toml
selected_model = "qwen3.8-flash-next-coder-iq1_m"

[[server_candidates]]
url = "http://127.0.0.1:8080/v1"
```

Preserve that repository's named command profiles and limits. During evaluation,
use only the Strata candidate so a failed probe cannot silently select another
backend. Verify the actual `/v1/models` ID before setting `selected_model`.
The existing MCP tools and Aider runner continue to use the OpenAI-compatible
endpoint; no Strata management MCP is required for task execution.

After setting low reasoning and observing `loaded: true`, run:

```powershell
hybrid-sdlc check --repo-root '<trusted-repository-path>' --json
```

The default CLI probe checks model availability only. Its optional readiness
probe has a three-second timeout, which may be too short for cold inference;
a timeout there is not a measured coding-task failure.

## Build and model configuration changes

The default Compose environment requests the pinned Coder IQ1_M configuration.
When changing `FAMILY`, `MODEL`, `CONTEXT`, `VISION`, `KV`, or `LOW_RAM`, set
`REINSTALL=1` for one startup so Strata rebuilds its generated model
configuration. Then set it back to `0` before later starts. The Compose file
records the initial profile; keep personal overrides in a separate local
Compose override file rather than changing the committed candidate.

Changing the upstream source pin or build arguments requires running
`docker compose -f compose.strata.yaml build` again before `up`. Model data is
independent of the image and remains in the named volume.

## Performance expectations and validation

Docker simplifies setup and gives the model a persistent Linux filesystem,
which avoids placing tens of gigabytes of model data on a Windows bind mount.
On Windows, Docker Desktop still runs Linux containers inside WSL2. Strata
loads substantial model data into system memory and streams some expert data
from storage, so WSL2 memory limits, SSD throughput, CPU load, and GPU
passthrough can affect speed. A container does not make the model faster by
itself. CUDA execution should still use the GPU, but equivalence with Strata's
native Windows launcher is unmeasured on this machine. Strata's WSL notes also
describe pinned memory limited to about 1 GB and KV streaming disabled, which
can make prompt processing slower than native Linux. Treat Docker Desktop on
Windows as an installation convenience whose performance must be measured.
The candidate configuration uses `KV=int8`; Strata documents the WSL KV
streaming limitation separately, so test the actual engine behavior and prompt
speed instead of assuming int8 streaming is active in this container.

The existing llama.cpp model smoke test is paused while this backend is
evaluated; that pause does not count as passing llama.cpp validation. The user
deferred runtime comparison until a real task delegation supplies a
representative fixture (2026-10-04). Compare its native and Docker launchers using
the same model, prompt, context, output limit, and reasoning effort. Record
model load time, time to first token, generation tokens per second, peak GPU
memory, system memory, and disk activity. Then run the same bounded coding
task and verification command through Hybrid SDLC. Do not promote this
candidate until the Docker run passes that task and the native-versus-Docker
measurements are available.

## Recorded local setup (2026-10-04)

- Hardware: RTX 3090 24 GiB, NVIDIA driver 596.21, Ryzen 9 7900X3D
  (AVX-512), approximately 32 GB physical RAM, WSL2 memory limit 24 GB.
- Docker Compose rendering and the existing repository layout tests pass.
  CUDA 13 GPU passthrough and the pinned Strata image build succeeded.
- Built image: `sha256:c3fed5000a88d2174ee55b6dec5ecac9d740c8bcb0a80091e94a4f9a87784b99`.
- Upstream setup accepted the 16K Coder profile and predicted about 81% of
  its experts on the GPU, with about 4 GB remaining resident in RAM. This
  is setup's estimate, not an observed throughput or peak-memory result.
- Both shards downloaded: 29,608,446,496 and 28,800,138,432 bytes. Recreating
  the container applied the defaults mount and reused these files without
  downloading again. The container reports healthy and publishes only
  `127.0.0.1:8080` on the host; upstream's generic LAN warning does not change
  that Docker host binding.
- `/health` reports loaded with 16K context and the expected model ID.
  `/settings` reports low reasoning and max_tokens 4096; `/props` exposes the
  output limit as `default_generation_settings.params.n_predict` but omits
  reasoning effort. The Hybrid SDLC model-availability probe passed.
- A bounded chat (none effort, max_tokens 128) answered `42` with stop finish
  reason in 4.95 s. This tiny initial request is not a throughput benchmark.
- Isolated Strata/Aider SMOKE-001 passed in one attempt, 56.7 s; an independent
  rerun passed both unittest methods. It correctly added multiply_by_two and
  tests for zero, positive, and negative inputs, preserving add_one. However,
  Aider also created `.aider.tags.cache.v4/cache.db`, so that run failed
  clean-diff acceptance. No model-generated commit was created.
- Smoke evidence: run `run_1791139151_6f5ae9e5`, source fixture
  `hsdlc-strata-smoke-20261004-01`, baseline
  `7c1b96d4a58ca7cddd2c8fd8b3dcd931f4e3e24f`. The generated patch and isolated
  worktree are retained; the original llama.cpp smoke fixture was not changed.
- Clean rerun `run_1791140948_7f6efcb2` passed in one attempt (116.2 s), with
  exactly `calculator.py` and `test_smoke.py` changed and both test methods
  independently passing. Its fixture baseline is
  `7e123e6cf6d3ecde909fec78b079738c9f43e42a`. The fixture explicitly opts into
  `aider_repo_map_tokens = 0`, disabling map context/cache for this tiny task.
  Normal tasks retain Aider's map unless configured otherwise; map-enabled
  cache handling remains open. No ignore rules were changed or patch paths
  hidden, and both runs' evidence is retained. The longer duration warrants
  controlled prompt-processing measurements, not a runtime-performance claim.
- Automated Aider invocation now skips the model-metadata warning prompt
  (`--no-show-model-warnings`) that could otherwise open its documentation
  URL under `--yes-always`. Other settings checks remain enabled. These code
  changes were exercised from the source worktree and the refreshed installed
  CLI/MCP. A fresh SDK stdio client discovered all five tools, passed the
  endpoint probe, and completed the isolated synchronous smoke in one attempt
  (19.7 s), with only the authorized two files changed and two test methods
  independently passing. See
  `benchmarks/results/strata/installed-mcp-smoke-20261004.json`. The existing
  Codex chat connection still returned `Transport closed`; it needs a separate
  reconnect. This smoke does not prove async survival or runtime parity.
- The development worktree's local `hybrid_sdlc.toml` selects the Strata
  endpoint; its former llama.cpp settings are saved in the ignored
  `hybrid_sdlc.llama.local.toml`. The previous smoke fixture is preserved
  for the paused llama.cpp rerun.

Upstream references: [Strata installation requirements](https://github.com/Niko1221/Strata/blob/6f32ec070f23ced9f50e704d854d775da52591ab/docs/AI_SETUP.md), [Strata API and settings](https://github.com/Niko1221/Strata/blob/6f32ec070f23ced9f50e704d854d775da52591ab/docs/DETAILS.md), [pinned upstream source](https://github.com/Niko1221/Strata/tree/6f32ec070f23ced9f50e704d854d775da52591ab).
