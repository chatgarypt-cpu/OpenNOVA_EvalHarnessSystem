# Multi-Goal DAG with Layered Repair Pattern

> Validated by: Registry R1.0 iteration plan (2026-06-02, 3 corrective rounds)
> Context: 5-Goal architecture with infrastructure layer + 2 business consumers + infrastructure repair + reality review

## When to use this pattern

When a single iteration covers **multiple layers of work** with different concerns:

1. **Infrastructure protocol** (DAG v0.4) — the execution pipeline itself
2. **Business consumers** (补丁 + 自持) — distinct phases that both run on the DAG
3. **Infrastructure repair** — fix bugs in the pipeline exposed by real execution
4. **Independent review** — external quality check decoupled from the pipeline

## The validated 5-Goal architecture

```
Goal 1:   DAG 协议（基础设施）— 不产出业务结果，只提供执行管道
  ├── Goal 2: 补丁            ← 业务层，消费 Goal 1
  ├── Goal 3: 自持系统        ← 业务层，消费 Goal 1
  │
  ├── N12 fan-in 验收（覆盖全部业务节点）
  │
  ├── Goal 4: DAG 系统修复    ← 基础设施修复层
  └── Goal 5: Reality Review  ← 独立核查层
```

## Key structural rules

1. **Goal 2 and Goal 3 are both consumers of Goal 1.** This is "one big dogfood" — the DAG protocol is being tested by real consumption. Do NOT make Goal 2/3 independent pipelines.
2. **Goal 4 is NOT the same as node-level Repair Agent (v0.4 §10).** Node-level repair fixes business execution issues (wrong output format, missing file). Goal 4 fixes infrastructure bugs in the DAG pipeline itself (relay runner exit 5, dialog misclassification, timeout logic, executor registration).
3. **Goal 5 is Reality Review** — the 5-agent team (registry-reality-review or code-reality-review) is independent of the execution pipeline. It does NOT run inside the DAG.
4. **Fan-in acceptance (N12) covers ALL business nodes** (Goal 2 + Goal 3), not just the last Goal. It happens before Goal 4 and Goal 5.

## Layered repair distinction

| Level | What it fixes | Trigger | Owner |
|-------|---------------|---------|-------|
| Node Repair Agent (v0.4 §10) | Node-level business execution failure | Validation fails on node output | Hermes (auto, ≤2 retries) |
| Goal 4 (DAG system repair) | Infrastructure bug in relay/executor/dialog | Goal 1~3 execution exposes DAG bug | Owner (Manually or via relay dispatch) |

## Common failure mode

**Phase-splitting scope loss:** When a comprehensive draft gets split into multiple phases (R1.0/R1.1/R1.2), pieces of the original scope are silently dropped. The loss pattern:

- Original core positioning: "修复+搭建自持能力"
- After split: R1.0 = only patch, self-sustaining → R1.1
- Result: The iteration plan's Goal 2/3 don't match the original draft's scope

**This session's Owner correction:** Gary found the self-sustaining system was missing from a 3-Goal plan I presented. He corrected it to 5 Goals. The corrective cycle:

1. I presented R1.0 with missing self-sustaining → Gary: "少了一个事情"
2. I split into 3 Goals (patch + self-sustaining + repair) → Gary: "goal 2是补丁；goal3是自持系统"
3. I split into 5 Goals (infra + patch + self-sustaining + DAG repair + QR) → ✅

**Lesson: before presenting any Goal structure, check the original draft's DAG nodes against yours. Report diffs proactively.**

## The "not a goal" trap

The original R1 draft had "修复" as an implicit step in every Goal node, not as a standalone Goal. When converted to explicit Goals, "修复" got promoted to a Goal (old Goal 3 in the first attempt). Gary's correction: Goal 4 is DAG SYSTEM repair (infrastructure), not business-level self-healing. The node-level healing stays inside Repair Agent protocol.

## Reference

- dag-execution SKILL.md §1.1 — 5-Goal architecture details
- dag-execution SKILL.md §4 — Repair Agent protocol (node-level)
- iteration-plan.md (R1.0, v0.4) — concrete example of 5-Goal plan
