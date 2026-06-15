#!/usr/bin/env python3.14
"""Monitor a tmux session for permission dialogs and auto-approve.

DESIGN (2026-06-04):
  dialog_watcher is the PRIMARY approval mechanism for permission dialogs.
  It pattern-matches common dialog text and sends keyboard input.

  ONLY last 50 lines of pane are scanned (speed).
  AUTO_MODE (workflow auto-mode) is played as notification, NOT auto-approved.

  Auto-detects tmux session from task_dir/runtime/session.yaml.
  If no task_dir given, uses first arg as explicit tmux session name.

Usage:
    python3 dialog_watcher.py <task_dir>          # auto-detect tmux session
    python3 dialog_watcher.py <tmux-session-id>    # direct tmux session (legacy)
"""

import json
import subprocess
import sys
import time
from pathlib import Path


def _resolve_session() -> tuple[str, Path | None]:
    """Resolve tmux session name from args.

    Returns (session_name, task_dir).
    - If arg is a directory with runtime/session.yaml → read tmux session ID
    - If arg is not a directory → treat as explicit session name
    - No args → "adarian_default" (fallback)
    """
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        session_yaml = candidate / "runtime" / "session.yaml"
        if candidate.is_dir() and session_yaml.exists():
            try:
                raw = session_yaml.read_text(encoding="utf-8")
                for line in raw.split("\n"):
                    if "tmux_session_id:" in line:
                        sid = line.split(":", 1)[1].strip().strip("\"'")
                        if sid:
                            return sid, candidate.resolve()
            except Exception:
                pass
            # Fallback: try heartbeat.json for task context
        if candidate.is_dir():
            return candidate.name, candidate.resolve()
        # It's a session name
        return sys.argv[1], None
    return "adarian_default", None


SESSION, TASK_DIR = _resolve_session()

# ── Common Claude Code permission dialog patterns ──────────────────────
AUTO_APPROVE_PATTERNS = [
    # File operations
    "Do you want to proceed?",
    "Do you want to create",
    "Do you want to overwrite",
    "Would you like to create",
    "Would you like to write",
    # General approval
    "Proceed?",
    "proceed?",
    "Continue?",
    "continue?",
    # Permission
    "Allow Claude to",
    "claude wants to",
    "Allow this",
    # Bash/command execution
    "Run this command?",
    "Run this bash",
    "Execute this command",
    # Create/edit/read file permission
    "Create file",
    "Write file",
    "Edit file",
    "Read file",
    # macOS specific
    "grant permission",
    "needs permission",
    # Generic y/n
    "y/N",
    "(Y/n)",
    # Pasted text confirmation — press Enter to submit pasted prompt
    "[Pasted text",
]

AUTO_MODE_PATTERNS = [
    "switch to auto mode",
    "auto mode",
    "workflows run best with",
]

# ── Prompt text submit pattern ───────────────────────────
# When executor prefills text at the ❯ prompt (clauderemote on, task prompt),
# the watcher detects text after ❯ and presses Enter to submit.
PROMPT_CHAR = "\u276f"  # ❯ (Claude Code prompt character)

GLASS_SOUND = "/System/Library/Sounds/Pop.aiff"

POLL_INTERVAL = 0.3  # seconds between scans
SUBMIT_COOLDOWN = 3   # seconds between submit Enter presses to avoid dups



def _tmux_has_session(session: str) -> bool:
    """Check if tmux session exists."""
    r = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True, timeout=5,
    )
    return r.returncode == 0


def _pane_tail(session: str, patterns: list[str]) -> str | None:
    """Scan tmux pane for any matching pattern (full pane scan)."""
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return None
    for line in r.stdout.split("\n"):
        for p in patterns:
            if p in line:
                return p
    return None


def _last_nonempty_line(session: str) -> str | None:
    """Get the last non-empty line from tmux pane."""
    r = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", "-5"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return None
    for line in reversed(r.stdout.split("\n")):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _send_enter(session: str) -> None:
    subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], timeout=5)


def _play_sound() -> None:
    try:
        subprocess.run(["afplay", GLASS_SOUND], timeout=3, capture_output=True)
    except Exception:
        pass


def main():
    print(f"[watcher] monitoring tmux: {SESSION}", flush=True)
    if TASK_DIR:
        print(f"[watcher] task_dir: {TASK_DIR}", flush=True)
    last_submit_at = 0.0  # debounce for prompt submit Enter

    while True:
        try:
            # Check session still exists
            if not _tmux_has_session(SESSION):
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ⏹️ tmux session '{SESSION}' gone — exiting", flush=True)
                break

            # ── Auto-mode detection (never auto-approve) ──────────
            if _pane_tail(SESSION, AUTO_MODE_PATTERNS):
                _play_sound()
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ⚠️ AUTO-MODE dialog — check Terminal window", flush=True)
                time.sleep(5)
                continue

            # ── Prompt text submit ──────────────────────────────
            # Executor prefilled a command at the ❯ prompt (e.g. /clauderemote on).
            # Watcher detects the prefilled command and presses Enter to submit it.
            now = time.time()
            last_line = _last_nonempty_line(SESSION)
            if last_line and last_line.startswith(PROMPT_CHAR) and len(last_line) > len(PROMPT_CHAR) and now - last_submit_at > SUBMIT_COOLDOWN:
                text_after = last_line[len(PROMPT_CHAR):].strip()
                if text_after and text_after.startswith("/"):
                    _send_enter(SESSION)
                    last_submit_at = now
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] ⌨️ submitted command: {text_after}", flush=True)
                    time.sleep(1.0)
                    continue

            # ── Permission dialog auto-approve ────────────────────
            matched = _pane_tail(SESSION, AUTO_APPROVE_PATTERNS)
            if matched:
                _send_enter(SESSION)
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] ✅ approved: {matched}", flush=True)
                time.sleep(1.5)
                continue

            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] ⚠️ [watcher] {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
