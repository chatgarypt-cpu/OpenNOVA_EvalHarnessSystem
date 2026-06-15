# 迭代计划冲突裁定规则 — 2026-06-02 确立

> 此文件记录用户与草稿迭代计划之间的冲突裁定模式。
> 首次发生场景：Registry R1.0 Model Patch 草案与 Gary 口头范围冲突。

## 冲突记录

| 冲突项 | 草案说法 | Gary 口头 | 裁定 |
|--------|---------|-----------|------|
| 阶段划分 | 一次 DAG 包含 full repair + self-maintenance + drift check | 三步走：R1.0 Model Patch → R1.1 Drift Check → R1.2 Self-Maintenance | 以 Gary 为准，拆分三步 |
| 写 registry | "不直接写 registry YAML" | 修 mcp_registry.yaml、executor_registry.yaml、README、schema | 以 Gary 为准，允许写 |
| 执行方式 | Claude Code agent team | relay runner dispatch | 以 Gary 为准，走 relay |
| 启动时机 | 计划就绪 | "先别动手" | 以 Gary 为准，不启动 |

## 裁定后最终范围（Registry R1.0 Model Patch）

只做 Gary 口头圈定的 8 项：
1. mcp-zhipu 按运行现实更新
2. fallback-subprocess 风险标注
3. README 补全
4. plugin 语义承认（schema 加字段，不建独立 YAML）
5. optional depends_on / used_by
6. hook_registry 注册 task-status-writer.py 为 candidate
7. 其他风险项修明显字段
8. drift check 只设计格式，不写 updater
