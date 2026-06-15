---
name: dag-execution
description: "DAG 链路执行 — DAG 节点串行执行协议、fan-in 聚合、安全隔离、Repair Agent。每个节点走 relay dispatch，完成后 fan-in agent team 统一验收。权威来源：新一代DAG工作流设计文档_v0.4_emerged.md。"
version: 0.8.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, dag, execution, fan-in, security, repair]
---

# DAG Execution Protocol v0.2.0

> 权威来源：`新一代DAG工作流设计文档_v0.4_emerged.md`（52KB，1269 行，v0.4 定稿）
> DAG 工作流不是默认开启的——需要通过 `dag_mode: true` 显式激活
> 加载时机：跑 DAG pipeline 时

## 0. 什么时候加载

1. **执行 DAG pipeline** — 串行/并行执行多个有依赖关系的节点
2. **创建 DAG 节点任务** — 为每个节点建 task_dir + dispatch + receipt
3. **配置安全隔离** — 设置 execution_context（路径白名单、命令白名单）
4. **配置 fan-in 验收** — 多节点完成后 agent team 统一聚合验收
5. **处理节点失败** — 触发 Repair Agent 修复流程
6. **编写 task_config.yaml** — 遇到 relay runner 配置报错时
7. **设计 DAG 节点的内部结构** — 判断用单次 relay 还是 Claude Code workflow

## 0.1 两层 DAG 范式（2026-06-02 定稿）

DAG 系统分两层，各管各的职责：

```text
第一层：Hermes DAG（Goal 层）
  管哪些 Goal 能跑了、节点间依赖、回收产出、触发 Repair
  └── 每个节点 = 一次 relay dispatch = 一个 tmux session
  
       ┌── 简单节点（如 baseline 扫描）→ 单次 relay，单 agent
       │
       └── 复杂节点（如 N2-N8 多个补丁）→ 单次 relay
            └── 第二层：Claude Code workflow（subtask 层）
                管节点内多 subtask 的依赖关系和并行执行
                └── 多个 workflow agent 在同一个 tmux session 内
```

### 什么时候走 workflow

| 场景 | 用单次 relay | 用 Claude Code workflow |
|------|-------------|------------------------|
| 只有一个子任务 | ✅ 直接 relay dispatch | ❌ 过度设计 |
| 多个子任务改不同文件，无依赖 | ✅ 可以，但 workflow 也可 | ✅ 在一个 session 内清晰 |
| 有依赖链（A→B→C） | ❌ 开多个 tmux 不合理 | ✅ 原生 DAG 支持 |
| 需要并行执行（A∥B→C） | ❌ 多 tmux 难以管理 | ✅ workflow 内部管理 |
| 输出需要合成汇总 | ❌ 需外部 fan-in | ✅ 内置 synthesis |

**核心原则：一个 DAG 节点 = 一次 relay dispatch = 一个 tmux session。** 节点内有多个 subtask 时用 Claude Code workflow 管理内部 DAG，不在 Hermes 层开多个 tmux。

### Hermes 与 Claude Code 的职责边界

Hermes 负责：设计 DAG 节点结构、写 dispatch/prompt、启动/监控 relay、回收产出、触发 Repair Agent。
Claude Code 负责：实现代码、写脚本、修改文件、自测、自修复。

**Hermes 不替 Claude Code 写代码。** 当需要实现/修改代码时，通过 relay dispatch 派给 Claude Code，不是用 Hermes 的 execute_code 或 terminal 直接写。例外：修基础设施 bug 且你离得最近（relay runner 本身卡住了、dispatch-gate 有问题），可以直接修。

### DAG 节点 dispatch SOP（2026-06-02 定型）

**dispatch/prompt.md 按 `references/relay-executor-prompt-contract.md` 结构组织。** 所有要求（加载 skill、触发 workflow、执行任务）通过 prompt 文本描述，不发送 slash 命令。

**三种 execution_mode（取代 dag_mode 布尔值）：**

| mode | 用法 | 适用场景 |
|------|------|----------|
| `direct` | 单次 relay，单 agent | 简单节点（如 baseline 扫描） |
| `workflow` | 单次 relay，Claude 内部 DAG | 多子任务有依赖关系（如 N2-N8 补丁组） |
| `agent-team` | 单次 relay，多 agent 审查 | Reality Review 等并行审查 |

**prompt 标准结构（coding 节点）：**

```markdown
载入 karpathy-coding 行为准则。
use a workflow to: <任务目标>

<具体描述>
```

**关键规则：**
- prompt 中用自然语言写"载入 X"、"创建 Y"，不发 `/karpathy-coding` 等 slash 命令
- `/workflows` 只查看 workflow 列表，不创建 workflow
- slash 命令测试前在 `.claude/commands/` 和 `.claude/skills/` 确认命令名
- 先检查产出文件是否已在盘上，再决定是否杀 session 重来
- **dispatch 后立即启动 `scripts/dialog_watcher.py`**（2 秒轮询，自动批权限对话框），作为 dispatch SOP 的固定步骤

