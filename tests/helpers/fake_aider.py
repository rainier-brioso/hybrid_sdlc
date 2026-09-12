"""Deterministic fake Aider executable for bounded-loop testing.

Usage (as argv target):
    python fake_aider.py <scenario> [options]

Scenarios:
    edit-success    Write a solution file and exit 0.
    edit-no-change  Write a sentinel file only on first invocation (tracks state in .hybrid_sdlc/).
    edit-identical  Write the same file every time (tests LOOP_STUCK detection).
    edit-malformed  Emit non-parseable output to stdout (simulates bad model output).
    timeout         Sleep for the duration specified by --sleep-secs (default 300).
    spawn-child     Spawn a subprocess that outlives this process; stays alive until SIGTERM.
    fail            Exit with code 1 without editing.
    counter-edit    Write incrementing content to a file (tests retry detection).

All scenarios support --output-dir <path> to specify where files are written
(defaults to current working directory).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=[
            "edit-success",
            "edit-no-change",
            "edit-identical",
            "edit-malformed",
            "timeout",
            "spawn-child",
            "fail",
            "counter-edit",
        ],
    )
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--sleep-secs", type=float, default=300.0)
    return parser.parse_args(argv)


def _scenario_edit_success(output_dir: Path) -> None:
    output_dir.joinpath("solution.py").write_text(
        "def answer():\n    return 42\n",
        encoding="utf-8",
    )
    print("[fake-aider] Generated solution.py", file=sys.stdout)


def _scenario_edit_no_change(output_dir: Path) -> None:
    # Store state outside the worktree in .hybrid_sdlc/ so it does not
    # interfere with git diff / edit detection logic.
    state_dir = output_dir / ".hybrid_sdlc"
    state_dir.mkdir(parents=True, exist_ok=True)
    counter = state_dir / ".fake_aider_count"
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    if count == 0:
        output_dir.joinpath("changed.py").write_text(
            "x = 1\n",
            encoding="utf-8",
        )
    counter.write_text(str(count + 1), encoding="utf-8")


def _scenario_edit_identical(output_dir: Path) -> None:
    output_dir.joinpath("always.py").write_text(
        "# identical content\nVALUE = 999\n",
        encoding="utf-8",
    )
    print("[fake-aider] Applied diff: always.py", file=sys.stdout)


def _scenario_edit_malformed(output_dir: Path) -> None:
    # Do NOT write any files - only emit invalid model output to stdout
    print("this is not valid model output at all", file=sys.stdout)
    print("no diff markers, no file references", file=sys.stdout)


def _scenario_timeout(_output_dir: Path, sleep_secs: float) -> None:
    interrupted = False

    def handler(sig: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, handler)
    try:
        time.sleep(sleep_secs)
    except (KeyboardInterrupt, SystemExit):
        interrupted = True
    if not interrupted:
        print(f"[fake-aider] Finished sleep({sleep_secs})", file=sys.stdout)


def _scenario_spawn_child(output_dir: Path) -> None:
    interrupted = False

    def handler(sig: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, handler)
    grandchild_pid_file = output_dir / "grandchild.pid"
    child = sys.executable
    subprocess_argv = [
        child,
        "-c",
        f"import os, time; "
        f'open("{grandchild_pid_file}", "w").write(str(os.getpid())); '
        f"time.sleep(120)",
    ]
    subprocess.Popen(subprocess_argv, creationflags=getattr(os, "CREATE_NEW_PROCESS_GROUP", 0))
    print("[fake-aider] Spawned grandchild process", file=sys.stdout)
    # Stay alive until cancelled so descendant cleanup is actually testable.
    try:
        while not interrupted:
            time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit):
        interrupted = True


def _scenario_fail(_output_dir: Path) -> None:
    print("[fake-aider] Failed to edit", file=sys.stderr)
    sys.exit(1)


def _scenario_counter_edit(output_dir: Path) -> None:
    counter_path = output_dir / "counter.txt"
    n = int(counter_path.read_text(encoding="utf-8")) + 1 if counter_path.exists() else 1
    counter_path.write_text(str(n), encoding="utf-8")
    output_dir.joinpath(f"version_{n}.py").write_text(
        f"version = {n}\n",
        encoding="utf-8",
    )
    print(f"[fake-aider] Wrote counter={n}", file=sys.stdout)


SCENARIOS = {
    "edit-success": _scenario_edit_success,
    "edit-no-change": _scenario_edit_no_change,
    "edit-identical": _scenario_edit_identical,
    "edit-malformed": _scenario_edit_malformed,
    "timeout": _scenario_timeout,
    "spawn-child": _scenario_spawn_child,
    "fail": _scenario_fail,
    "counter-edit": _scenario_counter_edit,
}


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    scenario_fn = SCENARIOS[args.scenario]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.scenario == "timeout":
        scenario_fn(output_dir, args.sleep_secs)
    elif args.scenario == "spawn-child":
        scenario_fn(output_dir)
    else:
        scenario_fn(output_dir)


if __name__ == "__main__":
    main()
