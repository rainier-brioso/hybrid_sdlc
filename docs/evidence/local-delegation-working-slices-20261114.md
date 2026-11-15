# Local Strata task-sizing evidence — 2026-11-14

These trials inform how to prepare bounded tasks for this local model. Each is
one observation, not a controlled comparison of prompts, scaffolding, sampling
or context. The work and task scope changed between trials, so the results do
not establish a cause, general coding reliability or a recommendation to change
model defaults.

## Observed trials

| Trial | Task shape | Outcome |
| --- | --- | --- |
| Synthetic Kotlin smoke | Small edit to one existing function plus tests | Success in 25.77s; both files changed; Kotlin compiled and 10 tests passed. |
| Synthetic temporal parser | Asked for a new 2-file Kotlin API and parser without an existing compiling implementation | Rejected after one attempt: 93-line source was invalid and the required test file was absent. |
| Metria Task 00 | New temporal API/parser and tests in one bounded task | Failed after two attempts in 167.41s. Applied source used nonexistent Kotlin/Java time APIs and invalid syntax; test file was missing. Exact patch rejected. |
| Metria Task 00A | Host-prepared compiling type/dispatch/test scaffold; Aider received both existing files via `aider_edit_files`; one date-only method and added tests | Success in 130.96s on the first attempt. 801 tests and Android debug assembly passed. Fresh exact-patch verification passed in 156.86s; patch accepted at review revision 1. |
| Metria Task 00B | Existing-method timestamp implementation with required tests | Source method compiled, but the patch omitted the required test-file edits and the 4–6 contract tests. The existing 801-test suite and debug assembly passed with no test-count change; the candidate was rejected as incomplete. |
| Metria Task 00C | Explicitly authorized tests-only follow-up against a provisional baseline pinned to Task 00B's production source hash | Test file only: added six timestamp test methods (113 lines) while preserving the original 10; 807 tests and debug assembly passed in 134.11s. Production source hash stayed unchanged. Fresh verification passed in 159.49s; tests-only artifact accepted at revision 1, with no feature integration. |

The successful real-project task retained the existing 16,384 output ceiling
and 1,024 reasoning budget, temperature 1.0, repetition penalty 1.05, recent
window 256 and 131,072 Strata context. It changed only an existing method and
tests against a passing scaffold baseline. The feature is still incomplete:
exact-timestamp parsing is deferred. The accepted artifact was subsequently
applied to the separate feature worktree and passed the integration gate:
801 tests and debug assembly in 20.69s. No storage, JSON, notes or UI behavior was
implemented by this task.

Task 00A provenance: run `run_1794611897_055e2ab3`, baseline
`7f6eab184c8c1a16666b27752b328b22864f05ed`, exact exported patch SHA-256
`44c0efc1797cb0941edb70fb34a4fd6c0ecd8601be2685cae137c4211862d8cf`.
The later integration check is separate from model acceptance and does not
establish complete feature behavior or physical acceptance.

Task 00B provenance: run `run_1794613119_9c7a6bfe`, baseline
`a63c19c6649b83fca93a172d1a9856ef10209d55`, one attempt in 106.80s, exported
patch SHA-256
`f5aafde2b45df544aa751651b159d7a12539a096a082654b1d35e08047362b83`. The
timestamp method's compile success and green baseline suite did not establish
the new behavior because required assertions were absent. The patch was rejected
for this contract gap; no implementation was integrated from Task 00B. The
date-only behavior from Task 00A was separately integrated and committed as
`0608672`, with 801 tests and debug assembly passing in 20.69s.

Task 00C provenance: run `run_1794614157_881b7557`, baseline
`ccae15dd588016d4a97564c7e2595f6c0465616f`, isolated branch
`codex/weight-import-timestamp-tests`, one attempt in 134.11s, full test patch
SHA-256 `1a71cfa24eeda032d1d3f6c8e24ff82ce27d9c216acc647d76fddce4f055ad01`.
It changed only the test file (+113 lines), adding six methods and retaining
the original ten. Production source hash
`592fb1a2691e7669a162e008705a008e53199eabbdc97f9d8397fb0d76f5e487` was
unchanged; 807 tests and Android debug assembly passed. The candidate and its
new contract were explicitly authorized for provisional isolated verification
after Task 00B was rejected. Independent review found no issues. Fresh
verification passed in 159.49s with the exact test patch unchanged; the
tests-only review is current/accepted revision 1. This does not retroactively
accept Task 00B or integrate either artifact into the feature checkout.
This one result does not establish that tests-only
recovery is generally reliable or appropriate without explicit authority.

