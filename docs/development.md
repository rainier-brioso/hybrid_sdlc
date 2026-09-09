# Hybrid SDLC — Development Guide

This document covers the developer workflow for the `hybrid-sdlc` package.

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| [uv](https://github.com/astral-sh/uv) | Fast Python package manager | `pip install uv` |
| Python ≥ 3.11 | Runtime | System package manager |
| Git | Repository operations | System package manager |

---

## Environment Setup

```bash
# Clone and enter the repository
git clone <repo-url>
cd hybrid_sdlc

# Install all dependencies (runtime + dev) from the lock file
uv sync --frozen

# Verify the CLI is reachable
uv run hybrid-sdlc --help
```

---

## Quality Gate

A single command runs **all** checks in the correct order:

```bash
uv run ruff format --check . && \
uv run ruff check . && \
uv run mypy src/ && \
uv run pytest --cov --cov-report=term-missing
```

### What each step does

| Step | Tool | What it checks |
|------|------|----------------|
| Format | `ruff format --check` | Consistent whitespace and style |
| Lint | `ruff check` | PEP 8 / bugbear / import order violations |
| Type check | `mypy src/` | Full strict type annotation coverage |
| Test + coverage | `pytest --cov` | All unit and integration tests; fails below 80 % |

> [!IMPORTANT]
> The complete suite runs without Aider, llama-server, a GPU, or internet access.
> All tests use local mocks and `tmp_path` fixtures.

---

## Coverage Thresholds

The global minimum is **80 % branch coverage** (`fail_under = 80` in `pyproject.toml`).

Security and correctness-critical modules are held to higher informal standards; do not lower
their coverage without a documented rationale:

| Module | Emphasis |
|--------|----------|
| `security.py` | Path confinement, environment scrubbing |
| `artifacts.py` | Atomic persistence, redaction, boundary checks |
| `processes.py` | Process tree termination (Windows Job Object + POSIX process group) |
| `aider_runner.py` | State-machine exit paths, failure-signature deduplication |
| `git_tools.py` | Preflight, locking, diff capture |

---

## Running a Subset of Tests

```bash
# Unit tests only
uv run pytest tests/unit/

# Integration tests only
uv run pytest tests/integration/

# One specific module
uv run pytest tests/unit/test_security.py -v

# With coverage for a specific package module
uv run pytest --cov=hybrid_sdlc.security tests/unit/test_security.py
```

---

## Adding a New Feature

1. **Write tests first** against the acceptance criteria in `docs/implementation_tasks.md`.
2. Implement in `src/hybrid_sdlc/`.
3. Run the full quality gate.
4. Update `docs/implementation_tasks.md` to mark the task complete.

---

## Project Layout

```
hybrid_sdlc/
├── src/hybrid_sdlc/          # Package source (src/ layout)
│   ├── __init__.py           # Public API re-exports and version
│   ├── __main__.py           # python -m hybrid_sdlc entry point
│   ├── errors.py             # Error hierarchy and exit-code contracts
│   ├── models.py             # Versioned result schemas (Pydantic)
│   ├── config.py             # Configuration discovery and precedence
│   ├── command_profiles.py   # Named test-command profiles
│   ├── security.py           # Repository-root verification, path confinement, env scrubbing
│   ├── git_tools.py          # Preflight, advisory lock, baseline and diff capture
│   ├── server_probe.py       # OpenAI-compatible endpoint probing and selection
│   ├── processes.py          # Bounded subprocess execution (Win32 Job / POSIX pgroup)
│   ├── aider_runner.py       # Bounded edit/test state machine
│   ├── artifacts.py          # Redaction, atomic persistence, retention cleanup
│   └── cli.py                # Click CLI (check, run-task, clean)
├── tests/
│   ├── unit/                 # Per-module unit tests (no network, no model)
│   └── integration/          # End-to-end CLI and loop tests
├── docs/
│   ├── implementation_plan.md
│   ├── implementation_tasks.md
│   ├── configuration.md
│   └── development.md        # ← this file
├── hybrid_sdlc.example.toml  # Annotated example configuration
├── pyproject.toml            # Build, dependencies, tool configuration
└── uv.lock                   # Pinned dependency lock file
```

---

## Design Decisions

### No shell execution
All subprocesses use `shell=False` with explicit `argv` arrays.
This prevents shell injection and makes command tracing deterministic.

### Advisory file lock
`.hybrid_sdlc/runner.lock` is an advisory lock implemented with `filelock`.
It prevents accidental concurrent runs but is not an OS security boundary.
The repository, configuration, and command profiles are assumed to be trusted.

### Process tree termination
- **Windows**: Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` guarantees
  all descendants exit when the job handle is closed.
- **POSIX**: Process group (`start_new_session=True`) + `SIGTERM`/`SIGKILL` to the group.

### Atomic artifact persistence
Artifacts are written via a same-directory temporary file, flushed, locked, and
atomically renamed.  Readers never observe a partial write.
On POSIX, the writer applies user-only permissions. On Windows, it preserves the
repository's inherited ACL because replacing ACLs can disrupt concurrent readers.

### Redaction before persistence
`redact_data` is called inside `atomic_save_json`; secrets are scrubbed before any disk write.
Pattern matching covers environment assignments, bearer tokens, GitHub PATs, URLs with
embedded credentials, and configured literal secrets.
