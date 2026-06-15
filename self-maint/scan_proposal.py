#!/usr/bin/env python3
"""
Scan Proposal — 读取 drift_check 输出，生成修复方案 YAML。

用法:
    python3 drift_check.py --quiet | python3 scan_proposal.py
    python3 drift_check.py --quiet -o drift.yaml && python3 scan_proposal.py < drift.yaml
    python3 scan_proposal.py -i drift_report.yaml
    python3 scan_proposal.py -o proposal.yaml   # 输出到文件（默认 stdout）

约束: 仅依赖 Python 标准库。不自启动、不自动 apply、不自动改文件。
"""

import argparse
import datetime
import sys
import os

# Use PyYAML like drift_check.py
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.datetime.now(
        tz=datetime.timezone(datetime.timedelta(hours=8))
    ).isoformat(timespec="seconds")


def load_drift_report(source):
    """Load drift report from file object or path, return items list."""
    data = yaml.safe_load(source)
    if data is None:
        return [], {}
    report = data.get("drift_report", data)
    items = report.get("items", [])
    meta = {
        "generated_at": report.get("generated_at", "unknown"),
        "total_entries": report.get("total_entries", 0),
        "drifted": report.get("drifted", len(items)),
        "checks_performed": report.get("checks_performed", []),
    }
    return items, meta


# ---------------------------------------------------------------------------
# Proposal generation logic
# ---------------------------------------------------------------------------

# Map (check_type, field) → change_type
CHANGE_TYPE_MAP = {
    "schema_compliance": {
        "__default__": "field_value_change",
        "file": "file_structure_fix",
    },
    "path_existence": {
        "__default__": "field_value_change",
    },
    "cross_reference": {
        "__default__": "field_value_change",
    },
    "readme_consistency": {
        "__default__": "file_structure_fix",
        "file": "file_structure_fix",
    },
    "env_check": {
        "__default__": "field_value_change",
    },
    "security_annotation": {
        "__default__": "field_value_change",
    },
    "git_status": {
        "__default__": "file_structure_fix",
    },
    "mcp_endpoint_ping": {
        "__default__": "field_value_change",
    },
}


def determine_change_type(item):
    """Determine change_type from check_type and field."""
    ct = item.get("check_type", "")
    field = item.get("field", "")
    ct_map = CHANGE_TYPE_MAP.get(ct, {"__default__": "field_value_change"})
    return ct_map.get(field, ct_map.get("__default__", "field_value_change"))


def determine_risk_level(item):
    """Determine risk level for the proposal based on drift severity."""
    sev = item.get("severity", "info")
    if sev == "critical":
        return "medium"
    if sev == "warning":
        return "low"
    return "low"


def generate_reason(item):
    """Generate a reason string referencing the drift finding."""
    ct = item.get("check_type", "")
    note = item.get("note", "")
    expected = item.get("expected", "")
    actual = item.get("actual", "")

    reason_map = {
        "schema_compliance": "registry_schema.md 要求该字段存在且值合法。",
        "path_existence": "registry 声明的路径应指向磁盘上实际存在的文件或目录。",
        "cross_reference": "跨文件引用必须指向实际存在的条目，否则运行时解析会失败。",
        "readme_consistency": "README.md 应与实际 registry 文件保持一致，确保文档可追溯。",
        "env_check": "env_required 声明的环境变量必须在运行环境中可用。",
        "security_annotation": "安全标注应完整，确保权限边界清晰。",
        "git_status": "registry 目录存在未提交变更，应确认是否为预期修改。",
        "mcp_endpoint_ping": "MCP 服务端点不可达，需确认服务状态。",
    }

    base = reason_map.get(ct, "检测到漂移，建议修复以保持一致性。")
    if note:
        return f"{base} {note}"
    return base


def generate_proposed_change(item):
    """Generate proposed_change object based on drift item."""
    ct = item.get("check_type", "")
    field = item.get("field", "")
    expected = item.get("expected", "")
    actual = item.get("actual", "")

    # For readme_consistency / file_structure_fix, use description form
    if ct in ("readme_consistency", "git_status"):
        if field == "file":
            return {
                "description": f"修复 {item.get('registry_file', '')} 文件结构或内容",
            }
        return {
            "description": f"将 {field} 从 '{actual}' 修改为 '{expected}'",
        }

    # For schema_compliance missing fields
    if ct == "schema_compliance" and actual in ("missing", "missing or empty"):
        return {
            "description": f"为条目 {item.get('entry_id', '')} 添加缺失字段 {field}",
        }

    # For cross_reference missing entries
    if ct == "cross_reference" and actual == "not found":
        return {
            "description": f"修复引用字段 {field}：{expected}",
        }

    # Default: from → to
    return {
        "from": actual,
        "to": expected,
    }


