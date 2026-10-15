# Checked task execution policy

HSDLC-073 design decision (2026-10-14): use an explicitly selected, optional
JSON file, separate from the human-readable committed task contract. Do not
discover sidecars or extract instructions from arbitrary Markdown. Existing
calls without a policy keep their legacy behavior.

## Version 1 contract

The CLI selects it with `--policy-file`; sync and async MCP select it with
optional `policy_path`. Paths are relative to the verified repository root.
Both the policy and task contract must be regular, confined, committed files
whose content matches the selected baseline under Git's text normalization.
Provenance hashes use committed blob bytes, so checkout line-ending conversion
does not change identity. Unknown keys, duplicate JSON
keys, unsupported versions, non-finite numbers, coercible wrong types and
oversized policy input (over 64 KiB) are rejected with structured policy errors.

```json
{
  "schema_version": "1.0.0",
  "task_id": "IMPORT-001",
  "spec_path": "docs/tasks/import-parse.md",
  "test_profile": "unit",
  "intended_paths": ["src/import_parser.py", "tests/test_import_parser.py"],
  "excluded_paths": ["src/storage.py"],
  "limits": {
    "max_retries": 2,
    "task_timeout_seconds": 600,
    "attempt_timeout_seconds": 180,
    "aider_max_tokens": 4096,
    "aider_reasoning_budget_tokens": 1024
  }
}
```

Task ID, contract path and profile must match the request exactly after
repository-relative contract-path resolution. Intended paths are a nonempty
list; exclusions may be empty. Version 1 JSON path fields accept only case-sensitive literal
repository-relative file paths with `/` separators. No globs, directory rules,
absolute paths, backslashes, drive prefixes, empty/dot/traversal components or
control characters are supported. Duplicate entries and a path appearing in
both lists are rejected. New files need not exist yet. This deliberately small
language avoids platform-dependent glob interpretation; broader patterns need
an explicit future format decision.

Limits confirm ceilings, not overrides: existing configuration/request values
remain effective and must not exceed the policy. Retry and task/attempt timeout
ceilings are required. Optional output/reasoning ceilings may be null or absent
(no additional ceiling); a finite ceiling requires an explicitly configured
effective value, since an unspecified server default cannot be checked. Zero
reasoning remains valid. The existing output-reserve validation still applies.
Selecting a policy never silently changes configuration, model, endpoint or
requested limits. `max_retries` retains the existing meaning: maximum attempts.
The policy's retry ceiling is an integer from 1 to 10; task timeout is 10–3600
seconds and attempt timeout is 5–1800 seconds, matching the configuration
ranges. Output ceilings are positive integers; reasoning ceilings are
nonnegative integers. These integers are not coerced from strings or floats.
These are configuration checks, not a guarantee of total elapsed time or token
cost. Existing timeout semantics remain unchanged; baseline and attempt test
commands use the named profile's separate subprocess timeout. Policy-aware
direct Python calls must supply that actual configured profile, not merely
reuse its name on a different command definition.

For example, after committing the contract and policy:

```sh
hybrid-sdlc run-task docs/tasks/import-parse.md --repo-root . --task-id IMPORT-001 --test-profile unit --policy-file docs/tasks/import-parse.policy.json --json
```

The same `--policy-file` option is available on `submit`. For MCP, add
`"policy_path": "docs/tasks/import-parse.policy.json"` to the existing
`run_spec_task_sync` or `submit_spec_job` arguments; it is optional, not a new
tool. Policy selection is repository-relative even if the task contract is
supplied as a confined absolute path.

## Validation and provenance

Validate before endpoint probing, async job allocation/reservation, synchronous
endpoint leases and worktree creation. Revalidate at runner entry and against
the actual isolated baseline before running commands. Persist the selected
policy's repository-relative path, SHA-256, contract SHA-256 and baseline commit
with new policy-aware jobs/results. Async workers must reject changed baseline,
contract or policy before execution; old job records without this optional
metadata remain readable. These are consistency checks, not protection against
hostile same-user filesystem races or forged local artifacts.

The recorded fields are additive: `policy_path`, `policy_sha256`,
`spec_sha256` and `policy_baseline_commit`. No-policy records leave them null.
Runtime/service configuration and external model defaults are not captured or
made immutable by these task-input hashes.

## Exported patch scope

With a selected policy, every path in the final exported candidate must appear
in `intended_paths` and must not appear in `excluded_paths`. Comparisons use
exact case-sensitive Git path names, not directory prefixes or globs. A rename
requires both its old and new paths to be permitted. Deletions, binary changes,
mode changes and non-ignored new/generated files count too. This is independent
of Aider's initial edit-context files.

The path declarations come from the pinned committed policy before repository
commands run. Editing a policy inside the isolated checkout cannot broaden the
allowed scope. The final export is checked after baseline/attempt tests, so
changes introduced by tests are included. An optional scoped commit separately
checks the exact staged tree against the baseline under the worktree operation
lock before creating the commit.

Non-ignored tool-generated files count just like source edits. Configure an
approved cache-isolated tool setup when needed; do not automatically widen the
policy or hide unexpected files to make a rejected run pass.

An out-of-scope candidate produces `TASK_SCOPE_VIOLATION`, with violating path
details and a failure result even if tests passed. The full private candidate
patch and worktree recovery record remain available; no patch is applied to the
source checkout. The explicit `--rollback-on-failure` option may discard the
isolated checkout's changes only after saving review evidence. A late commit
scope rejection also refreshes the candidate evidence before returning failure.
Without a selected policy, legacy execution behavior remains unchanged.
If execution had already timed out, failed or been cancelled, the scope failure
also retains the prior status and structured failure details.

## Boundary

Scope checks cover exported changes, not all filesystem activity. Ignored
untracked files are not part of the export; tracked files still count even if
an ignore rule matches them. Repository/test commands remain trusted and can
write during execution, including outside the checkout with user privileges.
Advisory locks coordinate toolkit operations, not arbitrary concurrent writers;
these checks are not an OS sandbox or protection against hostile same-user races.

Passing scope and tests is execution evidence, not recorded independent review,
product acceptance or integration. The explicit [review workflow](review-acceptance.md)
records a decision and rechecks the pinned scope when verifying acceptance;
a policy-aware successful run is not automatically accepted or merged.
Historical run records are not retroactively scope-checked by an upgrade.