## 1. 核心流程

### 1.1 五层 Goal 体系（2026-06-02 定型）

DAG 工作流在一个迭代中可覆盖多层 Goal。这是 Registry R1.0 狗食中验证的 5 层结构——注意这不是固定模板，而是经验模式；每个迭代按实际需要定义自己的 Goal 层数。

```text
Goal 1: DAG v0.4 工作流协议（基础设施）
  ├── 定义节点规范、fan-in、安全隔离、Repair Agent
  └── Goal 2 和 Goal 3 都通过本协议执行（消费者）
       │
       ├── Goal 2: 补丁（修复模型失真）
       │   ├── baseline → 并行补丁 → 交叉引用
       │   └── 节点校验失败由 v0.4 §10 Repair Agent 处理
       │
       ├── Goal 3: 搭建自持系统
       │   ├── self-maint spec → drift check → scan proposal
       │   └── 节点校验失败由 v0.4 §10 Repair Agent 处理
       │
       ├── N12 fan-in 验收（覆盖 Goal 2+3 全部节点）
       │
       ├── Goal 4: DAG 系统修复
       │   ├── 修执行链路 bug（relay/executor/dialog/安全隔离）
       │   ├── 不改设计文档、不改业务代码、不改 Hermes 核心
       │   └── 所有修复必须如实披露
       │
       └── Goal 5: Reality Review
           ├── 独立 5-agent team 核对全局执行质量
           └── registry-reality-review 或 code-reality-review
```

#### Goal 消费关系

- **Goal 1 是基础设施**，不产出业务结果。它的 DAG 协议被 Goal 2 和 Goal 3 消费。
- **Goal 2 和 Goal 3 是业务层**，其节点全部走 Goal 1 的 DAG 协议（relay dispatch + dag_mode + observer + Repair Agent）。
- **Goal 4 是基础设施修复层**，修 Goal 1~3 执行中暴露的 DAG 系统本身的问题。
- **Goal 5 是独立核查层**，与执行管道解耦。

#### 两层级 Repair 的区分

| 层级 | 触发条件 | 范围 | 对应的协议 |
|------|----------|------|-----------|
| 节点级 Repair Agent | DAG 节点产出校验失败 | 修节点内部问题（≤2 轮重试 → escalate） | v0.4 §10 |
| Goal 4 DAG 系统修复 | Goal 1~3 执行中暴露基础设施 bug | 修 relay/executor/dialog/隔离等管道问题 | 见 Goal 4 边界 |

**常见混淆：** 节点级 Repair Agent 修的是业务执行中遇到的问题（文件没写对、格式不对）。Goal 4 修的是管道本身（relay runner exit 5、timeout 不准、对话框误判）。两者不是替代关系，是不同层级的修复。

**常见偏移：** 将原始草案的 DAG 节点分拆到多个版本时，容易静默丢节点。执行前必须做 scope 验证（§1.2）。

### 1.2 激活条件

```text
task_config.yaml 中声明 execution_mode: workflow
  → 自动启用 §5.3 execution_context 安全隔离 + output_validation + Repair Agent
  → execution_mode: direct 或 agent-team 不受影响（不走 v0.4.1 三组件）
  → execution_mode 默认值 direct（由 relay_runner.py 的 run_task() 自动设置）
```

### 1.2 Scope 验证（执行前必须做的第零步）

> **Owner 纠正信号**（2026-06-02）：Gary 发现我呈现 R1.0 Goal 2 时漏掉了"资产注册自持系统"——原始 R1 草案的核心定位，在分拆 R1.0/R1.1/R1.2 时被静默去掉了，我没有主动察觉并汇报。

**在开始第一个 DAG 节点的 dispatch 之前，必须做 scope 验证：**

1. 对比**原始设计文档/草案的核心定位** vs **当前迭代计划中对应的 Goal**
2. 找出在分拆/细化过程中被**静默去掉了**的范围（不是写在 Non-Goals 里的，而是连提都没提的）
3. 将去范围项列表呈现给 Owner，确认是否要加回或保持去范围

**规则：**
- 原始草案的"核心定位"（core positioning）是最高层意图，分拆时被偏移是常态
- 偏移本身不是问题，**不汇报偏移**是问题
- 不要在 Owner 发现后才纠正——应该在执行前主动发现

**常见偏移模式：**
| 原始草案说 | 分拆后变成 | 偏移类型 |
|-----------|-----------|---------|
| "修复+搭建自持能力" | 只修复，自持推后 | 去掉了核心意图 |
| "全链路验证" | 只跑通部分节点 | scope 收缩 |
| 定位写了很多但 Goal 里没对应 | 找不到对应的 Goal | 遗漏 |

### 1.3 标准节点执行流程

