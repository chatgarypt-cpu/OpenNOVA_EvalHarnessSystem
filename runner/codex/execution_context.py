"""Codex execution context for CodexTmuxExecutor R0.

Defines the execution boundary, file scope, and task metadata
for a Codex tmux dispatch.  Analogous to Claude's ExecutionContext
but Codex-specific — no bash whitelist, no forbidden_patterns,
only path scope and task info.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CodexExecutionContext:
    """Pre-authorized execution context for a Codex tmux task.

    Fields
    ------
    task_id:
        Unique identifier for this execution instance.
    attempt_id:
        Unique identifier for this Codex attempt, defaults to ``task_id-a1``.
    task_dir:
        Absolute path to the task working directory.
    cwd:
        Working directory for the Codex process (defaults to task_dir).
    expected_outputs:
        List of relative or absolute paths that constitute task completion.
    allowed_roots:
        Directories Codex is permitted to write to.
        Path glob patterns are supported (e.g. ``~/项目开发/**``).
    forbidden_roots:
        Directories Codex is NOT permitted to write to.
        Path glob patterns are supported.
    sandbox_mode:
        If True, Codex's native sandbox is trusted.
        If False, ``--dangerously-bypass-approvals-and-sandbox`` is NOT passed
        and Codex manages its own approvals.
    approval_mode:
        ``owner_gate`` (default) — Hermes waits for Owner to approve the plan
        before sending ``/execute``.
        ``auto`` — plan is auto-approved (dogfood / CI only).
    plan_mode:
        If True (default), the two-phase ``/plan → approve → /execute`` flow
        is used.  If False, the prompt is injected at launch and Codex runs
        immediately (one-shot).
    receipt_path:
        Path for the structured codex_receipt.yaml.
    result_path:
        Path for the standard relay result.json/yaml.
    """

    task_id: str
    task_dir: str
    attempt_id: str = ""
    cwd: str | None = None
    dispatch_path: str | None = None
    expected_outputs: list[str] = field(default_factory=list)
    allowed_roots: list[str] = field(default_factory=list)
    forbidden_roots: list[str] = field(
        default_factory=lambda: ["~/.hermes/**", "~/.cc-switch/**"]
    )
    sandbox_mode: bool = True
    approval_mode: str = "owner_gate"
    plan_mode: bool = True
    receipt_path: str | None = None
    result_path: str | None = None

    @property
    def resolved_cwd(self) -> str:
        return self.cwd or self.task_dir

    @property
    def resolved_task_dir(self) -> Path:
        return Path(self.task_dir).resolve()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "task_dir": self.task_dir,
            "cwd": self.resolved_cwd,
            "dispatch_path": self.dispatch_path,
            "expected_outputs": self.expected_outputs,
            "allowed_roots": self.allowed_roots,
            "forbidden_roots": self.forbidden_roots,
            "sandbox_mode": self.sandbox_mode,
            "approval_mode": self.approval_mode,
            "plan_mode": self.plan_mode,
            "receipt_path": self.receipt_path,
            "result_path": self.result_path,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CodexExecutionContext":
        """Build context from a relay task config dict.

        Uses ``executor_options.codex_context`` (if present) otherwise
        falls back to ``paths`` + defaults.
        """
        raw = config.get("executor_options") or {}
        if not isinstance(raw, dict):
            raw = {}
        ctx_raw = raw.get("codex_context") if isinstance(raw, dict) else None
        if isinstance(ctx_raw, dict):
            task_id = str(ctx_raw.get("task_id", config.get("task_id", "codex-task")))
            return cls(
                task_id=task_id,
                task_dir=str(ctx_raw.get("task_dir", ".")),
                attempt_id=str(ctx_raw.get("attempt_id") or f"{task_id}-a1"),
                cwd=str(ctx_raw.get("cwd")) if ctx_raw.get("cwd") else None,
                dispatch_path=str(ctx_raw.get("dispatch_path")) if ctx_raw.get("dispatch_path") else None,
                expected_outputs=list(ctx_raw.get("expected_outputs", [])),
                allowed_roots=list(ctx_raw.get("allowed_roots", [])),
                forbidden_roots=list(ctx_raw.get("forbidden_roots", ["~/.hermes/**", "~/.cc-switch/**"])),
                sandbox_mode=bool(ctx_raw.get("sandbox_mode", True)),
                approval_mode=str(ctx_raw.get("approval_mode", "owner_gate")),
                plan_mode=bool(ctx_raw.get("plan_mode", True)),
                receipt_path=str(ctx_raw.get("receipt_path")) if ctx_raw.get("receipt_path") else None,
                result_path=str(ctx_raw.get("result_path")) if ctx_raw.get("result_path") else None,
            )

        # Fallback: build from paths section
        paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
        task_dir = str(paths.get("task_dir", "."))
        task_id = str(config.get("task_id", "codex-task"))
        return cls(
            task_id=task_id,
            task_dir=task_dir,
            attempt_id=f"{task_id}-a1",
            cwd=str(paths.get("workdir")) if paths.get("workdir") else task_dir,
            expected_outputs=list(raw.get("expected_outputs", [])) if isinstance(raw, dict) else [],
        )
