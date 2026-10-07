# Controlled Strata long-idle validation (not yet executed)

The reported long idle period and successful retry after restart do not prove
a stall's cause. This protocol is separate from the existing successful smoke
and must not be filled with inferred results. No automatic restart is involved.

## Prepare a comparable run

1. Use a disposable trusted Git fixture with a committed spec, fixed baseline,
   named test profile, and explicit model endpoint. Keep source checkout state
   unchanged between isolated attempts. Review the patch and rerun its tests.
2. Record Strata image/revision, model ID, quantization, context/KV settings,
   reasoning/output limits, host RAM, VM limit, GPU/driver, and any other model
   or client activity. Distinguish snapshots from peak measurements.
3. Run the bounded fixture once while loaded; save the run ID, result, test
   outcome, durations, and privacy-safe request metrics. Do not retain raw
   prompts, model output, source contents, credentials, or personal paths.

## Idle and repeat

1. Choose and record an idle interval (for example 24 hours). Record actual UTC
   start/end times; do not simply claim the intended interval elapsed.
2. Leave the same service/configuration in place. Avoid generating requests,
   scheduled inference probes, cache resets, restarts, or loading another model
   during that interval. Record sleep/resume, host updates, and memory pressure
   as confounders rather than silently excluding them.
3. Repeat the same committed task and test profile with unchanged limits.
   Record success or timeout, patch/tests, and available runtime observations.
   Container running and a successful model GET are not inference readiness.

For a separately configured managed runtime, an explicit failed run can be
correlated with opt-in readiness within 30 minutes:

```sh
hybrid-sdlc runtime strata diagnose --repo-root . --run-id ACTUAL_RUN_ID --json
hybrid-sdlc runtime strata diagnose --repo-root . --run-id ACTUAL_RUN_ID --readiness --timeout 10 --json
```

Do not configure/start a second service on the manual deployment's port or GPU.
The managed CLI cannot adopt or restart a manually owned container. See
[runtime ownership and activity limits](runtime-management.md).

## Failure and interpretation

After a timeout, assume the remote request might still be processing. Inspect
the service and participating activity before another inference or recovery
request. Recovery, if needed, is an explicit operator action; preserve the
pre-recovery evidence and record exactly what changed before retrying.

Repeat controlled cycles before making reliability claims. Record results and
confounders under `benchmarks/results/strata/`, with machine-specific paths
redacted. A successful retry, one idle cycle, or an advisory `suspected-stalled`
state establishes neither causation nor native-versus-Docker performance parity.
