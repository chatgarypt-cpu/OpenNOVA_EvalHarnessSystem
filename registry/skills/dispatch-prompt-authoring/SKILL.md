---
name: dispatch-prompt-authoring
description: "Dispatch Prompt 编写规范 — 基于 v0.2 Relay→Executor Prompt Contract + 2026-06-02 R1.0 狗食经验沉淀。每次写 dispatch prompt 前加载此 skill。"
version: 0.3.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, dispatch, prompt, contract]
---

# Dispatch Prompt Authoring Skill v0.2.0

> 经验来源：2026-06-02 Registry R1.0 狗食（Gary 反复纠正后定型）
> 关联契约：`references/relay-executor-prompt-contract-v0.2.md`
> 定位：独立于 pm-runtime 的 prompt 编写规范，不集成进 pm-runtime 主体

---

## 0. 什么时候加载

每次**创建或修改** relay dispatch 的 prompt.md 时，必须先加载本 skill。

---

## 1. Prompt 写法规则

### 1.1 核心原则

| 规则 | 说明 |
|------|------|
| 用自然语言，不要写 slash 命令 | relay paste 机制把 `/anything` 当文本粘，不是命令 |
| 任务目标 1-3 句 | 不要长篇大论，不要混入元讨论 |
| 明确"做什么"和"输出什么" | "完成后写 outputs/xxx.md" |
| 不要用"完成后"单独成句 | "先读所有文件。完成后写报告" → Claude 跳过执行直接写报告 |
| 重构任务：每种变化一个方法 | 涉及代码拆分的 prompt 必须要求每个抽出方法只负责一种变化原因（SRP）。如果某方法太大（>30 行），继续拆分 |
| 代码修改：必须带烟雾验证 | prompt 末尾必须有可执行的验证命令（编译检查 + 导入检查 + 简单烟雾运行），Claude 必须执行通过才算完成 |
| Codex 路径必须精确到 cwd 出发 | "在 outputs/ 下创建" -> Codex 在 cwd（workyb 根）创建，不在 task_dir 下 | 路径用 cwd 出发的相对路径：`tasks/active/xxx/outputs/file.txt` |

### 1.2 三种执行模式的 Prompt 写法

#### direct（默认，最常用）

直接写任务描述，不需要任何前缀或特殊格式。

```
将三个组件集成到 relay runner 中：
1. safety_context.py → 接入 tmux_executor
2. output_validator.py → 接入完成检测
3. Repair Agent → 回调绑定到 tmux paste

集成完成后写 outputs/xxx.md。
```

#### agent team（多 Agent 审查）

prompt 中引用 `@agent-name`，不需要额外前缀。agent 定义文件在 `~/.claude/agents/` 下。

```
@agent registry-file-mapper 检查文件结构
@agent yaml-schema-consistency 检查字段合规
```

#### workflow（⛔ 必须 Owner 显式确认后使用）

prompt 开头写 `"use a workflow to: [描述]"` 是确定性的触发方式。

```
use a workflow to: check the entire v0.4.1 system implementation quality
```

验证方法：检查 `workflows/scripts/*.js` 是否存在，或通过 `/workflows` 查看进度。

### 1.3 避坑清单

| 之前踩过的坑 | 错误写法 | 正确写法 |
|-------------|---------|---------|
| workflow 不触发 | 在 prompt 里写 `execution_mode: workflow` | "用 workflow 执行：[描述]" |
| 只分析不执行 | "先读所有文件。完成后写报告" | "通过 workflow 将三个组件集成。集成完写报告" |
| karpathy 没加载 | 写 `/karpathy-code` 等 slash 命令 | "载入 karpathy-coding 行为准则" |
| 契约讨论 | 把 feedback/contract 内容写进 prompt | prompt 只写任务，不写元讨论 |
| 重构不设烟雾验证 | prompt 没写验证命令 -> Claude 可能说"改好了"但代码编译不过 | prompt 末尾写可执行的编译检查 + 导入检查 + 烟雾运行命令 |
| Codex 路径理解偏差 | "在 outputs/ 下创建文件" -> Codex 在 cwd（Adarian 根）下创建 Adarian/outputs/，不是 task_dir/outputs/ | 路径写成相对于 task_dir 的绝对路径，或在 prompt 中用 `tasks/active/xxx/outputs/` 显式指定 |
| expected_outputs 不在 outputs/ 目录下 | executor 验证预期产出必须在 outputs/ 下，summary/summary.md 会触发 HOLD | 所有 expected_outputs 路径必须在 outputs/ 下，不要引用 outputs/ 外的目录 |

