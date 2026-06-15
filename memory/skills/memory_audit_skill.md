# Memory Governance — Audit + Cleanup Skill v0.3

> 版本提升：v0.2（审计 + 直接清理）→ v0.3（扫描 → 分类 → 展示 → Hook 确认 → 归档 → Git 追踪）
> 核心变化：清理不是删除，是从 memory 移到外部文件夹；全部操作走 Hook gate + Git
> 权威来源：`WorkflowBase/memory/memory_registry.yaml`

---

## 触发方式

以下任一提示词可触发：

```
/run memory-audit
/audit-memory
清理记忆 / 清理 memory
检查记忆污染
执行记忆治理
```

---

## 审查范围

1. **scope 污染** — 检查课程任务是否写入 Adarian 主线，临时实验是否改写 formal workflow 记忆。
2. **registry/readme 一致性** — memory_registry.yaml 与 README.md 的 scope 清单是否一致。
3. **外部模块路径漂移** — registry 中记录的外部模块路径是否存在、是否与配置一致。
4. **handoff 健康** — handoff 是否过期、缺字段、指向错误 scope。
5. **跨 scope 写入违规** — 检查是否有 scope 写入本应 write_restrictions 禁止的 scope。
6. **主 memory 污染** — 检查 L3 global memory 中是否有本应放在 scope memory、skill 或 session 状态中的条目。
7. **读污染** — Agent 在 scope A 工作时是否不当暴露了 scope B 的记忆内容。
8. **scope-recall 决策正确性** — 如果 scope-recall provider 可用，检查其 scope 路由决策是否正确。

---

## L3 Global Memory 分类规则

L3 只保留以下类别。其他一律清理。

### 可保留（L3 该放的）

| 类别 | 示例 | 说明 |
|------|------|------|
| 用户偏好 | 沟通风格、工作方式、命名偏好 | 跨会话稳定 |
| 架构决策 | DAG vs Agent Team 分界、SOLID 映射 | 不随版本变化 |
| 环境不变项 | 主模型、代理地址、工具路径 | 换电脑才变 |
| 行为规则 | 阻塞上报 protocol、hook gate 模式 | 每次会话都适用 |

### 必须清理（不该在 L3）

| 类别 | 示例 | 去向 |
|------|------|------|
| 具体任务结果 | "R1.2 通车 smoke PASS"、"5 agent 已创建" | 移入 cleaned/ 文件夹 |
| 流程/SOP 步骤 | "dispatch SOP: 先写 prompt 再 slash" | 移入 skill + 移入 cleaned/ |
| Session 级别观察 | "Workflow 触发方式已验证" | 移入 skill + 移入 cleaned/ |
| 重复条目 | 同一话题 2 条以上 | 合并后移入 cleaned/ |
| 已修 bug | "Relay I5 monitor loop ~30s" | 移入 cleaned/ |

### 边界情况

| 条目类型 | 判断 |
|---------|------|
| 用户纠正过的行为模式（block 上报、不提前结论） | ✅ 保留 |
| 已知但尚未修的 issue | 记入 handoff 或 task 文件，不保留在 L3 |
| 跟具体 session 绑定的决策 | 记入 handoff，不保留在 L3 |

---

## v0.3 清理流程（五阶段 + Hook Gate）

### Phase 1: 全量扫描

读 L3 global memory 的全部现有条目，逐条记录内容、长度、状态。

输出：完整的 L3 条目清单（每条用短摘要标识）

### Phase 2: 分类

逐条对照分类规则，标记：

```
ENTRY: <短摘要>
CLASSIFY: keep | archive | merge_into:<target> | move_to_skill:<skill_name>
REASON: <为什么>
```

### Phase 3: 展示分类结果表

输出结构化分类表，格式：

