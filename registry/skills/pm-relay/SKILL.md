---
name: pm-relay
description: "中台派发引擎 — Relay Runner 执行器架构、派发流程、observer 模式、CC Switch 路由、thinking-fixer。派发 relay 任务时加载。权威来源：pm-runtime。"
version: 0.1.3
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, relay, dispatch, executor]
---

# PM Relay Skill v0.1.1

> 权威来源：pm-runtime
> 加载时机：派发 relay 任务、处理 executor 问题、遇到对话框/权限弹窗时

---

## 0. 什么时候加载

1. **创建 relay dispatch** — 准备派发任务给 claude 或 codex executor
2. **启动 relay runner** — 用 relay init / relay run 启动任务
3. **处理 executor 对话框** — 遇到权限弹窗、文件创建确认等
4. **配置 observer / terminal 窗口** — 需要让任务执行过程可见
5. **处理超时/心跳问题** — executor 卡住、no_output_timeout 触发
6. **重跑 relay 任务** — 以不同参数重新执行

## 1. 核心定位

Relay runner 是**监听和通讯层**，不是智能执行器。它只管创建/接管 executor session、发 prompt、轮询输出、检测产物、写心跳、出错 HOLD。

不负责：替 executor 做判断、改业务文件、判断 closeout。

## 2. task_config.yaml 必填字段

dispatch 的 task_config.yaml 必须包含以下字段，缺失任何一项 relay runner 会报 `configuration_blocked`（exit 5）。

### 2.1 基础字段

```yaml
task_id: <唯一标识>
task_title: <人类可读标题>
task_domain: review | dogfood | smoke | inventory | demo | coursework
short_task: <与 task_id 一致>
task_type: review | pipeline | validation | experiment
executor: claude | codex
executor_type: claude | codex
execution_mode: tmux_interactive | managed_subprocess
observer_mode: true
observer_attach: terminal_window
paths:
  task_dir: tasks/active/<task_id>/
```

### 2.2 executor_options — 必须包含

```yaml
executor_options:
  expected_outputs:
    - outputs/<文件名>      # 相对 task_dir 的路径，不要写完整路径
  prompt_file: dispatch/prompt.md   # 相对 task_dir
```

**常见错误：**
- ❌ `expected_outputs: tasks/active/x/outputs/report.md` — 会被 task_dir 拼接成 `/task_dir/tasks/active/...`（路径重复）
- ✅ `expected_outputs: outputs/report.md` — 正确
- ❌ 缺少 `prompt_file` → `executor_options.prompt_file or executor_options.prompt is required`
- ❌ 缺少 `expected_outputs` → `executor_options.expected_outputs is required`
- ❌ `expected_outputs` 含 `summary/summary.md` → executor 的路径校验会拒绝"outputs 目录外的路径"，进入 HOLD 状态。**所有预期产出必须放在 outputs/ 目录下**，包括 summary。

### 2.3 可选字段

```yaml
iteration_plan_path: docs/iterations/<plan>/<file>.md
node_id: <node_id>
node_goal: <目标描述>
dag_execution_mode: workflow     # direct | workflow | agent-team（取代 dag_mode 布尔值）
                                   # 默认 direct（由 relay_runner 自动设置）
                                   # workflow 模式自动启用 §5.3 安全隔离 + output validation + Repair Agent
execution_profile: standard | smoke | full_dag
executor_options:
  codex_bypass_approvals: true
```

### 2.4 声音通知

声音通知由 relay_runner.py 的 `run_task()` 自动启用（`enable_sound_notification: True`, `notification_sound: "Glass"`），不需要在 task_config.yaml 中声明。如需禁用，显式设置 `enable_sound_notification: false`。

## 3. 双执行器模式

### 3.1 Claude Tmux Executor

`executor_type: claude` + `execution_mode: tmux_interactive`

支持交互式 TUI（@agent 多 agent 并行审查）、observer_mode: observer_attach=terminal_window、macOS afplay 通知。

### 3.2 Codex Subprocess Executor

`executor_type: codex` + `execution_mode: managed_subprocess`

- stdin=DEVNULL、JSONL 输出、auth 错误自动分类
- bypass flag 可配置（默认 true）
- 当前限制：不支持 observer

### 3.3 注册式架构

```python
register_executor("codex", CodexExecutor)
get_executor("codex")
```

## 4. Dispatch Gate

**所有派发必须走 relay runner。** 禁止手动创建 tmux。

