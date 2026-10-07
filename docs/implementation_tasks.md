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

- [x] **HSDLC-053 — Implement scoped commit creation**
  Files: `src/hybrid_sdlc/git_tools.py`, `tests/integration/test_commits.py`  
  Depends on: HSDLC-051  
  Acceptance:
  - The commit backend is inert without explicit opt-in; the CLI `--commit` flag is wired when the isolated runner lands in HSDLC-055.
  - Commit includes only changes produced inside the isolated worktree and references the task ID.
  - Failed tests and policy failures cannot produce a commit.

- [x] **HSDLC-054 — Define result integration back into the user branch**
  Files: `src/hybrid_sdlc/worktrees.py`, `docs/worktrees.md`, `tests/integration/test_worktrees.py`  
  Depends on: HSDLC-053  
  Acceptance:
  - Default result identifies the baseline commit and includes an exact, applyable patch; a commit hash is included only after explicit scoped commit opt-in.
  - Patch export includes tracked and untracked changes, preserves binary content, and does not mutate the isolated index or source checkout.
  - Patch artifacts are stored outside the checkout with documented sensitive-content handling.
  - No automatic merge, cherry-pick, or source-branch mutation occurs.
  - Documentation gives explicit cherry-pick/apply steps and conflict behavior.
  - Evidence: `pytest -q tests/integration/test_worktrees.py` reports 22 passed and two
    Windows symlink-privilege skips. Tests apply the binary-capable patch in a separate
    checkout, cover Unicode and spaced paths, preserve a dirty source checkout and the
    isolated index, reject patch conflicts without changing the target, and preserve the
    previous patch when Git metadata lookup or atomic replacement fails. A deterministic
    parent-path swap test confirms Git reads the private snapshot instead of reopening an
    untracked checkout path. Ruff, package mypy, and `git diff --check` pass.

- [x] **HSDLC-055 — Move the runner to isolated execution by default**
  Files: `src/hybrid_sdlc/aider_runner.py`, `src/hybrid_sdlc/cli.py`, `src/hybrid_sdlc/models.py`, `src/hybrid_sdlc/submission.py`, `src/hybrid_sdlc/worker.py`, `src/hybrid_sdlc/git_tools.py`, `src/hybrid_sdlc/artifacts.py`, `docs/worktrees.md`, `tests/`
  Depends on: HSDLC-052, HSDLC-054  
  Acceptance:
  - The runner uses a detached checkout at source `HEAD`; the source spec must be committed and unchanged, while unrelated dirty source changes are preserved.
  - Aider and test commands run with the isolated checkout as their working directory; repo-local test executables are resolved in that checkout and fail closed with setup guidance when missing, while external executables retain their approved paths.
  - Results identify the source repository, isolated worktree, baseline, exact review patch, and optional scoped commit. Run metadata and locks never enter the patch or commit.
  - `run-task --commit` is explicit and invokes the HSDLC-053 backend only after the final tests pass; async and MCP execution never commits implicitly.
  - Failed worktrees and their review patches are retained by default; optional rollback restores only the isolated worktree after patch export.
  - Existing Phase 2 and Phase 3 tests pass using isolated worktrees, with source preservation asserted for success, failure, cancellation, timeout, dirty checkout, and rollback scenarios.
  - Evidence: Windows full suite reports 344 passed, 10 skipped (Job Object and symlink privilege limits); Ruff, formatting, mypy, and diff checks pass.

---

## Phase 6 — Host Integrations and Skills

- [x] **HSDLC-056 — Author the host-neutral delegation skill**
  Files: `skills/local-delegate/SKILL.md`  
  Depends on: HSDLC-044, HSDLC-055  
  Acceptance:
  - Skill explains trust assumptions, synchronous/asynchronous choices, validation, status handling, and review evidence.
  - It never promises reactive wakeup on unsupported hosts or labels agent review deterministic.
  - Evidence: `skills/local-delegate/SKILL.md` documents the verified CLI/MCP
    contracts, fail-closed async host limitations, bounded polling and cancellation,
    and exact patch/commit review; skill metadata and `git diff --check` pass.

