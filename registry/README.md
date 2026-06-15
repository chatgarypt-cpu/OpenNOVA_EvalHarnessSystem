# Workyb 能力注册中心

> 本目录托管 workyb 系统已注册的能力资产 —— skill、MCP、hook、executor。
> 通过 registry 实现能力可见、可读、可审查，为后续 dispatch、permission、routing 提供基础。

## 文件结构

| 文件 | 用途 |
|------|------|
| `README.md` | 目录说明（当前版本 R0.1） |
| `registry_schema.md` | 通用 schema 定义 |
| `skill_registry.yaml` | skill 能力注册表 |
| `mcp_registry.yaml` | MCP 服务器注册表 |
| `hook_registry.yaml` | 系统 hook 注册表 |
| `executor_registry.yaml` | 执行器注册表 |
| `promotion_mapping.md` | workyb→Adarian 版本迁移映射 |

### 补充资产

| 路径 | 用途 |
|------|------|
| `WorkflowBase/registry/self-maint/drift_check.py` | 注册表自检工具 — 对比六件套 vs 现实文件系统，输出 drift report |
| `WorkflowBase/registry/self-maint/scan_proposal.py` | 扫描 proposal 生成 — 将 drift 输出转为可执行的变更建议 |
| `WorkflowBase/registry/self-maint/proposal_apply.py` | 提案预览工具 — `--apply` 被拒绝，仅预览 diff |

## 版本

- Registry 版本：R0.1
- Schema 版本：R0.1
- 状态：candidate
- 说明：当前 registry 为 candidate foundation asset，尚未正式 promotion

## 维护规则

1. 每个能力条目必须包含 schema 定义的必填字段。
2. 未确认的能力必须标记 `status: candidate` 或 `to_be_filled`，不得声称可用。
3. 新增能力条目不修改既有条目（除非升级 status）。
4. 删除能力条目必须经 Owner-Control 批准。
5. 本 registry 不自动启用能力 —— dispatch 层需独立接入。

## 注册表清单

- 已注册 Skill：~126+（Hermes 5 独立条目 + 11 group 覆盖 ~112 skills + CC Switch 7 独立条目 + 1 group 覆盖 7 skills）
- 已注册 MCP：7
- 已注册 Hook：13
- 已注册 Executor + PM Runtime Plugin：7
