# Relay → Executor Prompt Contract (v0.2)

**Status:** Draft — 已整合 Hermes 反馈
**Date:** 2026-06-02
**Parties:** Hermes (parent orchestrator) ↔ Executor (child node, e.g. Claude tmux)

---

## Prompt 结构（按顺序）

```yaml
# ═══════════════════════════════════════════════════════════
# Relay Dispatch Prompt Contract v0.2
# ═══════════════════════════════════════════════════════════

task_id: <任务标识>

# ── 第 1 区：执行模式 ──────────────────────────────────────
execution_mode: workflow    # direct | workflow | agent-team

# ── 第 2 区：前置要求（可选）──────────────────────────────
# Executor 原生 skill，通过 Skill tool 加载
prerequisites:
  - skill: karpathy-coding

# ── 第 3 区：任务目标 ──────────────────────────────────────
objective: > 1-2 句话

# ── 第 4 区：输入材料（可选）──────────────────────────────
inputs:
  - path/to/file.py

# ── 第 5 区：Agent Team（可选）─────────────────────────────
agent_team:
  agents:
    - name: xxx
      focus: "xxx"
  mode: parallel
  synthesis: review-synthesis

# ── 第 6 区：约束与限制（可选）──────────────────────────
constraints:
  timeout: 600
  scope: "仅修改 xxx 目录"
  forbidden: "不得修改 xxx"

# ── 第 7 区：输出约束 ──────────────────────────────────────
# 由 relay 自动追加到 prompt 末尾
```

## 区域说明

| 区域 | 必填 | 位置 | 说明 |
|------|------|------|------|
| `task_id` | ✅ | prompt 开头 | 任务标识 |
| `execution_mode` | ✅ | prompt 开头 | `direct` / `workflow` / `agent-team` |
| `prerequisites` | ⬜ | prompt 前部 | Executor 原生 skill，自动加载 |
| `objective` | ✅ | prompt 中部 | 1-2 句话 |
| `inputs` | ⬜ | prompt 中部 | 需要读取的文件路径 |
| `agent_team` | ⬜ | prompt 中部 | 仅 `agent-team` 模式需要 |
| `constraints` | ⬜ | prompt 中部 | timeout、scope、禁止操作 |
| `expected_outputs` | ✅ | prompt 末尾 | relay 自动追加 |

## Executor 收到 prompt 后的执行顺序

1. 解析 `execution_mode`
2. 按 `prerequisites` 列表加载原生 skill（通过 Skill tool）
3. 读取 `inputs` 列表中的文件
4. 读取 `expected_outputs`（prompt 末尾），规划输出路径
5. 根据 `execution_mode` 执行
6. 写入 `expected_outputs`，验证 receipt

## 历史问题（已解决）

| 问题 | 解决方案 |
|------|---------|
| karpathy-coding 路径 | Executor 原生 skill，`Skill(skill="karpathy-coding")` 加载 |
| /clauderemote 重复 | relay 自动处理，已从 prerequisites 去掉 |
| dag_mode vs execution_mode | 统一为三值枚举，废弃 dag_mode |
| agent 文件路径 | Agent tool 通过 subagent_type 自动解析 |

## 待解决

读文件权限 — relay 配置中应对 `cat`/`ls`/`head`/`tail` 等只读命令自动批准。当前通过 dialog_watcher.py 临时处理。
