# Gate → Skill → Dispatch 触发链治理模式

> 关联技能：dispatch-prompt-authoring
> 当前位置：Skill §6（§6 完整触发链）
> 设计日期：2026-06-03
> 此文件为 §6 的详细设计参考，供跨会话引用。

## 设计动机

B 线狗食阶段暴露了一个治理盲区：迭代计划下载后，Hermes 直接跳到 dispatch 或手动跑脚本询问"可以吗"，缺少一个从"迭代计划到达"到"dispatch 启动"之间的自动治理路径。

Gate → Skill → Dispatch 触发链解决了这个问题：

```
迭代计划到 Owner 手上（Downloads/）
  → [自动] hook 检测新文件
  → [自动] 落盘到资产目录
  → [自动] 审查冲突和完整性
  → [自动] 路由到正确的 skill 建目录
  → [人] 选执行模式（direct/agent-team/workflow）
  → [自动] relay dispatch
```

## 架构角色

| 层 | 角色 | 职责 | 
|----|------|------|
| Hook | 触发层 | pre_llm_call 自动扫描、落盘、审查。不输出执行指令，只输出 readiness + recommended_next_skill |
| Skill | 规则层 | 被 hook 推荐后加载，按自身规则创建目录或写 prompt。不自动 dispatch，但提供 dispatch 所需的完整材料 |
| Dispatch | 执行层 | relay runner 消费 skill 产出的 prompt + config，走 executor_registry |
| Owner | 确认层 | workflow 模式需 Owner 显式说"可以"，direct 和 agent-team 不需要 |

## 三种执行模式的触发差异

| 模式 | Hook 后行为 | Owner 确认 | 对应 executor |
|------|-----------|-----------|--------------|
| direct | skill 建目录 → 写 direct prompt → 立即 dispatch | 不需要 | Codex / Claude Code |
| agent-team | skill 建目录 → 写 @agent prompt → 立即 dispatch | 不需要 | Claude Code |
| workflow | skill 建目录 → 写 workflow prompt → **等 Owner** → dispatch | **必须** | Claude Code |

## 与 workflow_core 的关系

此触发链是 A 线治理的核心设计决策之一。正式化后应纳入 workflow_core.md 或 workflow_core_compact.md 的执行治理章节。

## 变更记录

| 日期 | 变更 |
|------|------|
| 2026-06-03 | 初版。基于 promotion-gate 的设计和实践建立 |
