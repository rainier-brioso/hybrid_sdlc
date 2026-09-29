# Hybrid SDLC Toolkit — Detailed Implementation Tasks

Source plan: [`docs/implementation_plan.md`](implementation_plan.md)

## How to Use This File

- Complete tasks in dependency order. Task identifiers remain stable when work is promoted to
  an earlier phase, so numeric order may differ from delivery order.
- Mark a task complete only after its acceptance criteria pass and evidence is recorded in the relevant pull request, commit, or run log.
- Do not weaken the trust model to make a test pass. The initial release supports trusted repositories and named command profiles; it is not an OS sandbox.
- No task may run destructive whole-tree Git commands (`git reset --hard`, `git clean`, or `git checkout .`) against the user's working tree.
- Each implementation task should produce a focused diff. If a task grows beyond the named files or acceptance criteria, split it before coding.

## Global Definition of Done

A task is complete when:

1. New behavior has unit or integration coverage proportional to its risk.
2. Tests pass on the developer platform and do not depend on a live model unless explicitly marked as hardware/manual verification.
3. Public functions and serialized schemas have type annotations and concise documentation.
4. Errors use stable machine-readable codes plus actionable human messages.
5. Logs and fixtures contain no real credentials, private paths, or unredacted secrets.
6. Formatting, linting, static analysis, and the complete automated test suite pass.

---

## Phase 1 — Core Runner and Policy Guard

### Packaging and Contracts

- [x] **HSDLC-001 — Create the Python package skeleton**  
  Files: `pyproject.toml`, `src/hybrid_sdlc/__init__.py`, `src/hybrid_sdlc/__main__.py`  
  Depends on: none  
  Acceptance:
  - Package uses a `src/` layout and declares the supported Python range.
  - `hybrid-sdlc` and `python -m hybrid_sdlc` invoke the same CLI.
  - `uv run hybrid-sdlc --help` succeeds in a fresh environment.

- [x] **HSDLC-002 — Pin runtime and development dependencies**  
  Files: `pyproject.toml`, `uv.lock`  
  Depends on: HSDLC-001  
  Acceptance:
  - Runtime dependencies include only libraries required by implemented Phase 1 behavior.
  - Test, lint, and type-check dependencies are isolated in a development group.
  - `uv sync --frozen` succeeds from the lock file.

- [x] **HSDLC-003 — Define stable error and exit-code contracts**  
  Files: `src/hybrid_sdlc/errors.py`, `src/hybrid_sdlc/models.py`, `tests/unit/test_errors.py`  
  Depends on: HSDLC-001  
  Acceptance:
  - Typed errors cover configuration, repository, policy, server, model, timeout, test, cancellation, and internal failures.
  - CLI exit codes are documented and tested.
  - Serialized failures contain `code`, `message`, and optional redacted `details`.

- [x] **HSDLC-004 — Define versioned result schemas**  
  Files: `src/hybrid_sdlc/models.py`, `tests/unit/test_models.py`  
  Depends on: HSDLC-003  
  Acceptance:
  - Run, attempt, probe, diff-summary, and failure records carry `schema_version`.
  - JSON round-trip tests cover success and every terminal failure category.
  - Unknown optional fields remain forward-compatible when records are read.

### Configuration and Command Profiles

- [x] **HSDLC-005 — Implement configuration discovery and precedence**  
  Files: `src/hybrid_sdlc/config.py`, `tests/unit/test_config.py`  
  Depends on: HSDLC-003  
  Acceptance:
  - Precedence is CLI arguments > repository `hybrid_sdlc.toml` > documented defaults.
  - Configuration loading never searches above the verified repository root.
  - Invalid keys, types, durations, ports, and paths fail with targeted diagnostics.

- [x] **HSDLC-006 — Define named test-command profiles**  
  Files: `src/hybrid_sdlc/command_profiles.py`, `tests/unit/test_command_profiles.py`  
  Depends on: HSDLC-005  
  Acceptance:
  - A profile defines `argv: list[str]`, relative `cwd`, timeout, and an environment allowlist.
  - Unknown profiles and empty argv arrays are rejected.
  - No string is parsed as a shell command.

- [x] **HSDLC-007 — Resolve and validate profile executables**  
  Files: `src/hybrid_sdlc/command_profiles.py`, `tests/unit/test_command_profiles.py`  
  Depends on: HSDLC-006  
  Acceptance:
  - The executable resolves once to an approved absolute path.
  - Missing executables and unapproved resolution changes fail before model execution.
  - Windows executable suffixes and POSIX executable permissions are covered by tests.

- [x] **HSDLC-008 — Add an example repository configuration**  
  Files: `hybrid_sdlc.example.toml`, `docs/configuration.md`  
  Depends on: HSDLC-006  
  Acceptance:
  - Examples cover pytest, unittest, npm, pnpm, Cargo, Go, and Vitest without claiming they are sandboxed.
  - Every example uses an argv array and an explicit working directory.
  - Documentation explains why Python and package-manager profiles execute trusted repository code.

### Repository and Path Policy

