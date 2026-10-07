# Controlled runtime benchmark protocol

This is preparation for future, authorized benchmarks using a representative
delegated task. The fixture is pending, so no run is ready or authorized. The
manifest at [`benchmark_task.json`](benchmark_task.json) is an unready template;
fill required pins from the actual artifacts and environment before execution.
Do not invent a fixture, hashes, prompt, or result.

## Offline preflight

The Python API `hybrid_sdlc.benchmark_manifest.validate_manifest(manifest)`
accepts a parsed JSON value and returns a deterministic list of field-path
errors. An empty list means the required preflight pins and comparison settings
pass structural checks. The shipped unready template must return errors.

```python
import json
from pathlib import Path

from hybrid_sdlc.benchmark_manifest import validate_manifest

manifest = json.loads(Path("benchmarks/benchmark_task.json").read_text(encoding="utf-8"))
errors = validate_manifest(manifest)
```

This function is offline and does not mutate its input. It does not access
files, start services, run benchmarks, or verify that a recorded digest matches
an actual artifact. Tokenizer/configuration provenance still needs independent
pinning and verification. It does not check ambient conditions, repetitions,
measurements, acceptance evidence, privacy of the manifest, or eligibility for
comparative claims. Those protocol gates remain the operator's responsibility;
successful preflight does not mean a benchmark is complete or authorized.

## Comparison boundaries

Measure container overhead within one engine and one exact model profile:
Strata native versus Strata Docker, or llama.cpp native versus llama.cpp
Docker. Each pair must use identical model files, quantization, tokenizer and
model configuration. Pin every shard by filename, byte size and SHA-256. Pin
each runtime build/image immutably and record its full launch arguments and
effective request settings. If the exact artifacts cannot be matched, the pair
is not a runtime comparison.

The candidate registry in the manifest records Strata v0.1.39 at source
revision `6f32ec070f23ced9f50e704d854d775da52591ab`, and the retained
llama.cpp Qwen3.6 35B-A3B Q4_K_S 16K profile. The 27B quality-comparison
profile is a separate model-quality group, not a runtime variant or a
container-overhead comparison. Comparing engines or different model sizes,
quantizations, or configurations is a quality/throughput tradeoff and must be
reported separately. Do not combine those results or call them runtime parity.

The Oct 4-5 image digest in historical notes is not verified as the current
runtime image. The manifest leaves current runtime digests null until captured.
Existing Strata probes and smoke runs and llama.cpp deployment checks are
historical diagnostics, not controlled benchmark results. Keep the existing
LLM smoke pause unchanged. Long-idle behavior is a separate validation; see
[Strata long-idle validation](../docs/strata-long-idle-validation.md).

## Protocol

1. Obtain a real representative fixture from an authorized delegation. Record
   its committed baseline and specification hashes, test profile, allowed
   paths, acceptance rules, and independent review procedure.
2. Select one engine/profile comparison group. Pin the shared model artifact
   identically for native and Docker. Record all model shards, tokenizer/config
   sources, runtime source revisions and image/binary digests, build arguments,
   launch arguments, and
   effective context, KV, reasoning, output limit, and parallelism. Record
   toolkit, Aider, Python, test tools, and test-environment versions. Record
   full non-secret launch/request settings; omit credentials and reference
   credential sources privately. Never serialize credential-bearing URLs or
   environment dumps.
3. Match fixture, prompt/input, task limits, and acceptance criteria exactly.
   Record a privacy-safe prompt hash. Capture ambient CPU/GPU, host RAM,
   driver, VM memory limit, disk activity, and concurrent model clients; do not
   change RAM/VM limits or other settings automatically between variants.
4. Run at least three repetitions per variant. A cold repetition starts a
   fresh process/model; measure startup separately from readiness and task
   time. Keep optional warm-cache repetitions in a separate series with at
   least three repetitions and report actual cache and token reuse. Use the
   same timeout and retry limit for both variants.
5. Count successful accepted runs divided by all started runs as the run
   success rate. Separately report accepted attempts divided by all started
   attempts. Include failures and timeouts in the matching denominator; report
   retries, startup failures, test failures, and invalid patches explicitly.
   Define time-to-green consistently as elapsed time to the first independently
   accepted patch under the shared retry policy.
6. Capture request-to-first-event and request-to-first-nonempty-content
   separately. Record prompt processing time, uncached prompt tokens, cache
   reused tokens, prompt tokens/second based on non-reused tokens and actual
   prefill duration, decode time and generation tokens/second. Account for
   reasoning tokens in token totals or state why unavailable. Also record
   process startup, readiness, total startup, completion, total task wall time,
   and total end-to-end wall time. Keep human review time separately identified.
7. Collect timestamped resource samples and sample interval. Distinguish
   load-time snapshots from sampled peaks and label the scope: host committed
   memory, process/container RSS, Docker/WSL2 VM, VRAM, and (where available)
   weight, KV, and compute-buffer memory. Do not compare different scopes.
   Unsupported metrics are null with a reason, never estimates.
8. For each run, preserve the exact generated patch. Make a fresh baseline copy
   before the next repetition. For independent tests, copy the baseline and
   apply that exact review patch to the copy; never reset or overwrite the
   worker patch/source checkout before review and testing. Inspect allowed
   paths, generated files, tests, and acceptance criteria; obtain an
   independent patch review.
9. Store hashes and privacy-safe metadata only. Do not include raw private
   prompts, private paths, secrets, tokens, or environment dumps in the repo.

Do not stop, restart, unload, or change a shared inference service unless its
operator explicitly authorizes that lifecycle action. A loaded model is not a
cold start. A comparative claim is prohibited if required artifact pins,
matched settings, repetitions, or acceptance evidence are missing. Report
cross-engine or cross-profile quality comparisons separately from runtime
overhead. A historical digest or note does not establish the current image
digest; leave any unverified current digest null.

## Reporting

Publish the completed manifest and per-repetition measurements, including all
failures, effective settings, resource scopes, and evidence hashes. Report
accepted-run success rate, attempt acceptance rate, quality acceptance,
latency, startup, and resource distributions by variant. State unsupported
metrics and unmatched conditions beside results. Existing smokes and the
separately paused llama.cpp smoke do not count as passes or benchmark results.
