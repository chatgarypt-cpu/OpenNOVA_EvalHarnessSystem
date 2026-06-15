---
name: task-repair
description: "通讯修复规约 — task-local communication repair：修复 relay/executor 通讯失败，不修业务代码。做修复操作时加载。权威来源：pm-runtime §10。"
version: 0.1.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, repair, communication, recovery]
---

# Task-local Communication Repair v0.1.0

> 权威来源：pm-runtime §10（Task-local Communication Repair）
> 加载时机：修复 executor 通讯故障时

## 0. 什么时候加载

1. **relay 通讯失败** — executor 输出没正常回传
2. **产物未回收** — report/receipt 缺失
3. **需要补写运行时状态** — heartbeat/progress/result 不完整
4. **需要追加审查发现** — Team Review 遗漏了关键对照

## 1. 允许的操作

1. 修 relay_runner 的 JSON 提取逻辑
2. 修 stdout/stderr extraction
3. 从 permission_denial payload 提取已完成报告
4. 从 ds_raw_inner.txt 重新提取报告
5. 补写 heartbeat/progress/result
6. 重新提取已完成 agent 输出
7. 补 runtime_note/process_issue
8. 生成 pm_runtime_summary
9. 把通讯失败与任务失败区分开
10. 将 MCP 只读工具加入 .claude/settings.local.json 白名单
11. 重组任务目录结构并在 summary 中披露迁移动作

## 2. 禁止的操作

修改 src/、tests/、main.py、config.py、workflow_core.md、iteration document、contracts、DS verdict；降级 blocker；修改 Codex diff；closeout；git commit；扩大 scope

## 3. 硬规则

**修通讯不修业务、修 relay 不修源码、回收报告不改结论、标记 process_issue 不降级 blocker、越界立即 HOLD、所有 repair 必须披露。**

## 4. 审查后追加发现

当 Team Review 完成后、Hermes 发现审查遗漏项（如未对照 R2 原文），应将发现追加到报告末尾而非创建独立文件。格式：

```markdown
---

## 附录 A：Hermes 补充发现（日期，审查结束后追加）

> 以下内容由 Hermes（PM Runtime）在 DS Team 审查完成后追加，非 DS Team 原始产出。
> 来源：{对照方法说明}

### A.1 {发现标题}

{内容 + 建议}
```

追加后删除任何独立的 gap analysis 文件。

## 5. Executor 代码 vs 业务代码：谁修

- **基础设施 bug**（dialog 未处理、定时器逻辑错误、状态机缺陷）：Hermes 可直接 patch
- **功能增强**（bash 命令提取增强、文档完善）：通过 relay runner 派给 Claude Code
- **业务源码**（src/、tests/、contracts/）：永远不直接改。走 dispatch → Team Review/Codex → review

判断标准：如果这个 patch 是 relay runner 能跑通的前置条件 → Hermes 直接修。如果已经能跑但不完美 → 派给 Claude Code。

## 6. Team Review 审查材料清单

为 Team Review 创建 dispatch 时，审查材料必须包含：
1. 主审查对象
2. 上游合同
3. 上游计划
4. workflow_compact YAML
5. workflow_core 相关章节

## References

- pm-runtime §10 — 权威来源
- pm-relay — 派发标准链路