- [x] **HSDLC-009 — Implement explicit repository-root verification**  
  Files: `src/hybrid_sdlc/security.py`, `tests/unit/test_security.py`  
  Depends on: HSDLC-003  
  Acceptance:
  - MCP-facing APIs require `repo_root`.
  - Interactive CLI fallback uses `git rev-parse --show-toplevel` and confirms the resolved directory.
  - Missing, non-Git, nested-mismatch, and inaccessible roots fail closed.

- [x] **HSDLC-010 — Implement confined path resolution**  
  Files: `src/hybrid_sdlc/security.py`, `tests/unit/test_security.py`  
  Depends on: HSDLC-009  
  Acceptance:
  - Relative paths resolve against the verified root.
  - Traversal, alternate Windows path forms, junctions, and symlinks escaping the root are rejected.
  - Nonexistent output paths validate their nearest existing parent before creation.

- [x] **HSDLC-011 — Implement clean-worktree preflight**  
  Files: `src/hybrid_sdlc/git_tools.py`, `tests/unit/test_git_tools.py`  
  Depends on: HSDLC-009  
  Acceptance:
  - Any tracked modification, staged change, untracked file, merge state, or rebase state blocks Phase 1 execution.
  - Ignored `.hybrid_sdlc/` artifacts do not make the tree dirty.
  - The diagnostic lists categories without dumping sensitive file contents.

- [x] **HSDLC-012 — Implement repository execution locking**  
  Files: `src/hybrid_sdlc/git_tools.py`, `tests/unit/test_git_tools.py`  
  Depends on: HSDLC-011  
  Acceptance:
  - A second runner fails quickly with owner/run metadata.
  - Stale locks can be diagnosed without silently stealing a live lock.
  - Lock release occurs on success, failure, cancellation, and handled signals.

### Environment and Artifact Privacy

- [x] **HSDLC-013 — Build a minimal subprocess environment**  
  Files: `src/hybrid_sdlc/security.py`, `tests/unit/test_security.py`  
  Depends on: HSDLC-005  
  Acceptance:
  - Environment construction starts from an explicit minimum rather than copying the complete parent environment.
  - Secret-pattern variables are absent unless explicitly allowed by a trusted profile.
  - The local endpoint receives only the documented dummy API credential.

- [x] **HSDLC-014 — Implement recursive artifact redaction**  
  Files: `src/hybrid_sdlc/artifacts.py`, `tests/unit/test_artifacts.py`  
  Depends on: HSDLC-004  
  Acceptance:
  - Redaction handles environment assignments, common token formats, configured literals, URLs with credentials, and nested JSON fields.
  - Redaction is applied before persistence and before terminal summaries.
  - Tests use synthetic secrets and prove none survive serialized output.

- [x] **HSDLC-015 — Implement atomic artifact persistence**  
  Files: `src/hybrid_sdlc/artifacts.py`, `tests/unit/test_artifacts.py`  
  Depends on: HSDLC-014  
  Acceptance:
  - Writes use a same-directory temporary file, flush, and atomic replacement under a lock.
  - Readers never observe partial JSON during concurrent-write tests.
  - Best-effort user-only file permissions are applied and platform differences documented.

- [x] **HSDLC-016 — Implement retention cleanup**  
  Files: `src/hybrid_sdlc/artifacts.py`, `tests/unit/test_artifacts.py`  
  Depends on: HSDLC-015  
  Acceptance:
  - Cleanup accepts a validated duration and removes only artifacts beneath `.hybrid_sdlc/`.
  - Active jobs and locked artifacts are skipped.
  - Dry-run output and boundary tests prove unrelated files cannot be deleted.

- [x] **HSDLC-017 — Add repository ignore rules**  
  Files: `.gitignore`, `tests/unit/test_repository_layout.py`  
  Depends on: HSDLC-015  
  Acceptance:
  - `.hybrid_sdlc/`, model weights, caches, and local configuration secrets are ignored.
  - Shareable example configuration and templates remain tracked.

### Model Endpoint Probing

- [x] **HSDLC-018 — Implement OpenAI-compatible endpoint probing**  
  Files: `src/hybrid_sdlc/server_probe.py`, `tests/unit/test_server_probe.py`  
  Depends on: HSDLC-004, HSDLC-005  
  Acceptance:
  - Probe uses bounded connect/read timeouts and calls `/v1/models`.
  - Network, HTTP, JSON, empty-model, and unexpected-schema errors are distinguished.
  - Tests use local mock servers and require no GPU or internet access.

- [x] **HSDLC-019 — Implement ordered endpoint selection**  
  Files: `src/hybrid_sdlc/server_probe.py`, `tests/unit/test_server_probe.py`  
  Depends on: HSDLC-018  
  Acceptance:
  - Explicit URL takes precedence; otherwise configured candidates are tried in order.
  - Selection verifies the requested model alias.
  - Failure summarizes every attempted endpoint without leaking credentials.

