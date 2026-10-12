# Security review and trust boundaries

Review date: 2026-10-12. Reviewed source baseline:
`771a59c332565ef6f3c51de1360fcc5ac08af00d` (PR #11).
HSDLC-067 is complete for this bounded review and the verified remediation below.
This is a bounded code/test review, not a penetration test or proof that no
vulnerabilities exist. No live model or lifecycle operations were used.

## Trust model

Use this toolkit only with repositories, test/build commands, inference
endpoints and host executables you trust. A named profile is permission to run
its program, not an allowlist of safe behavior inside that program. Python,
Git hooks and approved repository scripts can execute arbitrary user-level
code. The inherited host `PATH` and explicitly chosen Aider interpreter are
trusted inputs; resolving a command to an absolute path does not authenticate
its publisher or prevent later replacement by a same-user process.

Model-generated edits, MCP arguments, path inputs and unexpected process
output require validation and review. Isolated Git worktrees, advisory locks,
environment scrubbing and process groups/Job Objects are policy and accident
boundaries, **not an OS sandbox**. They do not restrict a repository command's
filesystem/network access or defend against hostile same-user processes
concurrently modifying paths. Use a separately configured OS sandbox/account
for untrusted code; do not infer one from Docker hosting the model server.

## Initial finding and verified remediation: sensitive content in child argv

At the reviewed baseline, `ServerCandidateConfig.validate_url` accepted HTTP(S)
URLs containing userinfo and query values. `build_aider_argv` put the endpoint
URL directly after `--openai-api-base` and task instructions after `--message`.
Those arguments were passed to the Aider child. Consequently embedded URL
credentials and instruction text could be visible to local process-inspection
tools while the child runs. Visibility depends on OS/account permissions.
Run-record redaction does not protect transient argv.

An offline reproduction using synthetic userinfo, query token and instruction
confirmed URL acceptance and the presence of all three in the constructed argv.
No real credentials were used and no child/model request was needed.

Remediation implemented and validated locally:

- A shared endpoint validator rejects userinfo, query/fragment delimiters,
  control characters and malformed HTTP(S) endpoints. It does not silently
  strip authentication or rewrite query semantics. Config, overrides and direct
  execution/probe paths use this contract; errors must not echo invalid inputs.
- Task instructions use a private temporary UTF-8 `--message-file`, outside
  checkout and retained artifacts, scoped to one attempt. The empty `--message`
  argument intentionally clears Aider's ambient message setting; it contains no
  instruction text. Explicit UTF-8 overrides ambient encoding.
- The installed Aider 0.86.2 parser was checked offline with ambient
  `message` and `encoding` settings: command-line clearing/file selection and
  UTF-8 precedence passed without invoking a model. Lifecycle and entry-point
  regressions passed as part of the final acceptance evidence. Additional
  guards reject managed option aliases and abbreviations, explicitly empty
  endpoint overrides, whitespace and malformed percent escapes. Protected
  temporary-file roots include the source, checkout and entire retained
  attempt directory; overlap is rejected before writing instruction text.

Normal exception handling removes temporary instruction files. Abrupt host or
parent termination can leave temporary data; private filesystem permissions do
not prevent the same user or a privileged process inspecting that file or
process memory. This change reduces argv exposure, not OS-level access.

Do not put credentials in inference endpoint URLs or secrets in task
instructions. URL-embedded authentication is now rejected; the ordinary local,
credential-free endpoint is unaffected.

## Reviewed boundaries and existing evidence

| Area | Implemented boundary | Evidence / limitation |
| --- | --- | --- |
| Path inputs | Canonical repository identity, confined resolved paths, Windows ADS rejection | `security.py`; `tests/unit/test_security.py`; symlink privilege limits remain explicit |
| Executables/environment | Explicit argv, `shell=False`, resolved profile executable, minimal child environment | `command_profiles.py`, `security.py`; profile allowlists intentionally permit selected environment values; host PATH is trusted |
| Child processes | POSIX session/group ownership; Windows child suspended until owned Job Object assignment; bounded cleanup | `processes.py`, `tests/unit/test_processes.py`; restrictive Windows hosts can reject async breakaway |
| Worktree mutation | Unique attempt storage; canonical path, record, registration and detached-checkout identity revalidation | `worktrees.py`, `tests/integration/test_worktrees.py`; no source-checkout reset/clean or automatic patch integration |
| Artifact persistence | Confined atomic JSON writes, recognized-pattern redaction, bounded summaries and metadata-only timeout evidence | `artifacts.py`, `timeout_diagnostics.py`, privacy tests; arbitrary secret detection is not guaranteed |
| Retention | Confined `.hybrid_sdlc` traversal and per-file writer locks; protected lock names | `clean_artifacts`; dry-run first; not an automatic cleanup of all external worktree/cache storage |
| Host/plugin setup | Explicit registration, conflict refusal, preserved unrelated entries, no-clobber proposal publication | `host_setup.py`, host/plugin package tests; installation is not authorization to overwrite another server |
| Runtime lifecycle | Managed ownership identity, scoped Compose operations, cooperative activity gate; model volumes preserved | `runtime_strata.py`; manual containers are not adopted; unrelated clients are not tracked |

Exact `result.patch` exports deliberately retain original bytes, including
binary/private source and possible secrets; redacting them would change what
applying the patch means. See [worktree results](worktrees.md). External Aider
map caches and retained worktrees also contain repository-derived data.
Keep that storage private and review before sharing. POSIX artifacts use
restrictive modes where implemented; Windows inherits the user's directory
ACLs rather than establishing a separate account boundary. Do not assume
`clean` removes retained external patches/caches or model volumes.

## Validation performed

- Offline targeted batch: **89 passed, 4 skipped** in 29.37 s. Modules:
  unit security, artifacts, processes, host setup, Codex plugin package, and
  worktree integration.
- Skips: three Windows directory-symlink privilege cases and one POSIX mode-bit
  case. These are unverified on this host, not passes.
- Additional executable-policy, artifact-privacy and timeout-evidence batch:
  **54 passed** in 13.08 s. Combined targeted evidence: 143 passed, 4 skipped.
- Two independent read-only code audits covered repository/process and
  artifact/host/runtime paths. Parent verified the argv finding independently.
  These initial audit batches preceded the production remediation.
- Final full suite on Windows Python 3.12.14: **786 passed, 14 skipped**, with
  152 subtests passed, in 201.65 s; total branch coverage **82.98%**.
  Skips retain restrictive Windows Job Object, symlink privilege and POSIX
  permission limits, plus the optional packaging prerequisite; they are not
  verified behavior.
- Fresh wheel, source distribution and source-rebuilt wheel were installed
  into disposable environments: **8 packaging checks passed**, independently
  of the ordinary suite's packaging skip. The user's installed CLI was not
  replaced.
- Ruff formatting (106 files), lint and strict mypy (26 source files) passed.
  Local links and `git diff --check` passed. Independent re-review verified
  the empty-override, malformed-URL, abbreviated-option and protected-temp-root
  fixes with no remaining actionable gap in those reviewed areas.
- No live inference, service lifecycle change, user-host registration change,
  new remote CI result, commit or push was performed for this remediation.

The remaining release checklist includes clean Windows and POSIX installation
rehearsals. Unsupported-host background survival and comparative hardware
benchmarks remain separate from this security review.