- [x] **HSDLC-057 — Add Codex manual MCP registration**
  Files: `docs/hosts/codex.md`  
  Depends on: HSDLC-044  
  Acceptance:
  - Instructions use `codex mcp add hybrid-sdlc -- hybrid-sdlc mcp` and include list/get/remove verification.
  - Manual MCP registration is clearly separated from plugin installation.
  - Evidence: `docs/hosts/codex.md` documents prerequisites, registration,
    list/get/remove commands, an optional non-job endpoint probe, security
    caveats, and the separate HSDLC-058 plugin work; links and `git diff --check`
    pass.

- [x] **HSDLC-058 — Package the Codex plugin and local marketplace entry**
  Files: `.codex-plugin/plugin.json`, `plugins/hybrid-sdlc/`, `.agents/plugins/marketplace.json`, `docs/hosts/codex.md`, plugin package sync tests
  Depends on: HSDLC-056, HSDLC-057  
  Acceptance:
  - Manifest validates against the installed Codex version.
  - A clean test profile can add the marketplace, install the plugin, discover the skill/MCP server, and remove it.
  - Verified with Codex CLI `0.158.0-alpha.2.1`: a process-scoped `CODEX_HOME` added the repo marketplace, installed and listed the plugin, confirmed the installed skill and MCP config, then removed the plugin and marketplace.
  - Initialized the packaged stdio MCP server and listed `check_local_model`, `run_spec_task_sync`, `submit_spec_job`, `get_job_status`, and `cancel_spec_job`.

- [x] **HSDLC-059 — Add Claude Code integration**
  Files: `.mcp.json`, `docs/hosts/claude-code.md`
  Depends on: HSDLC-044, HSDLC-056  
  Acceptance:
  - Configuration paths and commands are verified against a pinned supported Claude Code version.
  - Synchronous behavior and any notification limitations are documented accurately.
  - Evidence: Claude Code CLI 2.1.268, run through an isolated npm cache and
    `CLAUDE_CONFIG_DIR`, reported the repository `.mcp.json` server in project
    scope as pending interactive approval; `claude mcp get hybrid-sdlc` showed
    its configured command. The guide records project approval, optional
    project registration/removal syntax, sync-call backgrounding from 2.1.212,
    and explicit async job polling without cross-session wakeup claims.

- [x] **HSDLC-060 — Add Antigravity integration**
  Files: Antigravity manifest/configuration, `.agents/skills/local-delegate/SKILL.md`, `docs/hosts/antigravity.md`  
  Depends on: HSDLC-044, HSDLC-056  
  Acceptance:
  - Discovery paths and MCP registration are verified on a pinned supported version.
  - Reactive wakeup is promoted from experimental only after a recorded end-to-end test.
  - Discovery evidence: Antigravity IDE `1.107.0` discovered the workspace skill, but
    did not list the checked-in `.agents/mcp_config.json` server after an IDE
    reload, even though its terminal resolved `hybrid-sdlc`. A workspace-plugin
    experiment also did not appear, so it was not retained. Explicit
    user-profile registration made the server and all five tools appear in the
    IDE MCP list. The CLI now has an opt-in setup command. Following review
    (2026-10-05), existing configurations receive a separate merged proposal
    for manual review/application with the IDE closed; setup never replaces
    them. New configurations use atomic publication without overwriting a
    concurrent creator. The in-IDE smoke evidence is recorded below. Reactive
    wakeup remains experimental.
  - Race-fix validation (2026-10-05): 405 tests passed with 12 environment-limited
    skips; coverage 80.26%, Ruff lint/format and mypy passed. Regression tests
    preserve late config edits and concurrent creation, distinguish proposal
    output and dry-run, and tolerate temporary cleanup failure after publication.
    Independent review confirmed the original lost-update race is resolved.
  - In-IDE evidence (2026-10-05): Antigravity called both the endpoint probe
    and synchronous task tool. Run `run_1791231526_049a1603` returned a structured
    150-second Aider timeout. Strata metrics show zero output tokens and about
    160 seconds in prompt processing before cancellation. The isolated checkout
    is clean; that run did not validate successful coding and the current IDE version
    has not been supplied. See
    `benchmarks/results/strata/antigravity-mcp-timeout-20261005.json`.
  - Successful in-IDE retry (2026-10-05): after the user restarted Strata,
    `run_1791233269_f701bc55` succeeded in 52.4 seconds on attempt one. Independent
    review confirmed only the allowed files changed; both tests passed, covering
    zero, positive, and negative inputs. The source fixture remains clean.
    See `benchmarks/results/strata/antigravity-mcp-smoke-20261005.json`.
    The user reports almost 24 hours idle before the previous stall; this is
    an unconfirmed runtime hypothesis, not an MCP failure diagnosis. Reactive
    wakeup and host async survival remain explicitly unverified.

