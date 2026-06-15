"""
Memory Governance — Handoff 格式合规审计（SQLite 版）。

从 handoffs.db 读取所有历史记录，
检查 record_protocol 字段的存在性、枚举值合法性、时间戳格式。

用法：
    python3 WorkflowBase/memory/self-maint/audit_format.py

输出：
    合规记录数 / 不合规记录数 + 不合规明细
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

# DB 路径固定为 WorkflowBase/memory/handoff/handoffs.db
DB_PATH = Path(__file__).resolve().parent.parent / "handoff" / "handoffs.db"

# 当前合法的 record_protocol 枚举值
VALID_SKILLS = {
    "huihua-handoff", "closeout-gate", "post-review-framework",
    "code-reality-review", "memory-update",
}
VALID_RECORD_TYPES = {
    "session_handoff", "closeout", "review_report", "memory_update",
}
VALID_BLOCKER_STATUS = {"none", "present", "not_checked"}
VALID_ARTIFACT_QUALITY = {"pass", "pass_with_format_issues", "not_checked"}


def audit_row(row_id: int, content: str) -> list[str]:
    """审计单条 handoff 记录。返回问题列表。"""
    issues = []
    lines = content.split("\n")

    # 1. 检查 record_protocol 区块
    rp_start = None
    rp_end = None
    for i, line in enumerate(lines):
        if line.strip() == "record_protocol:" and rp_start is None:
            rp_start = i
        elif rp_start is not None and rp_end is None:
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("#"):
                continue
            if stripped and not line.startswith(" ") and not line.startswith("-"):
                rp_end = i
                break
    if rp_end is None:
        rp_end = len(lines)

    if rp_start is None:
        issues.append("missing_record_protocol")
        return issues

    rp_lines = lines[rp_start + 1: rp_end]
    rp_text = "\n".join(rp_lines)

    # 2. skill_loaded
    skill_match = re.search(r"skill_loaded:\s*(\S+)", rp_text)
    if not skill_match:
        issues.append("missing_skill_loaded")
    elif skill_match.group(1) not in VALID_SKILLS:
        issues.append(f"invalid_skill:{skill_match.group(1)}")

    # 3. record_type
    type_match = re.search(r"record_type:\s*(\S+)", rp_text)
    if not type_match:
        issues.append("missing_record_type")
    elif type_match.group(1) not in VALID_RECORD_TYPES:
        issues.append(f"invalid_record_type:{type_match.group(1)}")

    # 4. blocker_status
    bs_match = re.search(r"blocker_status:\s*(\S+)", rp_text)
    if not bs_match:
        issues.append("missing_blocker_status")
    elif bs_match.group(1) not in VALID_BLOCKER_STATUS:
        issues.append(f"invalid_blocker:{bs_match.group(1)}")

    # 5. artifact_quality
    aq_match = re.search(r"artifact_quality:\s*(\S+)", rp_text)
    if not aq_match:
        issues.append("missing_artifact_quality")
    elif aq_match.group(1) not in VALID_ARTIFACT_QUALITY:
        issues.append(f"invalid_quality:{aq_match.group(1)}")

    # 6. closeout_eligible
    ce_match = re.search(r"closeout_eligible:\s*(\S+)", rp_text)
    if not ce_match:
        issues.append("missing_closeout_eligible")
    elif ce_match.group(1) not in {"true", "false"}:
        issues.append(f"invalid_closeout_eligible:{ce_match.group(1)}")

    # 7. 检查头行时间戳格式
    first_line = lines[0].strip() if lines else ""
    time_match = re.search(r"\d{4}-\d{2}-\d{2}T?\d{2}:\d{2}", first_line)
    if not time_match:
        issues.append("missing_timestamp_header")

    return issues


def main() -> int:
    if not DB_PATH.exists():
        print(f"❌ handoffs.db 不存在: {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, content, archived_at FROM handoffs ORDER BY id"
    ).fetchall()
    conn.close()

    if not rows:
        print("❌ handoffs.db 中没有记录")
        return 1

    print("Memory Governance Handoff SQLite 格式合规审计")
    print(f"  DB: {DB_PATH}")
    print(f"  记录数: {len(rows)}")
    print()

    all_issues: dict[int, list[str]] = {}
    for row_id, content, archived_at in rows:
        issues = audit_row(row_id, content)
        if issues:
            all_issues[row_id] = issues

    clean = len(rows) - len(all_issues)
    print(f"✅ 合规: {clean}/{len(rows)}")
    print(f"❌ 不合规: {len(all_issues)}/{len(rows)}")
    print()

    if all_issues:
        print("不合规明细:")
        for row_id, issues in sorted(all_issues.items()):
            print(f"  row {row_id}:")
            for issue in issues:
                print(f"    - {issue}")
        print()

    # 按问题类型统计
    type_count: dict[str, int] = {}
    for issues in all_issues.values():
        for issue in issues:
            base = issue.split(":")[0]
            type_count[base] = type_count.get(base, 0) + 1

    if type_count:
        print("问题类型分布:")
        for t, c in sorted(type_count.items(), key=lambda x: -x[1]):
            print(f"  {t}: {c}")

    return 0 if not all_issues else 1


if __name__ == "__main__":
    sys.exit(main())
