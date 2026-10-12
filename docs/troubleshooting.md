# Troubleshooting

Start with the installed command's `--version` and `--help`, command/exit code,
structured error, run/job ID and UTC timestamps. A globally installed tool may
be older than your checkout; for source diagnostics use `uv run hybrid-sdlc` in
the toolkit checkout. Do not share credentials, full task text, private source
or exact patches in a support report. See [installation](installation.md) and
[security](security.md).

## Command not found or tools missing in a host

```sh
uv tool list
uv tool dir --bin
```

Check the reported launcher directory is on the PATH inherited by that exact
terminal/host. PowerShell: `Get-Command hybrid-sdlc, aider, python`; POSIX:
`command -v hybrid-sdlc` and `command -v aider`. Restart the terminal and host
after changing PATH. Use an actual absolute executable path if the host cannot
resolve the command. An MCP registration is configuration, not package install.

The stdio server command is `hybrid-sdlc mcp`. Register it once, preserve other
servers, then reload/start a new session and confirm all five tools. A loaded
skill does not imply MCP discovery, and discovery does not prove a model/task
works. Use [host guides](host-compatibility.md), including the documented
Antigravity user-config proposal workflow. Do not add shell banners/debug
messages to MCP stdout; it carries protocol messages.

## Missing module or locked Windows reinstall

`ModuleNotFoundError: hybrid_sdlc` can indicate a broken tool environment or a
launcher pointing at a different installation. Compare `uv tool list`, command
discovery and the absolute command stored in the host config. Do not just add
the source directory to global `PYTHONPATH` or assume a launcher proves install.

Before repairing the tool, finish/cancel its jobs and close the hosts/MCP
processes using it. From the toolkit checkout:

```sh
uv tool install --python 3.12 --force .
```

`Access is denied` while replacing `Scripts` often means a running process holds
the executable or environment open. In PowerShell, inspect only matching
processes; this command does not terminate anything:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'hybrid-sdlc.exe'" |
    Select-Object ProcessId, ParentProcessId, ExecutablePath
