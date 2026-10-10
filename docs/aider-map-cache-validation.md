# Map-enabled Aider cache smoke

Date: 2026-10-10. Scope: HSDLC-064C, not a representative performance benchmark.

## Setup

- Working branch: `codex/aider-repo-map-cache`, based on main `eab0117` with
  the uncommitted adapter changes under test.
- Installed Aider: 0.86.2, launched through its explicitly configured Python
  interpreter and the standalone cache adapter.
- Existing runtime image: `hybrid-sdlc/strata:v0.1.39-cuda13-sm86`.
- Endpoint/model: `http://127.0.0.1:8080/v1`,
  `qwen3.8-flash-next-coder-iq1_m`, reported context 16,384 tokens.
- Fixture baseline: `a226cf0a209d3af141855f407f3f21104807fe47`.
- Map budget: 1024; request output/reasoning budgets: 4096/1024.
- Attempt/task deadlines: 300/600 seconds; maximum editing attempts: 2.

The disposable fixture committed its specification and configuration. Only
`calculator.py` and `test_smoke.py` were authorized for edits. A third Python
file, `context.py`, supplied unselected symbol context. A committed marker
inside `.aider.tags.cache.v4/` tested preservation of baseline cache-like content.
The ignore file excluded only `__pycache__/` and `.hybrid_sdlc/`; it did not hide
the repository-map cache namespace.

## Result

Run `run_1791654921_5ea57d48` succeeded in 243.06 seconds with one toolkit editing
attempt and four passing tests. The exact patch added `multiply_by_two` and
separate zero, positive, and negative tests while retaining `add_one` and its
test. Both per-attempt capture and final export listed only the two authorized
files (+14/-1). No task commit was created or applied to the source repository.

Exact review-patch SHA256:
`cba90faea6de2adfecf70298a50e95e45d39ddf8ffe2330da907d4a8c398615d`.

The real Aider cache database was generated outside `checkout/`, under the
run-owned sibling `aider-cache/`. Read-only SQLite inspection found keys for
`context.py` and the fixture's other files, demonstrating actual map indexing
with a positive map budget. This is not a separate capture of the serialized
map in the inference request. The checkout's baseline cache directory retained
only its marker; no generated database appeared there. Source Git status stayed
clean, and the checkout had no nonignored untracked output. Ignore rules and the
baseline marker were unchanged.

Private evidence is retained under the toolkit's worktree storage:
`<temp>/hsdlc-wt/<repo-hash>/<worktree-id>/`, including `result.patch`,
`worktree.json`, `aider-cache/cache.db`, and the per-attempt run artifacts.
The final source run record is `.hybrid_sdlc/runs/run_1791654921_5ea57d48.json`.
These local artifacts are not distributed or committed with the package.

## Runtime fault observed

The container was already running and reported healthy. The first inference
request nevertheless failed with a Strata engine message:
`prefill: relayout: the stream failed`. Strata reported engine exit code 1 and
automatically reloaded it on a subsequent request. No manual container restart,
configuration change, or toolkit recovery action was performed.

After recovery, server logs reported a 2,460-token prompt, 402 output tokens,
59 seconds for the successful request, 62.2 output tokens/s, and a 93.0% expert
cache hit rate. The toolkit's 243.06-second duration includes the preceding
engine fault and recovery; it must not be presented as steady-state throughput
or a first-request success. One toolkit editing attempt can include multiple
provider requests/retries.

This observation does not establish the fault's root cause, a measured idle
interval, or completion of the controlled long-idle protocol. HSDLC-064E remains
open. This small smoke also does not establish native-versus-Docker parity or
cross-platform live validation.
