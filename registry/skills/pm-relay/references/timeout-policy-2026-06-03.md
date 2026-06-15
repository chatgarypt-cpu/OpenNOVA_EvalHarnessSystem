# 监控循环超时策略去静态化（2026-06-03）

## 背景

监控循环原本有四种超时机制：
1. `ready_timeout` — Agent 未在 X 秒内就绪就退出
2. `no_output_timeout` — pane 不动 X 秒就超时
3. `max_wall_time` — 跑 X 秒就超时
4. `›` prompt 检测 — 回到 prompt 但无产出就 idle_no_output

2026-06-03 Gary 三次纠正后，全部移除。

## 最终原则

**只要 tmux session 还在、heartbeat 还在更新，executor 就不 timeout。**

## 实现

`codex/tmux_executor.py` 的 `_monitor_execution()` 现在只检查：
- session 存活 → 必须存在
- 完成标记（`[Session complete` / Token usage）
- 预期产出文件
- heartbeat 写入（供下游 heartbeat_monitor.py 消费）

没有 idle timeout，没有 wall clock timeout。

## 影响

- `runtime_control` 配置节不再需要。task_config.yaml 不应包含此节。
- 已在 dispatch-prompt-authoring skill §2 中明确禁止。
- executor 现在会无限等待直到 session 消失或任务完成。
- 下游 `heartbeat_monitor.py` 负责检测 heartbeat 冻结并播报声音。

## Claude executor 侧的影响

`claude/tmux_executor.py` 中有一处 bug 在本次发现并修复：
- `from .relay_runner import write_legacy_progress` → `from ..relay_runner import write_legacy_progress`
- 单点（`.`）解析为 `claude/relay_runner`（不存在），应使用双点（`..`）解析为 `relay/relay_runner`
- 此 bug 在 2026-06-03 之前一直潜伏，当 `relay_runner.run_task()` 的完整 import 链触发时才暴露
