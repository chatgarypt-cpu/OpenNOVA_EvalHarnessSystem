"""
Memory Governance — Handoff 里程碑汇总（SQLite 版）。

从 handoffs.db 读取所有历史记录，提取 task_id 信息，
去 tasks/archived/ 查找对应 closeout 证据，
按分区生成里程碑摘要。

用法：
    python3 WorkflowBase/memory/self-maint/compress_handoffs.py

输出：
    WorkflowBase/memory/milestones/<milestone_id>/
      milestone_snapshot.md
      task_index.yaml

规则：
  - 不删除原始 handoff（SQLite 中永久保存）
  - 有 closeout 才产生里程碑
  - milestone 按任务分区，不按时间分区
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "WorkflowBase" / "memory" / "handoff" / "handoffs.db"
MILESTONES_DIR = PROJECT_ROOT / "WorkflowBase" / "memory" / "milestones"
ARCHIVED_TASKS_DIR = PROJECT_ROOT / "tasks" / "archived"


def extract_task_ids(content: str) -> list[str]:
    """从 handoff 内容中提取 task_id 引用。"""
    ids = set()
    for m in re.finditer(r"task_id[:：\s]+(\S+)", content):
        ids.add(m.group(1))
    for m in re.finditer(r"tasks/(?:active|archived)/[^/]+/([^/\s]+)", content):
        ids.add(m.group(1))
    return sorted(ids)


def has_closeout_evidence(task_id: str) -> bool:
    """检查 task_id 在 archived 目录下是否有 closeout 记录。"""
    if not ARCHIVED_TASKS_DIR.exists():
        return False
    for domain_dir in ARCHIVED_TASKS_DIR.iterdir():
        if not domain_dir.is_dir():
            continue
        task_dir = domain_dir / task_id
        if not task_dir.exists():
            continue
        task_status = task_dir / "task_status.yaml"
        if task_status.exists():
            content = task_status.read_text(encoding="utf-8")
            if "closeout" in content or "closed" in content or "status: closed" in content:
                return True
        summary = task_dir / "summary" / "summary.md"
        if summary.exists():
            content = summary.read_text(encoding="utf-8")
            if "closeout" in content or "closed" in content:
                return True
        result = task_dir / "runtime" / "result.json"
        if result.exists():
            try:
                data = json.loads(result.read_text(encoding="utf-8"))
                if data.get("runtime_state") in ("executor_completed",):
                    return True
            except Exception:
                pass
    return False


def collect_milestones() -> dict[str, list[dict]]:
    """从 SQLite 读取 handoff 记录，按任务分区。"""
    if not DB_PATH.exists():
        print(f"❌ handoffs.db 不存在: {DB_PATH}")
        return {}

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, content, archived_at, session_start, session_end FROM handoffs ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        print("handoffs.db 为空")
        return {}

    partitions: dict[str, list[dict]] = {}

    for row_id, content, archived_at, session_start, session_end in rows:
        task_ids = extract_task_ids(content)

        # 检查是否有 closeout 证据
        closeable_ids = [tid for tid in task_ids if has_closeout_evidence(tid)]

        if not closeable_ids:
            continue

        # 分区 key：取第一个可 closeout 的 task_id 前缀
        first_closeout = closeable_ids[0]
        partition = first_closeout.split("-")[0] if "-" in first_closeout else first_closeout

        record = {
            "row_id": row_id,
            "archived_at": archived_at,
            "session_start": session_start,
            "session_end": session_end,
            "task_ids": task_ids,
            "closeable_ids": closeable_ids,
        }

        if partition not in partitions:
            partitions[partition] = []
        partitions[partition].append(record)

    return partitions


def generate_milestone(partition: str, records: list[dict]) -> None:
    """为分区生成里程碑。"""
    milestone_id = f"{partition}-{datetime.now().strftime('%Y%m%d')}"
    milestone_dir = MILESTONES_DIR / milestone_id
    milestone_dir.mkdir(parents=True, exist_ok=True)

    # 提取时间范围
    dates = [r["archived_at"] or "" for r in records if r["archived_at"]]
    sessions = [r["session_start"] or "" for r in records if r["session_start"]]
    all_times = dates + sessions
    time_range = f"{min(all_times)[:16]} → {max(all_times)[:16]}" if all_times else "unknown"

    # 提取所有 task_id
    all_task_ids = sorted(set(
        tid for r in records for tid in r["task_ids"]
    ))
    closed_ids = sorted(set(
        tid for r in records for tid in r["closeable_ids"]
    ))

    # 写入 milestone_snapshot.md
    snapshot = f"""# Milestone — {milestone_id}

> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 数据来源：WorkflowBase/memory/handoff/handoffs.db（SQLite）

## 覆盖
- 分区: {partition}
- 时间范围: {time_range}
- 原始 handoff 记录数: {len(records)}

## 已 closeout 任务
"""
    for tid in closed_ids:
        snapshot += f"- {tid}\n"

    snapshot += """
## 提及的其他任务
"""
    for tid in all_task_ids:
        if tid not in closed_ids:
            snapshot += f"- {tid}（无 closeout 证据）\n"

    snapshot += """
## 原始 handoff SQLite row
"""
    for r in records:
        snapshot += f"- row {r['row_id']}: {r['session_start']} → {r['session_end']} (archived: {r['archived_at']})\n"

    (milestone_dir / "milestone_snapshot.md").write_text(snapshot, encoding="utf-8")

    # 写入 task_index.yaml
    index = {
        "milestone_id": milestone_id,
        "data_source": "SQLite",
        "generated_at": datetime.now().isoformat(),
        "handoff_record_count": len(records),
        "handoff_rows": [r["row_id"] for r in records],
        "tasks_closed": closed_ids,
        "tasks_mentioned": all_task_ids,
    }
    (milestone_dir / "task_index.yaml").write_text(
        yaml.dump(index, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(f"  ✅ 生成 milestone: {milestone_id}/")
    print(f"     - milestone_snapshot.md")
    print(f"     - task_index.yaml")
    print(f"     覆盖 {len(records)} 条 handoff 记录，{len(closed_ids)} 个已 closeout 任务")


def main() -> int:
    print("=" * 60)
    print("  Memory Governance — Handoff 里程碑汇总（SQLite）")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    partitions = collect_milestones()

    if not partitions:
        print("没有可汇总的分区（所有手写记录均无 closeout 证据）")
        print("或 handoffs.db 为空")
        return 0

    total = sum(len(r) for r in partitions.values())
    print(f"发现 {len(partitions)} 个可汇总分区（共 {total} 条记录）:")
    for partition, records in sorted(partitions.items()):
        print(f"  {partition}: {len(records)} 条记录")
    print()

    MILESTONES_DIR.mkdir(parents=True, exist_ok=True)

    for partition, records in sorted(partitions.items()):
        generate_milestone(partition, records)

    print()
    print(f"milestones 目录: {MILESTONES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
