# User-journey verification map

HSDLC-076 (2026-10-15), based on merged HSDLC-075 (`d7effa2`). This is an
evidence inventory, not a supported-release certificate. The checked
[manifest](verification-map.json) maps public CLI leaf commands and all five
MCP tools to exact automated test references, operator protocols, prerequisites
and explicit installed/live gaps. The visible `worker` command is included.
Internal helper modules are not additional CLI commands; any actual hidden CLI
leaves must be identified separately (there are none at this baseline).

## What each evidence tier means

| Tier | What it establishes | What it does not establish |
| --- | --- | --- |
| Contract/drift checks | Current commands/tools and referenced tests/documents are accounted for | That the listed tests ran, passed, or cover every flag and failure case |
| Automated behavior | Observations made by the executed fixture, mock, real-Git or SDK tests | Real inference, Docker/GPU behavior, a particular IDE session, or clean-client installation |
| Installed artifact | Wheel/sdist installation, module origins, entry points/help and runtime asset parity in disposable environments | Installed end-to-end delegation, review acceptance, host reconnect, or service lifecycle |
| Historical live observation | The dated, narrowly described measurement in its retained record | Current-build compatibility, repeatability, comparative savings, or another host's behavior |
| Operator/clean-client proof | Only the actual observations recorded against a selected build/environment | Untested platforms or stages, or any stage marked blocked/not run/skipped |

Passing a test that uses fake Docker is not a live runtime pass. A model-list
response is availability, not inference readiness. Registration/tool discovery
is not execution. Worker success is not acceptance; acceptance is not source
integration. A CLI job surviving its submitter is not proof of an IDE-owned MCP
adapter disconnect/reconnect. Preserve these distinctions when updating records.

Manifest statuses are deliberately limited: `not_run` means no corresponding
execution observation is recorded, `blocked` identifies an unmet prerequisite
in the described environment, and `historical_observation` points to dated,
limited evidence. `automated_help_only` describes the installed distribution
check's scope, not a newly recorded pass or functional journey. None certifies
the current release; interpret each with its prerequisites and limitations.

## Journey overview

Exact node IDs and the current command/tool inventory live in the manifest.
The table below explains the evidence boundaries rather than duplicating that
inventory. Several journeys share safeguards and supporting tests.

| Journey | Automated/installed evidence | Remaining installed/live boundary |
| --- | --- | --- |
| Install and launch | Distribution archive checks, isolated wheel/sdist/rebuilt-wheel imports and help | Clean Windows and POSIX client rehearsal remains HSDLC-069 |
| Codex completion bridge | Queue CLI contracts and synthetic job/command tests; dated direct queue and installed-wheel desktop responses plus replay refusal | Queue acceptance alone does not prove a response; clean-machine installation, repeated reliability and full orchestration remain unverified |
| Endpoint availability/readiness | CLI/MCP request validation and mocked probe outcomes | Historical Strata fixture observations only; readiness generates and needs operator approval |
| Synchronous delegation and policy | CLI/MCP handlers, fake-Aider E2E, real Git isolation, committed-policy/scope checks | Dated installed-SDK and Antigravity smoke records are not current-build or real-project proof |
| Submit, poll and cancel | Persisted transitions, bounded waits, process cleanup and SDK tests | Supported-host real adapter survival is separate; restrictive Windows hosts fail closed |
| Review and acceptance | Exact-patch replay, independent profile execution, stale/missing evidence, revision races and CLI handlers | Installed help is covered; current installed real-task review is still unverified |
| Artifact cleanup | Dry-run/deletion, confinement and active-lock/receipt invalidation tests | Cleanup/uninstall in a clean-client rehearsal is not yet recorded; external worktrees/model volumes are not recursive cleanup targets |
| Managed Strata runtime | Ownership/configuration/lifecycle/diagnostic tests with fake Docker and temporary assets | No automated live installed-CLI/Docker/GPU lifecycle proof |
| Host setup and MCP transport | Temporary Antigravity config/proposal safety, shipped-host contracts, SDK handshake/discovery | Actual host reload, permissions, continuation and reconnect require host-specific observations |

Historical evidence remains dated and limited:

- [Installed SDK synchronous smoke, 2026-10-04](../benchmarks/results/strata/installed-mcp-smoke-20261004.json):
  not the closed Codex chat transport, not a clean-client rehearsal, no async proof.
