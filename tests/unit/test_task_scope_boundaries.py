"""Literal scope checks are independent of filesystem and glob interpretation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hybrid_sdlc.errors import TaskScopeViolationError
from hybrid_sdlc.task_policy import TaskScope, validate_task_scope_paths


def _scope() -> TaskScope:
    return TaskScope(
        intended_paths=frozenset({"src/parser.py", "new/café file.bin"}),
        excluded_paths=frozenset({"src/private.py"}),
    )


@pytest.mark.parametrize(
    "paths",
    [[], ["src/parser.py"], ["new/café file.bin"], ["src/parser.py", "new/café file.bin"]],
)
def test_exact_allowed_paths_and_empty_candidate_pass(paths: list[str]) -> None:
    validate_task_scope_paths(_scope(), paths)


@pytest.mark.parametrize(
    "path",
    [
        "src/private.py",
        "src/other.py",
        "src/parser.py.backup",
        "src/parser.py/child",
        "Src/parser.py",
        "src/PARSER.py",
        "src\\parser.py",
        "./src/parser.py",
        "src/../src/parser.py",
        "/src/parser.py",
        "new/cafe file.bin",
    ],
)
def test_no_prefix_case_or_path_normalization_bypass(path: str) -> None:
    with pytest.raises(TaskScopeViolationError) as caught:
        validate_task_scope_paths(_scope(), [path])
    assert caught.value.code == "TASK_SCOPE_VIOLATION"
    assert caught.value.exit_code == 3
    expected_reason = "excluded" if path == "src/private.py" else "not_intended"
    assert caught.value.details == {"violations": [{"path": path, "reason": expected_reason}]}


def test_violations_are_deduplicated_sorted_and_do_not_include_allowed_paths() -> None:
    with pytest.raises(TaskScopeViolationError) as caught:
        validate_task_scope_paths(
            _scope(), ["z.py", "src/private.py", "a.py", "z.py", "src/parser.py"]
        )
    assert caught.value.details == {
        "violations": [
            {"path": "a.py", "reason": "not_intended"},
            {"path": "src/private.py", "reason": "excluded"},
            {"path": "z.py", "reason": "not_intended"},
        ]
    }


def test_exclusion_wins_even_for_a_directly_constructed_overlapping_scope() -> None:
    scope = TaskScope(intended_paths=frozenset({"a.py"}), excluded_paths=frozenset({"a.py"}))
    with pytest.raises(TaskScopeViolationError) as caught:
        validate_task_scope_paths(scope, ["a.py"])
    assert caught.value.details["violations"] == [{"path": "a.py", "reason": "excluded"}]


def test_captured_scope_is_not_mutable() -> None:
    scope = _scope()
    with pytest.raises(ValidationError):
        scope.intended_paths = frozenset({"*"})
    assert isinstance(scope.intended_paths, frozenset)
    validate_task_scope_paths(scope, ("src/parser.py",))
