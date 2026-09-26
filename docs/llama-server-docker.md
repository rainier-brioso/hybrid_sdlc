# Dockerized llama.cpp Server

Docker is the preferred reproducible deployment for the local inference server. The
`hybrid-sdlc` runtime itself remains endpoint-first: it can connect to this container, a
native `llama-server`, or another compatible external server without changing task execution.

## Prerequisites

- Docker Desktop using the WSL2 backend on Windows, or Docker Engine on Linux.
- NVIDIA Container Toolkit support exposed through `docker run --gpus all`.
- An NVIDIA driver compatible with the selected CUDA image. The repository defaults to
  llama.cpp's CUDA 12 image because CUDA 13 images may require a newer driver runtime.
- A locally downloaded GGUF model. Model weights are never copied into the image.

Verify GPU passthrough before loading the model:

```console
docker run --rm --gpus all <cuda-capable-image> nvidia-smi
```

## Configure

Copy `.env.example` to `.env`, then set `MODEL_DIR` and `MODEL_FILE`. Keep `.env` local;
it is ignored by Git. The model directory is mounted read-only.

The committed defaults select the bounded worker profile:

- 16,384-token context
- 4-bit K and V caches
- 1,536-token reasoning budget
- one parallel request
- localhost-only host port `8089`

Start the server:

```console
docker compose up -d llama-server
```

Follow startup and model-load logs:

```console
docker compose logs -f llama-server
```

Verify readiness from the host:

```console
curl.exe http://127.0.0.1:8089/health
curl.exe http://127.0.0.1:8089/v1/models
```

Stop the server without deleting model weights:

```console
docker compose down
```

## Recorded deployment check

The candidate worker profile was exercised on Windows with Docker Desktop, WSL2, and an
RTX 3090 using `Qwen3.6-35B-A3B-UD-Q4_K_S.gguf`:

- Docker GPU passthrough detected the RTX 3090 and 24,576 MiB VRAM.
- The CUDA 13 image rejected the installed driver because it required CUDA 13.4; the
  pinned CUDA 12 image started successfully.
- `/health` returned `ok`, `/v1/models` reported a 16,384-token context, and a chat
  completion finished successfully.
- The observed cached-prompt smoke completion generated approximately 59 tokens/second.
- Loading the 20.9 GB GGUF from a Windows bind mount took approximately 17 minutes.
- Steady state used approximately 20.2 GiB container memory and 23.35 GiB VRAM.

This proves functional compatibility, not superiority over the native Windows server.
Phase 7 retains the native-versus-Docker benchmark before either launcher is promoted.
An optional Docker named-volume model cache may reduce repeated Windows bind-mount startup
cost, but it intentionally remains opt-in because it duplicates roughly 20 GB of weights.

## Interactive profile

The 65,536-token interactive profile is a candidate, not a validated RTX 3090 setting.
The recorded 16,384-token run already used about 23.35 GiB of the card's 24 GiB VRAM;
increasing context can exhaust VRAM or require slow CPU offload. Keep the tested 16K
server context until a separate memory benchmark establishes a workable interactive
configuration. If evaluating 65K, set `LLAMA_CTX_SIZE=65536`, reduce GPU offload or
other memory demands as needed, and verify startup and sustained use before advertising
65,536 tokens to the client or compacting around 40,960 tokens. Do not run worker and
interactive servers concurrently on a single RTX 3090.

The files in `config/model-profiles/` record the intended client and benchmark settings.
They are candidate profiles until the native-versus-Docker benchmark is recorded.

## Operational boundaries

- The container listens on `0.0.0.0:8080` internally so Docker can publish it, but Compose
  binds the host port to `127.0.0.1` only.
- Run one large model and one inference request at a time on a 24 GB RTX 3090.
- Docker is a deployment mechanism, not the task sandbox. Repository test commands still
  execute under the trust model documented by `hybrid-sdlc`.
- Pin `LLAMA_CPP_IMAGE` to a tested build tag or immutable digest before declaring a
  profile supported. Floating CUDA tags are appropriate only while evaluating the setup.
- On Windows, compare Docker/WSL2 memory use with the native server before making Docker
  the only supported launch path.

## Troubleshooting

If Docker reports that the Linux engine pipe is unavailable, start Docker Desktop and wait
until the engine is ready. If GPU access fails, update WSL, Docker Desktop, and the NVIDIA
Windows driver before changing model settings.

If the model cannot be opened, verify that `MODEL_DIR` is shared with Docker Desktop and
that `MODEL_FILE` is the filename relative to `/models`, not a Windows absolute path.