def generate_apply_method(item):
    """Generate human-readable apply instructions."""
    entry_id = item.get("entry_id", "")
    reg_file = item.get("registry_file", "")
    field = item.get("field", "")
    ct = item.get("check_type", "")

    if ct == "readme_consistency":
        return f"在 {reg_file} 中更新 {field} 相关内容以匹配实际条目数。"
    if ct == "schema_compliance":
        if item.get("actual") in ("missing", "missing or empty"):
            return f"在 {reg_file} 中找到条目 {entry_id}，添加 {field} 字段并填入合法值。"
        return f"在 {reg_file} 中找到条目 {entry_id}，将 {field} 的值修改为合法枚举值。"
    if ct == "path_existence":
        return f"在 {reg_file} 中找到条目 {entry_id}，更新 {field} 指向实际存在的路径。"
    if ct == "cross_reference":
        return f"在 {reg_file} 中找到条目 {entry_id}，修复 {field} 引用使其指向已注册的条目。"
    if ct == "env_check":
        return f"确保环境变量已设置：在 shell 配置中 export 对应变量，或在条目 {entry_id} 中移除不必要的 env_required。"
    if ct == "security_annotation":
        return f"在 {reg_file} 中找到条目 {entry_id}，补充 {field} 安全标注。"
    if ct == "git_status":
        return f"检查 {reg_file} 的未提交变更，决定是否 commit 或 discard。"
    return f"在 {reg_file} 中找到条目 {entry_id}，修复 {field} 字段。"


def generate_proposals(items):
    """Convert drift items into scan proposals."""
    proposals = []
    for idx, item in enumerate(items, 1):
        proposal = {
            "proposal_id": f"prop-{idx:03d}",
            "target_file": item.get("registry_file", "unknown"),
            "target_entry": item.get("entry_id", "unknown"),
            "target_field": item.get("field", "unknown"),
            "change_type": determine_change_type(item),
            "proposed_change": generate_proposed_change(item),
            "reason": generate_reason(item),
            "risk_assessment": {
                "level": determine_risk_level(item),
                "impact": "元数据修改，不影响运行时行为"
                           if item.get("severity") != "critical"
                           else "阻塞性问题，可能影响条目可用性",
                "reversible": True,
            },
            "apply_method": generate_apply_method(item),
            "owner_decision": None,
        }
        proposals.append(proposal)
    return proposals


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scan Proposal — 从 drift report 生成修复方案"
    )
    parser.add_argument("-i", "--input", type=str, default=None,
                        help="Drift report YAML 文件路径（默认从 stdin 读取）")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="输出 proposal YAML 路径（默认 stdout）")
    parser.add_argument("--quiet", action="store_true",
                        help="抑制 stderr 摘要输出")
    args = parser.parse_args()

    # Read input
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            items, meta = load_drift_report(f)
    else:
        if sys.stdin.isatty():
            print("请通过管道传入 drift report，或使用 -i 指定文件。", file=sys.stderr)
            print("用法: python3 drift_check.py --quiet | python3 scan_proposal.py",
                  file=sys.stderr)
            sys.exit(1)
        items, meta = load_drift_report(sys.stdin)

    # Generate proposals
    proposals = generate_proposals(items)

    # Build output
    proposal_doc = {
        "scan_proposal": {
            "generated_at": now_iso(),
            "based_on": meta.get("generated_at", "unknown"),
            "total_proposals": len(proposals),
            "proposals": proposals,
        }
    }

    yaml_output = yaml.dump(proposal_doc, default_flow_style=False,
                            allow_unicode=True, sort_keys=False, width=120)

    # Write output
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_output)
        if not args.quiet:
            print(f"Proposal written to: {args.output}", file=sys.stderr)
    else:
        print(yaml_output)

    # Summary
    if not args.quiet:
        print("=" * 60, file=sys.stderr)
        print("  SCAN PROPOSAL SUMMARY", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  Generated:     {proposal_doc['scan_proposal']['generated_at']}",
              file=sys.stderr)
        print(f"  Based on:      {meta.get('generated_at', 'unknown')}",
              file=sys.stderr)
        print(f"  Total items:   {len(items)}", file=sys.stderr)
        print(f"  Proposals:     {len(proposals)}", file=sys.stderr)

        if proposals:
            sev_counts = {}
            for p in proposals:
                rl = p["risk_assessment"]["level"]
                sev_counts[rl] = sev_counts.get(rl, 0) + 1
            print(f"  Risk levels:   {sev_counts}", file=sys.stderr)

            print("\n  Proposals:", file=sys.stderr)
            for p in proposals:
                print(f"    {p['proposal_id']}: [{p['target_file']}] "
                      f"{p['target_entry']}.{p['target_field']} "
                      f"({p['change_type']}, risk={p['risk_assessment']['level']})",
                      file=sys.stderr)
        else:
            print("  ✅ 无需修复提案。", file=sys.stderr)

        print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