- [x] **HSDLC-020 — Add optional inference readiness probe**  
  Files: `src/hybrid_sdlc/server_probe.py`, `tests/unit/test_server_probe.py`  
  Depends on: HSDLC-019  
  Acceptance:
  - Readiness request uses a fixed harmless prompt, maximum five output tokens, and a strict timeout.
  - Probe can be disabled for health checks that must not invoke inference.
  - Response content is not treated as trusted instructions.

### Aider Execution and Bounded Loop

- [x] **HSDLC-021 — Build the Aider argv constructor**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `tests/unit/test_aider_runner.py`  
  Depends on: HSDLC-005, HSDLC-019  
  Acceptance:
  - Invocation includes the selected endpoint/model, diff edit format, no auto-commit, and no shell suggestions.
  - Paths and prompt inputs are separate argv elements.
  - Unit tests snapshot argv on Windows and POSIX without launching Aider.

- [x] **HSDLC-022 — Implement bounded subprocess execution**  
  Files: `src/hybrid_sdlc/processes.py`, `tests/unit/test_processes.py`  
  Depends on: HSDLC-013  
  Acceptance:
  - Processes run without a shell in a new POSIX process group or Windows Job Object owned by the runner/worker.
  - Timeout and cancellation terminate descendants and collect bounded output.
  - Tests prove a spawned grandchild does not survive cleanup.

- [x] **HSDLC-023 — Capture the clean baseline and per-attempt diff**  
  Files: `src/hybrid_sdlc/git_tools.py`, `tests/unit/test_git_tools.py`  
  Depends on: HSDLC-011  
  Acceptance:
  - Baseline commit and initial status are recorded before edits.
  - Each attempt records diff hash, changed paths, additions/deletions, and a patch artifact path.
  - Binary and oversized diffs produce bounded metadata rather than unbounded logs.

- [x] **HSDLC-024 — Implement test-profile execution**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `tests/unit/test_aider_runner.py`  
  Depends on: HSDLC-007, HSDLC-022  
  Acceptance:
  - The selected profile runs with its configured argv, cwd, environment, and timeout.
  - stdout/stderr capture respects byte limits while preserving exit status and truncation metadata.
  - No command-string parsing occurs.

- [x] **HSDLC-025 — Implement failure-signature extraction**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `tests/unit/test_aider_runner.py`  
  Depends on: HSDLC-024  
  Acceptance:
  - Signature generation normalizes volatile timestamps, durations, and absolute temporary paths.
  - Identical failures compare equal; materially different failures do not.
  - Raw failure output is redacted before storage.

- [x] **HSDLC-026 — Implement the bounded edit/test state machine**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `tests/integration/test_bounded_loop.py`  
  Depends on: HSDLC-021, HSDLC-023, HSDLC-024, HSDLC-025  
  Acceptance:
  - Baseline tests run before the first edit.
  - The loop terminates on pass, timeout, cancellation, zero/unchanged diff, repeated failure, or retry exhaustion.
  - Default maximum is three edit attempts and 600 seconds total.
  - Phase 1 failure retains the diff; it never resets or cleans the working tree.

- [x] **HSDLC-027 — Produce a structured terminal run result**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `tests/integration/test_bounded_loop.py`  
  Depends on: HSDLC-026  
  Acceptance:
  - Every exit path creates exactly one terminal result record.
  - Result links attempt summaries and artifacts using repository-relative paths.
  - Human summary and JSON output agree on status and error code.

### Phase 1 CLI

- [x] **HSDLC-028 — Implement `check`**  
  Files: `src/hybrid_sdlc/cli.py`, `tests/integration/test_cli.py`  
  Depends on: HSDLC-019, HSDLC-020  
  Acceptance:
  - Supports explicit URL, ordered probing, JSON output, and readiness opt-in.
  - Exit status distinguishes healthy, unavailable, and invalid configuration.

- [x] **HSDLC-029 — Implement synchronous `run-task`**  
  Files: `src/hybrid_sdlc/cli.py`, `tests/integration/test_cli.py`  
  Depends on: HSDLC-012, HSDLC-027  
  Acceptance:
  - Requires spec path, verified repository root, task ID, and test-profile name.
  - Rejects a dirty tree before contacting the model.
  - `--json` emits one valid result object and no decorative output on stdout.

- [x] **HSDLC-030 — Implement `clean`**  
  Files: `src/hybrid_sdlc/cli.py`, `tests/integration/test_cli.py`  
  Depends on: HSDLC-016  
  Acceptance:
  - Supports dry-run and explicit duration.
  - Reports skipped active artifacts and deleted artifact IDs.
  - Boundary tests prove cleanup cannot escape `.hybrid_sdlc/`.

- [x] **HSDLC-031 — Establish Phase 1 quality gates**  
  Files: `pyproject.toml`, `docs/development.md`  
  Depends on: HSDLC-001 through HSDLC-030  
  Acceptance:
  - One documented command runs formatting checks, linting, type checking, and tests.
  - Coverage thresholds emphasize security, process cleanup, persistence, and state-machine branches.
  - The complete suite runs without Aider, llama-server, a GPU, or internet access.

#### Phase 1 Completion Evidence — 2026-09-09

