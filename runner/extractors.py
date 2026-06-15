"""Codex JSONL and text extraction helpers for the PM Runtime relay MVP."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def parse_jsonl_events(path: str | Path) -> list[dict[str, Any]]:
    """Parse JSONL events without mutating the source log.

    Malformed or partial lines are returned as lightweight parse error events so
    callers can preserve evidence instead of dropping it.
    """
    events: list[dict[str, Any]] = []
    source = Path(path)
    if not source.exists():
        return [
            {
                "type": "parse_error",
                "error": "path_missing",
                "path": str(source),
            }
        ]

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.rstrip("\n")
            if not text.strip():
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    events.append(parsed)
                else:
                    events.append(
                        {
                            "type": "parse_error",
                            "error": "json_value_not_object",
                            "line_number": line_number,
                            "raw_excerpt": text[:500],
                        }
                    )
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "type": "parse_error",
                        "error": "json_decode_error",
                        "line_number": line_number,
                        "message": str(exc),
                        "raw_excerpt": text[:500],
                    }
                )
    return events


def _collect_text(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {"agent_message", "message", "text", "content"} and isinstance(
                nested, str
            ):
                found.append(nested)
            else:
                _collect_text(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect_text(nested, found)


def extract_codex_agent_messages(events: list[dict[str, Any]]) -> list[str]:
    """Extract agent-facing text from common Codex JSONL event shapes."""
    messages: list[str] = []
    for event in events:
        event_type = str(event.get("type") or event.get("event") or "")
        if event_type in {"agent_message", "item.completed", "message"}:
            _collect_text(event, messages)
        elif "agent_message" in event:
            value = event.get("agent_message")
            if isinstance(value, str):
                messages.append(value)
            else:
                _collect_text(value, messages)
    return [message for message in messages if message.strip()]


def extract_yaml_blocks(text: str) -> list[str]:
    """Return fenced YAML blocks from a text payload."""
    pattern = re.compile(r"```(?:yaml|yml)\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    return [match.group(1).strip() for match in pattern.finditer(text)]


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw in {"", "null", "None", "~"}:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "[]":
        return []
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    try:
        return int(raw)
    except ValueError:
        return raw


def _parse_flat_yaml(block: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key:
            result.setdefault(current_list_key, []).append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            current_list_key = None
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            result[key] = _parse_scalar(value)
            current_list_key = None
        else:
            result[key] = []
            current_list_key = key
    return result


def extract_receipt_candidate(text: str) -> dict[str, Any] | None:
    """Find the first YAML block that looks like a generic receipt."""
    required = {"task_id", "executor", "status", "verdict"}
    for block in extract_yaml_blocks(text):
        parsed = _parse_flat_yaml(block)
        if required.issubset(parsed):
            return parsed
    return None


def write_extraction_result(result: dict[str, Any], output_path: str | Path) -> None:
    """Write extraction output as JSON evidence."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

