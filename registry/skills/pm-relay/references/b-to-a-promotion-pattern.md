# B→A 资产 Promotion 两阶段 Gate 模式（2026-06-03 定型）

## 适用场景

将 B 线已验证的底座能力 promote 到 A 线技能目录。不是代码迁移，是 **asset copy + 路径声明**。

## 两阶段 Gate 结构

```text
Go1: Promotion Readiness Review（只读对账）
  → 检查 A 线接收环境
  → 盘点 B 线待迁资产
  → 冲突矩阵（文件/ID/schema/命名）
  → 推荐 mapping
  → 生成 Go2 migration plan
  → Owner 审完批准 → 进 Go2

Go2: Controlled Asset Migration（受控迁移）
  → 按 Go1 mapping 执行 copy/move/skip/hold
  → 不覆盖已有文件（target exists → HOLD AND REPORT）
  → copy not move（源文件保留）
  → 生成 PATH_REFERENCE.md + promotion_report.md
  → 完成后准备 Reality Review
```

## 关键设计决策（已定型）

| 决策 | 结论 |
|------|------|
| A 线 PM Runtime 是谁 | **Hermes**，不是 Claude Code |
| closeout-gate 按什么格式迁移 | **Hermes PM Runtime skill** 原生格式，不做 Claude Code native skill 适配 |
| 执行器选择 | Codex 或 Claude Code 均可，根据执行模式选 |
| 冲突时谁赢 | A 线治理权威优先；能力事实以 B 线验证证据为准 |
| 目标文件存在 | 不覆盖，HOLD AND REPORT |
| Go2 后做什么 | Reality Review — 对比实际工作流 vs workflow_core v4.0 |

## 角色说明（已消除历史误解）

```text
A 线 PM Runtime / 调度中台 = Hermes
被 Hermes 调度的 worker/reviewer = Claude Code / Codex / DS Team
Claude Code 的 native skill 格式 = 不构成 PM Runtime 迁移标准
```

## 参考

- 迭代计划：`docs/iterations/workflow/b-to-a-promotion-v1.1-go1-go2-integrated-plan.md`
- Go1 产出：`tasks/active/b-to-a-promotion-go1/outputs/`
- promotion-gate hook：`~/.hermes/scripts/promotion-gate.py`
