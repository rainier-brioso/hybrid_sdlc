# Hybrid SDLC Toolkit

The **Hybrid Spec-Driven SDLC Toolkit** (`hybrid-sdlc`) bridges high-reasoning cloud agents with cost-effective local inference via an autonomous, bounded editing and testing loop.

## Local inference

The runtime connects to a verified OpenAI-compatible endpoint and does not require a
specific server launcher. For the reproducible RTX 3090 llama.cpp deployment, see
[`docs/llama-server-docker.md`](docs/llama-server-docker.md). Native and externally managed
servers remain supported deployment choices.

Strata is available as an optional evaluation deployment through
[`docs/strata-server-docker.md`](docs/strata-server-docker.md) and
`compose.strata.yaml`. The remaining llama.cpp live smoke rerun is paused
while this backend is evaluated; Docker/WSL2 performance parity is not yet
established.