---

## 2. task_config.yaml 必填项

```yaml
observer_mode: true
observer_attach: terminal_window
executor_options:
  expected_outputs:
    - outputs/<产出文件名>
  prompt_file: dispatch/prompt.md
```

### 禁止：runtime_control

```yaml
# ❌ 不要写 runtime_control 节
runtime_control:
  emergency_max_wall_time_sec: 600
  no_output_timeout_sec: 120

# ✅ 不写 runtime_control = 全走 executor 默认行为
#     session 活着就不停，heartbeat 持续写
#     下游 heartbeat_monitor.py 负责检测心跳冻结
```

**规则：只要 tmux session 还在、heartbeat 还在更新，executor 就不 timeout。**
没有 idle timeout、没有 wall clock timeout。task_config.yaml 中不应出现 runtime_control 节。

### 硬门：禁止手动创建 tmux session

所有 Claude Code 派发任务必须走 relay runner dispatch + dispatch-gate.py。**禁止手动创建 tmux session**，除非满足以下所有条件：

1. relay runner 以失败退出（无法 dispatch）
2. 已向 Owner 上报 root cause
3. Owner 明确批准 fallback 方案

三种执行模式（direct/agent-team/workflow）都受此规则约束。违反 = 协议违规。

确认方法：每次 dispatch 前检查是否走到 `relay init → relay run` 流程。如果跳过 relay 直接 `tmux new-session`，先停手，上报。

### 声音通知

已由 `relay_runner.py` 默认开启（`run_task()` 中自动设置 `enable_sound_notification: True`），不需要手动配置。如需禁用，设置 `executor_options.enable_sound_notification: false`。

---

## 3. Dispatch SOP（执行顺序）

每步验证状态，不跳不赶。

### 先落盘，再 dispatch（硬门）

迭代计划或任务设计文档在 dispatch 前必须先落盘到正确的资产目录。
落盘 = 文件保存到 `docs/iterations/` 对应目录下，并可被后续引用。
没有落盘的文档不能作为 dispatch 依据。

```text
❌ 错：用聊天中的计划直接 dispatch
✅ 对：先把计划存档到docs/iterations/，再基于存档 dispatch
```

违反此规则被 Gary 明确纠正过："你应该先迁移过去，落盘，然后再去做dispatch。"

### Dispatch SOP

```text
0. [落盘] 迭代计划/设计文档先存档到 docs/iterations/ 对应目录
1. [准备] 加载本 skill，按 §1 写 prompt.md
2. [准备] 写 task_config.yaml + task_status.yaml + task_brief.md
3. [准备] 运行 tmux-session-gate.py 清理 orphan session
   ```bash
   python3 ~/.hermes/scripts/tmux-session-gate.py --apply
   ```
   只保留对应 tasks/active/ 下活跃任务的 tmux session。已归档任务的 session 是 orphan，必须杀掉。
4. [启动] relay init + relay run
5. [监视] 立即启动 dialog_watcher.py + heartbeat_monitor.py（背景进程）
   ```bash
   python3 tools/dialog_watcher.py <tmux-session-id>
   python3 tools/heartbeat_monitor.py tasks/active/<task-id>
   ```
   两个监视器互补：dialog_watcher 在运行中处理权限弹窗，heartbeat_monitor 在结束时播放通知音。
   Terminal 窗口由 relay 的 observer_attach 自动打开，不需要手动补
6. [主动轮询] 每 30 秒检查 outputs/ 目录，不依赖系统声音通知
   ```bash
   ls tasks/active/<task_id>/outputs/
   ```
   声音通知不可靠（afplay 可能被系统吞掉、你不在场、或 artifact detector 未触发 completion 导致 monitor loop 不退出）。**文件在盘上 = 任务完成，不等声音。**
   一旦 outputs/ 下出现 expected output 且 size > 0，立即进入步骤 7。
7. [汇报] 口头告诉 Gary 完成状态
   - 产出文件路径
   - 完成的修改列表（几句话摘要）
   - 是否需要 closeout 或你有其他意见
```

