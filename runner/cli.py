"""CLI for WorkflowBase relay runner (A-line)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Auto-add project root to sys.path
_proj = Path(__file__).resolve().parent.parent.parent
if str(_proj) not in sys.path:
    sys.path.insert(0, str(_proj))

from WorkflowBase.runner.relay_runner import run_task


def _kill_stale_executors(task_dir: str) -> None:
    """Kill any existing executor processes for the same task-dir.

    Prevents zombie accumulation from repeated dispatches.
    Sends SIGTERM first, then SIGKILL after 3s if still alive.
    The caller's own pid is excluded from the kill list.
    """
    import os
    import signal
    import subprocess
    import time

    task_dir_abs = str(Path(task_dir).resolve())
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return

    killers = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        pid_str, cmd = parts[1], parts[10]
        try:
            pid = int(pid_str)
        except (ValueError, IndexError):
            continue
        if pid == my_pid:
            continue
        if "hermes" in cmd.lower():
            continue
        if task_dir_abs in cmd and ("python" in cmd or "relay_runner" in cmd):
            killers.append(pid)

    if not killers:
        return

    for pid in killers:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    time.sleep(3)
    for pid in killers:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    print(f"[preflight] killed {len(killers)} stale executor(s) for {task_dir}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m WorkflowBase.runner.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="[A-line no-op] config is auto-derived from task dir")

    run_parser = subparsers.add_parser("run", help="run the configured executor")
    run_parser.add_argument("--task-dir", required=True, type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in ("init",):
        print("A-line: config auto-derived from task dir. Run 'run --task-dir <dir>' directly.")
        return 0

    if args.command == "run":
        _kill_stale_executors(str(args.task_dir))
        result = run_task(str(args.task_dir))
        print(f"=== Result ===")
        print(f"exit_code: {result.exit_code}")
        print(f"classification: {result.classification}")
        print(f"stdout: {result.stdout_path}")
        print(f"stderr: {result.stderr_path}")
        return result.exit_code

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