- `uv sync --frozen` completed successfully with 29 locked packages checked.
- `uv run hybrid-sdlc --help` completed successfully and exposed `check`, `run-task`, and `clean`.
- `uv run ruff format --check .` and `uv run ruff check .` passed.
- `uv run mypy src` passed in strict mode with no issues across 13 source files.
- `uv run pytest --cov --cov-report=term-missing` passed: 97 tests, 81.47% branch coverage
  against the configured 80% threshold.
- The suite used local mocks and fake executables; no live model, GPU, or internet access was used.

#### Phase 2 Completion Evidence — 2026-09-10

- `uv sync --frozen` completed successfully with 29 locked packages checked.
- `uv run ruff format --check .` passed (41 files already formatted).
- `uv run ruff check .` passed with no errors.
- `uv run mypy src` passed in strict mode with no issues across 13 source files.
- `uv run pytest --cov --cov-report=term-missing -ra` passed: 143 tests, 82.29% branch coverage
  against the configured 80% threshold.
- Test distribution: 50 E2E (7 artifact privacy, 26 fixtures and fake aider, 6 process cleanup, 4 sync delegation, 7 sync failures), 13 integration, 84 unit.
- The suite used local mocks and fake executables; no live model, GPU, or internet access was used.
- CI workflow `.github/workflows/ci.yml` updated with:
  - Explicit `contents: read` permissions
  - `astral-sh/setup-uv@v10.2.0` action with pinned uv version 0.5.28
  - Windows/Python 3.12 quality job plus four test-only compatibility jobs covering Python
    3.11-3.13 and Windows, Ubuntu, and macOS
  - Stable `CI / Required` aggregate check job (requires both job groups to succeed)

---

## Phase 2 — CI Gate and Fixture End-to-End Validation

- [x] **HSDLC-062 — Establish protected-main CI validation**
   Files: `.github/workflows/ci.yml`, `docs/development.md`  
    Depends on: HSDLC-031  
    Acceptance:
    - Pull requests and pushes to `main` run the full suite on Windows, Ubuntu, and macOS,
      and on Python 3.11, 3.12, and 3.13 through a compact compatibility matrix.
    - Frozen dependency sync, formatting, lint, strict types, tests, and branch coverage run
      in the Windows/Python 3.12 quality job without a live model, GPU, or internet-dependent test.
    - Workflow permissions are read-only by default, redundant runs are cancelled, and the
      required check names are stable enough to bind to the protected-branch ruleset.
    - Passing PR validation: https://github.com/rainier-brioso/hybrid_sdlc/actions/runs/36259293856
      (Windows quality, Ubuntu/macOS compatibility, and `CI / Required` all succeeded).

- [x] **HSDLC-032 — Create an isolated fixture-repository factory**  
    Files: `tests/helpers/repositories.py`  
    Depends on: HSDLC-062  
    Acceptance:
    - Tests create disposable Git repositories with passing, failing, and dirty variants.
    - Fixtures never mutate the toolkit checkout.

- [x] **HSDLC-033 — Create a deterministic fake Aider executable**  
    Files: `tests/helpers/fake_aider.py`  
    Depends on: HSDLC-032  
    Acceptance:
    - Scenarios cover successful edit, no edit, repeated edit, malformed output, timeout, and child-process spawning.
    - Behavior is selected without shell evaluation.
    - Note: scenario state is stored in `.hybrid_sdlc/` (ignored by git) rather than directly in the worktree.

- [x] **HSDLC-034 — Test successful synchronous delegation end to end**  
   Files: `tests/e2e/test_sync_delegation.py`  
   Depends on: HSDLC-029, HSDLC-032, HSDLC-033  
   Acceptance:
   - A failing fixture is edited, tests pass, artifacts are recorded, and no commit is created.
   - The resulting diff contains only expected fixture files.

- [x] **HSDLC-035 — Test every bounded-loop terminal condition**  
   Files: `tests/e2e/test_sync_failures.py`  
   Depends on: HSDLC-033  
   Acceptance:
   - Separate tests cover broken baseline, unchanged diff, repeated signature, retry exhaustion, task timeout, and output truncation.
   - Each produces the documented status, failure reason, and exit code.

- [x] **HSDLC-036 — Test cancellation and descendant cleanup**  
   Files: `tests/e2e/test_process_cleanup.py`  
   Depends on: HSDLC-022, HSDLC-033  
   Acceptance:
   - Interrupting a run terminates the fake Aider, test command, and grandchildren.
   - Terminal state and lock release are verified after cancellation.

- [x] **HSDLC-037 — Test artifact secrecy end to end**  
   Files: `tests/e2e/test_artifact_privacy.py`  
   Depends on: HSDLC-034  
   Acceptance:
   - Synthetic secrets in environment variables, source text, test output, and URLs do not appear in persisted summaries.
   - Raw prompt persistence remains disabled by default.

---

## Phase 3 — MCP and Asynchronous Job Lifecycle