```
┌──────┬──────────────────────────────┬────────────┬──────────────────┐
│ #    │ 条目摘要                     │ 分类       │ 原因             │
├──────┼──────────────────────────────┼────────────┼──────────────────┤
│ 1    │ "R1.2 通车 smoke PASS"      │ archive    │ 具体任务结果     │
│ 2    │ "Gary 认可的 6 原则"        │ keep       │ 架构决策         │
│ 3    │ "dispatch SOP: ..."         │ move_skill │ 已进 dispatch... │
│ ...  │                              │            │                  │
└──────┴──────────────────────────────┴────────────┴──────────────────┘
```

此阶段 **不执行任何操作**，只展示。

### Phase 4: Hook Gate — 用户确认

向用户明确请求批准：

```
--- Memory Cleanup Approval Request ---

建议清理 XX 条（archive）+ YY 条（move_to_skill）+ ZZ 条（merge）
预计可释放 ~NNNN 字符空间。

归档文件将写入：
  WorkflowBase/memory/cleaned/<YYYY-MM-DD>/

请确认：
1. 以上分类是否接受？
2. 是否有某条应改为 keep？
3. 是否有没扫到的条目也要加入清理？

输入 "yes" 或具体修改意见后继续。
```

**没有用户确认，不执行任何清理操作。** 这是硬 gate，类似 handoff-writer.py 的 record_protocol gate。

### Phase 5: 执行（归档到 cleaned/）

用户确认后：

1. **创建 cleaned 目录**：`WorkflowBase/memory/cleaned/<YYYY-MM-DD>/`
2. **每一条要清理的条目写入独立文件**：`<序号>-<短摘要>.md`，包含完整条目内容
3. **同步写入 scope-recall 数据库**：用 `sqlite3 ~/.hermes/scope-recall/memory.sqlite3 "INSERT INTO memories(content) VALUES('<内容>');"` 确保 scope-recall 有副本
4. **从 L3 memory 中移除**（只移不删——原始内容已备份到文件夹 + scope-recall）
5. **move_to_skill 的条目**：用 `skill_manage(action='patch')` 追加到对应 skill
6. **merge 的条目**：合并后写回 L3
7. **不自动 git commit** — 文件在 repo 的 cleaned/ 目录下已有记录，用户将来自行处理版本

### Phase 6: 报告

输出清理报告：

```
--- Memory Cleanup Report ---
日期: YYYY-MM-DD
清理总数: XX 条
  - archive (移入文件夹): XX 条
  - merge (合并): XX 条
  - move_to_skill: XX 条
  - keep: XX 条
释放字符: NNNN / 22000 (NN%)
L3 当前占用: NNNN / 22000 (NN%)

已归档到: WorkflowBase/memory/cleaned/<YYYY-MM-DD>/
```

---

## 执行规则（硬约束）

1. **审计只读** — Phase 1-3 不修改任何内容。
2. **Hook Gate** — Phase 4 之前绝对不执行变更。无用户确认 = 不清理。
3. **归档优先于删除** — 每条清理的条目先写文件，再同步到 scope-recall SQLite，最后移出 memory。
4. **文件留痕** — cleaned/ 目录在项目 repo 内，自然有版本历史。不自动 git commit。
5. **用户保留最终决定权** — 用户可在 Hook 阶段：
   - 改变某条的分类（keep 改为 archive，archive 改为 keep 等）
   - 添加不在原分类表中的条目
   - 完全拒绝全部建议
6. **历史留痕** — 每次清理的`cleaned/`目录按日期命名，不覆盖旧记录。

---

## Verdict 体系

- **PASS** — 全部检查项通过
- **PASS_WITH_FINDINGS** — 发现非阻断性问题，记录到 audits/
- **HOLD** — 发现阻断性问题，停止当前任务相关操作，报告 Owner
- **FAIL** — 发现严重污染/违规，立即停止写操作，报告 Owner

---

## 已知限制

- v0.3 仍为手动触发版，无自动定时触发。
- 不检查 Hermes 内建 memory 的底层存储内容（超出 governance 目录范围）。
- 不检查 scope-recall provider 的 SQLite 内部数据一致性。
- cleaned/ 目录积累后不会自动清理——用户可随时安全删除。