强制执行：`dispatch-gate.py`

## 5. Observer 模式与超时策略

### 5.1 Observer 模式

默认 `observer_mode: true` + `observer_attach: terminal_window`，弹 Terminal 窗口 attach tmux session。对话框你直接在窗口里手动处理。

### 5.2 超时策略（2026-06-03 重写 — 去静态化）

**核心原则：只要 session 还在、heartbeat 还在更新，executor 就不 timeout。**

所有静态超时值（`no_output_timeout_sec`、`emergency_max_wall_time_sec`、`ready_timeout`）已从监控循环中移除。executor 只检查：

| 信号 | 行为 |
|------|------|
| tmux session 存活 | session 活着 = 继续，session 消失 = 返回 |
| heartbeat 持续更新 | 下游 `heartbeat_monitor.py` 检测心跳冻结，负责通知 |
| expected_outputs 出现 | 任务完成，返回 |
| 完成标记（`[Session complete` / Token usage） | 任务完成，返回 |
| Agent 回到 prompt + 有产出 | 任务完成，返回 |

**不再做的事：**
- 不设 idle timeout（pane 不动 X 秒 → 超时）
- 不设 wall clock timeout（总运行 X 秒 → 超时）
- 不设 ready timeout（Agent 未在 X 秒内就绪 → 超时）

**替代方案：**
- `heartbeat_monitor.py` 作为独立守护进程运行，检测 heartbeat.json 冻结
- 两级确认：5 秒无心跳 → 检查 outputs/ 目录 → 10 秒仍无心跳 → 播报声音
- executor 的监控循环只返回正常完成信号，永远不主动杀死 session

**技术实现（2026-06-03 修复）：**
- `codex/tmux_executor.py` 的 `_monitor_execution()` 中已移除 `_max_idle_sec` 和 `_max_wall_time` 的所有检查代码
- task_config.yaml 中不再需要 `runtime_control` 节（已在 dispatch-prompt-authoring §2 中明确禁止）

### 5.3 Prompt 发送流程

```text
Claude 启动
  → monitor loop 等待 runtime_state = "waiting_for_input" / "waiting_for_ready"
  → 激活 remote mode（/clauderemote on）
  → remote_mode_sent = True
  → 检测到 runtime_state + remote_mode_sent + 未发 prompt
  → _send_prompt()（通过 tmux paste-buffer 注入 prompt.md）
  → prompt_sent = True
  → 写入 task_state: prompt_sent
  → 继续监控完成检测
```

**常见问题排查：** 如果 tmux 窗口弹出来了但 Claude 在空等（无 prompt 输出），检查：
1. progress.yaml 的 `runtime_state` 是什么
2. `result.json` 的 `classification` 是否为 `configuration_blocked`
3. 如果是 I5 旧代码：`ready_timeout` 硬退出问题（需更新 tmux_executor.py）
4. 如果是 config 错误：检查 task_config.yaml 字段完整性

## 6. Cleanup 所有权

每个 run 生成唯一 run_id。cleanup 绑定所有权，旧 cleanup 不会误杀新 run。

**禁止用静态 timeout 作为默认修复方式。** timeout 到达时检查 session 存活、heartbeat 更新、pane 状态。agent 活跃就重置 timer。

## 7. 完成检测与 artifact staleness

### 7.1 Artifact Completion 规则

- **Artifact Completion > Pane Runtime State** — 产物连续存在 2 轮即完成
- Basename 子串匹配解决 tmux 折行截断
- ERROR 检测限制最近 50 行，一次性错误不消耗 retry

### 7.2 Artifact staleness pitfall（已知问题）

当 relay runner 由 Hermes 的 `terminal()` 工具启动（前台模式，timeout=180s）时，**父进程退出 ≠ tmux session 死亡**。Claude 继续在 tmux 中工作并产出文件，但 monitor loop 已停止轮询，`progress.yaml` 永远停在 `prompt_sent` 状态，即使 `outputs/baseline_report.md`（17KB）已正常写入磁盘。

**表现：**
- progress.yaml: `runtime_state: prompt_sent`, `expected_outputs.exists: false`
- 但 `ls outputs/` 显示文件已存在且非空
- tmux session 存活，Claude 已完成工作（`capture-pane` 可见 receipt）

**根因：**
monitor loop 运行在 relay runner 的子线程中。当 `run_task()` 的前台 caller（Hermes terminal）超时退出后，子线程不再被调度，artifact 检测不会执行。