```text
0. [Scope 验证] 对比原始草案 vs 当前迭代计划，汇报去范围项（§1.2）
1. 读迭代计划，取出一个 DAG 节点（node_id / sub_goal / dependency）
2. 判断节点类型（§0.1 两层范式）：
   → 简单节点（单任务）：走标准 relay dispatch（步骤 3-11）
   → 复杂节点（多 subtask）：prompt 内嵌 workflow 定义，一次 dispatch
3. 创建 task_dir（tasks/active/<dag-<node_id>>/）
4. 写 dispatch/task_config.yaml（含 iteration_plan_path + node_id + sub_goal + dag_mode: true）
5. 写 dispatch/prompt.md（sub_goal 专注，不涵盖其他节点；复杂节点在 prompt 内定义 workflow agent）
6. 写 dispatch/task_brief.md（v0.4 §3.1 格式 + YAML frontmatter）
7. **每个涉及 coding/registry 编辑的节点，dispatch prompt 开头用自然语言要求加载 karpathy-coding（"载入 karpathy-coding 行为准则"），不发 @skill 或 /karpathy-coding 这类 paste 不生效的指令**
8. 运行 dispatch-gate.py 检查
9. 启动 relay runner dispatch（observer_mode: true + observer_attach: terminal_window）
10. **立即启动 dialog_watcher.py + heartbeat_monitor.py 在后台运行**，自动批权限对话框 + 监测完成信号
    ```bash
    python3 tools/dialog_watcher.py <tmux_session_name>
    python3 tools/heartbeat_monitor.py tasks/active/<task-id>
    ```
    注：`<tmux_session_name>` 从 `runtime/session.yaml` 的 `tmux_session_id` 字段获取。
    两个 companion 互补：dialog_watcher 在运行中处理权限弹窗，heartbeat_monitor 在任务终止时播放通知音。
11. **打开 Terminal 窗口给 Owner**（observer_attach 会自动打开，但有时可能失败，手动补一次）
    ```bash
    osascript -e 'tell application "Terminal" to activate' -e 'tell application "Terminal" to do script "tmux attach-session -t <session_id>; exit"'
    ```
12. 节点完成后回收 result.json + outputs/
11. 触发 output validation
    - pass → 生成 node_receipt → 进入下一节点
    - fail → Repair Agent（issue_packet → retry ≤ 2 轮 → escalate_to_owner）
12. 所有并行依赖节点完成后进入下一个 Group
```

### 1.7 并行策略（可选，按迭代计划调整）

```text
N1（前置 baseline）串行 → 其余节点可并行（改不同文件）→ fan-in agent team 验收
```

Group 1: Baseline node（串行前置）
Group 2: Patch nodes（并行，各自改不同文件）
Group 3: Fan-in acceptance（全部完成后 agent team 统一验收）

### 1.8 Dispatch Prompt 编写

prompt.md 的具体写法和 SOP 在独立 skill `dispatch-prompt-authoring`（`~/.hermes/skills/pm-runtime/dispatch-prompt-authoring/`）中。每次写 prompt 前加载它。本 skill 不重复定义 prompt 规范。

### 1.4 Karpathy Coding Guidelines 约束

coding 节点必须在 prompt 开头用自然语言要求加载 karpathy-coding：

```markdown
载入 karpathy-coding 行为准则。
...
```

karpathy-coding 文件位于 `~/.cc-switch/skills/karpathy-coding/SKILL.md`，Claude Code 自动发现。不发送 `/karpathy-coding` slash 命令。

4 条核心准则：
1. Think Before Coding — 先读再想再写
2. Simplicity First — 最简方案优先
3. Surgical Changes — 只改需要改的
4. Goal-Driven Execution — 以目标为驱动力，不偏离

非 coding 节点（baseline 扫描、fan-in 验收）不需要。

### 1.5 Dispatch prompt 通用结构

coding DAG 节点的 dispatch prompt.md 按以下结构组织（non-coding 节点不需要加载 karpathy-coding）：

```markdown
载入 karpathy-coding 行为准则。
use a workflow to: <任务目标>

<具体描述>
```

此结构遵循 `references/relay-executor-prompt-contract.md`（v0.2）。clauderemote 由 relay runner 自动激活。

此前缀防止 Claude Code 在 tmux 中弹出交互式选项菜单（弹菜单会卡住 relay runner 的自动化流程）。

### 1.6 task_config 模板

```yaml
iteration_plan_path: docs/iterations/<plan>/<file>.md
node_id: <node_id>
node_sub_goal: <该节点的子目标描述>
node_dependency: [<前置 node_id 列表>]
dag_execution_mode: workflow   # direct | workflow | agent-team，取代 dag_mode 布尔值
execution_profile: standard
```

### 1.8 task_brief 模板（v0.4 §3.1 格式）

```yaml
---
task_id: "dag-<node_id>"
lane: adarian_runtime
project_id: workyb
task_type: pipeline
owner_goal: "<该节点的 sub_goal>"
completion_target: usable
context_status:
  enough: true
plan:
  owner_approval_required: true
dag:
  nodes: ["<本节点 node_id>"]
agent_team:
  required: false  # false = 单节点执行，验收在 fan-in 节点
memory_scope:
  read: ["WorkflowBase/**", "tasks/**"]
  write: ["{{task_dir}}/outputs/**", "{{task_dir}}/runtime/**"]
  forbidden: ["src/**", ".hermes/credentials*"]
---
```

