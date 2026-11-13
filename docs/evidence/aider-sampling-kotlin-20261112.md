# Explicit request sampling and Kotlin trial — 2026-11-12

## Library behavior

The `codex/aider-request-sampling` change starts from merged main `4254887`.
Optional `[aider_sampling]` values are validated and forwarded through synchronous
CLI, MCP and async worker execution into Aider's temporary request settings.
No sampling defaults, budgets, edit formats, lifecycle behavior or acceptance
rules are changed. Provider-specific repetition/window fields are merged into
`extra_body` with the reasoning budget. Arbitrary request fields are not allowed.

Regression validation on Windows/Python 3.12: 1155 passed, 14 environment/opt-in
skips, 82.07% coverage. Ruff format/lint, mypy and diff checks passed. Coverage
includes configuration bounds, default omission, temporary-file cleanup,
CLI/MCP/worker forwarding and a real synthetic subprocess reading the generated
settings in an isolated checkout.

The real installed Aider 0.86.2 also loaded a generated settings file in a local
request-construction audit. Its completion function was intercepted, so this
audit sent no inference. Observed fields were exactly temperature 1.0,
max_tokens 16384, and extra_body containing reasoning_budget_tokens 1024,
repetition_penalty 1.05 and penalty_last_n 256. This verifies request construction;
it is not a provider quality or reliability measurement.

## One live bounded trial

The feature checkout's editable virtual-environment CLI executed one synthetic
Kotlin task; the normal globally installed CLI was not upgraded with unreviewed
code. No Metria files, model preset or service configuration changed.

Model: native Strata `qwen3.8-flash-next-coder-iq1_m`, existing 131072 context
preset. Request output remained capped at 16384, reasoning at 1024. Explicit
sampling was temperature 1.0, repetition penalty 1.05 and recent window 256.
Repository map was disabled. One bounded Aider attempt was allowed; task timeout
1800s, attempt timeout 600s, compiler/JUnit profile timeout 180s. Aider can make
multiple internal reflection requests within one bounded process attempt.

The committed disposable fixture contained a fictional temporal-parser contract,
a two-file scope policy and an immutable offline compiler harness. Cached Kotlin
2.3.10, JDK 21, JUnit 4.13.2 and Hamcrest were used without downloads. The baseline
compiled and ran a small fixed Kotlin smoke program. Candidate verification
required both intended Kotlin files, successful compilation and at least six
JUnit tests. No application source, member notes or device data was involved.

| Field | Observed result |
| --- | --- |
| Run | `run_1794438056_916a26e6` |
| Task | `SYNTHETIC-TIME-00` |
| Fixture baseline | `7134c7dfaab6b0ba3291a09e84dcaed808635ce3` |
| Overall duration | 45.05 seconds |
| Applied changes | Only `src/Temporal.kt`, 93 added lines |
| Missing output | `test/TemporalTest.kt` |
| Runner result | `RETRY_EXHAUSTED`, one attempt |
| Candidate profile | Exit 1: both intended Kotlin files required |
| Independent source-only compile | Exit 1: invalid Kotlin and unresolved APIs |
| Exact patch SHA-256 | `d287987e1ca61797f3879b5d6df793ad1894de6a030792eea3ea9657e972393a` |
| Persisted model judgment | Rejected, revision 1; current against the exact patch |

The independent compile checked the generated file unchanged. Examples of real
compiler failures include `private const DATE_RE` without `val`, Java-style
`private static int` declarations, unresolved `Invalid`/enum references, and
nonexistent date APIs. The required package declaration was also omitted.
The host did not repair the code or supply missing tests.

The last three server generation records associated with the attempt ended
normally (`stop, cancel=False`) at 2434, 481 and 679 tokens, all below the 16K
ceiling. No comparable runaway import repetition was visible in the applied
source. This is not proof that all response text was repetition-free: raw
response capture was not enabled for this trial.

Model review provenance: worker `qwen3.8-flash-next-coder-iq1_m`, reviewer
`gpt-6.1-sol`. No candidate acceptance or integration occurred. Final managed
runtime activity was idle. Synthetic artifacts remain in the operator's local
trial repository and retained worker checkout; no model response or member
content is included here.

## Interpretation

This trial proves configurable sampling reached Aider and at least one edit
block was applied. It does not demonstrate valid Kotlin, complete task output,
feature correctness or reliable coding. Compared with the earlier greedy-loop
reproduction, the observed response ended below its allowance, but the fixture
also added explicit Kotlin/compiler requirements. A single sample is not a
controlled reliability comparison or a sampling-only causal claim.

Keep the 16K output ceiling for investigation. Do not treat the absence of a
token-limit finish as success or retry the Metria feature on that basis alone.
Further model/prompt/edit-format comparisons should use the same offline
compiler gate and count complete correct patches. Higher output limits do not
repair the syntax errors or missing test file observed here.

## Follow-up status — 2026-11-13

A smaller existing-function Kotlin trial was proposed, keeping the same
sampling and 16K output ceiling. It was not submitted: the registered native
Strata endpoint was unreachable, and its own status command returned exit 6,
state `stale`, error `owner_host_lost`. Its suggested recovery was
`runtime start will recover after the owned child exits`. Managed task leases
were empty, but native runtime ownership was unresolved. No restart, second
instance, inference or automatic retry was attempted. Recover and verify the
existing managed service before this follow-up; this is not another model
quality measurement.

The focused sampling regression tests were rerun: 19 passed. Formatting, lint
and type checks also passed. The full-suite figures above remain the recorded
implementation validation; the full suite was not repeated for this evidence
update.
