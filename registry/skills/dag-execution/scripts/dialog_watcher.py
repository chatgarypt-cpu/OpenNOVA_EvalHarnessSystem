#!/usr/bin/env python3
"""Monitor a tmux session for:
1. Permission dialogs → auto-approve Enter
2. Claude idle at ❯ prompt but expected_outputs missing → nudge to continue
3. Queued messages (Press up to edit) → Enter to flush

Usage: python3 dialog_watcher.py <tmux-session-id> [task-dir]
Dispatch SOP: start immediately after 'relay run'."""

import subprocess
import sys
import time
import os
import re
from pathlib import Path

# Sound support (from WorkflowBase/infra/sound/)
_sound_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "infra" / "sound"
sys.path.insert(0, str(_sound_dir))
try:
    from sound_utils import load_config, resolve_sound, play_sound, get_sound
    _cfg = load_config(str(_sound_dir.parent.parent))
    _dw_cfg = _cfg.get("dialog_watcher", {})
    _auto_mode_sound = resolve_sound(
        _dw_cfg.get("auto_mode_sound", "Glass"), str(_sound_dir.parent.parent)
    )
    if not _dw_cfg.get("auto_mode_sound"):
        _auto_mode_sound = get_sound("auto_mode_sound", str(_sound_dir.parent.parent), _cfg)
except Exception:
    _auto_mode_sound = "Glass"

SESSION = sys.argv[1] if len(sys.argv) > 1 else "adarian_default"
TASK_DIR = sys.argv[2] if len(sys.argv) > 2 else None

DIALOG_PATTERNS = ["Do you want to proceed?", "Do you want to create",
                   "Do you want to overwrite"]

# Idle-at-prompt detection
IDLE_THRESHOLD = 30
NUDGE_COOLDOWN = 60
PROMPT_MARKER = "❯"
QUEUED_MESSAGE_MARKER = "Press up to edit"

_last_nudge_at = 0.0
_idle_since = 0.0
_last_was_idle = False
_last_pane_snapshot = ""
_last_pane_snapshot_at = 0.0
_pane_stable_since = 0.0


def expected_outputs_exist() -> bool:
    """Check if all expected_outputs from task_config exist on disk."""
    if not TASK_DIR:
        return True
    config_path = os.path.join(TASK_DIR, "dispatch", "task_config.yaml")
    if not os.path.exists(config_path):
        config_path = os.path.join(TASK_DIR, "dispatch", "task_config.json")
    if not os.path.exists(config_path):
        return True

    try:
        with open(config_path) as f:
            text = f.read()
        sections = text.split("expected_outputs:")
        if len(sections) < 2:
            return True
        outputs_section = sections[1].split("\n\n")[0]
        outputs_section = outputs_section.split("\n---")[0]
        outputs_section = outputs_section.split("\n  paths:")[0]
        outputs = re.findall(r"^\s*-\s+(.+)$", outputs_section, re.MULTILINE)
        for output in outputs:
            out_path = os.path.join(TASK_DIR, output.strip())
            if not os.path.exists(out_path):
                return False
        return True
    except Exception:
        return True


def pane_shows_idle(pane_text: str) -> bool:
    """Check if Claude is idle at the ❯ prompt.
    
    Detection (any of these triggers):
    1. ❯ in last line + expected_outputs don't exist (sufficient in most cases)
    2. ❯ in last line + pane content hasn't changed for 15+ seconds
    """
    lines = pane_text.strip().split("\n")
    last_line = lines[-1].strip() if lines else ""
    return PROMPT_MARKER in last_line


def pane_has_queued_messages(pane_text: str) -> bool:
    """Check if Claude has queued messages (❯ Press up to edit)."""
    return QUEUED_MESSAGE_MARKER in pane_text


def pane_content_stable_for(pane_text: str, threshold: float) -> bool:
    """Check if pane content hasn't changed for threshold seconds."""
    global _last_pane_snapshot, _last_pane_snapshot_at, _pane_stable_since
    
    now = time.time()
    # Hash the key parts of pane (last 30 lines, skip timestamps)
    key = pane_text.strip().split("\n")[-15:]
    snapshot = "\n".join(key)
    
    if snapshot == _last_pane_snapshot:
        if _pane_stable_since == 0.0:
            _pane_stable_since = now
        return (now - _pane_stable_since) >= threshold
    else:
        _last_pane_snapshot = snapshot
        _last_pane_snapshot_at = now
        _pane_stable_since = 0.0
        return False


def has_dialog(pane_text: str) -> str | None:
    """Check if any permission dialog pattern appears."""
    for line in pane_text.strip().split("\n"):
        for p in DIALOG_PATTERNS:
            if p in line:
                return p
    return None


while True:
    try:
        r = subprocess.run(["tmux", "capture-pane", "-t", SESSION, "-p", "-S", "-40"],
                           capture_output=True, text=True, timeout=10)
        pane = r.stdout
        now = time.time()

        # ── 1. Dialog approval ──
        dialog = has_dialog(pane)
        if dialog:
            subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"], timeout=5)
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] ✅ approved dialog: {dialog}", flush=True)
            time.sleep(2)

        # ── 2. Queued message flush ──
        # If Claude shows "Press up to edit queued messages", there's pending
        # text that needs Enter to send. Flush it.
        if pane_has_queued_messages(pane):
            subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"], timeout=5)
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] 📨 flushed queued message (Press up to edit)", flush=True)
            time.sleep(3)

        # ── 3. Idle-at-prompt detection ──
        is_idle = pane_shows_idle(pane)
        outputs_ok = expected_outputs_exist()

        if is_idle and not outputs_ok:
            if not _last_was_idle:
                _idle_since = now
                _last_was_idle = True
            elif now - _idle_since > IDLE_THRESHOLD and now - _last_nudge_at > NUDGE_COOLDOWN:
                subprocess.run(["tmux", "send-keys", "-t", SESSION, "继续", "Enter"], timeout=5)
                _last_nudge_at = now
                _idle_since = now
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] 🔔 nudged: Claude idle {IDLE_THRESHOLD}s with expected_outputs missing", flush=True)
        else:
            _last_was_idle = False

        time.sleep(3)

    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"[watcher] {e}", flush=True)
        time.sleep(5)
