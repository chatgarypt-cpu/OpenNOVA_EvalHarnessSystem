# Fan-in Acceptance vs Reality Review — 两个独立的质量门

> 确立于 R1.0 DAG 狗食规划阶段（2026-06-02），Owner 确认执行后额外走一次 Reality Review。

## 区别

| 维度 | Fan-in Acceptance（N10） | Reality Review |
|------|------------------------|----------------|
| 类型 | DAG 内部节点（node-level） | DAG 外部独立审查 |
| 执行方 | agent team（v0.4 §8 fan-in） | 5-agent registry-reality-review team |
| 检查内容 | 节点是否完成、字段是否正确、节点间一致性 | 最终产出质量、与现实是否一致、深层问题 |
| 标准 | 低——只需通过节点级验收 | 高——独立审查，可发现 fan-in 盲区 |
| 通过条件 | min_success_ratio >= 0.75 | verdict: pass 或 pass_with_findings |
| 阻塞关系 | 不阻塞 Reality Review | 可推翻 fan-in 结论 |

## 执行顺序

```
N10 fan-in acceptance → Reality Review（独立调用，不依赖 DAG pipeline）
```

两个门不级联：fan-in 过了只是 DAG 内部确认"节点各自跑完了"，Reality Review 才是对最终产出质量的真实验收。

## 触发方式

Relay runner dispatch，启用 registry-reality-review skill。不是 DAG 节点——是独立的审查任务。
