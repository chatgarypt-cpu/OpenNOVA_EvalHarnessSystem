---
name: task-directory-canonical
description: "任务目录规范 — tasks/ 目录结构、生命周期、归档分类与命名规范。定义任务证据工作区的目录模板、生命周期阶段、closeout checklist profile、归档规则。权威来源：pm-runtime。"
version: 0.1.2
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, task-directory, governance, archive, lifecycle]
---

# Task Directory Canonical Skill v0.1.2

> 权威来源：pm-runtime
> 适用空间：Adarian/tasks/
> 核心定位：定义 tasks/ 作为任务证据工作区的目录结构、生命周期、分类与命名规范

---

## 0. 什么时候加载此技能

涉及以下任意一步时，必须先加载本技能：

1. **创建新任务目录** — 需要决定目录路径和内部子目录结构
2. **归档已完成任务** — 需要按规范移入 archived/
3. **修复违规目录** — 现有目录不符合规范
4. **审查任务结构** — 检查已有任务是否合规

## 1. 一句话定位

```
tasks/ = task evidence workspace
```

一次任务从派发、执行、回传、审查、修复到 closeout 的全过程证据容器。

## 2. 顶层结构

### 2.1 Active — 平铺，无 domain 层

```text
tasks/active/<task_id>/
```

Active 任务很少（<=3），不需要 domain 分组。

### 2.2 Archived — 分类归档

```text
tasks/archived/
├── coursework/    ← 课程作业
├── inventory/     ← 能力扫描、盘点
├── registry/      ← Registry 迭代、狗食、审查
├── relay/         ← Relay Runner 全链路
└── workflow/      ← DAG 设计审查、治理审计
```

### 2.3 禁止结构

- ❌ `tasks/review/`（直接挂 tasks/ 下）
- ❌ `tasks/active/review/`（active 有 domain 层）
- ❌ `tasks/active/` 目录空置

#### 2.4 WorkflowBase 目录组织原则（WorkflowBase/ 目录）

本技能聚焦 `tasks/` 目录，但 `WorkflowBase/` 目录遵循相同的分类纪律：

**原则：维护工具嵌套在被维护资产之下。**

如果一个脚本/系统的唯一职责是维护某个资产（如 registry 的 drift_check），它的存放位置是该资产的子目录，而非资产同级目录。

```
✅ 正确：
  WorkflowBase/registry/           ← Registry 六件套
  WorkflowBase/registry/self-maint/ ← Registry 的自持维护工具

❌ 错误：
  WorkflowBase/registry/           ← Registry 六件套
  WorkflowBase/self-maint/         ← 独立的 "自持系统"
```

**判断标准：** 该工具是否只维护一个资产？只维护一个 → 嵌套。维护多个 → 保留为独立目录。

### 2.5 禁止分类：无 "other" 兜底

所有归档任务必须有精确的分类域名。**不允许 "other"/"misc"/"uncategorized" 等兜底分类。**

原因：合不进去的文件一定有它真实所属的堆。找不到分类域时，不是新增 "other"，而是：
1. 确定任务本质属于哪类工作——是审查（registry review）、扫描（inventory）、还是治理流程（workflow）？
2. 如果确实不属于任何现有域，新增一个精确命名的域（如 `dogfood/`），而不是 "other"

**域选择决策树：**

```
任务属于哪类工作？
├── 迭代/版本执行（R1.0 dogfood、节点执行）
│   └─→ registry/
├── 执行记录快速浏览和归档（inventory review、DS Team review）
│   └─→ inventory/
├── relay runner 全链路测试
│   └─→ relay/
├── DAG/workflow 设计审查、治理审计
│   └─→ workflow/
├── 课程作业
│   └─→ coursework/
└── 狗食测试（dogfood 专项）
    └─→ registry/（与迭代执行同域，用任务名区分）
```

**记忆辅助：** archive 域的每种 file 类型都有明确的去路。如果为了一个任务新建域，域名必须精确、自描述。"other" 没有存在理由。

## 3. 任务命名规范

格式：`<所属领域>-<版本/流水号>-<做了什么>`

规则：
1. 全英文小写，连字符分隔
2. 第一段是所属领域（relay/registry/inventory/workflow/coursework）
3. 目录名必须自描述
4. 不保留内部代号

## 4. 内部标准子目录

```
tasks/active/<task_id>/
├── dispatch/    ← 任务书和 prompt
├── logs/        ← 原始执行日志
├── runtime/     ← relay 运行时状态
├── outputs/     ← 任务正式产出
└── summary/     ← 压缩摘要
```

可选：receipts/（YAML receipt）、scratch/（临时 helper）

**强制性规则：**
1. 每个任务目录必须包含 dispatch/ + outputs/ + runtime/ + logs/
2. receipt 和 execution_report 必须在 outputs/ 下
3. relay runner 和 Hermes 都必须遵守此 schema

## 5. 生命周期

```text
active → settled → closed → archived
```

- Active: 执行中
- Settled: 执行完成，等 closeout
- Closed: Owner 裁决
- Archived: 移入归档树

## 6. Closeout Checklist（Profiled）

由 `task_status.yaml.execution_profile` 确定：

**Smoke:** task_status.yaml + result.json + outputs/
**Standard:** 加 dispatch/ + receipts/ + summary/
**Full DAG:** 加 execution_report + carryover/scratch/asset_promotion 检查

## 7. 旧任务向后兼容

- 默认 keep_legacy + light_index
- migrate 需 Owner-Control 批准
- 禁止默认批量迁移

## 8. 创建新任务的流程

```
1. 加载本 skill（task-directory-canonical）
2. 确认 task ID 符合命名规范
3. 创建 tasks/active/<task_id>/ 目录
4. 创建 dispatch/task_config.yaml + dispatch/prompt.md
5. 创建 outputs/、runtime/、logs/ 空目录
6. 创建 task_status.yaml（status: active）
```

**关键门：** 第 1 步不可跳过。不加载本技能就创建目录 = 违规。

## References

- 完整 spec 文档：`docs/iterations/workflow/task-directory-canonical-spec-v0.1.md`
- pm-runtime — 中台运行索引（父 skill）
- closeout-gate — closeout 具体执行规则
- dispatch-prompt-authoring — 本 skill 的下游（gate→本 skill 建目录→prompt skill 写 prompt→dispatch）

## 触发位置

本 skill 在 chain 中的位置：

```text
promotion-gate hook 通过
  → [当前] task-directory-canonical：建 tasks/active/<task-id>/ 目录（dispatch/ outputs/ runtime/ logs/ summary/）
    → dispatch-prompt-authoring：选模式、写 prompt
      → relay runner dispatch
```