- [x] **HSDLC-061 — Add host capability tests and compatibility table**
  Files: `docs/host-compatibility.md`, `tests/host_contracts/`  
  Depends on: HSDLC-058, HSDLC-059, HSDLC-060  
  Acceptance:
  - Table records tested host version, install method, sync support, async support, progress behavior, and wakeup behavior.
  - Unsupported or unverified claims are labeled explicitly.
  - Completed (2026-10-05): added a capability table and automated MCP tool
    discovery/argument-schema contracts (two tests passed). The table distinguishes
    historical host discovery versions, standalone SDK evidence, and Antigravity's
    successful in-IDE task; the current IDE version is explicitly not supplied.
    Async survival, displayed progress, and reactive wakeup require separate
    evidence and remain labeled unverified.

---

## Phase 7 — Packaging, Runtime Deployment, Hardware Validation, and Release

- [x] **HSDLC-063 — Add packaging smoke tests**
  Files: `.github/workflows/ci.yml`, `pyproject.toml`, `scripts/packaging_smoke.py`, `tests/packaging/`, `docs/development.md`
  Depends on: HSDLC-062  
  Acceptance:
  - Build wheel and source distribution, install each into a clean environment, and run both entry points.
  - Built distributions contain required templates and exclude tests, logs, weights, and local artifacts as intended.
  Validation (2026-10-05): Windows clean-install smoke passed for the wheel,
  source distribution, and a wheel rebuilt from the source distribution in
  separate Python 3.12 environments. Both CLI help entry points and installed
  MCP imports passed; module origins were inside the new environments, not the
  checkout. Archive templates matched canonical source bytes and installed
  initialization output. Only the external Spec Kit capability probe was
  mocked for initialization; this is not a live Spec Kit compatibility test.
  The opt-in packaging pytest invocation passed all six checks, including
  missing-input failure and template-drift regressions. Lint/format passed.
  The former integration wheel-build test moved to this dedicated path so
  ordinary pytest stays offline. CI now requires the Ubuntu/Python 3.12
  packaging job through `CI / Required`; remote CI execution is still pending.
  Regression validation: 410 passed, 13 skipped (12 Windows environment limits
  plus the intentionally opt-in distribution smoke), coverage 80.26%; final
  packaging checks passed all six tests separately. Mypy passed for 20 source
  files. Independent review identified source-archive prefix and canonical
  template-parity gaps; both were corrected and validated before handoff.

- [x] **HSDLC-064 — Validate the portable llama.cpp deployment contract**  
  Files: `compose.yaml`, `.env.example`, `config/model-profiles/`, `docs/llama-server-docker.md`, `tests/unit/test_repository_layout.py`  
  Depends on: HSDLC-034  
  Acceptance:
  - Compose mounts model weights read-only, publishes only to host loopback, requests the NVIDIA GPU, and limits llama.cpp to one parallel request.
  - Worker and interactive profiles are parseable, model paths remain user-owned configuration, and no weights or machine-specific paths are committed.
  - GPU passthrough, `/health`, and `/v1/models` are verified on the RTX 3090 before the task is completed.

  Evaluation status (2026-10-04): the user paused the remaining llama.cpp
  `SMOKE-001` live rerun while Strata is configured. The earlier task run passed
  its tests but modified `.gitignore` through Aider's automatic ignore rule.
  The runner fix is implemented; a live result restricted to `calculator.py`
  and `test_smoke.py` is still pending. Keep this evidence open and resume it
  when the llama.cpp evaluation is resumed.

