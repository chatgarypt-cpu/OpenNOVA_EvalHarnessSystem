# Tools 基础设施（2026-06-02）

## 组成

```
tools/
├── tools_config.yaml       ← 外置配置（profile 切换 + 各工具参数）
├── sound_utils.py          ← 共享声音模块（解析/播放/配置加载）
├── sounds/                 ← 自定义声音目录（.mp3 / .wav / .aiff / .m4a）
│   ├── mission_complete.mp3  — GTA 通关音效（任务完成）
│   ├── wasted.mp3            — GTA 死亡音效（任务崩溃）
│   ├── sad_violin.mp3        — 悲伤小提琴（失败通知）
│   └── movie_start.mp3       — 电影开场（auto-mode 通知）
├── dialog_watcher.py       ← 权限对话框主批准
├── heartbeat_monitor.py    ← 心跳完成检测 + 声音通知
└── node_validation.py      （如前所述）
```

## Profile 切换机制

`tools_config.yaml` 的 `profile` 字段控制全局声音场景：

```yaml
profile: public    # Glass 系统音，工位/图书馆安全
# profile: private  # 自定义音效，在家用
```

配置文件中的 `profiles` 段定义各场景的声音映射：

```yaml
profiles:
  public:
    heartbeat_sound: "Glass"
    auto_mode_sound: "Glass"
    crash_sound: "Glass"
  private:
    heartbeat_sound: "mission_complete.mp3"
    auto_mode_sound: "movie_start.mp3"
    crash_sound: "wasted.mp3"
```

字段优先级：工具配置段中显式指定 `sound` 字段 > profile 默认值。

## Sound Resolution 顺序

1. 绝对路径（`/Users/...`）→ 直接使用
2. `tools/sounds/<name>` → 自定义声音
3. `/System/Library/Sounds/<name>.aiff` → 系统声音
4. 任何扩展名（.mp3 / .wav / .m4a / .aac）→ afplay 原生支持
5. Fallback → Glass.aiff

## Heartbeat Monitor 信号检测

| 信号 | 条件 | 行为 |
|------|------|------|
| Terminal state | runtime_state 变成 completed/hold/error | 立即播 |
| Outputs 存在 | outputs/ 有文件但 heartbeat 说 running | 立即播 |
| 心跳停 5s | 5 秒无新 heartbeat | 查 outputs/，有则播，无则等二次确认 |
| 心跳停 10s | 仍无恢复 | 播（崩溃信号） |