### dialog_watcher 启动

relay dispatch 后**立即**启动（不要等到 Claude 就绪再开）：

```bash
python3 tools/dialog_watcher.py <tmux-session-id>
```

参数 `tmux-session-id` 从 `runtime/session.yaml` 的 `tmux_session_id` 字段获取。

**设计（2026-06-02）：** dialog_watcher 是权限对话框的**主批准机制**。ClaudeDialogHandler 对于 BASH_PERMISSION 不再 HOLD，改为 defer 给 watcher。watcher 每 0.5 秒扫描 pane 做模式匹配：

- **普通对话框**（"Do you want to proceed?"、"Do you want to create"、"Do you want to overwrite"）→ 自动按 Enter（选 Yes/Proceed）
- **Workflow auto-mode 对话框**（"switch to auto mode"）→ **不自动批准**。播放 Glass 提示音，打印通知到 stdout。需要你在这里（Hermes CLI）确认后，我手动通过 tmux send-keys 发送选择

如果 watcher 也匹配不到（罕见），DialogHandler 兜底——但不会 HOLD，仅记录日志。最终由 observer 窗口的人接管。

### 汇报 SOP

dispatch 后必须主动汇报，不等 Gary 问：

| 事件 | 汇报格式 |
|------|---------|
| relay 启动成功 | "Nxx relay dispatched，Terminal 窗口弹出" |
| 产出现 | "Nxx 跑完了，产出在 outputs/report.md" |
| exit 5 卡住 | "Nxx exit 5，卡在 ZZ 对话框。session 保留，你可以在 Terminal 窗口处理" |
| 完成 | "Goal 3 完成。结果：X 成功 / Y 失败 / Z carryover" |
脚本路径：`tools/dialog_watcher.py`（workyb 项目内）。

`dialog_watcher.py` 位于 `~/.hermes/skills/pm-runtime/dispatch-prompt-authoring/scripts/dialog_watcher.py`。也可从 workyb 项目路径调用 `tools/dialog_watcher.py`。

覆盖三种对话框类型：`"Do you want to proceed?"`（文件读写权限）、`"Do you want to create"`（创建新文件）、`"Do you want to overwrite"`（覆盖已有文件）。

**原因：** relay runner 的 ClaudeDialogHandler 对某些 bash permission 对话框无法自动批准（如 `find`、`ls` 等只读命令被拦，以及 `overwrite` 类型不在其检测列表）。dialog_watcher 每 2 秒扫描 pane 文本做简单字符串匹配，补上 relay 的缺口。

### relay exit 5 处理

exit 5 ≠ 任务失败。表示 relay runner 遇到无法自动处理的对话框，进入 HOLD 状态 → session 保留 → relay 退出 → 等人接管。检查 pane capture 确认对话框类型，手动处理或等待 watcher 捕获。

### 声称完成前检查证据

不要只看 progress.yaml 的 runtime_state 就声明完成。先检查 outputs/ 目录下文件是否存在、非空。Gary 对此非常敏感："你根本不知道已经跑完了"。

### 声音通知（自动）

relay_runner.py 的 `run_task()` 已默认开启 `enable_sound_notification: true` 和 `notification_sound: "Glass"`，不需要在 task_config.yaml 中手动配置。如需要静音，在 config 中显式设置 `enable_sound_notification: false`。

---

## 4. 三种执行模式选择树

```
execution_mode 三值枚举（取代 dag_mode 布尔值）：

任务需要多个独立视角同时看同一份输入？
  ├─ Yes → execution_mode: agent-team（@agent + synthesis）
  │        审查类任务：Reality Review、代码审查
  │        触发：prompt 引用 @agent-name（定义文件在 ~/.claude/agents/）
  │        安全级：中。不加载 v0.4.1 三组件
  │
  └─ No → 需要编排多个 agent 且有内部依赖？
           ├─ Yes → execution_mode: workflow（JS 脚本）
           │        多编码任务并行、系统验证
           │        触发：prompt 开头写 "use a workflow to:"
           │        安全级：高。加载 safety_context + output_validator + Repair Agent
           │        需 Owner 显式确认后才可派发
           │
           └─ No → execution_mode: direct（默认）
                   基线扫描、单步编码、脚本实现
                   触发：什么都不用做，relay_runner 默认 direct
                   安全级：低。不加载 v0.4.1 三组件
```

