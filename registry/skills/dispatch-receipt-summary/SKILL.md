---
name: dispatch-receipt-summary
description: "派发回执与摘要 — dispatch、receipt、summary 的模板/校验规则/回收流程。写 dispatch/summary 时加载。权威来源：pm-runtime §12+14。"
version: 0.1.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, dispatch, receipt, summary]
---

# Dispatch / Receipt / Summary Protocol v0.1.0

> 权威来源：pm-runtime §12（Dispatch 要求）+ §14（Report/Receipt/Summary 回收）
> 加载时机：写 dispatch、回收 receipt、生成 summary 时

## 0. 什么时候加载

1. **编写 dispatch** — 创建 task_config.yaml 或 prompt.md
2. **回收 receipt** — 验证 executor 回执完整性
3. **生成 summary** — 从 execution_report 提炼摘要
4. **验证产出完整性** — 检查必要产物是否存在

## 1. Dispatch 要求

PM Runtime 可以生成 dispatch draft，不能自行批准高风险任务。

### 1.1 必填字段

dispatch 至少包含：task_id、task_title、task_date、task_type、owner、executor、status、created_at、goal、scope、allowed_actions、forbidden_actions、allowed_read_paths、allowed_write_paths、expected_outputs、acceptance_criteria、failure_policy

### 1.2 DS 任务额外字段

team_mode_required、mcp_required、report_required、receipt_required

### 1.3 Codex 任务额外字段

allowed_files、forbidden_files、required_commands、diff_report_required、commit_mode

### 1.4 默认 failure policy

失败后 HOLD，不自动扩大权限，不自动改变任务目标

### 1.5 模板

标准化 dispatch 和 receipt 模板位于：
```
tools/pm_runtime/templates/dispatch.template.yaml
tools/pm_runtime/templates/receipt.template.yaml
```

dispatch 模板包含：17 基础字段 + 按 executor 的三组附加字段 + approval 记录段。来源：workflow_core §5.2。
receipt 模板包含：16 基础字段 + 可选补充字段 + 6 条机器校验规则。来源：workflow_core §5.3。

生成 dispatch 时，以模板为起点填写，不要每次重新手写结构。

## 2. Approval 规则

高风险任务必须有 Owner 明确批准。

**不得视为批准：** Owner 沉默、超时未回复、模糊表达、DS pass、Codex delivered、relay_runner 成功退出

**过渡期：** 若未生成 approval.yaml 但 Owner 已在聊天中明确批准，记录 approval_source: chat_confirmation。

## 3. Report / Receipt / Summary 回收

### 3.1 回收检查 10 项

report 存在、receipt 存在、output path 真实、task_id 一致、executor 一致、started_at/completed_at/elapsed 存在、blockers 列明、known issues 列明、process issues 列明、next_recommendation 明确。没有真实路径不算完成。

### 3.2 Summary 定位

summary 是**完整任务报告的人类简报**，不是 runtime metadata 的转述。

**生成方式：** post-execution LLM 摘要任务，不是 relay runner 的自动后处理。

```
outputs/execution_report.md 生成
→ task_status.yaml 标记 summary.required: true, summary.status: missing
→ Hermes/Control Agent 读取完整报告
→ 生成 summary/summary.md（20-80 行）
→ task_status.yaml 更新 summary.status: generated
```

Relay Runner 不负责生成 summary。它只标记 `summary.required` 和 `summary.status: missing`。

### 3.3 Summary 回答

- 这是什么任务？做完了吗？修了什么/产出了什么？
- 关键数字是什么？最终裁决是什么？
- 还有什么 carryover？需要 Owner 做什么决定？
- 全文报告在哪里？

### 3.4 Summary 不应包含

- heartbeat 次数、pane id、tmux session name
- executor stdout 首行截断、大量 N/A 字段
- runtime metadata 转述
- raw logs 摘抄
- result.json 的自然语言版本

### 3.5 推荐压缩比例

1/8 到 1/20。700 行的 execution_report → 30-80 行的 summary。

### 3.6 简报格式

必须包含：task_id、task_title、runtime_status、executor、dispatch_path、report_paths、receipt_paths、result_paths、summary_generated_at、blockers、known_issues、process_issues、next_recommendation、owner_control_required: true

**简报是汇报标准格式，不可跳过。** 每次长程任务结束后，必须读取 summary/pm_runtime_summary.md 再向 Owner 汇报。

## References

- pm-runtime §12（Dispatch 要求）+ §14（Report/Receipt/Summary 回收）
- `tools/pm_runtime/templates/dispatch.template.yaml`
- `tools/pm_runtime/templates/receipt.template.yaml`
- `~/.hermes/skills/pm-runtime/pm-runtime/references/pm-runtime-briefing.md`
