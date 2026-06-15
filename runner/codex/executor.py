"""Lightweight Codex executor for PM Runtime relay R1.2.

All Codex-specific logic lives here — command construction, stdin strategy,
subprocess management, prompt passing, and result parsing.

Protocol (follows executor_registry convention):
    __init__(self, config: dict) -> None
    run(self) -> RunResult             # fields: exit_code, classification, ...

=== Future registry upgrade ===
No refactoring needed — this class is already compatible with registry dispatch:
    register_executor("codex", CodexExecutor)
    result = get_executor("codex")(config).run()
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class CodexExecutor:
    """Run Codex CLI via managed subprocess for one-shot tasks.

    Key behaviors:
    - Uses `codex exec --json` for structured JSONL output
    - stdin=subprocess.DEVNULL — Codex hangs on piped stdin
    - Supports timeout, heartbeat, and artifact verification via existing relay_runner utilities
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.dirs = ensure_task_dirs(config)
        self.options: dict[str, Any] = (
            config.get("executor_options") if isinstance(config.get("executor_options"), dict) else {}
        )
        self.control: dict[str, Any] = (
            config.get("runtime_control") if isinstance(config.get("runtime_control"), dict) else {}
        )
        self.task_id = str(config.get("task_id") or "codex-task-default")
        self.workdir = str(config.get("workdir") or self.dirs["task_dir"])

    # ── Public protocol ─────────────────────────────────────────────

    def run(self) -> RunResult:
        """Execute the Codex task and return result.

        Steps:
        1. Resolve prompt from config
        2. Build command with proper flags
        3. Launch subprocess with DEVNULL stdin
        4. Pump stdout/stderr to files with monitoring
        5. Classify result
        6. Write all standard runtime artifacts
        7. Return RunResult compatible with relay_runner.run_task()
        """
        prompt = self._resolve_prompt()
        cmd = self._build_command(prompt)
        write_progress(self.config, "launching", "codex executor launch")
        write_task_state(self.config, "launching")
        sr = self._run_subprocess(cmd)
        classification = self._classify_result(sr)
        self._write_results(sr, classification)
        return RunResult(
            exit_code=0 if classification.get("classification") == "agent_completed" else 5,
            classification=classification,
            stdout_path=sr.stdout_path,
            stderr_path=sr.stderr_path,
            raw_output_path=sr.raw_output_path,
        )

    # ── Step 1: Prompt resolution ───────────────────────────────────

    def _resolve_prompt(self) -> str:
        """Resolve prompt from config, in priority order.

        1. executor_options.prompt_file  (relative to task_dir or absolute)
        2. executor_options.prompt       (inline text)
        3. dispatch/prompt.md            (convention from relay task init)
        """
        prompt_file = self.options.get("prompt_file")
        if prompt_file:
            p = Path(str(prompt_file))
            if not p.is_absolute():
                p = self.dirs["task_dir"] / p
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if text.strip():
                    return text

        inline = self.options.get("prompt")
        if inline and str(inline).strip():
            return str(inline)

        dispatch_dir = self.dirs["dispatch_path"].parent
        fallback = dispatch_dir / "prompt.md"
        if fallback.exists():
            text = fallback.read_text(encoding="utf-8")
            if text.strip():
                return text

        raise ValueError(
            "config_invalid: no prompt found — set executor_options.prompt_file, "
            "executor_options.prompt, or ensure dispatch/prompt.md exists"
        )

    # ── Step 2: Command construction ────────────────────────────────

    def _build_command(self, prompt: str) -> list[str]:
        """Build the Codex CLI command.

        Base command: codex exec --json --skip-git-repo-check --ephemeral -C <workdir>

        The `--dangerously-bypass-approvals-and-sandbox` flag is controlled by
        executor_options.codex_bypass_approvals (default: true for backward compat).
        Set to false to rely on the Adarian Safety Gate skill embedded in the prompt.

        Extra flags via executor_options.codex_flags (list[str]).
        """
        flags = [
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
        ]

        # Safety bypass: controlled by config, not hardcoded
        # When false, rely on Adarian Safety Gate skill in the prompt
        if self.options.get("codex_bypass_approvals", True):
            flags.append("--dangerously-bypass-approvals-and-sandbox")

        extra_flags = self.options.get("codex_flags")
        if isinstance(extra_flags, list):
            flags.extend(str(f) for f in extra_flags)
        return ["codex", *flags, "-C", self.workdir, prompt]

    # ── Step 3: Subprocess management ───────────────────────────────

    @dataclass
    class _SubprocessResult:
        """Internal structured result from a codex subprocess run."""
        returncode: int | None
        timed_out: bool
        stdout_text: str
        stderr_text: str
        stdout_path: Path
        stderr_path: Path
        raw_output_path: Path

    def _run_subprocess(self, cmd: list[str]) -> _SubprocessResult:
        """Launch Codex, pump output, monitor, return when done.

        stdin=subprocess.DEVNULL  ← Critical: Codex hangs on PIPE stdin.
        No dialog handling — Codex exec is one-shot, not interactive.
        """
        stdout_path = self.dirs["logs_dir"] / "stdout.log"
        stderr_path = self.dirs["logs_dir"] / "stderr.log"
        raw_output_path = self.dirs["logs_dir"] / "raw_output.jsonl"

        timeout_value = int(self.control.get("emergency_max_wall_time_sec", 0)) or None
        heartbeat_interval = int(self.control.get("heartbeat_interval_sec", 30))

        proc = subprocess.Popen(
            cmd,
            cwd=str(self.dirs["task_dir"]),
            stdin=subprocess.DEVNULL,     # ← Codex hangs on PIPE
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        executor_pid = proc.pid
        write_progress(self.config, "running", "codex process started")
        write_heartbeat(
            self.config, "running",
            executor_pid=executor_pid, heartbeat_seq=1,
        )

        started_at = time.time()
        timed_out = False

        # ── Pump threads (same pattern as relay_runner managed_subprocess) ──

        def _pump(stream, target_path: Path, raw_path: Path, stream_name: str) -> None:
            with target_path.open("w", encoding="utf-8") as full:
                for line in iter(stream.readline, ""):
                    full.write(line)
                    full.flush()
                    raw_line = (
                        json.dumps(
                            {"timestamp": now_iso(), "event": "stream",
                             "stream": stream_name, "text": line},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    with raw_path.open("a", encoding="utf-8") as raw_f:
                        raw_f.write(raw_line)

        threads = [
            threading.Thread(
                target=_pump, args=(proc.stdout, stdout_path, raw_output_path, "stdout"), daemon=True,
            ),
            threading.Thread(
                target=_pump, args=(proc.stderr, stderr_path, raw_output_path, "stderr"), daemon=True,
            ),
        ]
        for t in threads:
            t.start()

        # ── Monitor loop (heartbeat + timeout) ──

        heartbeat_seq = 1
        last_heartbeat_at = time.time()

        while proc.poll() is None:
            now = time.time()

            if timeout_value and (now - started_at) > timeout_value:
                timed_out = True
                write_progress(self.config, "timeout", "codex process timed out")
                write_abort_report(self.config, "codex executor timeout")
                proc.kill()
                break

            if now - last_heartbeat_at >= heartbeat_interval:
                heartbeat_seq += 1
                write_heartbeat(self.config, "running", executor_pid=executor_pid, heartbeat_seq=heartbeat_seq)
                write_progress(self.config, "slow_but_progressing", "codex still running")
                last_heartbeat_at = now

            time.sleep(0.05)

        returncode = proc.wait()
        for t in threads:
            t.join(timeout=2)

        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""

        return self._SubprocessResult(
            returncode=returncode,
            timed_out=timed_out,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            raw_output_path=raw_output_path,
        )

    # ── Step 4: Result classification ───────────────────────────────

    def _classify_result(self, sr: _SubprocessResult) -> dict[str, Any]:
        """Classify Codex execution result.

        Special handling:
        - Auth errors (401, token_expired) → environment_blocked
        - Otherwise delegates to relay_runner.classify_result() for standard logic
        """
        stderr_tail = sr.stderr_text[-2000:].lower()
        auth_patterns = ("401", "token_expired", "refresh_token", "unauthorized", "forbidden")
        auth_failure = any(p in stderr_tail for p in auth_patterns)

        if auth_failure and sr.returncode != 0:
            write_progress(self.config, "environment_blocked", "codex auth token expired or unauthorized")
            return {
                "classification": "environment_blocked",
                "evidence": [
                    {"path": str(sr.stderr_path), "excerpt_or_summary": sr.stderr_text[-500:]},
                    {"path": str(sr.stdout_path), "excerpt_or_summary": sr.stdout_text[-500:]},
                ],
                "confidence": "high",
                "classified_by": "codex_executor",
                "requires_independent_review": True,
            }

        expected_receipt = self._expected_path("expected_receipt_path")
        expected_report = self._expected_path("expected_report_path")

        return classify_result(
            sr.returncode,
            sr.stdout_path,
            sr.stderr_path,
            raw_output_path=sr.raw_output_path,
            expected_receipt_path=expected_receipt,
            expected_report_path=expected_report,
            timed_out=sr.timed_out,
        )

    def _expected_path(self, key: str) -> Path | None:
        """Resolve expected artifact path from config.

        Searches paths.{key} then executor_options.{key}.
        If relative, resolves against task_dir.
        """
        paths_cfg = self.config.get("paths") if isinstance(self.config.get("paths"), dict) else {}
        value = paths_cfg.get(key) or self.options.get(key)
        if value:
            p = Path(str(value))
            if not p.is_absolute():
                p = self.dirs["task_dir"] / p
            return p
        return None

    # ── Step 5: Result writing ──────────────────────────────────────

    def _write_results(self, sr: _SubprocessResult, classification: dict[str, Any]) -> None:
        """Write all standard runtime result files.

        Same contract as relay_runner.run_task() for managed_subprocess.
        """
        is_completed = classification.get("classification") == "agent_completed"
        final_state = "executor_completed" if is_completed else "executor_failed"
        task_status = "completed" if is_completed else "failed"
        reason = (
            "codex completed and artifacts verified"
            if is_completed
            else f"codex classified as {classification.get('classification')}"
        )

        write_task_state(self.config, final_state, task_status=task_status, extra={
            "classification": classification.get("classification"),
            "executor": "codex",
        })
        write_progress(self.config, final_state, reason)
        write_heartbeat(self.config, final_state, heartbeat_seq=0)

        if not is_completed:
            write_blocker_report(self.config, reason, sr.stderr_text[-1000:])
            write_owner_decision_request(
                self.config, "recovery_requires_approval", "request_owner_decision", reason,
            )
            write_owner_decision_record_template(self.config)

        write_legacy_result(
            self.config,
            runtime_state=final_state,
            returncode=sr.returncode,
            classification=classification,
            evidence_paths=[
                str(sr.stdout_path),
                str(sr.stderr_path),
                str(sr.raw_output_path),
            ],
        )

        append_registry_event(
            self.dirs["task_dir"],
            task_id=self.task_id,
            event_type="progress" if is_completed else "blocked",
            reason=reason,
            from_runtime_state="running",
            to_runtime_state=final_state,
            evidence_paths=[str(p) for p in [sr.stdout_path, sr.stderr_path, sr.raw_output_path] if p.exists()],
            session_id=str(self.config.get("session_id", "session-local")),
            round_id=str(self.config.get("round_id", "round-1")),
        )


# ── Self-registration ──────────────────────────────────────────────

from ..executor_registry import register_executor  # noqa: E402

register_executor("codex", CodexExecutor)
