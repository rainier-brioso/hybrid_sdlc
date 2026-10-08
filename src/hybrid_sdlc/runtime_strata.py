"""Opt-in, ownership-checked lifecycle management for a packaged Strata service."""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import math
import os
import re
import secrets
import shutil
import stat
import time
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any, ParamSpec, TypeVar
from urllib.parse import urlparse

from filelock import FileLock, Timeout

from hybrid_sdlc.errors import HybridSDLCError, ProcessExecutionError
from hybrid_sdlc.processes import run_bounded_subprocess

_ASSET_DIR = Path(__file__).with_name("_runtime") / "strata"
_STARTUP_ENTRYPOINT_LINE = (
    '    entrypoint: ["/opt/strata/.venv/bin/python", "/opt/hybrid-sdlc/startup.py"]\n'
)
_STARTUP_MOUNT_LINE = "      - ./startup.py:/opt/hybrid-sdlc/startup.py:ro\n"
_SCHEMA = 1
_PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_VOLUME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
_YAML_NUMERIC_VOLUME = re.compile(
    r"(?:[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9_]+)?|"
    r"0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)"
)
_YAML_DATE_VOLUME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_OUTPUT = 64 * 1024
_TIMEOUT = 60.0
_PACKAGED_MODEL_ID = "qwen3.8-flash-next-coder-iq1_m"
_IMAGE_BUILD_COMMAND = (
    "docker build --build-arg CUDA_ARCHITECTURES=86 --build-arg BUILD_VISION=0 "
    "--tag hybrid-sdlc/strata:v0.1.39-cuda13-sm86 "
    "https://github.com/Niko1221/Strata.git#6f32ec070f23ced9f50e704d854d775da52591ab"
)
_P = ParamSpec("_P")
_R = TypeVar("_R")


class RuntimeManagementError(HybridSDLCError):
    """Invalid runtime configuration, unsafe ownership, or Docker failure."""

    default_code = "RUNTIME_MANAGEMENT_FAILED"


