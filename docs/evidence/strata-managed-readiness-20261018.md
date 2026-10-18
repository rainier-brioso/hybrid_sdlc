# Installed managed Strata readiness correction

Observed 2026-10-18 on Windows/Python 3.12.14, following the
[initial setup and failed five-token probe](installed-workflow-runtime-20261018.md).

## Scope and artifact

A wheel containing the HSDLC-086 fix was built and installed with dependencies
into a separate environment outside the development checkout. The console
command ran from that environment's directory; the imported probe module was
confirmed under installed `site-packages`, not the source checkout.

- Wheel: `hybrid_sdlc-0.1.0-py3-none-any.whl`.
- Wheel SHA256: `4c07abe5ba61aa6e0dac9816c275872622e38236359e052642d04781dea1ec65`.
- Installed `server_probe.py` SHA256:
  `07b61cba3d29c6776796bfa4af2de2ad04e896afa06934636ff5bde3ca6b1dc5`.

## Invocation and result

```text
hybrid-sdlc runtime strata diagnose --readiness --timeout 30 --json
```

One installed-CLI invocation returned exit 0 in approximately 5.377 seconds:

```json
{
  "status": "ready",
  "endpoint": "http://127.0.0.1:8080/v1",
  "endpoint_available": true,
  "model_available": true,
  "model": "qwen3.8-flash-next-coder-iq1_m",
  "loaded": true,
  "managed_activity": "idle",
  "readiness": {
    "tested": true,
    "passed": true,
    "status": "ready",
    "error_code": null
  },
  "error_code": null
}
```

The managed guard selects a fixed request-local output limit of 256 tokens,
reasoning budget 32 and low reasoning effort. Backend-neutral probes retain
their previous request; model naming alone does not enable provider fields.
The managed request accepts only a nonempty assistant answer completed with
`stop`, without tool/function calls. The shared managed activity gate and
wall-clock-bounded inference subprocess remain in use.

After that isolated verification, only the `hybrid-sdlc` package was replaced
in the existing user tool environment from the same wheel, without replacing
its interpreter, dependencies or active MCP launchers. A second invocation
through the normal installed CLI used the default readiness timeout:

```text
hybrid-sdlc runtime strata diagnose --readiness --json
```

It returned exit 0 and the same ready/tested/passed result in approximately
3.136 seconds. This was a separate successful verification after installation,
not an automatic retry of an uncertain request. Existing MCP processes were
not stopped or restarted and may retain previously imported code until reload.

No Strata restart, saved runtime budget change, repository implementation or
member-data processing was performed for this verification. A successful
readiness response is not a benchmark, repeated reliability result, Windows
async survival proof or completed Metria feature trial. The ordinary `check`
availability GET retains its documented HTTP timeouts rather than an overall
wall-clock command deadline; managed `diagnose` GETs are separately bounded.

## Repository validation

- Focused unit/probe-worker/runtime CLI suite: 89 passed.
- Required full suite: 1,010 passed / 12 environment or opt-in skips in
  257.02 seconds; coverage 83.04% (required 80%).
- Ruff formatting and lint, mypy on 28 source files, documentation references,
  package parity, whitespace checks and independent review passed.

The skips include restrictive Windows Job Object breakaway, unavailable symlink
privileges, POSIX permission bits and opt-in distribution verification. The wheel
installation above is separate evidence, not a claim that skipped checks passed.
