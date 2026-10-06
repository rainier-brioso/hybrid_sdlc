"""Safety and lifecycle tests for explicitly managed Strata deployments."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from hybrid_sdlc.cli import cli
from hybrid_sdlc.job_manager import JobManager, JobStatus
from hybrid_sdlc.runtime_strata import (
    RuntimeManagementError,
    _docker,
    _docker_env,
    _lifecycle_guard,
    _local_context,
    _owned_containers,
    _read_json,
    _reconcile_and_guard_activity,
    _render_compose,
    _verify_model_volume,
    configure,
    logs,
    normalize_managed_loopback,
    release_async_task,
    reserve_async_task,
    reserve_managed_endpoint,
    restart,
    start,
    status,
    stop,
    sync_task_lease,
)


@pytest.fixture
def runtime_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / ".hybrid-sdlc" / "runtime" / "strata"
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._state_root", lambda: root)
    return root


def test_configure_is_explicit_private_idempotent_and_does_not_call_docker(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._docker",
        lambda *args, **kwargs: pytest.fail("configure must not contact Docker"),
    )
    first = configure()
    second = configure()
    assert first["changed"] is True
    assert second["changed"] is False
    marker = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    assert marker["endpoint"] == "http://127.0.0.1:8080/v1"
    assert marker["project"].startswith("hsdlc-strata-")
    assert marker["model_volume"].startswith("hsdlc-strata-data-")
    assert (runtime_root / "activity.json").exists()
    assert "gpus: all" in (runtime_root / "compose.yaml").read_text(encoding="utf-8")
    assert "pull_policy: never" in (runtime_root / "compose.yaml").read_text(encoding="utf-8")


def test_configure_explicit_volume_is_external_and_settings_cannot_be_changed(
    runtime_root: Path,
) -> None:
    configure(port=8099, model_volume="existing-strata-models")
    compose = (runtime_root / "compose.yaml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8099:8080"' in compose
    assert "existing-strata-models" in compose
    assert "external: true" in compose
    assert configure(port=8099, model_volume="existing-strata-models")["changed"] is False
    with pytest.raises(RuntimeManagementError, match="already configured"):
        configure(port=8100)


@pytest.mark.parametrize("bad_port", [0, 65536, True, "8080"])
def test_configure_rejects_invalid_ports(runtime_root: Path, bad_port: object) -> None:
    with pytest.raises(RuntimeManagementError):
        configure(port=bad_port)  # type: ignore[arg-type]
    assert not (runtime_root / "runtime.json").exists()


def test_configure_rejects_invalid_volume_name(runtime_root: Path) -> None:
    with pytest.raises(RuntimeManagementError, match="volume"):
        configure(model_volume="../manual")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8080/v1", True),
        ("http://127.0.0.1:8080/v1/", True),
        ("http://[::1]:8080/v1", True),
        ("http://127.0.0.1:8080/v1/chat/completions", False),
        ("https://127.0.0.1:8080/v1", False),
        ("http://192.168.1.20:8080/v1", False),
        ("http://localhost:8081/v1", False),
        ("http://user@localhost:8080/v1", False),
        ("http://localhost:8080/v1?query=1", False),
    ],
)
def test_loopback_endpoint_normalization(url: str, expected: bool) -> None:
    assert normalize_managed_loopback(url, 8080) is expected


def test_missing_runtime_is_inert_for_sync_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "not-configured"
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._state_root", lambda: root)
    with sync_task_lease("http://127.0.0.1:8080/v1"):
        pass
    assert not root.exists()


def test_sync_reservation_blocks_lifecycle_for_full_task_and_releases_on_error(
    runtime_root: Path,
) -> None:
    configure()
    with pytest.raises(ValueError, match="task failure"):
        with sync_task_lease("http://localhost:8080/v1"):
            with pytest.raises(RuntimeManagementError, match="queued or running"):
                _lifecycle_guard(runtime_root)
            raise ValueError("task failure")
    _, _, activity, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    assert activity["leases"] == []


def test_async_reservation_blocks_lifecycle_across_repositories_until_terminal(
    runtime_root: Path, tmp_path: Path
) -> None:
    configure()
    first_root = tmp_path / "first-repo"
    second_root = tmp_path / "second-repo"
    first, second = JobManager(first_root), JobManager(second_root)
    job = reserve_async_task(
        "http://127.0.0.1:8080/v1",
        first_root,
        lambda job_id: first.create("spec.md", "T001", job_id=job_id),
    )
    with pytest.raises(RuntimeManagementError, match="queued or running"):
        _lifecycle_guard(runtime_root)
    other = second.create("spec.md", "T002")
    second.transition(other.job_id, JobStatus.FAILED, failure_reason="launch_failure")
    with pytest.raises(RuntimeManagementError, match="queued or running"):
        _lifecycle_guard(runtime_root)
    first.transition(job.job_id, JobStatus.FAILED, failure_reason="launch_failure")
    release_async_task(first_root, job.job_id, job.host_url)
    config, compose = _lifecycle_guard(runtime_root)
    assert compose.is_file()
    assert config["project"].startswith("hsdlc-strata-")


def test_async_release_refuses_nonterminal_record(runtime_root: Path, tmp_path: Path) -> None:
    configure()
    repo = tmp_path / "repo"
    manager = JobManager(repo)
    job = reserve_async_task(
        "http://localhost:8080/v1",
        repo,
        lambda job_id: manager.create(
            "spec.md", "T1", job_id=job_id, host_url="http://localhost:8080/v1"
        ),
    )
    with pytest.raises(RuntimeManagementError, match="before job is terminal"):
        release_async_task(repo, job.job_id, job.host_url)


def test_terminal_job_reservation_is_reconciled_even_after_worker_did_not_release(
    runtime_root: Path, tmp_path: Path
) -> None:
    configure()
    repo = tmp_path / "repo"
    manager = JobManager(repo)
    job = reserve_async_task(
        "http://localhost:8080/v1",
        repo,
        lambda job_id: manager.create("spec.md", "T1", job_id=job_id),
    )
    manager.transition(job.job_id, JobStatus.FAILED, failure_reason="launch_failure")
    _, _, activity, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    reconciled = _reconcile_and_guard_activity(runtime_root, activity)
    assert reconciled["leases"] == []


def test_registry_corruption_and_compose_tampering_fail_closed(runtime_root: Path) -> None:
    configure()
    (runtime_root / "activity.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeManagementError, match="corrupt"):
        status()

    configure_root = runtime_root.parent
    marker_path = runtime_root / "activity.json"
    marker_path.write_text('{"schema_version": 1, "leases": []}', encoding="utf-8")
    (runtime_root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(RuntimeManagementError, match="modified"):
        status()
    assert configure_root.exists()


def test_invalid_utf8_runtime_state_is_a_structured_runtime_error(tmp_path: Path) -> None:
    file = tmp_path / "invalid.json"
    file.write_bytes(b"\xff")
    with pytest.raises(RuntimeManagementError):
        _read_json(file)


def test_reparse_runtime_files_are_rejected(runtime_root: Path) -> None:
    configure()
    target = runtime_root / "activity.json"
    target.unlink()
    try:
        target.symlink_to(runtime_root / "runtime.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(RuntimeManagementError, match="symlink or reparse"):
        status()


def test_configure_filesystem_permission_failure_is_structured(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import runtime_strata

    original_mkdir = Path.mkdir

    def deny_runtime_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == runtime_root:
            raise PermissionError("denied")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_runtime_mkdir)
    with pytest.raises(RuntimeManagementError, match="state directory"):
        runtime_strata.configure()


def test_remote_docker_context_and_remote_named_pipe_are_refused(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._docker", lambda *a, **k: '"tcp://remote:2376"\n'
    )
    with pytest.raises(RuntimeManagementError, match="remote Docker"):
        _local_context(runtime_root, "remote")
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._docker",
        lambda *a, **k: '"npipe:////remotehost/pipe/docker_engine"\n',
    )
    with pytest.raises(RuntimeManagementError, match="remote Docker"):
        _local_context(runtime_root, "remote-pipe")


def test_existing_container_with_wrong_token_is_never_owned(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import runtime_strata

    configure()
    _, config, _, compose = runtime_strata._load_config()
    calls = iter(
        [
            '"unix:///var/run/docker.sock"\n',
            "container-id\n",
            json.dumps(
                {
                    "com.hybrid-sdlc.managed": "true",
                    "com.hybrid-sdlc.owner": "wrong",
                    "com.docker.compose.project": config["project"],
                }
            ),
        ]
    )
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", lambda *a, **k: next(calls))
    with pytest.raises(RuntimeManagementError, match="ownership token"):
        _owned_containers(config, compose, "local")


def test_unconfigured_lifecycle_is_json_error_without_touching_docker(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._docker", lambda *a, **k: pytest.fail("Docker must not run")
    )
    result = CliRunner().invoke(cli, ["runtime", "strata", "start", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.output)["error"]["code"] == "RUNTIME_MANAGEMENT_FAILED"
    assert not runtime_root.exists()


def test_lifecycle_file_lock_coordinates_threads(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc import runtime_strata

    configure()
    monkeypatch.setattr(runtime_strata, "_TIMEOUT", 0.1)
    lock = runtime_strata._activity_lock(runtime_root)
    lock.acquire()
    caught: list[Exception] = []

    def contender() -> None:
        try:
            with runtime_strata._locked(runtime_root):
                pass
        except Exception as exc:  # test captures the cross-thread lock timeout
            caught.append(exc)

    worker = threading.Thread(target=contender)
    worker.start()
    worker.join(timeout=2)
    lock.release()
    assert caught and isinstance(caught[0], RuntimeManagementError)


def test_compose_renderer_uses_fixed_assets_and_validated_values() -> None:
    composed = _render_compose("a" * 32, "hsdlc-strata-aaaaaaaaaaaa", 8088, "test-volume", False)
    assert "127.0.0.1:8088:8080" in composed
    assert 'com.hybrid-sdlc.owner: "' + "a" * 32 in composed
    assert "com.docker.compose.project" not in composed


def test_docker_runner_bounds_process_and_filters_context_override_environment(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hybrid_sdlc.processes import SubprocessResult

    configure()
    monkeypatch.setenv("COMPOSE_FILE", "attacker.yaml")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_CONFIG", "C:/untrusted")
    env = _docker_env()
    assert not {"COMPOSE_FILE", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"} & env.keys()
    assert env["COMPOSE_DISABLE_ENV_FILE"] == "1"

    captured: dict[str, object] = {}
    monkeypatch.setattr("hybrid_sdlc.runtime_strata.shutil.which", lambda *_, **__: "docker.exe")

    def bounded(
        argv: list[str], cwd: Path, child_env: dict[str, str], timeout: float, **kwargs: object
    ) -> SubprocessResult:
        captured.update(argv=argv, cwd=cwd, env=child_env, timeout=timeout, kwargs=kwargs)
        return SubprocessResult(0, "ok", "", False, 0.01, False)

    monkeypatch.setattr("hybrid_sdlc.runtime_strata.run_bounded_subprocess", bounded)
    assert _docker(["version"], runtime_root) == "ok"
    assert captured["argv"] == ["docker.exe", "version"]
    assert captured["timeout"] == 60.0
    assert captured["kwargs"] == {"buffer_cap_bytes": 65536}


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "message"),
    [(2, False, "Docker command failed"), (0, True, "timed out")],
)
def test_docker_runner_reports_nonzero_and_timeout(
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    timed_out: bool,
    message: str,
) -> None:
    from hybrid_sdlc.processes import SubprocessResult

    monkeypatch.setattr("hybrid_sdlc.runtime_strata.shutil.which", lambda *_, **__: "docker")
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata.run_bounded_subprocess",
        lambda *a, **k: SubprocessResult(exit_code, "a" * 100000, "failure", True, 0.1, timed_out),
    )
    with pytest.raises(RuntimeManagementError, match=message):
        _docker(["ps"], runtime_root)


def test_start_stop_restart_use_only_owned_compose_project_and_never_build_or_pull(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()
    calls: list[list[str]] = []

    def docker(argv: list[str], *_: object, **__: object) -> str:
        calls.append(argv)
        if argv == ["context", "show"]:
            return "default\n"
        if argv[:2] == ["context", "inspect"]:
            return '"unix:///var/run/docker.sock"\n'
        if "ps" in argv:
            return ""
        if "volume" in argv and "ls" in argv:
            return ""
        return "fake docker lifecycle ok\n"

    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", docker)
    assert start()["status"] == "started"
    assert stop()["status"] == "stopped"
    assert restart()["status"] == "restarted"
    compose_actions = [
        call for call in calls if call and call[0] == "--context" and "compose" in call
    ]
    assert any(
        "up" in call and "--no-build" in call and "--pull" in call and "never" in call
        for call in compose_actions
    )
    assert any("--no-recreate" in call for call in compose_actions)
    assert any("stop" in call for call in compose_actions)
    assert any("restart" in call for call in compose_actions)
    assert all(
        "down" not in call and "build" not in call and "pull" not in call
        for call in compose_actions
    )
    assert all(str(runtime_root / "compose.yaml") in call for call in compose_actions)


def test_lifecycle_actions_do_not_reach_docker_while_sync_lease_active(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()
    monkeypatch.setattr(
        "hybrid_sdlc.runtime_strata._docker",
        lambda *a, **k: pytest.fail("Docker must not be called"),
    )
    with sync_task_lease("http://127.0.0.1:8080/v1"):
        with pytest.raises(RuntimeManagementError, match="queued or running"):
            stop()


def test_failed_mutation_keeps_uncertainty_sentinel_and_blocks_managed_tasks(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure()

    def docker(argv: list[str], *_: object, **__: object) -> str:
        if argv == ["context", "show"]:
            return "default\n"
        if argv[:2] == ["context", "inspect"]:
            return '"unix:///var/run/docker.sock"\n'
        if "ps" in argv or ("volume" in argv and "ls" in argv):
            return ""
        if "up" in argv:
            raise RuntimeManagementError("Docker command timed out", details={"timed_out": True})
        return ""

    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", docker)
    with pytest.raises(RuntimeManagementError, match="timed out"):
        start()
    activity = json.loads((runtime_root / "activity.json").read_text(encoding="utf-8"))
    assert [lease["kind"] for lease in activity["leases"]] == ["lifecycle_unknown"]
    with pytest.raises(RuntimeManagementError, match="did not return cleanly"):
        with sync_task_lease("http://localhost:8080/v1"):
            pytest.fail("uncertain lifecycle must block task execution")
    with pytest.raises(RuntimeManagementError, match="did not return cleanly"):
        reserve_async_task(
            "http://localhost:8080/v1",
            tmp_path / "repo",
            lambda _: pytest.fail("uncertain lifecycle must block job creation"),
        )

    report = status()
    assert report["lifecycle_uncertain"] == activity["leases"]
    assert logs()["lifecycle_uncertain"] == activity["leases"]


def test_missing_pinned_image_gives_build_guidance_before_mutation(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()

    def docker(argv: list[str], *_: object, **__: object) -> str:
        if argv == ["context", "show"]:
            return "default\n"
        if argv[:2] == ["context", "inspect"]:
            return '"unix:///var/run/docker.sock"\n'
        if "ps" in argv or ("volume" in argv and "ls" in argv):
            return ""
        if "image" in argv and "inspect" in argv:
            raise RuntimeManagementError(
                "Docker command failed", details={"exit_code": 1, "output": "No such image"}
            )
        pytest.fail(f"unexpected Docker operation before image check: {argv}")

    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", docker)
    with pytest.raises(RuntimeManagementError, match="does not build or pull images") as caught:
        start()
    assert "docker build" in caught.value.message
    activity = json.loads((runtime_root / "activity.json").read_text(encoding="utf-8"))
    assert activity["leases"] == []


def test_status_and_logs_report_owned_container_and_bound_log_output(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()
    config = json.loads((runtime_root / "runtime.json").read_text(encoding="utf-8"))
    calls: list[list[str]] = []

    def docker(argv: list[str], *_: object, **__: object) -> str:
        calls.append(argv)
        if argv == ["context", "show"]:
            return "default\n"
        if argv[:2] == ["context", "inspect"]:
            return '"unix:///var/run/docker.sock"\n'
        if "ps" in argv:
            return "owned-id\n"
        if "inspect" in argv and any("Config.Labels" in item for item in argv):
            return json.dumps(
                {
                    "com.hybrid-sdlc.managed": "true",
                    "com.hybrid-sdlc.owner": config["owner_token"],
                    "com.docker.compose.project": config["project"],
                }
            )
        if "logs" in argv:
            return "x" * 100000
        return "/strata-server running healthy\n"

    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", docker)
    assert status()["containers"] == [
        {"name": "strata-server", "state": "running", "health": "healthy"}
    ]
    output = logs(tail=7)
    assert output["status"] == "ok"
    assert len(output["logs"]) == 65536
    assert any("--tail" in call and "7" in call for call in calls)


def test_logs_without_container_are_read_only_and_bounded() -> None:
    with pytest.raises(RuntimeManagementError, match="from 1 through 10000"):
        logs(tail=10001)


def test_verify_model_volume_requires_exact_reuse_or_matching_owner(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(model_volume="explicit-reuse")
    _, config, _, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", lambda *a, **k: "")
    with pytest.raises(RuntimeManagementError, match="does not exist"):
        _verify_model_volume(config, "default", runtime_root)


def test_default_volume_with_conflicting_labels_is_not_adopted(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()
    _, config, _, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    outputs = iter([f"{config['model_volume']}\n", '{"some.owner":"someone-else"}'])
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", lambda *a, **k: next(outputs))
    with pytest.raises(RuntimeManagementError, match="ownership token"):
        _verify_model_volume(config, "default", runtime_root)


def test_default_volume_with_matching_owner_is_allowed(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure()
    _, config, _, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    labels = {"com.hybrid-sdlc.managed": "true", "com.hybrid-sdlc.owner": config["owner_token"]}
    outputs = iter([f"{config['model_volume']}\n", json.dumps(labels)])
    monkeypatch.setattr("hybrid_sdlc.runtime_strata._docker", lambda *a, **k: next(outputs))
    _verify_model_volume(config, "default", runtime_root)


def test_managed_endpoint_decorator_holds_lease_across_common_runner(
    runtime_root: Path,
) -> None:
    configure()
    seen: list[int] = []

    @reserve_managed_endpoint
    def fake_runner(*, endpoint_url: str) -> None:
        _, _, activity, _ = __import__(
            "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
        )._load_config()
        seen.append(len(activity["leases"]))

    fake_runner(endpoint_url="http://localhost:8080/v1")
    assert seen == [1]


def test_async_reservation_cleans_up_only_when_job_was_not_persisted(
    runtime_root: Path, tmp_path: Path
) -> None:
    configure()
    repo = tmp_path / "repo"

    def fail_before_persist(job_id: str) -> object:
        raise RuntimeError(job_id)

    with pytest.raises(RuntimeError):
        reserve_async_task("http://localhost:8080/v1", repo, fail_before_persist)
    _, _, activity, _ = __import__(
        "hybrid_sdlc.runtime_strata", fromlist=["_load_config"]
    )._load_config()
    assert activity["leases"] == []
