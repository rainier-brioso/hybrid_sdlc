# Configuration Guide

The Hybrid SDLC Toolkit (`hybrid-sdlc`) relies on a configuration file named `hybrid_sdlc.toml` located at the root of your target Git repository.

## Configuration Precedence

Settings are resolved in the following priority order:
1. **CLI Arguments & Flags** (highest priority)
2. **Repository Configuration** (`hybrid_sdlc.toml` at repo root)
3. **Built-in Defaults** (lowest priority)

Configuration loading is strictly confined to the repository root. `hybrid-sdlc` will never traverse parent directories looking for configuration files.

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
