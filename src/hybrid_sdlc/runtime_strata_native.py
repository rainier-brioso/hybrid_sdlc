"""Safe adapter for a pre-provisioned, host-native Strata runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, NoReturn, cast

from hybrid_sdlc.errors import ProcessExecutionError
from hybrid_sdlc.processes import SubprocessResult, run_bounded_subprocess
from hybrid_sdlc.runtime_strata import RuntimeManagementError

_MODEL = "qwen3.8-flash-next-coder-iq1_m"
_MAX_OUTPUT = 65536
_DANGEROUS_ENV = re.compile(r"^(?:PYTHON|STRATA)", re.IGNORECASE)
_SECRET = re.compile(r"(?i)(\b(?:api[_-]?key|token|authorization)\b\s*[:=]\s*)([^\s,;]+)")


def _fail(message: str) -> NoReturn:
    raise RuntimeManagementError(message)


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeManagementError(
            f"Native Strata configuration is missing or unreadable: {path.name}"
        ) from exc


def _python(install: Path) -> Path:
    candidates = (install / "python" / "python.exe", install / "python" / "bin" / "python3")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    _fail("Native Strata's installed Python interpreter is missing")


def _slug(value: str) -> str:
    slug = (
        re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_-]+", "-", value.lower())).strip("-_") or "launch"
    )
    if not slug[0].isalpha():
        slug = "l" + slug
    return slug[:48]


def _profile_id(preset: str) -> str:
    return "launch-" + _slug(preset)


def _valid_port_context(port: Any, context_size: Any) -> bool:
    return (
        type(port) is int
        and 1 <= port <= 65535
        and type(context_size) is int
        and 16384 <= context_size <= 131072
    )


def _start_client(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float
) -> SubprocessResult:
    """Bound the trusted runtime-start client while retaining its owned daemon.

    A kill-on-close task Job Object must not contain Strata's owner host. On an
    ambiguous timeout we kill only the client; the coordinator keeps its
    uncertainty sentinel and Strata retains ownership of any surviving server.
    This helper is used only for the fixed native `runtime start` operation.
    """
    before = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    chunks: list[list[bytes]] = [[], []]
    output_lock = threading.Lock()
    retained = 0
    truncated = False

    def drain(stream: Any, target: list[bytes]) -> None:
        nonlocal retained, truncated
        try:
            while data := stream.read(4096):
                with output_lock:
                    room = max(0, _MAX_OUTPUT - retained)
                    target.append(data[:room]) if room else None
                    retained += min(room, len(data))
                    truncated |= len(data) > room
        except OSError:
            pass
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=(stream, chunks[index]), daemon=True)
        for index, stream in enumerate((proc.stdout, proc.stderr))
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=2)
    except BaseException:
        proc.kill()
        proc.wait(timeout=2)
        raise
    for reader in readers:
        reader.join(timeout=1)
    # Inherited output handles must not turn an uncertain result into success.
    if any(reader.is_alive() for reader in readers):
        truncated = True
    with output_lock:
        out, err = (b"".join(part).decode("utf-8", errors="replace") for part in chunks)
    return SubprocessResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out,
        stderr=err,
        is_truncated=truncated,
        duration_seconds=time.monotonic() - before,
        timed_out=timed_out,
        cancelled=False,
    )


def _invoke(
    install: Path, components: Path, profiles: Path, args: list[str], timeout: float
) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not _DANGEROUS_ENV.match(k)}
    env.update(
        {
            "STRATA_COMPONENTS_DIR": str(components),
            "STRATA_INSTALL_DIR": str(install),
            "STRATA_PROFILE_DIR": str(profiles),
        }
    )
    try:
        argv = [str(_python(install)), str(install / "strata.py"), *args]
        if args[:2] == ["runtime", "start"]:
            result = _start_client(argv, install, env, timeout)
        else:
            result = run_bounded_subprocess(
                argv,
                install,
                env,
                timeout,
                buffer_cap_bytes=_MAX_OUTPUT,
            )
    except (OSError, ProcessExecutionError) as exc:
        raise RuntimeManagementError("Native Strata command could not be executed") from exc
    if result.timed_out or result.cancelled or result.is_truncated:
        _fail("Native Strata command did not complete cleanly")
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeManagementError("Native Strata returned malformed JSON") from exc
    # Strata deliberately returns exit 6/ok:false for a lost owner. Only the
    # exact status envelope is readable as recovery evidence; mutations and
    # every other unsuccessful response still fail closed.
    lost_owner = (
        len(args) == 6
        and args[:3] == ["runtime", "status", "--profile"]
        and args[4:] == ["--output", "json"]
    )
    if lost_owner and result.exit_code == 6 and _lost_owner_status(data, args[3]):
        return cast(dict[str, Any], data)
    if result.exit_code != 0:
        _fail("Native Strata command did not complete cleanly")
    if not isinstance(data, dict) or data.get("ok") is not True:
        _fail("Native Strata returned an unsuccessful or invalid result")
    return cast(dict[str, Any], data)


def _lost_owner_status(data: Any, profile: str) -> bool:
    return (
        isinstance(data, dict)
        and type(data.get("schema_version")) is int
        and data["schema_version"] == 1
        and data.get("ok") is False
        and data.get("profile") == profile
        and data.get("state") == "stale"
        and data.get("error") == "owner_host_lost"
        and type(data.get("exit_code")) is int
        and data["exit_code"] == 6
    )


def _source_config(profile: dict[str, Any]) -> Path:
    path = profile.get("source_config")
    if not isinstance(path, str) or not Path(path).is_absolute():
        _fail("Native Strata profile has an invalid source configuration path")
    return Path(path)


def prepare(
    *,
    install_dir: Path,
    components_dir: Path,
    profile_dir: Path,
    preset: str,
    port: int,
    context_size: int,
) -> dict[str, Any]:
    """Verify provisioned assets and return a drift-detecting registration."""
    install = Path(install_dir).expanduser().resolve()
    components = Path(components_dir).expanduser().resolve()
    profiles = Path(profile_dir).expanduser().resolve()
    if (
        not isinstance(preset, str)
        or not preset.strip()
        or not _valid_port_context(port, context_size)
    ):
        _fail("Native Strata preset, port, or context is invalid")
    profile_id = _profile_id(preset)
    cli = install / "strata.py"
    interp = _python(install)
    profile_path = profiles / f"{profile_id}.json"
    preset_path = profiles.parent / "launch-presets" / f"{_slug(preset)}.json"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        preset_data = json.loads(preset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagementError(
            "Native Strata profile or preset is missing or invalid"
        ) from exc
    if not isinstance(profile, dict) or profile.get("id") != profile_id:
        _fail("Native Strata saved profile identity is invalid")
    source = _source_config(profile)
    try:
        source_data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagementError(
            "Native Strata source configuration is missing or invalid"
        ) from exc
    if not isinstance(source_data, dict):
        _fail("Native Strata source configuration has an invalid shape")
    if (
        not isinstance(preset_data, dict)
        or not isinstance(preset_data.get("name"), str)
        or _slug(preset_data["name"]) != _slug(preset)
    ):
        _fail("Native Strata launch preset identity is invalid")
    if not cli.is_file() or not components.is_dir():
        _fail("Native Strata CLI or components directory is unavailable")
    effective = _invoke(
        install,
        components,
        profiles,
        ["profile", "show", profile_id, "--effective", "--output", "json"],
        60,
    )
    plan = effective.get("plan")
    if (
        not isinstance(effective.get("profile"), dict)
        or effective["profile"].get("id") != profile_id
        or not isinstance(plan, dict)
        or plan.get("profile") != profile_id
        or plan.get("model_name") != _MODEL
        or plan.get("engine_exe") != "component:strata-engine"
    ):
        _fail("Native Strata effective profile does not match the expected model and engine")
    effective_cfg, engine_argv = plan.get("effective"), plan.get("engine_argv")
    if not isinstance(engine_argv, list) or any(not isinstance(arg, str) for arg in engine_argv):
        _fail("Native Strata effective engine arguments have an invalid shape")
    try:
        arg_context = engine_argv[engine_argv.index("--max-context") + 1]
    except (AttributeError, ValueError, IndexError, TypeError):
        _fail("Native Strata effective engine arguments have no max-context")
    if (
        not isinstance(effective_cfg, dict)
        or effective_cfg.get("host") != "127.0.0.1"
        or effective_cfg.get("port") != port
        or arg_context != str(context_size)
    ):
        _fail("Native Strata effective endpoint or context differs from the requested settings")
    verified = _invoke(
        install,
        components,
        profiles,
        ["components", "verify", "--profile", profile_id, "--output", "json"],
        60,
    )
    report = verified.get("report")
    if not isinstance(report, dict) or report.get("ok") is not True:
        _fail("Native Strata engine or required profile assets failed verification")
    return {
        "schema_version": 1,
        "backend": "native",
        "install_dir": str(install),
        "components_dir": str(components),
        "profile_dir": str(profiles),
        "python": str(interp),
        "profile_id": profile_id,
        "preset": preset,
        "port": port,
        "endpoint": f"http://127.0.0.1:{port}/v1",
        "context_size": context_size,
        "model": _MODEL,
        "profile_sha256": _digest(profile_path),
        "preset_path": str(preset_path),
        "source_config": str(source),
        "source_sha256": _digest(source),
        "preset_sha256": _digest(preset_path),
        "cli_sha256": _digest(cli),
    }


def validate(config: Any) -> None:
    if (
        not isinstance(config, dict)
        or config.get("schema_version") != 1
        or config.get("backend") != "native"
    ):
        _fail("Native Strata registration has an invalid shape")
    paths = ("install_dir", "components_dir", "profile_dir", "python", "source_config")
    if any(
        not isinstance(config.get(key), str) or not Path(config[key]).is_absolute() for key in paths
    ):
        _fail("Native Strata registration contains invalid paths")
    if (
        not isinstance(config.get("preset"), str)
        or config.get("profile_id") != _profile_id(config["preset"])
        or config.get("model") != _MODEL
    ):
        _fail("Native Strata registration identity is invalid")
    if (
        type(config.get("port")) is not int
        or not 1 <= config["port"] <= 65535
        or type(config.get("context_size")) is not int
        or not 16384 <= config["context_size"] <= 131072
        or config.get("endpoint") != f"http://127.0.0.1:{config['port']}/v1"
    ):
        _fail("Native Strata registration endpoint or context is invalid")
    for key in ("profile_sha256", "source_sha256", "preset_sha256", "cli_sha256"):
        if (
            not isinstance(config.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", config[key]) is None
        ):
            _fail("Native Strata registration digest is invalid")
    install, profile_dir = Path(config["install_dir"]), Path(config["profile_dir"])
    preset_path = Path(config.get("preset_path", ""))
    expected_preset_path = profile_dir.parent / "launch-presets" / f"{_slug(config['preset'])}.json"
    if preset_path != expected_preset_path:
        _fail("Native Strata launch preset path does not match the registration")
    expected_python = _python(install)
    if str(expected_python) != config["python"] or Path(config["source_config"]) != _source_config(
        _read_object(profile_dir / f"{config['profile_id']}.json")
    ):
        _fail("Native Strata registration paths no longer match the saved profile")
    checks = {
        "profile_sha256": profile_dir / f"{config['profile_id']}.json",
        "source_sha256": Path(config["source_config"]),
        "preset_sha256": preset_path,
        "cli_sha256": install / "strata.py",
    }
    for key, path in checks.items():
        if _digest(path) != config[key]:
            _fail("Native Strata provisioned configuration changed; reconfigure the runtime")
    if not Path(config["components_dir"]).is_dir():
        _fail("Native Strata components directory is unavailable")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagementError("Native Strata profile is missing or invalid") from exc
    if not isinstance(value, dict):
        _fail("Native Strata profile has an invalid shape")
    return value


def _run(config: dict[str, Any], args: list[str], timeout: float) -> dict[str, Any]:
    validate(config)
    return _invoke(
        Path(config["install_dir"]),
        Path(config["components_dir"]),
        Path(config["profile_dir"]),
        args,
        timeout,
    )


def _status(config: dict[str, Any]) -> dict[str, Any]:
    data = _run(
        config, ["runtime", "status", "--profile", config["profile_id"], "--output", "json"], 60
    )
    state = data.get("state")
    if not isinstance(state, str) or state not in {
        "ready",
        "starting",
        "stopped",
        "failed",
        "stale",
    }:
        _fail("Native Strata status has an unknown state")
    if data.get("profile") != config["profile_id"]:
        _fail("Native Strata status profile does not match the registration")
    out: dict[str, Any] = {
        "backend": "native",
        "status": {
            "ready": "running",
            "starting": "starting",
            "stopped": "exited",
            "failed": "failed",
            "stale": "stale",
        }[state],
        "endpoint": config["endpoint"],
        "context_size": config["context_size"],
        "preset": config["preset"],
        "model": config["model"],
        "instance": {
            k: data.get(k)
            for k in (
                "owner_id",
                "instance_id",
                "host_pid",
                "child_pid",
                "state",
                "model_state",
                "api_identity_verified",
                "launch_attested",
                "generation_ready",
                "readiness_state",
                "api_url",
            )
            if k in data
        },
    }
    if _lost_owner_status(data, config["profile_id"]):
        out["recovery_available"] = True
        out["error_code"] = "owner_host_lost"
        out["next_action"] = (
            "Run `hsdlc start strata` to request Strata's guarded owner recovery. "
            "Strata refuses recovery when ownership cannot be verified."
        )
    for flag in ("api_identity_verified", "launch_attested", "generation_ready"):
        if flag in data and type(data[flag]) is not bool:
            _fail("Native Strata status contains invalid identity flags")
    if state == "ready":
        if (
            data.get("api_identity_verified") is not True
            or data.get("launch_attested") is not True
            or ("generation_ready" in data and data["generation_ready"] is not True)
            or data.get("model_state") != "ready"
            or data.get("api_url") != f"http://127.0.0.1:{config['port']}"
        ):
            _fail("Native Strata ready status failed identity or readiness checks")
        if (
            not isinstance(data.get("owner_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", data["owner_id"]) is None
            or not isinstance(data.get("instance_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", data["instance_id"]) is None
        ):
            _fail("Native Strata ready status lacks an owned instance identity")
    return out


def status(config: dict[str, Any]) -> dict[str, Any]:
    validate(config)
    return _status(config)


def _health(config: dict[str, Any]) -> None:
    from hybrid_sdlc.server_probe import _bounded_health_probe

    health = _bounded_health_probe(config["endpoint"], 5.0, native=True)
    if (
        not isinstance(health, dict)
        or health.get("available") is not True
        or health.get("status") != "ok"
        or health.get("service") != "strata"
        or health.get("model") != config["model"]
        or type(health.get("max_context")) is not int
        or health["max_context"] != config["context_size"]
        or health.get("loaded") is not True
    ):
        _fail("Native Strata health identity, model, context, or loaded state does not match")


def preflight(config: dict[str, Any], action: str) -> dict[str, Any]:
    validate(config)
    if action not in {"start", "stop", "restart"}:
        _fail("Unsupported native Strata action")
    current = _status(config)
    instance = current["instance"]
    if action == "start" and current["status"] == "running":
        _health(config)
        return {"action": action, "status": "running", "noop": True}
    if (
        action == "start"
        and current["status"] == "stale"
        and current.get("recovery_available") is True
    ):
        return {"action": action, "status": "stale", "recovery": True}
    if action == "start" and current["status"] not in {"exited"}:
        _fail("Native Strata profile state is unsafe for start")
    if action == "stop" and current["status"] not in {"running", "exited"}:
        _fail("Native Strata profile is not safely stoppable")
    if action == "restart" and current["status"] not in {"running", "exited"}:
        _fail("Native Strata profile state is unsafe for restart")
    if current["status"] == "running":
        _health(config)
    plan = {
        "action": action,
        "status": current["status"],
        "owner_id": instance.get("owner_id"),
        "instance_id": instance.get("instance_id"),
    }
    if action == "stop" and current["status"] == "exited":
        return {**plan, "noop": True}
    if action in {"stop", "restart"} and current["status"] == "running":
        planned = _run(
            config,
            ["runtime", "stop", "--profile", config["profile_id"], "--plan", "--output", "json"],
            60,
        )
        payload = planned.get("plan")
        if (
            not isinstance(payload, dict)
            or payload.get("profile") != config["profile_id"]
            or payload.get("owner_id") != plan["owner_id"]
            or payload.get("instance_id") != plan["instance_id"]
        ):
            _fail("Native Strata stop plan does not match the owned instance")
        if "would_drain" in payload and type(payload["would_drain"]) is not bool:
            _fail("Native Strata stop plan has invalid drain state")
        if payload.get("would_drain") is True:
            _fail("Native Strata stop is unsafe while external requests are active")
        plan["stop_plan"] = payload
    return plan


def mutate(config: dict[str, Any], action: str, preflight: dict[str, Any]) -> dict[str, Any]:
    validate(config)
    if not isinstance(preflight, dict) or preflight.get("action") != action:
        _fail("Native Strata mutation requires its matching preflight")
    if preflight.get("noop") is True:
        return _status(config)
    if action == "restart":
        if preflight.get("status") == "running":
            mutate(config, "stop", {**preflight, "action": "stop"})
        action = "start"
    if action == "stop":
        if preflight.get("status") == "exited":
            return _status(config)
        plan = preflight.get("stop_plan")
        if (
            not isinstance(plan, dict)
            or not isinstance(plan.get("owner_id"), str)
            or not isinstance(plan.get("instance_id"), str)
        ):
            _fail("Native Strata stop requires a verified owner and instance")
        _run(
            config,
            [
                "runtime",
                "stop",
                "--profile",
                config["profile_id"],
                "--if-owner-id",
                plan["owner_id"],
                "--if-instance-id",
                plan["instance_id"],
                "--output",
                "json",
            ],
            60,
        )
        result = _status(config)
        if result["status"] != "exited":
            _fail("Native Strata stop outcome could not be verified")
        return result
    if action != "start":
        _fail("Unsupported native Strata mutation")
    _run(
        config,
        [
            "runtime",
            "start",
            "--profile",
            config["profile_id"],
            "--wait",
            "180",
            "--output",
            "json",
        ],
        180,
    )
    result = _status(config)
    if result["status"] != "running":
        _fail("Native Strata start outcome could not be verified")
    _health(config)
    return result


def logs(config: dict[str, Any], tail: int = 100) -> dict[str, Any]:
    validate(config)
    if type(tail) is not int or not 1 <= tail <= 10000:
        _fail("Native Strata log tail must be from 1 through 10000")
    try:
        source = json.loads(Path(config["source_config"]).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagementError(
            "Native Strata run configuration is missing or invalid"
        ) from exc
    path = source.get("log") if isinstance(source, dict) else None
    if not isinstance(path, str) or not Path(path).is_absolute():
        _fail("Native Strata run configuration has no verified log path")
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _MAX_OUTPUT))
            text = stream.read(_MAX_OUTPUT).decode("utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeManagementError("Native Strata engine log is unavailable") from exc
    lines = text.splitlines()[-tail:]
    output = "\n".join(lines)[-_MAX_OUTPUT:]
    output = _SECRET.sub(r"\1[REDACTED]", output)
    return {"backend": "native", "status": "ok", "logs": output, "tail": tail}