- [x] **HSDLC-064A - Configure and validate optional Strata Docker deployment**
  Files: `compose.strata.yaml`, `config/strata/worker-defaults.json`, `config/model-profiles/strata-coder-rtx3090-worker.toml`, `docs/strata-server-docker.md`, `docs/strata-validation-checklist.md`
  Depends on: HSDLC-034
  Acceptance:
  - Build from an immutable upstream revision, expose only host loopback, request the NVIDIA GPU, and persist model data in a Docker named volume.
  - Configure the Coder IQ1_M model at 16K context with low-RAM mode for the evaluation host (RTX 3090, 32 GB RAM, 24 GB WSL2 memory cap); record the resident-versus-mapped decision rather than assume fit.
  - Verify Compose rendering, image build, GPU passthrough, loaded health, `/v1/models`, low-reasoning settings, and a bounded chat completion.
  - Document first-run downloads and persisted configuration behavior. Do not claim API health or structural validation proves Aider task compatibility or performance parity.
  Progress (2026-10-04): Compose rendering, repository layout checks, CUDA 13
  GPU passthrough, and the pinned image build passed. Setup accepted Coder
  IQ1_M at 16K with resident low-RAM mode as its predicted fit. Both model
  shards downloaded (58.4 GB total); recreation reused the volume. Loaded
  health, exact model ID, mounted low reasoning/4096 output defaults, the
  Hybrid SDLC availability probe, and a bounded arithmetic chat passed.
  Performance comparison remains open under HSDLC-064B.

- [ ] **HSDLC-064B - Verify Strata task compatibility and Docker performance**
  Files: `benchmarks/results/strata/`, `docs/strata-server-docker.md`
  Depends on: HSDLC-064A, HSDLC-065
  Acceptance:
  - Run the trusted smoke fixture through Hybrid SDLC and Aider using only the Strata endpoint and the exact advertised model ID. Tests must pass and the diff must contain only authorized source/test files.
  - Compare native Strata and Docker/WSL2 using identical model files, engine revision, context, reasoning effort, prompts, and at least three repetitions per runtime.
  - Record cold/warm startup, prompt processing, generation, RAM, VRAM, disk reads, and end-to-end time-to-green. Separately compare Strata's model with the existing Qwen 3.6 worker without attributing model differences to Docker overhead.
  - Retain Strata as a candidate until measured stability and performance justify promotion. WSL pinned-memory and KV-streaming restrictions must remain explicit.
  Progress (2026-10-04): Strata/Aider SMOKE-001 passed in one attempt (56.7 s),
  and its two unittest methods passed an independent rerun. The diff also
  contains `.aider.tags.cache.v4/cache.db`, so that run failed authorized-files-only
  acceptance. A subsequent explicit no-map fixture run
  (`run_1791140948_7f6efcb2`, 116.2 s) passed in one attempt with exactly the
  authorized source/test files changed and both test methods independently
  rerun successfully. Both worktrees are retained. Task compatibility for this
  tiny fixture is verified; native-versus-Docker measurements remain pending.
  Diagnostic follow-up (2026-10-04): a 1,321-token synthetic prompt took
  12.671 s, with identical repeats taking 0.172/0.125 s and 1,314 reused
  tokens. Prior Aider requests reported zero prompt reuse. Evidence and the
  reusable probe are under `benchmarks/results/strata/` and
  `benchmarks/strata_prompt_probe.py`. This is not a coding-performance or
  native-versus-Docker benchmark. Prompt construction/reuse and observed
  Windows memory pressure need controlled follow-up; runtime defaults remain
  unchanged.

  File-context follow-up (2026-10-04): starting Aider with the known editable
  files reduced this tiny fixture from two requests to one. The diagnostic
  passed in 22.6 s; the source CLI's optional `aider_edit_files` configuration
  run passed in 21.3 s with four independently verified test methods and only
  the authorized two files changed. This does not establish a general speedup
  or caching improvement: both requests reported zero reused tokens. See
  `benchmarks/results/strata/aider-file-context-cli-20261004.json`. Targets are
  existing committed regular files, validated in source and isolated checkout;
  they are context hints, not an edit allowlist.

  Source regression validation (2026-10-04): 400 passed, 12 environment-limited
  skips; Ruff lint/format and mypy (20 source files) passed. File-context
  implementation received independent read-only review with no confirmed
  defect. The installed CLI was refreshed after the operator stopped the
  processes holding its installation directory. A fresh SDK stdio MCP client
  discovered all five tools, passed the model probe, and completed
  `run_1791148535_3b03da02` in one attempt (19.7 s), with two independently
  passing test methods and only the authorized source/test files changed.
  See `benchmarks/results/strata/installed-mcp-smoke-20261004.json`.
  The existing Codex chat connection still returned `Transport closed`;
  fresh-client success does not verify that connection.

  Runtime comparison decision (2026-10-04): at the user's request, defer the
  native-versus-Docker benchmark until a real task delegation provides a
  representative fixture. HSDLC-064B remains open; no runtime parity or
  candidate promotion is claimed by the successful synchronous smoke.