def reserve_managed_endpoint(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Hold a process-independent reservation around every bounded task entry point."""

    @functools.wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        endpoint = bound.arguments.get("endpoint_url")
        if not isinstance(endpoint, str):
            raise RuntimeManagementError("A task endpoint is required for runtime coordination")
        with sync_task_lease(endpoint):
            return function(*args, **kwargs)

    return wrapped


def _state_root() -> Path:
    """Return the per-user state path; tests patch this function instead of env vars."""

    return Path.home() / ".hybrid-sdlc" / "runtime" / "strata"


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def _ensure_safe_state(*, create: bool = False) -> Path:
    root = _state_root()
    base = root.parents[2]
    try:
        for part in (base / ".hybrid-sdlc", base / ".hybrid-sdlc" / "runtime", root):
            if _is_reparse(part):
                raise RuntimeManagementError(
                    "Runtime state path contains a symlink or reparse point"
                )
        if create:
            root.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                root.chmod(0o700)
    except OSError as exc:
        raise RuntimeManagementError(
            "Cannot access the per-user Strata runtime state directory"
        ) from exc
    return root


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManagementError(
            f"Runtime state is unreadable or corrupt: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeManagementError(f"Runtime state has an invalid shape: {path.name}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise RuntimeManagementError(f"Could not safely write runtime state: {path.name}") from exc
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def _paths() -> tuple[Path, Path, Path, Path]:
    root = _ensure_safe_state()
    for name in (
        "runtime.json",
        "activity.json",
        "compose.yaml",
        "worker-defaults.json",
        "startup.py",
        "activity.lock",
    ):
        if _is_reparse(root / name):
            raise RuntimeManagementError(
                f"Runtime state file is a symlink or reparse point: {name}"
            )
    return root, root / "runtime.json", root / "activity.json", root / "compose.yaml"


def _load_config() -> tuple[Path, dict[str, Any], dict[str, Any], Path]:
    root, marker, activity, compose = _paths()
    if not root.exists() or not marker.exists():
        raise RuntimeManagementError(
            "Strata runtime is not configured; run `hybrid-sdlc runtime strata configure`."
        )
    config = _read_json(marker)
    if config.get("schema_version") != _SCHEMA:
        raise RuntimeManagementError("Strata runtime configuration has an unsupported version")
    token = config.get("owner_token")
    port = config.get("port")
    volume = config.get("model_volume")
    project = config.get("project")
    if (
        not isinstance(token, str)
        or re.fullmatch(r"[0-9a-f]{32}", token) is None
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(volume, str)
        or _VOLUME_PATTERN.fullmatch(volume) is None
        or not isinstance(project, str)
        or _PROJECT_PATTERN.fullmatch(project) is None
        or project != f"hsdlc-strata-{token[:12]}"
        or not isinstance(config.get("reuse_model_volume"), bool)
        or config.get("endpoint") != f"http://127.0.0.1:{port}/v1"
        or (
            config.get("docker_context") is not None
            and not isinstance(config.get("docker_context"), str)
        )
    ):
        raise RuntimeManagementError("Strata runtime configuration failed validation")
    expected = _render_compose(token, project, port, volume, bool(config.get("reuse_model_volume")))
    try:
        actual = compose.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeManagementError("Managed Compose file is missing or unreadable") from exc
    legacy = _render_compose(
        token, project, port, volume, bool(config.get("reuse_model_volume")), use_startup=False
    )
    if actual not in (expected, legacy):
        raise RuntimeManagementError(
            "Managed Compose file was modified; refusing Docker operations"
        )
    if actual == expected:
        try:
            if (root / "startup.py").read_bytes() != (_ASSET_DIR / "startup.py").read_bytes():
                raise RuntimeManagementError("Managed Strata startup file was modified")
        except OSError as exc:
            raise RuntimeManagementError("Managed Strata startup file is missing") from exc
    defaults = root / "worker-defaults.json"
    try:
        if defaults.read_bytes() != (_ASSET_DIR / "worker-defaults.json").read_bytes():
            raise RuntimeManagementError("Managed Strata settings file was modified")
    except OSError as exc:
        raise RuntimeManagementError("Managed Strata settings file is missing") from exc
    records = _read_json(activity)
    if records.get("schema_version") != _SCHEMA or not isinstance(records.get("leases"), list):
        raise RuntimeManagementError(
            "Strata activity registry is corrupt; refusing lifecycle changes"
        )
    for lease in records["leases"]:
        if not isinstance(lease, dict) or lease.get("kind") not in {
            "sync",
            "submitting",
            "job",
            "lifecycle_unknown",
        }:
            raise RuntimeManagementError("Strata activity registry contains an invalid lease")
    return root, config, records, compose


def _render_compose(
    token: str,
    project: str,
    port: int,
    volume: str,
    reuse_model_volume: bool,
    *,
    use_startup: bool = True,
) -> str:
    try:
        template = (_ASSET_DIR / "compose.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeManagementError("Packaged Strata Compose asset is unavailable") from exc
    if not use_startup:
        # Recognize only the exact previous shipped layout, never arbitrary
        # operator edits. Existing installations are not silently migrated.
        template = template.replace(_STARTUP_ENTRYPOINT_LINE, "").replace(_STARTUP_MOUNT_LINE, "")
    # Keep ordinary names in their historical plain-scalar form so existing
    # runtime markers continue to validate against their canonical Compose file.
    # Quote YAML numeric, date, null, and boolean scalars to keep them strings.
    yaml_volume = volume
    is_yaml_date = _YAML_DATE_VOLUME.fullmatch(volume) is not None
    if is_yaml_date:
        try:
            date.fromisoformat(volume)
        except ValueError:
            is_yaml_date = False
    if (
        _YAML_NUMERIC_VOLUME.fullmatch(volume)
        or is_yaml_date
        or volume.lower()
        in {
            "null",
            "true",
            "false",
        }
    ):
        yaml_volume = json.dumps(volume)
    rendered = (
        template.replace("__OWNER_TOKEN__", token)
        .replace("__PROJECT__", project)
        .replace("__PORT__", str(port))
        .replace("__MODEL_VOLUME__", yaml_volume)
    )
    if yaml_volume != volume:
        rendered = rendered.replace(f"- {yaml_volume}:/data", f'- "{volume}:/data"')
    if reuse_model_volume:
        rendered = rendered.replace(
            f'  {yaml_volume}:\n    name: {yaml_volume}\n    labels:\n      com.hybrid-sdlc.managed: "true"\n      com.hybrid-sdlc.owner: "{token}"',
            f"  {yaml_volume}:\n    name: {yaml_volume}\n    external: true",
        )
    return rendered


def configure(*, port: int = 8080, model_volume: str | None = None) -> dict[str, Any]:
    """Explicitly create a managed runtime definition without contacting Docker."""

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise RuntimeManagementError("Port must be an integer from 1 through 65535")
    if model_volume is not None and _VOLUME_PATTERN.fullmatch(model_volume) is None:
        raise RuntimeManagementError("Model volume must be a valid Docker volume name")
    root = _ensure_safe_state(create=True)
    marker, activity, compose = root / "runtime.json", root / "activity.json", root / "compose.yaml"
    with _locked(root):
        if marker.exists() or any(item.name != "activity.lock" for item in root.iterdir()):
            existing_files = {item.name for item in root.iterdir()}
            if existing_files - {"activity.lock"}:
                _, existing, _, _ = _load_config()
                if existing["port"] == port and (
                    model_volume is None or existing["model_volume"] == model_volume
                ):
                    return {"status": "configured", "changed": False, "config": str(marker)}
                raise RuntimeManagementError(
                    "Strata is already configured with different settings. This version does not "
                    "support reconfiguration; the existing runtime definition remains unchanged."
                )
        token = secrets.token_hex(16)
        project = f"hsdlc-strata-{token[:12]}"
        volume = model_volume or f"hsdlc-strata-data-{token[:12]}"
        config = {
            "schema_version": _SCHEMA,
            "owner_token": token,
            "project": project,
            "port": port,
            "model_volume": volume,
            "reuse_model_volume": model_volume is not None,
            "endpoint": f"http://127.0.0.1:{port}/v1",
        }
        (root / "worker-defaults.json").write_bytes(
            (_ASSET_DIR / "worker-defaults.json").read_bytes()
        )
        (root / "startup.py").write_bytes((_ASSET_DIR / "startup.py").read_bytes())
        _atomic_json(activity, {"schema_version": _SCHEMA, "leases": []})
        composed = _render_compose(token, project, port, volume, model_volume is not None)
        temp = compose.with_name(f".{compose.name}.{secrets.token_hex(8)}.tmp")
        try:
            temp.write_text(composed, encoding="utf-8")
            if os.name != "nt":
                temp.chmod(0o600)
            os.replace(temp, compose)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()
        _atomic_json(marker, config)  # marker is published last
        return {
            "status": "configured",
            "changed": True,
            "config": str(marker),
            "endpoint": config["endpoint"],
        }


def normalize_managed_loopback(url: str, configured_port: int) -> bool:
    """Match only HTTP loopback aliases on the managed port and normalized /v1 path."""

    try:
        parsed = urlparse(url)
        effective_port = parsed.port if parsed.port is not None else 80
        if parsed.scheme != "http" or effective_port != configured_port:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return False
        return parsed.path.rstrip("/") in {"", "/v1"} and not parsed.query and not parsed.fragment
    except ValueError:
        return False


def _potential_managed_endpoint(url: str) -> tuple[Path, dict[str, Any], Path] | None:
    root = _state_root()
    if not root.exists():
        return None
    try:
        _, config, _, compose = _load_config()
    except RuntimeManagementError:
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname.lower().rstrip(".") in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise
        return None
    if normalize_managed_loopback(url, config["port"]):
        return root, config, compose
    return None


def _activity_lock(root: Path) -> FileLock:
    if _is_reparse(root / "activity.lock"):
        raise RuntimeManagementError("Runtime activity lock is a symlink or reparse point")
    return FileLock(str(root / "activity.lock"), timeout=_TIMEOUT)


@contextlib.contextmanager
def _locked(root: Path) -> Iterator[None]:
    try:
        with _activity_lock(root):
            yield
    except (OSError, Timeout) as exc:
        raise RuntimeManagementError(
            "Could not acquire or release the Strata activity lock"
        ) from exc


@contextlib.contextmanager
def sync_task_lease(url: str) -> Iterator[None]:
    """Reserve the managed endpoint for one synchronous run, releasing in all outcomes."""

    candidate = _potential_managed_endpoint(url)
    if candidate is None:
        yield
        return
    root, config, _ = candidate
    lease_id = secrets.token_hex(16)
    with _locked(root):
        _, current, activity, _ = _load_config()
        if current["owner_token"] != config["owner_token"]:
            raise RuntimeManagementError("Managed Strata identity changed while reserving task")
        _ensure_no_lifecycle_uncertainty(activity)
        leases = activity["leases"]
        leases.append({"lease_id": lease_id, "kind": "sync", "created_at": time.time()})
        _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
    try:
        yield
    finally:
        with _locked(root):
            _, _, activity, _ = _load_config()
            leases = [entry for entry in activity["leases"] if entry.get("lease_id") != lease_id]
            _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})


@contextlib.contextmanager
def managed_readiness_guard(url: str) -> Iterator[bool]:
    """Serialize a readiness inference with managed task reservations and lifecycle work."""

    candidate = _potential_managed_endpoint(url)
    if candidate is None:
        yield False
        return
    root, config, _ = candidate
    with _locked(root):
        _, current, activity, _ = _load_config()
        if current["owner_token"] != config["owner_token"]:
            raise RuntimeManagementError("Managed Strata identity changed during readiness check")
        activity = _reconcile_and_guard_activity(root, activity)
        if activity["leases"]:
            raise RuntimeManagementError(
                "Managed readiness was skipped because endpoint activity is unresolved.",
                code="READINESS_BUSY",
                details={
                    "active_kinds": sorted({str(item.get("kind")) for item in activity["leases"]})
                },
            )
        # Keep the shared lock through the bounded POST. Reservations and lifecycle
        # operations cannot enter until the inference request has returned.
        yield True


def reserve_async_task(url: str, repo_root: Path, create_job: Callable[[str], Any]) -> Any:
    """Atomically persist a queued job and its managed-endpoint activity lease."""

    candidate = _potential_managed_endpoint(url)
    if candidate is None:
        from hybrid_sdlc.job_manager import new_job_id

        return create_job(new_job_id())
    root, config, _ = candidate
    with _locked(root):
        _, current, activity, _ = _load_config()
        if current["owner_token"] != config["owner_token"]:
            raise RuntimeManagementError("Managed Strata identity changed while submitting task")
        _ensure_no_lifecycle_uncertainty(activity)
        from hybrid_sdlc.job_manager import new_job_id

        planned_job_id = new_job_id()
        placeholder = f"job:{planned_job_id}"
        leases = activity["leases"] + [
            {
                "lease_id": placeholder,
                "kind": "submitting",
                "repo_root": str(repo_root.resolve()),
                "job_id": planned_job_id,
            }
        ]
        _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
        try:
            job = create_job(planned_job_id)
        except BaseException:
            from hybrid_sdlc.job_manager import JobManager, JobStatus

            try:
                existing = JobManager(repo_root).get(planned_job_id)
            except FileNotFoundError:
                leases = [entry for entry in leases if entry.get("lease_id") != placeholder]
                _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
            except Exception:
                # A corrupt or inaccessible record cannot be distinguished from active work.
                pass
            else:
                if existing.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                    leases = [entry for entry in leases if entry.get("lease_id") != placeholder]
                else:
                    leases[-1] = {
                        "lease_id": placeholder,
                        "kind": "job",
                        "repo_root": str(repo_root.resolve()),
                        "job_id": planned_job_id,
                    }
                _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
            raise
        leases = [entry for entry in leases if entry.get("lease_id") != placeholder]
        leases.append(
            {
                "lease_id": f"job:{job.job_id}",
                "kind": "job",
                "repo_root": str(repo_root.resolve()),
                "job_id": job.job_id,
            }
        )
        _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
        return job


def release_async_task(repo_root: Path, job_id: str, endpoint_url: str | None) -> None:
    """Release a durable job lease only after its terminal state has been persisted."""

    if endpoint_url is None:
        return
    candidate = _potential_managed_endpoint(endpoint_url)
    if candidate is None:
        return
    root = candidate[0]
    with _locked(root):
        _, _, activity, _ = _load_config()
        matching = any(
            entry.get("kind") == "job"
            and entry.get("job_id") == job_id
            and entry.get("repo_root") == str(repo_root.resolve())
            for entry in activity["leases"]
        )
        if not matching:
            return
        from hybrid_sdlc.job_manager import JobManager, JobStatus

        try:
            job = JobManager(Path(repo_root)).get(job_id)
        except Exception as exc:
            raise RuntimeManagementError("Could not confirm terminal async job state") from exc
        if job.status not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise RuntimeManagementError(
                "Cannot release managed Strata reservation before job is terminal"
            )
        leases = [
            entry
            for entry in activity["leases"]
            if not (
                entry.get("kind") == "job"
                and entry.get("job_id") == job_id
                and entry.get("repo_root") == str(repo_root.resolve())
            )
        ]
        _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})


def _docker_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        upper = key.upper()
        if upper.startswith("COMPOSE_") or upper in {
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
        }:
            env.pop(key, None)
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    return env


def _docker(argv: list[str], root: Path, *, timeout: float = _TIMEOUT) -> str:
    executable = shutil.which("docker", path=_docker_env().get("PATH"))
    if executable is None:
        raise RuntimeManagementError("Docker CLI was not found on PATH")
    try:
        result = run_bounded_subprocess(
            [executable, *argv], root, _docker_env(), timeout, buffer_cap_bytes=_MAX_OUTPUT
        )
    except (OSError, ProcessExecutionError) as exc:
        raise RuntimeManagementError("Could not run the Docker CLI safely") from exc
    output = result.stdout + result.stderr
    if result.timed_out:
        raise RuntimeManagementError("Docker command timed out", details={"argv": argv})
    if result.exit_code != 0:
        raise RuntimeManagementError(
            "Docker command failed",
            details={"argv": argv, "exit_code": result.exit_code, "output": output[-4000:]},
        )
    return output


def _local_context(root: Path, context: str | None = None) -> str:
    if context is None:
        context = _docker(["context", "show"], root).strip()
    if not context or "\n" in context or "\r" in context:
        raise RuntimeManagementError("Docker returned an invalid context name")
    endpoint_text = _docker(
        ["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}", context], root
    ).strip()
    try:
        endpoint = json.loads(endpoint_text)
    except json.JSONDecodeError as exc:
        raise RuntimeManagementError("Docker returned an invalid context endpoint") from exc
    unix = (
        isinstance(endpoint, str)
        and endpoint.startswith("unix://")
        and urlparse(endpoint).path.startswith("/")
        and not urlparse(endpoint).hostname
    )
    named_pipe = (
        isinstance(endpoint, str)
        and re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]+", endpoint, re.IGNORECASE) is not None
    )
    if not (unix or named_pipe):
        raise RuntimeManagementError(
            "The selected Docker context is not a local Unix socket or Windows named pipe; "
            "remote Docker endpoints are not supported.",
            details={"context": context, "endpoint": endpoint},
        )
    return context


def _compose_prefix(config: dict[str, Any], compose: Path, context: str) -> list[str]:
    return [
        "--context",
        context,
        "compose",
        "--project-name",
        str(config["project"]),
        "--project-directory",
        str(compose.parent),
        "--file",
        str(compose),
    ]


def _owned_containers(
    config: dict[str, Any], compose: Path, context: str | None = None
) -> list[str]:
    root = compose.parent
    project = str(config["project"])
    context = _local_context(root, context or config.get("docker_context"))
    output = _docker(
        [
            "--context",
            context,
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        root,
    )
    ids = [line.strip() for line in output.splitlines() if line.strip()]
    if not ids:
        return []
    inspected = _docker(
        ["--context", context, "inspect", "--format", "{{json .Config.Labels}}", *ids], root
    )
    rows = [line.strip() for line in inspected.splitlines() if line.strip()]
    if len(rows) != len(ids):
        raise RuntimeManagementError("Docker returned incomplete container ownership data")
    for container_id, row in zip(ids, rows, strict=True):
        try:
            labels = json.loads(row)
        except json.JSONDecodeError as exc:
            raise RuntimeManagementError("Docker returned invalid container labels") from exc
        if (
            not isinstance(labels, dict)
            or labels.get("com.hybrid-sdlc.managed") != "true"
            or labels.get("com.hybrid-sdlc.owner") != config["owner_token"]
            or labels.get("com.docker.compose.project") != project
        ):
            raise RuntimeManagementError(
                "A container uses the managed Compose project identity without the ownership token; "
                "refusing to control it.",
                details={"container_id": container_id},
            )
    return ids


def _verify_model_volume(config: dict[str, Any], context: str, root: Path) -> None:
    name = str(config["model_volume"])
    output = _docker(
        ["--context", context, "volume", "ls", "--quiet", "--filter", f"name={name}"], root
    )
    matches = {line.strip() for line in output.splitlines() if line.strip() == name}
    reuse = bool(config["reuse_model_volume"])
    if reuse and name not in matches:
        raise RuntimeManagementError(
            "The explicitly selected model volume does not exist; create it or configure another volume.",
            details={"volume": name},
        )
    if name not in matches or reuse:
        return
    labels_output = _docker(
        ["--context", context, "volume", "inspect", "--format", "{{json .Labels}}", name], root
    ).strip()
    try:
        labels = json.loads(labels_output)
    except json.JSONDecodeError as exc:
        raise RuntimeManagementError("Docker returned invalid model-volume labels") from exc
    if (
        not isinstance(labels, dict)
        or labels.get("com.hybrid-sdlc.managed") != "true"
        or labels.get("com.hybrid-sdlc.owner") != config["owner_token"]
    ):
        raise RuntimeManagementError(
            "A volume with the managed model-volume name exists without the ownership token; "
            "refusing to reuse it.",
            details={"volume": name},
        )


def _verify_pinned_image(context: str, root: Path) -> None:
    try:
        _docker(
            ["--context", context, "image", "inspect", "hybrid-sdlc/strata:v0.1.39-cuda13-sm86"],
            root,
        )
    except RuntimeManagementError as exc:
        output = str(exc.details.get("output", "")).lower()
        if any(
            marker in output
            for marker in (
                "no such image",
                "image not found",
                "pull access denied",
                "manifest unknown",
            )
        ):
            raise RuntimeManagementError(
                "The pinned Strata image is not installed. The lifecycle CLI does not build "
                "or pull images. Build the pinned image explicitly with:\n"
                f"{_IMAGE_BUILD_COMMAND}"
            ) from exc
        raise


def _reconcile_and_guard_activity(root: Path, activity: dict[str, Any]) -> dict[str, Any]:
    leases = activity["leases"]
    if not leases:
        return activity
    from hybrid_sdlc.job_manager import JobManager, JobStatus

    remaining: list[dict[str, Any]] = []
    changed = False
    for entry in leases:
        if not isinstance(entry, dict):
            raise RuntimeManagementError("Strata activity registry contains an invalid lease")
        kind = entry.get("kind")
        if kind in {"sync", "submitting", "lifecycle_unknown"}:
            remaining.append(entry)
            continue
        if (
            kind != "job"
            or not isinstance(entry.get("repo_root"), str)
            or not isinstance(entry.get("job_id"), str)
        ):
            raise RuntimeManagementError("Strata activity registry contains an unknown lease")
        try:
            job = JobManager(Path(entry["repo_root"])).get(entry["job_id"])
        except Exception as exc:
            raise RuntimeManagementError(
                "Could not verify a persisted Strata task reservation; refusing lifecycle change",
                details={"job_id": entry.get("job_id")},
            ) from exc
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            changed = True
        else:
            remaining.append(entry)
    if changed:
        updated = {"schema_version": _SCHEMA, "leases": remaining}
        _atomic_json(root / "activity.json", updated)
        return updated
    return activity


def _lifecycle_guard(root: Path) -> tuple[dict[str, Any], Path]:
    _, config, activity, compose = _load_config()
    _ensure_no_lifecycle_uncertainty(activity)
    activity = _reconcile_and_guard_activity(root, activity)
    if activity["leases"]:
        raise RuntimeManagementError(
            "Cannot change the managed Strata service while a task is queued or running.",
            details={"active_tasks": activity["leases"]},
        )
    return config, compose


def _ensure_no_lifecycle_uncertainty(activity: dict[str, Any]) -> None:
    if any(lease.get("kind") == "lifecycle_unknown" for lease in activity["leases"]):
        raise RuntimeManagementError(
            "A prior Docker lifecycle command did not return cleanly. Inspect status and logs, "
            "confirm the service has settled, then resolve the recorded activity entry manually."
        )


def _set_lifecycle_uncertain(root: Path, action: str) -> str:
    _, _, activity, _ = _load_config()
    lease_id = secrets.token_hex(16)
    leases = activity["leases"] + [
        {
            "lease_id": lease_id,
            "kind": "lifecycle_unknown",
            "action": action,
            "created_at": time.time(),
        }
    ]
    _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})
    return lease_id


def _clear_lifecycle_uncertain(root: Path, lease_id: str) -> None:
    _, _, activity, _ = _load_config()
    leases = [entry for entry in activity["leases"] if entry.get("lease_id") != lease_id]
    _atomic_json(root / "activity.json", {"schema_version": _SCHEMA, "leases": leases})


def _run_lifecycle(action: str) -> dict[str, Any]:
    root = _ensure_safe_state()
    if not root.exists() or not (root / "runtime.json").exists():
        raise RuntimeManagementError(
            "Strata runtime is not configured; run `hybrid-sdlc runtime strata configure`."
        )
    with _locked(root):
        config, compose = _lifecycle_guard(root)
        context = _local_context(root, config.get("docker_context"))
        if config.get("docker_context") is None:
            config["docker_context"] = context
            _atomic_json(root / "runtime.json", config)
        _owned_containers(config, compose)
        if action == "start":
            _verify_model_volume(config, context, root)
            _verify_pinned_image(context, root)
        if action not in {"start", "stop", "restart"}:
            raise RuntimeManagementError("Unsupported lifecycle action")
        operation_lease = _set_lifecycle_uncertain(root, action)
        try:
            # --no-build and --pull never guarantee no image build/download;
            # first startup can download model weights into the named volume.
            if action == "start":
                output = _docker(
                    [
                        *_compose_prefix(config, compose, context),
                        "up",
                        "--detach",
                        "--no-build",
                        "--pull",
                        "never",
                        "--no-recreate",
                        "strata-server",
                    ],
                    root,
                )
            elif action == "stop":
                output = _docker(
                    [*_compose_prefix(config, compose, context), "stop", "strata-server"], root
                )
            else:
                _docker(
                    [*_compose_prefix(config, compose, context), "restart", "strata-server"], root
                )
                output = "Restarted managed Strata service."
        except RuntimeManagementError:
            # A failed or timed-out client may leave the Docker daemon applying the request.
            # Preserve the sentinel so managed task activity fails closed pending inspection.
            raise
        except Exception:
            # A failed or timed-out client may leave the Docker daemon applying the request.
            # Preserve the sentinel so managed task activity fails closed pending inspection.
            raise
        _clear_lifecycle_uncertain(root, operation_lease)
        return {
            "status": action + "ed" if action != "stop" else "stopped",
            "output": output[-4000:],
        }


def status() -> dict[str, Any]:
    root, config, activity, compose = _load_config()
    context = _local_context(root, config.get("docker_context"))
    ids = _owned_containers(config, compose, context)
    states: list[dict[str, str]] = []
    if ids:
        output = _docker(
            [
                "--context",
                context,
                "inspect",
                "--format",
                "{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-health{{end}}",
                *ids,
            ],
            root,
        )
        for line in output.splitlines():
            fields = line.strip().split()
            if len(fields) >= 3:
                states.append(
                    {"name": fields[0].lstrip("/"), "state": fields[1], "health": fields[2]}
                )
    return {
        "status": "configured" if not states else states[0]["state"],
        "endpoint": config["endpoint"],
        "project": config["project"],
        "containers": states,
        "lifecycle_uncertain": [
            lease for lease in activity["leases"] if lease.get("kind") == "lifecycle_unknown"
        ],
    }


def diagnose(
    *,
    readiness: bool = False,
    timeout: float = 3.0,
    repo_root: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Read-only diagnostics for the configured managed endpoint."""

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 1 <= timeout <= 30
    ):
        raise RuntimeManagementError("Diagnostic timeout must be from 1 through 30 seconds")
    from hybrid_sdlc.server_probe import (
        _bounded_health_probe,
        _bounded_model_probe,
        probe_readiness,
    )
    from hybrid_sdlc.timeout_diagnostics import read_timeout_evidence, suspected_stall

    if (repo_root is None) != (run_id is None):
        raise RuntimeManagementError("Timeout evidence requires both repository root and run ID")

    _, config, _, _ = _load_config()
    endpoint = str(config["endpoint"])
    health_result = _bounded_health_probe(endpoint, float(timeout))
    health_available = bool(health_result.get("available"))
    loaded = health_result.get("loaded")

    # Health can establish startup/loading without generating inference. Avoid
    # probing the model list or issuing readiness while the managed server loads.
    if loaded is False:
        result = None
    else:
        result = _bounded_model_probe(endpoint, _PACKAGED_MODEL_ID, float(timeout))
        if readiness and result.available:
            result = probe_readiness(endpoint, _PACKAGED_MODEL_ID, float(timeout), result)

    managed_activity = "unknown"
    try:
        _, _, latest_activity, _ = _load_config()
        managed_activity = _diagnostic_activity_status(latest_activity)
    except RuntimeManagementError:
        pass
    busy = managed_activity == "busy"
    endpoint_available = bool(
        (result is not None and result.available)
        or (
            result is not None
            and result.error
            and result.error.code in {"MODEL_NOT_FOUND", "EMPTY_MODELS"}
        )
        or health_available
    )
    model_available = bool(result is not None and result.matched_model is not None)
    if busy or (result is not None and result.error and result.error.code == "READINESS_BUSY"):
        state = "busy"
    elif managed_activity == "unknown":
        state = "unknown"
    elif loaded is False:
        state = "loading"
    elif readiness and result is not None and result.readiness_passed:
        state = "ready"
    elif readiness and result is not None and (result.readiness_tested or result.error is not None):
        state = "unknown"
    elif endpoint_available and model_available and loaded is True:
        state = "available-unverified"
    elif not endpoint_available:
        state = "unavailable"
    else:
        state = "unknown"
    timeout_evidence = None
    if repo_root is not None and run_id is not None:
        timeout_evidence = read_timeout_evidence(repo_root, run_id, endpoint, _PACKAGED_MODEL_ID)
    if suspected_stall(
        evidence=timeout_evidence,
        loaded=loaded,
        model_available=model_available,
        managed_activity=managed_activity,
        readiness_tested=result.readiness_tested if result is not None else False,
        error_code=result.error.code if result is not None and result.error else None,
    ):
        state = "suspected-stalled"
    readiness_info = None
    if readiness:
        readiness_info = {
            "tested": result.readiness_tested if result is not None else False,
            "passed": result.readiness_passed if result is not None else False,
            "status": "ready"
            if result is not None and result.readiness_passed
            else "busy"
            if result is not None and result.error and result.error.code == "READINESS_BUSY"
            else "skipped-loading"
            if result is None
            else "uncertain",
            "error_code": result.error.code
            if result is not None and result.error
            else "LOADING"
            if result is None
            else None,
            "remote_request_cancellation": "unknown"
            if result is not None and result.error and result.error.code == "READINESS_TIMEOUT"
            else None,
        }
    return {
        "status": state,
        "endpoint": endpoint,
        "endpoint_available": endpoint_available,
        "model_available": model_available,
        "model": (result.matched_model or (result.models[0] if result.models else None))
        if result is not None
        else None,
        "model_profile": _PACKAGED_MODEL_ID,
        "health": "available" if health_available else "unknown",
        "loaded": loaded if isinstance(loaded, bool) else None,
        "health_error_code": health_result.get("error_code"),
        "managed_activity": managed_activity,
        "external_client_activity": "unsupported",
        "readiness": readiness_info,
        "timeout_evidence": timeout_evidence,
        "error_code": result.error.code if result is not None and result.error else None,
        "observations": [
            "Endpoint availability and inference readiness are separate observations.",
            "External-client activity telemetry is unsupported.",
            "A readiness timeout does not establish that the remote request was cancelled.",
            "Suspected stall is advisory; overload, client failures, and external activity remain possible.",
        ],
    }


