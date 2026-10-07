"""Offline preflight validation for comparative benchmark manifests.

This module validates required pins and settings only. It does not verify the
provenance referenced by tokenizer/configuration strings, ambient metrics,
repetitions, measurements, independent result evidence, or comparative-claim
eligibility. Empty repetitions are permitted during preflight; a successful
validation is not a completed benchmark and does not authorize a claim.

Validation is pure and uses only the supplied object. It does not access the
filesystem or network, launch subprocesses, or mutate the input.
"""

from __future__ import annotations

import math
from typing import TypeAlias

_Settings: TypeAlias = dict[str, object]
_REQUIRED_SETTINGS: tuple[str, ...] = (
    "context_tokens",
    "kv",
    "reasoning_effort",
    "output_limit",
    "parallelism",
)


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _is_sha256(value: object) -> bool:
    return _is_hex(value, 64)


def _is_git_revision(value: object) -> bool:
    return _is_hex(value, 40) or _is_hex(value, 64)


def _is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_finite_positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        # Avoid converting arbitrarily large JSON integers to float: that can
        # raise OverflowError even though an integer is finite by definition.
        return value > 0
    return isinstance(value, float) and math.isfinite(value) and value > 0


def _is_safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/"):
        return False
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        return False
    components = value.split("/")
    return all(component not in ("", ".", "..") for component in components)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _json_values_equal(left: object, right: object) -> bool:
    """Compare JSON values iteratively, preserving bool/int distinctions."""
    pending: list[tuple[object, object]] = [(left, right)]
    while pending:
        current_left, current_right = pending.pop()
        if type(current_left) is not type(current_right):
            return False
        if isinstance(current_left, dict) and isinstance(current_right, dict):
            if current_left.keys() != current_right.keys():
                return False
            pending.extend((current_left[key], current_right[key]) for key in current_left)
        elif isinstance(current_left, list) and isinstance(current_right, list):
            if len(current_left) != len(current_right):
                return False
            pending.extend(zip(current_left, current_right, strict=True))
        elif current_left != current_right:
            return False
    return True


def _validate_settings(value: object, path: str, errors: list[str]) -> _Settings | None:
    """Validate required settings and return a shallow copy for comparison."""
    if not isinstance(value, dict):
        errors.append(f"{path}: not an object")
        return None

    settings: _Settings = {}
    valid = True
    for key in _REQUIRED_SETTINGS:
        if key not in value:
            errors.append(f"{path}.{key}: missing")
            valid = False
            continue
        item = value[key]
        if key in ("context_tokens", "output_limit", "parallelism"):
            if not _is_positive_integer(item):
                errors.append(f"{path}.{key}: invalid positive integer")
                valid = False
            else:
                settings[key] = item
        elif not _is_nonblank_string(item):
            errors.append(f"{path}.{key}: invalid nonempty string")
            valid = False
        else:
            settings[key] = item

    # Additional settings are allowed and must participate in equality checks.
    # Dict equality is order independent; sorting here also makes the returned
    # representation deterministic for diagnostics and later comparison.
    for key in sorted(value):
        if key not in _REQUIRED_SETTINGS:
            settings[key] = value[key]
    return settings if valid else None


