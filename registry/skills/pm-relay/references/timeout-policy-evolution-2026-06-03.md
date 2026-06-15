# Timeout Policy Evolution — 2026-06-03

## 背景

Codex Tmux Executor 的监控循环最初有三个超时机制：
1. `no_output_timeout_sec` — pane 内容无变化超过此秒数 → timeout
2. `emergency_max_wall_time_sec` — 总运行时间超过此秒数 → timeout  
3. `›` prompt 短路 — 回到 prompt 但无预期产出 → idle_no_output

## 问题

Gary 反复纠正：静态 timeout 值是错误的修法。只要 user 能在 Terminal 窗口看到 agent 在跑、heartbeat 还在更新，就不应该 timeout。

> "只要 tmux session 还在、heartbeat 还在更新，executor 就不 timeout。"

## 最终修复（2026-06-03）

```python
# codex/tmux_executor.py 的 _monitor_execution() 中：
# 删除了以下所有代码：
if now - last_output > self._max_idle_sec:  # idle timeout
if now - self._started_at > self._max_wall_time:  # wall clock
if "› " in last_line:  # prompt 短路（改为不短路）
```

替换为：
1. session alive → 继续跑，session 消失 → 返回
2. 完成标记 → 返回
3. 预期产出 → 返回
4. 写 heartbeat → 下游 heartbeat_monitor.py 检测冻结

## 关键认识

- **完成标记不是万能的**：短任务（单文件创建）Codex 不输出 `[Session complete`
- **`›` prompt 不是空闲信号**：Codex 回到 prompt 后可能还在处理后续输入
- **heartbeat 是存活性信号**：不是进度信号。halo 活着就在写
- **heartbeat_monitor.py 是外部兜底**：检测 heartbeat 冻结 + outputs/ 交叉验证

## 修改文件

- `tools/pm_runtime/relay/codex/tmux_executor.py` — `_monitor_execution()` 重写
- `tools/sound_utils.py` — `play_sound()` 去除 timeout=3（导致声音截断）
- `tools/tools_config.yaml` — profile 路由（public/private）
