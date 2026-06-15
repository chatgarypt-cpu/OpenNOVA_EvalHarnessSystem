---
name: topic-boundary
description: "话题边界与授权 — 讨论阶段和执行的边界、authorization_status 自检、跨会话授权衰减。讨论→执行转换时加载。权威来源：pm-runtime §11.10。"
version: 0.1.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, governance, authorization, topic-boundary]
---

# Topic Boundary & Authorization Protocol v0.1.0

> 权威来源：pm-runtime §11.10（话题边界与授权协议）
> 加载时机：从讨论进入执行决策时

## 0. 什么时候加载

涉及以下任意一步时，必须先加载本技能：

1. **从讨论跨入执行** — 刚讨论了某个话题，需要判断能不能开始做
2. **检查授权状态** — 需要确认 next_authorized_action 是否存在
3. **做边界自检** — 准备说"推进X"或"我建议执行Y"之前
4. **处理跨会话问题** — 上一会话的授权在当前会话需要重新确认

## 1. 核心纪律

本协议不是文档，是行为约束。以下每条规则都是**在执行动作之前必须过的 gate**。违反 = 协议违规。

## 2. 关键区分

| 类型 | 含义 |
|------|------|
| **carryover** | 上一轮的遗留债，不是 next action |
| **recommended_next** | 建议的下一步，不是授权 |
| **next_authorized_action** | Owner 明确批准的当前动作 |

`carryover` 和 `recommended_next` 都不等于 `next_authorized_action`。没有后者时，不得执行任何操作。

## 3. 阶段转换的 Owner Gate

从以下状态进入 execution 必须显式请求 Owner-Control 确认：

discussion → spec_draft → inventory → review → classification → planning → execution / migration / landing / runtime patch / dogfood / closeout

## 4. 所有 key object 必须声明 5 字段

```yaml
id:
display_name:
type:        # spec | iteration_plan | task | backlog_item | carryover_item | asset
lane:        # workflow | registry | ...
status:      # draft | draft_review | active | closed | ...
authorization_status: discussion_only | draft_only | review_only | recommended | not_authorized | authorized_for_execution | authorized_for_migration | closed | archived
```

## 5. 跨会话授权衰减

跨会话的 `authorized_for_execution` 标记默认降级为 `discussion_only`。handoff 中的 authorization_status 作历史参考，不自动恢复为当前动作。

## 6. 自检规则

每次说"要不要推进 X"或"我建议执行 Y"前，检查：
1. 我引用的对象 `authorization_status` 是什么？
2. 我要提议的动作 `requires_owner_gate` 是 true 吗？
3. carryover 被我说成 next action 了吗？

如果 `authorization_status` 不是 `authorized_for_execution`，先确认再推进。

## 7. 当前状态修正模板

```yaml
current_topic:
  id:
  display_name:
  type:
  lane:
  status:
  authorization_status:

previous_topic:
  id:
  display_name:
  type:
  status:
  authorization_status:

carryover_from_previous:
  - id:
    display_name:
    type: backlog_item
    status: carryover
    authorization_status: not_authorized

next_authorized_action:
authorization_status:
```

没有 `next_authorized_action` 时只能做：解释 / 盘点 / 草拟 / 审查 / 归类 / 等待 Owner 决策。不能做：迁移 / 重命名 / 落盘 / 派发 Codex / 启动 dogfood。

## 8. 迭代计划冲突裁定规则（2026-06-02 确立）

当同时拥有**草稿迭代计划文件**和**Owner 口头范围**时，以下优先级确定最终执行范围：

| 优先级 | 来源 | 角色 |
|--------|------|------|
| P1 | 草稿迭代计划 | 结构参考——节点定义、输出格式、依赖关系以计划文件为准 |
| P2 | Owner 口头范围 | **覆盖权**——当计划内容与 Owner 口头范围冲突时，以 Owner 口头为准 |
| P3 | 执行通道 | **relay runner**——所有执行必须走 relay dispatch，禁止手动 tmux 或 Hermes 直接执行 |
| P4 | 启动条件 | **等 Owner 明确批准**——`next_authorized_action` 未设置前，不执行任何节点 |

### 8.1 典型场景：草案与口头范围冲突

常见冲突类型及裁定：

| 冲突 | 草案说法 | Owner 口头 | 裁定 |
|------|---------|-----------|------|
| 范围超了 | DAG 包含 A+B+C 三阶段 | 只要 A | 以 Owner 为准，只做 A |
| 操作受限 | "不直接写 registry YAML" | "修 registry YAML" | 以 Owner 为准，允许写 |
| 执行模式 | "启动 Claude Code agent" | "走 relay" | 以 Owner 为准，走 relay |
| 启动时机 | 计划写好了直接跑 | "先别动手" | 以 Owner 为准，不启动 |

### 8.2 自检规则

在准备执行迭代计划前，检查：
1. 计划文件的节点定义是否与 Owner 口头范围一致？不一致的地方标记为 P2 覆盖。
2. 所有节点是否都能通过 relay runner 执行？如有节点设计为"直接执行"，需改为 relay dispatch。
3. Owner 是否已明确说"动手"？没有就是 P4 阻塞。

## References

- pm-runtime §11.10 — 权威来源
- `~/.hermes/skills/pm-runtime/pm-runtime/references/topic-boundary-and-authorization-protocol.md`
- Downloads → Archive SOP (`downloads-iteration-archive` skill)：迭代计划归档后审查的配合协议