- [ ] **HSDLC-064C - Keep Aider repository-map cache out of bounded task patches**
  Depends on: the first live Strata smoke evidence under HSDLC-064B
  Acceptance:
  - Address `.aider.tags.cache.v4/cache.db` without deleting user-owned caches,
    changing the user's ignore rules, or hiding arbitrary unexpected changes.
  - Choose and document a supported strategy. Installed Aider 0.86.2 hardcodes
    the cache under the repository root; `--map-tokens 0` prevents RepoMap
    construction but removes repository-map context. Do not silently disable
    maps for all tasks merely to make this tiny smoke pass.
  - Add regression coverage and rerun the isolated authorized-files-only smoke;
    preserve the failing clean-diff evidence and the paused llama.cpp fixture.
  Progress (2026-10-04): optional, strictly validated `aider_repo_map_tokens`
  now reaches Aider through CLI, sync MCP, and background workers. Default
  omission preserves repository-map context; the explicit `0` fixture rerun
  passed clean-diff acceptance. Keep this task open for map-enabled cache
  handling: this opt-in mode does not solve cache artifacts for general tasks.

- [x] **HSDLC-064D - Add explicit managed Strata lifecycle commands**
  Files: `src/hybrid_sdlc/runtime_strata.py`, `src/hybrid_sdlc/_runtime/strata/`,
  CLI/runner/submission/worker hooks, runtime unit/CLI integration tests,
  `docs/runtime-management.md`
  Depends on: HSDLC-063, HSDLC-064A
  Acceptance:
  - Ship the required runtime assets and provide explicit start, stop, restart,
    status, and bounded logs commands for an opt-in managed Docker service.
  - Resolve the configured service identity, use bounded argv subprocesses,
    and never execute arbitrary repository-supplied Compose configuration.
  - Do not stop/restart user-owned or external endpoints. Coordinate lifecycle
    operations with task execution and refuse disruptive commands while managed
    jobs are queued/running, including synchronous work and concurrent clients.
  - Preserve model volumes and user configuration; no volume deletion or
    automatic large download/build as a side effect of status/diagnostics.
  - Test lifecycle behavior with mocks; document Docker prerequisites and
    distinguish startup/loading from inference readiness.
  Evidence (2026-10-06): implemented opt-in `runtime strata configure`,
  `start`, `stop`, `restart`, `status`, and bounded `logs`, with JSON output.
  Packaged assets and validated per-user state establish a unique ownership
  token, project, model volume, and local Docker context. Common synchronous
  runner leases and durable submission/worker reservations coordinate clients
  across repositories; corrupt/unresolved records and uncertain Docker
  mutations fail closed. Model volumes are never deleted, and image absence
  produces explicit build guidance from a read-only preflight.
  Validation: full frozen-source suite 471 passed / 14 skipped, plus the final
  corrupt-job-record regression passed with appended coverage (81.05% total;
  runtime module 86%). Ruff lint/format and mypy (21 source files) passed.
  Seven distribution checks passed, including isolated wheel, sdist, and
  rebuilt-wheel installs with canonical asset parity. A rendered temporary
  Compose definition passed read-only validation. Cross-process activity,
  real submission launch-failure cleanup, and worker terminalization hooks
  passed without live model calls or Docker mutations.
  The manual evaluation container retained its exact ID/start time; local
  repository configuration and installed user CLI were not changed. Windows
  Job Object survival, symlink privileges, and POSIX-mode tests remain
  environment-limited; the opt-in packaging test was verified separately.
  No live managed-service migration/start/stop/restart is claimed. PR #7's
  seven remote CI checks passed, including packaging and `CI / Required`,
  before it was merged. See [runtime guide](runtime-management.md).

  Post-merge review follow-up (2026-10-06): on the next runtime-diagnostics
  branch, implicit HTTP port 80 now matches both synchronous and async task
  reservations. YAML numeric/date/null/boolean volume names are quoted in
  rendered Compose, while previously valid string names retain their existing
  configuration bytes. Regression coverage and read-only Compose validation
  cover the fixes; this follow-up does not implement HSDLC-064E or
  migrate/start/stop the live service. Final validation: 546 passed / 14
  environment/opt-in skips, 81.19% coverage, Ruff lint/format and mypy passed.
  All 52 rendered Compose variants passed read-only parser validation;
  independent review found no remaining confirmed blocker. These fixes remain
  local to the next branch; no new commit, push, or PR merge is claimed.