- [Antigravity synchronous smoke, 2026-10-05](../benchmarks/results/strata/antigravity-mcp-smoke-20261005.json)
  and its [earlier timeout](../benchmarks/results/strata/antigravity-mcp-timeout-20261005.json):
  retain both outcomes; the IDE version was not recorded.
- [Controlled long-idle repeat, 2026-10-10](../benchmarks/results/strata/long-idle-20261010.md):
  one successful cycle, not sustained reliability or a native/Docker comparison.
- [Host compatibility](host-compatibility.md) retains its 2026-10-05 observation
  date. Adding this map does not refresh those observations.

## Reproduce the automated and installed checks

Use the [contributor environment](development.md), not the user's globally
installed tool. Normal fixture tests need Python 3.11-3.13 and Git; they do not
need a GPU, running model, Docker, or network. Some POSIX permissions and Windows
symlink/Job Object capabilities cannot be exercised on every runner. Record the
actual skips; a green job with a skip is not proof of that skipped behavior.

```sh
uv run pytest -q tests/host_contracts/test_verification_map.py
uv run pytest --cov --cov-report=term-missing -ra
```

The map guard reads Click/MCP contracts and test definitions without invoking
command callbacks, starting services, generating inference, or executing test
names from the manifest. It checks inventory/reference drift, not test quality
or live outcomes. It is part of the ordinary CI suite; `CI / Required` remains
unchanged.

For installed artifact checks, build into a new/reviewed output directory:

```sh
uv build --sdist --wheel --out-dir dist/verification-map
uv run python scripts/packaging_smoke.py --dist-dir dist/verification-map --python 3.12
```

The script uses disposable installation environments, not `uv tool install
--force` against an active user tool. Dependency resolution may require network.
The equivalent opt-in packaging pytest is enabled by
`HYBRID_SDLC_PACKAGING_DIST`; CI sets it in its packaging job. An ordinary suite
may skip that test, so report the separate packaging result explicitly.

Real-client validation uses the [release protocol](release-rehearsal.md),
[host guides](host-compatibility.md) and [Strata validation checklist](strata-validation-checklist.md).
Those are operator procedures, not instructions for the map checker to execute.
Do not start/restart models, change host registration, uninstall tools, inspect
private application data or submit paid requests merely to fill a map cell.
Use an approved disposable fixture, selected build/versions and an authorized
live window. Reconcile active jobs/endpoint work before any lifecycle action.

## Triage and maintenance

1. **Documentation drift:** a documented command/assumption disagrees with the
   actual supported contract. Correct the guidance, keep the discrepancy and
   its resolution visible, and add a behavior check when practical.
2. **Harness gap:** no observation exercises the promised boundary (for example,
   service tests without a CLI handler test, or source tests without an installed
   run). Add the missing check or retain an explicit unverified journey. A
   reference to a test name alone does not close it.
3. **Product regression:** a supported journey fails its actual acceptance
   criterion. Preserve the failure and reproduce/fix it. Do not remove its row,
   narrow the criterion, relax CI, or replace real execution with a mock to
   report a pass.

This task resolved two concrete gaps: the `review record` CLI success/error
path gains direct real-Git coverage, and installation/rehearsal guidance now
distinguishes external test executables resolved through trusted `PATH` from
literal external paths rejected by command policy. The latter gains a behavior
regression; it does not relax path policy. The separate absolute `aider_python`
setting is unchanged. Clean-client and real-host/live-runtime gaps stay open.

When adding/removing a public operation, update its manifest entry and evidence
alongside the code. Record build/commit, test command and actual result/skip
reason; refresh installed/live claims only from new observations. A green drift
test cannot promote a historical or not-run entry into a current pass.

For long tasks, retain a short decision/evidence/outcome checkpoint: task/run/job
IDs, source baseline, exact patch digest, selected profile, observed result,
unverified boundaries and next decision. Keep private artifact pointers private;
use stable aliases in shared records. Never store raw prompts, test transcripts,
credentials, personal paths or member data. Record actual usage/cost only when
available. The map does not create an authenticated evidence ledger or claim
comparative token savings. [HSDLC-077](implementation_tasks.md#planning-foundation-and-real-task-validation)
uses these boundaries for the separately approved Metria trial.
