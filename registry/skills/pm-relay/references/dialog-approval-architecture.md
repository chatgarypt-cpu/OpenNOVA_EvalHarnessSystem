# Dialog Approval Architecture (2026-06-02)

> Class-level design: dialog permission architecture for PM Runtime relay dispatches.

## Principle

The system has two dialog approval mechanisms. They are NOT redundant — they are **primary + fallback**:

```
dialog_watcher (external companion script)
  ├── PRIMARY — handles all bash permission dialogs
  ├── 0.5s polling, full pane scan
  ├── "Do you want to proceed?" → auto Enter (selects Yes)
  ├── "Do you want to create" → auto Enter
  ├── "Do you want to overwrite" → auto Enter
  ├── "switch to auto mode" → NOT auto-approved (plays sound, notifies Hermes CLI)
  └── Any other dialog watcher can pattern-match → auto Enter

ClaudeDialogHandler (in tmux_executor.py)
  └── FALLBACK — for dialogs watcher cannot match
      ├── BASH_PERMISSION_DIALOG: NEVER HOLDs. Returns dialog_type=None
      │   (running continues). Defers to watcher.
      └── OTHER_CONFIRMATION_DIALOG / FILE_EDIT / TRUST: still auto-approved
```

## Why

Previous design: DialogHandler was primary, watcher was optional add-on. This caused two problems:

1. **Race condition**: DialogHandler and watcher both scanned the same pane. If DialogHandler couldn't parse the bash command, it would HOLD and exit (code 5) before the watcher had time to send Enter. The watcher never got a chance.

2. **False positives**: DialogHandler's bash parser failed on commands like `grep -n "trigger" relay_runner.py` in workflow context (not a standard Bash tool invocation). It went to HOLD for a safe read-only command.

## Implementation

Changed in `tmux_executor.py` (dialog section 349-358):

Before:
```python
reason = str(bash_match.get("reason") or "...")
return DialogDecision(
    runtime_state="hold",
    dialog_type="BASH_PERMISSION_DIALOG",
    action="hold",
)
```

After:
```python
reason = str(bash_match.get("reason") or "...")
return DialogDecision(
    runtime_state="running",
    dialog_type=None,  # ← key change: no dialog = continue
    action="deferred_to_external_watcher",
)
```

## Companion processes

Every relay dispatch MUST start COMPANION PROCESSES alongside the relay runner:

```
relay dispatch
  ├── dialog_watcher.py       ← handles permission dialogs during execution
  └── heartbeat_monitor.py    ← detects task completion (three signals)
```

Both are background processes started immediately after `relay run`.

## Verification

- Normal `grep` / `ls` / `cat` commands in workflow context should no longer trigger HOLD
- Watcher should approve them within 0.5s of dialog appearance
- Auto-mode dialogs should NOT be auto-approved — user decides in Hermes CLI
- If a dialog appears that NEITHER watcher nor DialogHandler can handle, it falls through to the observer (Terminal window) where the user handles it directly
