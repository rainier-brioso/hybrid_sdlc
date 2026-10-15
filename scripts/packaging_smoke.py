"""Validate built distributions from isolated environments without source imports."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ASSETS = (
    "src/hybrid_sdlc/_runtime/strata/compose.yaml",
    "src/hybrid_sdlc/_runtime/strata/worker-defaults.json",
    "src/hybrid_sdlc/_runtime/strata/startup.py",
)
FORBIDDEN_PARTS = {
    ".hybrid_sdlc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "artifacts",
    "benchmarks",
    "build",
    "cache",
    "caches",
    "dist",
    "logs",
    "models",
    "tests",
    "legacy_specify",
    ".specify",
    "_specify",
}
FORBIDDEN_SUFFIXES = {
    ".bin",
    ".gguf",
    ".key",
    ".log",
    ".pem",
    ".pt",
    ".pth",
    ".safetensors",
}


def run(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=300,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {args!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def archive_members(archive: Path) -> set[str]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            return set(wheel.namelist())
    with tarfile.open(archive, "r:gz") as source:
        return {member.name for member in source.getmembers() if member.isfile()}


def runtime_asset_payloads(archive: Path) -> dict[str, bytes]:
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as source:
            return {
                name: source.read("hybrid_sdlc/_runtime/strata/" + PurePosixPath(name).name)
                for name in RUNTIME_ASSETS
            }
    with tarfile.open(archive, "r:gz") as source:
        pyprojects = [name for name in archive_members(archive) if name.endswith("/pyproject.toml")]
        if len(pyprojects) != 1:
            raise RuntimeError(f"{archive.name} must contain one root pyproject.toml")
        prefix = pyprojects[0].removesuffix("pyproject.toml")
        payloads: dict[str, bytes] = {}
        for name in RUNTIME_ASSETS:
            stream = source.extractfile(prefix + name)
            if stream is None:
                raise RuntimeError(f"{archive.name} is missing runtime asset {name}")
            payloads[name] = stream.read()
        return payloads


def check_runtime_asset_payloads(archive: Path, canonical: dict[str, bytes]) -> None:
    payloads = runtime_asset_payloads(archive)
    mismatched = [name for name in RUNTIME_ASSETS if payloads[name] != canonical[name]]
    if mismatched:
        raise RuntimeError(
            f"{archive.name} has runtime assets that differ from source: {mismatched}"
        )


def check_archive(archive: Path) -> None:
    raw_names = {PurePosixPath(name.replace("\\", "/")) for name in archive_members(archive)}
    if archive.suffix == ".whl":
        names = raw_names
    else:
        roots = {name.parts[0] for name in raw_names if name.name == "pyproject.toml"}
        if len(roots) != 1:
            raise RuntimeError(f"{archive.name} must contain one project root pyproject.toml")
        names = {PurePosixPath(*name.parts[1:]) for name in raw_names}
    strings = {name.as_posix() for name in names}
    if archive.suffix == ".whl":
        required = {"hybrid_sdlc/_aider_cache_bootstrap.py"}
        required |= {
            "hybrid_sdlc/_runtime/strata/" + PurePosixPath(name).name for name in RUNTIME_ASSETS
        }
    else:
        required = {
            "pyproject.toml",
            "README.md",
            "src/hybrid_sdlc/__init__.py",
            "src/hybrid_sdlc/_aider_cache_bootstrap.py",
        }
        required |= set(RUNTIME_ASSETS)
    if missing := required - strings:
        raise RuntimeError(f"{archive.name} is missing required members: {sorted(missing)}")

    forbidden: list[str] = []
    for name in names:
        filename = name.name.lower()
        if (
            set(name.parts) & FORBIDDEN_PARTS
            or filename == "spec_initializer.py"
            or name.suffix.lower() in FORBIDDEN_SUFFIXES
        ):
            forbidden.append(name.as_posix())
        elif filename in {"hybrid_sdlc.toml", ".env", ".coverage"}:
            forbidden.append(name.as_posix())
        elif filename.startswith("hybrid_sdlc.") and filename.endswith(".local.toml"):
            forbidden.append(name.as_posix())
        elif filename.startswith(".env.") and filename != ".env.example":
            forbidden.append(name.as_posix())
        elif filename.endswith(".pyc") or filename.startswith(".coverage"):
            forbidden.append(name.as_posix())
    if forbidden:
        raise RuntimeError(f"{archive.name} contains local/development files: {forbidden}")


def extract_sdist(archive: Path, destination: Path) -> Path:
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"Unsafe source archive path: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"Unreadable source archive file: {member.name}")
                target.write_bytes(stream.read())
            else:
                raise RuntimeError(f"Unsupported source archive member: {member.name}")
    projects = [path for path in destination.iterdir() if path.is_dir()]
    if len(projects) != 1 or not (projects[0] / "pyproject.toml").is_file():
        raise RuntimeError("sdist must contain one rebuildable project directory")
    return projects[0]


def clean_env(cache: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        upper = key.upper()
        if upper.startswith("PYTHON") or upper in {
            "VIRTUAL_ENV",
            "UV_PROJECT",
            "UV_PROJECT_ENVIRONMENT",
            "UV_WORKING_DIR",
        }:
            env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["UV_CACHE_DIR"] = str(cache)
    env.pop("UV_OFFLINE", None)
    return env


def smoke_install(
    artifact: Path,
    label: str,
    uv: str,
    python_version: str,
    root: Path,
    env: dict[str, str],
) -> None:
    venv = root / f"venv-{label}"
    run([uv, "venv", "--python", python_version, str(venv)], cwd=root, env=env)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    console = scripts / ("hybrid-sdlc.exe" if os.name == "nt" else "hybrid-sdlc")
    if not python.is_file():
        raise RuntimeError(f"Missing isolated interpreter: {python}")
    run([uv, "pip", "install", "--python", str(python), str(artifact)], cwd=root, env=env)
    if not console.is_file():
        raise RuntimeError(f"Missing installed CLI: {console}")

    command_env = env.copy()
    path = [str(scripts)]
    if os.name == "nt":
        path.extend([os.environ.get("SYSTEMROOT", "C:\\Windows")])
    else:
        path.extend(["/usr/bin", "/bin"])
    command_env["PATH"] = os.pathsep.join(path)
    check_origin = (
        "import pathlib,sys,hybrid_sdlc,hybrid_sdlc.mcp_server,hybrid_sdlc.probe_worker,hybrid_sdlc.timeout_diagnostics,hybrid_sdlc.review; "
        "v=pathlib.Path(sys.argv[1]).resolve(); "
        "modules=(hybrid_sdlc,hybrid_sdlc.mcp_server,hybrid_sdlc.probe_worker,hybrid_sdlc.timeout_diagnostics,hybrid_sdlc.review); "
        "paths=[pathlib.Path(m.__file__).resolve() for m in modules]; "
        "assert all(p.is_relative_to(v) for p in paths), paths; print(paths)"
    )
    print(
        run([str(python), "-I", "-c", check_origin, str(venv)], cwd=root, env=command_env).strip()
    )
    run([str(console), "--help"], cwd=root, env=command_env)
    run([str(python), "-I", "-m", "hybrid_sdlc", "--help"], cwd=root, env=command_env)
    command_check = (
        "from click.testing import CliRunner; from hybrid_sdlc.cli import cli; "
        "runner=CliRunner(); result=runner.invoke(cli,['--help']); "
        "assert result.exit_code==0,(result.output,result.exception); "
        "expected=('check','run-task','submit','status','cancel','clean','mcp','setup','runtime','review'); "
        "assert all(name in result.output for name in expected),result.output; "
        "assert 'init' not in result.output,result.output; "
        "runtime=runner.invoke(cli,['runtime','--help']); "
        "assert runtime.exit_code==0,(runtime.output,runtime.exception); "
        "assert 'strata' in runtime.output,runtime.output; "
        "review=runner.invoke(cli,['review','--help']); "
        "assert review.exit_code==0,(review.output,review.exception); "
        "assert all(name in review.output for name in ('record','status')),review.output; "
        "record=runner.invoke(cli,['review','record','--help']); "
        "assert record.exit_code==0,(record.output,record.exception); "
        "assert '--expected-patch-sha256' in record.output,record.output; print(result.output)"
    )
    run([str(python), "-I", "-c", command_check], cwd=root, env=command_env)
    print(f"PASS {label}: installed CLI imports and command help")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--uv", default=shutil.which("uv"))
    parser.add_argument("--python", default="3.12", dest="python_version")
    args = parser.parse_args()
    if not args.uv:
        parser.error("uv not found; pass --uv with its executable path")
    dist = args.dist_dir.resolve()
    wheels, sdists = sorted(dist.glob("*.whl")), sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        parser.error("dist-dir must contain exactly one wheel and one .tar.gz sdist")
    wheel, sdist = wheels[0], sdists[0]
    check_archive(wheel)
    check_archive(sdist)
    runtime_assets = {name: (ROOT / name).read_bytes() for name in RUNTIME_ASSETS}
    check_runtime_asset_payloads(wheel, runtime_assets)
    check_runtime_asset_payloads(sdist, runtime_assets)

    with tempfile.TemporaryDirectory(prefix="hybrid-sdlc-packaging-") as temporary:
        root = Path(temporary)
        env = clean_env(root / "uv-cache")
        smoke_install(
            wheel,
            "wheel",
            args.uv,
            args.python_version,
            root,
            env,
        )
        smoke_install(
            sdist,
            "sdist",
            args.uv,
            args.python_version,
            root,
            env,
        )
        extracted = root / "sdist-source"
        extracted.mkdir()
        source = extract_sdist(sdist, extracted)
        rebuilt = root / "rebuilt"
        rebuilt.mkdir()
        run(
            [args.uv, "build", "--wheel", "--out-dir", str(rebuilt), str(source)], cwd=root, env=env
        )
        rebuilt_wheels = sorted(rebuilt.glob("*.whl"))
        if len(rebuilt_wheels) != 1:
            raise RuntimeError("sdist rebuild did not produce exactly one wheel")
        check_archive(rebuilt_wheels[0])
        check_runtime_asset_payloads(rebuilt_wheels[0], runtime_assets)
        smoke_install(
            rebuilt_wheels[0],
            "sdist-rebuilt",
            args.uv,
            args.python_version,
            root,
            env,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
