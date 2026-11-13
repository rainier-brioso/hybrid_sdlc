# Native owner recovery and small Kotlin edit — 2026-11-13

## Lifecycle change and live recovery

The native runtime's status command returned exit 6 with schema version 1,
`ok: false`, `state: stale`, `error: owner_host_lost`, and the registered
profile. Hybrid SDLC previously discarded this response as a generic error,
blocking Strata's supported recovery before it could run.

The adapter now recognizes only that exact unsuccessful status response and
reports `status: stale`, `error_code: owner_host_lost`, and
`recovery_available: true`. Only explicit `hsdlc start strata` may recover it.
Strata's own `runtime start --profile` verifies the old lease and child records
and handles verified leftovers. The adapter does not delete private records,
signal saved PIDs, or bypass task leases or lifecycle uncertainty. Other stale
states, malformed/partial responses and stop/restart from stale ownership fail
closed. Failed recovery retains the existing uncertainty sentinel.

Using the feature worktree's editable CLI, explicit start recovered the
registered native `hsdlc-coder-128k` profile. Post-start ownership, generation
readiness and health checks passed. A separate bounded readiness request also
passed for `qwen3.8-flash-next-coder-iq1_m` at the registered loopback endpoint.
The preset remained 131072 context. No configuration/model download or Docker
fallback occurred. Final runtime state was running/ready with no lifecycle
uncertainty; the global installed CLI was not replaced.

Validation on Windows/Python 3.12: 1171 passed, 14 environment/opt-in skips,
82.33% coverage, in 262.48s. The focused lifecycle run passed 205 tests with
one symlink-privilege skip. Formatting, lint, mypy, mirrored-skill checks and
`git diff --check` passed. An initial restricted-runner test attempt hit long
Windows temporary paths and subprocess limits; the complete passing run used
a short temporary directory outside that runner. Independent source review
found no material correctness or safety issues.

## Synthetic existing-function trial

The disposable committed fixture had an existing Kotlin function converting
grams to kilograms and two passing positive tests. The worker had to clamp
negative inputs to zero and add zero, negative and `Int.MIN_VALUE` tests.
Only the existing source and test file were allowed. Candidate verification
also compiled five immutable acceptance tests for zero, negative, minimum,
fractional positive and maximum integer inputs. No Metria files were involved.

Request settings remained temperature 1.0, repetition penalty 1.05, recent
window 256, max output 16384 and reasoning budget 1024. One attempt was allowed,
with 1800s task, 600s attempt and 180s compiler/test-profile limits. The source
fixture remained unchanged; execution and review verification used detached
toolkit worktrees. The feature CLI included the sampling change from PR #22.

| Evidence | Observed result |
| --- | --- |
| Task | `SMALL-KOTLIN-01` |
| Run | `run_1794608962_c7cd96d9` |
| Baseline | `f8cd2430fd89044d8bd87ec37a7f7e2dc5138349` |
| Duration | 25.77 seconds |
| Attempts | One, successful |
| Edits | `src/Weight.kt`, `test/WeightTest.kt`; +4 / -1 |
| Kotlin compiler | 2.3.10, successful |
| JUnit4 | 10 tests passed: five worker-suite tests and five fixed acceptance tests |
| Patch SHA-256 | `a1fb190837f9a2d0cfc4fcf365553890b7c27dc797eb74406f0c3c2ccabceb77` |
| Model review | Accepted, revision 1, current against the exact artifact |
| Fresh review verification | Passed in 4.49s; candidate patch unchanged |

The generated implementation was
`return if (grams > 0) grams / 1000.0 else 0.0`.
The worker preserved the existing tests and added all three requested tests.
Review confirmed the exact patch's scope, syntax and behavior. The immutable
acceptance cases additionally exercised fractional conversion and integer
boundaries without overflow. Reviewer model: `gpt-6.1-sol`; worker model:
`qwen3.8-flash-next-coder-iq1_m`. Acceptance was a persisted model judgment,
not user approval or integration into an application repository.

The bounded engine log snapshot showed a 2604-token prompt generating 1025
tokens, followed by a 3646-token prompt (3628 reused) generating 334 tokens.
These were below the 16K ceiling. This snapshot is not a complete raw response
capture or a controlled sampling comparison.

This demonstrates one valid small Kotlin edit with compilation, test execution,
and fresh artifact verification after lifecycle recovery. It does not establish
reliable larger Kotlin generation or resolve the earlier rejected temporal
parser trial. Keep the larger task bounded and validate every resulting patch.