The member subsequently authorized combined integration of the retained
timestamp source and exact accepted tests into the separate feature worktree.
Both artifact digests and the combined patch check passed. Its full integration
gate passed 807 tests and Android debug assembly in 19.91s, with final source
digests preserved and main unchanged. The integration remains uncommitted;
the original Task 00B review remains rejected. This operational pickup is
separate from tests-only model acceptance and is not full feature acceptance.

The two Metria task shapes shared the model, hardware, token ceilings, sample
settings and offline Android profile, but they differed in source context,
scaffold, task size and number of requested behaviors. The outcome supports
trying a compiling scaffold, explicit file context and one existing-method
change for the next bounded slice. It does not show that sampling caused the
success or guarantee that future Kotlin tasks will pass.

## Hybrid SDLC completion gate

The follow-up implementation adds optional `required_changed_paths` to the
explicitly selected task policy. A source-and-tests task can declare both files;
the final candidate must change each declared path and retain a confined regular
file there. Unchanged files, deletion and rename-away sources do not satisfy
that declaration. The exact immutable scoped-commit tree and fresh policy-pinned
acceptance replay repeat the check. Omission returns `TASK_DELIVERABLE_MISSING`,
retains the exported patch and blocks scoped commit or acceptance as appropriate.

Synthetic Git-repository regressions reproduce a green test command with an
omitted required path, successful complete candidates, deleted/renamed required
files, immutable-tree commit rejection, CLI JSON/nonzero exit behavior and
fresh review blocking an incomplete candidate. The focused suite passed 140
tests. Independent implementation review found no material issue after adding
the acceptance-replay regression; formatting, lint and source typing passed.
The required full Windows/Python 3.12 gate passed 1190 tests with 14 platform or
opt-in skips, 82.31% coverage, in 268.12s. Formatting, lint and strict typing of
32 source files passed. The shipped skill copies are byte-identical; their
three package checks passed. Skipped Windows Job Object/symlink/POSIX cases and
the opt-in distribution gate remain unverified in this run. HSDLC-091 records
the completion evidence.

These checks apply only to selected policies with explicit declarations.
Historical no-policy trials remain historical evidence. Required file changes
do not establish assertion quality or complete product behavior. This work
updates Hybrid SDLC and its mirrored skill; Metria stays at its existing
checkpoint, with no further model runs or integration performed for this PR.

## Workflow to apply

1. Inspect actual source and tests. Choose one independently observable behavior.
2. For new APIs, prepare compiling stable types/signatures and honest fail-closed
   placeholders; retain boundary tests and identify deferred behaviors.
3. Commit that prerequisite before dependent delegated work.
4. Name intended source/test files and supply focused initial context through
   `aider_edit_files` when discovery would omit them. It does not enforce scope.
5. Keep the existing model, sampling, output/reasoning ceilings, profile and
   retry bound unless separately justified by measured evidence and authorized.
6. Review the complete patch; fresh-checkout verify the exact artifact before
   accepting it. Preserve run IDs, baseline, digest, test/build result and review.
7. After repeated compiler failures, stop and redesign the task/scaffold/context
   in a new committed contract. Do not repeat the unchanged prompt or treat
   token-limit increases as a fix for invalid syntax or missing files.
8. Check every required path and acceptance assertion against the patch. A
   passing existing suite does not prove new behavior if the requested tests or
   implementation are missing. Reject/block incomplete work instead of
   integrating or automatically retrying it. Test-count changes are only an
   indicator; a revised existing test can keep the count unchanged.
9. If a compiling production patch omitted required tests, preserve that exact
   candidate and review it. A separate tests-only follow-up may be considered
   only with explicit setup authority and an isolated provisional baseline
   pinned to the exact production source hash. Do not auto-commit rejected
   production code or retroactively accept the original run. Keep the
   follow-up isolated through review and fresh verification; if new tests
   expose a defect, report it without changing production code just to pass the
   tests-only gate. This is an optional recovery path, not an automatic retry.

The review and acceptance records remain in the trial repository's private
`.hybrid_sdlc` artifacts. This public guidance contains only synthetic task
descriptions and aggregate results; it includes no member data or user paths.