- [x] **HSDLC-038 — Define the persisted job-state machine**
  Files: `src/hybrid_sdlc/job_manager.py`, `tests/unit/test_job_manager.py`  
  Depends on: HSDLC-004, HSDLC-015  
  Acceptance:
  - Legal transitions are explicit and illegal transitions fail safely.
  - Terminal failures use `status=failed` plus a documented `failure_reason`.
  - Job IDs are collision-resistant and validated before filesystem access.

- [x] **HSDLC-039 — Implement the detached worker entry point**  
  Files: `src/hybrid_sdlc/worker.py`, `src/hybrid_sdlc/cli.py`, `tests/unit/test_worker.py`  
  Depends on: HSDLC-027, HSDLC-038  
  Acceptance:
  - `hybrid-sdlc worker <job_id>` claims one queued job and owns its subprocess group.
  - Duplicate workers cannot claim the same job.
  - Heartbeat and terminal state are persisted atomically.

- [x] **HSDLC-040 — Implement asynchronous submission**  
  Files: `src/hybrid_sdlc/submission.py`, `tests/integration/test_jobs.py`  
  Depends on: HSDLC-039  
  Acceptance:
  - Submission validates all policy inputs before creating the job.
  - It returns a job ID only after the worker starts or reports a launch failure.
  - The worker continues after the submitting process exits.

- [x] **HSDLC-041 — Implement status and abandoned-worker recovery**  
  Files: `src/hybrid_sdlc/job_manager.py`, `tests/integration/test_jobs.py`  
  Depends on: HSDLC-040  
  Acceptance:
  - `JobManager.status()` supports immediate lookup and finite waits capped at 60 seconds; polling intervals are validated.
  - Worker identity is persisted as an OS start token (Linux boot ID plus start ticks, Windows FILETIME); legacy records use a conservative timestamp fallback.
  - Confirmed dead/reused worker PIDs become `failed/abandoned_process` with typed diagnostic metadata; unknown OS probes preserve running state.
  - Recovery probes outside the lock and rechecks state and owner under the per-job lock before atomic persistence. `recover_running_jobs()` is available for host startup integration.
  - macOS/BSD process start times use `ps` at one-second precision, so PID reuse within the same second remains ambiguous.

- [x] **HSDLC-042 — Implement asynchronous cancellation**
  Files: `src/hybrid_sdlc/job_manager.py`, `src/hybrid_sdlc/worker.py`, `tests/integration/test_jobs.py`, `tests/unit/test_worker.py`
  Depends on: HSDLC-041  
  Acceptance:
  - Cancellation is idempotent.
  - Live process descendants terminate; terminal jobs remain unchanged.
  - A race between completion and cancellation resolves to one valid terminal state.
  - Running cancellation is persisted and observed by the worker heartbeat; the bounded subprocess runner terminates its owned process tree.

- [x] **HSDLC-043 — Implement async CLI commands**
  Files: `src/hybrid_sdlc/cli.py`, `src/hybrid_sdlc/job_manager.py`, `tests/integration/test_cli.py`
  Depends on: HSDLC-040, HSDLC-041, HSDLC-042  
  Acceptance:
  - `submit`, `status`, and `cancel` conform to the plan's canonical syntax.
  - Human and JSON output modes are tested.
  - `status` can safely list validated job records, and bounded waits require a job ID.
  - Submission diagnostics retain the allocated job ID; cancellation output distinguishes a request from termination.

- [x] **HSDLC-044 — Implement the stdio MCP server**
  Files: `src/hybrid_sdlc/mcp_server.py`, `tests/integration/test_mcp_server.py`  
  Depends on: HSDLC-029, HSDLC-043  
  Acceptance:
  - Exposes health, synchronous run, submission, status, and cancellation tools.
  - Tool schemas require `repo_root`, `spec_path`, `task_id`, and `test_profile` where applicable.
  - Untrusted tool arguments are validated through the same policy layer as CLI calls.

- [x] **HSDLC-045 — Verify MCP disconnect semantics**
  Files: `src/hybrid_sdlc/submission.py`, `tests/unit/test_submission.py`, `tests/e2e/test_mcp_disconnect.py`  
  Depends on: HSDLC-044  
  Acceptance:
  - On Linux and macOS, terminating the MCP adapter does not terminate a
    submitted worker; reconnecting and querying returns current or terminal state.
  - Synchronous calls terminate their owned process tree on adapter shutdown.
  - Before persisting a Windows async job, launch a short-lived child with the worker's
    process-creation flags and verify through `IsProcessInJob` that it escaped all
    enclosing Job Objects. Reject submission with `ASYNC_UNSUPPORTED_BY_HOST` when
    breakaway fails or cannot be verified; do not create a job record in that case.
  - The official Python MCP stdio client's default `KILL_ON_JOB_CLOSE` Job Object
    rejects async submissions because the worker cannot break away. The E2E suite
    verifies worker survival on POSIX hosts and rejects unsupported Windows hosts.
  - The Windows bounded-runner spawn-to-assignment race is resolved: children start
    suspended, enter the cleanup Job Object, and resume only after assignment.
    Deterministic Windows tests verify no child or grandchild runs before assignment
    and that shutdown/timeout cleanup terminates the process tree.
  - PR #2 CI run 36350686949 passed Linux, macOS, and Windows checks. The
    supported-host Windows survival E2E skipped on both the CI runner and a
    separate native PowerShell run because their Job Objects deny breakaway.
    Its successful execution is explicitly deferred to HSDLC-045A; this task
    does not claim Windows async-worker survival has been verified.

