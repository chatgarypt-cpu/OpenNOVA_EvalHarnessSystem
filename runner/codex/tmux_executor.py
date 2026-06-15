# -*- coding: utf-8 -*-
"""Codex Tmux Executor for Relay Runtime R0.

Completely independent of ClaudeTmuxExecutor — does NOT import anything
from ``tmux_executor.py``.

Design (OSS-informed):
  - Session management pattern from codex-orchestrator (kingbootoshi):
    ``tmux new-session -d -s <name> -c <cwd>``
  - Auto-approval pattern from codex-yolo: 0.5s poll, 7 prompt styles,
    primary + secondary signal matching.
  - Prompt injection via ``tmux send-keys`` + Enter (codex-orchestrator style).
  - Structured blocker classification specific to Codex interaction model.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .execution_context import CodexExecutionContext
from ..relay_runner import (
    RunResult,
    append_registry_event,
    classify_result,
    ensure_task_dirs,
    now_iso,
    write_abort_report,
    write_blocker_report,
    write_heartbeat,
    write_legacy_result,
    write_owner_decision_record_template,
    write_owner_decision_request,
    write_progress,
    write_task_state,
)

# ── Terminal states ──────────────────────────────────────────────────
TERMINAL_STATES = {"executor_completed", "executor_failed", "hold", "timeout", "error", "session_lost"}
SESSION_PREFIX = "codex-tmux-"

# ── Codex blocker keywords ───────────────────────────────────────────
CODEX_AUTH_ERRORS = {"401", "token_expired", "refresh_token", "unauthorized"}
CODEX_SANDBOX_KEYWORDS = {"sandbox denied", "permission denied", "cannot access"}
CODEX_HOLD_KEYWORDS = {"approval needed", "blocker", "NEEDS_CLARIFICATION", "NO_GO"}
CODEX_COMPLETION_MARKERS = {"[codex-agent: Session complete", "task complete"}


# ── Self-contained SoundNotifier ─────────────────────────────────────
class CodexSoundNotifier:
    """Sound notification using shared tools/sound_utils interface.

    Resolves sound via tools_config.yaml profile system — respects
    public/private scene switching.  Falls back to direct afplay if
    the shared infrastructure is unavailable.
    """

    def __init__(self, options: dict[str, Any] | None = None,
                 project_root: str = ""):
        self.options = options or {}
        self.project_root = project_root

    def notify(self, runtime_state: str) -> dict[str, Any]:
        if runtime_state in TERMINAL_STATES:
            self._play()
        return {"notified": runtime_state in TERMINAL_STATES}

    def _play(self) -> None:
        try:
            # Use shared sound infrastructure with profile-based routing.
            from tools.sound_utils import get_sound, play_sound  # noqa: F811
            path = get_sound("heartbeat_sound", self.project_root)
            play_sound(path)
        except Exception:
            # Fallback: direct afplay (no dependency on project structure)
            sound = self.options.get("completion_sound", "Glass")
            if sound == "Glass":
                try:
                    subprocess.run(
                        ["afplay", "/System/Library/Sounds/Glass.aiff"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                except Exception:
                    pass
                return
            if sound == "say":
                try:
                    subprocess.run(
                        ["say", "Codex task complete"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except Exception:
                    pass


# ── Self-contained TmuxSessionManager ────────────────────────────────
class CodexTmuxSessionManager:
    """Owns tmux session lifecycle for Codex (independent of Claude's manager)."""

    def __init__(self, session_id: str, workdir: Path):
        self.session_id = session_id
        self.workdir = workdir

    @staticmethod
    def sanitize(raw: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw.strip())
        cleaned = cleaned.strip("_-")
        return cleaned[:80] or "codex_task"

    def tmux_available(self) -> bool:
        return shutil.which("tmux") is not None

    def has_session(self) -> bool:
        r = subprocess.run(
            ["tmux", "has-session", "-t", self.session_id],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        return r.returncode == 0

    def create_or_reuse(self) -> bool:
        if self.has_session():
            return True
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", self.session_id, "-c", str(self.workdir)],
            check=True,
        )
        return False

    def send_literal(self, text: str) -> None:
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session_id, "-l", text],
            check=True,
        )

    def send_enter(self) -> None:
        subprocess.run(
            ["tmux", "send-keys", "-t", self.session_id, "Enter"],
            check=True,
        )

    def paste_text(self, text: str) -> None:
        """Load text into tmux buffer and paste into session."""
        buf = f"{self.session_id}_prompt"
        subprocess.run(
            ["tmux", "load-buffer", "-b", buf, "-"],
            input=text, text=True, check=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-d", "-b", buf, "-t", self.session_id],
            check=True,
        )

    def send_keys(self, text: str) -> None:
        """Send literal text + Enter (convenience for single-line commands)."""
        self.send_literal(str(text))
        self.send_enter()

    def capture(self, lines: int = 2000) -> str:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session_id, "-p", "-S", f"-{lines}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "tmux capture-pane failed")
        return r.stdout

    def capture_all(self) -> str:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", self.session_id, "-p", "-S", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "tmux capture-pane failed")
        return r.stdout

    def attach_observer(self, attach_mode: str) -> None:
        if attach_mode == "terminal_window":
            subprocess.run(
                ["osascript", "-e",
                 f'tell application "Terminal" to do script "tmux attach-session -t {self.session_id}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                check=False,
            )

    def schedule_cleanup(self, retention_seconds: int) -> None:
        if retention_seconds <= 0:
            subprocess.run(["tmux", "kill-session", "-t", self.session_id], check=False)
            return
        # Background cleanup after delay
        subprocess.Popen(
            ["bash", "-c",
             f"sleep {retention_seconds}; tmux kill-session -t {self.session_id} 2>/dev/null"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def kill_session(self) -> None:
        subprocess.run(["tmux", "kill-session", "-t", self.session_id], check=False)


# ── Blockers ─────────────────────────────────────────────────────────
@dataclass
class CodexBlockerInfo:
    blocker_type: str | None = None   # permission_blocked | sandbox_denied | auth_failure | timeout | unknown
    detail: str = ""
    matched_keyword: str | None = None


def _classify_codex_stdout(stdout_text: str) -> CodexBlockerInfo:
    """Classify Codex stdout for known blocker patterns.
    
    Auth errors are handled separately via stderr (line 280).
    This function only checks sandbox/permission and hold blockers.
    """
    lower = stdout_text.lower()

    # Sandbox/permission blockers
    for kw in CODEX_SANDBOX_KEYWORDS:
        if kw in lower:
            return CodexBlockerInfo("permission_blocked", f"Codex sandbox/permission blocked: {kw}", kw)

    # Codex hold keywords
    for kw in CODEX_HOLD_KEYWORDS:
        if kw in lower:
            return CodexBlockerInfo("codex_hold", f"Codex reported blocker: {kw}", kw)

    return CodexBlockerInfo()


# ── Output classifier ───────────────────────────────────────────────
_CODEX_CLASSIFICATION_MAP: dict[str, str] = {}


def _classify_codex_exit(
    exit_code: int,
    stdout_path: Path | str,
    stderr_path: Path | str,
    *,
    expected_outputs: list[Path],
    timed_out: bool = False,
    stdout_text: str = "",
) -> dict[str, Any]:
    """Classify a Codex tmux execution result.

    Returns a classification dict (same shape as relay_runner.classify_result).
    """
    result: dict[str, Any] = {
        "classification": "agent_completed",
        "confidence": "high",
        "classified_by": "codex_tmux_executor",
    }

    # Timeout
    if timed_out:
        result["classification"] = "timeout_or_abort"
        result["reason"] = "Codex tmux execution timed out"
        return result

    # Session lost
    if exit_code == 5:  # session_lost
        result["classification"] = "environment_blocked"
        result["reason"] = "tmux session was lost"
        return result

    # Auth failure
    stderr_lower = ""
    try:
        stderr_lower = Path(str(stderr_path)).read_text(encoding="utf-8", errors="replace").lower()
    except Exception:
        pass

    if any(p in stderr_lower for p in CODEX_AUTH_ERRORS):
        result["classification"] = "environment_blocked"
        result["reason"] = "Codex auth token expired or unauthorized"
        result["requires_independent_review"] = True
        return result

    # Codex blocker from stdout
    blocker = _classify_codex_stdout(stdout_text)
    if blocker.blocker_type:
        result["classification"] = blocker.blocker_type
        result["reason"] = blocker.detail
        return result

    # Expected outputs missing
    present = [p for p in expected_outputs if p.exists()]
    missing = [p for p in expected_outputs if not p.exists()]

    if missing and not present:
        result["classification"] = "missing_artifact"
        result["reason"] = f"Expected outputs missing: {missing}"
        result["missing"] = [str(p) for p in missing]
        return result

    if missing and present:
        result["classification"] = "agent_completed_with_missing"
        result["reason"] = f"Some outputs missing: {missing}"
        result["missing"] = [str(p) for p in missing]
        return result

    # Agent completed
    if exit_code == 0 and present:
        result["classification"] = "agent_completed"
        result["reason"] = "Codex completed and expected outputs present"
        return result

    if exit_code != 0:
        result["classification"] = "agent_failed"
        result["reason"] = f"Codex exited with code {exit_code}"
        return result

    return result


# ── Codex receipt writer ─────────────────────────────────────────────
def _write_codex_receipt(
    task_dir: Path,
    ctx: CodexExecutionContext,
    classification: dict[str, Any],
    stdout_text: str,
    *,
    created_files: list[str] | None = None,
    modified_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    forbidden_files_touched: list[str] | None = None,
    plan_output_excerpt: str | None = None,
) -> Path:
    """Write a structured codex_receipt.yaml to canonical and compat paths."""
    receipt_dir = task_dir / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "codex_receipt.yaml"
    codex_dir = task_dir / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    codex_receipt_path = codex_dir / "codex_receipt.yaml"

    receipt = {
        "executor": "codex-tmux",
        "execution_mode": "tmux_interactive",
        "task_id": ctx.task_id,
        "attempt_id": ctx.attempt_id,
        "plan_mode": ctx.plan_mode,
        "plan_approved_by": "owner" if ctx.approval_mode == "owner_gate" else ctx.approval_mode,
        "plan_output_excerpt": (plan_output_excerpt[:500] + "...") if plan_output_excerpt and len(plan_output_excerpt) > 500 else (plan_output_excerpt or ""),
        "allowed_files": list(ctx.allowed_roots),
        "forbidden_files": list(ctx.forbidden_roots),
        "created_files": created_files or [],
        "modified_files": modified_files or [],
        "commands_run": commands_run or [],
        "self_check": {
            "status": "not_run",
            "details": "",
            "commands_run": [],
        },
        "test_results": [],
        "forbidden_files_touched": forbidden_files_touched or [],
        "known_issues": [],
        "blockers": [classification.get("reason", "")] if classification.get("classification") != "agent_completed" else [],
        "classification": classification.get("classification"),
        "next_recommendation": classification.get("reason", ""),
        "closeout_claimed": False,
    }

    try:
        import yaml
        content = yaml.dump(receipt, default_flow_style=False, allow_unicode=True)
    except ImportError:
        content = json.dumps(receipt, ensure_ascii=False, indent=2)

    receipt_path.write_text(content, encoding="utf-8")
    codex_receipt_path.write_text(content, encoding="utf-8")

    return receipt_path


def _write_codex_handoff(
    task_dir: Path,
    ctx: CodexExecutionContext,
    classification: dict[str, Any],
    *,
    created_files: list[str] | None = None,
    modified_files: list[str] | None = None,
    known_issues: list[str] | None = None,
    elapsed_seconds: float | None = None,
) -> Path:
    """Write Codex handoff markdown under the canonical codex artifact dir."""
    codex_dir = task_dir / "codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = codex_dir / "codex_handoff.md"

    created = created_files or []
    modified = modified_files or []
    issues = known_issues or []
    next_step = classification.get("reason") or "Review Codex outputs and run downstream validation."
    elapsed = 0.0 if elapsed_seconds is None else max(0.0, elapsed_seconds)

    def _list_block(items: list[str], empty: str) -> str:
        if not items:
            return f"- {empty}"
        return "\n".join(f"- {item}" for item in items)

    content = f"""# Codex Handoff

## Task
- task_id: {ctx.task_id}
- attempt_id: {ctx.attempt_id}

## Diff Summary
{_list_block(modified, "No modified files detected by executor.")}

## Created Files
{_list_block(created, "No created files detected by executor.")}

## Known Issues
{_list_block(issues, "None recorded.")}

## Next Step
- {next_step}

## Execution Time
- {elapsed:.2f} seconds
"""
    handoff_path.write_text(content, encoding="utf-8")
    return handoff_path


def _write_result_yaml(runtime_dir: Path) -> Path | None:
    """Mirror runtime/result.json to runtime/result.yaml when available."""
    result_json_path = runtime_dir / "result.json"
    if not result_json_path.exists():
        return None

    payload = json.loads(result_json_path.read_text(encoding="utf-8"))
    result_yaml_path = runtime_dir / "result.yaml"
    try:
        import yaml
        content = yaml.dump(payload, default_flow_style=False, allow_unicode=True)
    except ImportError:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    result_yaml_path.write_text(content, encoding="utf-8")
    return result_yaml_path


def _detect_git_changed_files(workdir: Path) -> tuple[list[str], list[str]]:
    """Return created and modified files from git porcelain status, best-effort."""
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), "status", "--porcelain", "--untracked-files=all"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return [], []
    if result.returncode != 0:
        return [], []

    created: list[str] = []
    modified: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if status == "??" or "A" in status:
            created.append(path)
        elif any(marker in status for marker in ("M", "D", "R", "C", "U")):
            modified.append(path)

    return sorted(set(created)), sorted(set(modified))


# ── Expected output detection ────────────────────────────────────────
def _detect_expected_outputs(expected_paths: list[Path]) -> dict[str, Any]:
    """Check expected output files. Returns status dict."""
    statuses = []
    all_present = True
    for path in expected_paths:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        statuses.append({
            "path": str(path),
            "exists": exists,
            "size_bytes": size,
        })
        if not exists:
            all_present = False
    return {"all_present": all_present, "statuses": statuses}


# ── Main executor ────────────────────────────────────────────────────
class CodexTmuxExecutor:
    """Run Codex in a tmux session with optional /plan → /execute two-phase flow.

    Does NOT share any code with ClaudeTmuxExecutor — fully independent.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.dirs = ensure_task_dirs(config)
        self.ctx = CodexExecutionContext.from_config(config)
        raw_opts = config.get("executor_options")
        self.options = dict(raw_opts) if isinstance(raw_opts, dict) else {}
        raw_ctrl = config.get("runtime_control")
        self.control = dict(raw_ctrl) if isinstance(raw_ctrl, dict) else {}
        if not self.ctx.task_id:
            self.ctx.task_id = str(config.get("task_id", f"codex-{uuid.uuid4().hex[:8]}"))
        if not self.ctx.attempt_id:
            self.ctx.attempt_id = f"{self.ctx.task_id}-a1"
        if not self.ctx.receipt_path:
            self.ctx.receipt_path = str(self.dirs.get("task_dir", Path(".")) / "receipts" / "codex_receipt.yaml")
        if not self.ctx.result_path:
            self.ctx.result_path = str(self.dirs.get("runtime_dir", Path(".")) / "result.json")

        # Own tmux manager (self-contained, not from tmux_executor.py)
        session_id = CodexTmuxSessionManager.sanitize(f"{SESSION_PREFIX}{self.ctx.task_id}")
        workdir = Path(self.ctx.resolved_cwd).resolve()
        self.manager = CodexTmuxSessionManager(session_id, workdir)
        self.workdir = workdir
        self.session_id = session_id
        self.notifier = CodexSoundNotifier(self.options, project_root=str(self.workdir))
        self.task_dir: Path = self.dirs.get("task_dir", Path("."))
        self.runtime_dir: Path = self.dirs.get("runtime_dir", self.task_dir / "runtime")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        # Expected outputs
        self.expected_outputs: list[Path] = []
        for p in self.ctx.expected_outputs:
            path = Path(p)
            if not path.is_absolute():
                path = self.task_dir / path
            self.expected_outputs.append(path)

        # Monitor state
        self._exception_stage: str = ""
        self._artifact_stable_count: int = 0
        self._max_wall_time: int = int(self.control.get("emergency_max_wall_time_sec", 600))
        self._poll_interval: float = float(self.options.get("poll_interval", 0.5))
        self._max_idle_sec: int = int(self.control.get("no_output_timeout_sec", 120))
        self._started_at: float = 0.0

    # ── Public protocol ──────────────────────────────────────────────

    def run(self) -> RunResult:
        """Execute the Codex task and return result."""
        task_dir = str(self.task_dir)
        stdout_path = self.dirs["logs_dir"] / "pane_capture.log"
        stderr_path = self.dirs["logs_dir"] / "stderr.log"
        raw_output_path = self.dirs["logs_dir"] / "raw_output.jsonl"

        write_progress(self.config, "launching", f"Codex tmux executor: {self.ctx.task_id}")
        write_task_state(self.config, "launching")

        try:
            classification = self._execute()

            # Write all standard outputs
            self._write_results(classification, stdout_path, stderr_path, raw_output_path)

            exit_code = 0 if classification.get("classification") in (
                "agent_completed", "agent_completed_with_missing"
            ) else 5

            return RunResult(
                exit_code=exit_code,
                classification=classification,
                stdout_path=Path(str(stdout_path)),
                stderr_path=Path(str(stderr_path)),
                raw_output_path=Path(str(raw_output_path)),
            )
        except Exception as exc:
            cls = {"classification": "agent_failed", "reason": str(exc), "classified_by": "codex_tmux_executor"}
            write_progress(self.config, "error", str(exc))
            write_task_state(self.config, "error")
            self._write_results(cls, stdout_path, stderr_path, raw_output_path)
            return RunResult(
                exit_code=5, classification=cls,
                stdout_path=Path(str(stdout_path)), stderr_path=Path(str(stderr_path)),
                raw_output_path=Path(str(raw_output_path)),
            )

    # ── Execution ────────────────────────────────────────────────────

    def _execute(self) -> dict[str, Any]:
        """Core execution: launch → prompt → capture."""
        write_progress(self.config, "launching", "Creating tmux session")
        write_task_state(self.config, "launching")
        self._started_at = time.time()

        # 1. Create tmux session
        reused = self.manager.create_or_reuse()
        if self.options.get("observer_mode", True):
            attach = self.options.get("observer_attach", "terminal_window")
            if attach and attach != "none":
                self.manager.attach_observer(attach)
        self.wrote_session = False  # lazy on first heartbeat

        # 2. Launch Codex interactive mode
        codex_cmd = self._build_codex_launch_command()
        write_progress(self.config, "launching", f"Starting Codex: {codex_cmd}")
        self.manager.send_literal(codex_cmd)
        self.manager.send_enter()

        # 3. Wait for Codex to be ready
        self._wait_for_codex_ready()

        # 4. Inject the task prompt
        prompt_text = self._resolve_prompt()
        if prompt_text:
            write_progress(self.config, "prompt_sent", f"Injecting prompt ({len(prompt_text)} chars)")
            write_task_state(self.config, "prompt_sent")
            self._inject_plan(prompt_text)

        # 5. Monitor execution (includes heartbeat loop)
        write_progress(self.config, "running", "Codex task running, monitoring...")
        write_task_state(self.config, "running")
        # Write session info (task_state is the canonical tracking artifact)
        runtime_dir = self.dirs.get("runtime_dir", self.task_dir / "runtime")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        pane_text, timed_out = self._monitor_execution()

        # 6. Save pane capture
        stdout_path = self.dirs["logs_dir"] / "pane_capture.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(pane_text, encoding="utf-8")
        stderr_path = self.dirs["logs_dir"] / "stderr.log"
        stderr_path.touch()

        # Verify expected outputs
        present = [str(p) for p in self.expected_outputs if p.exists()]
        missing = [str(p) for p in self.expected_outputs if not p.exists()]

        # 7. Classify
        classification = _classify_codex_exit(
            0 if not timed_out else 5,
            stdout_path, stderr_path,
            expected_outputs=self.expected_outputs,
            stdout_text=pane_text,
            timed_out=bool(timed_out),
        )
        classification["plan_mode"] = self.ctx.plan_mode
        classification["expected_outputs_present"] = present
        classification["expected_outputs_missing"] = missing
        return classification

    # ── Helper: detect system proxy ─────────────────────────────────

    @staticmethod
    def _detect_system_proxy() -> tuple:
        """Detect system HTTP proxy from macOS system settings.

        Returns (host, port) tuple, or (None, None) if no proxy configured.
        Uses ``scutil --proxy`` to read system proxy settings.
        """
        try:
            r = subprocess.run(
                ["scutil", "--proxy"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("HTTPProxy :"):
                    host = line.split(":")[-1].strip()
                elif line.startswith("HTTPPort :"):
                    port = line.split(":")[-1].strip()
            if host and port:
                return (host, int(port))
        except Exception:
            pass
        return (None, None)

    # ── Helper: build launch command ─────────────────────────────────

    def _build_codex_launch_command(self) -> str:
        """Build the Codex interactive launch command.

        Uses codex with optional sandbox/approval flags.
        Based on codex-orchestrator's buildCodexArgs pattern.
        """
        # Proxy env vars are dynamically detected from system proxy settings
        # so codex can reach chatgpt.com through the local proxy
        # (known requirement for Adarian environment behind GFW).
        proxy_host, proxy_port = self._detect_system_proxy()
        parts = []
        if proxy_host and proxy_port:
            parts.append(f"export HTTPS_PROXY=http://{proxy_host}:{proxy_port}")
            parts.append(f"HTTP_PROXY=http://{proxy_host}:{proxy_port}")
            parts.append("&&")
        parts.append("codex")

        # Model — only pin if explicitly configured in executor_options.
        # Otherwise let Codex use its own default (which matches the
        # logged-in account type).
        model = self.options.get("codex_model") or self.options.get("model")
        if model:
            parts.append(f"-c model={model}")

        # Sandbox — when bypass is used, --ask-for-approval is implied
        # and must NOT be passed (Codex rejects the combination).
        bypass_sandbox = not self.ctx.sandbox_mode
        if bypass_sandbox:
            parts.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            # Approval policy (only when sandbox is active)
            approval = self.options.get("codex_approval", "never")
            parts.append(f"--ask-for-approval {approval}")

        # Working directory via -C (absolute path, shell-quoted)
        workdir_str = shlex.quote(str(self.workdir))
        parts.append(f"-C {workdir_str}")

        # --no-alt-screen for capture-pane compatibility
        parts.append("--no-alt-screen")
        parts.append("--ephemeral")

        return " ".join(parts)

    # ── Helper: prompt resolution ────────────────────────────────────

    def _resolve_prompt(self) -> str:
        """Resolve prompt text from config."""
        if self.options.get("prompt"):
            return str(self.options["prompt"])
        fallback = self.task_dir / "dispatch" / "prompt.md"
        if fallback.exists():
            return fallback.read_text(encoding="utf-8")
        if self.options.get("prompt_file"):
            p = Path(str(self.options["prompt_file"]))
            if not p.is_absolute():
                p = self.task_dir / p
            if p.exists():
                return p.read_text(encoding="utf-8")
        return ""

    # ── Helper: wait for Codex ready ─────────────────────────────────

    def _wait_for_codex_ready(self, timeout: int = 30) -> bool:
        """Wait until Codex shows its prompt indicator (>)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self.manager.capture(50)
                if "›" in text or "Implement" in text:
                    time.sleep(1)  # brief settle
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        # Even if not ready, proceed (best-effort)
        return False

    # ── Plan injection ───────────────────────────────────────────────

    def _inject_plan(self, prompt_text: str) -> str:
        """Send the task prompt to Codex.

        Note: Codex's ``/plan`` slash command is disabled while a task is
        running (observed error: ``/plan is disabled while a task is in
        progress``).  Instead, we paste the prompt text directly — Codex
        treats any text at the prompt as a task description and will
        automatically plan and execute.

        Based on codex-orchestrator pattern: prompt is passed at launch
        or pasted at the initial prompt as plain text, not as a slash
        command.
        """
        self.manager.paste_text(str(prompt_text))
        time.sleep(0.3)
        self.manager.send_enter()
        return prompt_text

    # ── Execute injection ────────────────────────────────────────────

    def _inject_execute(self) -> None:
        """Send /execute to Codex."""
        self.manager.send_keys("/execute")

    # ── Wait for completion ──────────────────────────────────────────

    def _wait_for_completion(self, timeout_sec: int = 120, phase: str = "execute") -> str:
        """Wait for phase to complete and return captured pane text."""
        deadline = time.time() + timeout_sec
        output = ""
        while time.time() < deadline:
            try:
                if not self.manager.has_session():
                    return output
                output = self.manager.capture(200)
                # Check for completion indicators
                if "›" in output and "Implement" not in output:
                    # Back at prompt, likely done
                    if any(f in output for f in [str(p.name) for p in self.expected_outputs]):
                        time.sleep(2)
                        return self.manager.capture(500)
            except Exception:
                pass
            time.sleep(self._poll_interval)
        return output

    # ── Monitor execution ────────────────────────────────────────────

    def _monitor_execution(self) -> tuple[str, bool]:
        """Poll tmux pane for output. Returns (final_pane_text, timed_out).

        Detection mechanisms:
          1. Session alive — session gone = return what we have
          2. Completion markers in pane text
             (codex-orchestrator pattern: [codex-agent: Session complete,
              Token usage: total=...])
          3. Expected output files detected on disk
          4. Prompt indicator (›) + expected outputs present = done

        No idle timeout or wall clock timeout here.
        The downstream heartbeat_monitor.py detects heartbeat.json
        staleness and handles sound notification on completion.
        """
        _TOKEN_USAGE_RE = re.compile(
            r"Token usage:\s*total=\d+,\s*input=\d+"
        )

        last_heartbeat = 0.0
        heartbeat_seq = 0
        outputs_logged = False

        while True:
            now = time.time()

            # Check session alive — only legitimate reason to stop
            if not self.manager.has_session():
                return (self.manager.capture(500) or "", False)

            # Capture
            try:
                pane_text = self.manager.capture(500)
            except Exception:
                time.sleep(self._poll_interval)
                continue

            # ── Completion markers (pane text) ──────────────────────
            session_complete = (
                "[codex-agent: Session complete" in pane_text
                or bool(_TOKEN_USAGE_RE.search(pane_text))
            )
            if session_complete:
                time.sleep(2)
                pane_text = self.manager.capture(500)
                status = _detect_expected_outputs(self.expected_outputs)
                if status["all_present"]:
                    write_progress(self.config, "artifact_detected",
                                   "Completion marker + expected outputs verified")
                    return (self.manager.capture_all(), False)
                write_progress(self.config, "idle",
                               "Codex session complete, expected outputs missing")
                return (self.manager.capture_all(), False)

            # ── Expected output files ────────────────────────────────
            status = _detect_expected_outputs(self.expected_outputs)
            if status["all_present"]:
                self._artifact_stable_count += 1
                if self._artifact_stable_count >= 3 and not outputs_logged:
                    write_progress(self.config, "artifact_detected",
                                   "expected outputs detected")
                    outputs_logged = True
                    time.sleep(5)
                    return (self.manager.capture_all(), False)
                if self._artifact_stable_count >= 5:
                    return (self.manager.capture_all(), False)
            else:
                self._artifact_stable_count = 0

            # ── Prompt indicator (fallback) ─────────────────────────
            if "› " in (pane_text.split("\n")[-1] if pane_text.strip() else ""):
                if status["all_present"]:
                    time.sleep(3)
                    return (self.manager.capture_all(), False)

            # ── Heartbeat (downstream heartbeat_monitor handles staleness) ─
            if now - last_heartbeat >= 30:
                heartbeat_seq += 1
                write_heartbeat(self.config, "running", executor_pid=0,
                                heartbeat_seq=heartbeat_seq)
                last_heartbeat = now

            time.sleep(self._poll_interval)

    # ── Owner decision request ───────────────────────────────────────

    def _write_owner_decision_request(self, request_type: str, reason: str) -> Path:
        """Write a structured owner decision request."""
        req_dir = self.task_dir / "runtime"
        req_dir.mkdir(parents=True, exist_ok=True)
        path = req_dir / "owner_decision_request.yaml"
        content = {
            "request_id": f"{self.ctx.task_id}_{request_type}_{int(time.time())}",
            "request_type": request_type,
            "reason": reason,
            "task_id": self.ctx.task_id,
            "timestamp": now_iso(),
            "session_id": self.session_id,
        }
        try:
            import yaml
            path.write_text(yaml.dump(content, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        except ImportError:
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # ── Results ──────────────────────────────────────────────────────

    def _write_results(
        self,
        classification: dict[str, Any],
        stdout_path: Path,
        stderr_path: Path,
        raw_output_path: Path,
    ) -> None:
        """Write all standard runtime result files."""
        is_completed = classification.get("classification") in (
            "agent_completed", "agent_completed_with_missing"
        )
        is_hold = classification.get("classification") == "hold"
        final_state = "executor_completed" if is_completed else ("hold" if is_hold else "executor_failed")
        task_status = "completed" if is_completed else ("failed" if not is_hold else "hold")
        reason = classification.get("reason") or f"classified as {classification.get('classification')}"

        write_task_state(self.config, final_state, task_status=task_status, extra={
            "classification": classification.get("classification"),
            "executor": "codex-tmux",
            "plan_mode": self.ctx.plan_mode,
        })
        write_progress(self.config, final_state, reason)
        write_heartbeat(self.config, final_state, heartbeat_seq=0)

        # Sound notification on completion
        self.notifier.notify(final_state)

        if not is_completed and not is_hold:
            stderr_text = ""
            try:
                stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            write_blocker_report(self.config, reason, stderr_text[-1000:])
            write_owner_decision_request(
                self.config, "recovery_requires_approval", "request_owner_decision", reason,
            )
            write_owner_decision_record_template(self.config)

        created_files = classification.get("created_files") or []
        modified_files = classification.get("modified_files") or []
        if not created_files and not modified_files:
            created_files, modified_files = _detect_git_changed_files(self.workdir)
        known_issues = classification.get("known_issues") or []
        if not is_completed and reason:
            known_issues = [*known_issues, reason]
        elapsed_seconds = (time.time() - self._started_at) if self._started_at else 0.0

        # Write codex artifacts
        _write_codex_receipt(
            self.task_dir, self.ctx, classification,
            stdout_text=(stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""),
            created_files=list(created_files),
            modified_files=list(modified_files),
        )
        _write_codex_handoff(
            self.task_dir, self.ctx, classification,
            created_files=list(created_files),
            modified_files=list(modified_files),
            known_issues=list(known_issues),
            elapsed_seconds=elapsed_seconds,
        )

        write_legacy_result(
            self.config,
            runtime_state=final_state,
            returncode=0 if is_completed else 5,
            classification=classification,
            evidence_paths=[str(stdout_path), str(stderr_path), str(raw_output_path)],
        )
        _write_result_yaml(self.runtime_dir)

        append_registry_event(
            self.task_dir,
            task_id=self.ctx.task_id,
            event_type="progress" if is_completed else "blocked",
            reason=reason,
            from_runtime_state="running",
            to_runtime_state=final_state,
            evidence_paths=[str(p) for p in [stdout_path, stderr_path, raw_output_path] if p.exists()],
            session_id=self.session_id,
            round_id=str(self.config.get("round_id", "round-1")),
        )


# ── Self-registration ──────────────────────────────────────────────
from ..executor_registry import register_executor  # noqa: E402

register_executor("codex-tmux", CodexTmuxExecutor)
