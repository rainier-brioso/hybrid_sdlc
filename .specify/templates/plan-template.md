# Implementation Plan: [FEATURE NAME]

Branch: `[NNN-feature-name]` | Date: [DATE] | Spec: `specs/<NNN-feature>/spec.md`

This plan is `specs/<NNN-feature>/plan.md`.

## Summary

[Summarize the need, intended approach, and affected user stories/acceptance criteria.]

## Technical Context

- Language/runtime: [version or NEEDS CLARIFICATION]
- Dependencies/platform: [relevant dependencies and platforms, or N/A]
- Data/storage: [relevant data and persistence, or N/A]
- Constraints/performance: [limits, or N/A]

## Interfaces & Contracts

- [Interface]: [signature or shape, inputs, outputs, errors, compatibility, and security constraints].
- [Data/external contract]: [schema, ownership, lifecycle, and failure behavior, or N/A].

## Architecture & Decisions

- [Decision]: [reason, alternatives considered, and consequences].

## Constitution Check

- [ ] Acceptance criteria are observable and testable.
- [ ] Interfaces/contracts and failure behavior are explicit.
- [ ] Tasks will be atomic and traceable to requirements.
- [ ] Applicable configured test profiles: [identifiers from target repository's command_profiles.<id>; use <profile-id> until configured].
- [ ] CI quality gates considered separately from runnable command profiles.
- Deviations and rationale: [N/A or explain].

## Project Structure

Feature artifacts are under `specs/<NNN-feature>/`:

```text
specs/<NNN-feature>/
├── spec.md
├── plan.md
└── tasks.md
```

Affected source and test paths: [list actual repository-relative paths and their roles].

## Verification

List configured `[command_profiles.<id>]` identifiers and what each validates. If the repository has no suitable profile, retain `<profile-id>` until one is configured. CI quality gates are not runnable named profiles. Do not put ad hoc shell commands here.

- [configured profile identifier or `<profile-id>`]: [acceptance criteria or contracts it verifies].
