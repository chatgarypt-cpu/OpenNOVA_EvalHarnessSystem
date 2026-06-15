"""Runtime relay runner for the PM Runtime Communication Substrate MVP."""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from WorkflowBase.runner.executor_registry import get_executor


RUNTIME_STATES = {
    "not_started",
    "created",
    "dispatch_ready",
    "pre_action_checking",
    "launching",
    "starting",
    "waiting_for_ready",
    "prompt_sent",
    "running",
    "healthy_running",
    "slow_but_progressing",
    "waiting_input",
    "waiting_for_input",
    "artifact_detected",
    "permission_blocked",
    "sandbox_denied",
    "suspected_blocked",
    "missing_receipt",
    "missing_report",
    "partial_output",
    "partial_output_recovered",
    "recovering",
    "recovered",
    "rerun_required",
    "aborting",
    "aborted",
    "executor_completed",
    "executor_failed",
    "completed",
    "failed",
    "timeout",
    "hold",
    "error",
    "session_lost",
    "artifact_missing",
    "environment_blocked",
    "hold_required",
    "summary_written",
}

FAILURE_CLASSIFICATIONS = {
    "agent_completed",
    "agent_failed",
    "permission_blocked",
    "sandbox_denied",
    "partial_output",
    "json_parse_failed",
    "no_output",
    "timeout_or_abort",
    "process_killed",
    "environment_blocked",
    "missing_receipt",
    "missing_report",
    "artifact_path_missing",
    "role_boundary_violation",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw in {"", "null", "None", "~"}:
        return None
    if raw in {"true", "True"}:
        return True
    if raw in {"false", "False"}:
        return False
    if raw == "[]":
        return []
    if raw.startswith("[") and raw.endswith("]"):
        inside = raw[1:-1].strip()
        if not inside:
            return []
        return [_parse_scalar(part.strip()) for part in inside.split(",")]
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def _prepared_yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        without_comment = _strip_comment(raw).rstrip()
        if not without_comment.strip():
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        lines.append((indent, without_comment.strip()))
    return lines


def _parse_yaml_block(
    lines: list[tuple[int, str]], start: int, indent: int
) -> tuple[Any, int]:
    if start >= len(lines):
        return {}, start
    is_list = lines[start][1].startswith("- ")
    if is_list:
        items: list[Any] = []
        index = start
        while index < len(lines):
            line_indent, content = lines[index]
            if line_indent < indent or not content.startswith("- "):
                break
            item_text = content[2:].strip()
            if not item_text:
                value, index = _parse_yaml_block(lines, index + 1, line_indent + 2)
                items.append(value)
            elif ":" in item_text and not item_text.startswith(("'", '"')):
                key, value_text = item_text.split(":", 1)
                item: dict[str, Any] = {}
                if value_text.strip():
                    item[key.strip()] = _parse_scalar(value_text.strip())
                    index += 1
                else:
                    value, index = _parse_yaml_block(lines, index + 1, line_indent + 2)
                    item[key.strip()] = value
                items.append(item)
            else:
                items.append(_parse_scalar(item_text))
                index += 1
        return items, index

    mapping: dict[str, Any] = {}
    index = start
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            index += 1
            continue
        if ":" not in content:
            index += 1
            continue
        key, value_text = content.split(":", 1)
        key = key.strip()
        value_text = value_text.strip()
        if value_text:
            mapping[key] = _parse_scalar(value_text)
            index += 1
        else:
            if index + 1 < len(lines) and lines[index + 1][0] > line_indent:
                value, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
                mapping[key] = value
            else:
                mapping[key] = None
                index += 1
    return mapping, index


def load_yaml(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        pass
    parsed, _ = _parse_yaml_block(_prepared_yaml_lines(text), 0, 0)
    return parsed if isinstance(parsed, dict) else {}


def _quote_yaml(value: str) -> str:
    if value == "":
        return '""'
    if any(char in value for char in [":", "#", "\n", "{", "}", "[", "]"]):
        return json.dumps(value, ensure_ascii=False)
    return value


def to_yaml(value: Any, indent: int = 0) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(to_yaml(nested, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_format_yaml_scalar(nested)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{prefix}[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(to_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_format_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_format_yaml_scalar(value)}"


def _format_yaml_scalar(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_yaml(str(value))


def write_yaml(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_yaml(data) + "\n", encoding="utf-8")


def append_registry_event(
    task_dir: str | Path,
    *,
    task_id: str,
    event_type: str,
    reason: str,
    actor: str = "runtime",
    from_runtime_state: str | None = None,
    to_runtime_state: str | None = None,
    evidence_paths: list[str] | None = None,
    session_id: str | None = None,
    round_id: str | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": str(uuid.uuid4()),
        "task_id": task_id,
        "session_id": session_id,
        "round_id": round_id,
        "timestamp": now_iso(),
        "actor": actor,
        "event_type": event_type,
        "from_runtime_state": from_runtime_state,
        "to_runtime_state": to_runtime_state,
        "reason": reason,
        "evidence_paths": evidence_paths or [],
    }
    registry = Path(task_dir) / "runtime" / "registry_events.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def ensure_task_dirs(config: dict[str, Any]) -> dict[str, Path]:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    task_dir = Path(str(paths.get("task_dir") or ".")).resolve()
    runtime_dir = Path(str(paths.get("runtime_dir") or task_dir / "runtime")).resolve()
    logs_dir = Path(str(paths.get("logs_dir") or task_dir / "logs")).resolve()
    summary_path = Path(
        str(paths.get("summary_path") or task_dir / "summary" / "pm_runtime_summary.md")
    ).resolve()
    dispatch_path = Path(
        str(paths.get("dispatch_path") or task_dir / "dispatch" / "task_config.yaml")
    ).resolve()
    for directory in [task_dir, runtime_dir, logs_dir, summary_path.parent, dispatch_path.parent]:
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "task_dir": task_dir,
        "runtime_dir": runtime_dir,
        "logs_dir": logs_dir,
        "summary_path": summary_path,
        "dispatch_path": dispatch_path,
    }


def validate_config(config: dict[str, Any]) -> list[str]:
    required = ["task_id", "task_domain", "short_task", "executor_type", "execution_mode"]
    missing = [key for key in required if not config.get(key)]
    paths = config.get("paths")
    if not isinstance(paths, dict) or not paths.get("task_dir"):
        missing.append("paths.task_dir")
    return missing


def write_pre_action_check(
    config: dict[str, Any],
    action_type: str,
    *,
    result: str = "pass",
    hold_reason: str | None = None,
) -> Path:
    dirs = ensure_task_dirs(config)
    scope = config.get("scope") if isinstance(config.get("scope"), dict) else {}
    check = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "action_type": action_type,
        "intended_executor": config.get("executor_type"),
        "task_domain": config.get("task_domain"),
        "task_level": config.get("task_level"),
        "artifact_expected": True,
        "artifact_target_paths": [
            str(dirs["runtime_dir"]),
            str(dirs["logs_dir"]),
            str(dirs["summary_path"]),
        ],
        "role_boundary_checked": True,
        "allowed_by_role": result == "pass",
        "needs_ds_team": False,
        "needs_owner_approval": bool(config.get("owner_control_required")),
        "mcp_or_tool_preflight_required": False,
        "scope_checked": True,
        "allowed_files": scope.get("allowed_files") or [],
        "forbidden_files": scope.get("forbidden_files") or [],
        "result": result,
        "hold_reason": hold_reason,
        "created_at": now_iso(),
    }
    path = dirs["runtime_dir"] / "pre_action_check.yaml"
    write_yaml(path, check)
    return path


def write_heartbeat(
    config: dict[str, Any],
    runtime_state: str,
    *,
    executor_pid: int | None = None,
    heartbeat_seq: int | None = None,
) -> Path:
    dirs = ensure_task_dirs(config)
    path = dirs["runtime_dir"] / "heartbeat.json"
    payload = {
        "task_id": config.get("task_id"),
        "runtime_state": runtime_state,
        "timestamp": now_iso(),
        "runtime_pid": os.getpid(),
        "executor_pid": executor_pid,
        "heartbeat_seq": heartbeat_seq,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    history_path = dirs["runtime_dir"] / "heartbeat_history.jsonl"
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    write_legacy_heartbeat(config, payload)
    return path


def write_progress(config: dict[str, Any], runtime_state: str, message: str) -> Path:
    dirs = ensure_task_dirs(config)
    path = dirs["runtime_dir"] / "progress.yaml"
    write_yaml(
        path,
        {
            "task_id": config.get("task_id"),
            "runtime_state": runtime_state,
            "message": message,
            "updated_at": now_iso(),
        },
    )
    write_legacy_progress(config, runtime_state, message)
    return path


def write_legacy_heartbeat(config: dict[str, Any], heartbeat: dict[str, Any]) -> Path:
    """Write a Hermes-readable heartbeat alias under the task runtime dir."""
    dirs = ensure_task_dirs(config)
    path = dirs["runtime_dir"] / "relay_heartbeat.txt"
    content = [
        "legacy_compat: true",
        "compat_for: hermes_old_relay",
        f"task_id: {heartbeat.get('task_id')}",
        f"runtime_state: {heartbeat.get('runtime_state')}",
        f"timestamp: {heartbeat.get('timestamp')}",
        f"runtime_pid: {heartbeat.get('runtime_pid')}",
        f"executor_pid: {heartbeat.get('executor_pid')}",
        f"heartbeat_seq: {heartbeat.get('heartbeat_seq')}",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def write_legacy_progress(config: dict[str, Any], runtime_state: str, message: str) -> Path:
    """Write a Hermes-readable progress alias under the task runtime dir."""
    dirs = ensure_task_dirs(config)
    path = dirs["runtime_dir"] / "relay_progress.md"
    content = f"""---
legacy_compat: true
compat_for: hermes_old_relay
task_id: {config.get("task_id")}
runtime_state: {runtime_state}
updated_at: {now_iso()}
---

{message}
"""
    path.write_text(content, encoding="utf-8")
    return path


def write_legacy_result(
    config: dict[str, Any],
    *,
    runtime_state: str,
    returncode: int | None,
    classification: dict[str, Any],
    evidence_paths: list[str],
) -> Path:
    """Write a Hermes-readable result alias under the task runtime dir."""
    dirs = ensure_task_dirs(config)
    path = dirs["runtime_dir"] / "result.json"
    payload = {
        "legacy_compat": True,
        "compat_for": "hermes_old_relay",
        "task_id": config.get("task_id"),
        "runtime_state": runtime_state,
        "returncode": returncode,
        "classification": classification.get("classification"),
        "confidence": classification.get("confidence"),
        "requires_independent_review": classification.get("requires_independent_review"),
        "evidence_paths": evidence_paths,
        "closeout_claimed": False,
        "created_at": now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_task_state(
    config: dict[str, Any],
    runtime_state: str,
    *,
    task_status: str = "running",
    extra: dict[str, Any] | None = None,
) -> Path:
    dirs = ensure_task_dirs(config)
    state = {
        "task_id": config.get("task_id"),
        "task_status": task_status,
        "runtime_state": runtime_state,
        "runtime_states_supported": sorted(RUNTIME_STATES),
        "known_issues": [
            "runtime_state values intentionally overlap task_status for MVP compatibility"
        ],
        "config_path": str(dirs["dispatch_path"]),
        "updated_at": now_iso(),
        "closeout_claimed": False,
    }
    if extra:
        state.update(extra)
    path = dirs["runtime_dir"] / "task_state.yaml"
    write_yaml(path, state)
    return path


def classify_result(
    returncode: int | None,
    stdout_path: Path,
    stderr_path: Path,
    *,
    raw_output_path: Path | None = None,
    expected_receipt_path: Path | None = None,
    expected_report_path: Path | None = None,
    partial_preserved: bool = False,
    role_boundary_violation: bool = False,
    timed_out: bool = False,
    aborted: bool = False,
) -> dict[str, Any]:
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    evidence = [
        {"path": str(stdout_path), "excerpt_or_summary": stdout[-500:]},
        {"path": str(stderr_path), "excerpt_or_summary": stderr[-500:]},
    ]
    if raw_output_path:
        if raw_output_path.exists():
            raw_excerpt = raw_output_path.read_text(encoding="utf-8", errors="replace")[-500:]
            evidence.append({"path": str(raw_output_path), "excerpt_or_summary": raw_excerpt})
        else:
            evidence.append({"path": str(raw_output_path), "excerpt_or_summary": "missing raw output"})

    if role_boundary_violation:
        classification = "role_boundary_violation"
        confidence = "high"
    elif expected_receipt_path and not expected_receipt_path.exists():
        classification = "missing_receipt"
        confidence = "high"
    elif expected_report_path and not expected_report_path.exists():
        classification = "missing_report"
        confidence = "high"
    elif raw_output_path and not raw_output_path.exists():
        classification = "artifact_path_missing"
        confidence = "high"
    elif raw_output_path and raw_output_path.exists():
        try:
            for raw_line in raw_output_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if raw_line.strip():
                    json.loads(raw_line)
        except json.JSONDecodeError:
            classification = "json_parse_failed"
            confidence = "high"
        else:
            classification = ""
            confidence = ""
    else:
        classification = ""
        confidence = ""

    if classification:
        pass
    elif timed_out or aborted:
        classification = "timeout_or_abort"
        confidence = "high"
    elif partial_preserved and returncode is None:
        classification = "partial_output"
        confidence = "medium"
    elif returncode is None:
        classification = "process_killed"
        confidence = "medium"
    elif "No such file or directory" in stderr or "not found" in stderr.lower():
        classification = "environment_blocked"
        confidence = "medium"
    elif "Operation not permitted" in stderr or "permission denied" in stderr.lower():
        classification = "permission_blocked"
        confidence = "high"
    elif "sandbox" in stderr.lower() and "denied" in stderr.lower():
        classification = "sandbox_denied"
        confidence = "high"
    elif returncode == 0 and (stdout or stderr):
        classification = "agent_completed"
        confidence = "high"
    elif returncode == 0:
        classification = "no_output"
        confidence = "medium"
    else:
        classification = "agent_failed"
        confidence = "high"
    # v0.1.2: returncode=0 but required artifacts missing is not agent_completed
    if classification == "agent_completed":
        if expected_report_path and not expected_report_path.exists():
            classification = "missing_report"
            confidence = "high"
        elif expected_receipt_path and not expected_receipt_path.exists():
            classification = "missing_receipt"
            confidence = "high"
    return {
        "classification": classification,
        "evidence": evidence,
        "confidence": confidence,
        "classified_by": "runtime",
        "requires_independent_review": classification != "agent_completed",
    }


def write_owner_decision_request(
    config: dict[str, Any],
    event_type: str,
    requested_action: str,
    observed_result: str,
    *,
    risk_level: str = "medium",
) -> Path:
    dirs = ensure_task_dirs(config)
    scope = config.get("scope") if isinstance(config.get("scope"), dict) else {}
    request = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "request_id": str(uuid.uuid4()),
        "executor": config.get("executor_type"),
        "event_type": event_type,
        "requested_action": requested_action,
        "affected_files": [],
        "observed_result": observed_result,
        "agent_message": observed_result,
        "risk_level": risk_level,
        "allowed_scope": scope.get("allowed_dirs") or scope.get("allowed_files") or [],
        "forbidden_scope": scope.get("forbidden_files") or [],
        "available_options": [
            "approve_with_scope",
            "reject",
            "abort_task",
            "request_safer_alternative",
            "ask_for_more_context",
        ],
        "recommended_action": requested_action,
        "owner_control_required": True,
        "created_at": now_iso(),
    }
    path = dirs["runtime_dir"] / "owner_decision_request.yaml"
    write_yaml(path, request)
    return path


def write_owner_decision_record_template(config: dict[str, Any]) -> Path:
    dirs = ensure_task_dirs(config)
    record = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "request_id": "",
        "owner_decision": "",
        "decision_source": "unknown",
        "decision_time": "",
        "approved_scope": [],
        "rejected_scope": [],
        "notes": "",
        "next_runtime_action": "",
    }
    path = dirs["runtime_dir"] / "owner_decision_record.yaml"
    write_yaml(path, record)
    return path


def _expected_artifact(config: dict[str, Any], key: str) -> Path | None:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    options = config.get("executor_options") if isinstance(config.get("executor_options"), dict) else {}
    value = paths.get(key) or options.get(key)
    return Path(str(value)).resolve() if value else None


def write_abort_report(
    config: dict[str, Any],
    abort_reason: str,
    *,
    abort_requested_by: str = "runtime",
) -> Path:
    dirs = ensure_task_dirs(config)
    report = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "abort_reason": abort_reason,
        "abort_requested_by": abort_requested_by,
        "abort_approved_by": "",
        "abort_time": now_iso(),
        "partial_output_preserved": True,
        "stdout_partial_path": str(dirs["logs_dir"] / "stdout.partial.log"),
        "stderr_partial_path": str(dirs["logs_dir"] / "stderr.partial.log"),
        "raw_output_partial_path": str(dirs["logs_dir"] / "raw_output.partial.jsonl"),
        "next_recommendation": "Owner-Control review required before retry",
        "owner_control_required": True,
    }
    path = dirs["runtime_dir"] / "abort_report.yaml"
    write_yaml(path, report)
    return path


@dataclass
class RunResult:
    exit_code: int
    classification: dict[str, Any]
    stdout_path: Path
    stderr_path: Path
    raw_output_path: Path
    summary_path: Path | None = None


def _materialize_task_package(config: dict[str, Any], dirs: dict[str, Path]) -> None:
    """v0.1.2: copy external dispatch/system_prompt into task package for sandbox self-containment."""
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    dispatch_dir = dirs["dispatch_path"].parent

    # Materialize external dispatch file -> dispatch/dispatch.md
    external_dispatch = paths.get("external_dispatch_path")
    if external_dispatch:
        ext_path = Path(str(external_dispatch))
        if ext_path.exists() and ext_path.is_file():
            target = dispatch_dir / "dispatch.md"
            shutil.copyfile(ext_path, target)

    # Materialize system prompt -> dispatch/system_prompt.md
    system_prompt = paths.get("system_prompt_path")
    if system_prompt:
        sp_path = Path(str(system_prompt))
        if sp_path.exists() and sp_path.is_file():
            target = dispatch_dir / "system_prompt.md"
            shutil.copyfile(sp_path, target)


def init_task(config_path: str | Path) -> int:
    config_source = Path(config_path)
    if not config_source.exists():
        raise FileNotFoundError(f"config path not found: {config_source}")
    if not config_source.is_file():
        raise ValueError(f"config_invalid: config path is not a file: {config_source}")
    config = load_yaml(config_path)
    missing = validate_config(config)
    if missing:
        raise ValueError(f"config_invalid: missing {', '.join(missing)}")
    dirs = ensure_task_dirs(config)
    if dirs["dispatch_path"].exists() and dirs["dispatch_path"].is_dir():
        raise ValueError(f"config_invalid: dispatch_path is a directory: {dirs['dispatch_path']}")
    source = config_source.resolve()
    if source != dirs["dispatch_path"]:
        shutil.copyfile(source, dirs["dispatch_path"])
    # v0.1.2: materialize external references into task package (self-containment)
    _materialize_task_package(config, dirs)
    pre_action = write_pre_action_check(config, "create_task")
    write_task_state(config, "created", task_status="approved")
    append_registry_event(
        dirs["task_dir"],
        task_id=str(config.get("task_id")),
        event_type="created",
        reason="task initialized by PM Runtime relay MVP",
        to_runtime_state="created",
        evidence_paths=[str(pre_action), str(dirs["runtime_dir"] / "task_state.yaml")],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    return 0


def _load_config_from_task_dir(task_dir: str | Path) -> dict[str, Any]:
    base = Path(task_dir).resolve()
    dispatch_config = base / "dispatch" / "task_config.yaml"
    state_path = base / "runtime" / "task_state.yaml"
    if dispatch_config.exists() and dispatch_config.is_file():
        return load_yaml(dispatch_config)
    if dispatch_config.exists() and dispatch_config.is_dir():
        raise ValueError(f"config_invalid: dispatch config path is a directory: {dispatch_config}")
    if state_path.exists():
        state = load_yaml(state_path)
        config_path = state.get("config_path")
        if config_path:
            recorded = Path(str(config_path))
            if recorded.exists() and recorded.is_file():
                return load_yaml(recorded)
            if recorded.exists() and recorded.is_dir():
                raise ValueError(f"config_invalid: recorded config_path is a directory: {recorded}")
            raise FileNotFoundError(f"recorded config_path not found: {recorded}")
    dispatch_dir = base / "dispatch"
    if dispatch_dir.exists():
        yaml_candidates = sorted(
            path for path in dispatch_dir.glob("*.yaml") if path.is_file()
        ) + sorted(path for path in dispatch_dir.glob("*.yml") if path.is_file())
        if len(yaml_candidates) == 1:
            return load_yaml(yaml_candidates[0])
        if len(yaml_candidates) > 1:
            names = ", ".join(str(path) for path in yaml_candidates)
            raise ValueError(f"config_invalid: multiple dispatch YAML candidates: {names}")
    raise FileNotFoundError(f"task_config not found under {base}")


def _executor_command(config: dict[str, Any]) -> list[str]:
    options = config.get("executor_options") if isinstance(config.get("executor_options"), dict) else {}
    executor_type = str(config.get("executor_type") or "local_echo")
    execution_mode = str(config.get("execution_mode") or "local_echo")
    command = options.get("command")
    extra_args = options.get("extra_args")
    if isinstance(command, list) and command and command[0] != "local_echo":
        return [str(item) for item in command]
    if isinstance(command, str) and command != "local_echo":
        return shlex.split(command)
    if executor_type == "shell_command" or execution_mode == "managed_subprocess":
        raise ValueError("config_invalid: managed subprocess requires executor_options.command")
    if executor_type == "codex" or execution_mode == "managed_codex_exec":
        args = [str(item) for item in extra_args] if isinstance(extra_args, list) else []
        return ["codex", *args]
    if executor_type == "claude" or execution_mode == "managed_relay_session":
        args = [str(item) for item in extra_args] if isinstance(extra_args, list) else []
        return ["claude", *args]
    stdout_text = str(options.get("echo_stdout") or "PM Runtime local_echo stdout")
    stderr_text = str(options.get("echo_stderr") or "PM Runtime local_echo stderr")
    script = (
        "import sys; "
        f"print({stdout_text!r}); "
        f"print({stderr_text!r}, file=sys.stderr)"
    )
    return [sys.executable, "-c", script]


def run_task(task_dir: str | Path) -> RunResult:
    config = _load_config_from_task_dir(task_dir)
    # Auto-derive paths from task_dir if config doesn't have paths section
    if "paths" not in config or not isinstance(config.get("paths"), dict) or not config["paths"].get("task_dir"):
        resolved = Path(task_dir).resolve()
        config["paths"] = {
            "task_dir": str(resolved),
            "dispatch_path": str(resolved / "dispatch" / "task_config.yaml"),
            "runtime_dir": str(resolved / "runtime"),
            "logs_dir": str(resolved / "logs"),
            "summary_path": str(resolved / "summary" / "pm_runtime_summary.md"),
        }
    dirs = ensure_task_dirs(config)
    # Default dag_execution_mode: "direct" unless explicitly set
    config.setdefault("dag_execution_mode", "direct")
    # Sound notification default
    executor_opts = config.get("executor_options")
    if isinstance(executor_opts, dict):
        executor_opts.setdefault("enable_sound_notification", True)
        executor_opts.setdefault("notification_sound", "Glass")
    else:
        config["executor_options"] = {
            "enable_sound_notification": True,
            "notification_sound": "Glass",
        }
    task_id = str(config.get("task_id"))
    write_task_state(config, "pre_action_checking")
    pre_action = write_pre_action_check(config, "launch_executor")
    append_registry_event(
        dirs["task_dir"],
        task_id=task_id,
        event_type="pre_action_checked",
        reason="launch pre-action check passed",
        from_runtime_state="created",
        to_runtime_state="pre_action_checking",
        evidence_paths=[str(pre_action)],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    write_task_state(config, "launching")
    write_progress(config, "launching", "executor launch started")
    append_registry_event(
        dirs["task_dir"],
        task_id=task_id,
        event_type="launched",
        reason="executor process launch requested",
        from_runtime_state="pre_action_checking",
        to_runtime_state="launching",
        evidence_paths=[],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )

    # ── Try registered executor (Claude, Codex, etc.) ────────────────
    executor_type = str(config.get("executor_type") or "")
    executor_cls = get_executor(executor_type)
    if executor_cls is not None:
        executor = executor_cls(config)
        result = executor.run()
        write_pm_runtime_summary(task_dir, dirs=dirs, config=config)
        return RunResult(
            result.exit_code,
            result.classification,
            result.stdout_path,
            result.stderr_path,
            result.raw_output_path,
        )

    stdout_path = dirs["logs_dir"] / "stdout.log"
    stderr_path = dirs["logs_dir"] / "stderr.log"
    raw_output_path = dirs["logs_dir"] / "raw_output.jsonl"
    stdout_partial = dirs["logs_dir"] / "stdout.partial.log"
    stderr_partial = dirs["logs_dir"] / "stderr.partial.log"
    raw_partial = dirs["logs_dir"] / "raw_output.partial.jsonl"
    command = _executor_command(config)
    started_at = time.time()
    timed_out = False
    returncode: int | None = None
    executor_pid: int | None = None
    heartbeat_seq = 0
    current_runtime_state = "launching"
    try:
        timeout_value = None
        control = config.get("runtime_control")
        heartbeat_interval = 30
        progress_interval = 120
        if isinstance(control, dict) and control.get("emergency_max_wall_time_sec"):
            timeout_value = int(control["emergency_max_wall_time_sec"])
        if isinstance(control, dict) and control.get("heartbeat_interval_sec"):
            heartbeat_interval = max(1, int(control["heartbeat_interval_sec"]))
        if isinstance(control, dict) and control.get("progress_check_interval_sec"):
            progress_interval = max(1, int(control["progress_check_interval_sec"]))
        process = subprocess.Popen(
            command,
            cwd=str(dirs["task_dir"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        executor_pid = process.pid
        current_runtime_state = "running"
        write_task_state(config, "running", extra={"executor_pid": executor_pid})
        write_progress(config, "running", "executor process started")
        heartbeat_seq += 1
        write_heartbeat(
            config,
            "running",
            executor_pid=executor_pid,
            heartbeat_seq=heartbeat_seq,
        )
        raw_start = {
            "timestamp": now_iso(),
            "event": "process_started",
            "command": command,
            "pid": executor_pid,
        }
        raw_output_path.write_text(json.dumps(raw_start, ensure_ascii=False) + "\n", encoding="utf-8")
        raw_partial.write_text(raw_output_path.read_text(encoding="utf-8"), encoding="utf-8")

        def pump(stream: Any, target: Path, partial: Path, stream_name: str) -> None:
            with target.open("w", encoding="utf-8") as full, partial.open(
                "w", encoding="utf-8"
            ) as part:
                for line in iter(stream.readline, ""):
                    full.write(line)
                    full.flush()
                    part.write(line)
                    part.flush()
                    with raw_output_path.open("a", encoding="utf-8") as raw_handle:
                        raw_handle.write(
                            json.dumps(
                                {
                                    "timestamp": now_iso(),
                                    "event": "stream",
                                    "stream": stream_name,
                                    "text": line,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                    raw_partial.write_text(
                        raw_output_path.read_text(encoding="utf-8", errors="replace"),
                        encoding="utf-8",
                    )

        threads = [
            threading.Thread(
                target=pump,
                args=(process.stdout, stdout_path, stdout_partial, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=pump,
                args=(process.stderr, stderr_path, stderr_partial, "stderr"),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()

        current_runtime_state = "healthy_running"
        write_task_state(config, "healthy_running", extra={"executor_pid": executor_pid})
        write_progress(config, "healthy_running", "executor process running")
        heartbeat_seq += 1
        write_heartbeat(
            config,
            "healthy_running",
            executor_pid=executor_pid,
            heartbeat_seq=heartbeat_seq,
        )
        last_heartbeat_at = time.time()
        last_progress_at = time.time()
        while process.poll() is None:
            now = time.time()
            if timeout_value and time.time() - started_at > timeout_value:
                timed_out = True
                current_runtime_state = "timeout"
                write_task_state(config, "timeout", extra={"executor_pid": executor_pid})
                process.kill()
                write_abort_report(config, "executor timeout")
                break
            if now - last_heartbeat_at >= heartbeat_interval:
                heartbeat_seq += 1
                write_heartbeat(
                    config,
                    current_runtime_state,
                    executor_pid=executor_pid,
                    heartbeat_seq=heartbeat_seq,
                )
                last_heartbeat_at = now
            if now - last_progress_at >= progress_interval:
                current_runtime_state = "slow_but_progressing"
                write_task_state(config, "slow_but_progressing", extra={"executor_pid": executor_pid})
                write_progress(config, "slow_but_progressing", "executor still running")
                last_progress_at = now
            time.sleep(0.05)

        returncode = process.wait()
        for thread in threads:
            thread.join(timeout=2)
    except OSError as exc:
        returncode = None
        stdout_path.write_text("", encoding="utf-8")
        stdout_partial.write_text("", encoding="utf-8")
        stderr_path.write_text(str(exc), encoding="utf-8")
        stderr_partial.write_text(str(exc), encoding="utf-8")

    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    if not stdout_partial.exists():
        stdout_partial.write_text(stdout, encoding="utf-8")
    if not stderr_partial.exists():
        stderr_partial.write_text(stderr, encoding="utf-8")
    raw_event = {
        "timestamp": now_iso(),
        "event": "process_completed",
        "command": command,
        "returncode": returncode,
        "elapsed_sec": round(time.time() - started_at, 3),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    with raw_output_path.open("a", encoding="utf-8") as raw_handle:
        raw_handle.write(json.dumps(raw_event, ensure_ascii=False) + "\n")
    raw_partial.write_text(raw_output_path.read_text(encoding="utf-8"), encoding="utf-8")
    classification = classify_result(
        returncode,
        stdout_path,
        stderr_path,
        raw_output_path=raw_output_path,
        expected_receipt_path=_expected_artifact(config, "expected_receipt_path"),
        expected_report_path=_expected_artifact(config, "expected_report_path"),
        partial_preserved=stdout_partial.exists() or stderr_partial.exists() or raw_partial.exists(),
        role_boundary_violation=bool(config.get("role_boundary_violation")),
        timed_out=timed_out,
    )
    classification_path = dirs["runtime_dir"] / "failure_classification.yaml"
    write_yaml(classification_path, classification)
    if classification["classification"] == "agent_completed":
        final_state = "executor_completed"
        task_status = "completed"
        exit_code = 0
        event_type = "progress"
        reason = "executor completed and output was captured"
    else:
        final_state = "executor_failed"
        task_status = "failed"
        exit_code = 5
        event_type = "blocked"
        reason = f"executor classified as {classification['classification']}"
        write_blocker_report(config, reason, stderr[-1000:])
        write_owner_decision_request(
            config,
            "recovery_requires_approval",
            "request_owner_decision",
            reason,
        )
        write_owner_decision_record_template(config)
    legacy_result_path = write_legacy_result(
        config,
        runtime_state=final_state,
        returncode=returncode,
        classification=classification,
        evidence_paths=[
            str(stdout_path),
            str(stderr_path),
            str(raw_output_path),
            str(classification_path),
        ],
    )
    write_task_state(
        config,
        final_state,
        task_status=task_status,
        extra={
            "classification": classification["classification"],
            "legacy_result_path": str(legacy_result_path),
        },
    )
    heartbeat_seq += 1
    write_heartbeat(config, final_state, executor_pid=executor_pid, heartbeat_seq=heartbeat_seq)
    write_progress(config, final_state, reason)

    # ── G5: expected_outputs validation ──
    if final_state == "executor_completed":
        output_validation = _check_expected_outputs(config)
        if not output_validation.get("all_present", True):
            log_msg = f"expected_outputs check: {output_validation.get('result', 'unknown')}"
            append_registry_event(
                dirs["task_dir"],
                task_id=task_id,
                event_type="expected_outputs_validation",
                reason=log_msg,
                from_runtime_state=final_state,
                to_runtime_state=f"{final_state}_artifact_{output_validation.get('result', 'unknown')}",
                evidence_paths=[str(Path(str(dirs.get("task_dir", "."))) / "outputs" / "expected_outputs_validation.json")],
                session_id=config.get("session_id", "session-local"),
                round_id=config.get("round_id", "round-1"),
            )

    # ── G2/G4: task log append ──
    if final_state == "executor_completed":
        log_result = _append_task_log(config, "completed")
        if log_result.get("errors"):
            append_registry_event(
                dirs["task_dir"],
                task_id=task_id,
                event_type="task_log_append_issue",
                reason="; ".join(log_result["errors"]),
                from_runtime_state=final_state,
                to_runtime_state=f"{final_state}_log_issue",
                session_id=config.get("session_id", "session-local"),
                round_id=config.get("round_id", "round-1"),
            )
    append_registry_event(
        dirs["task_dir"],
        task_id=task_id,
        event_type=event_type,
        reason=reason,
        from_runtime_state="healthy_running",
        to_runtime_state=final_state,
        evidence_paths=[
            str(stdout_path),
            str(stderr_path),
            str(raw_output_path),
            str(classification_path),
        ],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    write_pm_runtime_summary(task_dir, dirs=dirs, config=config)
    return RunResult(exit_code, classification, stdout_path, stderr_path, raw_output_path)


def write_blocker_report(config: dict[str, Any], suspected_blocker: str, stderr_tail: str) -> Path:
    dirs = ensure_task_dirs(config)
    report = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "runtime_state": "suspected_blocked",
        "elapsed_seconds": 0,
        "last_heartbeat_at": now_iso(),
        "last_progress_at": now_iso(),
        "stdout_growth": "unknown",
        "stderr_tail": stderr_tail,
        "suspected_blocker": suspected_blocker,
        "recommended_actions": [
            "continue",
            "attach",
            "request_owner_decision",
            "repair_permissions",
            "recover_partial_output",
            "abort",
        ],
        "owner_control_required": True,
    }
    path = dirs["runtime_dir"] / "blocker_report.md"
    content = "---\n" + to_yaml(report) + "\n---\n\nPM Runtime blocker report.\n"
    path.write_text(content, encoding="utf-8")
    return path


def read_registry_events(task_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(task_dir) / "runtime" / "registry_events.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError:
                events.append({"event_type": "json_parse_failed", "raw_excerpt": line[:500]})
    return events


# ── Repair Agent (§10) ────────────────────────────────────────────────


def _classify_problem_type(failures: list[str]) -> str:
    """Map validation failure messages to §3.2 problem_type enum."""
    for failure in failures:
        if "file_not_found" in failure:
            return "file_not_found"
        if "empty_output" in failure:
            return "empty_output"
        if "format_error" in failure:
            return "format_error"
        if "read_error" in failure:
            return "format_error"
    return "content_validation"


def generate_issue_packet(
    config: dict[str, Any],
    node_id: str,
    failures: list[str],
    evidence_paths: list[str] | None = None,
    current_retry: int = 0,
) -> Path:
    """Generate and write an issue_packet.yaml per §3.2.

    Returns the path to the written file.
    """
    from WorkflowBase.runner.output_validator import validate_outputs  # noqa: E402

    dirs = ensure_task_dirs(config)
    date_str = datetime.now().strftime("%Y%m%d")
    issue_seq = current_retry + 1
    issue_id = f"issue-{date_str}-{issue_seq:03d}"

    problem_type = _classify_problem_type(failures)
    fix_descriptions = "; ".join(failures[:5])

    packet: dict[str, Any] = {
        "issue_id": issue_id,
        "source_node": node_id,
        "reviewer": "repair_agent",
        "executor_to_fix": str(config.get("executor_type", "claude")),
        "problem_type": problem_type,
        "evidence": evidence_paths or [str(dirs["runtime_dir"] / "pane_capture.log")],
        "expected_fix": f"Repair: {fix_descriptions}",
        "allowed_scope": [],
        "forbidden_scope": [],
        "retry_limit": 2,
        "owner_decision_required": True,
        "current_retry": current_retry,
        "diagnosis": fix_descriptions,
        "repair_instruction": (
            f"The following expected outputs failed validation: "
            f"{fix_descriptions}. "
            "Please regenerate or fix these files."
        ),
    }
    path = dirs["runtime_dir"] / "issue_packet.yaml"
    write_yaml(path, packet)

    # Also append to registry event chain
    append_registry_event(
        dirs["task_dir"],
        task_id=str(config.get("task_id")),
        event_type="repair_agent_triggered",
        reason=f"issue_packet generated: {issue_id} ({problem_type})",
        from_runtime_state="executor_completed",
        to_runtime_state="repair_in_progress",
        evidence_paths=[str(path)],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    return path


def repair_node(
    config: dict[str, Any],
    expected_outputs: list[str],
    node_id: str = "node-1",
    *,
    send_repair_prompt: Any | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Repair Agent orchestrator (§10).

    Validates expected outputs, generates issue_packet on failure,
    invokes ``send_repair_prompt`` for retry (up to *max_retries* rounds),
    and escalates to owner when budget is exhausted.

    Args:
        config: Relay task config dict.
        expected_outputs: List of expected output paths (absolute or relative).
        node_id: DAG node identifier.
        send_repair_prompt: Optional callable ``(prompt_text: str) -> bool``
            that sends a repair prompt to the executor.  Return True on
            success.  If None, repair_node only generates artifacts without
            retrying.
        max_retries: Maximum repair rounds (default 2 per §10.3).

    Returns:
        dict with keys: ``verdict`` (pass|retry_once|escalate_to_owner),
        ``issue_packet_path``, ``validation``, ``retries_used``.
    """
    from WorkflowBase.runner.output_validator import validate_outputs  # noqa: E402

    dirs = ensure_task_dirs(config)
    task_dir = dirs["task_dir"]

    # Step 1: Validate outputs
    validation = validate_outputs(task_dir, expected_outputs)
    if validation.all_pass:
        return {
            "verdict": "pass",
            "issue_packet_path": None,
            "validation": validation.to_dict(),
            "retries_used": 0,
        }

    # Step 2: Retry loop
    retries_used = 0
    issue_packet_path: Path | None = None

    for attempt in range(max_retries):
        # Generate issue_packet
        issue_packet_path = generate_issue_packet(
            config,
            node_id,
            validation.failures,
            current_retry=attempt,
        )
        retries_used = attempt + 1

        # If no repair prompt sender, stop after generating the packet
        if send_repair_prompt is None:
            write_progress(
                config,
                "repair_in_progress",
                f"issue_packet generated (attempt {attempt + 1}/{max_retries}), no prompt sender configured",
            )
            continue

        # Build repair prompt and send
        repair_prompt = (
            f"Repair Agent detected output validation failures.\n\n"
            f"Issue: {validation.failures}\n\n"
            f"Please regenerate or fix the following files so they pass "
            f"validation: {', '.join(f.path for f in validation.items if f.error)}\n"
        )
        try:
            success = send_repair_prompt(repair_prompt)
        except Exception:
            success = False

        if success:
            # Re-validate after repair attempt
            validation = validate_outputs(task_dir, expected_outputs)
            if validation.all_pass:
                return {
                    "verdict": "pass",
                    "issue_packet_path": str(issue_packet_path),
                    "validation": validation.to_dict(),
                    "retries_used": retries_used,
                }

    # Step 3: Escalate to owner
    owner_request_path = _write_repair_owner_decision_request(
        config,
        node_id,
        validation,
        retries_used,
    )

    write_progress(
        config,
        "hold_required",
        f"Repair Agent exhausted {retries_used} retries; owner decision required",
    )
    append_registry_event(
        dirs["task_dir"],
        task_id=str(config.get("task_id")),
        event_type="repair_escalated",
        reason=f"Repair Agent exhausted {retries_used} retries, escalating to owner",
        from_runtime_state="repair_in_progress",
        to_runtime_state="hold_required",
        evidence_paths=[str(owner_request_path)],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )

    return {
        "verdict": "escalate_to_owner",
        "issue_packet_path": str(issue_packet_path) if issue_packet_path else None,
        "owner_request_path": str(owner_request_path),
        "validation": validation.to_dict(),
        "retries_used": retries_used,
    }


def _write_repair_owner_decision_request(
    config: dict[str, Any],
    node_id: str,
    validation: Any,
    retries_used: int,
) -> Path:
    """Write owner_decision_request.yaml for Repair Agent escalation."""
    dirs = ensure_task_dirs(config)
    request: dict[str, Any] = {
        "task_id": config.get("task_id"),
        "session_id": config.get("session_id") or "session-local",
        "round_id": config.get("round_id") or "round-1",
        "request_id": str(uuid.uuid4()),
        "node_id": node_id,
        "requester": "repair_agent",
        "event_type": "repair_exhausted",
        "question": (
            f"Repair Agent exhausted {retries_used} repair attempts. "
            f"Output validation failures: {validation.failures}. "
            "Please decide how to proceed."
        ),
        "options": [
            "retry_with_extended_scope",
            "accept_partial_output",
            "abort_task",
            "skip_node",
            "manual_fix",
        ],
        "urgency": "high",
        "failed_outputs": [i.path for i in validation.items if i.error],
        "validation_summary": validation.to_dict(),
        "owner_control_required": True,
        "created_at": now_iso(),
    }
    path = dirs["runtime_dir"] / "owner_decision_request.yaml"
    write_yaml(path, request)
    return path


# TODO: wire into DAG node completion callback
# This function is defined but not yet called by any scheduler.
# It should be connected to the node completion event when
# the DAG scheduler integration is ready.
def trigger_repair_if_needed(
    config: dict[str, Any],
    expected_outputs: list[str],
    node_id: str = "node-1",
    *,
    send_repair_prompt: Any | None = None,
) -> dict[str, Any] | None:
    """Check if outputs need repair and trigger Repair Agent if so.

    Returns None if outputs pass validation; otherwise returns the
    repair_node() result dict.
    """
    from WorkflowBase.runner.output_validator import validate_outputs  # noqa: E402

    dirs = ensure_task_dirs(config)
    validation = validate_outputs(dirs["task_dir"], expected_outputs)
    if validation.all_pass:
        return None
    return repair_node(
        config,
        expected_outputs,
        node_id,
        send_repair_prompt=send_repair_prompt,
    )


def _summarize_output_content(output_path: Path) -> str:
    """Read an output file and extract a content-level summary.
    Adapts to different output types: review reports, code changes, test results, etc."""
    if not output_path.exists() or output_path.stat().st_size == 0:
        return ""
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return ""
    lines = text.splitlines()

    def _clean(val: str) -> str:
        """Strip markdown formatting and extra whitespace from a value."""
        return val.replace("**", "").replace("__", "").replace("`", "").strip()

    seen = set()
    summary_parts = []

    # Pattern 1: Review report (Verdict + quality_check + Blocking Issues)
    for line in lines:
        stripped = line.strip()
        key = None
        val = None
        if "Verdict:" in stripped or "Verdict：" in stripped:
            key = "审查结论"
            val = _clean(stripped.split("Verdict")[-1].split("：")[-1].split(":")[-1].strip())
        elif "quality_check:" in stripped or "quality_check：" in stripped:
            key = "质量检查"
            val = _clean(stripped.split("quality_check")[-1].split("：")[-1].split(":")[-1].strip())
        if key and val:
            dedup = f"{key}:{val}"
            if dedup not in seen:
                seen.add(dedup)
                summary_parts.append(f"{key}：{val}")

    if summary_parts:
        # Dedup by key: keep first (most detailed) occurrence for each key
        by_key: dict[str, str] = {}
        for part in summary_parts:
            k = part.split("：")[0]
            if k not in by_key:
                by_key[k] = part
        return "；".join(by_key.values())

    # Pattern 2: Code changes / test results (filename-based heuristics)
    name = output_path.name.lower()
    if "test" in name or "spec" in name:
        passed = sum(1 for l in lines if "PASS" in l or "passed" in l or "✅" in l)
        failed = sum(1 for l in lines if "FAIL" in l or "failed" in l or "❌" in l)
        if passed or failed:
            return f"测试结果：通过 {passed}，失败 {failed}"
    if "diff" in name or "change" in name or "patch" in name:
        changed_files = [l for l in lines if l.strip().startswith("--- ") or l.strip().startswith("+++ ")]
        if changed_files:
            files = set(l.split()[-1] for l in changed_files if len(l.split()) > 1)
            return f"修改文件：{', '.join(files)}"

    # Fallback: first 3 meaningful lines
    meaningful = [l.strip() for l in lines if l.strip() and not l.strip().startswith("```")]
    excerpt = " | ".join(meaningful[:3])
    if len(excerpt) > 300:
        excerpt = excerpt[:297] + "..."
    return f"内容摘要：{excerpt}" if excerpt else ""


def write_pm_runtime_summary(task_dir: str | Path, dirs: dict[str, Path] | None = None, config: dict[str, Any] | None = None) -> Path:
    """Generate a concise post-task briefing from runtime state and result.json.
    Called automatically at the end of run_task() for both tmux and subprocess paths."""
    if dirs is None or config is None:
        config = _load_config_from_task_dir(task_dir)
        dirs = ensure_task_dirs(config)
    state_path = dirs["runtime_dir"] / "task_state.yaml"
    result_path = dirs["runtime_dir"] / "result.json"
    events = read_registry_events(dirs["task_dir"])
    state = load_yaml(state_path) if state_path.exists() else {}
    result = {} if not result_path.exists() else json.loads(result_path.read_text(encoding="utf-8"))

    # Compute duration from registry events or result
    started_at = state.get("created_at", "")
    completed_at = result.get("created_at", "")
    duration = ""
    if started_at and completed_at:
        try:
            from datetime import datetime as _dt
            s = _dt.fromisoformat(started_at)
            e = _dt.fromisoformat(completed_at)
            delta = e - s
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            duration = f"{mins}m{secs}s"
        except Exception:
            pass

    # Collect expected outputs info
    outputs_info = []
    outputs_summary = []
    for s in (result.get("expected_outputs_status") or result.get("expected_outputs", {}).get("detected") or []):
        if isinstance(s, dict):
            path_str = s.get('path', '?')
            outputs_info.append(f"- {path_str} ({s.get('size_bytes', 0)} bytes, exists={s.get('exists', False)})")
            content_summary = _summarize_output_content(Path(path_str)) if path_str != "?" else ""
            if content_summary:
                outputs_summary.append(content_summary)

    # Collect owner decision requests
    owner_requests = sorted(str(p) for p in dirs["runtime_dir"].glob("owner_decision_request*.yaml"))
    blocker_reports = sorted(str(p) for p in dirs["runtime_dir"].glob("blocker_report*"))

    # Collect key events from registry
    dialog_events = [e for e in events if e.get("event_type", "").startswith("dialog")]
    error_events = [e for e in events if e.get("event_type", "").startswith("error")]

    content = f"""# PM Runtime 简报 — {config.get('task_id', 'unknown')}

## 任务
- 任务 ID：{config.get('task_id')}
- 执行器类型：{config.get('executor_type')}
- 执行模式：{config.get('execution_mode')}
- 任务域：{config.get('task_domain', '?')}

## 执行结果
- 运行状态：{result.get('runtime_state', state.get('runtime_state', 'unknown'))}
- 退出码：{result.get('returncode', 'N/A')}
- 完成分类：{result.get('classification', 'N/A')}
- 用时：{duration or 'N/A'}
- 完成原因：{result.get('reason', 'N/A')}

## 产出物
{chr(10).join(outputs_info) if outputs_info else '- (无)'}

### 内容摘要
{chr(10).join('- ' + s for s in outputs_summary) if outputs_summary else '（无内容摘要）'}

## 对话框与权限
- 最后对话框：{result.get('dialog_handling', {}).get('last_dialog_type', '无')}
- 处理动作：{result.get('dialog_handling', {}).get('last_dialog_action', 'N/A')}
- 远程模式：{'已激活' if result.get('clauderemote', {}).get('active') else '未激活'}
- 权限请求：{len([r for r in owner_requests if 'permission' in r.lower()])} 次
- 对话框事件数：{len(dialog_events)}

## 问题记录
- 错误计数：{result.get('error_retry', {}).get('error_counts', {}) or '无'}
- 阻塞报告：{len(blocker_reports)} 份
- 需 Owner 决策：{len(owner_requests)} 项

## 关键文件
- 简报：{dirs['summary_path']}
- 结果：{result_path}
- 注册事件：{len(events)} 条
"""
    dirs["summary_path"].parent.mkdir(parents=True, exist_ok=True)
    dirs["summary_path"].write_text(content, encoding="utf-8")
    write_task_state(config, "summary_written", task_status=state.get("task_status", "completed"))
    append_registry_event(
        dirs["task_dir"],
        task_id=str(config.get("task_id")),
        event_type="summary_written",
        reason="PM Runtime summary written; no closeout claimed",
        from_runtime_state=str(state.get("runtime_state", "unknown")),
        to_runtime_state="summary_written",
        evidence_paths=[str(dirs["summary_path"])],
        session_id=config.get("session_id") or "session-local",
        round_id=config.get("round_id") or "round-1",
    )
    return dirs["summary_path"]


# ── G5: Expected Outputs Validator ──────────────────────────────────────────

def _check_expected_outputs(config: dict[str, Any]) -> dict[str, Any]:
    """Post-execution validation: verify expected_outputs exist and are non-empty.
    
    Returns a dict with check results for each expected output path.
    Used by G5 (产物完整性 validator).
    """
    dirs = ensure_task_dirs(config)
    executor_opts = config.get("executor_options", {})
    if isinstance(executor_opts, dict):
        expected = executor_opts.get("expected_outputs", [])
    else:
        expected = []
    
    # Also check paths section
    paths_section = config.get("paths", {})
    if isinstance(paths_section, dict):
        for key in ("expected_report_path", "expected_receipt_path"):
            val = paths_section.get(key)
            if val and str(val) not in expected:
                expected.append(str(val))
    
    if not expected:
        return {"checked": False, "reason": "no expected_outputs configured", "results": []}
    
    results = []
    all_present = True
    for path_str in expected:
        p = Path(str(path_str)).resolve()
        exists = p.exists()
        is_empty = exists and p.is_file() and p.stat().st_size == 0
        status = "present"
        if not exists:
            status = "missing"
            all_present = False
        elif is_empty:
            status = "empty"
            all_present = False
        results.append({
            "path": str(path_str),
            "resolved": str(p),
            "status": status,
            "size": p.stat().st_size if exists else 0,
        })
    
    validation = {
        "checked": True,
        "all_present": all_present,
        "result": "pass" if all_present else "fail",
        "results": results,
    }
    
    # Write validation result to outputs/
    outputs_dir = dirs.get("task_dir", Path(".")) / "outputs"
    outputs_dir = Path(str(outputs_dir)).resolve()
    outputs_dir.mkdir(parents=True, exist_ok=True)
    validation_path = outputs_dir / "expected_outputs_validation.json"
    import json as _json
    validation_path.write_text(_json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")
    
    return validation


# ── G2/G4: Task Log Callback ────────────────────────────────────────────────

_TASK_LOG_PATH = Path.home() / "项目开发" / "AdarianMigration" / "adarian mvp" / "docs" / "iterations" / "TASK_LOG.md"
_CHANGELOG_PATH = Path.home() / "项目开发" / "AdarianMigration" / "adarian mvp" / "docs" / "iterations" / "CHANGELOG.md"


def _append_task_log(config: dict[str, Any], status: str) -> dict[str, Any]:
    """Append a brief entry to TASK_LOG.md and CHANGELOG.md after executor completion.
    
    Only appends if the corresponding log file is writable.
    Failure to append is non-blocking (logged, not raised).
    Used by G2 (auto log) and G4 (verified log update).
    """
    task_id = config.get("task_id", "unknown")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    executor_type = config.get("executor_type", "unknown")
    
    # Build entry
    entry_lines = [
        "",
        f"## {date_str}: {task_id}",
        "",
        f"- **task_id**: {task_id}",
        f"- **executor**: {executor_type}",
        f"- **status**: {status}",
        f"- **timestamp**: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    entry = "\n".join(entry_lines)
    
    result = {"task_log": False, "changelog": False, "errors": []}
    
    # Append to TASK_LOG.md
    try:
        if _TASK_LOG_PATH.exists():
            existing = _TASK_LOG_PATH.read_text(encoding="utf-8")
            _TASK_LOG_PATH.write_text(existing.rstrip() + entry, encoding="utf-8")
        else:
            _TASK_LOG_PATH.write_text(
                "# Adarian MVP 任务执行日志 (TASK_LOG)\n\n自动日志起始于 relay_runner post-exec callback\n---" + entry,
                encoding="utf-8",
            )
        result["task_log"] = True
    except Exception as e:
        result["errors"].append(f"TASK_LOG append failed: {e}")
    
    # Append to CHANGELOG.md
    try:
        changelog_entry = f"\n## {date_str}: {task_id} ({executor_type})\n\n- 状态：{status}\n"
        if _CHANGELOG_PATH.exists():
            existing = _CHANGELOG_PATH.read_text(encoding="utf-8")
            _CHANGELOG_PATH.write_text(existing.rstrip() + changelog_entry, encoding="utf-8")
        else:
            _CHANGELOG_PATH.write_text(
                "# Adarian MVP 变更日志 (CHANGELOG)\n\n---" + changelog_entry,
                encoding="utf-8",
            )
        result["changelog"] = True
    except Exception as e:
        result["errors"].append(f"CHANGELOG append failed: {e}")
    
    return result
