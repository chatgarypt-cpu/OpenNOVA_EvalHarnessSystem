# Observer Mode Timeout Fix (2026-06-02)

## 问题

Relay runner 的 `_monitor_loop_impl()` 在 `_ready_timeout()`（默认 30s）到期后 exit 5 退出，导致 prompt 永远没发给 Claude。Claude 在 tmux 里空等，用户必须手动发 prompt。

这是 I5 已知 bug 的根因。更深层的问题是：observer 模式下不应该有任何人工超时退出，用户盯着就看得到问题。

## 修复（tmux_executor.py）

### 1. ready_timeout 
```python
# 改前：exit 5 退出
if not prompt_sent and now - started_at > self._ready_timeout():
    return self._finish("hold", "manual_confirmation_required",
                        "Claude ready prompt timeout", status, 5)

# 改后：打日志继续等
if not prompt_sent and now - started_at > self._ready_timeout():
    if not self._ready_timeout_warned:
        self._ready_timeout_warned = True
        self.writer.write_progress("waiting_for_ready",
            f"Claude not yet ready after ready_timeout_sec, continuing to wait")
    last_output_time = now  # 防止 no_output_timeout 误触发
```

### 2. no_output_timeout / max_wall_time

```python
# 改前：无条件退出
if now - last_output_time > self._no_output_timeout():
    return self._finish(...)

# 改后：observer 模式下跳过
if not self.observer_mode:
    if now - last_output_time > self._no_output_timeout():
        return self._finish(...)
    if now - started_at > self._max_wall_time():
        return self._finish(...)
```

### 3. 新增 `_ready_timeout_warned` 初始化

在 ClaudeTmuxExecutor.__init__() 中：
```python
self._ready_timeout_warned = False
```

## 原则

"用户盯着，你怕什么超时。" — Gary, 2026-06-02

observer 模式下所有超时都是 suspect signal，不是死亡判据。人工超时只在无 observer 的后台任务中生效。
