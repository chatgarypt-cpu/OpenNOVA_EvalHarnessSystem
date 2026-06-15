#!/usr/bin/env python3
"""
Memory Governance — scope_recall SQLite 数据库健康审计。

扫描 ~/.hermes/scope-recall/memory.sqlite3（或搜索备选路径），
提供只读健康报告：条目数、类别分布、陈腐条目、更新/创建时戳。

用法：
    python3 WorkflowBase/memory/self-maint/audit_scope_recall.py

退出码：0=健康，1=问题（数据库缺失/错误/陈腐条目过多）
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WORKYB = Path(__file__).resolve().parent.parent.parent.parent
HERMES_HOME = Path.home() / ".hermes"

# 搜索优先级
CANDIDATE_PATHS: list[Path] = [
    HERMES_HOME / "scope-recall" / "memory.sqlite3",
    HERMES_HOME / "scope-recall" / "scope_recall.db",
    HERMES_HOME / "scope_recall" / "scope_recall.db",
    HERMES_HOME / "scope_recall" / "scope_recall.sqlite",
    HERMES_HOME / "data" / "scope_recall.db",
    HERMES_HOME / "data" / "memory.sqlite3",
]


def find_db() -> Path | None:
    """搜索 scope_recall SQLite 数据库。"""
    # 优先精确路径
    for path in CANDIDATE_PATHS:
        if path.exists():
            return path.resolve()

    # 兜底：扫 *.db / *.sqlite
    found: list[Path] = []
    for pattern in ("*.db", "*.sqlite", "*.sqlite3"):
        found.extend(HERMES_HOME.rglob(pattern))
    # 排除 state.db / state-snapshots
    found = [p for p in found if "state" not in p.name.lower() and "snapshot" not in p.name.lower()]
    # 优先含 memory 或 scope 的路径
    scored: list[tuple[int, Path]] = []
    for p in found:
        score = 0
        if "memory" in p.name.lower():
            score += 2
        if "scope" in str(p).lower():
            score += 1
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if scored:
        return scored[0][1].resolve()
    return None


def open_db(path: Path) -> sqlite3.Connection:
    """以只读模式打开 SQLite 数据库。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fmt_timestamp(ts_str: str | None) -> str:
    """格式化 ISO 时戳为可读形式。"""
    if not ts_str:
        return "<无>"
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ts_str