---

## Phase 4 — Spec Kit Integration and Idempotent Initialization

- [ ] **HSDLC-045A — Verify MCP worker survival on a supported Windows host**
  Files: `tests/e2e/test_mcp_disconnect.py`, `docs/implementation_tasks.md`
  Depends on: HSDLC-045
  Acceptance:
  - Run `test_submitted_worker_survives_stdio_adapter_disconnect_and_reconnects`
    on a Windows host whose breakaway probe succeeds; record an actual pass,
    not a skip, with the host/runner configuration.
  - Confirm the worker survives adapter termination, status can be queried
    through a new MCP connection, and the job reaches a terminal state.
  - Fix any supported-host failure before claiming Windows async support is
    verified; retain fail-closed behavior on restrictive hosts.

- [x] **HSDLC-046 — Add canonical Spec Kit templates**
  Files: `.specify/memory/constitution.md`, `.specify/templates/*.md`  
  Depends on: HSDLC-031  
  Acceptance:
  - Feature artifacts target top-level `specs/<NNN-feature>/`.
  - Templates require acceptance criteria, interfaces, test commands by profile name, and atomic tasks.

- [x] **HSDLC-047 — Define initializer ownership metadata**
  Files: `src/hybrid_sdlc/spec_initializer.py`, `tests/unit/test_spec_initializer.py`  
  Depends on: HSDLC-046  
  Acceptance:
  - Generated files carry a toolkit version/checksum in a dedicated manifest.
  - User-owned files are distinguishable from unchanged generated files.

- [x] **HSDLC-048 — Implement safe idempotent initialization**
  Files: `src/hybrid_sdlc/spec_initializer.py`, `tests/integration/test_init.py`  
  Depends on: HSDLC-047  
  Acceptance:
  - Canonical templates are available from an installed package, not only a source checkout.
  - First run creates missing files; second run is byte-for-byte idempotent.
  - Customized files are preserved unless `--force` is explicitly supplied.
  - `--force` creates a recoverable backup and reports every replacement.
  - The built wheel includes all four canonical files; source-checkout and wheel resource loading are covered.

- [x] **HSDLC-049 — Integrate installed Spec Kit capability detection**
  Files: `src/hybrid_sdlc/spec_initializer.py`, `tests/unit/test_spec_initializer.py`, `tests/integration/test_init.py`
  Depends on: HSDLC-048  
  Acceptance:
  - Detection runs the local `specify version --features --json` capability probe with captured output and a timeout; it records the executable, version, and boolean feature statuses without network access.
  - A valid machine-readable response is the compatibility floor; no unsupported minimum version or unrelated feature is assumed.
  - Missing, timed out, non-zero, or malformed probes produce actionable setup guidance before any destination, backup, or manifest write.

- [x] **HSDLC-050 — Implement and test `hybrid-sdlc init`**
  Files: `src/hybrid_sdlc/cli.py`, `tests/integration/test_cli.py`, `docs/spec-kit.md`  
  Depends on: HSDLC-048, HSDLC-049  
  Acceptance:
  - Supports target directory, dry-run, and explicit force behavior.
  - Tests cover empty repositories, existing Spec Kit projects, customized templates, target errors, JSON output, and interrupted initialization.
  - A durable journal precedes template, backup, and manifest writes; reruns reconcile exact initializer bytes, preserve intervening user edits, and retain force backups.
  - Dry-run performs the same capability, path, and ownership preflight without writing destinations, journal, manifest, or backups.
  - [Initialization instructions and recovery behavior](spec-kit.md) are documented.

---

## Phase 5 — Isolated Worktrees and Opt-In Commits

- [x] **HSDLC-051 — Implement isolated worktree creation**
  Files: `src/hybrid_sdlc/worktrees.py`, `tests/integration/test_worktrees.py`  
  Depends on: HSDLC-011, HSDLC-027  
  Acceptance:
  - Each delegated run uses a uniquely named worktree rooted at a recorded baseline commit.
  - The user's checkout remains byte-for-byte unchanged during execution.
  - Partial creation failures clean up only paths created by that attempt.
  - Evidence: tests/integration/test_worktrees.py verifies baseline isolation, unchanged
    source checkout files/status, unique worktrees, and safe cleanup after registered and
    unregistered partial creation failures.

