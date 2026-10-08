# Strata validation checklist

Use this checklist after the current model download finishes. It records only
observations from this validation run; do not fill in results in advance.

## 1. Confirm the service is ready

In PowerShell, enter the repository worktree first:

```powershell
Set-Location 'C:\path\to\hybrid_sdlc' # Replace with your checkout location.
$base = 'http://127.0.0.1:8080'
$health = Invoke-RestMethod "$base/health" -TimeoutSec 10
$health | ConvertTo-Json -Depth 10
if ($health.loaded -ne $true) { throw 'Strata is not loaded; defer inference checks.' }

$models = Invoke-RestMethod "$base/v1/models" -TimeoutSec 10
$models | ConvertTo-Json -Depth 10
```

Check the actual model ID in the model-list response against the configured
candidate `qwen3.8-flash-next-coder-iq1_m`. Preserve the full response as
evidence; do not infer readiness from container state alone.

Check Hybrid SDLC's endpoint/model probe after `loaded` is true:

```powershell
hybrid-sdlc check --repo-root . --json
```

The default CLI check probes model availability. It is not a coding-task
evaluation, and the optional readiness probe may time out on cold inference.

## 2. Inspect the mounted defaults

The startup-mounted `config/strata/worker-defaults.json` specifies
`reasoning_effort: low` and `max_tokens: 4096`. Do not add
`reasoning_budget_tokens` to this shared JSON: the pinned Strata release rejects
that setting there and discards the shared defaults. Its reasoning budget is
configured in the per-model file
`/data/config/strata-coder-iq1_m.json`; the shipped startup hook adds `1024`
only when the key is absent, preserving explicitly configured values. Existing
running or legacy managed services are not automatically upgraded.
Request-level values override that model default, and `0` disables the
budget. The budget is a wrap-up threshold, not an exact cap on reasoning
tokens. Strata documents `GET /props` as exposing configured generation
defaults, with shared settings taking precedence. Inspect and record the full
response after startup before interpreting fields. Its nested response shape
is not assumed here:

```powershell
$settings = Invoke-RestMethod "$base/settings" -TimeoutSec 10
$settings | ConvertTo-Json -Depth 20
$properties = Invoke-RestMethod "$base/props" -TimeoutSec 10
$properties.default_generation_settings | ConvertTo-Json -Depth 10
```

Verify `/settings` reports `defaults.reasoning_effort` as `low` and
`defaults.max_tokens` as `4096`. Confirm the model config contains
`reasoning_budget_tokens: 1024`. On the pinned release, `/props` reports the
output cap as `default_generation_settings.params.n_predict`, but does not
expose reasoning effort there. Record the actual values. The prompt and
generated output must fit within the model context. This settings check is
separate from the inference request below, which explicitly overrides
reasoning effort to `none`.

When the model budget is enabled, startup should also report
`thinking budget: 1024 tokens`. Record any intentional override instead of
assuming the new default was applied to an existing deployment. Static and
image syntax checks do not prove that a restarted live model uses this setting.

## 3. Run one bounded API smoke request

Only do this after health reports `loaded: true` and the model ID is confirmed.
This single request uses a small, deterministic prompt, caps output at 128
tokens, requests no extra reasoning, and allows up to 120 seconds:

```powershell
$body = @{
    model = 'qwen3.8-flash-next-coder-iq1_m'
    messages = @(@{ role = 'user'; content = 'What is 17 + 25? Reply with the sum only.' })
    max_tokens = 128
    reasoning_effort = 'none'
} | ConvertTo-Json -Depth 10

$completion = Invoke-RestMethod "$base/v1/chat/completions" `
    -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 120