## 2. 安全隔离（v0.4 §5）

### 2.1 execution_context

每个 DAG 节点启用以下安全隔离：

```yaml
execution_context:
  permissions:
    write_paths:
      - "{{task_dir}}/outputs/*"
      - "{{task_dir}}/runtime/*"
    read_paths:
      - "{{task_dir}}/**"
      - "WorkflowBase/**"
    bash_commands:
      - "ls" | "cat" | "head" | "tail" | "python3" | "mkdir -p"
  forbidden:
    dangerous_flags:
      - "--allow-dangerously-skip-permissions"
    bash_patterns:
      - "rm -rf" | "sudo" | "chmod 777" | "curl | bash"
    write_outside_task: true
    read_outside_declared: true
```

### 2.2 弹窗处理（2026-06-02 更新）

**已不再是 DialogHandler 的职责。** DialogHandler 不再对 BASH_PERMISSION 做批准或 HOLD，改为 defer 给外部 dialog_watcher（见 dispatch-prompt-authoring §3 dialog_watcher 章节）。dialog_watcher 是主批准机制，ClaudeDialogHandler 是 fallback。

| 弹窗类型 | 处理方式 | 责任模块 |
|----------|---------|---------|
| TRUST / FILE_CREATION | 自动批准（路径白名单校验） | ClaudeDialogHandler |
| FILE_EDIT | 自动批准 | ClaudeDialogHandler |
| BASH_PERMISSION（普通） | 模式匹配后自动批准 | dialog_watcher（外部 companion） |
| BASH_PERMISSION（workflow auto-mode） | 不自动批准，播放声音通知，等 Owner 在 Hermes CLI 确认 | dialog_watcher → Hermes |
| 未知弹窗 | fallback：DialogHandler 不 HOLD，仅记录日志，最终由 observer 窗口的人接管 | observer（人） |

### 2.3 隔离原则

- DAG 工作流不是默认开启模式，需 `dag_mode: true` 显式激活
- 非 DAG 任务的 relay dispatch 不受影响
- 激活后节点写入限 `outputs/`+`runtime/`，读取限 `task_dir`+`WorkflowBase/`

### 2.4 Observer 模式覆盖超时（2026-06-02）

**核心原则：用户盯着 Terminal 窗口时，所有人工超时全部跳过。**

```text
observer_mode: true（默认）+ observer_attach: terminal_window
  → _ready_timeout: 不再 exit 5，打日志继续等（warning not death）
  → _no_output_timeout: 跳过（120s 不触发）
  → _max_wall_time: 跳过（600s 不触发）
```

时间退出仅在无 observer 的后台运行中生效。实现位于 `tmux_executor.py` 的 `_monitor_loop_impl()`。此设计源于用户反馈"用户盯着，你怕什么超时"——人工超时挡不住用户能接管的问题。

## 3. Fan-in 验收协议（v0.4 §8）

### 3.1 触发时机

所有并行节点完成并校验通过后，触发 fan-in agent team 统一验收。

### 3.2 聚合策略

| 策略 | 适用场景 | 行为 |
|------|----------|------|
| `vote` | 多 Agent 审查 | 多数一致的结果胜出，分歧项标记 |
| `merge` | 同类数据合并 | 按字段对齐合并，去重后拼接 |
| `best_of` | 质量择优 | 按评分指标选最优结果 |

### 3.3 部分失败策略

| 策略 | 行为 |
|------|------|
| `continue` | 跳过失败节点，聚合成功节点结果，标记缺失 |
| `fail_fast` | 任意节点失败立即终止，上报 Owner |
| `retry_then_continue` | 失败节点自动重试（最多 2 轮），仍失败则 continue |

### 3.4 验收 agent team 角色定义

验收 agent team 至少包含一个 review-synthesis 代理。其职责是**真正汇总，不是简单拼接**：

1. **事实核查** — 验证每条 claim 是否有源文件支撑，标注 source-backed 和 source-unverified。不直接拿 reviewer 的结论当结论。
2. **上下文矛盾检测** — 比较各 reviewer 输出，标记观点矛盾、重复发现和盲区。矛盾项单独列出附 Hermes/MiMo 判断。
3. **真正汇总** — 按优先级组织（BLOCKING → WARNING → SUGGESTION），同主题归并，每个 finding 标注来源 reviewer。

report 使用 Write() 工具写，禁止 inline editor。写入后执行质量自检：无行号残留、无单词断裂、无表格错位。

### 3.5 验收检查项

| 检查项 | 说明 |
|--------|------|
| 节点间一致性 | 各节点产出是否对齐 |
| Sub-goal 完成度 | 每个节点是否真实完成 |
| 安全合规 | 是否有 API key 泄漏等安全违规 |
| 字段完整性 | 必填字段是否齐全，enum 值是否合法 |
| Carryover | 未完成项是否明确标记为下一版本输入 |