**当前处理方式：**
- 手动验证 outputs/ 下的文件存在且非空（`head -5 outputs/*.md`）
- 手动写 node_receipt（`classification: agent_completed`）
- 或在 tmux 内确认 Claude 已打印 ARTIFACT_WRITE_RECEIPT

**长期修复方向：**
- relay runner 应独立于 caller 进程运行（daemonize），或
- monitor loop 使用独立的定时器线程不受 caller 生命周期影响

### 7.3 Heartbeat Monitor — 自动检测完成（2026-06-02 新增）

心跳监视器（`tools/heartbeat_monitor.py`）是与 relay dispatch 并行运行的 companion 进程，独立于 monitor loop 检测任务完成。

**检测信号（三个条件任一触发即播放 Glass 提示音）：**

| # | 信号 | 场景 | 原理 |
|---|------|------|------|
| 1 | runtime_state 进入 terminal 状态 | 正常完成/失败 | heartbeat.json 的 runtime_state ∈ {executor_completed, hold, timeout, error, session_lost} |
| 2 | heartbeat 超过 20s 未更新 | relay 进程挂了 | monitor loop 停止写心跳 → stale → play sound |
| 3 | outputs/ 有文件但 heartbeat 说 running | artifact detector 盲区（monitor loop 活着但没检测到完成） | 文件存在性 vs runtime_state 矛盾 → play sound |

**Signal 3 解决了 §7.2 描述的 artifact staleness 问题：** 当 monitor loop 停止轮询但 Claude 已经写完产出时，心跳监视器通过 cross-check outputs/ 目录触发通知。

**启动方式（与 dialog_watcher 并列）：**

```bash
python3 tools/heartbeat_monitor.py tasks/active/<task-id>
```

**与 dialog_watcher 的关系：**
- dialog_watcher = 运行中的权限弹窗处理
- heartbeat_monitor = 结束时的完成通知
- 两者互补，都在 dispatch 后立即启动

### 7.4 Executor 监控可靠性设计原则（2026-06-03 修复沉淀）

在设计和维护 executor 的监控循环（`_monitor_execution()`）时，必须遵守以下原则。违反这些原则会导致 Codex 做完工作了但是 executor 还在空等（自食用测试教训）。

**原则 0：完成标记是局部可靠的，不是万能的**

对于多步骤/长时间 Codex 会话，Codex 在结束时输出 `[codex-agent: Session complete` 和 Token usage 行。但对于简单的单文件创建任务（如写一个 txt），Codex **不输出任何完成标记**——写完就直接回到 `›` prompt。

这意味着：

```python
# 三路检测的实际覆盖率：
# 长任务（多步修改、PR 审查等）
#   1) 完成标记 [Session complete / Token usage] → ✅ 覆盖
#   2) 预期产出文件 → ✅ 覆盖
#   3) 动态空闲超时 → ✅ 兜底
#
# 短任务（单文件创建、简单查询）
#   1) 完成标记 → ❌ Codex 不输出
#   2) 预期产出文件 → ✅ 唯一可靠信号
#   3) 动态空闲超时 → ⚠️ 兜底但浪费 120s
```

修复方向：对于简单任务，增强 `›` prompt + 预期产出交叉验证。如果 `›` 可见且预期产出存在就返回完成，不等完成标记。如果 `›` 可见但预期产出不存在 → 先检查是否正确路径（Codex 可能写到了 cwd 下）。

**原则 1：完成标记必须是真检测，不是死定义**

```python
# ❌ 错：定义常量后忘记引用
CODEX_COMPLETION_MARKERS = {"[codex-agent: Session complete", ...}
# _monitor_execution() 里从未引用此变量

# ✅ 对：在监控循环里扫描 pane 文本
session_complete = "[codex-agent: Session complete" in pane_text
if session_complete:
    return (capture_all(), False)
```

死定义（定义了但从不引用）比没定义更糟——团队会认为这个功能"已经测过了"。

**原则 2：声音通知的状态集合必须与 executor 状态枚举对齐**

```python
# ❌ 错：TERMINAL_STATES 缺了 executor_failed
TERMINAL_STATES = {"executor_completed", "hold", "timeout", "error", "session_lost"}
# CodexSoundNotifier.notify() 检查 runtime_state in TERMINAL_STATES
# → executor_failed 永远不匹配 → 声音永远不响

# ✅ 对：终端状态集合必须覆盖所有可能的 final_state
TERMINAL_STATES = {"executor_completed", "executor_failed", "hold", "timeout", "error", "session_lost"}
```

