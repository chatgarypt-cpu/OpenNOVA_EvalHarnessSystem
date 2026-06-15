---
name: closeout-gate
description: "任务收口门 — closeout checklist、task_status.yaml 写入规则、task-status-writer.py 用法。做 closeout 时加载。权威来源：task-directory-canonical §7、pm-runtime §13。"
version: 0.1.1
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, closeout, gate, governance]
---

# Closeout Gate Protocol v0.1.1

> 权威来源：task-directory-canonical §7（Closeout Gate）+ pm-runtime §13（Approval 规则）
> 加载时机：做 closeout 裁决时

## 0. 什么时候加载

1. **做 closeout 裁决** — Owner 决定关闭任务
2. **写 task_status.yaml** — 需通过 task-status-writer.py 校验
3. **检查 closeout checklist** — 确认所有必要文件齐备
4. **归档任务** — closeout 后移入 archived

## 1. 核心原则

只有 Owner-Control 确认 closeout 的任务才移入 archived。Hermes completed ≠ closeout。Reviewer pass ≠ closeout。

## 2. Closeout Checklist（Profiled）

由 `task_status.yaml.execution_profile` 确定档位。

### Smoke（简单验证）

```yaml
required:
  - task_status.yaml
  - runtime/result.json
  - outputs/
optional:
  - summary/summary.md
  - receipts/
```

### Standard（普通执行：review、inventory、patch）

```yaml
required:
  - task_status.yaml
  - dispatch/
  - runtime/result.json
  - outputs/
  - receipts/
  - summary/summary.md
```

### Full DAG（DAG/dogfood/runtime 底座任务）

```yaml
required:
  - task_status.yaml
  - dispatch/
  - runtime/result.json
  - outputs/execution_report.md
  - receipts/
  - summary/summary.md
  - carryover_check
  - scratch_check
  - asset_promotion_check
  - runtime_logs_retention_decision
```

所有 closeout 信息写入 `task_status.yaml.closeout`。人读 closeout 结论在 `summary/summary.md`。

### 2.4 累积/伞状任务的 Closeout 模式

某些任务是**迭代伞状任务**（umbrella iteration task）—— 执行期跨多个 session、通过多次 dispatch 完成、子节点已在单独目录归档。这种任务不适用单次 dispatch 的 checklist。

**特征：** dispatch/ runtime/ receipts/ 可能全部缺失（子节点各自有）。

**处理规则：**

```yaml
closeout:
  checklist_done:
    - outputs/execution_report.md
    - summary/summary.md
    - sub_nodes_archived (15 under archived/registry/)
    - registry_assets_updated
  checklist_na:
    - dispatch/ (accumulated multi-session task)
    - runtime/ (no single runtime state)
    - receipts/ (node-level receipts in sub-tasks)
```

**规则：**
1. 缺失的目录用 `checklist_na` 列出，附理由
2. 不要为合模而人工创建空目录
3. 子节点归档状态必须在 `checklist_done` 中明确包含
4. checklist 条目的设计不应限制 closeout 的范围——伞状任务的证据是"子节点已归档"，不是"缺了dispatch/"

### 2.5 checklist 条目状态值

每个 checklist 条目的合法状态：
- `done` — 存在且正确
- `not_applicable` — 该任务类型不需要此条目（附理由）
- `missing` — 应该存在但缺失（closeout 需要挂起）

## 3. Task Status Writer（强制执行）

写 `task_status.yaml` 时走 `task-status-writer.py` 脚本：

```bash
cat task_status.yaml | python3 ~/.hermes/scripts/task-status-writer.py --file task_status.yaml
```

**校验规则：**
- `closeout.status = closed` 时，`decided_by` 不能是泛称"Owner-Control"或"Hermes"
- 必须是实际 Owner 名（如 "Owner-Control (Gary)"）
- 必须有 `decided_at` 和 `rationale`
- 校验失败返回 exit 1 不写入

## 4. 触发条件

1. task_status.yaml.closeout.status = closed
2. 无未解决的 blocking issue
3. 已 closeout 的任务在后续会话中不再 active — 不应再从 archived 捞任务执行

## 5. 批准规则

高风险任务必须有 Owner 明确批准。