def _diagnostic_activity_status(activity: dict[str, Any]) -> str:
    """Observe leases without mutating the runtime registry."""
    from hybrid_sdlc.job_manager import JobManager, JobStatus

    active = False
    for entry in activity.get("leases", []):
        if not isinstance(entry, dict):
            return "unknown"
        kind = entry.get("kind")
        if kind in {"sync", "submitting", "lifecycle_unknown"}:
            active = True
            continue
        if (
            kind != "job"
            or not isinstance(entry.get("repo_root"), str)
            or not isinstance(entry.get("job_id"), str)
        ):
            return "unknown"
        try:
            job = JobManager(Path(entry["repo_root"])).get(entry["job_id"])
        except Exception:
            return "unknown"
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            continue
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            active = True
        else:
            return "unknown"
    return "busy" if active else "idle"


def logs(*, tail: int = 100) -> dict[str, Any]:
    if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 10000:
        raise RuntimeManagementError("Log tail must be from 1 through 10000 lines")
    root, config, activity, compose = _load_config()
    context = _local_context(root, config.get("docker_context"))
    ids = _owned_containers(config, compose, context)
    if not ids:
        return {
            "status": "not-created",
            "logs": "",
            "lifecycle_uncertain": [
                lease for lease in activity["leases"] if lease.get("kind") == "lifecycle_unknown"
            ],
        }
    output = _docker(
        [
            *_compose_prefix(config, compose, context),
            "logs",
            "--no-color",
            "--tail",
            str(tail),
            "strata-server",
        ],
        root,
    )
    return {
        "status": "ok",
        "logs": output[-_MAX_OUTPUT:],
        "lifecycle_uncertain": [
            lease for lease in activity["leases"] if lease.get("kind") == "lifecycle_unknown"
        ],
    }


def start() -> dict[str, Any]:
    return _run_lifecycle("start")


def stop() -> dict[str, Any]:
    return _run_lifecycle("stop")


def restart() -> dict[str, Any]:
    return _run_lifecycle("restart")
