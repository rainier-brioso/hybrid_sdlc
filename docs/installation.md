# Installation and first run

This guide describes the current development build, not a fully validated
supported release. Comparative benchmarks and the clean Windows/POSIX release
rehearsal remain open. Use only trusted repositories, commands, endpoints and
host executables; see [security boundaries](security.md).

## 1. Prerequisites and installation

- Hybrid SDLC requires Python **3.11–3.13**, Git and a configured Git commit
  identity. These examples use Python 3.12 and uv.
- Aider is installed separately. The optional cache adapter supports
  **aider-chat 0.86.2 only**; that Aider version requires Python 3.10–3.12.
  Toolkit Python compatibility does not imply Aider compatibility.
- A trusted OpenAI-compatible endpoint must serve the exact configured model
  ID. Installing the CLI does not start inference or download model weights.
- Docker and NVIDIA support are prerequisites only for the optional model
  deployment. The CLI and stdio MCP server run natively on the host.

From a terminal, install from the source checkout:

```sh
git clone https://github.com/rainier-brioso/hybrid_sdlc.git
cd hybrid_sdlc
uv tool install --python 3.12 .
uv tool install --python 3.12 'aider-chat==0.86.2'
hybrid-sdlc --version
hybrid-sdlc --help
aider --version
```

To install a locally built wheel instead, use its actual path:

```sh
uv tool install --python 3.12 /path/to/hybrid_sdlc-0.1.0-py3-none-any.whl
```

PowerShell accepts an equivalent quoted Windows path, for example
`'C:\packages\hybrid_sdlc-0.1.0-py3-none-any.whl'`. Neither method installs the
target repository's test dependencies or host/plugin registration.

For toolkit development, use a separate source environment:

```sh
uv sync --python 3.12 --frozen --group dev
uv run hybrid-sdlc --help
```

### PATH and executable discovery

uv reports environment storage and launcher storage separately:

```sh
uv tool dir
uv tool dir --bin
uv tool list
```

The directory printed by `--bin` must be on the PATH inherited by your terminal
and agent host. After PATH changes, restart those processes. In PowerShell:

```powershell
Get-Command hybrid-sdlc, aider, python | Select-Object Name, Source
```

On POSIX:

```sh
command -v hybrid-sdlc
command -v aider
command -v python
```

Use the actual absolute executable path in host registration if PATH cannot be
made consistent. A registration does not install the package. For the test
profile below, select an existing trusted external Python environment and put
its executable directory first on the terminal/host's `PATH`. Use its bare
command name (`python` or `python.exe`) in `argv[0]` and verify which interpreter
it resolves to. Do not invent an installation path or rely on a source-local
`.venv` being copied. Command profiles reject literal executable paths outside
the target repository; this is separate from the absolute `aider_python` setting.

## 2. Prepare a disposable first-run repository

Keep the toolkit checkout separate from the repository being edited. For
example, in PowerShell:

```powershell
New-Item -ItemType Directory -Path 'C:\work\hsdlc-smoke'
Set-Location 'C:\work\hsdlc-smoke'
git init
New-Item -ItemType Directory -Path tests
```

POSIX equivalent:

```sh
mkdir -p "$HOME/work/hsdlc-smoke/tests"
cd "$HOME/work/hsdlc-smoke"
git init
```

Choose a new directory; do not replace an existing project. Create these files
in your editor. `calculator.py`:

```python
def add_one(value: int) -> int:
    return value + 1
```

`tests/test_calculator.py`:

```python
import unittest
from calculator import add_one


class CalculatorTests(unittest.TestCase):
    def test_add_one(self):
        self.assertEqual(add_one(1), 2)
```

`.gitignore`:

```gitignore
__pycache__/
.hybrid_sdlc/
hybrid_sdlc.toml
```

`spec.md`:

```markdown
# SMOKE-001

Add multiply_by_two(value: int) -> int to calculator.py, returning value * 2.
Preserve add_one. Add unittest cases for zero, positive and negative values in
tests/test_calculator.py, preserving the existing test. Only those two files
may change. Do not change config, ignore rules or this spec. Do not commit.
```

