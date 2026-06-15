# Dispatch Prompt 经验教训（2026-06-03 狗食）

## 路径精度 — Codex 创建文件的路径解析

Codex 创建文件时，路径**相对于它的 cwd**（task_config 中的 `codex_context.cwd`，通常是 workyb 根），**不是相对于 task_dir**。

```text
❌ "在 outputs/ 下创建 monitoring_summary.txt"
   → Codex 创建了 workyb/outputs/monitoring_summary.txt（错！）
   → 因为 cwd = workyb 根，outputs/ 被理解为 workyb/outputs/

✅ "在 tasks/active/codex-dogfood-monitoring-fix/outputs/ 下创建"
   → Codex 创建到正确位置
```

修法不是改 executor 逻辑，是改 prompt 写法——路径写完整。

## 短任务不输出完成标记

Codex 对于简单的单步操作（创建文件、简单查询）**不输出** `[codex-agent: Session complete` 或 Token usage 行。它直接回到 `›` prompt。

这意味着 completion_marker 检测对于短任务不可靠。预期路径产出（expected_outputs）是唯一可靠的完成信号。长任务（多步修改、PR 审查）才有完成标记。

## 不要在 prompt 中写"跳过安全门"

当 `adarian-iteration-safety-gate` 安全门检测到 dirty tree 或跨项目写入时，它停在 Terminal 窗口中等待用户确认。**不要在 prompt 加"跳过 dirty tree 检查"绕过它。** gate 是留给用户确认的。

正确做法：告诉用户去看 Terminal 窗口，处理 gate 提示，确认后 Codex/Claude 继续执行。

## 执行器选择

- Codex direct mode：适合单步文件操作、严格路径边界
- Claude Code workflow：适合 DAG 编排、多节点依赖、代码审查
- Claude Code direct：Codex 额度用尽时可用

不是硬绑定。同一任务用 Codex 或 Claude Code 执行都可以，visual prompt 结构和执行模式不变。
