# Relay → Executor Prompt Contract (v0.2)

**Status:** Draft — Hermes 反馈已整合
**Date:** 2026-06-02
**Parties:** Hermes (parent orchestrator) ↔ Executor (child node, e.g. Claude tmux)

---

## 概述

Hermes 调用 Executor 时，必须按以下结构组织 prompt。Executor 的行为依赖于此结构的完整性。

## Prompt 结构（按顺序）

```yaml
# Relay Dispatch Prompt Contract v0.2

task_id: <task-id>

execution_mode: workflow    # direct | workflow | agent-team

prerequisites:
  - skill: karpathy-coding

objective: >
  1-2 句话任务描述

inputs:
  - <文件路径>

agent_team:           # 仅 agent-team 模式
  agents:
    - name: <agent>
      focus: "<职责>"
  mode: parallel
  synthesis: <汇总 agent>

constraints:
  scope: "<范围>"
  forbidden: "<禁止>"
```

## Executor 收到 prompt 后的执行顺序

1. 解析 execution_mode
2. 按 prerequisites 列表加载原生 skill（通过 Skill tool）
3. 读取 inputs 列表中的文件
4. 读取 expected_outputs（prompt 末尾），规划输出路径
5. 根据 execution_mode 执行
6. 写入 expected_outputs，验证 receipt

## 已解决的问题

| 问题 | 方案 |
|------|------|
| karpathy-coding 路径 | Executor 原生 skill，`Skill(skill="karpathy-coding")` 加载 |
| /clauderemote 重复 | relay 自动处理，已从 prerequisites 去掉 |
| dag_mode vs execution_mode | 统一为三值枚举 execution_mode，废弃 dag_mode |
| agent 文件路径 | Agent tool 通过 subagent_type 自动解析 |

**注意：** 当前 relay runner 不解析此 YAML frontmatter（作为纯文本传递给 Executor）。task_config.yaml 中的 `dag_execution_mode` 字段控制 relay 侧的三组件加载。
