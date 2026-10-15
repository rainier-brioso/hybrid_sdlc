# Clean-machine release rehearsal

HSDLC-069 requires evidence from a clean Windows environment and a clean POSIX
environment. This procedure is a manual operator protocol; a packaging smoke
test is useful artifact evidence but does not replace either clean-client run.
Do not claim the task complete from the current development checkout, an
editable install, or a host that has unresolved local changes. HSDLC-065 and
HSDLC-068 remain open dependencies: prepare this protocol now, then execute it
only after their release inputs are resolved.

Record one sanitized JSON record per operating system with
[`release-rehearsal-record.template.json`](release-rehearsal-record.template.json).
Keep full logs, patches, prompts, credentials, and personal paths out of that
record. Retain any necessary private evidence outside the fixture checkout.
Use actual run/job IDs, UTC timestamps, and stable path aliases such as
`<REHEARSAL_ROOT>` and `<UV_TOOL_DIR>`.
Do not invent correlation IDs. This is an operator evidence record, not a
runtime API schema or an automatic validator.

## 1. Prepare independent clients and artifact

1. Start from a verified commit and record its full commit ID. Use a fresh
   Windows account or VM and a separate fresh POSIX account/machine/VM. A new
   account on an existing host is acceptable only if its user environment is
   clean and host-wide prerequisites/limitations are recorded; it does not make
   the OS freshly installed. Neither may reuse the development user's tool
   environment. Keep the source checkout
   and build output separate from both clients.
2. From the verified commit, build one wheel and one sdist. Record each
   artifact's filename and SHA-256. Transfer the same wheel bytes to both
   clients and verify the digest after transfer. The sdist build is supporting
   release evidence; both client rehearsals install the recorded wheel.
3. Install Hybrid SDLC from that wheel into a fresh `uv` tool environment with
   Python 3.12. Install Aider separately as `aider-chat==0.86.2` in its own
   Python 3.12 `uv` tool environment. Do not combine their interpreters.
4. Run outside every source checkout and fixture tree. Scrub inherited
   `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV`; also inspect `uv` project
   overrides. Do not set `PYTHONPATH` to make imports work. Do not use editable
   installs or add a source tree to `PATH`.
5. In each fixture's ignored local TOML, set `aider_python` to the absolute interpreter
   obtained from `uv tool dir` (not `uv tool dir --bin`). Confirm that exact
   interpreter reports Aider 0.86.2. Record only an alias for its path.

If a sdist-built wheel is the candidate release artifact, rebuild it from the
verified sdist once and explicitly designate that exact resulting wheel as the
candidate; hash it and distribute those exact bytes to both clients. Never
silently compare a wheel built on one client with a different local build on
the other.

Producer commands, from the verified toolkit checkout:

```sh
git status --porcelain
git rev-parse HEAD
uv build --sdist --wheel --out-dir dist/rehearsal-candidate
```

Require a clean producer tree and a new output directory, or explicitly review
its existing contents. Filename/version alone does not identify a build. Retain
expected wheel and sdist digests outside the fixture. On each clean client,
verify the transferred wheel **before** installation. PowerShell:

```powershell
$wheel = 'C:\rehearsal\artifacts\ACTUAL_WHEEL_FILENAME.whl'
$verifyPython = uv python find 3.12
& $verifyPython -I -c "import hashlib,sys; from pathlib import Path; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())" $wheel
uv tool list
uv tool install --python 3.12 $wheel
uv tool install --python 3.12 'aider-chat==0.86.2'
```

POSIX:

```sh
wheel="$HOME/rehearsal/artifacts/ACTUAL_WHEEL_FILENAME.whl"
verify_python="$(uv python find 3.12)"
"$verify_python" -I -c 'import hashlib,sys; from pathlib import Path; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())' "$wheel"
uv tool list
uv tool install --python 3.12 "$wheel"
uv tool install --python 3.12 'aider-chat==0.86.2'
```

Compare the hash with the producer record and stop on mismatch. The initial
inventory must not contain Hybrid SDLC; do not use `--force` to claim a clean
install. Python 3.12, uv and Git must already be available. Dependency resolution
may use network; no model download/runtime start is part of these commands.

## 2. Verify the installed artifact and origins