### 隔离规则

v0.4.1 三组件（safety_context / output_validator / Repair Agent）只在 `execution_mode: workflow` 时加载。
`direct` 和 `agent-team` 不走这三道安全校验，用原生 Claude Code 行为。

---

## 5. 与其他 skill 的关系

| Skill | 关系 |
|-------|------|
| `pm-runtime` | 父索引层。本 skill 不集成进 pm-runtime 主体 |
| `dag-execution` | dag-execution 负责 DAG 节点执行流程。本 skill 负责写 dispatch prompt |
| `pm-relay` | 派发的具体 relay runner 配置和 executor 选项 |
| `relay-executor-prompt-contract.md` | 本 skill 的契约参照。contract 描述 Executor 期望的格式，本 skill 给出实际能跑的写法 |
| `references/three-mode-selection-guide.md` | 三种执行模式（direct/agent-team/workflow）的选择树和 prompt 写法 |
| `codex` | Codex 特有工程细节（stdin hang、auth、proxy），本 skill 只管执行模式和 prompt 结构 |
| `task-directory-canonical` | 前置依赖：gate 通过后先由此 skill 建目录，本 skill 再写 prompt |

---

## 6. 完整触发链（gate → skill → mode select → dispatch）

```text
1. [trigger] promotion-gate hook 自动触发（扫描 Downloads → 落盘 → 审查）
   输出: recommended_next_skill: task-directory-canonical

2. [dirs] task-directory-canonical skill
   加载此 skill → 创建 tasks/active/<task-id>/ 目录结构

3. [prompt] dispatch-prompt-authoring skill（本 skill）
   根据任务性质选三种执行模式之一：
   ├─ direct（默认）：单 executor 单步
   ├─ agent-team：多 Agent 并行审查
   └─ workflow（DAG）：多节点有依赖链，需 Owner 显式确认才可派发

4. [dispatch] relay runner dispatch
   如果选 codex → 还需加载 codex skill 处理工程细节
```

详细设计参考：`references/gate-skill-dispatch-trigger-chain.md`

### 三种模式选择依据

| 模式 | 适用场景 | 推荐执行器 | Owner 确认 |
|------|---------|-----------|-----------|
| direct | 单文件修改、单步执行、asset copy | Codex（首选）/ Claude Code | 不需要 |
| agent-team | 审查类（Reality Review、代码审查） | Claude Code（Codex 不支持） | 不需要 |
| workflow | DAG 有依赖链（Go1 的 6 节点串行） | Claude Code（Codex 不支持） | **必须 Owner 显式确认** |

> 执行器选择不是死绑定。Codex 额度用尽时，Claude Code 可以直接走 direct 模式写代码。
> Claude Code 不适合的单步任务（如需要严格文件边界），Codex 走 direct 模式。

### workflow 节点派发时的额外规则

```text
1. 非默认模式，必须 Owner 显式说"可以"后才能 dispatch
2. Owner 确认后才能写 prompt 中的 "use a workflow to:" 触发句
3. 不得默认走 workflow，即使计划中定义了 DAG 节点
4. Owner 确认后，prompt 必须以 "use a workflow to:" 开头
```

---

## 7. Changelog

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-02 | v0.1 | 初版 |
| 2026-06-02 | v0.3 | dialog/heartbeat_monitor/tools_config |
| 2026-06-03 | v0.4 | Codex 路径 pitfall |
| 2026-06-03 | v0.5 | 新增 §6 完整触发链（gate → skill → mode select → dispatch），添加 workflow 确认规则和 codex skill 引用；新增 task-directory-canonical 前置依赖说明 |

---

## 8. Pitfalls（2026-06-02 积累）

### 杀 task 前先检查产出

