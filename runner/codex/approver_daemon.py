"""Codex auto-approval daemon for CodexTmuxExecutor R0.

Based on codex-yolo's approver-daemon.sh design:
  - 0.5s poll interval
  - 7 Codex prompt styles (command_execution, file_edits, mcp_tool_calls,
    trust_directory, full_access, network_host, proceed)
  - Primary + secondary signal matching (avoids false positives)
  - 2-second per-pane cooldown
  - Slash autocomplete picker veto
  - Audit log with timestamps
  - Self-terminates when tmux session is gone

Usage (background):
  python3 codex_approver_daemon.py <session-name> [poll-interval] [audit-log]
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path


# ── Prompt detection patterns (from codex-yolo approver-daemon.sh) ──

# Primary signals — question/header phrases
PRIMARY_PATTERNS = [
    r"Would you like to run",
    r"Would you like to make",
    r"Allow Codex to",
    r"Approve app tool call",
    r"Do you trust the contents",
    r"Enable full access",
]

# Secondary signal 1: Approval options
APPROVAL_PATTERNS = [
    r"Yes, just this once",
    r"Yes, proceed\s*\(y\)",
    r"Yes, continue",
    r"Yes, and don't ask",
    r"Run the tool and continue",
    r"Apply full access",
    r"Yes, and allow this host",
]

# Secondary signal 2: Denial/context
CONTEXT_PATTERNS = [
    r"No, and tell Codex",
    r"Decline this tool call",
    r"Go back without",
    r"Cancel this",
    r"may have side effects",
    r"may access external",
    r"may modify",
    r"untrusted",
    r"prompt injection",
]

# Slash picker veto — detect /xxx autocomplete popup
SLASH_PICKER_PATTERN = r"^[ ]*(❯[ ]*)?/[a-z][-a-z]+[ ]{2,}"

# Approval key selection — if "Yes, proceed (y)" visible, send 'y' not Enter
PROCEED_Y_PATTERN = r"Yes, proceed\s*\(y\)"


def detect_prompt(content: str) -> str | None:
    """Detect permission prompts using primary + secondary signals.

    Returns a pattern description string if matched, None otherwise.
    Based on codex-yolo's detect_prompt() with primary/secondary signal logic.
    """
    lines = content.split("\n")
    tail = "\n".join(lines[-25:])  # Last 25 lines — where dialogs appear
    tail_lower = tail.lower()

    has_primary = any(
        p.lower() in tail_lower for p in PRIMARY_PATTERNS
    )
    has_approval = any(
        p.lower() in tail_lower for p in APPROVAL_PATTERNS
    )
    has_context = any(
        p.lower() in tail_lower for p in CONTEXT_PATTERNS
    )

    # Require primary + at least one secondary
    if has_primary and (has_approval or has_context):
        parts = ["question"]
        if has_approval:
            parts.append("approval")
        if has_context:
            parts.append("context")
        return "+".join(parts)

    # Fallback: approval + context without explicit question header
    # (some dialogs render the question above visible area)
    if has_approval and has_context:
        return "approval+context"

    return None


def detect_slash_picker(content: str) -> bool:
    """Detect if slash command autocomplete popup is visible.

    Vetoes approval to avoid selecting autocomplete items.
    Based on codex-yolo's detect_slash_picker().
    """
    lines = content.split("\n")
    tail = "\n".join(lines[-15:])
    count = len(re.findall(SLASH_PICKER_PATTERN, tail, re.MULTILINE))
    return count >= 2


def detect_completed_or_idle(content: str) -> bool:
    """Detect if Codex is back at prompt (idle/completed, not in a dialog)."""
    lines = content.split("\n")
    # Last non-empty line should be the prompt indicator
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            # After completion, prompt shows "› " or "› Implement"
            if stripped.startswith("›"):
                return True
            break
    return False


def approval_key(content: str) -> str:
    """Determine the correct approval key (Enter or y).

    Some Codex prompts show "Yes, proceed (y)" which requires 'y' not Enter.
    """
    tail = "\n".join(content.split("\n")[-25:])
    if re.search(PROCEED_Y_PATTERN, tail, re.IGNORECASE):
        return "y"
    return "Enter"


def pane_list(session: str) -> list[str]:
    """Get all pane IDs in a session."""
    r = subprocess.run(
        ["tmux", "list-panes", "-s", "-t", session, "-F", "#{pane_id}"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if r.returncode != 0:
        return []
    return [p.strip() for p in r.stdout.split("\n") if p.strip()]


def pane_capture(pane: str) -> str | None:
    """Capture pane content."""
    r = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", pane],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def session_exists(session: str) -> bool:
    """Check if tmux session still exists."""
    r = subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return r.returncode == 0


def send_key(pane: str, key: str) -> None:
    """Send a key to a tmux pane."""
    subprocess.run(
        ["tmux", "send-keys", "-t", pane, key],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
    )


def main():
    session = sys.argv[1] if len(sys.argv) > 1 else "codex-tmux-default"
    poll_interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
    audit_log = sys.argv[3] if len(sys.argv) > 3 else f"/tmp/codex-approver-{session}.log"

    audit_path = Path(audit_log)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    def audit(msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(audit_path, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    audit(f"Daemon started for session={session} poll={poll_interval}s")
    print(f"[codex-approver] monitoring session={session} poll={poll_interval}s", flush=True)

    last_approved: dict[str, float] = {}
    cooldown = 2.0

    while session_exists(session):
        panes = pane_list(session)
        if not panes:
            time.sleep(poll_interval)
            continue

        now = time.time()

        for pane in panes:
            # Cooldown check
            last = last_approved.get(pane, 0.0)
            if now - last < cooldown:
                continue

            content = pane_capture(pane)
            if not content or not content.strip():
                continue

            # Veto: slash autocomplete picker
            if detect_slash_picker(content):
                continue

            # Check if Codex is back at idle prompt (not in a dialog)
            if detect_completed_or_idle(content):
                continue

            # Detect permission prompt
            pattern = detect_prompt(content)
            if pattern:
                key = approval_key(content)
                send_key(pane, key)
                last_approved[pane] = time.time()
                audit(f"APPROVED pane={pane} pattern={pattern} key={key}")
                print(f"[codex-approver] approved: pane={pane} pattern={pattern} key={key}", flush=True)

        time.sleep(poll_interval)

    audit("Daemon exiting: session no longer exists")
    print(f"[codex-approver] session {session} gone, exiting", flush=True)


if __name__ == "__main__":
    main()
