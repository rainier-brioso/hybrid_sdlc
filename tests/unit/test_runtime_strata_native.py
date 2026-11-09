from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hybrid_sdlc import runtime_strata_native as native
from hybrid_sdlc.errors import HybridSDLCError
from hybrid_sdlc.processes import SubprocessResult

_REAL_INVOKE = native._invoke


@pytest.fixture(autouse=True)
def native_cli_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def invoke(
        install: Path, components: Path, profiles: Path, args: list[str], timeout: float
    ) -> dict[str, Any]:
        if args[:2] == ["profile", "show"]:
            profile_id = args[2]
            profile = json.loads((profiles / f"{profile_id}.json").read_text(encoding="utf-8"))
            source = json.loads(Path(profile["source_config"]).read_text(encoding="utf-8"))
            return {
                "ok": True,
                "profile": {"id": profile_id},
                "plan": {
                    "profile": profile_id,
                    "model_name": source["model_name"],
                    "engine_exe": "component:strata-engine",
                    "effective": {"host": "127.0.0.1", "port": profile["overrides"]["port"]},
                    "engine_argv": source["args"],
                },
            }
        if args[:2] == ["components", "verify"]:
            return {"ok": True, "report": {"ok": True}}
        raise AssertionError(args)

    monkeypatch.setattr(native, "_invoke", invoke)