当认为 relay dispatch 跑偏时，先 `ls outputs/` 和 `ls WorkflowBase/<目标路径>/` 检查产出是否已在盘。多次教训：N10 和 N11 被杀时脚本已写完。**杀重来是最后手段，不是第一反应。**

### 不确定的命令先查再发

不知道 `/karpathycode` 还是 `/karpathy-coding`？先查：
- `ls ~/.claude/commands/` — 看注册的 slash 命令
- `ls ~/.claude/skills/` — 看 skill 名
- `grep -r "karpathy" ~/.claude/` — 搜已有记录

猜了再发会被 Gary 指出"你没有核对真实名字"。

### 不凭感觉判断完成状态

`progress.yaml` 的 `runtime_state` 可能停留在 `prompt_sent` 即使节点已跑完（artifact detection 未触发）。**以 outputs/ 目录下的文件存在性为准。**

### 按完对话框后继续监视

批一个对话框不代表后面不再有。watcher 必须持续运行（每 2 秒扫描）。新增的对话框类型（如 overwrite）需要补进 PATTERNS 列表。

### 声音通知不可靠，不要等它

`SoundNotifier.notify()` 只在 `_finish()` 调用时触发，而 `_finish()` 只在 monitor loop 达到 terminal state 时执行。如果 artifact detector 未正确检测到产出文件（`exists: false` 但文件实际在盘），monitor loop 永不退出，声音永不播放。

**正确做法：** dispatch 后每 30 秒主动 `ls outputs/`。文件在盘 = 任务完成。不等 notify，不等 relay exit，自觉口头告诉 Gary。

### Codex 短任务不输出完成标记

Codex 的 `[codex-agent: Session complete` 和 `Token usage: total=...` 完成标记只出现在长会话/多操作任务结束时。对于"写一个文件就退出"的简单任务，Codex 直接回到 `›` prompt，不输出任何完成标记。

影响：如果 prompt 只让 Codex 做一件小事（写一个文件、改一个配置），executor 的完成标记检测不会命中。此时只能依赖 expected_outputs 检测或空闲超时兜底。

缓解：短任务的 expected_outputs 必须精确指向 Codex 实际会创建的路径（用 `tasks/active/xxx/outputs/` 而不是模糊的 `outputs/`）。<table></table>

### promotion-gate：A 线两阶段迁移任务必须先过 gate

涉及 A 线资产迁移、B→A promotion 等两阶段任务（Go1 只读对账 → Go2 受控迁移），dispatch 前必须先跑 `promotion-gate.py` 验证阶段正确性：

```bash
python3 ~/.hermes/scripts/promotion-gate.py <go1|go2> [--plan-dir PATH]
```

gate 检查：迭代计划落盘、阶段正确、无冲突任务活跃、任务目录完整、Owner 批准。
gate 不通过 → 不允许 dispatch。违反此规则被 Gary 明确纠正过。

### 主动汇报不要等被问

Gary 今天明确指出的问题：Task A 的产出文件已经在盘上了，但我既没告诉他，系统声音也没响。他说"为什么你没有通知我？"。

规则：产出文件一出现在 outputs/，立即口头通知。不等 Gary 问，不等声音，不等 relay 退出。形式是几句话的摘要（改了哪些文件、做了什么改动），不是详细报告。

### Go3 Reality Review 是 Go2 后的强制门，不做等于盲迁

2026-06-03 Go3 验证发现：18 项 Go2 声明已迁移的资产，只有 10 项（55.6%）在 A 线真实可用。剩余的 registry 指向了不存在的 runtime——`claude/` executor、`plugins/`、`path_resolver`、`memory_governance/` 等均缺失。

Go3 必须用 Claude Code agent team（5 agent：registry-file-mapper / yaml-schema-consistency / capability-authenticity / boundary-risk / registry-synthesis），不得用单 agent 替代。

根因：registry YAML 是 B 线的运行时快照，YAML 文件本身不验证 A 线是否就绪。Go2 复制了文件没验证运行态。

详见 `references/b-to-a-promotion-pattern.md`（已更新为三阶段 Gate 模式）。

### dialog_watcher 的 auto_mode_notified 去重

