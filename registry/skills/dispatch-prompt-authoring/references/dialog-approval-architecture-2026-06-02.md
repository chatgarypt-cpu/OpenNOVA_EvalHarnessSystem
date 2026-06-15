# Dialog Approval Architecture (2026-06-02)

## Current Design

```
dialog_watcher（外部 companion）— PRIMARY
  ├── 0.5s 轮询 pane 全文本
  ├── 普通对话框（proceed/create/overwrite）→ 自动按 Enter
  └── workflow auto-mode（"switch to auto mode"）→ 播声音 + 通知，等人批

ClaudeDialogHandler（tmux_executor.py 内嵌）— FALLBACK
  ├── TRUST / FILE_CREATION → 自动批准（路径白名单）
  ├── FILE_EDIT → 自动批准
  ├── BASH_PERMISSION → 不再 HOLD，返回 dialog_type=None（running 继续）
  └── watcher 匹配不到的罕见情况 → 兜底
```

## Key Changes from Previous Design

Before 2026-06-02:
- DialogHandler was the primary approval mechanism
- BASH_PERMISSION dialogs that weren't whitelisted would HOLD (exit 5)
- dialog_watcher was secondary (2s poll, often too slow)

After 2026-06-02:
- dialog_watcher is primary (0.5s poll, full pane)
- DialogHandler defers on BASH_PERMISSION (dialog_type=None, no HOLD)
- Auto-mode dialogs require human approval via Hermes CLI

## Why

The old design had a race condition: DialogHandler would HOLD before watcher could auto-approve. Gary's philosophy is "可见、可接管" — make decisions visible rather than having the system guess. Simple pattern matching (watcher) is more reliable than command parsing (DialogHandler) for typical bash permission dialogs.

## Files Changed

- `tools/dialog_watcher.py` — Rewritten: 0.5s poll, auto-mode detection with sound, sound_utils integration
- `tools/pm_runtime/relay/tmux_executor.py` — `ClaudeDialogHandler._classify()` line 349: BASH_PERMISSION not allowed → dialog_type=None (not HOLD)
- `tools/sound_utils.py` — Shared sound resolution module
- `tools/tools_config.yaml` — External config for sound names, poll intervals
