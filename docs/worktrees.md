# Isolated worktree results

An isolated run starts from a recorded baseline commit in a detached Git
worktree. Its review result contains the baseline hash and an exact patch from
that baseline to the current isolated checkout. The patch includes tracked
changes (staged and unstaged), committed changes, and untracked non-ignored
files. Binary file contents and Unicode or space-containing paths are
preserved. Export reads the isolated index without changing it.

Result export does not modify the source checkout, move a branch, merge, or
cherry-pick. The bounded runner starts from `HEAD` in a detached worktree and
requires the spec file to be committed and unchanged; unrelated staged,
unstaged, and untracked source files remain in the user's checkout and are not
copied into the run. The test profile always runs with its working directory
inside the isolated checkout. A repository-local test executable is resolved
again at its corresponding path there; if it is absent (for example, a
source-local `.venv/Scripts/pytest.exe`), the run fails closed with guidance
instead of silently testing the source checkout. Install that runtime in the
isolated checkout, or configure a bare/external executable. An external
executable keeps its approved absolute path and still runs with the isolated
working directory. A commit hash is returned only when
`run-task --commit` is explicitly requested and the final test attempt passes;
otherwise the result has no commit hash. Async and MCP runs never commit
implicitly. The patch remains the complete snapshot to apply. If edits
were made after the scoped commit, those edits appear in the patch but not in
the commit hash.

Patch files are written as `result.patch` next to the durable worktree record,
outside the isolated checkout. They are exact review artifacts: they are not
redacted or truncated. A patch may contain credentials, private source, or
other sensitive content. Keep the temporary worktree storage private, inspect
the patch before sharing it, and remove it when it is no longer needed. On
POSIX systems the patch file is created with mode `0600`; on Windows it uses
the inherited permissions of the user's temporary directory.

Per-attempt logs and lock files are also stored beside the worktree record, so
they cannot become part of the exported patch or scoped commit. The final run
record is written under the source repository's `.hybrid_sdlc/runs/` only when
Git already ignores that location. Otherwise it stays in the private attempt
directory to preserve the source checkout's Git status. Failed worktrees are
retained for inspection by default. To restore only the isolated checkout to
its baseline after exporting the patch, pass `--rollback-on-failure` to
`run-task`.

The patch and opt-in commit include non-ignored files left by the test command
as well as model edits. Configure the target repository's ignore rules for
generated outputs (such as `__pycache__/` or build directories), and review the
patch before applying or cherry-picking it.

Before producing a diff for an untracked file, the exporter copies its bytes
from an opened, identity-checked file handle into a private temporary snapshot;
Git reads that snapshot instead of reopening the live checkout path. POSIX
uses directory-relative no-follow opens. Windows validates the resolved path
and file identity before and after reading because Python does not provide the
same portable directory-handle traversal there. Worktree locks coordinate
normal tool operations, but they cannot stop a hostile same-user process from
changing files concurrently at operating-system call boundaries. Do not share
the temporary worktree directory with untrusted local processes.

## Apply a patch to a user checkout

Review the patch first. From PowerShell, set the paths for your checkout and
the exported patch, then check and apply it:

```powershell
$repo = 'C:\path\to\your\checkout'
$patch = 'C:\path\to\temporary\hsdlc-wt\<repo-id>\<worktree-id>\result.patch'
git -C $repo apply --binary --check $patch
git -C $repo apply --binary $patch
```

The target checkout must be based on the recorded baseline for the patch to
apply cleanly. The check reports context conflicts before applying. If it
fails, no files should be changed by the check; keep the patch intact, inspect
the target's changes, and either apply the patch to a clean checkout at the
baseline or resolve the differences manually. Do not use `git apply --reject`
unless you intentionally want partial application and `.rej` files. After
applying, review `git diff` and run the target project's checks before making
a commit.

## Cherry-pick an explicitly created commit

When result metadata includes a scoped commit hash, inspect that commit and
cherry-pick it only after review. The commit is stored in the local Git object
database shared with the linked worktree:

```powershell
git -C $repo show --stat <commit-hash>
git -C $repo cherry-pick <commit-hash>
```

Cherry-pick applies only the committed snapshot. Use the exported patch when
the result also contains edits made after that commit. If cherry-pick reports
a conflict, Git pauses with conflict markers; resolve each file, run the
project checks, then use `git -C $repo cherry-pick --continue`. To abandon
that attempt, use `git -C $repo cherry-pick --abort`. These commands act on
the explicitly selected target checkout only; result export itself never
performs them.