- [ ] **HSDLC-064E - Add runtime readiness and suspected-stall diagnostics**
  Depends on: HSDLC-064D, HSDLC-020
  Acceptance:
  - Keep default MCP health checks read-only and non-generating; expose bounded
    opt-in inference readiness distinctly from endpoint/model availability.
  - Distinguish unavailable, loading, busy, ready, and suspected-stalled states
    using available observations; label uncertainty and unsupported telemetry.
    Idle time, a single timeout, or low token throughput does not prove a stall.
  - Do not send diagnostic inference concurrently with active managed work or
    automatically restart on timeout. Recovery remains an explicit lifecycle
    operation subject to ownership and active-job checks.
  - Preserve capped, redacted Aider timeout diagnostics without raw prompt or
    secret persistence; cover diagnostic retention and cleanup in tests.
  - Record controlled long-idle/retry evidence separately from the successful
    smoke; do not claim the 2026-10-05 restart proved the prior stall's cause.

  First increment implemented locally (2026-10-06): add managed-endpoint
  `runtime strata diagnose`, with GET-only availability/health checks by
  default and an explicit bounded `--readiness` request. Coordinate readiness
  through the shared managed activity gate, including the existing CLI
  `check --readiness` path, without changing the five-tool MCP interface.
  Distinguish loading, managed activity, confirmed readiness, and uncertainty;
  do not infer a stall or restart automatically. Keep HSDLC-064E open until
  privacy-safe timeout evidence, suspected-stall classification, and controlled
  long-idle/retry evidence satisfy the remaining acceptance criteria.

  Validation: 602 passed / 14 environment/opt-in skips, 81.45% coverage;
  Ruff lint/format and mypy (22 source files) passed. Seven opt-in packaging
  checks passed, including isolated wheel/sdist/rebuilt-wheel installs and
  installed origin checks for the private probe worker. Independent review
  findings were addressed: diagnostic GETs and managed readiness use wall-clock
  process budgets (including slow-drip response tests), and default diagnostics
  exclude verified terminal jobs without rewriting activity records. A Windows
  clock-origin mismatch and redundant post-Job-Object taskkill were corrected;
  blocked-stdin and clock-origin regressions pass. The five MCP tools remain
  unchanged. Windows background survival, symlink privileges, and POSIX-mode
  checks remain environment-limited; packaging was verified separately.
  No live model request, managed-service migration/start/stop/restart, installed
  user CLI update, commit, push, or remote CI result is claimed for this increment.
  The existing local repository configuration is preserved unchanged.

  Second increment implemented locally (2026-10-07): Aider attempt timeouts
  retain a fixed metadata allowlist under `failure.details.timeout_diagnostics`
  (under 1 KiB, no raw output/prompt/source/command/environment). Existing
  artifact cleanup and active-writer locks apply. Explicit `diagnose --repo-root
  ... --run-id ...` reads only one confined, size-capped record and requires
  matching endpoint/model metadata from the previous 30 minutes. Account/query
  distinctions participate in the opaque fingerprint; no raw credentials are
  persisted. Only matching timeout evidence plus a loaded/available model,
  known-idle managed activity, and a distinct opt-in readiness timeout can
  produce advisory `suspected-stalled`; no automatic recovery is triggered.
  Default GETs and the five MCP tools are unchanged. Independent privacy review
  identified an endpoint correlation issue, now fixed with route/account tests.
  Fingerprints are not encryption; artifacts remain private trusted state.
  README now summarizes installation, host/runtime choices and dated Strata
  observations with their limits; personal machine paths were removed from
  tracked examples/evidence without changing metrics. Controlled live evidence
  remains pending; use the [long-idle protocol](strata-long-idle-validation.md).
  Final validation: 641 passed / 14 environment/opt-in skips, 81.75% coverage;
  Ruff lint/format (92 files), mypy (23 source files), and seven isolated
  packaging checks passed. Six benchmark JSON records parse correctly and all
  16 README local links resolve. No tracked personal user-directory references
  remain. The existing local config is unchanged; no real model inference,
  service mutation, user-tool installation, commit, push, or remote CI run was
  performed. HSDLC-064E stays open solely for controlled live long-idle/retry
  evidence, not because a model stall has been established.

  Final review follow-up (2026-10-07): artifact-boundary resolution now runs
  inside the guarded evidence reader. Filesystem errors and Python 3.11
  symlink-loop errors return static invalid evidence instead of escaping as a
  traceback. Eight regression cases cover the repository, artifact directory,
  runs directory, and record path, without opening an unresolvable boundary.
  Independent fix review found no remaining actionable defect. Revalidation:
  649 passed / 14 environment/opt-in skips, 81.75% coverage; 109 targeted tests,
  Ruff lint/format (93 files), mypy (23 source files), and seven fresh isolated
  packaging checks passed. All 16 README local links resolve and six benchmark
  JSON records parse. Local configuration remains unchanged and excluded from
  the PR; controlled live long-idle/retry evidence remains pending.

  PR #8 CI follow-up (2026-10-07): Windows Python 3.12 exposed a cleanup race
  after a successful asynchronous `TerminateJobObject` call: the root had
  exited while its grandchild was still alive. Cleanup now confirms zero
  active processes in the owned job within a two-second deadline before
  treating termination as complete. Failed termination/query or an expired
  drain deadline keeps the existing fallback; `taskkill` is also time-bounded.
  The original containment/liveness assertion is unchanged. Nine deterministic
  regressions cover asynchronous drain, failure and fallback paths. Independent
  review found no actionable issue. The original failing test passed ten
  consecutive local Windows runs; process/cleanup regressions passed 36 tests
  on both Python 3.11 and isolated Python 3.12. Full local revalidation:
  658 passed / 14 environment/opt-in skips, 81.85% coverage; lint, formatting,
  mypy and seven fresh packaging checks passed. Remote CI must be rerun before
  merge; the initial failing run is not reported as successful.

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

