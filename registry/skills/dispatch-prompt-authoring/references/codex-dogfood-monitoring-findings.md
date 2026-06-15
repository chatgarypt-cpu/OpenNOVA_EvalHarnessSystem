# Codex Dogfood Monitoring Findings（2026-06-03）

## 场景
CodexTmuxExecutor R0 监控修复狗食测试。Codex 做了三件事：写一个 txt 文件到 outputs/、回到 prompt、退出。

## 问题链

### 问题 1：Codex 写错了路径
- prompt 说"在 outputs/ 下创建 monitoring_summary.txt"
- Codex 在 `cwd/project-root/outputs/` 下创建了文件
- Executor 在 `task_dir/outputs/` 下等文件
- 预期产出检测永远不命中

**修复：** `dispatch-prompt-authoring` 避坑清单增加了"Codex 路径理解偏差"条目

### 问题 2：完成标记不适用于短任务
- `[codex-agent: Session complete` 和 Token usage 行只在**多步骤长会话**中输出
- 简单单文件创建 → Codex 直接回到 prompt，没有任何结束标记
- 完成标记检测对短任务完全无效

**当前覆盖率：**

| 任务类型 | 完成标记 | 预期产出 | 动态空闲 |
|---------|---------|---------|---------|
| 长任务（多步修改） | ✅ | ✅ | ✅ 兜底 |
| 短任务（单文件） | ❌ 不输出 | ✅ 唯一可靠 | ⚠️ 但浪费 120s |

### 问题 3：prompt 写得太抽象
- "在 outputs/ 下"对 Codex 来说 → cwd/outputs/
- 应该用 `tasks/active/<task_id>/outputs/` 显式指定全路径

## 结论
1. Codex 短任务依赖预期产出检测和动态空闲超时，完成标记不可靠
2. prompt 路径必须精确到 task_dir 级别
3. runtime_control 不要在 task_config 中硬写——动态 pane 检测已覆盖
