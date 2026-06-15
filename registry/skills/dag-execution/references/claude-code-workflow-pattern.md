# Claude Code Dynamic Workflow 使用模式（R1.0 验证）

> 验证日期：2026-06-02
> 触发方式：prompt 开头写 `"use a workflow to: [任务描述]"` 是确定性的

## 触发方式

```markdown
use a workflow to: <任务描述>

### Agent 1: <name>
**目标：** <做什么>
**依赖：** <前置 agent>

### Agent 2: <name>
**依赖：** Agent 1

## 输出要求
输出到 outputs/report.md
```

## 验证方法

| 标志 | 说明 |
|------|------|
| `workflows/scripts/*.js` 文件存在 | ✅ 已创建 JS 脚本 |
| `/workflows` 可查看进度 | ✅ Workflow tool 运行中 |
| 有 `⏵⏵` 和 agent 列表 | ✅ Workflow UI 激活 |

## 限制

| 限制 | 说明 |
|------|------|
| 并发上限 | 最多 16 个 concurrent agent |
| 总数上限 | 1000 agent per run |
| 无 mid-run 用户输入 | 运行中不能暂停等决策（权限弹窗除外） |
| 退出后重开 | 退出 Claude Code → workflow 重新开始 |

## pitfall

- workflow 创建后通过 `/workflows` 查看进度，不是通过 terminal 监视
- workflow 的 JS 脚本在 `~/.claude/projects/<project>/workflows/scripts/` 下
- 触发时若 clauderemote 激活，Claude 会弹字母选项（Y/n）要求确认创建 — 选 Yes
- 已存在的 workflow 可通过 `s` 保存为命令重复使用
