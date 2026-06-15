#!/usr/bin/env python3.14
"""
State Machine Logger — watches progress.yaml for state transitions
and outputs JSONL state machine log to stdout.

Usage:
    python3 state_watcher.py tasks/active/<task-id>/

Output (JSONL per state change):
    {"event":"state_change","from":"waiting_for_input","to":"remote_mode_activated","ts":"..."}
    {"event":"prompt_sent","state":"prompt_sent","ts":"..."}
    {"event":"terminal","state":"executor_completed","classification":"agent_completed","exit_code":0,"ts":"..."}
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: state_watcher.py <task-dir>", file=sys.stderr)
        return 1

    task_dir = Path(sys.argv[1])
    progress_path = task_dir / "runtime" / "progress.yaml"
    result_path = task_dir / "runtime" / "result.json"
    last_state = ""

    print(json.dumps({"event": "watcher_start", "task_dir": str(task_dir), "ts": now_iso()}), flush=True)

    while True:
        try:
            # Check result.json first (terminal state)
            if result_path.exists():
                try:
                    raw = result_path.read_text(encoding="utf-8")
                    result = json.loads(raw)
                    state = result.get("runtime_state", "unknown")
                    classification = result.get("classification", "")
                    exit_code = result.get("exit_code", -1)
                    log = {
                        "event": "terminal",
                        "state": state,
                        "classification": classification,
                        "exit_code": exit_code,
                        "ts": now_iso(),
                    }
                    print(json.dumps(log), flush=True)
                    return 0
                except (json.JSONDecodeError, OSError):
                    pass

            # Read progress.yaml
            if progress_path.exists():
                try:
                    raw = progress_path.read_text(encoding="utf-8")
                    for line in raw.splitlines():
                        if line.startswith("runtime_state:"):
                            current_state = line.split(":", 1)[1].strip()
                            if current_state != last_state:
                                log = {
                                    "event": "state_change",
                                    "from": last_state,
                                    "to": current_state,
                                    "ts": now_iso(),
                                }
                                print(json.dumps(log), flush=True)
                                last_state = current_state
                except OSError:
                    pass

            time.sleep(1)
        except KeyboardInterrupt:
            print(json.dumps({"event": "watcher_stop", "ts": now_iso()}), flush=True)
            return 0
        except Exception as e:
            print(json.dumps({"event": "watcher_error", "error": str(e), "ts": now_iso()}), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
