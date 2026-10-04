# Strata prompt-processing investigation — 2026-10-04

Status: diagnostic investigation, not native-versus-Docker comparison.

## Runtime and conditions

- Strata 0.1.39, revision `6f32ec070f23ced9f50e704d854d775da52591ab`.
- Model `qwen3.8-flash-next-coder-iq1_m`, Docker/WSL2, RTX 3090 24 GB.
- Context 16,384; int8 KV; low-RAM mode on; shared defaults low reasoning,
  output cap 4,096. The probe explicitly overrides reasoning/output only
  for its own requests; it does not mutate defaults.
- The operator confirmed no other local model was loaded.
- Host snapshot: 32,613,876 KiB total physical RAM, initially 1,275,060 KiB
  free; a second pre-probe snapshot reported 743,136 KiB free. These are
  point-in-time observations, not peak measurements.
- GPU snapshot: 23,992 MiB used / 24,576 MiB total. WDDM process telemetry
  did not provide individual-process GPU memory attribution.
- Docker stats: 5.312 GiB container memory / 23.47 GiB VM limit. WSL
  `/proc/meminfo`: 20,804,912 KiB available and 21,419,584 KiB cached. These
  metrics have different accounting scopes; do not equate container RSS,
  Linux available memory, or Windows free RAM.

## Existing smoke-request metrics

Read-only `/metrics` records separate prompt processing from generation:

| Smoke | Prompt tokens | Reused | Prompt ms | Decode ms | Decode tok/s | Total s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Initial, request 1 | 2,412 | 0 | 23,710.3 | 4,378.7 | 58.5 | 28.1 |
| Initial, request 2 | 2,529 | 0 | 12,214.7 | 5,919.3 | 75.5 | 18.1 |
| No-map rerun, request 1 | 2,169 | 0 | 60,095.5 | 5,809.7 | 39.2 | 65.9 |
| No-map rerun, request 2 | 2,400 | 0 | 36,748.0 | 7,102.0 | 59.7 | 43.9 |

The slower smoke is dominated by prompt processing, not lengthy output.
Different prompts and runtime conditions prevent attributing this difference
to the map setting, Docker overhead, or any specific memory mechanism.
High GPU expert-cache hit rate is not prompt-prefix reuse.

## Bounded probe protocol

Run `benchmarks/strata_prompt_probe.py` from the source worktree. It sends three
identical synthetic-code prompts sequentially, with explicit no reasoning,
temperature zero, a 64-token output cap, and bounded request timeouts. It
records streaming first-event and first-content latency separately, per-request
server metrics when uniquely attributable, and the actual reused-token count.
It does not send real repository files, clear caches, restart the model,
enable API monitoring, or change server configuration.

The first request is a new synthetic prompt on an already loaded model, not
a cold-start measurement. Repeats are not independent fresh-prompt samples.
Any concurrently submitted request invalidates unique metric attribution.
Three requests cannot establish native parity or general code-task quality.

## Probe results

All three requests completed with `finish_reason: stop` and content `ACK`.
Actual prompt size was 1,321 tokens (4,988 characters); see the accompanying
`prompt-probe-20261004.json` for measured values and prompt checksum.

| Request | First content s | Client total s | Reused tokens | Server prompt ms |
| --- | ---: | ---: | ---: | ---: |
| New prompt on loaded model | 12.609 | 12.671 | 0 | 12,533.0 |
| Identical repeat 1 | 0.109 | 0.172 | 1,314 | 68.0 |
| Identical repeat 2 | 0.078 | 0.125 | 1,314 | 58.3 |

The new prompt cost about 105 prompt tokens/s in this observation. Repeated
requests reused 1,314 tokens and processed only seven new tokens. Comparing
their full prompt size against the reduced processing time would misleadingly
inflate prompt throughput. The median cached client duration is 0.1485 s;
the two cached samples span 0.125–0.172 s. Do not combine cached samples with
the new-prompt sample to claim typical fresh-prompt performance.

There was no reasoning output. Generation produced only two tokens per
request, too few to characterize generation throughput. The first SSE event
arrived much earlier than content; it is not time to the first answer token.

## Interpretation and next measurement

The existing setup supports effective immediate identical-prefix reuse without
enabling multi-conversation parking. The prior Aider smoke requests had zero
reused tokens, whereas this probe reused almost the whole prompt on repeats.
This establishes prompt processing and reuse as useful optimization targets,
but does not identify which Aider prompt changes prevent reuse or establish
that caching accounts for all of the observed variation.

Keep fresh client sessions for role/attempt isolation. Do not enable persistent
cross-task histories or conversation parking just to improve these numbers.
Next, measure representative distinct coding prompts under controlled host
memory conditions and inspect Aider's prompt/context construction without
persisting private task contents. Compare native and Docker only after both
can be measured with matching artifacts and settings. No apps were stopped,
memory limits altered, or server defaults changed during this investigation.

