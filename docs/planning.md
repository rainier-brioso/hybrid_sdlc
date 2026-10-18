# Lightweight planning and delegation

Hybrid SDLC separates host-led planning from bounded local execution. No Spec
Kit installation, special directory layout, planning daemon or new MCP tool is
required. The runner accepts an ordinary committed task file. These workflow
rules guide the host; they are not a new runtime contract parser.

The shipped `local-delegate` skill is the host-facing entrypoint for this full
workflow, including taking a raw feature spec, preparing bounded contracts and
configuration, managing an already configured Strata prerequisite, and reviewing
each local result before dependent work. Its installed copies are self-contained:
they must not depend on this development checkout or a bespoke handoff prompt.
CLI/MCP installation alone does not load a skill into a host. Follow the host
installation guide and verify the selected Hybrid SDLC skill in a fresh chat.

## Choose the planning path

| Path | Use when | Planning artifact |
| --- | --- | --- |
| Fast modification | Clear goal, known approach, limited dependencies | One short task contract |
| Planned change | Dependent tasks, architectural uncertainty or cross-cutting behavior | Short dependency plan plus a contract per task |

The analyst names the path and why. Size chooses the planning shape; risk
chooses verification effort. A small security or concurrency change may need
independent review and real integration evidence. A large mechanical change
may need little design exploration. Neither path weakens execution safeguards.

Ground decisions in the current source and tests. Resolve observable technical
uncertainty with an authorized bounded prototype before writing a long plan.
Ask the user about product preferences or authority that evidence cannot settle.
Do not generate competing implementations or review panels by default.

## Write one contract per execution

Use a repository-owned location such as `docs/features/<feature>/tasks/`;
the location is a convention, not a runner requirement. Keep the contract
committed and unchanged before submission. `task_id` identifies the run but
does not select a section of a large document. Prefer one focused task file
instead of sending the entire program plan or conversation history.

```markdown
# TASK-001 — Observable outcome

Goal: the behavior the user needs.
Context: relevant source paths and verified facts only.
Scope: exact intended files, exclusions, dependencies and non-goals.
Baseline: the agreed source commit; prerequisites that must already pass.
Contract: inputs, outputs, interfaces and important failure behavior.
Acceptance: observable results, including negative and boundary cases.
Verification: a configured command_profiles.<id> name and independent checks.
Limits: confirmed attempts, timeouts and effective output/reasoning settings.
Stop: ambiguity, unsupported prerequisites, scope growth or exhausted limits.
Handoff: run ID, actual baseline, exact patch hash, tests and unresolved risks.
```

Do not invent evidence or silently change model, profile, limits or scope.
CI job names are not runnable command profiles. Ignored machine configuration,
source `.venv` paths and uncommitted files are not copied into the isolated
checkout; ensure the approved test runtime works there before delegation.
The current runner aborts when baseline tests fail. A known failing regression
needs a separately designed reproduction/profile strategy, not disabled tests
or a claim that broken-baseline execution is already supported.

For machine-checked task identity and confirmed limit ceilings, select an
optional committed [task policy](task-policy.md) alongside the contract. Its
strict JSON schema is separate from Markdown instructions. Do not infer policy
from prose or silently discover another file. Policies declare exact intended
paths and exclusions, checked against the complete final exported patch.

## Execute, review and integrate

1. Analyst supplies the bounded contract and confirmed configuration.
2. One local worker edits/tests in an isolated checkout under existing limits.
3. Deterministic checks report execution outcome, not product acceptance.
4. Reviewer inspects the exact exported patch against the contract and source.
5. Human or explicitly authorized integrator accepts and integrates it.

For async runs, poll the same job until terminal; MCP provides no universal
wakeup guarantee. An accepted patch is not automatically applied or merged.
Dependent tasks start from a fresh committed baseline containing accepted
prerequisites. Independent writers require separate worktrees and ownership;
their combined change still needs integration checks. Serialize local inference
initially; repository locks do not prove endpoint-wide GPU scheduling.

Review identity includes run ID, source baseline, complete patch digest, test
evidence and reviewer provenance. A changed patch requires renewed acceptance.
Use the CLI [review workflow](review-acceptance.md) to persist that decision;
execution tools do not automatically record acceptance. Its independent test
run checks the candidate in a fresh checkout, without applying it to the source.
Preserve distinct states: execution succeeded, reviewed/accepted, integrated,
and physically or operationally verified. A same-model persona review is not
independent model evidence; deterministic checks remain essential.

