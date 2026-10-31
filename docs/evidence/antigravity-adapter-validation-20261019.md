# Antigravity App adapter — source validation, 2026-10-19

Status: implementation and offline gates passed; installed real-job rehearsal
pending. This is separate from the earlier
[user-observed synthetic App ACK](antigravity-notification-investigation-20261019.md).
The implementation builds on main `a8cd739` on the feature branch; the source
changes were not committed or installed into the user's active tool environment
as part of this validation.

## Implemented surface

- `bridge antigravity`: exact authorized chat UUID, persisted job/repository,
  explicit continuation authority, bounded local wait and owned process-tree
  delivery, allowlisted metadata and durable no-resend receipts.
- `bridge antigravity-sidecar`: explicit dry-run/preparation/removal for a
  deterministic one-job manifest. Existing global configuration is preserved
  byte-for-byte; updates are private proposals for reviewed manual merge.
- Windows App ProductVersion `2.24.0.0` and its observed wrapper are validated
  without executing the host. Version/wrapper validation is repeated before
  delivery, so changes during a long job wait fail closed.
- Cleanup preserves the bridge receipt namespace. Codex transport implementation
  and the five MCP tools remain unchanged.

See the [adapter guide](../antigravity-completion-bridge.md) for commands, exact
setup/removal boundaries and recovery. The canonical and both shipped skill
copies include the experimental App workflow and remain byte-identical.

## Verification

Final Windows/Python 3.12.14 gate:

- Full suite: **1,049 passed / 14 skipped**, 268.95 seconds.
- Branch-inclusive coverage: **82.35%** (80% required).
- Ruff format: 135 files checked; lint passed.
- Strict mypy: no issues in 30 source files.
- Skill frontmatter validation passed; package/host verification contracts:
  17 passed.
- Offline wheel and source distribution builds passed; archive-content checks
  passed, and the wheel's adapter, sidecar, CLI and cleanup sources matched the
  final source files exactly. No clean-install smoke was run.
- `git diff --check` passed.

Independent review identified manifest discovery/schema, continuation authority,
partial-removal recovery and concurrent config-growth bounds issues during
implementation. They were fixed with regressions; final review found no remaining
material issue. A separate skill scenario pass checked missing setup authority,
unknown App version, uncertain delivery and removal during running work. The
guide explicitly discloses that file removal cannot verify process termination;
there is no portable App bridge-PID stop/ownership API implemented here.

A read-only resolver check accepted the installed App version and advertised
wrapper. It did not execute `agentapi`, send a message or verify installed
adapter delivery. Tests use fixture repositories, mock version metadata and
mock transport; they do not invoke a real App or local model.

## Unverified boundaries

The 14 skips cover restrictive Windows Job Object breakaway, unavailable symlink
privileges/POSIX modes and the opt-in clean distribution installation check.
They are not passing evidence for those behaviors. No new live message,
Antigravity setting change, Strata operation, tool upgrade or host restart was
performed for HSDLC-088 implementation validation.

The earlier one-message helper rehearsal does not prove this shipped adapter,
actual worker execution, repeated reliability, host shutdown/restart, busy or
closed targets, other App versions/platforms, IDE support or waiting-turn
resumption. HSDLC-088 stays open for separately approved installed real-job
validation. Receipts/locks are per adapter and repository, not global delivery
deduplication or an OS sandbox.
