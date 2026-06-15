# Workyb → Adarian Promotion Mapping

> 定义 workyb 孵化资产向 Adarian 正式版本迁移时的版本转换与映射规则。
> 创建日期：2026-05-30

## 原则

```text
workyb 的 r0 / r0.1 = 孵化版本号（狗食测试、模板验证、底座能力自举）
Adarian 的 vX.Y.Z = 正式工程版本号（Codex landing、DS Review、closeout）
两者不得混用。
```

## 当前孵化资产

| Asset | Workyb 版本 | 状态 | 预计 Adarian 目标 |
|-------|------------|------|-----------------|
| 资产/registry/ | R0 | 已执行，待 promotion | TBD |
| 资产/registry/ | R0.1 | draft | TBD |

## 迁移流程

孵化自举完成后，向 Adarian 迁移的 Pipeline：

```text
workyb 孵化版本
→ Adarian formal asset candidate 评估
→ Adarian 版本定位（vX.Y.Z / iteration doc）
→ Codex landing
→ DS / Code Reality Review
→ closeout
```

关键转换节点：**Promotion Gate**。workyb 内走的是孵化线 promotion（promote / keep_as_candidate / discard），Adarian 走的是正式工程线的 accept / closeout。

## 映射表

```yaml
promotion_mapping:
  source_asset: workyb/资产/registry
  source_version: registry-r0.1
  target_project: adarian
  target_version: TBD
  target_asset_type: skill_governance / runtime_governance / workflow_core_patch
  promotion_status: candidate / accepted / rejected / landed
  evidence:
    - registry reality review
    - validation receipt
    - promotion gate receipt
```

## 目录预期

当 workyb registry 满足 promotion 条件后：

```
AdarianMigration/adarian mvp/
├── docs/iterations/v<X.Y.Z>-registry-landing.md
├── 资产/ (或 Adarian 对应资产目录)
│   └── registry/          ← 迁移目标
│       ├── README.md
│       ├── registry_schema.md
│       ├── skill_registry.yaml
│       ├── mcp_registry.yaml
│       ├── hook_registry.yaml
│       └── executor_registry.yaml
└── ...
```
