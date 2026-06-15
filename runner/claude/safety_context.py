"""Execution context safety isolation for DAG v0.4 (§5.3).

Provides the ExecutionContext dataclass that defines allowed paths, bash
commands, and forbidden patterns for a task node.  Generates security
constraint prompt fragments and validates runtime operations.

Design doc: docs/skills/workflow_v4.0/新一代DAG工作流设计文档_v0.4_emerged.md §5.3
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExecutionContext:
    """Pre-authorized execution context per §5.3.

    Paths use glob patterns.  ``**`` matches any number of directory
    components; ``*`` matches a single component.
    """

    write_paths: list[str] = field(default_factory=list)
    read_paths: list[str] = field(default_factory=list)
    bash_commands: list[str] = field(default_factory=list)
    forbidden_flags: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)

    # ── Path validation ───────────────────────────────────────────────

    def validate_write_path(self, path: str) -> bool:
        """Return True if *path* is allowed for writing."""
        return self._match_any_path(path, self.write_paths)

    def validate_read_path(self, path: str) -> bool:
        """Return True if *path* is allowed for reading."""
        return self._match_any_path(path, self.read_paths)

    # ── Bash validation ───────────────────────────────────────────────

    def validate_bash_command(self, cmd: str) -> bool:
        """Return True if *cmd* is permitted by the whitelist.

        Checks:
        1. No forbidden patterns appear anywhere in the raw string.
        2. No forbidden flags appear anywhere in the raw string.
        3. The leading executable (first token) is in ``bash_commands``.
        """
        # 1. Forbidden patterns (substring match, case-sensitive)
        for pattern in self.forbidden_patterns:
            if pattern in cmd:
                return False

        # 2. Forbidden flags (substring match)
        for flag in self.forbidden_flags:
            if flag in cmd:
                return False

        # 3. Shell-injection tokens
        # Allow: | (pipe), || (fallback chain), 2>/dev/null (stderr suppress)
        # Block: ; && $( ` ` >file <file (arbitrary redirect / command chaining)
        _SHELL_INJECTION = [";", "&&", "$(", "`"]
        for token in _SHELL_INJECTION:
            if token in cmd:
                return False

        # 3b. Redirect to arbitrary paths — block "> /tmp/foo" style but allow
        # "2>/dev/null" (stderr to null) and "2>&1" (stderr to stdout).
        _DANGEROUS_REDIRECT = re.compile(r"(?<!\d)[><](?!\s*/dev/null\s|\s*&\d|\s*$)")
        if _DANGEROUS_REDIRECT.search(cmd):
            return False

        # 3c. Pipe to eval-like commands (potential injection)
        # Note: bare "|" is allowed (grep, head, etc.)
        _DANGEROUS_PIPE = re.compile(r"\|\s*(sh|bash|zsh|python|perl|ruby)\b")
        if _DANGEROUS_PIPE.search(cmd):
            return False

        # 4. Extract the base executable and check whitelist
        try:
            parts = shlex.split(cmd, posix=True)
        except ValueError:
            return False
        if not parts:
            return False

        executable = Path(parts[0]).name
        for allowed in self.bash_commands:
            allowed_parts = allowed.split()
            if executable == allowed_parts[0]:
                # If the whitelist entry has flags (e.g. "mkdir -p"), ensure
                # they are present as a prefix.
                if len(allowed_parts) > 1:
                    if len(parts) >= len(allowed_parts):
                        if parts[: len(allowed_parts)] == allowed_parts:
                            return True
                    continue
                return True
        return False

    # ── Prompt injection ──────────────────────────────────────────────

    def security_prompt_suffix(self) -> str:
        """Return a Markdown fragment describing the constraints.

        Intended to be appended to the prompt sent to the Claude Code
        executor so the agent knows the boundaries.
        """
        lines = ["", "## Execution Context Constraints", ""]

        if self.write_paths:
            lines.append("**Allowed write paths:**")
            for p in self.write_paths:
                lines.append(f"- `{p}`")
            lines.append("")

        if self.read_paths:
            lines.append("**Allowed read paths:**")
            for p in self.read_paths:
                lines.append(f"- `{p}`")
            lines.append("")

        if self.bash_commands:
            lines.append("**Allowed bash commands:**")
            for c in self.bash_commands:
                lines.append(f"- `{c}`")
            lines.append("")

        if self.forbidden_patterns or self.forbidden_flags:
            lines.append("**Forbidden operations:**")
            for f in self.forbidden_flags:
                lines.append(f"- Flag: `{f}`")
            for f in self.forbidden_patterns:
                lines.append(f"- Pattern: `{f}`")
            lines.append("")

        lines.append(
            "Do not attempt to write outside allowed paths, use commands "
            "not in the whitelist, or invoke forbidden flags/patterns."
        )
        return "\n".join(lines)

    # ── Serialization helpers ─────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the §5.3 YAML schema."""
        return {
            "execution_context": {
                "permissions": {
                    "write_paths": list(self.write_paths),
                    "read_paths": list(self.read_paths),
                    "bash_commands": list(self.bash_commands),
                },
                "forbidden": {
                    "dangerous_flags": list(self.forbidden_flags),
                    "bash_patterns": list(self.forbidden_patterns),
                    "write_outside_task": True,
                    "read_outside_declared": True,
                },
            }
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionContext":
        """Load from a dict matching the §5.3 schema."""
        ctx = data.get("execution_context", data)
        perms = ctx.get("permissions", {})
        forbidden = ctx.get("forbidden", {})
        return cls(
            write_paths=perms.get("write_paths", []),
            read_paths=perms.get("read_paths", []),
            bash_commands=perms.get("bash_commands", []),
            forbidden_flags=forbidden.get("dangerous_flags", []),
            forbidden_patterns=forbidden.get("bash_patterns", []),
        )

    # ── Private helpers ───────────────────────────────────────────────

    def _match_any_path(self, path: str, patterns: list[str]) -> bool:
        """Return True if *path* matches any glob in *patterns*."""
        normalized = str(Path(path))
        for pattern in patterns:
            expanded = self._expand_pattern(pattern)
            if fnmatch.fnmatch(normalized, expanded):
                return True
            # Also try matching against just the path components
            # for relative patterns (e.g. "WorkflowBase/**")
            if fnmatch.fnmatch(normalized, pattern):
                return True
        return False

    @staticmethod
    def _expand_pattern(pattern: str) -> str:
        """Normalize ``**`` for fnmatch compatibility.

        fnmatch does not natively support ``**``, so we translate:
        - ``**`` at the end → matches everything below (``*``)
        - ``**`` in the middle → ``*`` (single-level, best-effort)
        """
        # "dir/**" → "dir/*"  (fnmatch treats * as any chars including /)
        if pattern.endswith("**"):
            return pattern[:-2] + "*"
        return pattern


# ── Default context factory ───────────────────────────────────────────


def default_execution_context(task_dir: str) -> ExecutionContext:
    """Create the default §5.3 execution context for a task directory.

    Uses the whitelist from the design doc:
    - Write: task_dir/outputs/*, task_dir/runtime/*
    - Read:  task_dir/**, WorkflowBase/**
    - Bash:  ls, cat, head, tail, python3, mkdir -p
    """
    td = task_dir.rstrip("/")
    return ExecutionContext(
        write_paths=[f"{td}/outputs/*", f"{td}/runtime/*"],
        read_paths=[f"{td}/**", "WorkflowBase/**"],
        bash_commands=["ls", "cat", "head", "tail", "python3", "mkdir -p"],
        forbidden_flags=["--allow-dangerously-skip-permissions"],
        forbidden_patterns=["rm -rf", "sudo", "chmod 777", "curl | bash", "> /dev/null 2>&1"],
    )


def build_context_from_config(config: dict[str, Any]) -> ExecutionContext:
    """Build an ExecutionContext from a relay task config dict.

    If the config contains an ``execution_context`` section (§5.3 schema),
    load from that.  Otherwise, derive from paths.task_dir.
    """
    raw = config.get("execution_context")
    if isinstance(raw, dict):
        return ExecutionContext.from_dict(raw)
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    task_dir = str(paths.get("task_dir") or ".")
    return default_execution_context(task_dir)
