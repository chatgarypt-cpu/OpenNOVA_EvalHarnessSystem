"""
Path Resolver — PM Runtime 路径防漂移模块。

功能：
  1. 从单一 project_root 基址派生所有路径
  2. 所有路径可校验存在性，缺失时报明确错误
  3. 迁移时只改 project_root 一个变量
  4. 同时支持 workyb（B 线）和 Adarian（A 线）两套项目路径

用法：
    resolver = ProjectPaths()                          # 自动检测 Adarian（A 线）
    resolver = ProjectPaths(root="/新路径/项目开发/AdarianMigration/adarian mvp")  # 手动指定

    resolver.task_dir("b-to-a-promotion-go1")
    # → /Users/gary/项目开发/AdarianMigration/adarian mvp/tasks/active/b-to-a-promotion-go1

    resolver.registry("executor_registry.yaml")
    # → /Users/gary/项目开发/AdarianMigration/adarian mvp/WorkflowBase/registry/executor_registry.yaml

    resolver.adarian("docs/skills/workflow_v4.0")
    # → /Users/gary/项目开发/AdarianMigration/adarian mvp/docs/skills/workflow_v4.0

注册：
    已在 executor_registry.yaml 注册为 skill path-resolver。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 默认基址探测 ──────────────────────────────────────────────

_DEFAULT_PROJECT_ROOT = Path.home() / "项目开发" / "AdarianMigration" / "adarian mvp"
_DEFAULT_ADARIAN = Path.home() / "项目开发" / "AdarianMigration" / "adarian mvp"


# ── 路径条目状态 ──────────────────────────────────────────────

@dataclass
class PathEntry:
    """单个路径的解析结果。"""
    key: str                    # 逻辑键名，如 "task_dir", "executor_registry"
    resolved: Path              # 解析后的绝对路径
    exists: bool                # 路径是否存在
    is_file: bool               # 是否是文件（vs 目录）
    error: str = ""             # 如果存在性校验失败，错误描述


# ── 主类 ──────────────────────────────────────────────────────

class ProjectPaths:
    """项目路径解析器。

    所有路径从 project_root 基址出发，不写死绝对路径。
    迁移到新机器时，只需修改 project_root 一个值。
    """

    def __init__(
        self,
        root: Optional[str | Path] = None,
        adarian_root: Optional[str | Path] = None,
    ):
        self.root = Path(root).expanduser().resolve() if root else _DEFAULT_PROJECT_ROOT.resolve()
        self.adarian_root = (
            Path(adarian_root).expanduser().resolve()
            if adarian_root
            else _DEFAULT_ADARIAN.resolve()
        )
        self._cache: dict[str, PathEntry] = {}

    # ── 核心解析方法 ─────────────────────────────────────────

    def _p(self, key: str, path: Path, check_exists: bool = True) -> PathEntry:
        """构造 PathEntry，可选进行存在性校验。"""
        entry = PathEntry(
            key=key,
            resolved=path,
            exists=path.exists(),
            is_file=path.is_file() if path.exists() else False,
        )
        if check_exists and not entry.exists:
            entry.error = f"路径不存在: {path}"
        self._cache[key] = entry
        return entry

    def resolve(self, key: str) -> PathEntry:
        """按逻辑键名查找已缓存的路径条目。"""
        if key in self._cache:
            return self._cache[key]
        return PathEntry(key=key, resolved=Path(), exists=False, is_file=False,
                         error=f"未知路径键: {key}")

    # ── Adarian 项目路径 ─────────────────────────────────────

    def task_dir(self, task_id: str) -> PathEntry:
        """tasks/active/<task_id>/"""
        return self._p(f"task_dir:{task_id}",
                       self.root / "tasks" / "active" / task_id)

    def task_output(self, task_id: str, filename: str = "") -> PathEntry:
        """tasks/active/<task_id>/outputs/<filename>"""
        p = self.root / "tasks" / "active" / task_id / "outputs"
        if filename:
            p = p / filename
        return self._p(f"task_output:{task_id}/{filename}", p,
                       check_exists=bool(filename))

    def registry(self, filename: str = "") -> PathEntry:
        """WorkflowBase/registry/<filename>"""
        p = self.adarian_root / "WorkflowBase" / "registry"
        if filename:
            p = p / filename
        return self._p(f"registry:{filename}", p, check_exists=bool(filename))

    def iteration_plan(self, plan_name: str) -> PathEntry:
        """docs/iterations/<plan_name>"""
        return self._p(f"iter_plan:{plan_name}",
                       self.adarian_root / "docs" / "iterations" / plan_name)

    def tools(self, subpath: str = "") -> PathEntry:
        """tools/<subpath>"""
        p = self.root / "tools"
        if subpath:
            p = p / subpath
        return self._p(f"tools:{subpath}", p, check_exists=bool(subpath))

    def executor(self, name: str, subpath: str = "") -> PathEntry:
        """WorkflowBase/runner/<name>/<subpath>"""
        p = self.root / "WorkflowBase" / "runner" / name
        if subpath:
            p = p / subpath
        return self._p(f"executor:{name}/{subpath}", p)

    def archived_task(self, domain: str, task_id: str) -> PathEntry:
        """tasks/archived/<domain>/<task_id>/"""
        return self._p(f"archived:{domain}/{task_id}",
                       self.root / "tasks" / "archived" / domain / task_id)

    # ── Adarian 项目路径 ─────────────────────────────────────

    def adarian(self, subpath: str = "") -> PathEntry:
        """AdarianMigration/adarian mvp/<subpath>"""
        p = self.adarian_root
        if subpath:
            p = p / subpath
        return self._p(f"adarian:{subpath}", p, check_exists=bool(subpath))

    def adarian_skill(self, skill_name: str) -> PathEntry:
        """Adarian docs/skills/workflow_v4.0/<skill_name>"""
        return self._p(f"adarian_skill:{skill_name}",
                       self.adarian_root / "docs" / "skills" / "workflow_v4.0" / skill_name)

    def adarian_pm_runtime(self, subpath: str = "") -> PathEntry:
        """Adarian PM Runtime 目录: docs/skills/workflow_v4.0/pm_runtime/<subpath>"""
        base = self.adarian_root / "docs" / "skills" / "workflow_v4.0" / "pm_runtime"
        if subpath:
            base = base / subpath
        return self._p(f"adarian_pm:{subpath}", base)

    # ── Hermes 脚本路径 ──────────────────────────────────────

    def hermes_script(self, script_name: str) -> PathEntry:
        """~/.hermes/scripts/<script_name>"""
        return self._p(f"hermes_script:{script_name}",
                       Path.home() / ".hermes" / "scripts" / script_name)

    def hermes_skill(self, skill_name: str) -> PathEntry:
        """~/.hermes/skills/<skill_name>/SKILL.md"""
        p = Path.home() / ".hermes" / "skills"
        full_path = p / skill_name
        skill_md = full_path / "SKILL.md"

        # 直接路径不存在或没有 SKILL.md → 尝试在分类子目录中搜索
        if not skill_md.exists():
            found = False
            if p.exists():
                for cat_dir in p.iterdir():
                    if cat_dir.is_dir():
                        candidate = cat_dir / skill_name / "SKILL.md"
                        if candidate.exists():
                            full_path = cat_dir / skill_name
                            skill_md = candidate
                            found = True
                            break
            if not found:
                # 仍不存在，返回完整路径（self._p 会标记 exists=False）
                pass
        return self._p(f"hermes_skill:{skill_name}", skill_md)

    # ── 批量校验 ─────────────────────────────────────────────

    def verify(self, *keys: str) -> list[PathEntry]:
        """批量校验多个路径的存在性。返回所有条目（含缺失的）。"""
        results = []
        for key in keys:
            entry = self.resolve(key)
            results.append(entry)
        return results

    def verify_all(self) -> list[PathEntry]:
        """校验所有已缓存的路径。"""
        return list(self._cache.values())

    def missing_report(self, *keys: str) -> str:
        """生成人类可读的缺失路径报告。"""
        lines = ["## 路径缺失报告", ""]
        found_issues = False
        for key in keys or list(self._cache.keys()):
            entry = self.resolve(key)
            if entry.error:
                found_issues = True
                lines.append(f"  ❌ {entry.key}: {entry.error}")
        if not found_issues:
            lines.append("  ✅ 所有路径正常")
        lines.append("")
        return "\n".join(lines)

    # ── 迁移辅助 ─────────────────────────────────────────────

    def report_all(self) -> str:
        """输出所有已解析路径的完整报告（用于调试/迁移审查）。"""
        lines = [
            f"project_root: {self.root}",
            f"adarian_root: {self.adarian_root}",
            "",
            "已缓存的路径:",
        ]
        for key, entry in sorted(self._cache.items()):
            status = "✅" if entry.exists else "❌"
            ftype = "📄" if entry.is_file else "📁"
            lines.append(f"  {status} {ftype} {entry.key}: {entry.resolved}")
        return "\n".join(lines)


# ── 便捷函数 ──────────────────────────────────────────────────

def default_paths() -> ProjectPaths:
    """返回默认配置的 ProjectPaths 实例。"""
    return ProjectPaths()


# ── CLI 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    paths = default_paths()
    if "--report" in sys.argv:
        print(paths.report_all())
    elif "--verify" in sys.argv:
        print(paths.missing_report())
    else:
        print(f"用法: python3 {__file__} --report|--verify")
        print()
        print("--report  输出所有已配置路径的状态")
        print("--verify  输出路径缺失报告")
