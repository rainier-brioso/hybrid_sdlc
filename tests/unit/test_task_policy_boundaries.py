"""Independent strict-format and ceiling boundary checks for task policies."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hybrid_sdlc.task_policy import (
    TaskPolicyError,
    TaskScope,
    _check_limits,
    _parse_policy,
    _validate_path_lists,
    validate_required_deliverables,
)


def _document() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "task_id": "TASK-001",
        "spec_path": "docs/tasks/task.md",
        "test_profile": "unit",
        "intended_paths": ["src/parser.py", "tests/test_parser.py"],
        "excluded_paths": ["src/storage.py"],
        "limits": {
            "max_retries": 2,
            "task_timeout_seconds": 600,
            "attempt_timeout_seconds": 180,
            "aider_max_tokens": 4096,
            "aider_reasoning_budget_tokens": 1024,
        },
    }


def test_required_changed_paths_are_optional_for_existing_policies() -> None:
    policy = _parse_policy(json.dumps(_document()).encode())
    assert policy.required_changed_paths == []
    assert (
        TaskScope(intended_paths=frozenset(), excluded_paths=frozenset()).required_changed_paths
        == frozenset()
    )


@pytest.mark.parametrize(
    "required",
    [
        ["src/parser.py", "src/parser.py"],
        ["src/other.py"],
        ["src/storage.py"],
        ["src\\parser.py"],
        ["../parser.py"],
    ],
)
def test_required_paths_must_be_literal_unique_intended_and_not_excluded(
    required: list[str],
) -> None:
    document = _document()
    document["required_changed_paths"] = required
    with pytest.raises(TaskPolicyError):
        _validate_path_lists(_parse_policy(json.dumps(document).encode()))


def test_required_deliverable_must_be_changed_even_if_file_already_exists(tmp_path) -> None:
    scope = TaskScope(
        intended_paths=frozenset({"src/Required.py"}),
        excluded_paths=frozenset(),
        required_changed_paths=frozenset({"src/Required.py"}),
    )
    path = tmp_path / "src" / "Required.py"
    path.parent.mkdir()
    path.write_text("existing", encoding="utf-8")
    from hybrid_sdlc.errors import TaskDeliverableMissingError

    with pytest.raises(TaskDeliverableMissingError) as caught:
        validate_required_deliverables(scope, [], tmp_path)
    assert caught.value.code == "TASK_DELIVERABLE_MISSING"
    assert caught.value.exit_code == 3


@pytest.mark.parametrize("relative", ["src/Required.py", "src/aÃ±adir.py"])
def test_required_deliverable_accepts_exact_case_unicode_regular_file(
    tmp_path, relative: str
) -> None:
    scope = TaskScope(
        intended_paths=frozenset({relative}),
        excluded_paths=frozenset(),
        required_changed_paths=frozenset({relative}),
    )
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("new contents", encoding="utf-8")
    validate_required_deliverables(scope, [relative], tmp_path)


def test_required_deliverable_rejects_case_mismatch(tmp_path) -> None:
    scope = TaskScope(
        intended_paths=frozenset({"src/Required.py"}),
        excluded_paths=frozenset(),
        required_changed_paths=frozenset({"src/Required.py"}),
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "required.py").write_text("new", encoding="utf-8")
    from hybrid_sdlc.errors import TaskDeliverableMissingError

    with pytest.raises(TaskDeliverableMissingError):
        validate_required_deliverables(scope, ["src/required.py"], tmp_path)


def test_renaming_away_required_path_does_not_satisfy_deliverable(tmp_path) -> None:
    from hybrid_sdlc.errors import TaskDeliverableMissingError

    scope = TaskScope(
        intended_paths=frozenset({"src/Required.py", "src/Renamed.py"}),
        excluded_paths=frozenset(),
        required_changed_paths=frozenset({"src/Required.py"}),
    )
    renamed = tmp_path / "src" / "Renamed.py"
    renamed.parent.mkdir()
    renamed.write_text("renamed content", encoding="utf-8")
    with pytest.raises(TaskDeliverableMissingError) as caught:
        validate_required_deliverables(
            scope,
            ["src/Required.py", "src/Renamed.py"],
            tmp_path,
        )
    assert caught.value.details == {"invalid_paths": ["src/Required.py"]}


def _effective(**overrides: object) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "task_timeout_seconds": 600,
            "attempt_timeout_seconds": 180,
            "aider_max_tokens": 4096,
            "aider_reasoning_budget_tokens": 1024,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        b'{"limits":{"max_retries":1,"max_retries":2}}',
        b'{"limits":{"max_retries":NaN}}',
        b'{"limits":{"max_retries":Infinity}}',
        b'{"limits":{"max_retries":-Infinity}}',
        b"[]",
        b"null",
        b"true",
        b"{} trailing",
        b"\xff",
    ],
)
def test_invalid_json_never_exposes_input(raw: bytes) -> None:
    with pytest.raises(TaskPolicyError) as caught:
        _parse_policy(raw)
    assert caught.value.exit_code == 3
    assert caught.value.details == {}


@pytest.mark.parametrize("field", ["task_id", "spec_path", "test_profile", "schema_version"])
@pytest.mark.parametrize("value", [None, True, 1, [], {}])
def test_identity_fields_are_not_coerced(field: str, value: object) -> None:
    document = _document()
    document[field] = value
    with pytest.raises(TaskPolicyError):
        _parse_policy(json.dumps(document).encode())


@pytest.mark.parametrize("version", ["1", "1.0", "1.0.1", "2.0.0", ""])
def test_unsupported_version_fails_closed(version: str) -> None:
    document = _document()
    document["schema_version"] = version
    with pytest.raises(TaskPolicyError):
        _parse_policy(json.dumps(document).encode())


@pytest.mark.parametrize("nested", [False, True])
def test_unknown_fields_are_rejected_without_echoing_value(nested: bool) -> None:
    document = _document()
    target = document["limits"] if nested else document
    assert isinstance(target, dict)
    target["private_unknown"] = "private-token-never-echo"
    with pytest.raises(TaskPolicyError) as caught:
        _parse_policy(json.dumps(document).encode())
    assert "private-token-never-echo" not in str(caught.value)


@pytest.mark.parametrize(
    "field",
    [
        "max_retries",
        "task_timeout_seconds",
        "attempt_timeout_seconds",
        "aider_max_tokens",
        "aider_reasoning_budget_tokens",
    ],
)
@pytest.mark.parametrize("value", [True, "2", 2.0, [], {}])
def test_policy_limits_require_json_integers(field: str, value: object) -> None:
    document = _document()
    limits = document["limits"]
    assert isinstance(limits, dict)
    limits[field] = value
    with pytest.raises(TaskPolicyError):
        _parse_policy(json.dumps(document).encode())


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/src/parser.py",
        "C:/src/parser.py",
        "../parser.py",
        "src/../parser.py",
        "./parser.py",
        "src//parser.py",
        "src/",
        "src\\parser.py",
        "src/*.py",
        "src/**",
        "src/parser?.py",
        "src/[ab].py",
        "src/{a,b}.py",
        "src/stream:private",
        "src/line\nfeed.py",
        "src/tab\tname.py",
        "src/nul\x00.py",
        "src/delete\x7f.py",
        "src/\ud800.py",
    ],
)
@pytest.mark.parametrize("field", ["intended_paths", "excluded_paths"])
def test_path_language_rejects_ambiguous_or_nonliteral_paths(field: str, path: str) -> None:
    document = _document()
    document[field] = [path]
    policy = _parse_policy(json.dumps(document).encode())
    with pytest.raises(TaskPolicyError):
        _validate_path_lists(policy)


@pytest.mark.parametrize("field", ["intended_paths", "excluded_paths"])
def test_duplicate_path_declarations_are_rejected(field: str) -> None:
    document = _document()
    document[field] = ["src/parser.py", "src/parser.py"]
    with pytest.raises(TaskPolicyError):
        _validate_path_lists(_parse_policy(json.dumps(document).encode()))


def test_intended_and_excluded_paths_cannot_overlap() -> None:
    document = _document()
    document["excluded_paths"] = ["src/parser.py"]
    with pytest.raises(TaskPolicyError):
        _validate_path_lists(_parse_policy(json.dumps(document).encode()))


def test_case_sensitive_literals_and_new_file_declarations_are_preserved() -> None:
    document = _document()
    document["intended_paths"] = ["new/File.py", "new/file.py", "src/añadir.py"]
    policy = _parse_policy(json.dumps(document).encode())
    _validate_path_lists(policy)
    assert policy.intended_paths == ["new/File.py", "new/file.py", "src/añadir.py"]


@pytest.mark.parametrize(
    ("field", "effective"),
    [
        ("task_timeout_seconds", 601),
        ("attempt_timeout_seconds", 181),
        ("aider_max_tokens", 4097),
        ("aider_reasoning_budget_tokens", 1025),
        ("aider_max_tokens", None),
        ("aider_reasoning_budget_tokens", None),
        ("task_timeout_seconds", float("nan")),
        ("attempt_timeout_seconds", float("inf")),
        ("aider_max_tokens", True),
        ("aider_reasoning_budget_tokens", "1024"),
    ],
)
def test_uncheckable_or_excessive_effective_limits_are_rejected(
    field: str, effective: object
) -> None:
    policy = _parse_policy(json.dumps(_document()).encode())
    with pytest.raises(TaskPolicyError):
        _check_limits(policy, max_retries=2, config=_effective(**{field: effective}))


@pytest.mark.parametrize("retries", [3, True, "2", float("nan")])
def test_retry_ceiling_does_not_accept_coercion_or_excess(retries: object) -> None:
    policy = _parse_policy(json.dumps(_document()).encode())
    with pytest.raises(TaskPolicyError):
        _check_limits(policy, max_retries=retries, config=_effective())  # type: ignore[arg-type]


def test_finite_fractional_runner_timeouts_are_checked_without_truncation() -> None:
    policy = _parse_policy(json.dumps(_document()).encode())
    _check_limits(
        policy,
        max_retries=2,
        config=_effective(task_timeout_seconds=599.5, attempt_timeout_seconds=179.5),
    )
    with pytest.raises(TaskPolicyError):
        _check_limits(policy, max_retries=2, config=_effective(attempt_timeout_seconds=180.1))


def test_null_token_ceilings_and_independent_ceilings_do_not_override_config() -> None:
    document = _document()
    limits = document["limits"]
    assert isinstance(limits, dict)
    limits["aider_max_tokens"] = None
    limits["aider_reasoning_budget_tokens"] = None
    config = _effective(aider_max_tokens=None, aider_reasoning_budget_tokens=None)
    before = vars(config).copy()
    _check_limits(_parse_policy(json.dumps(document).encode()), max_retries=1, config=config)
    assert vars(config) == before

    limits["aider_max_tokens"] = 4096
    limits["aider_reasoning_budget_tokens"] = 8192
    _check_limits(_parse_policy(json.dumps(document).encode()), max_retries=2, config=_effective())


def test_zero_reasoning_is_a_real_ceiling() -> None:
    document = _document()
    limits = document["limits"]
    assert isinstance(limits, dict)
    limits["aider_reasoning_budget_tokens"] = 0
    policy = _parse_policy(json.dumps(document).encode())
    _check_limits(policy, max_retries=2, config=_effective(aider_reasoning_budget_tokens=0))
    with pytest.raises(TaskPolicyError):
        _check_limits(policy, max_retries=2, config=_effective(aider_reasoning_budget_tokens=1))
