"""tmux-backed Claude Code executor for Relay Runtime R0."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from WorkflowBase.runner.output_validator import validate_outputs
from WorkflowBase.runner.relay_runner import (
    append_registry_event,
    ensure_task_dirs,
    now_iso,
    repair_node,
    write_progress,
    write_task_state,
    write_yaml,
)
from .safety_context import ExecutionContext, build_context_from_config


TERMINAL_STATES = {"executor_completed", "hold", "timeout", "error", "session_lost"}
TRUNCATION_MARKERS = ["ctrl+o to expand", "truncated"]


@dataclass
class TmuxRunResult:
    exit_code: int
    classification: dict[str, Any]
    stdout_path: Path
    stderr_path: Path
    raw_output_path: Path


@dataclass
class ArtifactStatus:
    all_present: bool
    statuses: list[dict[str, Any]]
    hold_reason: str | None = None
    path_mismatch: list[dict[str, Any]] | None = None


@dataclass
class PaneState:
    runtime_state: str
    changed: bool
    hold_reason: str | None = None
    error_reason: str | None = None
    error_pattern: str | None = None


@dataclass
class DialogDecision:
    runtime_state: str | None = None
    message: str | None = None
    dialog_type: str | None = None
    matched_keyword: str | None = None
    raw_target: str | None = None
    resolved_target: str | None = None
    target_resolution_strategy: str | None = None
    target_path: str | None = None
    action: str | None = None
    choice_letter: str | None = None
    signature: str | None = None
    auto_approved: bool = False
    dedup_hit: bool = False
    fallback_used: bool = False
    action_skipped: bool = False


class TmuxSessionManager:
    """Owns tmux availability, session identity, lifecycle, send, and capture behavior."""

    def __init__(self, session_id: str, workdir: Path) -> None:
        self.session_id = session_id
        self.workdir = workdir

    @staticmethod
    def sanitize_session_id(raw: str) -> str:
        # tmux uses . as pane separator (session:window.pane)
        # and : as window separator; both must be avoided in session names
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip())
        cleaned = cleaned.strip("_-")
        return cleaned[:80] or "relay_task"

    def tmux_available(self) -> bool:
        return shutil.which("tmux") is not None

    def has_session(self) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", self.session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def create_or_reuse(self) -> bool:
        if self.has_session():
            return True
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", self.session_id, "-c", str(self.workdir)],
            check=True,
        )
        return False

    def send_literal(self, text: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", self.session_id, "-l", text], check=True)

    def send_enter(self) -> None:
        subprocess.run(["tmux", "send-keys", "-t", self.session_id, "Enter"], check=True)

    def paste_text(self, text: str) -> None:
        buffer_name = f"{self.session_id}_prompt"
        subprocess.run(
            ["tmux", "load-buffer", "-b", buffer_name, "-"],
            input=text,
            text=True,
            check=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-d", "-b", buffer_name, "-t", self.session_id],
            check=True,
        )

    def capture(self) -> str:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session_id, "-p", "-S", "-2000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "tmux capture-pane failed")
        return result.stdout

    def start_claude(self, command: list[str]) -> None:
        self.send_literal(" ".join(_shell_quote(part) for part in command))
        self.send_enter()

    def schedule_cleanup(self, retention_seconds: int, run_id: str = "") -> None:
        if retention_seconds <= 0:
            subprocess.run(["tmux", "kill-session", "-t", self.session_id], check=False)
            return
        token = Path(f"/tmp/tmux_cleanup_{self.session_id}")
        try:
            token.write_text(run_id)
        except OSError:
            pass
        shell_command = (
            f"sleep {int(retention_seconds)}; "
            f"stored=\"$(cat /tmp/tmux_cleanup_{self.session_id} 2>/dev/null)\"; "
            f"if [ \"$stored\" != \"{run_id}\" ]; then exit 0; fi; "
            f"rm -f /tmp/tmux_cleanup_{self.session_id}; "
            f"tmux has-session -t {self.session_id} 2>/dev/null && "
            f"tmux kill-session -t {self.session_id}"
        )
        subprocess.run(["tmux", "run-shell", "-b", shell_command], check=False)

    def claim_cleanup(self, run_id: str) -> None:
        """Claim cleanup ownership: write our run_id, cancelling any old cleanup timer."""
        token = Path(f"/tmp/tmux_cleanup_{self.session_id}")
        try:
            token.write_text(run_id)
        except OSError:
            pass

    def attach_observer(self, attach_mode: str) -> None:
        """Open an observer window attached to this tmux session.
        attach_mode: 'terminal_window' (macOS Terminal.app), 'none' (no-op)."""
        if attach_mode == "terminal_window":
            script = (
                f'tell app "Terminal" to do script '
                f'"tmux attach-session -t {self.session_id}"'
            )
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                timeout=10,
            )


class ClaudeDialogHandler:
    """Owns Claude Code confirmation-dialog policy and the resulting automatic responses."""

    TRUST_DIALOG_KEYWORDS = ["Yes, I trust this folder", "1. Yes, I trust", "I trust this folder"]
    FILE_CREATION_KEYWORDS = ["Do you want to create", "Create file", "create this file"]
    FILE_EDIT_KEYWORDS = ["Do you want to make this edit"]
    BASH_PERMISSION_KEYWORDS = ["Do you want to proceed?", "Bash(", "Bash command", "Tool: Bash"]
    PERMISSION_DIALOG_KEYWORDS = ["dangerously-skip-permissions", "Yes, I accept"]
    OTHER_CONFIRMATION_KEYWORDS = ["Are you sure", "Please confirm", "Confirm this action"]

    @staticmethod
    def _last_screen_region(pane_text: str, num_lines: int = 50) -> str:
        """Return the bottom N lines of the pane — where dialogs actually appear.
        Prevents keyword false positives from report content in scrollback."""
        lines = pane_text.splitlines()
        return "\n".join(lines[-num_lines:]) if len(lines) > num_lines else pane_text

    def __init__(self, artifacts: "ArtifactDetector") -> None:
        self.artifacts = artifacts
        self._handled_visible_signatures: set[str] = set()
        self.last_decision: dict[str, Any] = {"initialized": False}

    def handle(
        self,
        pane_text: str,
        manager: TmuxSessionManager,
        *,
        remote_mode_active: bool,
        safety_context: "ExecutionContext | None" = None,
    ) -> DialogDecision:
        decision = self._classify(pane_text, safety_context=safety_context)
        self.last_decision = self._decision_payload(decision)
        if decision.dialog_type is None:
            self._handled_visible_signatures.clear()
            return decision
        if decision.runtime_state == "hold":
            return decision
        if decision.signature in self._handled_visible_signatures:
            decision.dedup_hit = True
            decision.action_skipped = True
            decision.auto_approved = False
            self.last_decision = self._decision_payload(decision)
            return decision
        if decision.action in {"accept_trust", "accept_file_creation", "accept_bash_permission", "accept_file_edit"}:
            self._send_approval(decision, manager)
            self._handled_visible_signatures.add(str(decision.signature))
            self.last_decision = self._decision_payload(decision)
        return decision

    def _classify(
        self, pane_text: str, *, safety_context: "ExecutionContext | None" = None
    ) -> DialogDecision:
        recent = self._last_screen_region(pane_text)
        # PERMISSION_DIALOG: only scan the bottom screen region, not the full pane.
        # Full-pane scan causes false positives when report content mentions
        # "dangerously-skip-permissions" in design review items.
        permission_keyword = self._first_keyword(recent, self.PERMISSION_DIALOG_KEYWORDS)
        if permission_keyword:
            return DialogDecision(
                runtime_state="hold",
                message="permission bypass dialog detected; R1 does not allow this mode",
                dialog_type="PERMISSION_DIALOG",
                matched_keyword=permission_keyword,
                action="hold",
                signature=self._signature("PERMISSION_DIALOG", permission_keyword, None, "hold"),
            )

        trust_keyword = self._first_keyword(pane_text, self.TRUST_DIALOG_KEYWORDS)
        if trust_keyword:
            choice = self._choice_for_term(pane_text, ["trust", "yes", "allow"])
            action = "accept_trust"
            return DialogDecision(
                runtime_state="waiting_for_ready",
                message="workspace trust dialog accepted",
                dialog_type="TRUST_DIALOG",
                matched_keyword=trust_keyword,
                action=action,
                choice_letter=choice,
                signature=self._signature("TRUST_DIALOG", trust_keyword, None, action),
                auto_approved=True,
                fallback_used=choice is None,
                target_path=None,
            )

        file_keyword = self._first_keyword(pane_text, self.FILE_CREATION_KEYWORDS)
        if file_keyword:
            match = self.artifacts.match_file_creation_target(pane_text)
            if match.get("allowed"):
                target_path = str(match.get("target_path") or "")
                action = "accept_file_creation"
                choice = self._choice_for_term(pane_text, ["yes", "create", "allow"])
                return DialogDecision(
                    runtime_state="running",
                    message="expected output file creation accepted",
                    dialog_type="FILE_CREATION_DIALOG",
                    matched_keyword=file_keyword,
                    raw_target=str(match.get("target_raw") or ""),
                    resolved_target=target_path,
                    target_resolution_strategy=str(match.get("target_resolution_strategy") or ""),
                    target_path=target_path,
                    action=action,
                    choice_letter=choice,
                    signature=self._signature("FILE_CREATION_DIALOG", file_keyword, target_path, action),
                    auto_approved=True,
                    fallback_used=choice is None,
                )
            reason = str(match.get("reason") or "file creation dialog target is outside expected_outputs")
            target_path = str(match.get("target_path") or match.get("target_raw") or "")
            return DialogDecision(
                runtime_state="hold",
                message=reason,
                dialog_type="FILE_CREATION_DIALOG",
                matched_keyword=file_keyword,
                raw_target=str(match.get("target_raw") or ""),
                resolved_target=str(match.get("target_path") or ""),
                target_resolution_strategy=str(match.get("target_resolution_strategy") or ""),
                target_path=target_path,
                action="hold",
                signature=self._signature("FILE_CREATION_DIALOG", file_keyword, target_path, "hold"),
            )

        edit_keyword = self._first_keyword(pane_text, self.FILE_EDIT_KEYWORDS)
        if edit_keyword:
            return DialogDecision(
                runtime_state="running",
                message="file edit dialog auto-approved",
                dialog_type="FILE_EDIT_DIALOG",
                matched_keyword=edit_keyword,
                action="accept_file_edit",
                signature=self._signature("FILE_EDIT_DIALOG", edit_keyword, None, "accept_file_edit"),
                auto_approved=True,
            )

        bash_match = self.artifacts.match_bash_permission(pane_text, safety_context)
        if bash_match.get("is_bash_permission"):
            keyword = str(bash_match.get("matched_keyword") or "Do you want to proceed?")
            command = str(bash_match.get("command") or "")
            target_path = str(bash_match.get("target_path") or "")
            if bash_match.get("allowed"):
                # Try "don't ask again" first for safe commands
                choice = self._choice_for_term(pane_text, ["don't ask", "and don't ask"])
                if not choice:
                    choice = self._choice_for_term(pane_text, ["yes", "allow", "proceed", "run"])
                action = "accept_bash_permission"
                return DialogDecision(
                    runtime_state="running",
                    message="safe bash permission dialog accepted",
                    dialog_type="BASH_PERMISSION_DIALOG",
                    matched_keyword=keyword,
                    target_path=target_path,
                    action=action,
                    choice_letter=choice,
                    signature=self._signature("BASH_PERMISSION_DIALOG", command or keyword, target_path, action),
                    auto_approved=True,
                    fallback_used=choice is None,
                )
            # NOT allowed — don't HOLD, defer to dialog_watcher (external)
            # dialog_watcher handles bash permissions by pattern matching + send-keys
            # DialogHandler is fallback for dialogs watcher cannot match
            reason = str(bash_match.get("reason") or "bash permission dialog is not safe to auto-approve")
            return DialogDecision(
                runtime_state="running",
                message=f"bash permission deferred to external watcher: {reason}",
                dialog_type=None,
                matched_keyword=keyword,
                target_path=target_path,
                action="deferred_to_external_watcher",
                signature=self._signature("BASH_PERMISSION_DIALOG", command or keyword, target_path, "deferred_to_external_watcher"),
                auto_approved=False,
            )

        other_keyword = self._first_keyword(pane_text, self.OTHER_CONFIRMATION_KEYWORDS)
        if other_keyword:
            return DialogDecision(
                runtime_state="hold",
                message="unknown confirmation dialog requires owner review",
                dialog_type="OTHER_CONFIRMATION_DIALOG",
                matched_keyword=other_keyword,
                action="hold",
                signature=self._signature("OTHER_CONFIRMATION_DIALOG", other_keyword, None, "hold"),
            )
        return DialogDecision()

    def _send_approval(self, decision: DialogDecision, manager: TmuxSessionManager) -> None:
        if decision.choice_letter:
            manager.send_literal(decision.choice_letter)
            manager.send_enter()
            decision.fallback_used = False
            return
        manager.send_enter()
        decision.fallback_used = True

    def _choice_for_term(self, pane_text: str, terms: list[str]) -> str | None:
        """Find the selector key for a dialog choice matching any term.

        Handles two formats:
        - Letter-based (clauderemote): [A] Yes
        - Numeric (default/fallback): 1. Yes  or  ❯ 1. Yes
        """
        for line in pane_text.splitlines():
            # Letter format: [A] label
            match = re.search(r"\[([A-Z])\]\s*(.+)", line)
            if match and any(term in match.group(2).lower() for term in terms):
                return match.group(1)
            # Numeric format: ❯ 1. label  or  2. label
            match = re.search(r"(?:❯\s*)?(\d+)\.\s*(.+)", line)
            if match and any(term in match.group(2).lower() for term in terms):
                return match.group(1)
        return None

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _first_keyword(text: str, keywords: list[str]) -> str | None:
        for keyword in keywords:
            if keyword in text:
                return keyword
        return None

    @staticmethod
    def _signature(dialog_type: str, keyword: str, target_path: str | None, action: str) -> str:
        normalized_keyword = " ".join(keyword.lower().split())
        normalized_target = target_path or ""
        return f"{dialog_type}|{action}|{normalized_keyword}|{normalized_target}"

    @staticmethod
    def _decision_payload(decision: DialogDecision) -> dict[str, Any]:
        return {
            "initialized": True,
            "last_dialog_type": decision.dialog_type,
            "last_dialog_action": decision.action,
            "last_dialog_signature": decision.signature,
            "matched_keyword": decision.matched_keyword,
            "raw_target": decision.raw_target,
            "resolved_target": decision.resolved_target,
            "target_resolution_strategy": decision.target_resolution_strategy,
            "target_path": decision.target_path,
            "choice_letter": decision.choice_letter,
            "auto_approved": decision.auto_approved,
            "dedup_hit": decision.dedup_hit,
            "fallback_used": decision.fallback_used,
            "action_skipped": decision.action_skipped,
            "message": decision.message,
        }

class PaneStateParser:
    """Owns conversion of captured Claude pane text into coarse Relay runtime states."""

    READY_INDICATORS = ["\u276f", "What can I help"]
    RUNNING_INDICATORS = [
        "esc to interrupt",
        "Beboppin",
        "Blanching",
        "\u273d",  # ✽
        "\u27d0",  # ⟐ four-teardrop-spoked asterisk
        "Harmonizing",  # Claude long thought/synthesis phase
    ]
    ERROR_INDICATORS = [
        "APIConnectionError",
        "An error occurred",
        "Unexpected error",
        "FAILED",
        "rate limit",
        "interrupted",
        "command not found",
        "No such file or directory",
        "Permission denied",
        "Traceback (most recent call last)",
    ]

    RECENT_LINES = 50

    def __init__(self) -> None:
        self._last_capture: str | None = None

    @staticmethod
    def _recent_text(pane_text: str, num_lines: int = RECENT_LINES) -> str:
        lines = pane_text.splitlines()
        return "\n".join(lines[-num_lines:]) if len(lines) > num_lines else pane_text

    def parse(self, pane_text: str, *, default_state: str) -> PaneState:
        changed = pane_text != self._last_capture
        self._last_capture = pane_text
        recent = self._recent_text(pane_text)
        lowered = recent.lower()
        for marker in self.ERROR_INDICATORS:
            if marker.lower() in lowered:
                return PaneState(
                    "error",
                    changed,
                    error_reason=f"error indicator detected in pane: {marker}",
                    error_pattern=marker,
                )
        recent = self._recent_text(pane_text)
        if any(marker in recent for marker in self.RUNNING_INDICATORS):
            return PaneState("running", changed)
        if self._last_nonempty_line(pane_text).startswith(tuple(self.READY_INDICATORS)):
            return PaneState("waiting_for_input", changed)
        if any(marker in pane_text for marker in self.READY_INDICATORS):
            return PaneState("waiting_for_ready", changed)
        return PaneState(default_state, changed)

    @staticmethod
    def _last_nonempty_line(text: str) -> str:
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped
        return ""


class ArtifactDetector:
    """Owns expected output path validation and artifact completion checks."""

    BASH_PROCEED_KEYWORDS = ["Do you want to proceed?", "Proceed?", "proceed?"]
    BASH_CONTEXT_KEYWORDS = ["Bash(", "Bash command", "Tool: Bash", "Bash tool", "Run command"]
    FORBIDDEN_BASH_WORDS = {
        "sudo",
        "rm",
        "rmdir",
        "mv",
        "cp",
        "chmod",
        "chown",
        "curl",
        "wget",
        "ssh",
        "scp",
        "git",
        "dd",
        "kill",
        "killall",
        "launchctl",
        "mkfs",
        "shutdown",
        "reboot",
    }
    FORBIDDEN_SHELL_TOKENS = [";", "&&", "||", "|", "$(", "`"]
    FORBIDDEN_TARGET_CHARS = [";", "&", "|", "$", "`", "\n", "\r"]

    def __init__(self, task_dir: Path, expected_outputs: list[str], workdir: Path) -> None:
        self.task_dir = task_dir.resolve()
        self.workdir = workdir.resolve()
        self.outputs_dir = (self.task_dir / "outputs").resolve()
        self.configured_outputs = expected_outputs
        self.expected_paths = [self._resolve_expected(path) for path in expected_outputs]

    def validate(self) -> str | None:
        for path in self.expected_paths:
            try:
                path.relative_to(self.outputs_dir)
            except ValueError:
                return f"expected output outside task outputs directory: {path}"
        return None

    def ensure_dirs(self) -> None:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        for path in self.expected_paths:
            path.parent.mkdir(parents=True, exist_ok=True)

    def status(self) -> ArtifactStatus:
        statuses: list[dict[str, Any]] = []
        all_present = bool(self.expected_paths)
        for path in self.expected_paths:
            exists = path.exists() and path.is_file()
            size = path.stat().st_size if exists else 0
            text_tail = path.read_text(encoding="utf-8", errors="replace")[-2000:] if exists else ""
            truncated = any(marker in text_tail for marker in TRUNCATION_MARKERS)
            ready = exists and size > 0 and not truncated
            statuses.append(
                {
                    "path": str(path),
                    "exists": exists,
                    "size_bytes": size,
                    "ready": ready,
                    "truncated_marker_detected": truncated,
                }
            )
            all_present = all_present and ready
        if not self.expected_paths:
            return ArtifactStatus(False, statuses, "executor_options.expected_outputs is required")
        mismatches = self.path_mismatches()
        hold_reason = "expected output path mismatch detected" if mismatches else None
        return ArtifactStatus(all_present, statuses, hold_reason, mismatches)

    def pane_mentions_expected_output(self, pane_text: str) -> bool:
        for path in self.expected_paths:
            if str(path) in pane_text or path.name in pane_text:
                return True
        return False

    def match_file_creation_target(self, pane_text: str) -> dict[str, Any]:
        target_raw = self._extract_creation_target(pane_text)
        if not target_raw:
            for path in self.expected_paths:
                if str(path) in pane_text:
                    return {
                        "allowed": True,
                        "target_raw": str(path),
                        "target_path": path,
                        "target_resolution_strategy": "absolute_expected_output_match",
                    }
            return {
                "allowed": False,
                "target_raw": None,
                "target_path": None,
                "target_resolution_strategy": "unresolved",
                "reason": "file creation dialog target path could not be confirmed",
            }
        unsafe_reason = self._unsafe_dialog_target_reason(target_raw)
        if unsafe_reason:
            return {
                "allowed": False,
                "target_raw": target_raw,
                "target_path": None,
                "target_resolution_strategy": "rejected_unsafe_target",
                "reason": unsafe_reason,
            }
        target_path = self._resolve_dialog_target(target_raw)
        if target_path and target_path in set(self.expected_paths):
            return {
                "allowed": True,
                "target_raw": target_raw,
                "target_path": target_path,
                "target_resolution_strategy": "absolute_expected_output_match",
            }
        basename_match = self._resolve_expected_basename(target_raw)
        if basename_match.get("allowed"):
            return basename_match
        if basename_match.get("reason"):
            return basename_match
        reason = "file creation dialog target is outside expected_outputs"
        if target_path is None:
            reason = "file creation dialog target is relative or ambiguous; expected absolute task output path"
        return {
            "allowed": False,
            "target_raw": target_raw,
            "target_path": target_path,
            "target_resolution_strategy": "unresolved",
            "reason": reason,
        }

    def match_bash_permission(
        self, pane_text: str, safety_context: "ExecutionContext | None" = None
    ) -> dict[str, Any]:
        proceed_keyword = self._first_keyword(pane_text, self.BASH_PROCEED_KEYWORDS)
        context_keyword = self._first_keyword(pane_text, self.BASH_CONTEXT_KEYWORDS)
        if not proceed_keyword or not context_keyword:
            return {"is_bash_permission": False}
        command = self._extract_bash_command(pane_text)
        if not command:
            return {
                "is_bash_permission": True,
                "allowed": False,
                "matched_keyword": proceed_keyword,
                "command": None,
                "target_path": None,
                "reason": "bash permission command could not be parsed",
            }
        safety = self._evaluate_bash_command(command, safety_context)
        return {
            "is_bash_permission": True,
            "matched_keyword": proceed_keyword,
            "command": command,
            **safety,
        }

    def path_mismatches(self) -> list[dict[str, Any]]:
        mismatches: list[dict[str, Any]] = []
        roots = [self.workdir / "outputs", Path.cwd().resolve() / "outputs"]
        for expected in self.expected_paths:
            try:
                relative = expected.relative_to(self.outputs_dir)
            except ValueError:
                continue
            for root in roots:
                candidate = (root / relative).resolve()
                if candidate == expected:
                    continue
                if candidate.exists() and not expected.exists():
                    mismatches.append(
                        {
                            "expected_path": str(expected),
                            "mismatched_path": str(candidate),
                            "reason": "output appeared outside task_dir/outputs",
                        }
                    )
        return mismatches

    def output_contract(self) -> str:
        lines = [
            "## IMPORTANT OUTPUT CONTRACT",
            "",
            "You must write the final deliverable to the exact expected output file(s) listed below.",
            "You must actually write the file to disk. Do NOT only display the answer in the terminal.",
            "Do NOT claim the task is complete unless the file has actually been written to disk.",
            "",
            "Required output file(s):",
            "",
        ]
        for index, path in enumerate(self.expected_paths, 1):
            lines.append(f"{index}. {path}")
        lines.extend(
            [
                "",
                "Do NOT write outputs to:",
                f"- {self.workdir / 'outputs'}",
                f"- {Path.cwd().resolve() / 'outputs'}",
                "- any path outside the task_dir/outputs/ directory.",
                "",
                "After writing each file, you MUST verify it by:",
                "  - Reading the file back from disk, OR",
                "  - Checking that it exists on disk and is non-empty.",
                "",
                "At the end, print this receipt:",
                "",
                "ARTIFACT_WRITE_RECEIPT:",
                f"  path: {{expected_output_path}}",
                "  written_to_disk: true",
                "  read_back_verified: true",
                "  final_status: completed",
                "",
                "If you cannot write the file, stop and explain the reason. Do NOT pretend the task is complete.",
            ]
        )
        return "\n".join(lines)

    def metadata(self, status: ArtifactStatus | None = None) -> dict[str, Any]:
        current = status or self.status()
        return {
            "configured": self.configured_outputs,
            "resolved_absolute": [str(path) for path in self.expected_paths],
            "detected": current.statuses,
            "path_mismatch": current.path_mismatch or [],
        }

    def _resolve_expected(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        return (self.task_dir / path).resolve()

    def _resolve_dialog_target(self, value: str) -> Path | None:
        path = Path(value)
        if not path.is_absolute():
            return None
        return path.resolve()

    def _resolve_expected_basename(self, value: str) -> dict[str, Any]:
        path = Path(value)
        if path.is_absolute():
            return {"allowed": False, "reason": None}
        if path.name != value:
            return {
                "allowed": False,
                "target_raw": value,
                "target_path": None,
                "target_resolution_strategy": "rejected_relative_path",
                "reason": "file creation dialog target is relative but not a basename",
            }
        matches = [expected for expected in self.expected_paths if expected.name == path.name]
        if len(matches) == 1:
            return {
                "allowed": True,
                "target_raw": value,
                "target_path": matches[0],
                "target_resolution_strategy": "expected_output_basename_match",
            }
        if len(matches) > 1:
            return {
                "allowed": False,
                "target_raw": value,
                "target_path": None,
                "target_resolution_strategy": "ambiguous_basename_match",
                "reason": "file creation dialog basename matches multiple expected outputs",
            }
        # No exact basename match — try prefix match (handles tmux-wrapped filenames)
        prefix_matches = [expected for expected in self.expected_paths if expected.name.startswith(path.name)]
        if len(prefix_matches) == 1:
            return {
                "allowed": True,
                "target_raw": value,
                "target_path": prefix_matches[0],
                "target_resolution_strategy": "expected_output_basename_prefix_match",
            }
        # Try substring match (handles tmux-wrapped filenames clipped on both sides)
        # E.g. "ompletion-test.m" is a substring of "completion-test.md" (positions 1:17)
        substr_matches = [expected for expected in self.expected_paths if path.name in expected.name]
        if len(substr_matches) == 1:
            return {
                "allowed": True,
                "target_raw": value,
                "target_path": substr_matches[0],
                "target_resolution_strategy": "expected_output_basename_substr_match",
            }
        return {
            "allowed": False,
            "target_raw": value,
            "target_path": None,
            "target_resolution_strategy": "basename_not_in_expected_outputs",
            "reason": "file creation dialog basename does not match expected_outputs",
        }

    def _unsafe_dialog_target_reason(self, value: str) -> str | None:
        if any(token in value for token in self.FORBIDDEN_TARGET_CHARS):
            return "file creation dialog target contains shell control characters"
        try:
            path = Path(value)
            parts = path.parts
        except Exception:
            return "file creation dialog target could not be normalized"
        if ".." in parts:
            return "file creation dialog target contains parent directory traversal"
        return None

    def _evaluate_bash_command(
        self, command: str, safety_context: "ExecutionContext | None" = None
    ) -> dict[str, Any]:
        # Allow && echo|printf|true chains (Claude's mkdir && echo OK pattern)
        if "&&" in command:
            safe_chain = re.match(
                r"^(.*?)\s*&&\s*(echo|printf|true)\b.*?$",
                command,
                re.IGNORECASE | re.DOTALL,
            )
            if safe_chain:
                check_command = safe_chain.group(1).strip()
                if check_command and not any(
                    t in check_command for t in self.FORBIDDEN_SHELL_TOKENS
                ):
                    # Evaluate the safe prefix; if it passes, allow the full command
                    return self._evaluate_bash_command(check_command, safety_context)
        # §5.3 ExecutionContext whitelist check (additional layer)
        if safety_context is not None and not safety_context.validate_bash_command(command):
            return self._bash_denied(command, "bash command rejected by §5.3 execution context whitelist")
        if any(token in command for token in self.FORBIDDEN_SHELL_TOKENS):
            return self._bash_denied(command, "bash command contains shell control syntax")
        try:
            parts = shlex.split(command, posix=True)
        except ValueError as exc:
            return self._bash_denied(command, f"bash command could not be parsed: {exc}")
        if not parts:
            return self._bash_denied(command, "bash command is empty")
        executable = Path(parts[0]).name
        if executable in self.FORBIDDEN_BASH_WORDS:
            return self._bash_denied(command, f"bash command is not whitelisted: {executable}")
        if any(Path(part).name in self.FORBIDDEN_BASH_WORDS for part in parts[1:]):
            return self._bash_denied(command, "bash command contains forbidden operation")

        targets: list[Path] = []
        action = executable
        if executable in {"mkdir", "touch", "ls", "head", "tail", "python3", "python"}:
            targets = self._targets_from_simple_file_command(parts[1:]) if executable in {"mkdir", "touch"} else []
            action = executable
        elif executable in {"cat", "echo", "printf"}:
            redirect_target = self._redirection_target(command)
            targets = [redirect_target] if redirect_target else []
            action = "write_redirection"
        elif executable == "tee":
            targets = self._targets_from_simple_file_command(parts[1:])
        else:
            return self._bash_denied(command, f"bash command is not whitelisted: {executable}")

        if not targets:
            return self._bash_denied(command, "bash permission target path could not be parsed")
        denied = [path for path in targets if not self._path_allowed_for_bash(path)]
        if denied:
            return {
                "allowed": False,
                "action": action,
                "target_path": str(denied[0]),
                "target_paths": [str(path) for path in targets],
                "reason": "bash permission target path is outside task outputs/runtime allowlist",
            }
        return {
            "allowed": True,
            "action": action,
            "target_path": str(targets[0]),
            "target_paths": [str(path) for path in targets],
            "reason": None,
        }

    def _targets_from_simple_file_command(self, args: list[str]) -> list[Path]:
        targets: list[Path] = []
        for arg in args:
            if arg.startswith("-"):
                continue
            path = Path(arg)
            if not path.is_absolute():
                return []
            targets.append(path.resolve())
        return targets

    @staticmethod
    def _redirection_target(command: str) -> Path | None:
        matches = re.findall(r"(?:^|\s)(?:>>|>)\s*([^\s]+)", command)
        if not matches:
            return None
        raw = matches[-1].strip().strip("'\"")
        path = Path(raw)
        if not path.is_absolute():
            return None
        return path.resolve()

    def _path_allowed_for_bash(self, path: Path) -> bool:
        resolved = path.resolve()
        allowed_roots = [
            self.outputs_dir,
            (self.task_dir / "runtime").resolve(),
            (self.task_dir / "logs").resolve(),
        ]
        for root in allowed_roots:
            if resolved == root:
                return True
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _bash_denied(command: str, reason: str) -> dict[str, Any]:
        return {
            "allowed": False,
            "action": None,
            "target_path": None,
            "target_paths": [],
            "reason": reason,
            "command": command,
        }

    @staticmethod
    def _first_keyword(text: str, keywords: list[str]) -> str | None:
        for keyword in keywords:
            if keyword in text:
                return keyword
        return None

    @staticmethod
    def _extract_creation_target(pane_text: str) -> str | None:
        patterns = [
            r"Do you want to create(?: the file)?\s+[`'\u201c\u201d]?([^`'\u201c\u201d\n\?]+)",
            r"Create file[:\\s]+[`'\u201c\u201d]?([^`'\u201c\u201d\n\?]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, pane_text, re.IGNORECASE)
            if not match:
                continue
            target = match.group(1).strip()
            target = target.strip("`'\"\\u201c\\u201d ")
            if target:
                return target
        return None

    # Known command keywords that start a new argument group
    _CMD_KEYWORDS = {
        "mkdir", "touch", "cat", "echo", "chmod", "cp", "mv", "rm", "python",
        "python3", "npm", "git", "sed", "awk", "curl", "wget", "ls", "cd",
        "find", "grep", "pip", "node", "npx", "cargo", "rustc", "docker",
    }

    @staticmethod
    def _extract_bash_command(pane_text: str) -> str | None:
        patterns = [
            r"Bash\((.*?)\)",
            r"(?:Bash command|Command|command):\s*`([^`]+)`",
            r"(?:Bash command|Command|command):\s*([^\n]+)",
            r"^\s*\$\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, pane_text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if not match:
                continue
            command = " ".join(match.group(1).strip().split())
            if command:
                return ArtifactDetector._fix_continuation(command)
        # 5th: clauderemote multi-line bash dialog format
        # "Bash command" header, indented command lines, ended by "Do you want to proceed"
        claude_match = re.search(
            r"Bash command\s*\n+((?:\s*\S.*\n?)+?)\s*\n+Do you want to proceed",
            pane_text,
            re.IGNORECASE | re.DOTALL,
        )
        if claude_match:
            raw = claude_match.group(1)
            lines = []
            for line in raw.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                # Skip description lines (e.g. "Check syntax of modified file")
                if stripped[0].isupper() and not stripped.startswith(("/", '"', "'")):
                    first_word = stripped.split(maxsplit=1)[0] if stripped.split() else ""
                    if first_word not in {
                        "python3", "python", "node", "npm", "git", "echo",
                        "cat", "ls", "cd", "mkdir", "touch", "rm", "cp",
                        "mv", "sed", "awk", "curl", "wget", "pip", "npx",
                    }:
                        continue
                lines.append(stripped)
            command = " ".join(lines)
            if command:
                return ArtifactDetector._fix_continuation(command)
        return None

    @staticmethod
    def _fix_continuation(command: str) -> str:
        """Detect and fix tmux word-wrapped continuation fragments."""
        tokens = command.split()
        if len(tokens) <= 2:
            return command

        fixed: list[str] = [tokens[0]]
        for tok in tokens[1:]:
            # If it looks like a command keyword or a flag, keep as separate token
            if tok in ArtifactDetector._CMD_KEYWORDS or tok.startswith("-"):
                fixed.append(tok)
            # If previous token ends with a path separator, join without space (continuation)
            elif fixed and (fixed[-1].endswith("/") or fixed[-1].endswith("\\\\")):
                fixed[-1] += tok
            else:
                fixed.append(tok)

        return " ".join(fixed)


class RuntimeFileWriter:
    """Owns tmux executor runtime files without changing subprocess executor output contracts."""

    def __init__(self, config: dict[str, Any], session_id: str) -> None:
        self.config = config
        self.session_id = session_id
        self.dirs = ensure_task_dirs(config)
        self.heartbeat_seq = 0

    @property
    def pane_capture_path(self) -> Path:
        return self.dirs["runtime_dir"] / "pane_capture.log"

    @property
    def raw_output_path(self) -> Path:
        return self.dirs["logs_dir"] / "raw_output.jsonl"

    @property
    def stdout_path(self) -> Path:
        return self.dirs["logs_dir"] / "stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.dirs["logs_dir"] / "stderr.log"

    def write_session(self, *, command: list[str], reused: bool, workdir: Path) -> None:
        write_yaml(
            self.dirs["runtime_dir"] / "session.yaml",
            {
                "task_id": self.config.get("task_id"),
                "tmux_session_id": self.session_id,
                "executor_type": "claude",
                "execution_mode": "tmux_interactive",
                "workdir": str(workdir),
                "command": command,
                "reuse_existing_session": reused,
                "created_at": now_iso(),
            },
        )

    def append_capture(self, pane_text: str, runtime_state: str) -> None:
        self.pane_capture_path.parent.mkdir(parents=True, exist_ok=True)
        with self.pane_capture_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n--- capture_at: {now_iso()} state: {runtime_state} ---\n")
            handle.write(pane_text)
            if not pane_text.endswith("\n"):
                handle.write("\n")
        self._append_raw({"event": "pane_capture", "runtime_state": runtime_state})

    def write_heartbeat(
        self,
        runtime_state: str,
        *,
        last_output_at: str | None,
        output_changed: bool,
        artifact_status: ArtifactStatus,
        hold_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.heartbeat_seq += 1
        payload = {
            "task_id": self.config.get("task_id"),
            "tmux_session_id": self.session_id,
            "executor_type": "claude",
            "execution_mode": "tmux_interactive",
            "runtime_state": runtime_state,
            "last_heartbeat_at": now_iso(),
            "last_output_at": last_output_at,
            "output_changed": output_changed,
            "expected_outputs_status": artifact_status.statuses,
            "hold_reason": hold_reason,
            "runtime_pid": os.getpid(),
            "heartbeat_seq": self.heartbeat_seq,
        }
        if extra:
            payload.update(extra)
        path = self.dirs["runtime_dir"] / "heartbeat.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (self.dirs["runtime_dir"] / "heartbeat_history.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        relay_heartbeat = self.dirs["runtime_dir"] / "relay_heartbeat.txt"
        relay_heartbeat.write_text(
            "\n".join(
                [
                    "legacy_compat: true",
                    "compat_for: hermes_old_relay",
                    f"task_id: {payload.get('task_id')}",
                    f"runtime_state: {runtime_state}",
                    f"timestamp: {payload.get('last_heartbeat_at')}",
                    f"runtime_pid: {payload.get('runtime_pid')}",
                    "executor_pid:",
                    f"heartbeat_seq: {self.heartbeat_seq}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_progress(self, runtime_state: str, message: str, *, extra: dict[str, Any] | None = None) -> None:
        if not extra:
            write_progress(self.config, runtime_state, message)
            return
        payload = {
            "task_id": self.config.get("task_id"),
            "runtime_state": runtime_state,
            "message": message,
            "updated_at": now_iso(),
            **extra,
        }
        write_yaml(self.dirs["runtime_dir"] / "progress.yaml", payload)
        from WorkflowBase.runner.relay_runner import write_legacy_progress

        write_legacy_progress(self.config, runtime_state, message)

    def write_task_state(self, runtime_state: str, *, task_status: str = "running", extra: dict[str, Any] | None = None) -> None:
        write_task_state(self.config, runtime_state, task_status=task_status, extra=extra)

    def write_result(
        self,
        *,
        runtime_state: str,
        classification: str,
        reason: str,
        artifact_status: ArtifactStatus,
        notification: dict[str, Any],
        exit_code: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "legacy_compat": True,
            "compat_for": "hermes_old_relay",
            "task_id": self.config.get("task_id"),
            "runtime_state": runtime_state,
            "returncode": 0 if exit_code == 0 else None,
            "classification": classification,
            "confidence": "high",
            "requires_independent_review": classification != "agent_completed",
            "evidence_paths": [
                str(self.pane_capture_path),
                str(self.dirs["runtime_dir"] / "session.yaml"),
                str(self.raw_output_path),
            ],
            "expected_outputs_status": artifact_status.statuses,
            "hold_reason": reason if runtime_state == "hold" else None,
            "reason": reason,
            "closeout_claimed": False,
            "created_at": now_iso(),
        }
        if extra:
            payload.update(extra)
        payload.update(notification)
        result_path = self.dirs["runtime_dir"] / "result.json"
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._append_raw({"event": "result_written", "runtime_state": runtime_state, "exit_code": exit_code})
        return payload

    def write_emergency_result(
        self,
        *,
        error_type: str,
        error_message: str,
        intended_runtime_state: str,
        exception_stage: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "legacy_compat": True,
            "compat_for": "hermes_old_relay",
            "task_id": self.config.get("task_id"),
            "runtime_state": "error",
            "intended_runtime_state": intended_runtime_state,
            "error_type": error_type,
            "error_message": error_message,
            "exception_stage": exception_stage,
            "session_preserved": True,
            "fallback_result_writer_used": True,
            "created_at": now_iso(),
        }
        if extra:
            payload.update(extra)
        result_path = self.dirs["runtime_dir"] / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def finalize_stream_files(self) -> None:
        self.stdout_path.write_text("", encoding="utf-8")
        self.stderr_path.write_text("", encoding="utf-8")
        if not self.raw_output_path.exists():
            self.raw_output_path.write_text("", encoding="utf-8")

    def _append_raw(self, event: dict[str, Any]) -> None:
        self.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"timestamp": now_iso(), **event}
        with self.raw_output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SoundNotifier:
    """Owns best-effort terminal or macOS notification for final executor states."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.enabled = options.get("enable_sound_notification", True) is not False
        self.sound = str(options.get("notification_sound") or "Glass")

    def notify(self, runtime_state: str) -> dict[str, Any]:
        if not self.enabled or runtime_state not in TERMINAL_STATES:
            return {
                "notification_attempted": False,
                "notification_success": False,
                "notification_method": "disabled",
                "notification_error": None,
            }
        sound_path = Path("/System/Library/Sounds") / f"{self.sound}.aiff"
        if shutil.which("afplay") and sound_path.exists():
            return self._try_command(["afplay", str(sound_path)], "macos_afplay")
        if shutil.which("say"):
            return self._try_command(["say", "Relay task finished"], "macos_say")
        try:
            print("\a", end="", flush=True)
            return {
                "notification_attempted": True,
                "notification_success": True,
                "notification_method": "terminal_bell",
                "notification_error": None,
            }
        except Exception as exc:  # pragma: no cover - defensive fallback
            return {
                "notification_attempted": True,
                "notification_success": False,
                "notification_method": "failed",
                "notification_error": str(exc),
            }

    @staticmethod
    def _try_command(command: list[str], method: str) -> dict[str, Any]:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {
                "notification_attempted": True,
                "notification_success": True,
                "notification_method": method,
                "notification_error": None,
            }
        except Exception as exc:
            return {
                "notification_attempted": True,
                "notification_success": False,
                "notification_method": "failed",
                "notification_error": str(exc),
            }


