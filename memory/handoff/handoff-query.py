#!/usr/bin/env python3
"""
Handoff SQLite 查询工具。

用法：
    # 总览所有记录
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --list

    # 查看单条
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --id 1

    # 按 task_id 搜索（从内容中提取）
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --task v1.3.1

    # 按时间范围
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --from '2026-06-06' --to '2026-06-07'

    # 正则匹配内容
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --regex 'blocker_status:\\s*present'

    # 查看匹配记录的完整内容
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --id 1 --show

    # 组合过滤
    .venv/bin/python WorkflowBase/memory/handoff/handoff-query.py --task code-reality --show
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "handoffs.db"


def extract_task_ids(content: str) -> list[str]:
    """从 handoff 内容中提取 task_id 引用。"""
    ids: set[str] = set()
    # task_id: xxx 或 task_id：xxx
    for m in re.finditer(r"task_id[:：\s]+([a-zA-Z0-9][a-zA-Z0-9_.\-]+)", content):
        ids.add(m.group(1))
    # tasks/active/<domain>/<id> 或 tasks/archived/<domain>/<id>
    for m in re.finditer(r"tasks/(?:active|archived)/[a-zA-Z0-9_.\-]+/([a-zA-Z0-9][a-zA-Z0-9_.\-]+)", content):
        ids.add(m.group(1))
    return sorted(ids)


def build_query(args: argparse.Namespace) -> tuple[str, list]:
    """Build SQL query + params from CLI args. Returns (sql, params)."""
    conditions: list[str] = []
    params: list = []

    if args.id:
        conditions.append("id = ?")
        params.append(args.id)

    if args.task:
        # 用 content LIKE 配合 task_id 模式匹配（效率足够，数据量很小）
        conditions.append("content LIKE ?")
        params.append(f"%{args.task}%")

    if args.regex:
        # regex 过滤在 Python 层做，先全量查
        pass  # handled post-query

    where = ""
    if conditions:
        where = " WHERE " + " AND ".join(conditions)

    return where, params


def match_regex_in_rows(rows: list, pattern: str) -> list:
    """Filter rows where content matches regex."""
    try:
        compiled = re.compile(pattern, re.MULTILINE | re.DOTALL)
    except re.error as e:
        print(f"❌ 正则语法错误: {e}", file=sys.stderr)
        sys.exit(1)
    return [r for r in rows if compiled.search(r[1])]


def fmt_row_meta(row) -> str:
    """Format a single row as metadata line."""
    rid, content, archived_at, session_start, session_end = row
    task_ids = extract_task_ids(content)
    tasks_str = ", ".join(task_ids[:5])
    if len(task_ids) > 5:
        tasks_str += f" ... (+{len(task_ids) - 5})"
    meta = (
        f"  #{rid:<3} {session_start or '?':^20} → {session_end or '?':^20}"
        f"  [{len(content):>5} chars]"
        f"  tasks: {tasks_str}"
    )
    return meta


def cmd_list(rows: list, show: bool = False) -> None:
    """Show overview of all matching rows."""
    if not rows:
        print("没有匹配的记录。")
        return

    print(f"共 {len(rows)} 条记录:\n")
    for row in rows:
        print(fmt_row_meta(row))
    print()

    if show:
        for row in rows:
            print(f"{'=' * 60}")
            print(f"  row {row[0]} 完整内容:")
            print(f"{'=' * 60}")
            print(row[1])
            print()


def cmd_single(row, show: bool = False) -> None:
    """Show a single row in detail."""
    if not row:
        print("未找到该记录。")
        return

    rid, content, archived_at, session_start, session_end = row
    task_ids = extract_task_ids(content)

    print(f"  记录 #{rid}")
    print(f"  会话时间: {session_start} → {session_end}")
    print(f"  归档时间: {archived_at}")
    print(f"  内容大小: {len(content)} chars")
    print(f"  引用 task_ids: {', '.join(task_ids) if task_ids else '（无）'}")
    print()

    if show:
        print(f"{'=' * 60}")
        print(content)
        print(f"{'=' * 60}")
    else:
        # head 预览
        lines = content.split("\n")
        preview_lines = lines[:min(12, len(lines))]
        print("  预览 (前 12 行):")
        for l in preview_lines:
            print(f"    {l}")
        if len(lines) > 12:
            print(f"    ... (共 {len(lines)} 行，用 --show 查看完整)")


def fmt_timestamp(ts) -> str:
    """Format SQLite timestamp for display."""
    if ts and len(ts) >= 16:
        return ts[:16]
    return str(ts or "?")


def main():
    parser = argparse.ArgumentParser(description="handoffs.db 查询工具")
    parser.add_argument("--list", action="store_true", help="列出所有匹配记录（默认行为）")
    parser.add_argument("--id", type=int, help="按 row ID 查询单条")
    parser.add_argument("--task", help="按 task_id 关键词搜索")
    parser.add_argument("--from", dest="from_date", help="时间范围起始 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM)")
    parser.add_argument("--to", dest="to_date", help="时间范围结束")
    parser.add_argument("--regex", help="正则匹配 content")
    parser.add_argument("--show", action="store_true", help="显示记录的完整内容")

    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ handoffs.db 不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # --- 单条查询 ---
    if args.id:
        cursor.execute(
            "SELECT id, content, archived_at, session_start, session_end FROM handoffs WHERE id = ?",
            (args.id,),
        )
        row = cursor.fetchone()
        conn.close()
        cmd_single(row, show=args.show)
        return

    # --- 列表查询 ---
    conditions = []
    params = []

    if args.task:
        conditions.append("content LIKE ?")
        params.append(f"%{args.task}%")

    if args.from_date:
        conditions.append("(session_start >= ? OR (session_start IS NULL AND archived_at >= ?))")
        params.append(args.from_date)
        params.append(args.from_date)

    if args.to_date:
        conditions.append("(session_end <= ? OR (session_end IS NULL AND archived_at <= ?))")
        params.append(f"{args.to_date} 23:59")
        params.append(f"{args.to_date} 23:59")

    where = ""
    if conditions:
        where = " WHERE " + " AND ".join(conditions)

    cursor.execute(
        f"SELECT id, content, archived_at, session_start, session_end FROM handoffs {where} ORDER BY id",
        params,
    )
    rows = cursor.fetchall()
    conn.close()

    # --- 正则过滤（Python 层） ---
    if args.regex:
        rows = match_regex_in_rows(rows, args.regex)

    cmd_list(rows, show=args.show)


if __name__ == "__main__":
    main()