检查清单：每新增一个 `final_state` 值，同步更新 `TERMINAL_STATES`（以及 `SoundNotifier` 的状态匹配）。

**原则 3：动态空闲超时 > 静态墙超时**

```python
# ❌ 错：固定 wall time，不关心 agent 是否在工作
if now - self._started_at > self._max_wall_time:
    return timeout  # 即使 agent 在输出也 cut

# ✅ 对：pane 内容变化就重置 idle 计时器
if pane_text != prev_pane_text:
    last_output = now
    prev_pane_text = pane_text

if now - last_output > self._max_idle_sec:
    return idle_timeout  # pane 完全静止才超时

# 墙超时只做兜底（10 分钟）
if now - self._started_at > self._max_wall_time:
    return wall_timeout  # 兜底，不应该触发的
```

判断"agent 是否在干活"的信号：**pane 内容变化**。如果 pane 一直在变，agent 在工作，不要打断它。

- `no_output_timeout_sec`（defined in runtime_control）必须接入 monitor loop，不是死变量
- 墙超时（`emergency_max_wall_time_sec`）只做兜底

**原则 4：`›` prompt 检测不能短路判断**

Codex 回到 prompt（`›`）不代表结束了——它可能刚完成一个步骤，还在等后续输入。

```python
# ❌ 错：回到 prompt 且无产出 → 立即 idle_no_output
if "› " in last_line:
    if not expected_outputs_present:
        return (capture, False)  # 误判！Codex 可能还在忙

# ✅ 对：回到 prompt 但无产出 → 继续循环等完成标记或空闲超时
if "› " in last_line:
    if expected_outputs_present:
        return (capture, False)  # 有产出 + 回到 prompt = 真完成
    # 没有产出 → 继续循环，等 [Session complete] 或 idle timeout
```

**原则 5：pane 全文扫描 > 最后一行扫描**

完成标记可能出现在 pane 的任何位置，不一定是最后一行。

```python
# ❌ 错：只查最后一行
"› " in (pane_text.split("\n")[-1])

# ✅ 对：全文扫描
"[codex-agent: Session complete" in pane_text
```

**实现模式（通用 template — 2026-06-03 版本，不含任何 timeout）：**

```python
def _monitor_execution(self) -> tuple[str, bool]:
    _COMPLETION_MARKERS = re.compile(r"...")  # 完成标记正则

    while True:
        # Session alive — 唯一真正的"超时"信号
        if not self.manager.has_session():
            return ("", False)

        # Capture
        pane_text = self.manager.capture(...)

        # 1) Completion markers
        if _detect_completion(pane_text):
            time.sleep(2)
            return (self.manager.capture_all(), False)

        # 2) Expected file outputs
        if _detect_expected_outputs(...)["all_present"]:
            time.sleep(3)
            return (self.manager.capture_all(), False)

        # 3) Prompt indicator + expected outputs present
        if "› " in last_line and status["all_present"]:
            return (self.manager.capture_all(), False)

        # Heartbeat（下游 heartbeat_monitor.py 检测冻结）
        if now - last_heartbeat >= 30:
            write_heartbeat(...)

        time.sleep(self._poll_interval)
```

关键区别：**没有 idle_timeout、没有 wall_clock、没有 ready_timeout**。只有完成检测和心跳。heartbeat 冻结由 `heartbeat_monitor.py` 独立检测。

检测优先级：完成标记 > 文件产出 > prompt + 产出交叉验证 > heartbeat 冻结（下游）。

**核心原则：不要因为"看起来不对"就杀掉正在跑的 Claude session。** 先检查实际产出。

```text
❌ 错误：Claude 没触发 workflow 模式 → 杀掉 session 重来
✅ 正确：Claude 可能在用其他方式工作 → 让它跑完 → 检查 outputs/ 是否有产物
❌ 错误：progress.yaml 显示 runtime_state=prompt_sent → "卡住了"
✅ 正确：先 ls outputs/ 确认文件在不在
❌ 错误：发现 prompt 写错了 → 立即杀 session
✅ 正确：观察 Claude 是否已在正确方向工作 → 跑完后在下一个调度修 prompt
```

判断标准：

