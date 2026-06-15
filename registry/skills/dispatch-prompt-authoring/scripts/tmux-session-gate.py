#!/usr/bin/env python3
"""
Tmux Session Gate — 检查并清理与 active 任务不对应的 tmux session。

规则：只有 tasks/active/ 下存在的任务对应的 tmux session 可以存活。
其他 session（已归档任务、无关联 session）标记为 orphan 并建议杀掉。

用法：
    python3 ~/.hermes/scripts/tmux-session-gate.py         # 只检查不杀
    python3 ~/.hermes/scripts/tmux-session-gate.py --apply  # 检查 + 杀 orphan

返回值：
    0 — 无 orphan session，或已清理完毕
    1 — 有 orphan session 且未 --apply
"""

import argparse
import os
import subprocess
import sys

ADARIAN_ROOT = os.path.expanduser("~/项目开发/AdarianMigration/adarian mvp")
ACTIVE_TASKS_DIR = os.path.join(ADARIAN_ROOT, "tasks", "active")


def get_tmux_sessions() -> list[dict]:
    """获取所有 tmux session，返回 [{id, name, created}]"""
    try:
        output = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}|#{session_created_string}"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    sessions = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 1)
        sessions.append({"name": parts[0], "created": parts[1] if len(parts) > 1 else ""})
    return sessions


def get_active_task_ids() -> set[str]:
    """从 tasks/active/ 获取当前活跃任务 ID"""
    if not os.path.isdir(ACTIVE_TASKS_DIR):
        return set()
    return {d for d in os.listdir(ACTIVE_TASKS_DIR)
            if os.path.isdir(os.path.join(ACTIVE_TASKS_DIR, d))
            and not d.startswith(".")}


def session_matches_active(session_name: str, active_ids: set[str]) -> bool:
    """判断 tmux session 是否对应某个活跃任务。session 命名格式: adarian_<task_id>"""
    name = session_name
    if name.startswith("workyb_"):  # 向后兼容旧 session 命名
        name = name[len("workyb_"):]
    # A 线 session 带有 adarian_ 前缀
    if name.startswith("adarian_"):
        name = name[len("adarian_"):]
    return name in active_ids


def kill_session(session_name: str) -> bool:
    """杀掉指定的 tmux session"""
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=True, timeout=5, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ 杀失败 {session_name}: {e.stderr.decode().strip()}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("  ❌ tmux 命令不可用", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Tmux Session Gate — 清理 orphan session")
    parser.add_argument("--apply", action="store_true", help="执行清理（杀掉 orphan session）")
    args = parser.parse_args()

    sessions = get_tmux_sessions()
    if not sessions:
        print("✅ 无 tmux session")
        return 0

    active_ids = get_active_task_ids()
    if not active_ids:
        print("⚠️  无活跃任务目录，所有 tmux session 都是 orphan")
        if args.apply:
            for s in sessions:
                kill_session(s["name"])
        return 0 if args.apply else 1

    orphans = [s for s in sessions if not session_matches_active(s["name"], active_ids)]
    actives = [s for s in sessions if session_matches_active(s["name"], active_ids)]

    if actives:
        print(f"✅ 活跃任务 session（{len(actives)}）：")
        for s in actives:
            print(f"   {s['name']}")

    if orphans:
        print(f"\n⚠️  Orphan session（{len(orphans)}）—— 对应任务已归档/不存在：")
        for s in orphans:
            print(f"   {s['name']}（创建于 {s['created']}）")

        if args.apply:
            print(f"\n🔪 正在清理 {len(orphans)} 个 orphan session...")
            killed = sum(1 for s in orphans if kill_session(s["name"]))
            print(f"✅ 已杀 {killed}/{len(orphans)}")
            return 0
        else:
            print(f"\n💡 运行 `python3 {__file__} --apply` 以清理")
            return 1
    else:
        print("✅ 全部 tmux session 对应活跃任务，无 orphan")
        return 0


if __name__ == "__main__":
    sys.exit(main())