### 3.5 验收输出（fan_in_receipt.yaml）

```yaml
fan_in_receipt:
  node_id: "agent-team-acceptance"
  aggregation_strategy: "vote"
  source_nodes:
    - node_id: "node-a"
      status: "completed"
    - node_id: "node-b"
      status: "completed"
      output_path: "{{task_dir}}/outputs/"
  aggregated_output:
    path: "{{task_dir}}/outputs/acceptance_report.md"
  conflicts:
    path_conflicts: 0
    content_conflicts: 0
  partial_failure:
    total_nodes: 8
    succeeded: 7
    failed: 1
    success_ratio: 0.875
    policy_applied: "continue"
    meets_min_ratio: true
  verdict: "pass" / "pass_with_findings" / "hold"
  needs_owner_decision: false
```

## 4. Repair Agent 协议（v0.4 §10）

### 4.1 触发条件

节点产出物未通过预期校验时触发：
- 文件不存在（expected_outputs 路径无文件）
- 文件为空（0 字节）
- 格式错误（YAML/JSON 解析失败）
- 内容校验失败（字段缺失、结构不完整）

### 4.2 修复流程

```
节点完成 → 校验产出
  → pass → 进入下一节点
  → fail → Repair Agent 诊断 Issue
           → 生成 Issue Packet
           → Executor 重试（retry_once）
           → 再次校验
             → pass → 进入下一节点
             → fail → 二次重试（累计 ≤ 2 轮）
                      → 仍失败 → escalate_to_owner
```

### 4.3 三裁决枚举

| 裁决 | 含义 | 后续动作 |
|------|------|----------|
| `pass` | 修复成功 | 进入下一节点 |
| `retry_once` | 需要再试一次（≤ 2 轮上限） | 生成新 Issue Packet，Executor 重试 |
| `escalate_to_owner` | 超出修复轮数或方向错误 | 生成 owner_decision_request.yaml，等人工介入 |

### 4.4 Issue Packet 模板

```yaml
issue_packet:
  issue_id: "issue-YYYYMMDD-NNN"
  source_node: ""
  reviewer: "repair_agent"
  executor_to_fix: ""
  problem_type: "file_not_found" | "empty_output" | "format_error" | "timeout" | "permission_denied" | "logic_error"
  evidence:
    - "runtime/pane_capture.log"
  expected_fix: ""
  allowed_scope: []
  forbidden_scope: []
  retry_limit: 2
  owner_decision_required: false
  current_retry: 0
  diagnosis: ""
  repair_instruction: ""
```

## 5. DAG vs Agent Team vs Claude Code Workflow 选择规则

| 特征 | 用 DAG（Hermes relay） | 用 Agent Team（@agent） | 用 Claude Code Workflow |
|------|------------------------|----------------------|------------------------|
| 节点关系 | B 依赖 A 的产出 | 所有角色读同一套输入 | 多任务有依赖关系 |
| 产物形式 | 每个节点有独立 artifact | 最后只要一个汇总输出 | 每个 agent 产独立输出 + synthesis |
| 适合场景 | 审查→执行→测试→修复→复审（串行） | 结构+边界+一致性审查（并行） | 多个相关编码任务，有内部依赖 |
| 执行位置 | 每个节点 = 独立 relay dispatch | 单 tmux session | 单 tmux session |
| 并发机制 | Hermes 编排多 tmux | Claude 内部串行 agent | Workflow 运行时并行 |
| 适用规模 | 跨 session 的长程 pipeline | 3-5 个审查视角 | 5-16 个并发 agent，1000 上限 |
| 中断恢复 | 可重跑单节点 | 重新执行 | 同 session 内可 resume |

### 5.1 选择规则

- 串行依赖链 + 独立产物 → DAG（每节点走 relay dispatch + node_receipt）
- 同一输入、多视角、汇总输出 → Agent Team（@agent 并行 + synthesis）
- 多个相关编码任务在同一上下文中、有内部依赖 → **Claude Code Dynamic Workflow**

**核心原则：** Hermes relay 是 DAG 编排层，不是并行工具执行层。当多个 agent 在相同上下文内并行工作——走 Claude Code Workflow。当需要跨节点隔离、独立重跑、异构 executor——走 Hermes relay DAG。两者互补，不替代。

### 5.2 反模式：Hermes relay 多开代替 Claude Code Workflow

| ❌ 错误做法 | ✅ 正确做法 |
|-------------------------------|-----------|
| N2~N8 开 7 个独立 relay dispatch → 7 个 tmux Terminal 窗口 | 合并为单 relay dispatch → 单 tmux → workflow 内部 DAG |
| Hermes 外部编排并行 | Workflow 运行时内部编排 |
| 7 个独立 runtime/ 目录 | 1 个 runtime/，1 份聚合 report |

### 5.3 如何构造 Workflow Prompt