- [ ] **HSDLC-D06 — Evaluate an optional fully local persona workflow** after Strata endpoint and task compatibility are validated.
  Planning note (2026-10-04): keep one model loaded and serialize repository analyst, implementation worker, and patch reviewer. Start a fresh client conversation on each role switch and forward only validated, bounded handoffs, not full histories. Use explicit per-request reasoning effort, thinking budgets, and output caps; the proposed starting budgets remain experimental. Enforce analyst/reviewer read-only capabilities in the tool/controller layer, preserve worker execution policies, and add endpoint-wide scheduling before supporting overlapping jobs across repositories. Keep multi-conversation parking disabled initially under the 24 GB WSL limit. Verify Aider forwards provider-specific settings, context reserves output room, truncated/invalid results fail safely, and switching roles does not reload model weights or inherit another role's transcript. Same-model review is not independent evidence of correctness; retain tests and deterministic checks. Host-native cheaper cloud subagent configuration remains separate and must not be overwritten. No implementation or deployment change is authorized by this planning note.

- [ ] **HSDLC-D01 — Evaluate a persistent supervisor daemon** after per-job worker behavior is stable.
- [ ] **HSDLC-D02 — Add pluggable OS sandbox providers** for genuinely untrusted repositories.
- [ ] **HSDLC-D03 — Add optional remote or non-Qwen OpenAI-compatible endpoints** without weakening local-first defaults.
- [ ] **HSDLC-D04 — Add a web/dashboard UI** only after the CLI/MCP schemas are stable.
- [ ] **HSDLC-D05 — Add automated branch integration** only with explicit user approval and conflict-safe semantics.
