# BENCH-VALIDATOR-001: Offline manifest preflight

Implement only this task. Read `benchmarks/benchmark_task.json` as the schema-v2
reference. This is preflight validation, NOT benchmark execution or proof that a
comparative claim is allowed. Do not fill the template with invented pins/results.

## Allowed changes

- Add `src/hybrid_sdlc/benchmark_manifest.py`.
- Add `tests/unit/test_benchmark_manifest.py`.

All other files are protected, including this spec, the JSON template, existing
tests, configuration, dependency files and documentation. No network requests,
dependency installation, Git operations/commits, service lifecycle changes,
subprocess calls or file writes in the implementation.

## API and acceptance

Provide a pure, typed function `validate_manifest(manifest: object) -> list[str]`.
Return an empty list for valid preflight inputs; otherwise return deterministic,
field-path-based error strings without echoing user-supplied values. Do not mutate
the input. Use only Python's standard library. Do not add CLI integration yet.
Handle malformed mappings/lists/scalars without raising. Do not trust `status`,
`readiness`, candidate defaults, or historical notes to establish readiness.

Validate the following actual values (missing/null/blank/wrong types are errors):

1. Root must be an object and schema_version must be integer 2 (not bool).
2. Under shared: toolkit_version, aider_version, python_version and
   test_environment must be nonempty strings; timeout_seconds a positive finite
   number, retry_limit a positive integer (booleans invalid); prompt_sha256 must
   be exactly 64 hex characters.
3. shared.fixture: id, test_profile, acceptance and independent_review are
   nonempty strings; baseline_revision is a full 40 or 64 hex Git object ID;
   baseline_sha256 and spec_sha256 are 64 hex hashes. allowed_patch_paths is a
   nonempty list of unique normalized repository-relative strings (no absolute,
   drive/UNC paths, backslashes, empty/dot/parent components, or NUL).
4. comparison.engine and profile_id are nonempty strings. shared_model_pin
   requires nonempty repo and quantization, an immutable 40/64 hex revision,
   nonempty tokenizer_source and config_source, and at least one shard.
   Each shard is an object with a unique normalized relative filename, a
   positive integer bytes (not bool), and a 64 hex sha256. Reject duplicate
   filenames, malformed shards, and mutable revisions such as main/latest.
   tokenizer_source/config_source are provenance strings, not verified by this
   offline validator; the caller must pin/verify their referenced content.
5. comparison.variants requires both native and docker objects, each containing
   runtime object. Both require immutable revision (40/64 hex), build_args and
   launch_args lists of strings (empty lists allowed). Native requires 64 hex
   binary_sha256; Docker requires nonempty image and image_digest of the exact
   form sha256:<64 hex>. Both runtime revisions must match (case-insensitively).
6. Both runtime.request_settings and runtime.effective_settings must be objects
   containing context_tokens, kv, reasoning_effort, output_limit, parallelism.
   context_tokens/output_limit/parallelism are positive integers (not bool);
   kv/reasoning_effort are nonempty strings. Request settings must equal
   effective settings within each variant, and effective settings must match
   between variants, including any additional keys present. Compare mappings
   only after validation; malformed input must not crash. A mismatch is an error.

This initial validator intentionally does not validate ambient metrics,
repetitions, measurement/acceptance evidence or comparative-claim eligibility.
Document those limits in the module/function docstrings. Empty repetitions are
allowed at preflight: the three-run gate applies to future completed results.
Do not report successful preflight as a completed benchmark.

## Verification

Write standard-library unittest tests compatible with pytest. Tests may add the
repository `src` directory to sys.path for standalone unittest discovery.
The configured test profile runs:
`python -m unittest discover -s tests/unit -p test_benchmark_manifest.py -v`.

Cover a complete synthetic valid fixture (test-only placeholder pins, clearly
not real benchmark evidence), the unchanged template rejecting missing pins,
missing/malformed sections, wrong schema/type, malformed hashes/digests,
main/latest revisions, empty/duplicate/malformed shards, unsafe allowed paths,
bool numeric fields, NaN/infinity, settings mismatches within/across variants,
extra effective keys, no mutation, no user values in diagnostics, and permitted
empty repetitions. Include table-driven malformed nested values. Ensure all
new test cases actually run and pass. Preserve all existing repository files.