- [x] **HSDLC-052 — Implement safe isolated rollback**
  Files: `src/hybrid_sdlc/worktrees.py`, `tests/integration/test_worktrees.py`  
  Depends on: HSDLC-051  
  Acceptance:
  - Rollback restores the isolated worktree to its recorded baseline.
  - No command targets the user's checkout or an unresolved path.
  - Tests include spaces, Unicode, symlinks, and interrupted cleanup.
  Evidence and limits:
  - Rollback validates the caller record against its persisted identity, expected
    per-repository temporary storage, and Git's linked-worktree registration before
    mutation; it restores detached HEAD, index, and tracked files to the recorded baseline.
  - Git restore and untracked cleanup target only the verified checkout. NUL-delimited
    Git paths preserve spaces and Unicode; symlink leaves are removed without following
    them, and empty directories are removed only when empty.
  - Checkout and attempt-directory identity, canonical path, and linked registration are
    rechecked before each Git mutation and untracked filesystem deletion/descent. This
    catches path replacement between workflow steps; it cannot prevent a malicious
    same-user process racing in the interval between a check and an operating-system call.
  - Repositories with submodule gitlinks are unsupported: creation rejects a baseline
    containing gitlinks, and rollback rejects gitlinks in its baseline or current index
    before mutation.
  - Tests cover forged and stale records, source-checkout preservation, spaces, Unicode,
    symlink escape, checkout path replacement, submodule rejection, HEAD drift, and retry
    after interrupted cleanup.

- [ ] **HSDLC-053 — Implement scoped commit creation**  
  Files: `src/hybrid_sdlc/git_tools.py`, `tests/integration/test_commits.py`  
  Depends on: HSDLC-051  
  Acceptance:
  - Commits are created only with explicit `--commit`.
  - Commit includes only changes produced inside the isolated worktree and references the task ID.
  - Failed tests and policy failures cannot produce a commit.

- [ ] **HSDLC-054 — Define result integration back into the user branch**  
  Files: `src/hybrid_sdlc/worktrees.py`, `docs/worktrees.md`, `tests/integration/test_worktrees.py`  
  Depends on: HSDLC-053  
  Acceptance:
  - Default result is a commit hash and patch for review; no automatic merge occurs.
  - Documentation gives explicit cherry-pick/apply steps and conflict behavior.

- [ ] **HSDLC-055 — Move the runner to isolated execution by default**  
  Files: `src/hybrid_sdlc/aider_runner.py`, `src/hybrid_sdlc/cli.py`, `tests/e2e/`  
  Depends on: HSDLC-052, HSDLC-054  
  Acceptance:
  - Existing Phase 2 and Phase 3 tests pass using isolated worktrees.
  - Failure rollback is available only for the isolated worktree.
  - User-checkout preservation is asserted in every end-to-end terminal condition.

---

## Phase 6 — Host Integrations and Skills

- [ ] **HSDLC-056 — Author the host-neutral delegation skill**  
  Files: `skills/local-delegate/SKILL.md`  
  Depends on: HSDLC-044, HSDLC-055  
  Acceptance:
  - Skill explains trust assumptions, synchronous/asynchronous choices, validation, status handling, and review evidence.
  - It never promises reactive wakeup on unsupported hosts or labels agent review deterministic.

- [ ] **HSDLC-057 — Add Codex manual MCP registration**  
  Files: `docs/hosts/codex.md`  
  Depends on: HSDLC-044  
  Acceptance:
  - Instructions use `codex mcp add hybrid-sdlc -- hybrid-sdlc mcp` and include list/get/remove verification.
  - Manual MCP registration is clearly separated from plugin installation.

- [ ] **HSDLC-058 — Package the Codex plugin and local marketplace entry**  
  Files: `.codex-plugin/plugin.json`, marketplace manifest files, `docs/hosts/codex.md`  
  Depends on: HSDLC-056, HSDLC-057  
  Acceptance:
  - Manifest validates against the installed Codex version.
  - A clean test profile can add the marketplace, install the plugin, discover the skill/MCP server, and remove it.

- [ ] **HSDLC-059 — Add Claude Code integration**  
  Files: `CLAUDE.md`, `.claude/` configuration or documented commands, `docs/hosts/claude-code.md`  
  Depends on: HSDLC-044, HSDLC-056  
  Acceptance:
  - Configuration paths and commands are verified against a pinned supported Claude Code version.
  - Synchronous behavior and any notification limitations are documented accurately.

- [ ] **HSDLC-060 — Add Antigravity integration**  
  Files: Antigravity manifest/configuration, `.agents/skills/local-delegate/SKILL.md`, `docs/hosts/antigravity.md`  
  Depends on: HSDLC-044, HSDLC-056  
  Acceptance:
  - Discovery paths and MCP registration are verified on a pinned supported version.
  - Reactive wakeup is promoted from experimental only after a recorded end-to-end test.

- [ ] **HSDLC-061 — Add host capability tests and compatibility table**  
  Files: `docs/host-compatibility.md`, `tests/host_contracts/`  
  Depends on: HSDLC-058, HSDLC-059, HSDLC-060  
  Acceptance:
  - Table records tested host version, install method, sync support, async support, progress behavior, and wakeup behavior.
  - Unsupported or unverified claims are labeled explicitly.

---

## Phase 7 — Packaging, Runtime Deployment, Hardware Validation, and Release