class ClaudeTmuxExecutor:
    """Owns the R0 orchestration loop that wires tmux, Claude, artifacts, runtime files, and notification."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.dirs = ensure_task_dirs(config)
        self.options = config.get("executor_options") if isinstance(config.get("executor_options"), dict) else {}
        self.control = config.get("runtime_control") if isinstance(config.get("runtime_control"), dict) else {}
        self.task_id = str(config.get("task_id") or "relay-task")
        self.workdir = Path(str(config.get("workdir") or self.options.get("workdir") or self.dirs["task_dir"])).resolve()
        self.session_id = TmuxSessionManager.sanitize_session_id(f"adarian_{self.task_id}")
        self.manager = TmuxSessionManager(self.session_id, self.workdir)
        self.artifacts = ArtifactDetector(self.dirs["task_dir"], self._expected_outputs(), self.workdir)
        self.dialogs = ClaudeDialogHandler(self.artifacts)
        self.parser = PaneStateParser()
        self.writer = RuntimeFileWriter(config, self.session_id)
        self.notifier = SoundNotifier(self.options)
        # §5.3 execution context: only loaded in workflow mode
        if config.get("dag_execution_mode") == "workflow":
            self.safety_context = build_context_from_config(config)
        else:
            self.safety_context = None
        self.observer_mode = str(self.options.get("observer_mode", "true")).lower() == "true"
        self.observer_attach = str(self.options.get("observer_attach", "terminal_window")).lower()
        self.last_output_at: str | None = None
        self.remote_mode_status: dict[str, Any] = {
            "attempted": False,
            "active": False,
            "fallback_used": False,
            "fallback_strategy": "safe_whitelist_enter",
        }
        self.error_counts: dict[str, int] = {}
        self.last_error_at: str | None = None
        self._last_error_signature: str | None = None
        self._exception_stage = "not_started"
        self.run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        self._artifact_stable_consecutive_count = 0
        self._ready_timeout_warned = False

    def _spawn_monitors(self) -> None:
        """Auto-start dialog_watcher and heartbeat_monitor as nohup daemons.

        Both monitors are spawned in detached process groups (nohup) so they
        survive even if the relay runner's parent process exits.
        The task_dir is passed so monitors auto-detect tmux session from runtime/.
        """
        task_dir = str(self.dirs["task_dir"])
        proj_root = str(Path(__file__).resolve().parent.parent.parent)
        python = sys.executable

        watcher_script = f"{proj_root}/registry/skills/dispatch-prompt-authoring/scripts/dialog_watcher.py"
        heartbeat_script = f"{proj_root}/infra/heartbeat_monitor.py"

        for label, script, args in [
            ("dialog_watcher", watcher_script, [task_dir]),
            ("heartbeat_monitor", heartbeat_script, [task_dir]),
        ]:
            try:
                cmd = [python, script] + args
                log_dir = Path(task_dir) / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"{label}.log"
                log_file = open(log_path, "a", encoding="utf-8")
                subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [executor] spawned {label} for {task_dir}", flush=True)
            except Exception as e:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [executor] failed to spawn {label}: {e}", flush=True)

    def run(self) -> TmuxRunResult:
        self.writer.finalize_stream_files()
        command = self._claude_command()
        self._launch_command = command
        invalid_reason = self._validate_start(command)
        if invalid_reason:
            return self._finish("hold", "configuration_blocked", invalid_reason, ArtifactStatus(False, []), 5)

        self.artifacts.ensure_dirs()
        self.writer.write_task_state("starting", extra={"tmux_session_id": self.session_id})
        self.writer.write_progress("starting", "tmux interactive executor starting")
        try:
            reused = self.manager.create_or_reuse()
            self.manager.claim_cleanup(self.run_id)
            if self.observer_mode and self.observer_attach and self.observer_attach != "none":
                self.manager.attach_observer(self.observer_attach)
            self.writer.write_session(command=command, reused=reused, workdir=self.workdir)
            self._registry_event("tmux_session_ready", "tmux session created or reused", "launching", "starting")
            self._spawn_monitors()
            if not reused:
                self.manager.start_claude(command)
            return self._monitor_loop(prompt_already_sent=reused)
        except Exception as exc:
            status = self._safe_artifact_status()
            return self._finish(
                "error",
                "environment_blocked",
                str(exc),
                status,
                5,
                extra=self._runtime_extra(
                    status,
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "exception_stage": "run",
                        "session_preserved": True,
                    },
                ),
            )

    def _monitor_loop(self, *, prompt_already_sent: bool) -> TmuxRunResult:
        try:
            return self._monitor_loop_impl(prompt_already_sent=prompt_already_sent)
        except Exception as exc:
            status = self._safe_artifact_status()
            extra = self._runtime_extra(
                status,
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "exception_stage": self._exception_stage,
                    "session_preserved": True,
                },
            )
            return self._finish("error", "environment_blocked", str(exc), status, 5, extra=extra)

    def _monitor_loop_impl(self, *, prompt_already_sent: bool) -> TmuxRunResult:
        prompt_sent = prompt_already_sent
        remote_mode_sent = False
        remote_mode_active = False
        runtime_state = "waiting_for_ready"
        hold_reason: str | None = None
        started_at = time.time()
        last_heartbeat_at = 0.0
        last_progress_at = 0.0
        last_output_time = time.time()

        while True:
            now = time.time()
            self._exception_stage = "has_session"
            if not self.manager.has_session():
                status = self._safe_artifact_status()
                return self._finish("session_lost", "environment_blocked", "tmux session disappeared", status, 5)

            pane_text, pane_state, last_output_time, runtime_state = self._capture_and_detect(
                runtime_state, last_output_time, now,
            )

            dialog, runtime_state, hold_reason = self._handle_dialogs(
                pane_text, runtime_state, hold_reason, remote_mode_active,
            )
            self.remote_mode_status["fallback_used"] = bool(
                self.remote_mode_status.get("fallback_used") or dialog.fallback_used
            )

            # Artifact detection + heartbeat.
            self._exception_stage = "artifact_detection"
            status = self._safe_artifact_status()
            expected_metadata = {"expected_outputs": self.artifacts.metadata(status)}
            if status.path_mismatch:
                hold_reason = "expected output path mismatch detected"
                runtime_state = "hold"
            last_heartbeat_at = self._log_state_change(
                pane_text, runtime_state, pane_state.changed, hold_reason, status,
                now, last_heartbeat_at,
            )

            if hold_reason:
                # If outputs already exist despite the hold, complete the task
                if status.all_present:
                    self.writer.write_task_state("artifact_detected", extra={"tmux_session_id": self.session_id})
                    self.writer.write_progress("artifact_detected", "expected outputs detected (recovered from hold)")
                    return self._finish(
                        "executor_completed",
                        "agent_completed",
                        "expected outputs detected (recovered from hold)",
                        status,
                        0,
                        extra=self._runtime_extra(status),
                    )
                return self._finish(
                    "hold",
                    "manual_confirmation_required",
                    hold_reason,
                    status,
                    5,
                    extra=self._runtime_extra(status, expected_metadata),
                )

            # Prompt state management (ready timeout, remote mode, prompt send).
            runtime_state, hold_reason, prompt_sent, remote_mode_sent, remote_mode_active, last_output_time = (
                self._handle_prompt_state(
                    runtime_state, hold_reason, remote_mode_sent, remote_mode_active,
                    prompt_sent, now, started_at, last_output_time,
                )
            )

            if status.all_present and runtime_state in {"waiting_for_input", "waiting_for_ready", "prompt_sent"}:
                self.writer.write_task_state("artifact_detected", extra={"tmux_session_id": self.session_id})
                self.writer.write_progress("artifact_detected", "expected outputs detected")
                return self._finish(
                    "executor_completed",
                    "agent_completed",
                    "expected outputs detected",
                    status,
                    0,
                    extra=self._runtime_extra(status),
                )

            # Artifact completion > pane runtime_state:
            # When expected outputs exist and are stable for N consecutive polls,
            # complete even if pane UI indicators keep state as "running".
            # Handles cases where Claude finishes but UI status text (e.g.
            # "esc to interrupt") keeps the parser in running state.
            if status.all_present and all(s.get("size_bytes", 0) > 0 for s in status.statuses):
                self._artifact_stable_consecutive_count += 1
            else:
                self._artifact_stable_consecutive_count = 0
            if self._artifact_stable_consecutive_count >= 2:
                self.writer.write_task_state("artifact_detected", extra={"tmux_session_id": self.session_id})
                self.writer.write_progress(
                    "artifact_detected", "expected outputs detected (stable across multiple polls)"
                )
                return self._finish(
                    "executor_completed",
                    "expected_outputs_stable",
                    "expected outputs detected (stable across multiple polls)",
                    status,
                    0,
                    extra=self._runtime_extra(status),
                )

            if runtime_state == "error":
                error_hold = self._record_error(pane_state, pane_text)
                if error_hold:
                    return self._finish(
                        "hold",
                        "agent_failed",
                        "error_retry_limit_exceeded",
                        status,
                        5,
                        extra=self._runtime_extra(status, error_hold),
                    )

            # Timeout checks.
            timeout_result, last_output_time = self._check_timeouts(
                status, started_at, last_output_time, now,
            )
            if timeout_result is not None:
                return timeout_result

            # Periodic progress.
            if now - last_progress_at >= self._progress_interval():
                self.writer.write_task_state(runtime_state, extra={"tmux_session_id": self.session_id})
                self.writer.write_progress(
                    runtime_state,
                    "tmux executor monitor loop active",
                    extra=self._runtime_extra(status),
                )
                last_progress_at = now

            time.sleep(self._poll_interval())

    # ── Extracted monitor-loop helpers ──────────────────────────────────

    def _capture_and_detect(
        self, runtime_state: str, last_output_time: float, now: float,
    ) -> tuple[str, PaneState, float, str]:
        """Capture pane text, parse state, update idle timer.

        Returns (pane_text, pane_state, last_output_time, runtime_state).
        """
        self._exception_stage = "capture"
        pane_text = self.manager.capture()
        self._exception_stage = "parse"
        pane_state = self.parser.parse(pane_text, default_state=runtime_state)
        if pane_state.changed:
            self.last_output_at = now_iso()
            last_output_time = now
        runtime_state = pane_state.runtime_state
        # When RUNNING_INDICATORS match, agent is still working even if
        # terminal output hasn't changed (e.g. long thinking/Harmonizing).
        # Reset idle timer so no_output_timeout only fires when truly idle.
        if runtime_state == "running":
            last_output_time = now
        # Agent recovered from one-off error (output changed, back to progress).
        # Reset error counts so retry budget isn't consumed by transient errors.
        if self.error_counts and pane_state.changed and runtime_state != "error":
            self.error_counts = {}
        return pane_text, pane_state, last_output_time, runtime_state

    def _handle_dialogs(
        self,
        pane_text: str,
        runtime_state: str,
        hold_reason: str | None,
        remote_mode_active: bool,
    ) -> tuple[DialogDecision, str, str | None]:
        """Run dialog classifier and apply state/hold mutations.

        Returns (dialog, runtime_state, hold_reason).
        """
        self._exception_stage = "dialog_handling"
        dialog = self.dialogs.handle(
            pane_text, self.manager,
            remote_mode_active=remote_mode_active,
            safety_context=self.safety_context,
        )
        if dialog.runtime_state:
            runtime_state = dialog.runtime_state
            if dialog.runtime_state == "hold":
                hold_reason = dialog.message
                # Permission Human Takeover Protocol:
                # When permission dialog triggers hold, write request file
                # and mark progress so Owner sees WAITING_OWNER_PERMISSION.
                if dialog.dialog_type == "PERMISSION_DIALOG":
                    self._write_permission_request(dialog)
                    self.writer.write_progress("WAITING_OWNER_PERMISSION", hold_reason or "permission required")
        return dialog, runtime_state, hold_reason

    def _handle_prompt_state(
        self,
        runtime_state: str,
        hold_reason: str | None,
        remote_mode_sent: bool,
        remote_mode_active: bool,
        prompt_sent: bool,
        now: float,
        started_at: float,
        last_output_time: float,
    ) -> tuple[str, str | None, bool, bool, bool, float]:
        """Manage prompt lifecycle: ready timeout warning, remote mode activation,
        prompt sending.

        Returns (runtime_state, hold_reason, prompt_sent, remote_mode_sent,
                 remote_mode_active, last_output_time).
        """
        # Ready timeout is a suspect signal, not a death sentence.
        # Claude may take longer than ready_timeout_sec to start (remote mode activation,
        # model loading, etc.). Instead of exiting, log a warning and keep waiting.
        # The no_output_timeout and max_wall_time checks below handle genuine stalls.
        if not prompt_sent and now - started_at > self._ready_timeout():
            if not self._ready_timeout_warned:
                self._ready_timeout_warned = True
                self.writer.write_progress(
                    "waiting_for_ready",
                    f"Claude not yet ready after ready_timeout_sec ({self._ready_timeout()}s), continuing to wait",
                )
            last_output_time = now  # prevent no_output_timeout from false-firing during launch

        if runtime_state in {"waiting_for_input", "waiting_for_ready"} and not remote_mode_sent and (now - started_at > 5.0):
            self._exception_stage = "clauderemote_activation"
            activation = self._activate_remote_mode()
            remote_mode_active = activation.get("active") is True
            remote_mode_sent = True
            self.writer.write_progress(
                "remote_mode_activated",
                "clauderemote mode activation attempted",
                extra={"clauderemote": self.remote_mode_status},
            )
            self._registry_event("remote_mode_activated", "clauderemote mode activation attempted", "starting", "waiting_after_remote")

        if runtime_state in {"waiting_for_input", "waiting_for_ready"} and not prompt_sent and remote_mode_sent and (now - started_at > 7.0):
            self._exception_stage = "send_prompt"
            self._send_prompt()
            prompt_sent = True
            runtime_state = "prompt_sent"
            self.writer.write_task_state("prompt_sent", extra={"tmux_session_id": self.session_id})
            self.writer.write_progress("prompt_sent", "prompt sent to Claude tmux session")
            self._registry_event("prompt_sent", "prompt sent to Claude tmux session", "starting", "prompt_sent")

        return runtime_state, hold_reason, prompt_sent, remote_mode_sent, remote_mode_active, last_output_time

    def _check_timeouts(
        self,
        status: ArtifactStatus,
        started_at: float,
        last_output_time: float,
        now: float,
    ) -> tuple[TmuxRunResult | None, float]:
        """Check no_output_timeout and max_wall_time. Returns (timeout_result, last_output_time).

        Observer mode (user watching Terminal window) overrides all timeouts.
        User said: "用户盯着，你怕什么超时" — artificial timeouts are unnecessary
        when the user can see the process and handle issues themselves.
        Timeouts only apply in headless (no-observer) runs.
        """
        if not self.observer_mode:
            if now - last_output_time > self._no_output_timeout():
                return self._finish(
                    "hold",
                    "no_output",
                    "no pane output change beyond no_output_timeout_sec",
                    status,
                    5,
                    extra=self._runtime_extra(status),
                ), last_output_time

            if now - started_at > self._max_wall_time():
                return self._finish(
                    "timeout",
                    "timeout_or_abort",
                    "tmux executor timeout",
                    status,
                    5,
                    extra=self._runtime_extra(status),
                ), last_output_time

        return None, last_output_time

    def _log_state_change(
        self,
        pane_text: str,
        runtime_state: str,
        output_changed: bool,
        hold_reason: str | None,
        status: ArtifactStatus,
        now: float,
        last_heartbeat_at: float,
    ) -> float:
        """Write pane capture and heartbeat when conditions are met. Returns updated last_heartbeat_at."""
        expected_metadata = {"expected_outputs": self.artifacts.metadata(status)}
        if output_changed or now - last_heartbeat_at >= self._heartbeat_interval():
            self._exception_stage = "heartbeat_write"
            self.writer.append_capture(pane_text, runtime_state)
            self.writer.write_heartbeat(
                runtime_state,
                last_output_at=self.last_output_at,
                output_changed=output_changed,
                artifact_status=status,
                hold_reason=hold_reason,
                extra=self._runtime_extra(status, expected_metadata),
            )
            last_heartbeat_at = now
        return last_heartbeat_at

    def _build_result(
        self,
        runtime_state: str,
        classification: str,
        reason: str,
        status: ArtifactStatus,
        exit_code: int,
        extra: dict[str, Any] | None = None,
    ) -> TmuxRunResult:
        """Construct TmuxRunResult from final state."""
        return self._finish(runtime_state, classification, reason, status, exit_code, extra=extra)

    # ── End of extracted monitor-loop helpers ───────────────────────────
    def _activate_remote_mode(self) -> dict[str, Any]:
        """Send /clauderemote on to switch Claude Code to letter-based dialog mode."""
        self.remote_mode_status.update(
            {
                "attempted": True,
                "active": "unverified",
                "fallback_strategy": "safe_whitelist_enter",
            }
        )
        try:
            self.manager.send_literal("/clauderemote on")
            # 不 Enter——由 dialog_watcher 检测到 prompt 下的输入后提交
            time.sleep(2.0)
            pane_text = self.manager.capture()
        except Exception as exc:
            self.remote_mode_status.update(
                {
                    "active": False,
                    "fallback_used": True,
                    "error": str(exc),
                }
            )
            return self.remote_mode_status
        lowered = pane_text.lower()
        if "unknown command" in lowered or "not found" in lowered:
            self.remote_mode_status.update({"active": False, "fallback_used": True})
        elif "clauderemote" in lowered and any(word in lowered for word in ["on", "enabled", "开启", "远程模式"]):
            self.remote_mode_status.update({"active": True})
        else:
            self.remote_mode_status.update(
                {
                    "active": "unverified",
                    "fallback_used": True,
                    "known_issue": "clauderemote activation could not be confirmed from pane text",
                }
            )
        return self.remote_mode_status

    def _send_prompt(self) -> None:
        prompt = self._load_prompt()
        # paste-buffer for reliable multi-line paste, then short delay
        # before Enter to let Claude's TUI absorb the pasted content.
        self.manager.paste_text(prompt)
        # 不 Enter——由 dialog_watcher 检测到对话框后提交
        time.sleep(2.0)

    def _validate_and_repair(self, artifact_status: ArtifactStatus) -> dict[str, Any] | None:
        """§10 output validation + Repair Agent via tmux paste.

        Returns repair result dict if validation ran, None if skipped.
        """
        if not artifact_status.all_present or not self.artifacts.expected_paths:
            return None
        task_dir = str(self.dirs["task_dir"])
        expected = [str(p) for p in self.artifacts.expected_paths]
        validation = validate_outputs(task_dir, expected)
        result: dict[str, Any] = {"validation_verdict": validation.verdict}
        if validation.all_pass:
            result["format_check"] = "pass"
            return result
        result["format_check"] = "fail"
        result["failures"] = validation.failures
        # Trigger Repair Agent with tmux paste callback
        repair = repair_node(
            self.config,
            expected,
            node_id=self.task_id,
            send_repair_prompt=self._tmux_repair_prompt_sender(),
            max_retries=2,
        )
        result["repair"] = repair
        return result

    def _tmux_repair_prompt_sender(self):
        """Return a callable that sends repair prompts via tmux paste."""
        def sender(prompt_text: str) -> bool:
            try:
                self.manager.paste_text(prompt_text)
                time.sleep(1.0)
                self.manager.send_enter()
                time.sleep(5.0)
                return True
            except Exception:
                return False
        return sender

    def _finish(
        self,
        runtime_state: str,
        classification: str,
        reason: str,
        artifact_status: ArtifactStatus,
        exit_code: int,
        extra: dict[str, Any] | None = None,
    ) -> TmuxRunResult:
        extra_payload = self._runtime_extra(artifact_status, extra)
        # §10 output validation + Repair Agent integration (workflow mode only)
        repair_result = self._validate_and_repair(artifact_status) if self.safety_context else None
        if repair_result:
            extra_payload["repair_agent"] = repair_result
        try:
            notification = self.notifier.notify(runtime_state)
            task_status = "completed" if runtime_state == "executor_completed" else "failed"
            self.writer.write_task_state(
                runtime_state,
                task_status=task_status,
                extra={
                    "classification": classification,
                    "tmux_session_id": self.session_id,
                    "hold_reason": reason if runtime_state == "hold" else None,
                    "session_preserved": runtime_state != "executor_completed",
                    **extra_payload,
                },
            )
            self.writer.write_heartbeat(
                runtime_state,
                last_output_at=self.last_output_at,
                output_changed=False,
                artifact_status=artifact_status,
                hold_reason=reason if runtime_state == "hold" else None,
                extra=extra_payload,
            )
            self.writer.write_progress(runtime_state, reason, extra=extra_payload)
            result_payload = self.writer.write_result(
                runtime_state=runtime_state,
                classification=classification,
                reason=reason,
                artifact_status=artifact_status,
                notification=notification,
                exit_code=exit_code,
                extra={
                    "session_preserved": runtime_state != "executor_completed",
                    **extra_payload,
                },
            )
            self._registry_event(
                "progress" if exit_code == 0 else "blocked",
                reason,
                "running",
                runtime_state,
            )
            if runtime_state == "executor_completed" and not self.options.get("preserve_session", False):
                self.manager.schedule_cleanup(self._retention_seconds(), self.run_id)
            return TmuxRunResult(
                exit_code=exit_code,
                classification=result_payload,
                stdout_path=self.writer.stdout_path,
                stderr_path=self.writer.stderr_path,
                raw_output_path=self.writer.raw_output_path,
            )
        except Exception as exc:
            return self._emergency_finish(runtime_state, exc, extra_payload)

    def _validate_start(self, command: list[str]) -> str | None:
        if not self.manager.tmux_available():
            return "tmux is not installed or not on PATH"
        if shutil.which("claude") is None:
            return "claude is not installed or not on PATH"
        if any("--dangerously-skip-permissions" in part for part in command):
            return "--dangerously-skip-permissions is not allowed in R1 tmux executor"
        configured_command = self.options.get("command")
        if isinstance(configured_command, str) and "--dangerously-skip-permissions" in configured_command:
            return "--dangerously-skip-permissions is not allowed in R1 tmux executor"
        if isinstance(configured_command, list) and any("--dangerously-skip-permissions" in str(part) for part in configured_command):
            return "--dangerously-skip-permissions is not allowed in R1 tmux executor"
        if not self.artifacts.expected_paths:
            return "executor_options.expected_outputs is required"
        artifact_error = self.artifacts.validate()
        if artifact_error:
            return artifact_error
        prompt_file = self.options.get("prompt_file")
        if prompt_file:
            prompt_path = self._task_relative_path(str(prompt_file))
            try:
                prompt_path.relative_to(self.dirs["task_dir"])
            except ValueError:
                return f"prompt_file outside task_dir: {prompt_path}"
            if not prompt_path.exists():
                return f"prompt_file not found: {prompt_path}"
        elif not self.options.get("prompt"):
            return "executor_options.prompt_file or executor_options.prompt is required"
        return None

    def _claude_command(self) -> list[str]:
        extra_args = self.options.get("extra_args")
        command = ["claude"]
        if isinstance(extra_args, list):
            command.extend(str(item) for item in extra_args)
        teammate_mode = str(self.options.get("teammate_mode") or "auto")
        if teammate_mode == "tmux":
            command.extend(["--teammate-mode", "tmux"])
        elif teammate_mode == "in-process":
            command.extend(["--teammate-mode", "in-process"])
        return command

    def _load_prompt(self) -> str:
        prompt_file = self.options.get("prompt_file")
        if prompt_file:
            prompt = self._task_relative_path(str(prompt_file)).read_text(encoding="utf-8")
        else:
            prompt = str(self.options.get("prompt") or "")
        suffix = self.artifacts.output_contract()
        if self.safety_context:
            suffix += "\n\n" + self.safety_context.security_prompt_suffix()
        return f"{prompt.rstrip()}\n\n{suffix}\n"

    def _expected_outputs(self) -> list[str]:
        expected = self.options.get("expected_outputs")
        if isinstance(expected, list):
            return [str(item) for item in expected]
        return []

    def _task_relative_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        return (self.dirs["task_dir"] / path).resolve()

    def _safe_artifact_status(self) -> ArtifactStatus:
        try:
            return self.artifacts.status()
        except Exception as exc:
            return ArtifactStatus(
                False,
                [],
                hold_reason=f"artifact detection failed: {type(exc).__name__}: {exc}",
            )

    def _runtime_extra(
        self,
        artifact_status: ArtifactStatus,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        dialog_decision = self.dialogs.last_decision
        if not dialog_decision.get("initialized"):
            dialog_decision = {
                **dialog_decision,
                "reason": "no dialog was processed this run",
            }
        payload: dict[str, Any] = {
            "clauderemote": self.remote_mode_status,
            "dialog_handling": dialog_decision,
            "expected_outputs": self.artifacts.metadata(artifact_status),
            "error_retry": {
                "error_counts": self.error_counts,
                "error_retry_limit": self._error_retry_limit(),
                "last_error_at": self.last_error_at,
            },
        }
        if extra:
            payload.update(extra)
        return payload

    def _write_permission_request(self, dialog: Any) -> None:
        """Write runtime/permission_request.json for Permission Human Takeover Protocol."""
        import json as _json
        from datetime import datetime, timezone, timedelta
        task_dir = Path(str(self.dirs.get("task_dir", "")))
        runtime_dir = task_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        cmd_str = " ".join(_shell_quote(part) for part in (self._launch_command or []))
        keyword = getattr(dialog, "matched_keyword", "") or ""
        risk = "high" if "dangerously" in keyword else "medium"
        tz = timezone(timedelta(hours=8))
        now_str = datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z")
        request = {
            "task_id": self.task_id,
            "dialog_type": getattr(dialog, "dialog_type", None),
            "dialog_action": getattr(dialog, "action", None),
            "matched_keyword": keyword,
            "raw_target": getattr(dialog, "raw_target", None),
            "hold_reason": getattr(dialog, "message", None),
            "command": cmd_str,
            "workdir": str(self.workdir),
            "risk_level": risk,
            "suggested_action": "inspect_first",
            "observer_hint": f"tmux attach-session -t {self.session_id}",
            "created_at": now_str,
        }
        request_path = runtime_dir / "permission_request.json"
        try:
            request_path.write_text(_json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _record_error(self, pane_state: PaneState, pane_text: str) -> dict[str, Any] | None:
        pattern = pane_state.error_pattern or pane_state.error_reason or "unknown_error_pattern"
        signature = f"{pattern}:{self._last_nonempty_line(pane_text)}"
        if signature != self._last_error_signature:
            self.error_counts[pattern] = self.error_counts.get(pattern, 0) + 1
            self.last_error_at = now_iso()
            self._last_error_signature = signature
        count = self.error_counts.get(pattern, 0)
        payload = {
            "runtime_state": "hold" if count >= self._error_retry_limit() else "error",
            "hold_reason": "error_retry_limit_exceeded" if count >= self._error_retry_limit() else None,
            "error_pattern": pattern,
            "error_count": count,
            "error_retry_limit": self._error_retry_limit(),
            "last_error_at": self.last_error_at,
        }
        self.writer.write_progress(
            "error_observed",
            pane_state.error_reason or "error indicator detected",
            extra={"error_retry": payload},
        )
        if count >= self._error_retry_limit():
            return payload
        return None

    def _emergency_finish(
        self,
        intended_runtime_state: str,
        exc: Exception,
        extra: dict[str, Any] | None = None,
    ) -> TmuxRunResult:
        try:
            payload = self.writer.write_emergency_result(
                error_type=type(exc).__name__,
                error_message=str(exc),
                intended_runtime_state=intended_runtime_state,
                exception_stage=self._exception_stage or "finish",
                extra=extra,
            )
        except Exception as fallback_exc:  # pragma: no cover - last ditch logging
            payload = {
                "runtime_state": "error",
                "intended_runtime_state": intended_runtime_state,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "fallback_result_writer_used": True,
                "fallback_error": str(fallback_exc),
            }
            print(f"tmux executor emergency result failed: {fallback_exc}", file=sys.stderr)
        return TmuxRunResult(
            exit_code=5,
            classification=payload,
            stdout_path=self.writer.stdout_path,
            stderr_path=self.writer.stderr_path,
            raw_output_path=self.writer.raw_output_path,
        )

    @staticmethod
    def _last_nonempty_line(text: str) -> str:
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    def _heartbeat_interval(self) -> int:
        return int(self.options.get("heartbeat_interval_sec") or self.control.get("heartbeat_interval_sec") or 30)

    def _progress_interval(self) -> int:
        return int(self.options.get("progress_check_interval_sec") or self.control.get("progress_check_interval_sec") or 120)

    def _max_wall_time(self) -> int:
        return int(self.options.get("emergency_max_wall_time_sec") or self.control.get("emergency_max_wall_time_sec") or 600)

    def _no_output_timeout(self) -> int:
        return int(self.options.get("no_output_timeout_sec") or 120)

    def _ready_timeout(self) -> int:
        return int(self.options.get("ready_timeout_sec") or 30)

    def _retention_seconds(self) -> int:
        return int(self.options.get("session_retention_seconds") or 900)

    def _poll_interval(self) -> float:
        return float(self.options.get("poll_interval_sec") or 2.0)

    def _error_retry_limit(self) -> int:
        return max(1, int(self.options.get("error_retry_limit") or 3))

    def _registry_event(self, event_type: str, reason: str, from_state: str, to_state: str) -> None:
        append_registry_event(
            self.dirs["task_dir"],
            task_id=self.task_id,
            event_type=event_type,
            reason=reason,
            from_runtime_state=from_state,
            to_runtime_state=to_state,
            evidence_paths=[str(self.writer.pane_capture_path), str(self.dirs["runtime_dir"] / "session.yaml")],
            session_id=self.config.get("session_id") or "session-local",
            round_id=self.config.get("round_id") or "round-1",
        )


def _shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


# ── Self-registration ──────────────────────────────────────────────

from WorkflowBase.runner.executor_registry import register_executor  # noqa: E402

register_executor("claude", ClaudeTmuxExecutor)