$completion | ConvertTo-Json -Depth 20
```

Inspect the observed response's assistant content and `finish_reason`. Confirm
the content answers `42`. Treat `finish_reason = length` as output capped by
the token limit, not as a completed response; a normal completed answer should
have a non-length completion reason (commonly `stop`). Record any other reason
verbatim and investigate it instead of calling the request successful. Keep
the full response as evidence. This request does not establish the default
`low` setting; that is verified separately in step 2.

## 4. Prepare an isolated Aider smoke (do not run during download)

When the model is ready, use a clean, isolated test repository/worktree and
point Hybrid SDLC/Aider only at the Strata candidate. Use the existing
SMOKE001 task: ask for the authorized `multiply_by_two` helper and its focused
tests, then run only those tests. Inspect the diff and test output and preserve
them as evidence; clean up the isolated checkout separately after recording
the result. Do not use a real task repository, permit unrelated edits, or
count a generated patch without the focused tests passing.

Recorded first run (2026-10-04): `run_1791139151_6f5ae9e5` passed its tests in
one attempt, but included an unrequested Aider tags-cache database. The
authorized-files-only acceptance failed on that run. Preserve this result as
evidence.

Clean rerun (2026-10-04): `run_1791140948_7f6efcb2` passed in one attempt
in 116.2 s, with exactly `calculator.py` and `test_smoke.py` changed. Independent
review confirmed the helper and zero/positive/negative assertions; the two
unittest methods passed an independent rerun. No model commit was created.
The source fixture remains clean at baseline
`7e123e6cf6d3ecde909fec78b079738c9f43e42a` and both generated worktrees are retained.

This rerun explicitly sets the top-level `aider_repo_map_tokens = 0` in the
tiny fixture configuration. It disables repository-map context and its cache;
it is not the default for normal tasks. Omitting the setting preserves Aider's
default map, and map-enabled tasks can still produce the cache. No ignore
rules were changed and no unexpected changes were filtered from the patch.

The rerun was slower than the initial 56.7 s run. Strata logs show prompt
processing around 60 s and 37 s for its two requests, followed by completed
responses at 66 s and 44 s respectively. This observation identifies prompt
processing as a substantial component, but does not establish why it varied
or attribute the slowdown to the repository-map setting or Docker.

Source validation after the opt-in setting: 376 tests passed, 11 skipped
(Windows Job Object, symlink privileges, and POSIX-only permission behavior).
Ruff lint/format checks passed and mypy reported no issues in 20 source files.
The configuration/argv propagation change also received a read-only independent
review with no material findings. These checks do not replace the remaining
native-versus-Docker benchmark or supported-host Windows survival proof.

### Monitor observations supplied by the operator

The operator reported these completed requests from Strata's monitor on
2026-10-04. These are observations from the initial run, not a controlled
runtime benchmark; the monitor's output-token count is not necessarily only
visible answer text.

| Time | Prompt tokens | Reused tokens | Output tokens | Generation tok/s | VRAM hit rate | Total duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20:37:15 | 28 | 0 | 3 | 6.8 | 73.3% | 4.9 s |
| 20:39:20 | 2,412 | 0 | 256 | 58.5 | 95.6% | 28.1 s |
| 20:39:48 | 2,529 | 0 | 447 | 75.5 | 95.7% | 18.1 s |

Generation throughput and total request duration measure different parts of
the request. Zero reused tokens indicates no reported prompt-prefix reuse,
not an absence of GPU expert-cache hits. These values alone do not establish
Docker overhead, native performance, or code quality.

## 5. Compare runtimes and models separately

A bounded synthetic prompt-reuse diagnostic is now recorded in
`benchmarks/results/strata/prompt-processing-20261004.md` and its companion
JSON record. Run `python benchmarks/strata_prompt_probe.py` to repeat that
diagnostic with no server configuration changes. It measures one new prompt
and two identical cached repeats, not three independent coding tasks or a
native-versus-Docker comparison.

An explicit editable-file-context follow-up through the source CLI also passed:
`run_1791145561_9634b0d7` (21.3 s, one model request, four test methods
independently passing). Its optional `aider_edit_files` setting supplies the
two known existing committed files at startup; it does not restrict Aider's
editing capabilities. Only those two files changed. The installed CLI/MCP
was subsequently refreshed. A fresh SDK stdio client discovered all five
tools, passed the endpoint probe, and completed `run_1791148535_3b03da02`
in one attempt (19.7 s), changing only the two authorized files; two test
methods passed an independent rerun. The existing Codex chat connection
still returned `Transport closed`. See the installed MCP and file-context evidence under
`benchmarks/results/strata/` for runtime conditions and limitations.

The user deferred the native-versus-Docker comparison until a real task
delegation provides a representative fixture (2026-10-04). Performance parity
and candidate promotion remain unverified.

For native-versus-Docker comparison, use the same Strata revision, model
artifacts, prompt, context, output limit, and reasoning effort. Run at least
three repeats per runtime and record each result separately: load time, time
to first token, generation tokens per second, peak GPU memory, system memory,
and disk activity. Keep hardware/software conditions and any differences
visible in the record. Do not claim runtime parity from one run or from the
API smoke.

If comparing Qwen, run and report it as a separate model comparison with its
own model ID and settings. Do not assume or assert parity with the Strata
Coder candidate. No comparison measurements have been collected yet.

## Download and startup note

On 2026-10-04, both model shards finished downloading. The container was
recreated with the defaults-file mount and reused the named `strata-data`
volume without redownloading the model. Loaded health, the model list, live
low-reasoning/output defaults, and the bounded arithmetic API request passed.
Recheck these properties after future configuration or deployment changes.