**不得视为批准：** Owner 沉默、超时未回复、模糊表达、历史偏好、PM Runtime 自判"应该可以"、DS pass、Codex delivered、relay_runner 成功退出

**过渡期：** 若未生成 approval.yaml 但 Owner 已在聊天中明确批准，记录 `approval_source: chat_confirmation`。

## 6. 关闭前验证（产出核验门）

Owner 说"收口"后，在写 task_status.yaml 之前，必须先做一步：**实际验证产出物的真实性。** 不要仅凭 report.md 的描述信赖任务完成。workflow 产出的报告可能说"修好了"但实际上没修对。

验证方法：
1. 读 report.md 了解 workflow 宣称做了什么
2. 对每条修改声明，去实际的文件系统核验（字段值、def 关键字存在性、编译检查）
3. 对代码修改任务：跑编译检查 + 导入测试
4. 汇总结果写入 task_status.yaml.closeout.checklist_done

**教训（2026-06-02）：** Gary 说"他这两个的产出，你先看一看，都通过了吗？" 在此之前我直接信了 report.md 没核验。

## 7. Carryover 验证门（Carryover Verification Protocol）

> 新增于 2026-06-03，基于 Registry R1.0 DAG Workflow Dogfood closeout 经验。

当需要验证 carryover items（已知问题列表中的待修项）是否已修复时，不要凭记忆或报告描述下结论。使用以下系统化验证方法：

### 7.1 验证步骤

1. **从执行报告提取 carryover 清单** — 找到 Known Issues / Repairable Issues / Carryover / 改进建议 等小节
2. **对每条 item 做代码库快照检查**：
   - 搜关键词 — 函数名、变量名、文件路径 是否仍存在
   - 读当前代码 — 确认代码是否已变化
   - 比对 Registry 文件 — YAML 字段值是否已更新
3. **按验证结果分类**：
   - `✅ 已修复` — 代码已变更，问题不再存在
   - `🟢 不需修复` — false positive（review 判断有误），解释原因
   - `🟡 协议级，无代码修复` — 属于行为规范/协议，不存在代码级别的 fix
   - `🟡 部分修复` — 核心功能已修但某一方面未覆盖
   - `❌ 未修复` — 代码与报告描述的问题仍一致
4. **区分"代码可验证" vs "协议/行为级"**：protocol items（如"dispatch 后主动汇报"、"写文档前加载 skill"）不能用代码搜索验证，标注为行为规范项单独列。
5. **汇总到 closeout 状态摘要**：列出各分类计数，给出整体裁决建议。

### 7.2 裁决原则

- carryover 文档化 ≠ 自动升级为下一任务（"不要把这些carryover自动升级为下一任务"）
- 小修 + 技术债 不阻塞 closeout，除非有 blocking issue
- 协议/行为级问题无法通过代码验证，标注为 known_behavior_gap
- false positive 需在 closeout 记录中明确声明"review 判断有误"，不列入未修

### 7.3 输出格式参考（A/B/C Category Layout）

```yaml
closeout:
  carryover_a_immediate_small_fixes:
    a1: ✅ 已修复 | a2: ✅ 已修复 | a3: 🟢 false positive | a4: ❌ 未修复
  carryover_b_operational_discipline:
    b1: 🟡 protocol | b2: ✅ 已修复 | b3: 🔴 未修复 | b4: 🟡 protocol | b5: 🟡 protocol
  carryover_c_tech_debt:
    c1: ✅ 已修复 | c2: ❌ 未修复 | c3: ❌ 未修复 | c4: 🟡 partial
    c5: ❌ 未修复 | c6: ❌ 未修复
```

## 8. Pitfalls

### 伞状任务的 checklist_na 不是偷懒借口

`checklist_na` 只用于**该任务类型天然不产出**的文件（如累积迭代任务没有单次 dispatch/ 目录）。不要用它跳过应该存在的文件。

### checklist 条目的设计不应限制 closeout 的范围

伞状任务的证据是"子节点已归档"，不是"缺了dispatch/"。不要为合模而人工创建空目录。

## References

- task-directory-canonical §7 — 权威来源
- pm-runtime §13（Approval 规则）
- `~/.hermes/scripts/task-status-writer.py`
