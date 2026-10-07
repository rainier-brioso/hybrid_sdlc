"""Tests for offline comparative benchmark manifest preflight validation."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = str(_ROOT / "src")
if _SOURCE not in sys.path:
    sys.path.insert(0, _SOURCE)

from hybrid_sdlc.benchmark_manifest import validate_manifest  # noqa: E402

_HEX40 = "a" * 40
_HEX64 = "b" * 64


def _valid_manifest() -> dict[str, Any]:
    """Return a complete synthetic fixture, never real benchmark evidence."""
    settings: dict[str, object] = {
        "context_tokens": 16_384,
        "kv": "int8",
        "reasoning_effort": "low",
        "output_limit": 4096,
        "parallelism": 1,
    }
    return {
        "schema_version": 2,
        "shared": {
            "toolkit_version": "0.1.0",
            "aider_version": "0.86.2",
            "python_version": "3.12.4",
            "test_environment": "synthetic-test-only",
            "timeout_seconds": 300,
            "retry_limit": 3,
            "prompt_sha256": _HEX64,
            "fixture": {
                "id": "synthetic-fixture",
                "baseline_revision": _HEX40,
                "baseline_sha256": _HEX64,
                "spec_sha256": _HEX64,
                "test_profile": "synthetic",
                "acceptance": "test only",
                "allowed_patch_paths": ["src/example.py", "tests/test_example.py"],
                "independent_review": "synthetic reviewer",
            },
        },
        "comparison": {
            "engine": "synthetic-engine",
            "profile_id": "synthetic-profile",
            "shared_model_pin": {
                "repo": "https://example.invalid/model",
                "revision": _HEX40,
                "quantization": "synthetic-quantization",
                "tokenizer_source": "synthetic-tokenizer-source",
                "config_source": "synthetic-config-source",
                "shards": [{"filename": "weights/model.gguf", "bytes": 1024, "sha256": _HEX64}],
            },
            "variants": {
                "native": {
                    "runtime": {
                        "revision": _HEX40,
                        "binary_sha256": _HEX64,
                        "build_args": ["--synthetic"],
                        "launch_args": [],
                        "request_settings": copy.deepcopy(settings),
                        "effective_settings": copy.deepcopy(settings),
                    },
                    "repetitions": [],
                },
                "docker": {
                    "runtime": {
                        "revision": _HEX40,
                        "image": "synthetic-image:tag",
                        "image_digest": "sha256:" + _HEX64,
                        "build_args": [],
                        "launch_args": ["--synthetic"],
                        "request_settings": copy.deepcopy(settings),
                        "effective_settings": copy.deepcopy(settings),
                    },
                    "repetitions": [],
                },
            },
        },
    }


class BenchmarkManifestTests(unittest.TestCase):
    def test_complete_synthetic_fixture_passes(self) -> None:
        self.assertEqual(validate_manifest(_valid_manifest()), [])

    def test_unready_repository_template_rejects_missing_pins(self) -> None:
        manifest_path = _ROOT / "benchmarks" / "benchmark_task.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        errors = validate_manifest(manifest)
        self.assertTrue(errors)
        self.assertIn("shared.timeout_seconds: invalid finite positive number", errors)
        self.assertFalse(manifest["readiness"]["comparative_claim_allowed"])

    def test_root_and_schema_types(self) -> None:
        for value in (None, 42, "manifest", [], True):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(validate_manifest(value), ["root: not an object"])
        for schema in (None, True, 2.0, "2", 1):
            with self.subTest(schema=schema):
                manifest = _valid_manifest()
                manifest["schema_version"] = schema
                self.assertIn("schema_version: must be integer 2", validate_manifest(manifest))

    def test_required_nested_objects_missing_null_or_wrong_type(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
            ("shared missing", lambda item: item.pop("shared"), "shared: not an object"),
            ("shared null", lambda item: item.update(shared=None), "shared: not an object"),
            (
                "fixture wrong type",
                lambda item: item["shared"].update(fixture=[]),
                "shared.fixture: not an object",
            ),
            (
                "comparison missing",
                lambda item: item.pop("comparison"),
                "comparison: not an object",
            ),
            (
                "pin wrong type",
                lambda item: item["comparison"].update(shared_model_pin=None),
                "comparison.shared_model_pin: not an object",
            ),
            (
                "variants wrong type",
                lambda item: item["comparison"].update(variants=[]),
                "comparison.variants: not an object",
            ),
            (
                "native wrong type",
                lambda item: item["comparison"]["variants"].update(native=None),
                "comparison.variants.native: not an object",
            ),
            (
                "docker runtime wrong type",
                lambda item: item["comparison"]["variants"]["docker"].update(runtime=[]),
                "comparison.variants.docker.runtime: not an object",
            ),
            (
                "native runtime null",
                lambda item: item["comparison"]["variants"]["native"].update(runtime=None),
                "comparison.variants.native.runtime: not an object",
            ),
            (
                "native request settings wrong type",
                lambda item: item["comparison"]["variants"]["native"]["runtime"].update(
                    request_settings=[]
                ),
                "comparison.variants.native.runtime.request_settings: not an object",
            ),
            (
                "native effective settings null",
                lambda item: item["comparison"]["variants"]["native"]["runtime"].update(
                    effective_settings=None
                ),
                "comparison.variants.native.runtime.effective_settings: not an object",
            ),
            (
                "docker request settings null",
                lambda item: item["comparison"]["variants"]["docker"]["runtime"].update(
                    request_settings=None
                ),
                "comparison.variants.docker.runtime.request_settings: not an object",
            ),
            (
                "docker effective settings wrong type",
                lambda item: item["comparison"]["variants"]["docker"]["runtime"].update(
                    effective_settings=[]
                ),
                "comparison.variants.docker.runtime.effective_settings: not an object",
            ),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                manifest = _valid_manifest()
                mutate(manifest)
                self.assertIn(expected, validate_manifest(manifest))

    def test_all_required_nonblank_strings_reject_missing_null_and_whitespace(self) -> None:
        paths = (
            ("shared", "toolkit_version"),
            ("shared", "aider_version"),
            ("shared", "python_version"),
            ("shared", "test_environment"),
            ("shared", "fixture", "id"),
            ("shared", "fixture", "test_profile"),
            ("shared", "fixture", "acceptance"),
            ("shared", "fixture", "independent_review"),
            ("comparison", "engine"),
            ("comparison", "profile_id"),
            ("comparison", "shared_model_pin", "repo"),
            ("comparison", "shared_model_pin", "quantization"),
            ("comparison", "shared_model_pin", "tokenizer_source"),
            ("comparison", "shared_model_pin", "config_source"),
            ("comparison", "variants", "docker", "runtime", "image"),
            ("comparison", "variants", "native", "runtime", "request_settings", "kv"),
            (
                "comparison",
                "variants",
                "docker",
                "runtime",
                "effective_settings",
                "reasoning_effort",
            ),
        )
        for path in paths:
            expected = ".".join(path)
            for invalid in ("missing", None, "   "):
                with self.subTest(path=expected, invalid=invalid):
                    manifest = _valid_manifest()
                    target: dict[str, Any] = manifest
                    for component in path[:-1]:
                        target = target[component]
                    if invalid == "missing":
                        target.pop(path[-1])
                    else:
                        target[path[-1]] = invalid
                    self.assertTrue(
                        any(
                            error.startswith(expected + ":")
                            for error in validate_manifest(manifest)
                        )
                    )

    def test_required_hashes_and_revisions_reject_mutable_or_malformed_values(self) -> None:
        cases: tuple[tuple[tuple[str, ...], object], ...] = (
            (("shared", "prompt_sha256"), "z" * 64),
            (("shared", "fixture", "baseline_revision"), "main"),
            (("shared", "fixture", "baseline_revision"), "a" * 39),
            (("shared", "fixture", "baseline_sha256"), None),
            (("shared", "fixture", "spec_sha256"), "latest"),
            (("comparison", "shared_model_pin", "revision"), "latest"),
            (("comparison", "variants", "native", "runtime", "revision"), "main"),
            (("comparison", "variants", "native", "runtime", "binary_sha256"), "0" * 63),
            (("comparison", "variants", "docker", "runtime", "image_digest"), "sha256:latest"),
        )
        for path, value in cases:
            with self.subTest(path=".".join(path)):
                manifest = _valid_manifest()
                target: dict[str, Any] = manifest
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value
                self.assertTrue(validate_manifest(manifest))

    def test_timeout_accepts_finite_positive_integer_float_and_huge_integer(self) -> None:
        for value in (1, 0.25, 10**10000):
            with self.subTest(value_type=type(value).__name__):
                manifest = _valid_manifest()
                manifest["shared"]["timeout_seconds"] = value
                self.assertNotIn(
                    "shared.timeout_seconds: invalid finite positive number",
                    validate_manifest(manifest),
                )

    def test_timeout_rejects_zero_negative_bool_non_number_and_nonfinite(self) -> None:
        values: tuple[object, ...] = (0, -1, True, "300", float("nan"), float("inf"), -float("inf"))
        for value in values:
            with self.subTest(value=repr(value)):
                manifest = _valid_manifest()
                manifest["shared"]["timeout_seconds"] = value
                self.assertIn(
                    "shared.timeout_seconds: invalid finite positive number",
                    validate_manifest(manifest),
                )

    def test_positive_integer_fields_reject_bool_zero_negative_float_and_string(self) -> None:
        paths = (
            ("shared", "retry_limit"),
            ("comparison", "shared_model_pin", "shards", 0, "bytes"),
            ("comparison", "variants", "native", "runtime", "request_settings", "context_tokens"),
            ("comparison", "variants", "native", "runtime", "request_settings", "output_limit"),
            ("comparison", "variants", "native", "runtime", "request_settings", "parallelism"),
        )
        for path in paths:
            for value in (True, 0, -1, 1.5, "1"):
                with self.subTest(path=path, value=value):
                    manifest = _valid_manifest()
                    target: Any = manifest
                    for component in path[:-1]:
                        target = target[component]
                    target[path[-1]] = value
                    self.assertTrue(validate_manifest(manifest))

    def test_allowed_paths_are_nonempty_unique_and_normalized(self) -> None:
        invalid_lists: tuple[object, ...] = (
            None,
            "src/example.py",
            [],
            ["C:/absolute"],
            ["C:relative"],
            ["/absolute"],
            ["//server/share"],
            ["../parent"],
            ["src/../parent"],
            ["src//file.py"],
            ["src/./file.py"],
            ["src\\file.py"],
            ["src/\x00file.py"],
            ["src/file.py", "src/file.py"],
            [1],
        )
        for paths in invalid_lists:
            with self.subTest(paths=repr(paths)):
                manifest = _valid_manifest()
                manifest["shared"]["fixture"]["allowed_patch_paths"] = paths
                self.assertTrue(
                    any(
                        error.startswith("shared.fixture.allowed_patch_paths")
                        for error in validate_manifest(manifest)
                    )
                )

    def test_shards_require_well_formed_unique_pins(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("missing list", lambda item: item["comparison"]["shared_model_pin"].pop("shards")),
            ("null list", lambda item: item["comparison"]["shared_model_pin"].update(shards=None)),
            ("empty list", lambda item: item["comparison"]["shared_model_pin"].update(shards=[])),
            (
                "malformed entry",
                lambda item: item["comparison"]["shared_model_pin"]["shards"].__setitem__(0, None),
            ),
            (
                "unsafe name",
                lambda item: item["comparison"]["shared_model_pin"]["shards"][0].update(
                    filename="../weights.gguf"
                ),
            ),
            (
                "duplicate name",
                lambda item: item["comparison"]["shared_model_pin"]["shards"].append(
                    copy.deepcopy(item["comparison"]["shared_model_pin"]["shards"][0])
                ),
            ),
            (
                "missing bytes",
                lambda item: item["comparison"]["shared_model_pin"]["shards"][0].pop("bytes"),
            ),
            (
                "wrong hash",
                lambda item: item["comparison"]["shared_model_pin"]["shards"][0].update(
                    sha256="bad"
                ),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                manifest = _valid_manifest()
                mutate(manifest)
                self.assertTrue(validate_manifest(manifest))

    def test_variant_runtime_fields_and_settings_are_checked(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
            (
                "missing build args",
                lambda item: item["comparison"]["variants"]["native"]["runtime"].pop("build_args"),
                "comparison.variants.native.runtime.build_args:",
            ),
            (
                "invalid launch args",
                lambda item: item["comparison"]["variants"]["docker"]["runtime"].update(
                    launch_args=[2]
                ),
                "comparison.variants.docker.runtime.launch_args:",
            ),
            (
                "missing settings",
                lambda item: item["comparison"]["variants"]["native"]["runtime"].pop(
                    "request_settings"
                ),
                "comparison.variants.native.runtime.request_settings:",
            ),
            (
                "zero required setting",
                lambda item: item["comparison"]["variants"]["native"]["runtime"][
                    "request_settings"
                ].update(context_tokens=0),
                "comparison.variants.native.runtime.request_settings.context_tokens:",
            ),
            (
                "within-variant mismatch",
                lambda item: item["comparison"]["variants"]["native"]["runtime"][
                    "effective_settings"
                ].update(context_tokens=8192),
                "comparison.variants.native.runtime: request_settings != effective_settings",
            ),
            (
                "cross-variant mismatch",
                lambda item: item["comparison"]["variants"]["docker"]["runtime"][
                    "request_settings"
                ].update(context_tokens=8192),
                "comparison.variants: effective_settings differ across variants",
            ),
            (
                "extra key mismatch",
                lambda item: item["comparison"]["variants"]["native"]["runtime"][
                    "effective_settings"
                ].update(extra="changed"),
                "comparison.variants.native.runtime: request_settings != effective_settings",
            ),
            (
                "extra bool/int mismatch",
                lambda item: item["comparison"]["variants"]["native"]["runtime"][
                    "effective_settings"
                ].update(extra=1),
                "comparison.variants.native.runtime: request_settings != effective_settings",
            ),
            (
                "revision mismatch",
                lambda item: item["comparison"]["variants"]["docker"]["runtime"].update(
                    revision="c" * 40
                ),
                "comparison.variants: runtime revisions do not match",
            ),
        )
        for label, mutate, expected_error in cases:
            with self.subTest(label=label):
                manifest = _valid_manifest()
                if label in ("extra key mismatch", "extra bool/int mismatch"):
                    manifest["comparison"]["variants"]["native"]["runtime"]["request_settings"][
                        "extra"
                    ] = True if label == "extra bool/int mismatch" else "same"
                    if label == "extra bool/int mismatch":
                        manifest["comparison"]["variants"]["native"]["runtime"][
                            "effective_settings"
                        ]["extra"] = 1
                    else:
                        manifest["comparison"]["variants"]["native"]["runtime"][
                            "effective_settings"
                        ]["extra"] = "same"
                        mutate(manifest)
                elif label == "cross-variant mismatch":
                    mutate(manifest)
                    manifest["comparison"]["variants"]["docker"]["runtime"]["request_settings"][
                        "context_tokens"
                    ] = 8192
                    manifest["comparison"]["variants"]["docker"]["runtime"]["effective_settings"][
                        "context_tokens"
                    ] = 8192
                else:
                    mutate(manifest)
                errors = validate_manifest(manifest)
                if expected_error.endswith(":"):
                    self.assertTrue(any(error.startswith(expected_error) for error in errors))
                else:
                    self.assertIn(expected_error, errors)

    def test_equal_nested_additional_settings_are_accepted(self) -> None:
        manifest = _valid_manifest()
        extra = {"limits": [1, {"enabled": True, "label": "test"}]}
        for variant_name in ("native", "docker"):
            runtime = manifest["comparison"]["variants"][variant_name]["runtime"]
            runtime["request_settings"]["extra"] = copy.deepcopy(extra)
            runtime["effective_settings"]["extra"] = copy.deepcopy(extra)
        self.assertEqual(validate_manifest(manifest), [])

    def test_deep_additional_settings_compare_without_recursion(self) -> None:
        def nested_value(leaf: object) -> object:
            value = leaf
            for _ in range(500):
                value = {"nested": [value]}
            return value

        matching = _valid_manifest()
        for variant_name in ("native", "docker"):
            runtime = matching["comparison"]["variants"][variant_name]["runtime"]
            runtime["request_settings"]["deep"] = nested_value("same")
            runtime["effective_settings"]["deep"] = nested_value("same")
        self.assertEqual(validate_manifest(matching), [])

        mismatching = _valid_manifest()
        for variant_name in ("native", "docker"):
            runtime = mismatching["comparison"]["variants"][variant_name]["runtime"]
            runtime["request_settings"]["deep"] = nested_value("same")
            runtime["effective_settings"]["deep"] = nested_value(
                "different" if variant_name == "native" else "same"
            )
        self.assertIn(
            "comparison.variants.native.runtime: request_settings != effective_settings",
            validate_manifest(mismatching),
        )

    def test_case_insensitive_equal_runtime_revisions_pass(self) -> None:
        manifest = _valid_manifest()
        manifest["comparison"]["variants"]["native"]["runtime"]["revision"] = _HEX40.upper()
        self.assertEqual(validate_manifest(manifest), [])

    def test_empty_repetitions_are_allowed_during_preflight(self) -> None:
        self.assertEqual(validate_manifest(_valid_manifest()), [])

    def test_valid_and_invalid_inputs_are_not_mutated(self) -> None:
        for manifest in (_valid_manifest(), {"schema_version": 2, "shared": None}):
            with self.subTest(valid=bool(manifest.get("shared"))):
                before = copy.deepcopy(manifest)
                validate_manifest(manifest)
                self.assertEqual(manifest, before)

    def test_diagnostics_do_not_echo_supplied_values(self) -> None:
        manifest = _valid_manifest()
        manifest["shared"]["toolkit_version"] = "PRIVATE_SENTINEL"
        manifest["shared"]["prompt_sha256"] = "SECRET_HASH_SENTINEL"
        errors = validate_manifest(manifest)
        self.assertTrue(errors)
        self.assertNotIn("PRIVATE_SENTINEL", " ".join(errors))
        self.assertNotIn("SECRET_HASH_SENTINEL", " ".join(errors))

    def test_error_order_is_independent_of_settings_mapping_insertion_order(self) -> None:
        first = _valid_manifest()
        second = _valid_manifest()
        request = first["comparison"]["variants"]["native"]["runtime"]["request_settings"]
        effective = first["comparison"]["variants"]["native"]["runtime"]["effective_settings"]
        invalid_settings = {
            "context_tokens": 0,
            "kv": " ",
            "reasoning_effort": " ",
            "output_limit": 0,
            "parallelism": 0,
        }
        request.clear()
        request.update(invalid_settings)
        effective.clear()
        effective.update(invalid_settings)
        reversed_settings = dict(reversed(list(request.items())))
        second["comparison"]["variants"]["native"]["runtime"]["request_settings"] = (
            reversed_settings
        )
        second["comparison"]["variants"]["native"]["runtime"]["effective_settings"] = dict(
            reversed(list(effective.items()))
        )
        expected = [
            "comparison.variants.native.runtime.request_settings.context_tokens: invalid positive integer",
            "comparison.variants.native.runtime.request_settings.kv: invalid nonempty string",
            "comparison.variants.native.runtime.request_settings.reasoning_effort: invalid nonempty string",
            "comparison.variants.native.runtime.request_settings.output_limit: invalid positive integer",
            "comparison.variants.native.runtime.request_settings.parallelism: invalid positive integer",
            "comparison.variants.native.runtime.effective_settings.context_tokens: invalid positive integer",
            "comparison.variants.native.runtime.effective_settings.kv: invalid nonempty string",
            "comparison.variants.native.runtime.effective_settings.reasoning_effort: invalid nonempty string",
            "comparison.variants.native.runtime.effective_settings.output_limit: invalid positive integer",
            "comparison.variants.native.runtime.effective_settings.parallelism: invalid positive integer",
        ]
        self.assertEqual(validate_manifest(first), expected)
        self.assertEqual(validate_manifest(first), validate_manifest(second))


if __name__ == "__main__":
    unittest.main()
