#!/usr/bin/env python3
"""
Heartbeat Monitor — 监控 relay runner 的心跳信号，任务结束时自动播放通知音。

DESIGN (2026-06-02):
  relay runner 的 monitor loop 每轮更新 heartbeat.json（含 timestamp + runtime_state）。
  心跳监视器每 2 秒读取 heartbeat，检测三种终止信号：

  1. runtime_state 变为 terminal state → 立即播声音
  2. outputs/ 有文件但 heartbeat 仍说 running → 立即播声音（artifact detector 盲区）
  3. 心跳停 5 秒：先查 outputs/，有文件就播（完成），没文件就再等 5 秒 → 还没恢复才播（崩溃）

  两次确认避免短时抖动误报。心跳监视器没有杀进程的权限。只检测和通知。

配置：
  tools/tools_config.yaml → heartbeat_monitor 段
  tools/sounds/ → 自定义声音文件

Usage:
    python3 heartbeat_monitor.py tasks/active/<task-id>

与 dialog_watcher.py 的关系：
  - dialog_watcher：在任务运行中处理权限弹窗
  - heartbeat_monitor：在任务结束时发通知
  两者互补，可并行运行。
"""
import json
import sys
import time
from pathlib import Path

# 同级目录下的 helpers
_INFRA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_INFRA_DIR))
sys.path.insert(0, str(_INFRA_DIR / "sound"))
from sound_utils import load_config, resolve_sound, play_sound, get_sound  # noqa: E402

TERMINAL_STATES = {"executor_completed", "executor_failed", "hold", "timeout", "error", "session_lost"}


def _resolve_tmux_session(task_dir: Path) -> str | None:
    """Read tmux session ID from runtime/session.yaml."""
    session_yaml = task_dir / "runtime" / "session.yaml"
    try:
        raw = session_yaml.read_text(encoding="utf-8")
        for line in raw.split("\n"):
            if "tmux_session_id:" in line:
                sid = line.split(":", 1)[1].strip().strip("\"'")
                if sid:
                    return sid
    except Exception:
        return None
    return None


def _tmux_has_session(session: str) -> bool:
    """Check if tmux session exists."""
    import subprocess
    r = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True, timeout=5,
    )
    return r.returncode == 0
DEFAULT_CFG = {
    "sound": "Glass",
    "stale_threshold": 5,
    "stale_confirm": 10,
    "poll_interval": 2.0,
}


def read_heartbeat(task_dir: Path):
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


def _parse_ts(ts_str: str):  # -> float | None
    """解析 ISO timestamp，失败返回 None"""
    try:
        cleaned = ts_str.split(".")[0]
        return time.mktime(time.strptime(cleaned, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, IndexError, OSError):
        return None


def main():
    task_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    # 从脚本自身位置推断项目根（workyb/tools/ → workyb/）
    project_root = str(Path(__file__).resolve().parent.parent.parent)

    # 加载配置
    cfg = load_config(str(project_root))
    hb_cfg = cfg.get("heartbeat_monitor", DEFAULT_CFG)
    sound_name = hb_cfg.get("sound", DEFAULT_CFG["sound"])
    # 如果 sound 显式指定了，直接用；否则按 profile 选
    sound_path = (
        resolve_sound(sound_name, str(project_root))
        if sound_name
        else get_sound("heartbeat_sound", str(project_root), cfg)
    )
    stale_threshold = hb_cfg.get("stale_threshold", DEFAULT_CFG["stale_threshold"])
    stale_confirm = hb_cfg.get("stale_confirm", DEFAULT_CFG["stale_confirm"])
    poll_interval = hb_cfg.get("poll_interval", DEFAULT_CFG["poll_interval"])

    print(f"[heartbeat] monitoring: {task_dir}", flush=True)
    print(f"[heartbeat] sound: {sound_name} → {sound_path}", flush=True)

    # Resolve tmux session for session liveness check
    tmux_session = _resolve_tmux_session(task_dir)
    if tmux_session:
        print(f"[heartbeat] tmux session: {tmux_session}", flush=True)
    else:
        print(f"[heartbeat] no session.yaml found — session liveness check disabled", flush=True)

    last_state = None  # type: str | None
    last_ts = None  # type: float | None
    notified_states = set()  # type: set[str]
    notified_outputs = False
    notified_stale = False  # debounce: only play stale sound once
    stale_since = None  # type: float | None

    while True:
        try:
            # Session liveness check — exit when tmux session dies
            if tmux_session and not _tmux_has_session(tmux_session):
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ⏹️ tmux session '{tmux_session}' gone — exiting", flush=True)
                break

            hb = read_heartbeat(task_dir)
            now = time.time()

            # ── 读取当前心跳时间戳 ──────────────────────────────────────
            current_ts = None
            current_state = None
            if hb:
                current_state = hb.get("runtime_state", "")
                raw_ts = hb.get("updated_at") or hb.get("last_heartbeat_at")
                current_ts = _parse_ts(raw_ts) if raw_ts else None
            if current_ts:
                last_ts = current_ts
            if current_state:
                last_state = current_state

            # ── Signal 1: runtime_state 变成 terminal state ────────────
            if hb and current_state in TERMINAL_STATES and current_state not in notified_states:
                play_sound(sound_path)
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ✅ task reached terminal state: {current_state}", flush=True)
                notified_states.add(current_state)
                stale_since = None
                time.sleep(poll_interval)
                continue

            # ── Signal 3: outputs 在盘但 heartbeat 还说 running ────────
            if check_outputs_exist(task_dir) and not notified_outputs:
                if last_state and last_state not in TERMINAL_STATES:
                    play_sound(sound_path)
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] 🔍 outputs detected while runtime_state='{last_state}' — task likely completed (artifact detector blind spot)", flush=True)
                notified_outputs = True
                stale_since = None

            # ── Signal 2: 心跳超时（两次确认） ──────────────────────────
            if last_ts and hb:
                age = now - last_ts

                if age <= stale_threshold:
                    stale_since = None
                    notified_stale = False  # fresh heartbeat, reset stale debounce
                elif stale_since is None:
                    # 第一级：刚过阈值，查 outputs/
                    stale_since = now
                    outputs_found = check_outputs_exist(task_dir)
                    if outputs_found:
                        if not notified_stale:
                            play_sound(sound_path)
                            notified_stale = True
                            ts = time.strftime("%H:%M:%S")
                            print(f"[{ts}] ⏹️ heartbeat stale ({stale_threshold}s) + outputs found → task completed", flush=True)
                        stale_since = now
                else:
                    # 第二级：二次确认
                    confirm_age = now - stale_since
                    if confirm_age >= (stale_confirm - stale_threshold):
                        if not notified_stale:
                            play_sound(sound_path)
                            notified_stale = True
                            ts = time.strftime("%H:%M:%S")
                            print(f"[{ts}] ⚠️ heartbeat stale ({stale_confirm}s, double-checked) + no outputs → task may have crashed", flush=True)
                        stale_since = now
            else:
                stale_since = None

            time.sleep(poll_interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[heartbeat] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