## Aider editable-file preloading follow-up

Installed Aider 0.86.2 detects newly mentioned repository files in the model
response, adds accepted files to chat, and sends a reflected follow-up before
applying edits (`base_coder.send_message`, `check_for_file_mentions`, and
`run_one`). Our original smoke supplied the spec read-only but no editable
files at startup. Its two-request pattern is consistent with this discovery
round trip, without inspecting or persisting private prompt history.

On the same clean fixture baseline, explicitly preloading `calculator.py` and
`test_smoke.py` yielded one request and a correct patch in 22.557 s. Both
test methods passed independently. The patch hash matches the prior clean
smoke exactly; only the authorized files changed, with no model commit.
The request processed 2,302 prompt tokens in 8,613.5 ms and generated 399
tokens in 7,334.5 ms. See `aider-file-preload-20261004.json`.

This was a one-off diagnostic through the existing runner API. No token reuse
occurred, so it is a round-trip reduction, not a caching improvement. Runtime
conditions differ from prior runs; do not advertise a general speedup from
22.6 s versus 116.2 s. Explicit file context is useful only when the caller
already knows the intended files. It must not be guessed from specification
prose or represented as a security-enforced edit allowlist.

### Actual Strata reuse mechanism

The live server runs `serve/server.py --engine strata`, which spawns the
custom `engine/strata --serve` process. Its cache behavior must not be inferred
from the bundled llama.cpp HTTP server's slot-selection implementation.

In the pinned custom engine, `src/program/generate.cpp` resumes only when the
new token sequence starts with a complete saved session/checkpoint prefix
(`starts_with`, `choose_prefix`, around lines 6418–6440). Checkpoints include
turn boundaries; a system-root checkpoint is saved only if it meets the
`prompt_cache_root` threshold (default 2,048 tokens, around lines 6981–6991).
If no saved prefix matches, reuse is zero and the session is reset. Different
suffixes after a matching checkpoint are supported; arbitrary shared text
before an unsaved position is not enough to establish reuse.

This is consistent with identical probe requests reusing a turn-boundary
checkpoint while changed Aider file context fails to match one. It is not
proof of the exact checkpoint miss in the earlier smoke, since no private
tokenized prompts or checkpoint contents were captured. No cache thresholds,
parking limits, reasoning placement, or inference settings were changed.

### Optional repository configuration validation

The real source CLI was rerun with
`aider_edit_files = ["calculator.py", "test_smoke.py"]` at fixture baseline
`a648df7d7c402f61c41ce3a88eb46f6e0bd9693e`. Run
`run_1791145561_9634b0d7` passed in one attempt (21.306 s), with one model
request and only the authorized source/test files changed. Independent
inspection confirmed the helper and all three new input cases; four unittest
methods passed independently. This patch splits the cases into separate test
methods and is not byte-identical to the earlier diagnostic patch.

The model request had 2,302 prompt tokens, zero reuse, 8,950 ms prompt time,
6,337 ms decode time, and 418 output tokens. The implementation passes
validated existing committed paths from the isolated checkout; omitted
configuration preserves existing behavior. It does not parse targets out of
spec prose or enforce an editable-file allowlist. Native parity and general
cache tuning remain unverified. The installed CLI/MCP is still unchanged.

Final source validation: 400 tests passed, 12 skipped for Windows Job Object,
symlink privilege, or POSIX-only permission limitations. Ruff lint/format
checks passed; mypy reported no issues in 20 source files. Independent
read-only review found no confirmed defect. Runtime regression coverage
includes committed-but-modified targets, invalid spec/untracked/directory/
traversal targets, and isolated-checkout file forwarding. The new real
symlink regression remains unverified on this host because creating symlinks
requires a privilege this runner lacks.

Installed-tool refresh is deliberately deferred while two `hybrid-sdlc.exe`
processes are active under the Codex parent process. Close Codex before
running `uv tool install --force` against the source worktree from a normal
PowerShell window, then reopen Codex to establish a new MCP session. No
running client was killed and no project commit or push was performed.

## Installed MCP verification after tool refresh

The operator subsequently stopped the old clients and reported a successful
tool installation. The installed CLI model check passed. A fresh SDK stdio
connection launching `C:\Users\ray\.local\bin\hybrid-sdlc.exe mcp` discovered
all five tools, passed `check_local_model`, and ran `run_spec_task_sync`.
Run `run_1791148535_3b03da02` succeeded in one attempt (19.707 s), with only
`calculator.py` and `test_smoke.py` changed and no commit. Independent patch
inspection and a two-test rerun passed. See `installed-mcp-smoke-20261004.json`.

The initial call through this existing Codex chat returned `Transport closed`.
The fresh SDK connection proves the installed server works, but the chat's
connection still needs reinitialization. Its reconnect is not counted as
verified. The test used the existing development Python on PATH for the
fixture test profile, so it does not establish clean-machine readiness.