def provision(
    tmp_path: Path, preset: str = "hsdlc-coder-128k", port: int = 8080, context: int = 131072
) -> dict[str, Any]:
    profile_id = native._profile_id(preset)
    install = tmp_path / "install"
    (install / "python").mkdir(parents=True)
    (install / "python" / "python.exe").write_bytes(b"python")
    (install / "strata.py").write_text("cli", encoding="utf-8")
    profile_dir = install / "profiles"
    profile_dir.mkdir()
    (install / "launch-presets").mkdir()
    (install / "launch-presets" / f"{native._slug(preset)}.json").write_text(
        json.dumps({"name": preset}), encoding="utf-8"
    )
    source = tmp_path / "run.json"
    source.write_text(
        json.dumps(
            {
                "args": ["--max-context", str(context)],
                "port": port,
                "model_name": "qwen3.8-flash-next-coder-iq1_m",
                "log": str(tmp_path / "engine.log"),
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / f"{profile_id}.json").write_text(
        json.dumps(
            {
                "id": profile_id,
                "source_config": str(source),
                "overrides": {"host": "127.0.0.1", "port": port},
            }
        ),
        encoding="utf-8",
    )
    components = tmp_path / "components"
    components.mkdir()
    return native.prepare(
        install_dir=install,
        components_dir=components,
        profile_dir=profile_dir,
        preset=preset,
        port=port,
        context_size=context,
    )


def test_prepare_and_validate_pin_native_configuration(tmp_path: Path) -> None:
    config = provision(tmp_path)
    native.validate(config)
    assert config["backend"] == "native"
    assert config["context_size"] == 131072
    assert config["model"] == "qwen3.8-flash-next-coder-iq1_m"
    assert config["endpoint"] == "http://127.0.0.1:8080/v1"


def test_prepare_supports_slugged_profile_port_and_context(tmp_path: Path) -> None:
    config = provision(tmp_path, "Custom Coder 65k", port=8123, context=65536)
    native.validate(config)
    assert config["profile_id"] == "launch-custom-coder-65k"
    assert config["endpoint"] == "http://127.0.0.1:8123/v1"
    assert config["context_size"] == 65536


def test_validate_rejects_configuration_drift_and_boolean_context(tmp_path: Path) -> None:
    config = provision(tmp_path)
    Path(config["source_config"]).write_text("{}", encoding="utf-8")
    with pytest.raises(HybridSDLCError, match="changed"):
        native.validate(config)
    config["context_size"] = True
    with pytest.raises(HybridSDLCError, match="context"):
        native.validate(config)


def test_run_uses_explicit_safe_argv_environment_and_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = provision(tmp_path)
    monkeypatch.setattr(native, "_invoke", _REAL_INVOKE)
    monkeypatch.setenv("STRATA_PROFILE_DIR", "attacker")
    monkeypatch.setenv("STRATA_COMPONENTS_DIR", "attacker")
    monkeypatch.setenv("PYTHONPATH", "attacker")
    seen: dict[str, Any] = {}

    def fake(
        argv: list[str], cwd: Path, env: dict[str, str], timeout: float, **kwargs: Any
    ) -> SubprocessResult:
        seen.update(argv=argv, cwd=cwd, env=env, timeout=timeout, kwargs=kwargs)
        return SubprocessResult(0, '{"ok":true}', "", False, 0.1, False)

    monkeypatch.setattr(native, "run_bounded_subprocess", fake)
    native._run(config, ["runtime", "status"], 60)
    assert seen["argv"] == [
        config["python"],
        str(Path(config["install_dir"]) / "strata.py"),
        "runtime",
        "status",
    ]
    assert seen["env"]["STRATA_PROFILE_DIR"] == config["profile_dir"]
    assert "PYTHONPATH" not in seen["env"]
    assert seen["kwargs"] == {"buffer_cap_bytes": 65536}
    assert seen["timeout"] == 60


@pytest.mark.parametrize("stdout", ['{"ok":false}', "[]", '{"ok":1}', "truncated"])
def test_run_fails_closed_on_bad_json_or_false_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    config = provision(tmp_path)
    monkeypatch.setattr(native, "_invoke", _REAL_INVOKE)
    monkeypatch.setattr(
        native,
        "run_bounded_subprocess",
        lambda *a, **k: SubprocessResult(0, stdout, "", False, 0, False),
    )
    with pytest.raises(HybridSDLCError):
        native._run(config, ["runtime", "status"], 1)


def test_status_refuses_unknown_state_and_malformed_ready_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = provision(tmp_path)
    monkeypatch.setattr(
        native,
        "_run",
        lambda *a, **k: {"ok": True, "profile": config["profile_id"], "state": "mystery"},
    )
    with pytest.raises(HybridSDLCError, match="unknown state"):
        native.status(config)
    monkeypatch.setattr(
        native,
        "_run",
        lambda *a, **k: {
            "ok": True,
            "profile": config["profile_id"],
            "state": "ready",
            "api_identity_verified": True,
            "launch_attested": True,
            "generation_ready": True,
            "model_state": "ready",
            "api_url": "http://127.0.0.1:8080",
            "owner_id": "bad",
            "instance_id": "also-bad",
        },
    )
    with pytest.raises(HybridSDLCError, match="identity"):
        native.status(config)


def test_stop_requires_matching_native_plan_and_uses_both_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = provision(tmp_path)
    owner, instance = "a" * 32, "b" * 32
    calls: list[list[str]] = []

    def run(cfg: dict[str, Any], args: list[str], timeout: float) -> dict[str, Any]:
        calls.append(args)
        if "--plan" in args:
            return {
                "ok": True,
                "plan": {
                    "profile": config["profile_id"],
                    "owner_id": owner,
                    "instance_id": instance,
                },
            }
        return {"ok": True, "profile": config["profile_id"], "state": "ready"}

    monkeypatch.setattr(native, "_run", run)
    monkeypatch.setattr(native, "_health", lambda cfg: None)
    states = iter(("running", "exited"))
    monkeypatch.setattr(
        native,
        "_status",
        lambda cfg: {
            "status": next(states),
            "instance": {"owner_id": owner, "instance_id": instance},
        },
    )
    plan = native.preflight(config, "stop")
    result = native.mutate(config, "stop", plan)
    assert result["status"] == "exited"
    guarded = calls[1]
    assert guarded[guarded.index("--if-owner-id") + 1] == owner
    assert guarded[guarded.index("--if-instance-id") + 1] == instance
    assert "--all" not in guarded


def test_logs_are_bounded_and_tail_is_checked(tmp_path: Path) -> None:
    config = provision(tmp_path)
    Path(json.loads(Path(config["source_config"]).read_text())["log"]).write_text(
        "x" * 100000, encoding="utf-8"
    )
    result = native.logs(config, 8)
    assert len(result["logs"]) == 65536
    with pytest.raises(HybridSDLCError, match="tail"):
        native.logs(config, True)
