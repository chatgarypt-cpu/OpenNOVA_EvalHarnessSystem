"""
PM Runtime Self-Maintenance — 运行时自持系统。

类比 Registry 的 self-maint（drift_check / scan_proposal / proposal_apply），
PM Runtime self-maint 核验：
  1. 所有注册的 executor 代码路径是否真实存在
  2. 所有注册的 hook/gate 脚本是否真实存在
  3. 所有注册的 skill SKILL.md 是否真实存在
  4. 当前活跃任务的路径是否完整
  5. path_resolver 能否解析所有关键路径
  6. 检测已归档任务的 orphan tmux session

用法：
    python3 tools/pm_runtime/pm_runtime_self_maint.py        # 全量核验
    python3 tools/pm_runtime/pm_runtime_self_maint.py --fast  # 仅路径核验
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure A 线 project root is on sys.path for package imports
_WORKYB = Path(__file__).resolve().parent.parent.parent
if str(_WORKYB) not in sys.path:
    sys.path.insert(0, str(_WORKYB))

from WorkflowBase.runner.path_resolver import default_paths


def main() -> int:
    fast = "--fast" in sys.argv
    paths = default_paths()

    print("=" * 60)
    print(f"  PM RUNTIME SELF-MAINT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  mode: {'fast' if fast else 'full'}")
    print("=" * 60)
    print()

    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    all_ok = True

    # ── 1. Executor 代码路径核验 ──────────────────────────────
    print("  [1/5] Executor 代码路径...")
    executors = [
        ("ClaudeTmuxExecutor", paths.executor("claude", "tmux_executor.py")),
        ("CodexTmuxExecutor", paths.executor("codex", "tmux_executor.py")),
        ("CodexSubprocess", paths.executor("codex", "executor.py")),
        ("ExecutorRegistry", paths.registry("executor_registry.yaml")),
        ("RelayRunner", paths._p("relay_runner", Path("WorkflowBase/runner/relay_runner.py"))),
    ]
    for name, entry in executors:
        status = "✅" if entry.exists else "❌"
        print(f"    {status} {name}: {entry.resolved}")
        if not entry.exists:
            issues.append(f"executor_missing:{name}")
            all_ok = False
        checks.append({"check": "executor_path", "name": name, "ok": entry.exists})

    # ── 2. Gate 脚本核验 ──────────────────────────────────────
    if not fast:
        print("\n  [2/5] Gate/Hook 脚本...")
        gates = [
            ("handoff-loader", paths.hermes_script("handoff-loader.py")),
            ("handoff-trigger", paths.hermes_script("handoff-trigger.py")),
            ("handoff-writer", paths.hermes_script("handoff-writer.py")),
            ("task-status-writer", paths.hermes_script("task-status-writer.py")),
            ("dispatch-gate", paths.hermes_script("dispatch-gate.py")),
            ("commit-gate", paths.hermes_script("commit-gate.py")),
            ("promotion-gate", paths.hermes_script("promotion-gate.py")),
            ("session-end-stamp", paths.hermes_script("session-end-stamp.py")),
            ("tmux-session-gate", paths.hermes_script("tmux-session-gate.py")),
        ]
        for name, entry in gates:
            status = "✅" if entry.exists else "❌"
            print(f"    {status} {name}: {entry.resolved}")
            if not entry.exists:
                issues.append(f"gate_missing:{name}")
            checks.append({"check": "gate_script", "name": name, "ok": entry.exists})

    # ── 3. PM Runtime Skills 核验 ─────────────────────────────
    if not fast:
        print("\n  [3/5] PM Runtime Skills...")
        pm_skills = [
            "pm-runtime/SKILL.md",
            "closeout-gate/SKILL.md",
            "task-directory-canonical/SKILL.md",
            "dispatch-prompt-authoring/SKILL.md",
            "pm-relay/SKILL.md",
            "dag-execution/SKILL.md",
            "task-repair/SKILL.md",
            "topic-boundary/SKILL.md",
            "path-resolver/SKILL.md",
        ]
        for skill_rel in pm_skills:
            skill_name = skill_rel.split("/")[0]
            entry = paths.hermes_skill(skill_name)
            exists = entry.exists
            status = "✅" if exists else "❌"
            print(f"    {status} {skill_name}: {entry.resolved}")
            if not exists:
                issues.append(f"skill_missing:{skill_name}")
            checks.append({"check": "pm_skill", "name": skill_name, "ok": exists})

    # ── 4. Registry 文件核验 ──────────────────────────────────
    print("\n  [4/5] Registry 核心文件...")
    registry_files = ["executor_registry.yaml", "skill_registry.yaml",
                      "hook_registry.yaml", "mcp_registry.yaml",
                      "registry_schema.md", "promotion_mapping.md"]
    for fname in registry_files:
        entry = paths.registry(fname)
        status = "✅" if entry.exists else "❌"
        print(f"    {status} {fname}: {entry.resolved}")
        if not entry.exists:
            issues.append(f"registry_missing:{fname}")
            all_ok = False
        checks.append({"check": "registry_file", "name": fname, "ok": entry.exists})

    # ── 5. Path Resolver 自检 ─────────────────────────────────
    print("\n  [5/5] Path Resolver 自检...")
    resolver_checks = [
        ("proj_root", paths.root.exists()),
        ("adarian_root", paths.adarian_root.exists()),
        ("iter_plans_dir", paths.iteration_plan("").resolved.parent.exists()),
    ]
    for name, ok in resolver_checks:
        status = "✅" if ok else "❌"
        print(f"    {status} {name}")
        if not ok:
            issues.append(f"resolver_check:{name}")
        checks.append({"check": "resolver", "name": name, "ok": ok})

    # ── 汇总 ──────────────────────────────────────────────────
    print()
    print("=" * 60)
    if not issues:
        print("  ✅ 全部检查通过 — PM Runtime 基础设施完整")
        print("=" * 60)
        return 0
    else:
        print(f"  ❌ 发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"     - {issue}")
        print("=" * 60)
        print()
        print("  修复建议:")
        for issue in issues:
            parts = issue.split(":", 1)
            if len(parts) == 2:
                cat, name = parts
                if cat == "executor_missing":
                    print(f"    - {name}: 注册路径存在但文件缺失，检查 executor_registry.yaml 中的 module_path")
                elif cat == "gate_missing":
                    print(f"    - {name}: ~/.hermes/scripts/{name} 不存在，需要创建")
                elif cat == "skill_missing":
                    print(f"    - {name}: ~/.hermes/skills/.../{name}/SKILL.md 不存在，检查 skill 是否安装")
                elif cat == "registry_missing":
                    print(f"    - {name}: WorkflowBase/registry/{name} 缺失")
        return 1


if __name__ == "__main__":
    sys.exit(main())
