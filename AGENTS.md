# Repository development instructions

## Project and sources of truth

Hybrid SDLC is a Python CLI and host-native MCP server that delegates bounded
editing and testing to Aider through an OpenAI-compatible inference endpoint.
Start with `docs/implementation_plan.md` and `docs/implementation_tasks.md` for
scope and acceptance criteria; use `docs/development.md` for contributor setup
and `docs/configuration.md` for runtime configuration.

- Implement package behavior in `src/hybrid_sdlc/`.
- Keep regression coverage in `tests/unit/`, `tests/integration/`, and `tests/e2e/`.
- Keep installation guidance for library users in `docs/hosts/`. Supporting a
  host application does not require using that host to develop this repository.
- `skills/local-delegate/SKILL.md` is the canonical delegation workflow. Keep
  its shipped workspace/plugin copies and MCP definitions consistent; see
  `tests/unit/test_codex_plugin_package.py`.

## Implementation contracts

- Preserve versioned result schemas, structured errors, and CLI exit codes.
- Run subprocesses with explicit argument arrays and `shell=False`; preserve
  bounded retries, timeouts, cancellation, and process-tree cleanup.
- Preserve repository path confinement, committed-spec validation, isolated
  worktree execution, secret redaction, and atomic artifact persistence.
- Repository commands are trusted inputs; MCP access and advisory locks are
  not an OS sandbox. Passing tests still requires reviewing the exact patch.
- Async jobs require status polling until terminal. Claim host wakeup or
  background survival only when supported by recorded evidence.
- Keep model endpoints configurable. Host registration must be explicit and
  preserve existing user configuration. Model weights, secrets, local runtime
  overrides, and temporary artifacts must stay out of commits.

## Validation and evidence

Install development dependencies with `uv sync --frozen --group dev`. For code
changes, run relevant regression tests and the required checks before handoff:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy src/
uv run pytest --cov --cov-report=term-missing -ra
```

The coverage threshold is 80%. Tests use fixtures and mocks; live model and GPU
checks are separate. Report environment-limited skips as unverified behavior.
For documentation-only edits, check references and `git diff --check`.
Update task status only when its acceptance criteria are met, and keep runtime
benchmarks distinct from endpoint health or small smoke tests.

## Git workflow

Develop on a feature branch and integrate through a PR to protected `main`.
Preserve unrelated user changes and review staged paths before committing.
Keep the stable required CI check `CI / Required`; wait for it to pass before
merging. Do not bypass branch protection or force-push `main`.
