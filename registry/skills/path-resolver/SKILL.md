---
name: path-resolver
description: "PM Runtime 路径防漂移模块 — 从单一 project_root 基址派生所有路径，支持存在性校验、缺失报告、迁移辅助。每次涉及路径操作时加载。"
version: 0.1.0
author: Hermes + Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, path, resolver, governance]
---

# Path Resolver — PM Runtime 路径防漂移模块

## 定位

> 所有路径从单一 project_root 基址出发，不写死绝对路径。
> 迁移到新机器时，只需修改 project_root 一个值。

## 在 PM Runtime 中的位置

```text
所有涉及路径的操作（dispatch、归档、closeout、资产注册）
  → 先加载本 skill
    → path_resolver.py 负责解析路径并校验存在性
      → 校验通过后下游 skill 才执行实际操作
```

## 提供的路径类型

| 方法 | 用途 | 示例 |
|------|------|------|
| `task_dir(task_id)` | active 任务目录 | `tasks/active/<task_id>/` |
| `task_output(task_id, file)` | 任务产出文件 | `tasks/active/xxx/outputs/report.md` |
| `registry(filename)` | 注册表文件 | `WorkflowBase/registry/executor_registry.yaml` |
| `iteration_plan(name)` | 迭代计划 | `docs/iterations/workflow/xxx.md` |
| `tools(subpath)` | 工具脚本 | `tools/sound_utils.py` |
| `executor(name, subpath)` | executor 代码 | `tools/pm_runtime/relay/codex/tmux_executor.py` |
| `adarian(subpath)` | A 线项目目录 | `AdarianMigration/adarian mvp/docs/skills/...` |
| `adarian_skill(name)` | A 线技能目录 | `adarian mvp/docs/skills/workflow_v4.0/<skill>` |
| `hermes_script(name)` | Hermes 脚本 | `~/.hermes/scripts/promotion-gate.py` |
| `hermes_skill(name)` | Hermes skill | `~/.hermes/skills/pm-runtime/closeout-gate/` |

## 校验

```python
from tools.pm_runtime.path_resolver import default_paths

paths = default_paths()
paths.task_dir("b-to-a-promotion-go1").exists   # True/False
paths.registry("executor_registry.yaml").error    # 如缺失则返回错误描述
print(paths.report_all())                         # 所有路径状态
print(paths.missing_report())                     # 仅缺失路径
```

## 迁移

换机器时只需修改 `path_resolver.py` 顶部的两个常量：

```python
_DEFAULT_WORKYB = Path.home() / "项目开发" / "workyb"
_DEFAULT_ADARIAN = Path.home() / "项目开发" / "AdarianMigration" / "adarian mvp"
```

## 注册

- 代码：`tools/pm_runtime/path_resolver.py`
- 注册表：`WorkflowBase/registry/executor_registry.yaml`（type: resolver）