On each client, verify the CLI version/help and import origins while outside
the checkout. Use the installed tool's Python directly, with isolated mode
(`-I`) and without `uv tool dir --bin`. Check the package and the CLI/MCP
modules used by the smoke. Every resolved module path must be beneath the
Hybrid SDLC tool environment's installed package directory. Record the
redacted resolved origins and command exit statuses. Stop if any import comes
from a checkout, user site, or another environment.

Also record the exact wheel digest installed, Python and `uv` versions, OS
release, and installed package version. `scripts/packaging_smoke.py` checks
distribution contents and isolated installation behavior, but it does not
exercise real model delegation or host disconnect/reconnect and cannot count
as a rehearsal pass.

PowerShell path discovery and a non-generating origin check:

```powershell
$toolRoot = uv tool dir
$toolEnv = Join-Path $toolRoot 'hybrid-sdlc'
$toolPython = Join-Path $toolEnv 'Scripts\python.exe'
$toolCli = Join-Path (uv tool dir --bin) 'hybrid-sdlc.exe'
& $toolCli --version
& $toolCli --help
& $toolPython -I -m hybrid_sdlc --help
& $toolPython -I -c "import sys,hybrid_sdlc,hybrid_sdlc.cli,hybrid_sdlc.mcp_server; from pathlib import Path; from importlib.metadata import distribution; root=Path(sys.prefix).resolve(); package=Path(distribution('hybrid-sdlc').locate_file('hybrid_sdlc')).resolve(); paths=[Path(m.__file__).resolve() for m in (hybrid_sdlc,hybrid_sdlc.cli,hybrid_sdlc.mcp_server)]; assert root==Path(sys.argv[1]).resolve(); assert package.is_relative_to(root); assert all(p.is_relative_to(package) for p in paths); print(root,package,*paths,sep='\n')" $toolEnv
```

POSIX uses the same check with these paths:

```sh
tool_env="$(uv tool dir)/hybrid-sdlc"
tool_python="$tool_env/bin/python"
tool_cli="$(uv tool dir --bin)/hybrid-sdlc"
"$tool_cli" --version
"$tool_cli" --help
"$tool_python" -I -m hybrid_sdlc --help
"$tool_python" -I -c 'import sys,hybrid_sdlc,hybrid_sdlc.cli,hybrid_sdlc.mcp_server; from pathlib import Path; from importlib.metadata import distribution; root=Path(sys.prefix).resolve(); package=Path(distribution("hybrid-sdlc").locate_file("hybrid_sdlc")).resolve(); paths=[Path(m.__file__).resolve() for m in (hybrid_sdlc,hybrid_sdlc.cli,hybrid_sdlc.mcp_server)]; assert root==Path(sys.argv[1]).resolve(); assert package.is_relative_to(root); assert all(p.is_relative_to(package) for p in paths); print(root,package,*paths,sep="\n")' "$tool_env"
```

Compare `sys.prefix` with the expected tool environment too. Do not derive it
from the resolved Python executable: POSIX venv interpreters may be symlinks
to shared Python installations. Record installed dependency versions, including
MCP, and redact local paths before publishing evidence.

## 3. Establish a safe, real fixture

