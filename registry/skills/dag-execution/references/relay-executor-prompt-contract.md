# Relay → Executor Prompt Contract v0.2

> Hermes 调用 Executor 时使用的 prompt 结构规范。
> Executor 的行为依赖于此结构的完整性。

## Prompt 结构（按顺序）

```yaml
task_id: <唯一标识>
execution_mode: direct | workflow | agent-team

prerequisites:
  - skill: karpathy-coding

objective: 1-2 句话描述任务

inputs:
  - <需要读取的文件路径>

constraints:
  scope: "修改范围"
  forbidden: "禁止操作"
```

## 区域说明

| 区域 | 必填 | 位置 | 说明 |
|------|------|------|------|
| task_id | ✅ | prompt 开头 | 任务标识 |
| execution_mode | ✅ | prompt 开头 | 三值枚举，废弃 dag_mode |
| prerequisites | ⬜ | prompt 前部 | Executor 原生 skill，自动加载 |
| objective | ✅ | prompt 中部 | 1-2 句话 |
| inputs | ⬜ | prompt 中部 | 文件路径 |
| constraints | ⬜ | prompt 中部 | timeout、scope、禁止操作 |
| expected_outputs | ✅ | prompt 末尾 | relay 自动追加 |

## Executor 行为

1. 解析 execution_mode
2. 按 prerequisites 列表加载 skill（通过 Skill tool）
3. 读取 inputs 列表中的文件
4. 读取 expected_outputs（prompt 末尾），规划输出路径
5. 根据 execution_mode 执行：
   - **direct**: 直接执行任务
   - **workflow**: 创建 Workflow script，用 Workflow tool 执行
   - **agent-team**: 按 agent 配置编排多 Agent
6. 写入 expected_outputs，验证 receipt

## 三种 execution_mode 的选择

| 模式 | 触发方式 | 安全级 | 适用场景 |
|------|---------|--------|---------|
| direct | 默认模式 | 低 | 单 agent 直接执行 |
| agent-team | prompt 引用 @agent-name | 中 | 审查类任务 |
| workflow | prompt 开头写 "use a workflow to:" | 高 | 多 agent DAG，需 Owner 确认 |

## 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-02 | v0.1 | 初稿 |
| 2026-06-02 | v0.2 | expected_outputs 移至末尾；去掉 /clauderemote 重复；karpathy-coding 为原生 skill；dag_mode→execution_mode |