def validate_manifest(manifest: object) -> list[str]:
    """Return deterministic field-path errors for a schema-v2 preflight.

    Only JSON-compatible values are in scope. The function tolerates malformed
    mappings, lists, and scalar values without raising or echoing their values.
    An empty result confirms required preflight structure and pins; it does not
    establish source provenance, completed measurements, or benchmark claims.
    """
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["root: not an object"]

    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 2
    ):
        errors.append("schema_version: must be integer 2")

    shared = manifest.get("shared")
    if not isinstance(shared, dict):
        errors.append("shared: not an object")
    else:
        for key in ("toolkit_version", "aider_version", "python_version", "test_environment"):
            if not _is_nonblank_string(shared.get(key)):
                errors.append(f"shared.{key}: invalid nonempty string")
        if not _is_finite_positive_number(shared.get("timeout_seconds")):
            errors.append("shared.timeout_seconds: invalid finite positive number")
        if not _is_positive_integer(shared.get("retry_limit")):
            errors.append("shared.retry_limit: invalid positive integer")
        if not _is_sha256(shared.get("prompt_sha256")):
            errors.append("shared.prompt_sha256: invalid 64-hex")

        fixture = shared.get("fixture")
        if not isinstance(fixture, dict):
            errors.append("shared.fixture: not an object")
        else:
            for key in ("id", "test_profile", "acceptance", "independent_review"):
                if not _is_nonblank_string(fixture.get(key)):
                    errors.append(f"shared.fixture.{key}: invalid nonempty string")
            if not _is_git_revision(fixture.get("baseline_revision")):
                errors.append("shared.fixture.baseline_revision: invalid 40/64-hex")
            for key in ("baseline_sha256", "spec_sha256"):
                if not _is_sha256(fixture.get(key)):
                    errors.append(f"shared.fixture.{key}: invalid 64-hex")
            paths = fixture.get("allowed_patch_paths")
            if not isinstance(paths, list) or not paths:
                errors.append("shared.fixture.allowed_patch_paths: invalid nonempty list")
            else:
                seen_paths: set[str] = set()
                for index, path in enumerate(paths):
                    field = f"shared.fixture.allowed_patch_paths[{index}]"
                    if not _is_safe_relative_path(path):
                        errors.append(f"{field}: invalid path")
                    elif isinstance(path, str) and path in seen_paths:
                        errors.append(f"{field}: duplicate")
                    else:
                        seen_paths.add(path)

    comparison = manifest.get("comparison")
    if not isinstance(comparison, dict):
        errors.append("comparison: not an object")
        return errors

    for key in ("engine", "profile_id"):
        if not _is_nonblank_string(comparison.get(key)):
            errors.append(f"comparison.{key}: invalid nonempty string")

    pin = comparison.get("shared_model_pin")
    if not isinstance(pin, dict):
        errors.append("comparison.shared_model_pin: not an object")
    else:
        for key in ("repo", "quantization", "tokenizer_source", "config_source"):
            if not _is_nonblank_string(pin.get(key)):
                errors.append(f"comparison.shared_model_pin.{key}: invalid nonempty string")
        if not _is_git_revision(pin.get("revision")):
            errors.append("comparison.shared_model_pin.revision: invalid 40/64-hex")

        shards = pin.get("shards")
        if not isinstance(shards, list) or not shards:
            errors.append("comparison.shared_model_pin.shards: invalid nonempty list")
        else:
            seen_filenames: set[str] = set()
            for index, shard in enumerate(shards):
                field = f"comparison.shared_model_pin.shards[{index}]"
                if not isinstance(shard, dict):
                    errors.append(f"{field}: not an object")
                    continue
                filename = shard.get("filename")
                if not _is_safe_relative_path(filename):
                    errors.append(f"{field}.filename: invalid path")
                elif isinstance(filename, str):
                    if filename in seen_filenames:
                        errors.append(f"{field}.filename: duplicate")
                    else:
                        seen_filenames.add(filename)
                if not _is_positive_integer(shard.get("bytes")):
                    errors.append(f"{field}.bytes: invalid positive integer")
                if not _is_sha256(shard.get("sha256")):
                    errors.append(f"{field}.sha256: invalid 64-hex")

    variants = comparison.get("variants")
    if not isinstance(variants, dict):
        errors.append("comparison.variants: not an object")
        return errors

    effective_settings: dict[str, _Settings | None] = {}
    runtime_revisions: dict[str, str] = {}
    for variant_name in ("native", "docker"):
        variant = variants.get(variant_name)
        if not isinstance(variant, dict):
            errors.append(f"comparison.variants.{variant_name}: not an object")
            continue

        runtime = variant.get("runtime")
        if not isinstance(runtime, dict):
            errors.append(f"comparison.variants.{variant_name}.runtime: not an object")
            continue

        revision = runtime.get("revision")
        if not _is_git_revision(revision):
            errors.append(f"comparison.variants.{variant_name}.runtime.revision: invalid 40/64-hex")
        elif isinstance(revision, str):
            runtime_revisions[variant_name] = revision.lower()
        for key in ("build_args", "launch_args"):
            if not _is_string_list(runtime.get(key)):
                errors.append(
                    f"comparison.variants.{variant_name}.runtime.{key}: invalid string list"
                )

        if variant_name == "native":
            if not _is_sha256(runtime.get("binary_sha256")):
                errors.append("comparison.variants.native.runtime.binary_sha256: invalid 64-hex")
        else:
            if not _is_nonblank_string(runtime.get("image")):
                errors.append("comparison.variants.docker.runtime.image: invalid nonempty string")
            digest = runtime.get("image_digest")
            if not (
                isinstance(digest, str)
                and digest.startswith("sha256:")
                and _is_sha256(digest[len("sha256:") :])
            ):
                errors.append(
                    "comparison.variants.docker.runtime.image_digest: invalid sha256:<64hex>"
                )

        request = _validate_settings(
            runtime.get("request_settings"),
            f"comparison.variants.{variant_name}.runtime.request_settings",
            errors,
        )
        effective = _validate_settings(
            runtime.get("effective_settings"),
            f"comparison.variants.{variant_name}.runtime.effective_settings",
            errors,
        )
        if (
            request is not None
            and effective is not None
            and not _json_values_equal(request, effective)
        ):
            errors.append(
                f"comparison.variants.{variant_name}.runtime: "
                "request_settings != effective_settings"
            )
        effective_settings[variant_name] = effective

    if "native" in effective_settings and "docker" in effective_settings:
        native = effective_settings["native"]
        docker = effective_settings["docker"]
        if native is not None and docker is not None and not _json_values_equal(native, docker):
            errors.append("comparison.variants: effective_settings differ across variants")

    if len(runtime_revisions) == 2 and len(set(runtime_revisions.values())) != 1:
        errors.append("comparison.variants: runtime revisions do not match")

    return errors
