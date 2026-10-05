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

The setting accepts a non-negative integer. Omission leaves Aider's default unchanged. Setting it to `0` disables the repository map, which can avoid Aider creating `.aider.tags.cache.v4` during a small task with explicitly named files. That also removes repository-wide symbol context and is best limited to tasks whose relevant files are listed directly. A positive value retains the map with the requested token budget. For map-enabled tasks, Aider may still create its repository-map cache in the task checkout; cache handling remains unresolved.

## Aider Edit Context Files

`aider_edit_files` optionally preloads selected, committed repository files into every Aider task:

```toml
aider_edit_files = ["src/example.py", "tests/test_example.py"]
```

Omit the setting (or use an empty list) to preserve Aider's normal context selection. Entries must be unique, non-empty repository-relative file paths. Absolute paths, traversal, the task specification itself, untracked or modified files, directories, and symlinks are rejected before Aider runs. The setting does not infer file paths from the specification.

These paths provide initial context and are **not** an edit allowlist or a security boundary: Aider may still edit other repository files. Use repository permissions and the existing isolated-worktree review flow to inspect changes.

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