| Claude 状态 | 杀 session？ |
|------------|-------------|
| 卡在文件创建对话框（`Do you want to create...`） | 不杀，点 Enter 通过 |
| 被 bash whitelist 阻塞（`hold`） | 不杀，用户从 Terminal 窗口处理 |
| 虽然没走 workflow 但在正常写代码 | **不杀**，让它跑完看产出 |
| 完全无响应 >5 分钟 | 检查 pane，确认是死锁还是正常思考 |
| 产出文件已在盘上 | 不杀，直接回收 |

**常见陷阱：** Claude Code 即使不走 workflow 模式也能高效完成任务（如 N10 的 drift_check.py 702 行全部写完）。杀掉一个正在产出的 session 是浪费而不是优化。

## 8. 重跑协议

不删 outputs、改 expected_outputs 路径名、不碰 timeout、杀 session + 清 cleanup token。

## 9. CC Switch 路由

启动依赖 Claude Code 的 relay 前检查 CC Switch 代理活跃：
```bash
curl -s http://127.0.0.1:15721/health 2>/dev/null | grep -q '"status":"healthy"'
```

## 10. DeepSeek Thinking Fixer

fallback-only 插件，仅当检测到 thinking 回传错误时激活。MiMo 不需要。

## 11. Pitfalls（2026-06-03 狗食沉淀）

### dispatch-approval-gate — 新 tmux session 必须走用户确认门

每次新 tmux session 的 dispatch 必须先触发用户确认。这是 PM Runtime 的原生能力，不是可选的纪律。

```text
正确流程：
  Hermes 准备 dispatch → dispatch-approval-gate hook 触发
    → hook 输出需要用户确认的信号
    → 用户在 Hermes CLI 中确认（y/yes/可以）
    → 用户确认后才执行 realy 或 _launch.py dispatch

不得：
  - 在 prompt 中写"跳过安全门检查"绕过 gate
  - 连续重发 dispatch 不经过 gate
  - gate 阻塞后自行改 prompt 重试
```

gate 是留给用户确认的，不是给 prompt 绕过的。当 safety gate 阻塞（如 dirty tree / 跨项目写入 / adarian-iteration-safety-gate），告诉用户去 Terminal 窗口处理，不要在 prompt 加"跳过"声明。

已在 `~/.hermes/scripts/dispatch-approval-gate.py` 实现钩子，配置在 `~/.hermes/config.yaml` 的 `hooks.pre_llm_call` 中激活。

### 不要在 task_config.yaml 中写静态 runtime_control 值

`runtime_control` 的 `no_output_timeout_sec` 和 `emergency_max_wall_time_sec` 在 task_config.yaml 中是可选的。executor 有合理的默认值（idle 120s, wall 600s），并且 **动态 pane 检测已经覆盖了超时场景**。写死 60s 或 300s 反而会导致正常工作的 agent 被切断。

```yaml
# ❌ 不要写：
runtime_control:
  emergency_max_wall_time_sec: 300   # 人为缩短兜底时间，可能切断正常工作的 agent
  no_output_timeout_sec: 60          # 动态 pane 检测已经覆盖空闲超时

# ✅ 不写 runtime_control，让 executor 默认值 + 动态 pane 检测决定
# （默认 idle_timeout=120s, wall=600s）
```

例外：确实需要精确控制超时的特殊场景（如 CI/CD 固定窗口），但这时应当同时开启 observer_mode 让用户能看见进度。

### Codex 路径理解偏差

Codex 创建文件时，路径相对于它的 cwd（task_config 中的 `codex_context.cwd`，通常是 workyb 根）。不要在 prompt 中用 "在 outputs/ 下创建" 这种模糊路径——Codex 会在 `cwd/outputs/` 下创建，而不是 `task_dir/outputs/`。

正确写法：prompt 中用绝对路径 `tasks/active/<task_id>/outputs/<文件名>`，或者在 `codex_context.cwd` 指向 task_dir（但这样 Codex 无法访问项目根下的其他文件）。

这个教训已同时收录到 `dispatch-prompt-authoring` 的避坑清单中。

### commit-gate 协议

Hermes 不得 git commit。每次 commit 前必须跑 `~/.hermes/scripts/commit-gate.py` 展示变更，用 clarify 问 Owner 确认。违反 = 协议违规。

这个规则已同时收录到 `pm-runtime-roles` 的"不得做"清单和 Hermes memory 中。

- pm-runtime — 中台运行索引（父 skill）
- pm-relay-dialog — 对话框详细处理规则
- `tools/pm_runtime/relay/` — executor 实现源码
- `~/.hermes/scripts/dispatch-gate.py` — 派发门脚本
