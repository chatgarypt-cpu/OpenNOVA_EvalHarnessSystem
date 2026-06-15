# Multi-Goal DAG 大狗食模式

> Validated: Registry R1.0 (2026-06-02, 3 corrective rounds to finalize)
> Core insight: Goal 1 (infra) is consumed by Goal 2/3 (business). Goal 4 repairs infrastructure. Goal 5 is independent review.

## Pattern structure

```
Goal 1: DAG v0.4 协议（基础设施）
  ├── Goal 2: 业务层 A（补丁/修复）
  │   └── 全部节点走 Goal 1 DAG 协议
  ├── Goal 3: 业务层 B（自持系统建设）
  │   └── 全部节点走 Goal 1 DAG 协议
  ├── [N fan-in: 验收全部业务节点]
  ├── Goal 4: DAG 系统修复（修管道本身）
  │   ├── relay runner / executor / dialog / 隔离 bug
  │   ├── 不改设计文档、不改业务代码、不改 Hermes 核心
  │   └── 所有修改必须披露
  └── Goal 5: Reality Review（独立 5-agent team 核对）
```

## When to use

When an iteration covers multiple layers of concern:

| Layer | Example | Outcome |
|-------|---------|---------|
| Infrastructure (Goal 1) | DAG protocol, relay dispatch | Pipeline runs |
| Business consumer A (Goal 2) | Registry patch, content fix | Model corrected |
| Business consumer B (Goal 3) | Self-sustaining system | Capability added |
| Infrastructure repair (Goal 4) | Fix bugs found in Goal 1~3 execution | Pipeline healthy |
| Independent review (Goal 5) | Reality check by 5-agent team | Quality verified |

## Key rules

1. **Layered repair distinction**: node-level Repair Agent (v0.4 §10) fixes business execution issues within a DAG node (≤2 retries → escalate). Goal 4 fixes bugs in the pipeline infrastructure itself (relay, executor, dialog handling, safety isolation). They are NOT the same.
2. **Business consumers share the same infrastructure**: Goal 2 and Goal 3 both run on Goal 1's DAG. Do NOT give them independent pipelines.
3. **Fan-in covers all business nodes**, not just the last Goal. It happens between Goal 3 and Goal 4.
4. **Reality Review is NOT a DAG node** — it's a standalone review task dispatched via relay to the 5-agent review team. Decoupled from pipeline.

## Common failure: scope loss during phase splitting

Original draft → split into R1.0/R1.1/R1.2 → scope silently dropped:

1. Original core positioning: "修复+搭建自持能力" (two things bound together)
2. After split: R1.0 = only patch, self-sustaining → R1.1
3. Result: the iteration plan's Goals don't match the original draft's scope
4. Owner caught it: "少了一个事情"

**Prevention**: before presenting any Goal structure, compare your node table against the original draft's DAG nodes. Report any silently dropped scope proactively.

## Corrective cycle recorded

1. Agent presented 2-Goal plan (infra + patch, missing self-sustaining) → Gary: "是不是少了一个事情"
2. Agent split to 3 Goals (infra + patch + self-sustaining + repair) → Gary: "goal2是补丁；goal3是自持系统"
3. Agent split to 5 Goals (infra + patch + self-sustaining + DAG repair + QR) → ✅

**Lesson**: iterations with fewer than expected Goals should trigger a scope-verification reflex. Don't assume "I merged them" — check if you lost them.

## Reference

- dag-execution SKILL.md §1.1 — 5-Goal architecture
- multi-goal-repair-rounds-pattern.md — sibling reference about repair layering
- fan-in-vs-reality-review.md — sibling reference about quality gates