Retry only within the original task and confirmed limits. If scope expands,
requirements become unclear, or repeated failures challenge the approach,
stop and reassess as planned work. Review-driven changes get a new bounded
contract or an explicitly revised contract; do not enlarge the old task in
place while it runs. On pause/pickup retain IDs, baseline, artifact pointers,
remaining work and unresolved decisions, never silently resubmit an active job.

## Optional subagent and role configuration (planned)

Keep two independent configuration layers:

| Layer | Owner | Intended use |
| --- | --- | --- |
| Host-native subagents | The host and the user's existing configuration | Bounded research, review and explicitly selected implementation roles |
| Local model profiles | Hybrid SDLC configuration | Serialized local analyst/worker/reviewer requests with confirmed provider limits |

Plan these now, but do not claim they are implemented. The initial workflow
can use existing user-configured host agents and the current local worker.
No new agent factory or MCP subagent tool is needed for that trial.

The host analyst selects roles according to task risk and confirmed available
capabilities. Default to one local implementation worker; additional host
research/review agents need a bounded question and a reason to spend their
context/budget. Host-paid implementation and multi-model panels are explicit
choices, not automatic fallbacks. A cheap model name is not a quality guarantee.

Reuse user settings rather than overwriting them. Future host adapters must
discover supported model identifiers, reasoning options, agent roles and tool
restrictions; emit reviewable configuration proposals only when requested.
Never assume one host's agent format or permission behavior works in another.
Unsupported mandatory capabilities block the role; any permitted sequential
fallback is disclosed and cannot silently select a paid provider.

Local profiles keep one model loaded initially, use fresh bounded handoffs and
separate reasoning/output settings by role. Read-only analyst/reviewer access
requires enforced capabilities, not personas. Record actual provider/model,
effective settings and review provenance. Design the schema now; recommend
budgets or smaller-model switches only after task-quality, cost and hardware
evidence. See HSDLC-078/079 and D06 in the task plan.

## What is enforced today

The runner validates repository/spec paths, committed spec state, named profiles,
execution limits and isolated worktrees. Configured Aider edit files determine
initial context; they are **not an enforced task write allowlist or OS sandbox**.
An explicitly selected task policy additionally checks task/contract/profile
identity, committed provenance and configured limit ceilings. Its literal path
lists are checked after tests against the complete exported candidate and
before an optional scoped commit. Changes outside those lists fail with saved
candidate evidence. Markdown-only scope and role labels remain instructions,
not enforced capabilities. Exact-artifact review records are explicit CLI
operations; no enforced analyst/reviewer capability mode exists yet. See the
implementation tasks before claiming planned protections are available. Export
checks do not prevent arbitrary repository-command writes or provide an OS sandbox.

## Improve from evidence

Measure time and cost per accepted change, including planning, worker attempts,
failures, retries, review and integration. Record actual usage where exposed;
otherwise mark it unavailable. Reasoning/output caps do not guarantee total
token or monetary cost. No silent paid-model fallback. One smoke or successful
task does not establish comparative savings, throughput or sustained reliability.

For long work, keep a concise decision trail: choice, reason, evidence pointer
and outcome. Keep private content out of shared artifacts. Map important user
journeys to automated, installed-artifact, live and host-specific verification;
missing physical evidence remains unverified. Recurring defects should gain a
type/boundary check or regression test rather than ever-longer agent rules.
Use the checked [verification map](verification-map.md) to find existing tests,
operator protocols and explicit gaps before planning new evidence. A referenced
test or historical smoke is not a new execution or a current live pass.

This design draws ideas from specification-driven task contracts and
[pstack's planning](https://github.com/cursor/plugins/blob/1b6d44ff26d4e242f4e8f68c391e6d2f7ca16def/pstack/skills/poteto-mode/playbooks/multi-phase-plan.md)
and [review](https://github.com/cursor/plugins/blob/1b6d44ff26d4e242f4e8f68c391e6d2f7ca16def/pstack/skills/interrogate/SKILL.md)
workflows. It does not import their skills, cloud fan-out requirements or
autonomy policy. Runtime follow-ups are tracked in
[implementation tasks](implementation_tasks.md#planning-foundation-and-real-task-validation).

## Migrating from the removed initializer

The root `hybrid-sdlc init` command and bundled Spec Kit templates are removed.
`hybrid-sdlc runtime strata configure` remains the separate opt-in runtime setup
command; the existing lifecycle commands are unchanged.
Existing user `.specify/`, specs, manifests, journals and backups are not
deleted, rewritten or migrated. Existing committed task files still work through
the existing CLI/MCP spec APIs; the optional policy argument is additive. The
package no longer probes or requires `specify`; users may keep other planning
tools independently.