```

Close the owning host/server normally, then retry. Confirm ownership and active
jobs before considering explicit process termination; do not blindly kill every
Python process. If locks or permission failures remain, investigate them rather
than deleting the uv tools directory. Restart hosts after a successful reinstall.

For development only, `uv sync --python 3.12 --frozen --group dev` creates the
toolkit's separate environment. It does not repair every user-installed launcher
or provision a target project's test environment.

## Aider interpreter, cache adapter or test executable errors

- Verify `aider --version` and PATH when `aider_python` is unset. Installing
  Hybrid SDLC does not install Aider. The ordinary launch can create a map cache
  inside the checkout; inspect it as part of the exported patch.
- For the cache adapter, use the absolute interpreter containing **aider-chat
  0.86.2**, not `aider.exe` or the toolkit interpreter. Derive its environment
  from `uv tool dir`, not `uv tool dir --bin`; verify the version with that
  interpreter. `AIDER_ADAPTER_*` errors require fixing the interpreter/version
  or incompatible private API. There is no automatic interpreter discovery or
  fallback to disabling maps. See [configuration](configuration.md).
- The toolkit supports Python 3.11–3.13; Aider 0.86.2 requires Python below
  3.13. Separate Python 3.12 tool environments avoid confusing those contracts.
- A named profile's command must exist and be executable. A repository-local
  `.venv/Scripts/pytest.exe` or `.venv/bin/python` is not copied into the isolated
  worktree. A missing local executable fails closed, rather than testing the
  source. Choose a suitable trusted external executable or provision that
  isolated runtime. Review interpreter selection; PATH and approved scripts
  are trusted inputs, not an OS sandbox.

`aider_edit_files` are committed initial context paths, not an edit allowlist.
The spec/context files must be unchanged and committed; other source dirt is
preserved but not copied to the task. See [worktrees](worktrees.md).

## Connection failure, model not found or readiness false

1. Confirm the intended endpoint is running and the target repository's
   `hybrid_sdlc.toml` uses the correct base URL and exact advertised model ID.
2. Run `hybrid-sdlc check --repo-root . --json`. Default checks contact model-list
   endpoints without generating; `--probe-all` contacts all configured candidates.
   Inspect `available`, `matched_model` and `error`, not only the exit code.
3. Availability is not inference readiness. `--readiness` explicitly generates
   a small response; use it only when no unresolved task/request makes another
   inference unsafe. Managed activity guards cover participating clients only.
4. `CONNECT_ERROR` indicates the connection failed. `MODEL_NOT_FOUND` or
   `SERVER_PROBE_FAILED` can mean no candidate provided the selected model.
   Preserve per-candidate error evidence; neither proves a server stall.

Endpoint userinfo, query/fragment delimiters, whitespace and malformed escapes
are rejected, including non-secret query parameters and explicit empty
overrides. Use a supported credential-free local URL; do not move credentials
into paths or model names. Unsupported authentication is not silently stripped.

Task instructions use temporary UTF-8 files outside source/checkout/retained
attempt storage. Ambient Aider message/encoding settings cannot override that
delivery. If custom TEMP/TMPDIR points inside protected task storage, choose
normal private user temporary storage outside those roots. Abrupt termination
can leave temporary data, and same-user/privileged processes can inspect it;
this is not encrypted memory. Non-UTF-8 ambient Aider encoding is not preserved.

## Empty patch, `LOOP_ZERO_DIFF`, context or thinking limits

`LOOP_ZERO_DIFF` reports an empty change result; it does not identify the cause.
Aider may have produced no change, encountered a request/context error or
exhausted output before answering. Inspect run failure/test metadata and the
provider's available status/logs privately. Not every provider/Aider error is
currently preserved in the structured result. Do not label an empty patch a
successful task or proven model/server failure without evidence.

Context, response output and reasoning are distinct budgets. In an observed
16,384-token Strata context, a 10,573-token prompt left a server-reported maximum
output of 5,803; asking for 8,192 was rejected before generation. Hybrid SDLC
does not estimate the serialized prompt, automatically lower the response cap,
or truncate the prompt. A 4,096-token output limit is **not** a 4,096-token
context window. The 1,024-token reasoning example is a wrap-up threshold inside
the output budget, not extra context or an exact reasoning cap.

If the server exhausts output while still thinking, consider a smaller bounded
task, a suitable reasoning threshold or an output cap that fits the actual
context. These changes must be explicit; measured smoke values are not universal
defaults. Existing Strata model config is read at startup and request values
can override it. Do not restart a shared service just to experiment with
budgets. See [request/deployment precedence](configuration.md).

## Timeout, cancellation or slower inference

A client timeout or cancellation does **not** prove that remote inference
stopped. Inspect service activity and terminal job/run state before another
task, readiness request or disruptive recovery. Do not retry or restart
automatically. Preserve run ID, timestamps and capped metadata; timeout evidence
does not retain raw Aider prompt/output, but exact patches and caches remain
private repository-derived data.

For a separately configured managed runtime, a GET-only diagnostic is:

```sh
hybrid-sdlc runtime strata diagnose --json
```

Correlate one actual recent timeout explicitly; this still uses GETs only:

```sh
hybrid-sdlc runtime strata diagnose --repo-root . --run-id ACTUAL_RUN_ID --json
```

The reader requires matching endpoint/model metadata and an eligible timeout
within 30 minutes. Only a separately requested `--readiness` inference that
times out with the other required observations can yield advisory
`suspected-stalled`. That state is not causal proof. The managed CLI cannot
adopt an arbitrary manual container; use its existing operator tooling and do
not configure a second service on its port/GPU. See
[runtime management and ownership](runtime-management.md).

One [controlled idle cycle](../benchmarks/results/strata/long-idle-20261010.md)
passed both tasks with identical reviewed patches: 74.13 s before idle and
177.19 s after a conservative 25 h 30 m interval. The repeat spent about 158 s
processing its prompt. The cause of that slowdown remains unknown; a single
cycle does not establish sustained reliability or Docker/native parity.
Container uptime is not time since the last inference, and health/model GET
success does not establish working generation. Periodic inference keep-alives
were not added.

## Async submission, status and host behavior

Once submission succeeds, retain the returned job ID and poll:

```sh
hybrid-sdlc status ACTUAL_JOB_ID --repo-root . --wait --json
```

Poll again while `queued`/`running`; `completed`, `failed` and `cancelled` are
terminal. Status-command success means the record was retrieved, not that the
task succeeded. A cancellation request is not a terminal result or proof that
remote processing ended. If background submission is refused by a restrictive
Windows Job Object, polling cannot make it supported: use synchronous execution.
Windows supported-host survival/reconnect and host wakeup remain separate
unverified claims; see [compatibility evidence](host-compatibility.md).

Do not delete ambiguous/corrupt activity records to unblock work. Cooperative
locks cannot see unrelated API clients, and removing a marker does not stop its
owner. Inspect identity and actual activity before any recovery.

## Exact patches, cleanup and permissions

`result.patch` deliberately preserves exact bytes and can contain sensitive
source or credentials. Review before sharing/applying, run `git apply --check`
first, and stop on conflicts; never reset a dirty source checkout as a shortcut.
See [worktree review](worktrees.md).

```sh
hybrid-sdlc clean --repo-root . --older-than 7d --dry-run --json
```

Review the exact eligible paths before running the same command without
`--dry-run`. Cleanup is confined to this repository's `.hybrid_sdlc`; it does
not remove external patches/worktrees, map caches, initialization backups,
runtime definitions, weights or volumes. Active locks can cause files to be
skipped. Do not manually delete broad computed directories or model data.

Symlink tests may skip when the Windows account lacks the required privilege;
POSIX mode-bit checks do not prove Windows ACL behavior. Restrictive Job Object
tests also leave supported-host background survival unverified. Skips are not
passes; changing the threshold or removing assertions is not a fix.

Stop launching requests when remote processing or ownership is unresolved.
Inspect private bounded evidence, not raw public logs; recovery remains an
explicit authorized operation. For reinstall/uninstall steps, see
[installation](installation.md).