def age_days(ts_str: str | None) -> float | None:
    """返回时戳距今的天数。"""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        # 处理无时区的情况
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def collect_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """收集所有统计指标（只读）。"""
    stats: dict[str, Any] = {}

    # 1. 总条目
    stats["total_entries"] = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    # 2. 按 target（类别）分布
    rows = conn.execute(
        "SELECT target, COUNT(*) AS cnt FROM memories GROUP BY target ORDER BY cnt DESC"
    ).fetchall()
    stats["per_target"] = {str(r["target"]): int(r["cnt"]) for r in rows}

    # 3. 按 source 分布
    rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM memories GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    stats["per_source"] = {str(r["source"]): int(r["cnt"]) for r in rows}

    # 4. 时戳范围
    row = conn.execute(
        "SELECT MIN(created_at) AS first, MAX(created_at) AS last, "
        "MIN(updated_at) AS first_upd, MAX(updated_at) AS last_upd "
        "FROM memories"
    ).fetchone()
    stats["created_range"] = (str(row["first"]), str(row["last"]))
    stats["updated_range"] = (str(row["first_upd"]), str(row["last_upd"]))

    # 5. 创建超过 30 天的条目（陈腐，未经检查）
    row = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE "
        "created_at < date('now', '-30 days')"
    ).fetchone()
    stats["stale_created_30d"] = int(row[0])

    # 同：用 updated_at（更相关）
    row = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE "
        "updated_at < date('now', '-30 days')"
    ).fetchone()
    stats["stale_updated_30d"] = int(row[0])

    # 6. 创建超过 7 天且从未更新（或更新超过 7 天前）
    row = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE "
        "updated_at < date('now', '-7 days')"
    ).fetchone()
    stats["stale_updated_7d"] = int(row[0])

    # 7. 创建超过 7 天且从未更新（created_at == updated_at）
    row = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE "
        "created_at = updated_at AND "
        "created_at < date('now', '-7 days')"
    ).fetchone()
    stats["never_updated_7d"] = int(row[0])

    # 8. 最旧和最新条目
    row = conn.execute(
        "SELECT id, target, created_at FROM memories ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    stats["oldest_entry"] = dict(row) if row else None

    row = conn.execute(
        "SELECT id, target, created_at FROM memories ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    stats["newest_entry"] = dict(row) if row else None

    # 9. FTS 完整性
    try:
        fts_count = int(conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0])
        memory_count = stats["total_entries"]
        fts_missing = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories AS m "
                "LEFT JOIN memories_fts AS f ON f.memory_id = m.id "
                "WHERE f.memory_id IS NULL"
            ).fetchone()[0]
        )
        fts_stale = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories_fts AS f "
                "LEFT JOIN memories AS m ON m.id = f.memory_id "
                "WHERE m.id IS NULL"
            ).fetchone()[0]
        )
        stats["fts"] = {
            "memory_rows": memory_count,
            "fts_rows": fts_count,
            "missing_fts": fts_missing,
            "stale_fts": fts_stale,
            "healthy": fts_missing == 0 and fts_stale == 0 and fts_count == memory_count,
        }
    except sqlite3.OperationalError:
        stats["fts"] = {"error": "FTS 表不存在或结构不一致"}

    return stats


def determine_health(stats: dict[str, Any]) -> tuple[bool, list[str]]:
    """判断整体健康状态。返回 (healthy, issues_list)。"""
    issues: list[str] = []

    if stats["total_entries"] == 0:
        issues.append("数据库为空，无记忆条目")

    # 陈腐条目过多 -> warning
    total = stats["total_entries"]
    stale_ratio = stats["stale_updated_30d"] / max(total, 1)
    if stale_ratio > 0.5:
        issues.append(f"超过 50% 的条目 30 天未更新 ({stats['stale_updated_30d']}/{total})")

    # FTS 不健康
    fts = stats.get("fts", {})
    if fts.get("healthy") is False:
        missing = fts.get("missing_fts", 0)
        stale = fts.get("stale_fts", 0)
        parts = []
        if missing:
            parts.append(f"{missing} 条缺少 FTS 索引")
        if stale:
            parts.append(f"{stale} 条 FTS 孤立记录")
        issues.append(f"FTS 索引不一致：{'；'.join(parts)}")

    # 最小时辰戳检查：如果 all updated 超过 7 天前
    if total > 0 and stats.get("stale_updated_7d", 0) == total:
        issues.append("所有条目最后一次更新超过 7 天前")

    return len(issues) == 0, issues