dialog_watcher.py 检测到 workflow auto-mode 后，播一次 auto_mode_sound 并每秒轮询。如果不去重，`auto mode on` 常驻 tmux pane 文本，watcher 每 0.5s 播一次音效。已加入 `auto_mode_notified` 布尔标记（2026-06-03）。

修复位置：`tools/dialog_watcher.py` 的 main() 函数。如果遇到"一直播声音"的问题，检查 `auto_mode_notified` 标记是否存在。

### 加载对应 skill 再写文档

创建执行报告前先加载 `phase-retrospective` skill。closeout 前加载 `closeout-gate` skill。不要自由发挥。

### heartbeat_monitor 是必须启动的 companion 进程

每次 dispatch 后，除了 dialog_watcher，**必须同时启动 heartbeat_monitor**：

```bash
python3 tools/heartbeat_monitor.py tasks/active/<task-id>
```

这个脚本检测三种完成信号（terminal state transition / stale heartbeat / outputs 在盘但 runtime_state 说 running），任何一种触发就播 Glass 声音。解决了 artifact detector 的盲区问题（见 pm-relay §7.3）。

如果只启动了 dialog_watcher 没启动 heartbeat_monitor → 任务完成时可能没声音通知 → Owner 不知道进度。这不是可选步骤。

### tools_config.yaml 是工具的外置配置区

`tools/tools_config.yaml` 集中管理所有 tools/ 脚本的可调参数：

```yaml
heartbeat_monitor:
  sound: "Glass"              # 系统声音名，或 tools/sounds/ 下的 .mp3/.wav
  stale_threshold: 5          # 心跳停多久开始检查（秒）
  stale_confirm: 10           # 二次确认阈值
dialog_watcher:
  auto_mode_sound: "Glass"
  poll_interval: 0.5
```

自定义声音放 `tools/sounds/`，afplay 支持 AIFF / WAV / MP3 / AAC / M4A。
不需要改代码，改配置即可换声音。

### 不要囤积 tmux 孤儿进程

每个已归档的任务在 tmux 中可能残留 session。dispatch 新任务前必须清理它们：`python3 ~/.hermes/scripts/tmux-session-gate.py --apply`。只有 tasks/active/ 下的任务有权保留 tmux session。违反会导致 tmux 进程膨胀到不可控。

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-02 | v0.1 | 初版，基于 R1.0 狗食经验建立。含 prompt 结构规范、三种执行模式选择树、dispatch SOP、dialog_watcher 启动步骤、relay-executor-prompt-contract v0.2 参考 |
| 2026-06-02 | v0.1.1 | 追加：三种模式选择树、workflow 触发方式「use a workflow to:」（已验证 ✅）、dialog_watcher 原因说明和 SOP 固化、contract 参考文件 |
| 2026-06-02 | v0.1.1 | 三种执行模式选择指南（`references/three-mode-selection-guide.md`）；`dag_execution_mode` 取代 `dag_mode`
| 2026-06-02 | v0.3 | 2026-06-02 会话：dialog 架构重设计（watcher 主批 + DialogHandler fallback）；新增 heartbeat_monitor 作为必启 companion；新增 tools_config.yaml 外置配置区；SOP step 6 改为主动轮询 outputs/，不等声音通知；新增 pitfall「声音通知不可靠，不要等它」「主动汇报不要等被问」「tools_config.yaml 是工具的外置配置区」；reference 新增 `dialog-approval-architecture-2026-06-02.md` |
| 2026-06-03 | v0.4 | 新增 Codex 路径理解偏差 pitfall；reference 新增 `codex-dogfood-monitoring-findings.md`（短任务完成标记不覆盖、prompt 路径精确性教训） |
| 2026-06-03 | v0.5 | §3 SOP 新增「先落盘再dispatch」硬门（第 0 步）；新增 pitfall「Codex 短任务不输出完成标记」；新增 pitfall「promotion-gate 引用」；version bump |

## References

- `references/codex-dogfood-monitoring-findings.md` — 短任务完成标记不覆盖、prompt 路径精确性教训
- `references/b-to-a-promotion-pattern.md` — B→A 资产 Promotion 两阶段 Gate 模式（Go1/Go2 对账→迁移）
