# Hybrid SDLC Constitution

This constitution governs features planned under `specs/<NNN-feature>/`.

## Principles

1. **Specify observable behavior.** Requirements use stable identifiers and describe outcomes a user or caller can verify.
2. **Make contracts explicit.** Plans identify public interfaces, inputs, outputs, errors, compatibility expectations, and relevant data boundaries before implementation.
3. **Keep changes safe and bounded.** Respect repository boundaries, avoid exposing secrets, and define limits and failure behavior for external processes and persisted data.
4. **Test by configured profile.** Use an identifier that exists under the target repository's `[command_profiles.<id>]` configuration. If no suitable profile is configured, keep `<profile-id>` as a placeholder until the repository owner defines one. CI quality gates are validation workflows, not runnable command-profile identifiers.
5. **Deliver reviewable increments.** Tasks are atomic, single-purpose, ordered by dependency, and small enough to review independently. Each task names affected paths and its configured profile identifier or unresolved placeholder.

## Quality Gates

- Every feature spec includes testable acceptance criteria and measurable success criteria.
- Every plan documents affected interfaces/contracts, architecture decisions, and applicable configured profile identifiers (or unresolved placeholders).
- Every task maps to a requirement, has a clear completion condition, and can be reviewed as a focused change.
- A feature is complete only when its acceptance criteria pass and required project quality gates succeed.

## Governance

Resolve conflicts in favor of security, explicit contracts, and the feature's acceptance criteria. Record justified deviations in the feature plan, including their impact and follow-up. Amend this constitution through a reviewed repository change.