def print_report(stats: dict[str, Any], db_path: Path, issues: list[str]) -> None:
    """打印格式化报告。"""
    width = 60
    print("=" * width)
    print("  Memory Governance — scope_recall 健康审计")
    print("=" * width)
    print(f"  数据库: {db_path}")
    print(f"  大小:   {db_path.stat().st_size / 1024:.1f} KB")
    print(f"  扫描于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # ─── 概览 ───
    print(f"{'─── 概览 ───':^{width}}")
    print(f"  总条目:          {stats['total_entries']}")
    if stats["newest_entry"]:
        print(f"  最新条目:         {fmt_timestamp(stats['newest_entry']['created_at'])}  [{stats['newest_entry']['target']}]")
    if stats["oldest_entry"]:
        print(f"  最旧条目:         {fmt_timestamp(stats['oldest_entry']['created_at'])}  [{stats['oldest_entry']['target']}]")
    print(f"  创建时间范围:     {fmt_timestamp(stats['created_range'][0])} → {fmt_timestamp(stats['created_range'][1])}")
    print(f"  更新时间范围:     {fmt_timestamp(stats['updated_range'][0])} → {fmt_timestamp(stats['updated_range'][1])}")
    print()

    # ─── 类别分布 ───
    print(f"{'─── 类别分布 (target) ───':^{width}}")
    if stats["per_target"]:
        max_name = max(len(k) for k in stats["per_target"])
        for target, cnt in sorted(stats["per_target"].items(), key=lambda x: -x[1]):
            bar = "█" * max(1, int(cnt / max(stats["per_target"].values()) * 20))
            print(f"  {target:<{max_name}}  {cnt:>4}  {bar}")
    else:
        print("  (无条目)")
    print()

    # ─── 来源分布 ───
    print(f"{'─── 来源分布 (source) ───':^{width}}")
    if stats["per_source"]:
        max_name = max(len(k) for k in stats["per_source"])
        for src, cnt in sorted(stats["per_source"].items(), key=lambda x: -x[1]):
            bar = "█" * max(1, int(cnt / max(stats["per_source"].values()) * 20))
            print(f"  {src:<{max_name}}  {cnt:>4}  {bar}")
    print()

    # ─── 陈腐分析 ───
    print(f"{'─── 陈腐分析 ───':^{width}}")
    total = stats["total_entries"]
    print(f"  30 天未更新:      {stats['stale_updated_30d']:>4}  ({stats['stale_updated_30d']/max(total,1)*100:.1f}%)")
    print(f"  7 天未更新:       {stats['stale_updated_7d']:>4}  ({stats['stale_updated_7d']/max(total,1)*100:.1f}%)")
    print(f"  创建 7 天未更新:  {stats['never_updated_7d']:>4}  ({stats['never_updated_7d']/max(total,1)*100:.1f}%)")
    print()

    # ─── FTS 完整性 ───
    fts = stats.get("fts", {})
    print(f"{'─── FTS 全文索引 ───':^{width}}")
    if "error" in fts:
        print(f"  ⚠ {fts['error']}")
    elif fts:
        status = "✓ 健康" if fts["healthy"] else "✗ 异常"
        print(f"  状态:             {status}")
        print(f"  memory 表行数:    {fts['memory_rows']}")
        print(f"  FTS 表行数:       {fts['fts_rows']}")
        if fts["missing_fts"]:
            print(f"  缺少 FTS:         {fts['missing_fts']}")
        if fts["stale_fts"]:
            print(f"  孤立 FTS:         {fts['stale_fts']}")
    print()

    # ─── 问题摘要 ───
    if issues:
        print(f"{'─── 发现问题 ───':^{width}}")
        for issue in issues:
            print(f"  ❌ {issue}")
        print()
    else:
        print(f"{'─── 一切健康 ✓ ───':^{width}}")
        print()


def main() -> int:
    # 1. 查找数据库
    db_path = find_db()
    if db_path is None:
        print("❌ 未找到 scope_recall SQLite 数据库。")
        print(f"   搜索路径: {HERMES_HOME}")
        print("   可能不存在或尚未初始化。")
        return 1

    # 2. 打开数据库
    try:
        conn = open_db(db_path)
    except sqlite3.Error as e:
        print(f"❌ 无法打开数据库: {db_path}")
        print(f"   错误: {e}")
        return 1

    # 3. 检查是否有 memories 表
    try:
        conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        print(f"❌ 数据库 {db_path} 中不存在 'memories' 表。")
        print("   这可能不是 scope_recall 数据库。")
        conn.close()
        return 1

    # 4. 收集统计
    try:
        stats = collect_stats(conn)
    except sqlite3.Error as e:
        print(f"❌ 查询数据库时出错: {e}")
        conn.close()
        return 1
    finally:
        conn.close()

    # 5. 健康判断
    healthy, issues = determine_health(stats)

    # 6. 输出报告
    print_report(stats, db_path, issues)

    # 7. 退出码
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