prompt 开头写 `"use a workflow to: [任务描述]"` 是确定性的 workflow 触发方式（2026-06-02 从 Executor 确认）。标准结构：

```
use a workflow to: <任务描述>

### Agent 1: <name>
**目标：** <做什么>
**依赖：** <前置 agent>

### Agent 2: <name>
**依赖：** Agent 1

## 输出要求
聚合输出到 outputs/report.md
```

验证方法：检查 `workflows/scripts/*.js` 文件是否存在，或通过 `/workflows` 查看进度。

### 5.4 Workflow 限制和 pitfall

| 限制 | 说明 |
|------|------|
| 并发上限 | 最多 16 个 concurrent agent |
| 总数上限 | 1000 agent per run |
| 无 mid-run 用户输入 | Workflow 运行中不能暂停等决策（权限弹窗除外） |
| 退出后重开 | 退出 Claude Code → workflow 重新开始 |
| 成本 | 多 agent = 多 token。先用小切片试跑 |
| 进度查看 | 会话内 `/workflows` 命令查看进度和 agent 详情 |

## 6. 目录职责

| 区域 | 放什么 | 不能放什么 |
|------|--------|-----------|
| `docs/iterations/<name>/` | 迭代计划（设计/配置/上游） | 执行证据、receipt |
| `tasks/active/<task_id>/` | 执行产物（outputs/receipts/results） | 计划草稿 |
| `WorkflowBase/registry/` | Registry 自身资产 | 执行记录 |
| `docs/templates/` | 模板（profile 分层） | 具体任务产物 |

## 7. 报告交付格式

1. **总报告** (`outputs/execution_report.md`) — 融合所有子文件
2. **摘要** (`summary/pm_runtime_summary.md`) — 总报告 ~1/8 压缩版

## 8. 阻塞必须上报

1. **必须通知 Owner**，说明什么被阻塞、为什么、有哪些替代选项
2. **不可自行选择替代方案并继续执行**
3. **不可在通知 Owner 之前继续推进后续 DAG 节点**

## 9. 关键 pitfall

### 观测产物优先于运行时状态（2026-06-02 纠正）

**"你根本不知道已经跑完了"** — 我多次依赖 progress.yaml 的 runtime_state 判断节点是否完成，但实际输出文件早已在磁盘上。

```text
错误做法：progress.yaml runtime_state: prompt_sent → "还在跑"
正确做法：ls outputs/ → 文件在盘 → 完成了（progress.yaml 可能是 stale 信号）
```

**核心原则：产出证据 > 运行时状态 > pane capture > heartbeat。** 多次教训：N10 被杀了两次但 `drift_check.py` 第一次就写完了；N11 被杀时 `scan_proposal.py` 和 `proposal_apply.py` 也已写完了。

- 产物证据（outputs/ 下的文件）是唯一可靠信号
- progress.yaml 的 runtime_state 可能停留在 prompt_sent 即使节点已跑完（artifact detection 未触发）
- pane capture 可能截断（timestamp 不更新）
- heartbeat 只表示 relay runner 进程活着，不表示节点状态

**每条 relay dispatch 后检查 completed 的标准做法：**
1. 检查 expected_output 对应文件是否在磁盘上存在且非空
2. 只有文件存在时才看 progress.yaml 确认
3. 文件存在但 progress 还是 prompt_sent → 就是完成了，artifact detection 没触发而已
4. **不要等声音通知** — heartbeat_monitor 是辅助层（检测 stale heartbeat + outputs 交叉验证），但可能失效（afplay 不响、monitor loop 不退出）。dispatch 后每 30 秒主动扫 `ls tasks/active/<task-id>/outputs/`
5. **heartbeat_monitor 作为第二层保障** — 在 `_finish()` 未触发时，heartbeat_monitor 通过检测 outputs 存在而 runtime_state 不更新来发现完成并播声音

### Hermes 的职责边界：设计/派发/验收，不落地（2026-06-02 纠正）

**"你不是负责落地的，你更新完设计之后是让 claude code 来落地的"**

当需要实现 DAG 系统组件时（如 execution_context 安全隔离、Repair Agent、output validation），Hermes 的职责是：

| 步骤 | 谁做 | 做什么 |
|------|------|--------|
| 1 | Hermes | 更新设计文档（v0.4 design doc → v0.4.1 amendment） |
| 2 | Hermes | 更新操作层 skill（dag-execution SKILL.md） |
| 3 | Hermes | 写 dispatch prompt，定义组件规格、输入材料、约束 |
| 4 | Hermes | 通过 relay runner dispatch 给 Claude Code（走 workflow 模式） |
| 5 | Claude Code | 实现具体代码，跑 compile check + 自测 |
| 6 | Hermes | 回收实现报告，验收实现质量 |

Hermes 不直接写 tools/pm_runtime/relay/ 下的代码（除非 Owner 正在积极监督且需要快速迭代）。功能性新组件的标准路径是 relay dispatch → Claude Code 实现 → Hermes 验收。例外：基础设施 bug 阻塞 relay dispatch 本身，或 Owner 明确说"你直接修"，Hermes 可以直接改。