`hybrid_sdlc.toml` (replace the model ID with the server's exact advertised ID):

```toml
selected_model = "qwen3.8-flash-next-coder-iq1_m"
attempt_timeout_seconds = 300
task_timeout_seconds = 600
max_retries = 2
aider_edit_files = ["calculator.py", "tests/test_calculator.py"]
aider_repo_map_tokens = 0

[[server_candidates]]
url = "http://127.0.0.1:8080/v1"

[command_profiles.smoke]
argv = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
cwd = "."
timeout_seconds = 30
env_allowlist = []
```

This deliberately small example disables the repository map. It is not a
general map/timeout recommendation. For map-enabled work, configure the
cache-isolated adapter below and choose appropriate context and deadlines.
`aider_edit_files` supplies context, **not an enforced edit allowlist**.

For Strata, optional experimental request budgets are 4096 output tokens and
1024 reasoning tokens; add `aider_max_tokens = 4096` and
`aider_reasoning_budget_tokens = 1024` at the top level, before the TOML tables,
if explicitly choosing those limits. The whole prompt plus response must fit;
the reasoning threshold is not extra context or an exact cap. See
[configuration](configuration.md). Endpoint userinfo, queries and fragments are
rejected, even non-secret query parameters. Aider input/output is UTF-8.

Run the baseline tests, then commit the fixture:

```sh
python -m unittest discover -s tests -v
git add .gitignore calculator.py tests/test_calculator.py spec.md
git commit -m "Create trusted Hybrid SDLC smoke fixture"
```

For a real repository, preserve existing settings rather than copying this
example wholesale. The spec must be committed and unchanged. Other dirty source
files may remain, but they and untracked files are **not copied** to the task
worktree. Config itself is loaded from the source; committing it is not a runner
requirement. This fixture ignores its local config; machine-specific settings
should remain local.

Named test profiles run trusted code with your account's privileges. Install
their dependencies separately. A source-local `.venv` is not copied; a profile
resolving to its executable fails closed if missing in the isolated checkout.
Use a suitable trusted external executable or provision the isolated runtime;
do not silently test the source checkout. See [worktrees](worktrees.md).

### Optional map-enabled Aider adapter

Find the Aider interpreter using the environment root from `uv tool dir`,
**not** the launcher directory from `uv tool dir --bin`. PowerShell:

```powershell
$aiderPython = Join-Path (uv tool dir) 'aider-chat\Scripts\python.exe'
& $aiderPython -I -c "from importlib.metadata import version; print(version('aider-chat'))"
$aiderPython
```

POSIX:

```sh
aider_python="$(uv tool dir)/aider-chat/bin/python"
"$aider_python" -I -c "from importlib.metadata import version; print(version('aider-chat'))"
printf '%s\n' "$aider_python"
```

Verify 0.86.2 and the actual path. In the target TOML's top level, set
`aider_python` to that absolute interpreter path and replace the zero map
budget with a positive value, for example 1024. Do not use `aider.exe` or
the toolkit's interpreter. The adapter keeps map caches outside the checkout
and retains them for review; it does not sandbox Aider or hide unexpected edits.
For a non-uv Aider installation, locate its actual interpreter yourself.

## 3. Availability, synchronous execution and review

Ensure your chosen endpoint is already running, then from the target root:

```sh
hybrid-sdlc check --repo-root . --json
hybrid-sdlc run-task spec.md --repo-root . --task-id SMOKE-001 --test-profile smoke --json
```

`check` uses model-list requests without generating. Availability is not
inference readiness; explicitly adding `--readiness` generates a small request.
Do not probe inference alongside unresolved work. Managed coordination tracks
participating clients only, not unrelated endpoint clients.

The result provides status, run ID, tests and an isolated `worktree_path` and
`review_patch`. Review the exact patch, allowed files and requested cases, then
rerun the tests inside that worktree. Passing tests never apply the patch to
the source. From the source checkout, after review, substitute the actual path:

```sh
git apply --binary --check /path/to/result.patch
git apply --binary /path/to/result.patch
python -m unittest discover -s tests -v
git diff
```

PowerShell accepts the equivalent quoted Windows patch path. The source must
match the recorded baseline/context; if the check fails, stop and inspect the
conflict. Do not reset a dirty checkout. Exact patches can contain private
source and secrets; keep them private. See [worktree result review](worktrees.md).
`--commit` is an explicit synchronous opt-in for an isolated task commit after
tests pass; it still does not merge into your source branch. The example spec
above forbids committing, so do not add that flag to this smoke.

## 4. Asynchronous jobs and MCP

Where the host supports detached workers:

```sh
hybrid-sdlc submit spec.md --repo-root . --task-id SMOKE-001 --test-profile smoke --json
hybrid-sdlc status ACTUAL_JOB_ID --repo-root . --wait --json
```

Replace `ACTUAL_JOB_ID` with the returned ID and poll again until `completed`,
`failed` or `cancelled`; `queued` and `running` are nonterminal. Each status wait
is bounded to 60 seconds. A successful status query's exit code does not mean
the job succeeded: inspect its status/result. Keep the source fixture at its
original baseline if repeating this task; do not submit it after applying the
earlier patch and expect an identical test.

Cancellation is a request:

```sh
hybrid-sdlc cancel ACTUAL_JOB_ID --repo-root . --json
hybrid-sdlc status ACTUAL_JOB_ID --repo-root . --wait --json
```

Poll to a terminal state. Client-side cancellation/timeout does not prove the
remote inference request ended. On restrictive Windows Job Object hosts,
background submission fails closed; use synchronous execution. Supported-host
Windows survival and host wakeup remain unverified; see
[host compatibility](host-compatibility.md).

The host-native stdio server is `hybrid-sdlc mcp`. Registration is explicit and
does not require Docker. Follow the existing [Codex](hosts/codex.md),
[Antigravity](hosts/antigravity.md) or [Claude Code](hosts/claude-code.md) guide.
Avoid duplicate plugin/manual registration, preserve unrelated servers, and
reload/start a new host session to discover tools. Do not print diagnostics to
the MCP stdout protocol stream.

## 5. Planning and optional runtime ownership

Write a short committed task contract for a fast modification, or a dependency
plan with one contract per task for a larger change. See
[planning and migration](planning.md). There is no root `hybrid-sdlc init`
command or `specify` requirement. Existing user planning files and old
initializer backups remain untouched. Runtime initialization is independent.

For manual model deployment, read [Strata Docker setup](strata-server-docker.md)
or the retained [llama.cpp setup](llama-server-docker.md). For separately owned
CLI lifecycle operations, read [runtime management](runtime-management.md).
Installing the package does not build an image, start a model, adopt a manual
container, register a host, or perform automatic recovery. Do not run two model
services on the same GPU/port/volume. Never restart automatically after one
timeout; inspect remote activity first.

## 6. Cleanup, upgrades and uninstall

Preview before deleting eligible repository artifacts:

```sh
hybrid-sdlc clean --repo-root . --older-than 7d --dry-run --json
hybrid-sdlc clean --repo-root . --older-than 7d --json
```

Use the same threshold after reviewing the preview. This skips protected/active
locks and targets this repository's `.hybrid_sdlc` only. It does **not** remove
external retained worktrees, patches/map caches, initialization backups,
runtime configuration or model volumes. Inspect those separately; do not delete
computed broad directories or ambiguous activity records.

Before reinstalling or uninstalling, finish/cancel jobs and close hosts/MCP
processes that use the tool environment. From the toolkit source checkout:

```sh
uv tool install --python 3.12 --force .
```

Restart hosts afterward. On Windows, an open executable can prevent replacing
the environment; see [troubleshooting](troubleshooting.md). To remove the CLI:

```sh
uv tool uninstall hybrid-sdlc
uv tool list
```

Remove host registration separately using its guide, preserving other servers.
For Antigravity, manually remove only its `hybrid-sdlc` user-config entry after
closing the host. Uninstall does not stop a model or remove user configuration,
artifacts, weights or volumes. Aider is a separate tool: leave it installed
unless you explicitly want to uninstall that too.
