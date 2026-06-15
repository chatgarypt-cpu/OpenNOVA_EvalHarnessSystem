#!/usr/bin/env python3
"""
Heartbeat Monitor — 监控 relay runner 的心跳信号，任务结束时自动播放通知音。

DESIGN (2026-06-02):
  relay runner 的 monitor loop 每轮更新 heartbeat.json（含 timestamp + runtime_state）。
  心跳监视器读取 heartbeat，检测三种终止信号：

  1. runtime_state 变为 terminal state（hold / executor_completed / error / timeout / session_lost）
  2. heartbeat 超时未更新（>20s 无新 timestamp，说明进程挂了或退出）
  3. 心跳仍更新但 outputs/ 文件已在盘上（artifact detector 盲区补丁）

  任一信号触发 → 播放 Glass 提示音 + 打印通知到 stdout。

  心跳监视器没有杀进程的权限。只检测和通知。

Usage:
    python3 heartbeat_monitor.py <task_dir>

与 dialog_watcher.py 的关系：
  - dialog_watcher：在任务运行中处理权限弹窗
  - heartbeat_monitor：在任务结束时发通知
  两者互补，可并行运行。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TERMINAL_STATES = {"executor_completed", "hold", "timeout", "error", "session_lost"}
POLL_INTERVAL = 2.0
HEARTBEAT_STALE_SEC = 20
GLASS_SOUND = "/System/Library/Sounds/Glass.aiff"


def read_heartbeat(task_dir: Path):  # -> dict or None
    """读取 heartbeat.json，失败时返回 None"""
    path = task_dir / "runtime" / "heartbeat.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def check_outputs_exist(task_dir: Path) -> bool:
    """检查 outputs/ 下是否有非空文件"""
    outputs_dir = task_dir / "outputs"
    if not outputs_dir.is_dir():
        return False
    for f in outputs_dir.iterdir():
        if f.is_file() and f.stat().st_size > 0:
            return True
    return False


def play_sound() -> None:
    """播放 Glass 提示音"""
    try:
        subprocess.run(["afplay", GLASS_SOUND], timeout=3, capture_output=True)
    except Exception:
        pass


def main():
    task_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    print(f"[heartbeat] monitoring: {task_dir}", flush=True)

    last_state = None  # type: str | None
    last_ts = None  # type: float | None
    notified_states: set[str] = set()
    notified_outputs = False
    stale_reported = False

    while True:
        try:
            hb = read_heartbeat(task_dir)
            now = time.time()

            # ── Signal 1: runtime_state transition to terminal ─────────────
            if hb:
                state = hb.get("runtime_state", "")
                ts_str = hb.get("updated_at", "")
                if state in TERMINAL_STATES and state not in notified_states:
                    play_sound()
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] task reached terminal state: {state}", flush=True)
                    notified_states.add(state)
                last_state = state

            # ── Signal 2: heartbeat stale (>20s no update) ─────────────────
            if hb and "updated_at" in hb:
                try:
                    ts_parsed = time.mktime(
                        time.strptime(hb["updated_at"].split(".")[0], "%Y-%m-%dT%H:%M:%S")
                    )
                    last_ts = ts_parsed
                except (ValueError, IndexError):
                    ts_parsed = None

            if last_ts and (now - last_ts) > HEARTBEAT_STALE_SEC and not stale_reported:
                play_sound()
                ts = time.strftime("%H:%M:%S")
                outputs_found = check_outputs_exist(task_dir)
                if outputs_found:
                    print(f"[{ts}] heartbeat stale ({HEARTBEAT_STALE_SEC}s) + outputs found -> task likely completed", flush=True)
                else:
                    print(f"[{ts}] heartbeat stale ({HEARTBEAT_STALE_SEC}s) + no outputs -> task may have crashed", flush=True)
                stale_reported = True

            # ── Signal 3: outputs exist but heartbeat never says done ──────
            outputs_found = check_outputs_exist(task_dir)
            if outputs_found and not notified_outputs:
                if last_state and last_state not in TERMINAL_STATES:
                    play_sound()
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] outputs detected while runtime_state='{last_state}' - task likely completed (artifact detector blind spot)", flush=True)
                notified_outputs = True

            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[heartbeat] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