- [ ] **HSDLC-063 — Add packaging smoke tests**  
  Files: `.github/workflows/ci.yml`, `tests/packaging/`  
  Depends on: HSDLC-062  
  Acceptance:
  - Build wheel and source distribution, install each into a clean environment, and run both entry points.
  - Built distributions contain required templates and exclude tests, logs, weights, and local artifacts as intended.

- [x] **HSDLC-064 — Validate the portable llama.cpp deployment contract**  
  Files: `compose.yaml`, `.env.example`, `config/model-profiles/`, `docs/llama-server-docker.md`, `tests/unit/test_repository_layout.py`  
  Depends on: HSDLC-034  
  Acceptance:
  - Compose mounts model weights read-only, publishes only to host loopback, requests the NVIDIA GPU, and limits llama.cpp to one parallel request.
  - Worker and interactive profiles are parseable, model paths remain user-owned configuration, and no weights or machine-specific paths are committed.
  - GPU passthrough, `/health`, and `/v1/models` are verified on the RTX 3090 before the task is completed.

- [ ] **HSDLC-065 — Define the hardware benchmark protocol**  
  Files: `benchmarks/README.md`, `benchmarks/benchmark_task.json`  
  Depends on: HSDLC-064  
  Acceptance:
  - Protocol pins model repository/revision, GGUF file checksum, llama.cpp image/build, launch arguments, context, prompt, repository fixture, and ambient assumptions.
  - Metrics include startup time, host committed memory, load/peak VRAM, prompt tokens/second, generation tokens/second, time-to-green, patch success rate, and total duration.

- [ ] **HSDLC-066 — Benchmark Qwen 3.6 35B-A3B native versus Docker**  
  Files: `benchmarks/results/rtx-3090-qwen36-35b-a3b-native.json`, `benchmarks/results/rtx-3090-qwen36-35b-a3b-docker.json`, `docs/model-profiles.md`  
  Depends on: HSDLC-065  
  Acceptance:
  - Run at least three repetitions per runtime at the documented 16K worker context with identical weights and tasks.
  - Record weights, KV cache, compute buffers, total VRAM, host memory, and startup overhead separately where tooling permits.
  - Promote Docker to the sole recommended launcher only when its stability and memory results are comparable; otherwise retain both documented paths.

- [ ] **HSDLC-066A — Benchmark the Qwen 3.6 27B quality profile**  
  Files: `benchmarks/results/rtx-3090-qwen36-27b.json`, `docs/model-profiles.md`  
  Depends on: HSDLC-065  
  Acceptance:
  - Uses the same protocol, task fixtures, quantization class, and context as the 35B-A3B comparison.
  - Documentation compares time-to-green and patch success rate in addition to token throughput and memory.
  - The dense 27B profile is not labeled faster or preferred without measured evidence.

- [ ] **HSDLC-067 — Complete security and destructive-operation review**  
  Files: `docs/security.md`, test evidence  
  Depends on: HSDLC-055, HSDLC-061, HSDLC-062  
  Acceptance:
  - Threat model clearly separates policy validation from OS sandboxing.
  - Review covers path escape, executable shadowing, environment leakage, log leakage, process cleanup, worktree targeting, cleanup boundaries, and plugin configuration.
  - No destructive command can target the user's checkout through computed or unresolved paths.

- [ ] **HSDLC-068 — Write installation, operation, and troubleshooting documentation**  
  Files: `README.md`, `docs/installation.md`, `docs/troubleshooting.md`  
  Depends on: HSDLC-061, HSDLC-063, HSDLC-065  
  Acceptance:
  - Documents prerequisites, supported versions, first run, configuration, sync/async use, result review, cleanup, and uninstall.
  - Examples use canonical command syntax and named test profiles.

- [ ] **HSDLC-069 — Perform a clean-machine release rehearsal**  
  Files: release checklist/evidence  
  Depends on: HSDLC-063, HSDLC-067, HSDLC-068  
  Acceptance:
  - A clean Windows environment and one clean POSIX environment install the package from the built artifact.
  - Each completes health check, fixture delegation, asynchronous reconnect, artifact cleanup, and uninstall.

- [ ] **HSDLC-070 — Tag the first supported release**  
  Files: changelog and release metadata  
  Depends on: HSDLC-069  
  Acceptance:
  - Version, changelog, lock file, package metadata, compatibility table, and documentation agree.
  - Release notes distinguish verified behavior from experimental host capabilities and candidate hardware profiles.

---

## Deferred Work — Not Required for the Initial Release

- [ ] **HSDLC-D01 — Evaluate a persistent supervisor daemon** after per-job worker behavior is stable.
- [ ] **HSDLC-D02 — Add pluggable OS sandbox providers** for genuinely untrusted repositories.
- [ ] **HSDLC-D03 — Add optional remote or non-Qwen OpenAI-compatible endpoints** without weakening local-first defaults.
- [ ] **HSDLC-D04 — Add a web/dashboard UI** only after the CLI/MCP schemas are stable.
- [ ] **HSDLC-D05 — Add automated branch integration** only with explicit user approval and conflict-safe semantics.
