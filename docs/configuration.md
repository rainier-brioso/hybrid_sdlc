# Configuration Guide

The Hybrid SDLC Toolkit (`hybrid-sdlc`) relies on a configuration file named `hybrid_sdlc.toml` located at the root of your target Git repository.

## Configuration Precedence

Settings are resolved in the following priority order:
1. **CLI Arguments & Flags** (highest priority)
2. **Repository Configuration** (`hybrid_sdlc.toml` at repo root)
3. **Built-in Defaults** (lowest priority)

Configuration loading is strictly confined to the repository root. `hybrid-sdlc` will never traverse parent directories looking for configuration files.

Optional [managed Strata lifecycle](runtime-management.md) settings are separate,
user-level Docker service state. Configuring that service does not change this
repository configuration or select its endpoint for tasks automatically.

## Aider Repository Map

`aider_repo_map_tokens` optionally sets Aider's repository-map token budget for each task:

```toml
# Omit this setting to keep Aider's normal repository-map behavior.
aider_repo_map_tokens = 0
```

The setting accepts a non-negative integer. Omission leaves Aider's default unchanged. Setting it to `0` disables the repository map, which can avoid Aider creating `.aider.tags.cache.v4` during a small task with explicitly named files. That also removes repository-wide symbol context and is best limited to tasks whose relevant files are listed directly. A positive value retains the map with the requested token budget.

### Optional cache-isolated Aider launch

`aider_python` explicitly selects the Python interpreter of an existing Aider
installation. It enables a compatibility adapter for **Aider 0.86.2 only**:

```toml
# Absolute path to the Python interpreter containing aider-chat, not aider.exe.
# Windows example; replace the installation directory for your machine.
aider_python = 'C:\tools\aider-env\Scripts\python.exe'
# Linux/macOS alternative:
# aider_python = '/opt/aider-env/bin/python'
```

The adapter redirects Aider's repository-map disk cache to run-owned storage
outside the isolated checkout, without disabling repository maps or filtering
the review patch. Unexpected checkout edits, including files under a cache-like
directory name, remain subject to normal patch export. Existing user caches and
ignore rules are not modified.

Omit this setting to keep the existing Aider command launch. That unadapted launch
can still create `.aider.tags.cache.v4` inside the task checkout. The adapter does
not combine with a custom `aider_cmd` prefix supplied through the Python API;
that combination is rejected rather than silently dropping wrapper arguments. It
does not discover interpreters, install Aider, or fall back to disabling maps. An
invalid interpreter, unsupported Aider version, or incompatible private API is
an error. Aider's private API is not a stable extension contract; support for a
new version requires explicit validation.

The external cache is reused during retries and retained with run artifacts,
including after timeout or cancellation. Immediate deletion could race a still
exiting child process. Cache contents may contain source symbols and paths; treat
them as private run artifacts. This option is not an OS sandbox: the interpreter
and repository commands must be trusted. Synthetic tests do not establish live
model performance or cross-platform end-to-end validation.

## Aider Edit Context Files

`aider_edit_files` optionally preloads selected, committed repository files into every Aider task:

```toml
aider_edit_files = ["src/example.py", "tests/test_example.py"]
```

Omit the setting (or use an empty list) to preserve Aider's normal context selection. Entries must be unique, non-empty repository-relative file paths. Absolute paths, traversal, the task specification itself, untracked or modified files, directories, and symlinks are rejected before Aider runs. The setting does not infer file paths from the specification.

These paths provide initial context and are **not** an edit allowlist or a security boundary: Aider may still edit other repository files. Use repository permissions and the existing isolated-worktree review flow to inspect changes.

## Optional Aider Request Budgets

For compatible OpenAI-style endpoints such as Strata, optional settings can set Aider's request output limit and the provider-specific reasoning budget:

```toml
# Experimental values for the measured 16K-context task; tune for each prompt/model.
aider_max_tokens = 4096
aider_reasoning_budget_tokens = 1024
```

Both settings are omitted by default, preserving Aider's existing request behavior. `aider_max_tokens` must be a positive integer. `aider_reasoning_budget_tokens` accepts zero or a positive integer; zero explicitly disables the Strata reasoning budget. If both are supplied, the reasoning budget must be lower than the output limit.

The shipped Strata deployment separately supplies server-side defaults of 4096
output tokens and a 1024-token reasoning budget for new definitions. Its startup
hook sets the model budget only when absent; explicit model settings and request
values take precedence. Existing services are not automatically restarted or
migrated. These Strata deployment defaults do not change the backend-neutral
toolkit defaults; see [Strata Docker setup](strata-server-docker.md).

The output limit is a response-token cap, not the model's context size. It must fit in the context remaining after the complete prompt Aider sends. Hybrid SDLC does not estimate the serialized prompt size, choose a smaller limit, or truncate the prompt automatically. In one observed 16,384-token Strata context, a 10,573-token prompt left a server-reported maximum output of 5,803; requesting 8,192 was rejected before generation. Choose a limit with room for the actual prompt and any server overhead. The reasoning budget controls how much of that response budget Strata may spend reasoning before it should leave room to answer; it is a wrap-up threshold, not an exact token count, and does not enlarge the context window. These values are task-specific examples, not general recommended defaults.

Hybrid SDLC writes only configured values to a short-lived Aider model-settings file under the special `aider/extra_params` entry and passes its path to the Aider process. That special entry can replace other values an operator has placed in the same global extra-parameters slot. Named model defaults remain available, but arbitrary ambient extra parameters are neither read nor copied. The file is removed after Aider exits, including timeout, cancellation, and launch-error paths.

For synchronous CLI runs, `--aider-max-tokens` and `--aider-reasoning-budget-tokens` override the repository TOML values. MCP tools use the repository configuration; the five tool schemas do not add budget arguments.

## Trust Model & Named Command Profiles

> [!WARNING]
> Named command profiles are designed for **trusted repositories**. The policy guard protects against path traversal, shell injection, and leaked environment variables, but it **does not provide OS sandboxing**.
> Subprocesses defined in command profiles execute with the current user's operating system privileges. When testing untrusted third-party code, run `hybrid-sdlc` inside a container or isolated virtual machine.

### Profile Structure

Command profiles define how tests are executed:
- `argv: list[str]`: An explicit list of command line arguments (e.g. `["pytest", "-v"]`). Commands are executed directly with `shell=False` to prevent shell injection.
- `cwd: str`: A relative path from the repository root (e.g. `"."` or `"packages/frontend"`). Parent path traversal (`..`) is strictly prohibited.
- `timeout_seconds: int`: Maximum execution duration before the process tree is forcibly terminated.
- `env_allowlist: list[str]`: List of specific environment variables passed from the parent process into the sanitized subprocess environment.

## Supported Framework Examples

See [`hybrid_sdlc.example.toml`](../hybrid_sdlc.example.toml) for reference definitions including:
- **Pytest**: `["pytest", "-v"]`
- **Unittest**: `["python", "-m", "unittest", "discover", "-s", "tests"]`
- **NPM**: `["npm", "test"]`
- **PNPM**: `["pnpm", "test"]`
- **Vitest**: `["npx", "vitest", "run"]`
- **Cargo (Rust)**: `["cargo", "test"]`
- **Go**: `["go", "test", "./..."]`
