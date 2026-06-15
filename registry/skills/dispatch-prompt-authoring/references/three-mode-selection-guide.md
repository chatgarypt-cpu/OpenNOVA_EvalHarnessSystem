# 三种执行模式选择指南

依据 dag_execution_mode 字段的值选择：

```
任务需要多个独立视角同时看同一份输入？
  ├─ Yes → agent-team（@agent + synthesis）
  │        审查类任务：Reality Review、代码审查、设计验证
  │        触发：prompt 引用 @agent-name
  │        定义文件：~/.claude/agents/<name>.md
  │        安全级：中（Agent 只读或 synthesis 约束）
  │
  └─ No → 需要编排大量 agent（10+）且可重复？
           ├─ Yes → workflow（JS 脚本 + Workflow tool）
           │        批量扫描、大规模迁移、系统质量验证
           │        触发：prompt 开头写 "use a workflow to: <任务>"
           │        安全级：高（执行代码、改文件、全自动）
           │        必须 Owner 显式确认
           │
           └─ No → direct（单 agent 直接执行）
                   基线扫描、代码集成、脚本实现
                   触发：默认模式，不需要任何额外指令
                   安全级：低
```

## 在 prompt 中的写法

| 模式 | prompt 写法 |
|------|------------|
| direct | 正常写任务描述，不需要前缀 |
| agent-team | prompt 中引用 `@agent-name`，或描述审查角色 |
| workflow | prompt 以 `use a workflow to: <任务目标>` 开头 |

## 在 task_config.yaml 中的声明

```yaml
dag_execution_mode: direct      # 默认值，不写也生效
dag_execution_mode: agent-team
dag_execution_mode: workflow    # 启用 §5.3 安全隔离 + output validation + Repair Agent
```

## 安全隔离范围

| 隔离 | direct | agent-team | workflow |
|------|--------|------------|----------|
| safety_context (bash 白名单) | ❌ | ❌ | ✅ |
| output_validator (格式校验) | ❌ | ❌ | ✅ |
| Repair Agent (tmux paste) | ❌ | ❌ | ✅ |