### 设计文档权威性

始终以 `新一代DAG工作流设计文档_v0.4_emerged.md` 为权威设计源。

❌ **不要用这些替代：**
- `new_dag_workflow_design_synthesis_2026-05-26.md` — 会话合成稿，不是可执行的规范
- `B_line_lightweight_production_DAG_v0.3*` — 旧版 B 线设计，未被 v0.4 supersede？
- `v0.3.*` 系列文档 — 已被 v0.4 覆盖

✅ **同目录下 v0.4 关联文档：**
- `v0.4一致性检查报告_2026-05-29.md` — 验证报告

如果项目目录下没有 `新一代DAG工作流设计文档_v0.4_emerged.md`，去 `docs/skills/workflow_v4.0/` 下找最新版本。不要用同目录的其他合成稿替代。

### 迭代计划子文件夹模式

迭代计划的所有源材料应放在同一子文件夹下，以 `iteration-plan.md` 为索引入口：

```text
docs/iterations/<project>/<iteration-name>/
├── iteration-plan.md                         ← 主迭代计划（索引入口）
├── 新一代DAG工作流设计文档_v0.4_emerged.md     ← 权威设计文档（同目录）
└── <其他参考草案>.md                          ← 存档参考
```

**规则：** `iteration-plan.md` 中引用的同目录文件标注（同目录），不写绝对路径。

### Karpathy Coding Guidelines — 自然语言加载，不发 @skill

每个 coding 节点必须在 dispatch prompt 开头用自然语言要求加载 karpathy-coding：

```markdown
载入 karpathy-coding 行为准则。
```

karpathy-coding 是 Executor 原生 skill，通过 Skill tool 自动加载，不需要外部路径、不发 `@skill`、不发 `/karpathy-coding`（paste 机制不支持 slash 命令）。

非 coding 节点（baseline 扫描、fan-in 验收）不需要。

注意：`@skill` 在 Claude Code agent team 模式下是**文本注入，不是协议执行**。如果 prompt 在 `@skill karpathy-coding` 之后又用 `@agent` 显式定义了多个 agent，那些显式的 `@agent` 定义会覆盖 skill 内部的角色分工。正确做法：要么让 skill 管所有角色（prompt 里不写 `@agent`），要么精确对齐 prompt 里的 `@agent` 与 skill 定义。

### Relay runner config 常见错误

dispatch task_config.yaml 的常见配置错误：

| 错误 | 表现 | 修复 |
|------|------|------|
| `expected_outputs` 写了完整路径（如 `tasks/active/x/outputs/f.md`） | path 被 task_dir 重复拼接 → configuration_blocked | 改 `outputs/f.md`（相对 task_dir） |
| 缺少 `prompt_file` | configuration_blocked | 加 `executor_options.prompt_file: dispatch/prompt.md` |
| 缺少 `paths.task_dir` | config_invalid | 加 `paths.task_dir: tasks/active/<task_id>/` |
| 缺少 `task_domain` 或 `short_task` | config_invalid | 补全基础字段 |

DAG 不是默认模式。每个 DAG 节点的 task_config.yaml 必须声明 `dag_mode: true`。忘记写 = 安全隔离缺位。

### Ready timeout 不是死亡判据

Relay runner 的 `_ready_timeout()` 默认 30 秒（`ready_timeout_sec` 选项）。**Claude Code 启动 + remote mode 激活 + 模型加载可能超过 30 秒。**

旧行为（I5 bug）：ready timeout 到 → exit 5 退出，prompt 永远没发 → Claude 在 tmux 里空等。
修复后行为：ready timeout 到 → 打一行日志继续等 → Claude 就绪后自动发 prompt。

**如果 relay runner exit 5 且 progress.yaml 显示 `Claude ready prompt timeout`：**
- 检查 `tmux_executor.py` 的 `_monitor_loop_impl()` 中 ready timeout 是否还是硬退出
- 应该改为 warning+continue（2026-06-02 已修复，但代码可能被覆盖）

**其他 exit 5 原因排查：**
- `configuration_blocked` → 检查 task_config.yaml 字段完整性（见上表）
- `expected output outside task outputs directory` → expected_outputs 路径相对 task_dir
- `executor_options.expected_outputs is required` → 需要在 executor_options 下声明，不是顶层字段

### Fan-in 的部分失败

### Repair Agent 的生态位

**观察（2026-06-02 R1.0 狗食）：** Claude Code 在编码过程中自带自测→发现 bug→自修复循环（如写 output_validator 时测出 YAML 校验太宽松，自动修了）。这个能力比 Hermes 层 Repair Agent 的 output validation retry 更底层、更有效。Hermes Repair Agent 的 retry loop（文件缺失 → 重发同 prompt）在 Claude 已认定完成的情况下大概率无效。

**处理：** 挂起观察。如果在实际执行中 Repair Agent 被触发的次数 ≈ 0，砍掉 retry loop，只保留检测+escalate_to_owner。

