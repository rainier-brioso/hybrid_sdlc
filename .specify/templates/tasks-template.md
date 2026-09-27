# Tasks: [FEATURE NAME]

Input: `specs/<NNN-feature>/spec.md` and `specs/<NNN-feature>/plan.md`  
Output: `specs/<NNN-feature>/tasks.md`

## Task Format

Use one checklist item per atomic, reviewable change:

`- [ ] T001 [US1] [Single focused action; include exact repository-relative paths] (Verify: <configured-profile-id>; satisfies: <AC/FR id>)`

Use stable sequential IDs. Add `[P]` only when tasks can safely run independently. Keep each task single-purpose, name its affected paths, and state its completion evidence. Group tasks under a user story or shared setup phase; preserve dependency order.

## Verification Profile

Use an identifier defined under the target repository's `[command_profiles.<id>]` configuration. If no suitable profile exists yet, use `<profile-id>` as a placeholder pending configuration. CI quality gates are workflow checks, not runnable command-profile identifiers. Do not substitute raw ad hoc test commands. Explain verification evidence in the task or feature review.

## Phase 1: Setup

- [ ] T001 [Shared] [Focused setup change with exact paths] (Verify: [profile-id]; satisfies: [AC/FR id])

## Phase 2: User Stories

### User Story 1 - [Brief Title] (Priority: P1)

Goal: [User-visible outcome]  
Independent verification: [Named profile ID and acceptance criterion]

- [ ] T002 [US1] [One focused implementation or test change with exact paths] (Verify: [profile-id]; satisfies: [AC/FR id])
- [ ] T003 [P] [US1] [Independent focused change with exact paths] (Verify: [profile-id]; satisfies: [AC/FR id])

Repeat for additional user stories and order dependent tasks explicitly.

## Phase 3: Integration & Acceptance

- [ ] T004 [Shared] [Focused integration or documentation change with exact paths] (Verify: [profile-id]; satisfies: [AC id])