Create one disposable, credential-free Git fixture outside the installed tool
directory and any source checkout. Commit its baseline and create a Git bundle
from that exact commit. Record the baseline commit/tree Git object IDs, spec
blob object ID and bundle SHA-256. Transfer the same bundle to both clients; clone
it to make the Windows and POSIX fixtures, verifying those identities
on both. Keep the baseline clone untouched and make distinct clones for the
synchronous and asynchronous task runs. This gives both OS rehearsals the
same input while keeping task worktrees independent. Use the committed ignore
rules from the [installation fixture](installation.md#2-prepare-a-disposable-first-run-repository):
`.hybrid_sdlc/`, `__pycache__/` and local `hybrid_sdlc.toml` are ignored.
Create the local config for each clone; never put credentials or machine paths
in the bundle. Use profile ID `smoke` and the installed Hybrid SDLC environment's
absolute Python for the standard-library unittest command. Do not rely on a
source `.venv` or a global Python assumed to exist. Select the verified Aider
interpreter and a positive map budget for a map-enabled adapter run, recording
its settings. Local configuration may differ only for required endpoint/path
selection; record each config's digest and explain those differences.

From the producer's disposable fixture, not the toolkit checkout:

```sh
git -C /path/to/disposable-fixture rev-parse HEAD
git -C /path/to/disposable-fixture rev-parse 'HEAD^{tree}'
git -C /path/to/disposable-fixture rev-parse HEAD:spec.md
git -C /path/to/disposable-fixture bundle create /path/to/artifacts/release-smoke.bundle HEAD
```

Hash the bundle like the wheel. On each client, from its own rehearsal root:

```sh
git clone /path/to/artifacts/release-smoke.bundle fixture-baseline
git clone /path/to/artifacts/release-smoke.bundle fixture-sync
git clone /path/to/artifacts/release-smoke.bundle fixture-async
git -C fixture-sync rev-parse HEAD
git -C fixture-async rev-parse HEAD
git -C fixture-sync status --porcelain
git -C fixture-async status --porcelain
```

Use equivalent quoted Windows paths. Match the baseline, tree and spec object
IDs with the producer. Keep the clones separate and never apply the result
patches to them. Git object IDs avoid confusing Windows working-file line
endings with changes to the committed fixture; check actual source Git status
before and after both tasks too.

Configure an actual reachable OpenAI-compatible inference endpoint and record
sanitized endpoint and model identity. Do not include tokens, private URLs, or
request content. No mock, stub, canned response, synthetic worker, Docker
binding, or service/container change is valid evidence of delegation. If no
real endpoint is available, mark the dependent steps blocked. A VM's localhost
is not the Windows host's localhost: select an operator-approved reachable
endpoint without weakening bindings, starting another model or changing the
existing service. Schedule clients sequentially and resolve active processing
before another inference. Preserve the
same committed baseline identity on both OS clients; local ignored
configuration may differ only where paths or local executable resolution
require it.

Use only explicit permitted fixture files and requested tests. First run the
non-generating health check. Then perform one real synchronous fixture task.
Review the actual returned patch and run the requested case tests independently
against the task worktree. Verify the original fixture baseline remains
unchanged and that no patch was automatically applied to it. Health success
alone is not delegation evidence; a health check with generation enabled is
not the non-generating health step.

Using the absolute installed CLI discovered above, PowerShell:

```powershell
& $toolCli check --repo-root 'C:\rehearsal\fixture-sync' --json
& $toolCli run-task spec.md --repo-root 'C:\rehearsal\fixture-sync' --task-id SMOKE-001 --test-profile smoke --json
```

POSIX:

```sh
"$tool_cli" check --repo-root "$HOME/rehearsal/fixture-sync" --json
"$tool_cli" run-task spec.md --repo-root "$HOME/rehearsal/fixture-sync" --task-id SMOKE-001 --test-profile smoke --json
```

Require multiplication implementation and zero/positive/negative test cases,
with `add_one` preserved. Rerun the profile inside the returned isolated
checkout with the selected Python. Save run/test exit codes, patch SHA-256,
expected/actual changed files and source status. Do not copy prompt, test source
or patch contents into the sanitized record.

## 4. Prove async continuity through the real host

Register the absolute installed stdio server using the documented setup for the
actual [host being rehearsed](host-compatibility.md). Record its version and
adapter PID/start identity privately, with stable aliases in public evidence.
Submit a real fixture job through that host and poll until its persisted
status is `running`. Only then disconnect the actual host-owned adapter using
the host's supported disconnect method; close only the adapter/connection
owned by this rehearsal. Do not terminate a whole host job/container or a
worker to simulate a disconnect.

Reconnect through the same host and query the same job. Record evidence that
the old adapter PID exited, a new adapter PID connected, and the original job
ID and worker identity continued. Observe the job reach `completed`; inspect
its result, review the patch, and independently run the requested case tests.
Poll again until a terminal state whenever a query returns `queued` or
`running`. CLI status waits are bounded at 60 seconds; repeat them as needed.
For MCP, call `get_job_status` with its `repo_root`, `job_id`,
`wait_timeout_seconds`, and `poll_interval_seconds` fields and likewise
continue until terminal.

Call `submit_spec_job` with the following arguments, replacing the absolute path:

```json
{
  "repo_root": "REPLACE_WITH_ABSOLUTE_ASYNC_FIXTURE_PATH",
  "spec_path": "spec.md",
  "task_id": "SMOKE-001",
  "test_profile": "smoke"
}
```

Retain the actual returned job ID and call `get_job_status` with:

```json
{
  "repo_root": "REPLACE_WITH_ABSOLUTE_ASYNC_FIXTURE_PATH",
  "job_id": "REPLACE_WITH_ACTUAL_JOB_ID",
  "wait_timeout_seconds": 0,
  "poll_interval_seconds": 0.25
}
```

Use an immediate status observation before disconnect. After reconnect, waits
may use up to 60 seconds. CLI `submit` followed by another CLI `status` process
is **not** MCP disconnection evidence. Record disconnect/reconnect UTC times,
observed worker PID/creation/start-token identity and how continuity was checked.
A PID alone does not exclude PID reuse.

A job that was merely queued, completed before the adapter was disconnected,
or never observed running during the disconnect window is inconclusive, not a
pass. If the host does not expose a safe, supported adapter-only disconnect,
mark async reconnect blocked; do not invent an automated MCP tool or kill the
whole host to manufacture evidence.

The evaluated Python MCP stdio SDK's default Windows Job Object can prevent
worker breakaway. If the actual host reports `ASYNC_JOB_HOST_UNSUPPORTED`,
record that exact outcome as a fail-closed host limitation and mark async
reconnect `blocked` for that unsupported environment. Verify privately that
no new job record was allocated and save the refusal code. Do not patch the SDK, use
private Job Object APIs, inject `sitecustomize`, or fake/preflight a worker to
manufacture survival. Such an outcome does not pass HSDLC-069's async
acceptance criterion.

Establish the host's actual disconnect behavior before the test: closing a
client can terminate more than its adapter. Do not disable containment to make
the test pass. If the task finishes too quickly, record the attempt as
inconclusive and arrange a separately approved bounded task/window, without
fake workers or concealed failed attempts.

## 5. Clean up and uninstall

Before cleanup, account for every submitted job. Require every job to be
terminal and confirm remote processing is known to have finished. Review the
`clean --older-than 0s --dry-run` output first; verify it lists only known
artifacts from this fixture and a nonzero count. Then run the matching cleanup
without `--dry-run` and record the count and sanitized IDs. If the preview
contains unknown artifacts or any external work remains active, stop and mark
cleanup blocked. Do not run broad directory deletion, remove uv caches, or
delete an external worktree.

PowerShell, with the installed CLI and this rehearsal's fixture only:

```powershell
& $toolCli clean --repo-root 'C:\rehearsal\fixture-sync' --older-than 0s --dry-run --json
& $toolCli clean --repo-root 'C:\rehearsal\fixture-sync' --older-than 0s --json
```

POSIX:

```sh
"$tool_cli" clean --repo-root "$HOME/rehearsal/fixture-sync" --older-than 0s --dry-run --json
"$tool_cli" clean --repo-root "$HOME/rehearsal/fixture-sync" --older-than 0s --json
```

Repeat only for the known async fixture after its jobs/writers have finished.
An empty preview is not deletion evidence. Save the known record IDs and private
evidence outside both fixtures before cleanup; verify eligible records are
gone afterward and baseline source files remain unchanged. External patches,
worktrees, map caches, runtime definitions and model volumes are not targets.

Close all host sessions/adapters using their normal UI or supported command.
Only after the host is closed, uninstall Hybrid SDLC from the fresh `uv` tool
environment and verify the package and CLI entry point are absent. Preserve
the Aider installation, its configuration, model weights, and unrelated tool
environments. Record any intended fixture files retained and the verification
result. If any remote job or endpoint processing may still be active, defer
uninstall and record cleanup as blocked instead of claiming success.

On the clean client's own tool environment, after stopping its adapters:

```sh
uv tool uninstall hybrid-sdlc
uv tool list
```

Verify the package is absent from that inventory and the recorded absolute
launcher no longer exists. Do not query a different global Python/launcher and
call it proof of removal. Remove only this rehearsal's host registration using
its guide, preserving other entries. Do not use `uv tool uninstall --all`.

## 6. Evaluate evidence

Each OS record must cover artifact/install, import origins, availability,
synchronous delegation, asynchronous disconnect/reconnect, cleanup, and
uninstall. Use `passed` only when the stated observations are present. Use
`failed` for a tested criterion that failed, `blocked` when it could not be
safely or practically exercised, `skipped` only for an intentionally omitted
step, and `not_run` for untouched steps. Skipped, blocked, failed, and not-run
steps do not count as passes. Do not mark HSDLC-069 complete unless both OS
records pass every required phase, prerequisite gates are resolved, and reviewers can verify the private
evidence without exposing it in the sanitized record.

Documentation checks for this protocol: JSON parse, local-link resolution,
CLI/help syntax review, and `git diff --check`. These checks do not execute a
release rehearsal.