### safety_context shell 注入检测过严

`safety_context.py` 初始的 Shell 注入白名单把 `||`（命令链）、`2>/dev/null`（stderr 静默）、`|`（管道）都当成注入拦截了。Claude Code 习惯用 `ls -la "path" 2>/dev/null || echo "not found"` 这种模式做错误处理，不是注入。

**修复（2026-06-02）：** 
- `||` 和 `|` 从注入列表中移除
- `>/dev/null` 和 `>&1` 模式允许
- 危险模式仅拦截 `;`、`&&`、`$(`、`` ` `` 和 `| sh`/`| bash` 等 eval 管道

**教训：安全隔离要在功能和约束之间找到平衡点。Claude Code 的正常工作流需要管道和错误处理。不要因为过于严格的 pattern 匹配堵住了正常用法。**

### 两套 bash 白名单不一致（2026-06-02 部分过时）

`tmux_executor.py` 的 DialogHandler 有独立硬编码 bash 白名单（仅 `mkdir`/`touch`/`cat`/`echo`/`printf`/`tee`），跟 `safety_context.py` 的默认白名单（`ls`/`cat`/`head`/`tail`/`python3`/`mkdir -p`）不一致。交集仅 `cat` 和 `mkdir`。

**2026-06-02 更新：** DialogHandler 已不再做 BASH_PERMISSION 的批准或 HOLD（见 §2.2），bash 权限对话框的主批准已移交给外部 dialog_watcher。watcher 不依赖白名单列表，而是纯模式匹配。但仍存在两套白名单的结构债务——长期方向是统一到 safety_context 引用。

### 杀任务前确认产出（2026-06-02 修正）

当认为一个 relay dispatch 跑偏了（白名单卡住、workflow 没触发、超时），**不要直接 kill session + 删目录重来**。先检查产出文件是否已经在盘上：

```text
ls tasks/active/<task_id>/outputs/     # 先看有没有产出
ls WorkflowBase/<目标路径>/                      # 再看脚本/代码写了没有
```

多次教训：N10 被杀了两次，但 `drift_check.py` 其实第一次就写完了；N11 被杀时 `scan_proposal.py` 和 `proposal_apply.py` 也已经写完了。

**规则：**
1. 先检查产出文件——可能在盘
2. 产出在了 -> 任务完成了，只是 relay 没检测到 -> 写 receipt 标记完成
3. 产出不在 -> 才考虑 kill session 重来
4. 重来前先确认根因（白名单/配置/权限/模型），不只是重试

部分失败时（continue 策略），必须标记哪些节点失败，不能静默跳过。验收报告的 `success_ratio` 必须 >= `min_success_ratio`（默认 0.75）。

### Repair Agent 轮次上限

重试不超过 2 轮。超出必须 `escalate_to_owner`，不能无限重试。

## References

- `docs/skills/workflow_v4.0/新一代DAG工作流设计文档_v0.4_emerged.md` — 权威设计文档（v0.4 定稿）
- `docs/skills/workflow_v4.0/v0.4一致性检查报告_2026-05-29.md` — v0.4 一致性验证
- pm-runtime — 中台运行索引（父 skill）
- pm-relay — 节点执行通过 relay 派发
- task-repair — Repair Agent 具体操作
- pm-relay-dialog — 弹窗处理规则
- `references/relay-executor-prompt-contract.md` — Relay → Executor Prompt Contract v0.2（Hermes × Claude Code 沟通协议）
- `scripts/dialog_watcher.py` — 权限对话框自动批准监视器（dispatch SOP 必启，位于 tools/dialog_watcher.py）
- `scripts/heartbeat_monitor.py` — 心跳监视器（dispatch SOP 必启，位于 tools/heartbeat_monitor.py），检测 stale heartbeat + outputs 交叉验证，任务终止时播通知音
- `scripts/tmux-session-gate.py` — tmux session 清理门（dispatch 前运行），只保留对应活跃任务的 session（位于 ~/.hermes/scripts/tmux-session-gate.py）
- `references/claude-code-workflow-pattern.md` — Claude Code Dynamic Workflow 使用模式（R1.0 验证）
- `references/fan-in-vs-reality-review.md` — Fan-in 验收与 Reality Review 的职责边界（R1.0 确立）
- `references/multi-goal-repair-rounds-pattern.md` — 多 Goal DAG + 跨 Goal repair 轮模式（R1.0 验证）
- `references/multi-goal-dag-dogfood-pattern.md` — 大狗食模式：基础设施 + 消费者 + 系统修复 + 独立审查

## Changelog

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-02 | v0.8 | DAG 节点 dispatch SOP 定型：prompt 结构 + 三种 execution_mode + dialog_watcher + 声音通知 + observer 超时覆盖 |
| 2026-06-02 | v0.8.1 | 弹窗处理 §2.2 更新（watcher 主批 + DialogHandler fallback）；References 新增 heartbeat_monitor/tmux-session-gate；"两套 bash 白名单" pitfall 过时标注 |
