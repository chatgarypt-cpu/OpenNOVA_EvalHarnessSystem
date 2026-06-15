# B→A 资产 Promotion 三阶段 Gate 模式

将 B 线已验证的资产 promote 到 A 线时，使用 Go1（只读对账）+ Go2（受控迁移）+ Go3（Reality Review 验证）三阶段 Gate。

## 适用条件

- B 线底座能力已达到可用阈值
- A 线目标目录已存在但无对应资产
- 迁移前需确认：命名冲突？schema 冲突？registry ID 冲突？目标路径存在？

## 执行流程

```
Go1: Readiness Review（只读，不写不复制）
  N0: Scope & Path Preflight
  N1: A 线接收环境检查（目标目录、已有文件、authority drift）
  N2: B 线待迁资产盘点（源文件存在性、完整性）
  N3: 冲突矩阵（文件名/路径/schema/registry ID）
  N4: 推荐 Mapping（每个资产的 recommended_action）
  N5: Go2 迁移计划（结构化迁移表、Batch、验证命令）
  N6: Summary（GO/CONDITIONAL_GO/HOLD 判定）

Go2: Controlled Migration（基于 Go1 计划执行）
  按 Batch 顺序执行 copy / rename / reference
  不覆盖目标已有文件（NO_OVERWRITE_HOLD）
  每个 Batch 后执行验证命令
  最终全局验证（语法检查、路径残留、registry 指向）

Go3: Reality Review（实施后必须执行）
  ⚠️ 关键发现：Go2 迁移完成后，registry 指向的能力可能 55%+ 在 A 线不存在
  四维度审查：
    1. 注册表一致性（六件套存在性、条目数、README 对照）
    2. Schema 一致性（YAML 可解析性、枚举合规、跨文件引用）
    3. 能力真实性（status=active 的条目是否真实存在、module_path 可解析）
    4. 权限与风险（permission_level/risk_level 合理性、路径约束完整度）
  硬验收标准：
    - 六件套全部存在
    - 所有 YAML 可解析
    - 无 B 线非文档性路径残留
    - schema 与 YAML 一致
    - module_path 全部相对且可解析
    - 验证命令全部通过
```

## 核心教训（2026-06-03 Go3 验证）

**Go2 后必须做 Go3，不能假定迁移完成。** 在 18 项资产的 B→A 迁移中：

| 指标 | 值 |
|------|-----|
| 迁移声明 | 18 项全部拷贝 |
| Go3 验证发现实际完成 | 10/18（55.6%） |
| 未迁移项 | `claude/`、`plugins/`、`path_resolver`、`self-maint` 脚本、`memory_governance/`、`sounds/`、`zhipu_mcp/` |
| Schema FAIL | 3（type 枚举违规 + depends_on 断裂） |
| 能力真实性 FAIL | 5（registry 说 active 但实现不存在） |
| 安全边界 WARN | 3（full 权限无约束、审批缺失） |

**根因**：registry YAML 是 B 线的运行时快照——在 B 线所有路径都真实存在，但快照本身不含"A 线是否就绪"的信息。Go2 只复制了文件，没验证 A 线是否可运行。

**对应措施**：Go3 必须在 Go2 之后立即执行，且需用 Claude Code agent team（5 agent，含 synthesis）。单 agent 代替多角色是违规。

## 权威规则

```yaml
naming_conflict: A_line_wins
schema_conflict: HOLD_AND_REPORT
registry_id_conflict: HOLD_AND_REPORT
existing_target_file: NO_OVERWRITE_HOLD
runtime_source: reference_original_not_duplicate
```

## A 线推荐目录结构（2026-06-03 约定）

```
WorkflowBase/             ← 底座（顶层名可自选）
├── registry/             ← 注册表（YAML + skills/mcp/plugins 实体）
├── runner/               ← 运行时（relay_runner + codex/claude executor）
├── governance/           ← 治理（memory_governance 审计清理脚本）
├── infra/                ← 支撑设施（sound、config）
├── self-maint/           ← 自持系统（scan/apply/drift 可插拔 scanner）
├── workflow_core.md      ← 给人读的设计说明
└── management.yaml       ← 从 Go3 产出的维护状态

tasks/                    ← 执行证据（跟 WorkflowBase 平级）
├── active/
└── archived/
```

## 触发链

```text
promotion-gate hook（自动扫描 Downloads → 落盘 → 审查）
  → task-directory-canonical（建任务目录）
    → dispatch-prompt-authoring（选三种模式之一：
      direct / agent-team / workflow）
      → relay runner dispatch
```

## 各阶段 Gate 检查

| 阶段 | 通过条件 | 禁止 |
|------|---------|------|
| Go1 | 所有资产 mapping 明确、无未解冲突 | 写文件、复制资产 |
| Go2 | Go1 报告已批准、mapping 已冻结 | 改 mapping、覆盖已有文件、改 workflow_core |
| Go3 | 硬验收 ≥ 5/6、无 BLOCKING_FAIL | 修改任何文件、提 R1 需求 |
