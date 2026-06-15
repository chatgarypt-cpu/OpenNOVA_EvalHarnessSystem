"""Recovery helpers for the PM Runtime relay MVP."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .relay_runner import (
    append_registry_event,
    ensure_task_dirs,
    load_yaml,
    now_iso,
    to_yaml,
    write_owner_decision_request,
    write_task_state,
)


RECOVERY_TYPES = {
    "trivial_recovery",
    "non_trivial_recovery",
    "owner_approved_recovery",
}


def recover_task(task_dir: str | Path) -> tuple[int, Path]:
    """Attempt trivial recovery by preserving existing partial/log evidence."""
    base = Path(task_dir).resolve()
    config_path = base / "dispatch" / "task_config.yaml"
    if not config_path.exists():
        fallback_config = {
            "task_id": "unknown",
            "executor_type": "unknown",
            "paths": {
                "task_dir": str(base),
                "runtime_dir": str(base / "runtime"),
                "logs_dir": str(base / "logs"),
                "summary_path": str(base / "summary" / "pm_runtime_summary.md"),
            },
        }
        summary = write_recovery_summary(
            fallback_config,
            recovery_type="non_trivial_recovery",
            original_failure_paths=[],
            new_output_paths=[],
            runtime_state="rerun_required",
            owner_approval_required=True,
        )
        write_owner_decision_request(
            fallback_config,
            "recovery_requires_approval",
            "request_owner_decision",
            "task_config missing; non-trivial recovery requires Owner-Control",
            risk_level="high",
        )
        return 7, summary

    config = load_yaml(config_path)
    dirs = ensure_task_dirs(config)
    logs_dir = dirs["logs_dir"]
    runtime_dir = dirs["runtime_dir"]
    original_paths = [
        path
        for path in [
            logs_dir / "stdout.log",
            logs_dir / "stderr.log",
            logs_dir / "raw_output.jsonl",
            logs_dir / "stdout.partial.log",
            logs_dir / "stderr.partial.log",
            logs_dir / "raw_output.partial.jsonl",
        ]
        if path.exists()
    ]
    new_paths: list[Path] = []
    if not original_paths:
        summary = write_recovery_summary(
            config,
            recovery_type="non_trivial_recovery",
            original_failure_paths=[],
            new_output_paths=[],
            runtime_state="rerun_required",
            owner_approval_required=True,
        )
        write_owner_decision_request(
            config,
            "recovery_requires_approval",
            "request_owner_decision",
            "no recoverable logs found; rerun requires Owner-Control",
            risk_level="high",
        )
        write_task_state(config, "rerun_required", task_status="hold")
        return 7, summary

    evidence_dir = runtime_dir / "recovered_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for source in original_paths:
        target = evidence_dir / source.name
        if not target.exists():
            shutil.copyfile(source, target)
        new_paths.append(target)

    summary = write_recovery_summary(
        config,
        recovery_type="trivial_recovery",
        original_failure_paths=[str(path) for path in original_paths],
        new_output_paths=[str(path) for path in new_paths],
        runtime_state="recovered",
        owner_approval_required=False,
    )
    write_task_state(config, "recovered", task_status="completed")
    append_registry_event(
        dirs["task_dir"],
        task_id=str(config.get("task_id")),
        event_type="recovered",
        reason="trivial recovery preserved existing log evidence",
        from_runtime_state="recovering",
        to_runtime_state="recovered",
        evidence_paths=[str(summary), *[str(path) for path in new_paths]],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    return 0, summary


def write_recovery_summary(
    config: dict[str, Any],
    *,
    recovery_type: str,
    original_failure_paths: list[str],
    new_output_paths: list[str],
    runtime_state: str,
    owner_approval_required: bool,
) -> Path:
    if recovery_type not in RECOVERY_TYPES:
        recovery_type = "non_trivial_recovery"
    dirs = ensure_task_dirs(config)
    payload = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "recovery_type": recovery_type,
        "original_failure_paths": original_failure_paths,
        "new_output_paths": new_output_paths,
        "evidence_preserved": bool(new_output_paths),
        "owner_approval_required": owner_approval_required,
        "owner_approval_record": "",
        "runtime_state": runtime_state,
        "closeout_claimed": False,
        "created_at": now_iso(),
    }
    path = dirs["runtime_dir"] / "recovery_summary.md"
    path.write_text("---\n" + to_yaml(payload) + "\n---\n\nRecovery summary.\n", encoding="utf-8")
    return path
