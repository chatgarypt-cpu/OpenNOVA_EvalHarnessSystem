#!/usr/bin/env python3
"""
Proposal Apply — 预览 scan_proposal 中的变更 diff，拒绝 --apply。

用法:
    python3 proposal_apply.py proposal.yaml          # 预览所有 proposal
    python3 proposal_apply.py proposal.yaml --dry-run # 同上（显式 dry-run）
    python3 proposal_apply.py proposal.yaml --apply   # ❌ 被拒绝

约束: 仅依赖 Python 标准库。不自动 apply、不自动改文件。
      所有 proposal 仅展示 diff 预览，不执行任何写操作。
"""

import argparse
import sys
import os
import difflib

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_proposals(filepath):
    """Load scan_proposal.yaml, return list of proposals."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    proposal_doc = data.get("scan_proposal", data)
    return proposal_doc.get("proposals", [])


def load_registry_file(target_file):
    """Try to load the target registry file content as lines."""
    # Resolve relative to project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    proj_root = os.path.normpath(os.path.join(script_dir, "..", ".."))
    registry_dir = os.path.join(proj_root, "WorkflowBase", "registry")

    candidates = [
        os.path.join(registry_dir, target_file),
        os.path.join(proj_root, target_file),
    ]

    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.readlines(), path
    return None, None


def generate_diff_preview(proposal, lines, file_path):
    """Generate a unified diff preview for a proposal."""
    entry_id = proposal.get("target_entry", "")
    field = proposal.get("target_field", "")
    change = proposal.get("proposed_change", {})
    change_type = proposal.get("change_type", "")

    # Build a human-readable diff block
    diff_lines = []
    diff_lines.append(f"--- a/{file_path}")
    diff_lines.append(f"+++ b/{file_path}")

    if change_type == "field_value_change" and "from" in change:
        diff_lines.append(
            f"@@ {entry_id}.{field} @@"
        )
        diff_lines.append(f'- {field}: {change["from"]}')
        diff_lines.append(f'+ {field}: {change["to"]}')
    elif change_type == "field_add":
        diff_lines.append(f"@@ {entry_id} @@")
        desc = change.get("description", f"添加字段 {field}")
        diff_lines.append(f"+ # {desc}")
    elif change_type == "file_structure_fix":
        diff_lines.append(f"@@ {entry_id} @@")
        desc = change.get("description", f"修复 {field}")
        diff_lines.append(f"+ # {desc}")
    else:
        diff_lines.append(f"@@ {entry_id}.{field} @@")
        desc = change.get("description", str(change))
        diff_lines.append(f"+ # {desc}")

    return "\n".join(diff_lines)


def format_proposal_card(proposal, index, total):
    """Format a single proposal as a readable card."""
    lines = []
    pid = proposal.get("proposal_id", f"prop-{index:03d}")
    risk = proposal.get("risk_assessment", {})
    decision = proposal.get("owner_decision")
    dec_icon = {"approved": "✅", "rejected": "❌"}.get(decision, "⏳")

    lines.append(f"┌─ {pid} ({index}/{total}) ─────────────────────────────")
    lines.append(f"│ 目标: {proposal['target_file']} → {proposal['target_entry']}.{proposal['target_field']}")
    lines.append(f"│ 类型: {proposal.get('change_type', 'unknown')}")
    lines.append(f"│ 风险: {risk.get('level', '?')} — {risk.get('impact', '?')}")
    lines.append(f"│ 可逆: {'是' if risk.get('reversible') else '否'}")
    lines.append(f"│ 决策: {dec_icon} {decision or 'pending'}")
    lines.append(f"│")
    lines.append(f"│ 理由: {proposal.get('reason', 'N/A')}")
    lines.append(f"│")

    # Proposed change
    change = proposal.get("proposed_change", {})
    if "from" in change:
        lines.append(f"│ 变更: - {proposal['target_field']}: {change['from']}")
        lines.append(f"│       + {proposal['target_field']}: {change['to']}")
    elif "description" in change:
        lines.append(f"│ 变更: {change['description']}")
    else:
        lines.append(f"│ 变更: {change}")

    lines.append(f"│")
    lines.append(f"│ 应用: {proposal.get('apply_method', 'N/A')}")
    lines.append(f"└──────────────────────────────────────────")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Proposal Apply — 预览 scan_proposal 变更（不执行 apply）"
    )
    parser.add_argument("proposal_file", type=str,
                        help="scan_proposal.yaml 文件路径")
    parser.add_argument("--apply", action="store_true",
                        help="（被拒绝）尝试自动应用变更")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式（默认行为）")
    parser.add_argument("--prop", type=str, default=None,
                        help="只查看指定 proposal（如 prop-001）")
    args = parser.parse_args()

    # ---- Safety gate: refuse --apply ----
    if args.apply:
        print("=" * 60, file=sys.stderr)
        print("  ❌ --apply 已被拒绝", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("", file=sys.stderr)
        print("  安全策略：proposal_apply.py 不执行任何文件修改。", file=sys.stderr)
        print("", file=sys.stderr)
        print("  所有 proposal 仅展示 diff 预览。", file=sys.stderr)
        print("  实际修改必须由 Owner 手动执行，或在 Claude Code", file=sys.stderr)
        print("  会话中由 Owner 显式指令下编辑。", file=sys.stderr)
        print("", file=sys.stderr)
        print("  如需应用，请：", file=sys.stderr)
        print("    1. 审查下方 diff 预览", file=sys.stderr)
        print("    2. 手动编辑对应 registry 文件", file=sys.stderr)
        print("    3. 或在会话中说 '按 prop-NNN 执行'", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        sys.exit(1)

    # ---- Load proposals ----
    if not os.path.exists(args.proposal_file):
        print(f"错误: 文件不存在 — {args.proposal_file}", file=sys.stderr)
        sys.exit(1)

    proposals = load_proposals(args.proposal_file)
    if not proposals:
        print("无 proposal 可预览。", file=sys.stderr)
        sys.exit(0)

    # Filter if --prop specified
    if args.prop:
        proposals = [p for p in proposals if p.get("proposal_id") == args.prop]
        if not proposals:
            print(f"未找到 proposal: {args.prop}", file=sys.stderr)
            sys.exit(1)

    total = len(proposals)

    print("=" * 60)
    print("  PROPOSAL APPLY — DRY RUN 预览")
    print("=" * 60)
    print(f"  模式: 预览（不执行任何修改）")
    print(f"  Proposals: {total}")
    print("=" * 60)
    print()

    for idx, proposal in enumerate(proposals, 1):
        # Card
        print(format_proposal_card(proposal, idx, total))

        # Diff preview
        target = proposal.get("target_file", "")
        lines, resolved_path = load_registry_file(target)
        if lines:
            diff = generate_diff_preview(proposal, lines, resolved_path)
            print()
            print("  Diff 预览:")
            for dl in diff.split("\n"):
                print(f"  {dl}")
        else:
            print()
            print(f"  ⚠ 无法读取 {target}（文件不存在或不在预期路径），仅展示 proposal 内容。")

        print()

    # Summary
    print("=" * 60)
    print("  预览结束 — 未执行任何修改")
    print("=" * 60)
    print()
    print("  决策提示：")
    print("    [A] 全部采纳 → 手动编辑 registry 文件")
    print("    [B] 部分采纳 → 选择性执行")
    print("    [C] 全部忽略")
    print()
    print("  ⚠ 此工具不会自动修改任何文件。")
    print("=" * 60)


if __name__ == "__main__":
    main()
